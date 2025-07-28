import threading
import time
from datetime import datetime

from pymodbus.datastore import ModbusSlaveContext

from handler.dout_monitor_handler import DOutMonitorHandler
from handler.flow_handler import SutoFlowHandler
from simulator.profile_generator import ProfileGenerator
from utils.pin_processor.pin_value_reader import PinValueReader
from utils.pin_processor.pin_value_writer import PinValueWriter


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

    def start(self, fx_code: int, base_address: int = 0, interval_sec: float = 1.0):
        """
        Start the background simulation thread.

        :param fx_code: Modbus function code (e.g., 3 or 4)
        :param base_address: base register address
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
                    offset = pin.get("offset")
                    addr = base_address + int(offset)
                    name = pin.get("name", f"offset_{offset}")
                    log_ctx = self._format_log_ctx(device_id, model, name, addr)

                    # Check if any pin handler wants to handle this pin
                    handled = False
                    for handler in self.pin_handlers:
                        if handler.should_handle(model, pin):
                            handler.handle(self.context, fx_code, addr, pin, self._log, log_ctx)
                            handled = True
                            break

                    if handled:
                        continue  # skip normal profile handling if already handled

                    # Process using profile generator
                    profile = pin.get("profile")
                    if not profile:
                        continue

                    current_val: int | float | None = self.reader.get_current_value(pin, fx_code, addr)
                    val: float = ProfileGenerator.generate(profile, t, current_val)
                    self.writer.write(pin, fx_code, addr, val, log_ctx)

                # Apply device-wide handlers after all pins are processed
                for handler in self.device_handlers:
                    handler.handle(fx_code)

                t += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        """
        Stop the simulation loop.
        """
        self.running = False

    def _format_log_ctx(self, device_id: str, model: str, name: str, addr: int) -> str:
        """
        Format the prefix used for logging per pin.

        :return: formatted string for log context
        """
        return f"[{device_id}][{model}] {name} (addr={addr})"

    @staticmethod
    def _log(msg: str):
        """
        Default logger using timestamp.
        """
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
