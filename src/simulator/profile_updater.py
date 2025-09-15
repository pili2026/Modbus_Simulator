import threading
import time
from datetime import datetime

from pymodbus.datastore import ModbusSlaveContext

from handler.dout_monitor_handler import DOutMonitorHandler
from handler.flow_handler import SutoFlowHandler
from simulator.profile_generator import ProfileGenerator
from utils.pin_processor.pin_value_reader import PinValueReader
from utils.pin_processor.pin_value_writer import PinValueWriter

# ----- NEW: function code mapping -----
FC_MAP = {
    "coil": 1,  # read coils (FC1), write single/multiple coil (FC5/15)
    "discrete": 2,  # read discrete inputs (FC2)
    "holding": 3,  # read holding registers (FC3), write single/multi reg (FC6/16)
    "input": 4,  # read input registers (FC4)
}


def _fx_for_pin(pin: dict) -> int:
    regtype = (pin.get("register_type") or "holding").lower()
    return FC_MAP.get(regtype, 3)


class ProfileUpdater:
    """
    This class is responsible for running the simulation update loop,
    generating values based on profiles and applying them to the Modbus context.
    It also delegates special logic to pin-level and device-level handlers.
    """

    def __init__(self, config: dict, context: ModbusSlaveContext):
        self.config = config
        self.context = context
        self.running = True

        self.reader = PinValueReader(context)
        self.writer = PinValueWriter(context, self._log)

        # Initialize all pin-level and device-level logic handlers
        self.pin_handlers = [DOutMonitorHandler()]
        self.device_handlers = [SutoFlowHandler(context, config)]

    # ----- CHANGED: drop fx_code parameter -----
    def start(self, base_address: int = 0, interval_sec: float = 1.0):
        """
        Start the background simulation thread.

        :param base_address: base register address (0-based for ModbusSlaveContext)
        :param interval_sec: how often to run the update loop
        """
        t = 0
        model = self.config.get("model", "")
        pin_list = self.config.get("pins", [])
        device_id = self.config.get("device_id", "Unknown")

        def _loop():
            nonlocal t
            while self.running:
                for pin in pin_list:
                    # address & context
                    offset = int(pin.get("offset", 0))
                    addr = base_address + offset

                    # NEW: per-pin fx_code (by register_type)
                    fx_code = _fx_for_pin(pin)

                    name = pin.get("name", f"offset_{offset}")
                    log_ctx = self._format_log_ctx(device_id, model, name, addr)

                    # Pin-level handler (e.g., DOut monitor / bit view)
                    handled = False
                    for handler in self.pin_handlers:
                        if handler.should_handle(model, pin):
                            handler.handle(self.context, fx_code, addr, pin, self._log, log_ctx)
                            handled = True
                            break

                    if handled:
                        # If handler took care of this pin, skip the profile overwrite
                        continue

                    # Normal profile flow
                    profile = pin.get("profile")
                    if not profile:
                        continue

                    current_val = self.reader.get_current_value(pin, fx_code, addr)
                    val = ProfileGenerator.generate(profile, t, current_val)
                    self.writer.write(pin, fx_code, addr, val, log_ctx)

                # device-wide handlers after all pins
                for handler in self.device_handlers:
                    handler.handle(_fx_for_pin({"register_type": "holding"}))  # or keep original assumption if needed

                t += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        """Stop the simulation loop."""
        self.running = False

    def _format_log_ctx(self, device_id: str, model: str, name: str, addr: int) -> str:
        return f"[{device_id}][{model}] {name} (addr={addr})"

    @staticmethod
    def _log(msg: str):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
