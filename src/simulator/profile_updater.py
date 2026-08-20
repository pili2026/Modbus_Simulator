import threading
import time
from datetime import datetime
from typing import Any

from pymodbus.datastore import ModbusSlaveContext

from handler.dout_monitor_handler import DOutMonitorHandler
from handler.flow_handler import SutoFlowHandler
from model.profile_enum import ProfileEnum
from simulator.profile_generator import ProfileGenerator
from utils.constants import fx_code_for_register_type
from utils.pin_processor.pin_value_reader import PinValueReader
from utils.pin_processor.pin_value_writer import PinValueWriter


def _fx_for_pin(pin: dict, default_register_type: str = "holding") -> int:
    return fx_code_for_register_type(pin.get("register_type"), default_register_type)


class ProfileUpdater:
    def __init__(self, config: dict, context: ModbusSlaveContext):
        self.config = config
        self.context = context
        self.running = True
        self.reader = PinValueReader(context)
        self.writer = PinValueWriter(context, self._log)
        self.profile_states: dict[tuple[str, int], dict[str, Any]] = {}
        self.pin_handlers = [DOutMonitorHandler()]
        self.device_handlers = [SutoFlowHandler(context, config)]

    def start(self, base_address: int = 0, interval_sec: float = 1.0):
        tick = 0
        interval_sec = float(interval_sec)
        if interval_sec <= 0:
            raise ValueError("interval_sec must be greater than zero")

        model = self.config.get("model", "")
        pin_list = self.config.get("pins", []) or []
        device_id = self.config.get("device_id", "Unknown")
        default_register_type = self.config.get("register_type", "holding")

        def _loop():
            nonlocal tick
            while self.running:
                elapsed = tick * interval_sec

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

                    carry_to = None
                    try:
                        ptype = str(profile.get("type", "")).lower()
                        carry_to = profile.get("carry_to")
                    except Exception:
                        ptype = ""

                    if ptype == "ramp" and carry_to:
                        step = profile.get("step", 1)
                        try:
                            step = float(step)
                        except Exception:
                            step = 1.0

                        min_v = profile.get("min", 0)
                        max_v = profile.get("max", 65535)
                        try:
                            min_v = int(min_v)
                            max_v = int(max_v)
                        except Exception:
                            min_v, max_v = 0, 65535

                        wrap_range = max_v - min_v + 1
                        try:
                            base_cv = int(current_val)
                        except Exception:
                            base_cv = min_v
                        theoretical_next = base_cv + step

                        carry_count = 0
                        if theoretical_next > max_v:
                            try:
                                carry_count = (
                                    int((theoretical_next - min_v) // wrap_range) + 1
                                )
                            except Exception:
                                carry_count = 1

                            try:
                                wrapped = min_v + int(
                                    (theoretical_next - min_v) % wrap_range
                                )
                            except Exception:
                                wrapped = min_v
                            val = wrapped

                        if carry_count > 0:
                            target_pin = None
                            for tp_pin in pin_list:
                                if tp_pin.get("name") == carry_to:
                                    target_pin = tp_pin
                                    break

                            if target_pin:
                                fx_target = _fx_for_pin(
                                    target_pin, default_register_type
                                )
                                target_addr = base_address + int(
                                    target_pin.get("offset", 0)
                                )
                                current_hi = self.reader.get_current_value(
                                    target_pin, fx_target, target_addr
                                )
                                try:
                                    current_hi = int(current_hi)
                                except Exception:
                                    current_hi = 0

                                new_hi = (current_hi + carry_count) % 65536
                                self.writer.write(
                                    target_pin,
                                    fx_target,
                                    target_addr,
                                    new_hi,
                                    log_ctx + " [carry]",
                                )
                            else:
                                self._log(
                                    f"{log_ctx} [WARN] carry_to target '{carry_to}' not found"
                                )

                    self.writer.write(pin, fx_code, addr, val, log_ctx)

                for handler in self.device_handlers:
                    try:
                        handler.handle(fx_code_for_register_type("holding"))
                    except Exception as exc:
                        self._log(
                            f"[{device_id}][{model}] [WARN] device handler error: {exc}"
                        )

                tick += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _format_log_ctx(self, device_id: str, model: str, name: str, addr: int) -> str:
        return f"[{device_id}][{model}] {name} (addr={addr})"

    @staticmethod
    def _log(msg: str):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
