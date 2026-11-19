import threading
import time
from datetime import datetime
from typing import Any, Callable

from pymodbus.datastore import ModbusSlaveContext

from handler.dout_monitor_handler import DOutMonitorHandler
from handler.flow_handler import SutoFlowHandler
from handler.load_schedule_handler import LoadScheduleHandler
from model.profile_enum import ProfileEnum
from simulator.profile_generator import ProfileGenerator
from utils.constants import fx_code_for_register_type
from utils.pin_processor.pin_value_reader import PinValueReader
from utils.pin_processor.pin_value_writer import PinValueWriter


def _fx_for_pin(pin: dict, default_register_type: str = "holding") -> int:
    return fx_code_for_register_type(pin.get("register_type"), default_register_type)


def ramp_wrap_and_carry(
    current: float, step: float, min_v: int, max_v: int
) -> tuple[int | None, int]:
    """Wrap one ``carry_to`` ramp step and count the overflows.

    Returns ``(wrapped, carry_count)``. ``wrapped`` is ``None`` when the next
    value does not exceed ``max_v``; the profile's own value is used then. The
    low word holds ``wrap_range = max_v - min_v + 1`` distinct values, so a next
    value of ``max_v + 1`` is exactly one overflow, and ``k`` full wraps carry
    ``k`` into the target word.
    """
    wrap_range = max_v - min_v + 1
    theoretical_next = current + step
    if theoretical_next <= max_v:
        return None, 0
    offset = theoretical_next - min_v
    return min_v + int(offset % wrap_range), int(offset // wrap_range)


class ProfileUpdater:
    def __init__(
        self,
        config: dict,
        context: ModbusSlaveContext,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.context = context
        self.running = True
        self.reader = PinValueReader(context)
        self.writer = PinValueWriter(context, self._log)
        self.profile_states: dict[tuple[str, int], dict[str, Any]] = {}
        # Raises LoadScheduleConfigError on an invalid load_schedule block.
        load_schedule = LoadScheduleHandler(
            context, config, monotonic_clock=monotonic_clock, wall_clock=wall_clock
        )
        # The load schedule handler comes first: while active it claims the
        # energy/KW pins, so their ramp/wave profiles are skipped.
        self.pin_handlers = [load_schedule.pin_claim, DOutMonitorHandler()]
        self.device_handlers = [SutoFlowHandler(context, config), load_schedule]

    def start(self, base_address: int = 0, interval_sec: float = 1.0):
        interval_sec = float(interval_sec)
        if interval_sec <= 0:
            raise ValueError("interval_sec must be greater than zero")

        def _loop():
            tick = 0
            while self.running:
                self.run_tick(tick * interval_sec, base_address)
                tick += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def run_tick(self, elapsed: float, base_address: int = 0):
        """Apply one update cycle: pin handlers/profiles, then device handlers."""
        model = self.config.get("model", "")
        pin_list = self.config.get("pins", []) or []
        device_id = self.config.get("device_id", "Unknown")
        default_register_type = self.config.get("register_type", "holding")

        for pin in pin_list:
            offset = int(pin.get("offset", 0))
            addr = base_address + offset
            fx_code = _fx_for_pin(pin, default_register_type)
            name = pin.get("name", f"offset_{offset}")
            log_ctx = self._format_log_ctx(device_id, model, name, addr)

            handled = False
            for handler in self.pin_handlers:
                try:
                    if handler.should_handle(model, pin):
                        handler.handle(
                            self.context, fx_code, addr, pin, self._log, log_ctx
                        )
                        handled = True
                        break
                except Exception as exc:
                    self._log(f"{log_ctx} [WARN] pin handler error: {exc}")
            if handled:
                continue

            profile = pin.get("profile")
            if not profile:
                continue

            profile_type = str(profile.get("type", "")).lower()
            current_val = self.reader.get_current_value(pin, fx_code, addr)
            if profile_type == ProfileEnum.HOLD:
                continue

            state_key = (str(name), offset)
            runtime_state = self.profile_states.setdefault(state_key, {})
            val = ProfileGenerator.generate(
                profile, elapsed, current_val, runtime_state
            )

            carry_to = profile.get("carry_to")
            if profile_type == "ramp" and carry_to:
                val = self._apply_ramp_carry(
                    profile,
                    current_val,
                    val,
                    carry_to,
                    pin_list,
                    base_address,
                    default_register_type,
                    log_ctx,
                )

            self.writer.write(pin, fx_code, addr, val, log_ctx)

        for handler in self.device_handlers:
            try:
                handler.handle(fx_code_for_register_type("holding"))
            except Exception as exc:
                self._log(f"[{device_id}][{model}] [WARN] device handler error: {exc}")

    def _apply_ramp_carry(
        self,
        profile: dict,
        current_val: Any,
        val: float,
        carry_to: str,
        pin_list: list[dict],
        base_address: int,
        default_register_type: str,
        log_ctx: str,
    ) -> float:
        try:
            step = float(profile.get("step", 1))
        except Exception:
            step = 1.0

        try:
            min_v = int(profile.get("min", 0))
            max_v = int(profile.get("max", 65535))
        except Exception:
            min_v, max_v = 0, 65535

        try:
            base_cv = int(current_val)
        except Exception:
            base_cv = min_v

        wrapped, carry_count = ramp_wrap_and_carry(base_cv, step, min_v, max_v)
        if wrapped is None:
            return val

        if carry_count > 0:
            target_pin = next((p for p in pin_list if p.get("name") == carry_to), None)
            if target_pin:
                fx_target = _fx_for_pin(target_pin, default_register_type)
                target_addr = base_address + int(target_pin.get("offset", 0))
                current_hi = self.reader.get_current_value(
                    target_pin, fx_target, target_addr
                )
                try:
                    current_hi = int(current_hi)
                except Exception:
                    current_hi = 0

                new_hi = (current_hi + carry_count) % 65536
                self.writer.write(
                    target_pin, fx_target, target_addr, new_hi, log_ctx + " [carry]"
                )
            else:
                self._log(f"{log_ctx} [WARN] carry_to target '{carry_to}' not found")

        return wrapped

    def stop(self):
        self.running = False

    def _format_log_ctx(self, device_id: str, model: str, name: str, addr: int) -> str:
        return f"[{device_id}][{model}] {name} (addr={addr})"

    @staticmethod
    def _log(msg: str):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
