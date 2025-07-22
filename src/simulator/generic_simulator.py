import threading
import time

from pymodbus.datastore import ModbusSlaveContext

from base.modbus_context_builder import ModbusContextBuilder
from simulator.profile_updater import ProfileUpdater
from utils.constants import FC_CODE_MAP
from utils.value_formatter import simulate_log


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

        if device_type == "io_module":
            base_addr = self.config.get("base_address", 0)
            self.updater = ProfileUpdater(self.config, self.context)
            self.updater.start(fx_code=fx, base_address=base_addr)

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
                        current_out += 10
                    elif on_off == 0 and current_out > 0:
                        current_out -= 10
                    current_out = max(0, min(cmd_hz, current_out))

                    self.context.setValues(fx, out_hz_addr, [current_out])
                    simulate_log(f"[{device_id}][{model}] ON={on_off}, CMD={cmd_hz}, OUT={current_out}")
                    time.sleep(1)
                except Exception as e:
                    simulate_log(f"[ERROR] {model} loop error: {e}")

        threading.Thread(target=_loop, daemon=True).start()
