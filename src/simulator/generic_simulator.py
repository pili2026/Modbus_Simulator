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
        self.context, _ = builder.build()

        device_type = self.config.get("type", "").lower()
        base_addr = int(self.config.get("base_address", 0))
        interval_sec = float(self.config.get("interval_sec", 1.0))

        pins = self.config.get("pins", [])
        has_profiles = any(isinstance(pin.get("profile"), dict) for pin in pins)
        should_start_updater = has_profiles or any(
            (pin.get("name", "").startswith("DOut") or pin.get("bit") is not None)
            for pin in pins
        )
        if should_start_updater:
            self.updater = ProfileUpdater(self.config, self.context)
            self.updater.start(base_address=base_addr, interval_sec=interval_sec)

        if device_type == "inverter":
            self._start_inverter_logic()
        return self.context

    def _start_inverter_logic(self):
        model = self.config.get("model", "")
        device_id = self.config.get("device_id", "Unknown")
        fx = FC_CODE_MAP["hr"]
        mapping = self.config.get("register_mapping", {})
        on_off_addr = mapping.get("on_off")
        cmd_hz_addr = mapping.get("command_hz")
        out_hz_addr = mapping.get("output_hz")

        def _loop():
            while self.running:
                try:
                    on_off = self.context.getValues(fx, on_off_addr, count=1)[0]
                    cmd_hz = self.context.getValues(fx, cmd_hz_addr, count=1)[0]
                    current_out = self.context.getValues(fx, out_hz_addr, count=1)[0]

                    if on_off == 1 and current_out < cmd_hz:
                        current_out += 100
                    elif on_off == 0 and current_out > 0:
                        current_out -= 100
                    current_out = max(0, min(cmd_hz, current_out))

                    self.context.setValues(fx, out_hz_addr, [current_out])
                    simulate_log(
                        f"[{device_id}][{model}] ON={on_off}, CMD={cmd_hz}, OUT={current_out}"
                    )
                    time.sleep(1)
                except Exception as exc:
                    simulate_log(f"[ERROR] {model} loop error: {exc}")

        threading.Thread(target=_loop, daemon=True).start()
