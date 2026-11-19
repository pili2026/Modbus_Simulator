import threading
import time

from pymodbus.datastore import ModbusSlaveContext

from base.modbus_context_builder import ModbusContextBuilder
from simulator.profile_updater import ProfileUpdater
from utils.constants import FC_CODE_MAP
from utils.log_formatter import simulate_log


class GenericModbusSimulator:
    def __init__(self, config: dict):
        self.config = config
        self.context: ModbusSlaveContext | None = None
        self.updater: ProfileUpdater | None = None
        self.running = True

    def build_context(self) -> ModbusSlaveContext:
        builder = ModbusContextBuilder(self.config)
        self.context, fx = builder.build()

        device_type = self.config.get("type", "").lower()
        base_addr = int(self.config.get("base_address", 0))
        interval_sec = float(self.config.get("interval_sec", 1.0))

        pins = self.config.get("pins", [])
        has_profiles = any(isinstance(p.get("profile"), dict) for p in pins)

        should_start_updater = (
            has_profiles
            or "load_schedule" in self.config
            or any(
                (p.get("name", "").startswith("DOut") or p.get("bit") is not None)
                for p in pins
            )
        )

        if should_start_updater:
            self.updater = ProfileUpdater(self.config, self.context)
            self.updater.start(
                base_address=base_addr,
                interval_sec=interval_sec,
            )

        if device_type == "inverter":
            self._start_inverter_logic()

        return self.context

    def _start_inverter_logic(self):
        if self.context is None:
            simulate_log("[ERROR] Cannot start inverter logic because context is not initialized")
            return

        model = self.config.get("model", "")
        device_id = self.config.get("device_id", "Unknown")

        fx = FC_CODE_MAP["hr"]
        mapping = self.config.get("register_mapping", {})

        on_off_addr = mapping.get("on_off")
        cmd_hz_addr = mapping.get("command_hz")
        out_hz_addr = mapping.get("output_hz")
        kw_addr = mapping.get("kw")

        missing_fields = []
        if on_off_addr is None:
            missing_fields.append("on_off")
        if cmd_hz_addr is None:
            missing_fields.append("command_hz")
        if out_hz_addr is None:
            missing_fields.append("output_hz")

        if missing_fields:
            simulate_log(
                f"[ERROR] [{device_id}][{model}] Missing inverter register_mapping fields: "
                f"{missing_fields}"
            )
            return

        inverter_logic_config = self.config.get("inverter_logic", {})

        hz_ramp_step = int(inverter_logic_config.get("hz_ramp_step", 100))
        kw_ramp_step = int(inverter_logic_config.get("kw_ramp_step", 10))
        max_hz = int(inverter_logic_config.get("max_hz", 6000))
        max_kw = int(inverter_logic_config.get("max_kw", 100))
        interval_sec = float(inverter_logic_config.get("interval_sec", 1))

        context = self.context

        simulate_log(
            f"[{device_id}][{model}] Start inverter logic: "
            f"on_off={on_off_addr}, command_hz={cmd_hz_addr}, "
            f"output_hz={out_hz_addr}, kw={kw_addr}, "
            f"hz_ramp_step={hz_ramp_step}, kw_ramp_step={kw_ramp_step}, "
            f"max_hz={max_hz}, max_kw={max_kw}"
        )

        def _loop():
            while self.running:
                try:
                    on_off = context.getValues(fx, on_off_addr, count=1)[0]
                    cmd_hz = context.getValues(fx, cmd_hz_addr, count=1)[0]
                    current_out_hz = context.getValues(fx, out_hz_addr, count=1)[0]

                    current_kw = None
                    if kw_addr is not None:
                        current_kw = context.getValues(fx, kw_addr, count=1)[0]

                    if on_off == 1:
                        current_out_hz = self._ramp_towards(
                            current=current_out_hz,
                            target=cmd_hz,
                            step=hz_ramp_step,
                        )

                        if current_kw is not None:
                            target_kw = self._calculate_target_kw(
                                current_hz=current_out_hz,
                                max_hz=max_hz,
                                max_kw=max_kw,
                            )
                            current_kw = self._ramp_towards(
                                current=current_kw,
                                target=target_kw,
                                step=kw_ramp_step,
                            )
                    else:
                        current_out_hz = self._ramp_towards(
                            current=current_out_hz,
                            target=0,
                            step=hz_ramp_step,
                        )

                        if current_kw is not None:
                            current_kw = self._ramp_towards(
                                current=current_kw,
                                target=0,
                                step=kw_ramp_step,
                            )

                    context.setValues(fx, out_hz_addr, [current_out_hz])

                    if kw_addr is not None and current_kw is not None:
                        context.setValues(fx, kw_addr, [current_kw])

                    simulate_log(
                        f"[{device_id}][{model}] "
                        f"ON={on_off}, CMD_HZ={cmd_hz}, OUT_HZ={current_out_hz}, KW={current_kw}"
                    )

                    time.sleep(interval_sec)

                except Exception as e:
                    simulate_log(f"[ERROR] [{device_id}][{model}] Inverter loop error: {e}")
                    time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    @staticmethod
    def _ramp_towards(current: int, target: int, step: int) -> int:
        if step <= 0:
            return target

        if current < target:
            return min(current + step, target)

        if current > target:
            return max(current - step, target)

        return current

    @staticmethod
    def _calculate_target_kw(current_hz: int, max_hz: int, max_kw: int) -> int:
        if current_hz <= 0 or max_hz <= 0 or max_kw <= 0:
            return 0

        hz_ratio = min(current_hz, max_hz) / max_hz
        return int(max_kw * hz_ratio)