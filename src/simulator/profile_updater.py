import threading
import time
from datetime import datetime

from pymodbus.datastore import ModbusSlaveContext

from handler.dout_monitor_handler import DOutMonitorHandler
from handler.flow_handler import SutoFlowHandler
from simulator.profile_generator import ProfileGenerator
from utils.pin_processor.pin_value_reader import PinValueReader
from utils.pin_processor.pin_value_writer import PinValueWriter

# ----- Function code mapping -----
FC_MAP = {
    "coil": 1,  # FC1 read coils, FC5/15 write coils
    "discrete": 2,  # FC2 read discrete inputs
    "holding": 3,  # FC3 read holding registers, FC6/16 write holding registers
    "input": 4,  # FC4 read input registers
}


def _fx_for_pin(pin: dict) -> int:
    """Determine Modbus function code based on pin.register_type (default: holding)"""
    regtype = (pin.get("register_type") or "holding").lower()
    return FC_MAP.get(regtype, 3)


class ProfileUpdater:
    """
    ProfileUpdater
    Responsible for background simulation loop: generates values based on pin profiles
    and writes them into Modbus context, while dispatching special handlers for
    specific pins or devices.

    New in this version:
    - Support for "ramp + carry_to": when low word exceeds max, automatically increments
      the specified high word (can increment multiple times if needed).
    """

    def __init__(self, config: dict, context: ModbusSlaveContext):
        self.config = config
        self.context = context
        self.running = True

        self.reader = PinValueReader(context)
        self.writer = PinValueWriter(context, self._log)

        # Handlers for specific pins and devices
        self.pin_handlers = [DOutMonitorHandler()]
        self.device_handlers = [SutoFlowHandler(context, config)]

    def start(self, base_address: int = 0, interval_sec: float = 1.0):
        """
        Start the background simulation thread.

        :param base_address: Starting register address (0-based in ModbusSlaveContext)
        :param interval_sec: Loop interval in seconds
        """
        t = 0
        model = self.config.get("model", "")
        pin_list = self.config.get("pins", []) or []
        device_id = self.config.get("device_id", "Unknown")

        def _loop():
            nonlocal t
            while self.running:
                for pin in pin_list:
                    # Target address
                    offset = int(pin.get("offset", 0))
                    addr = base_address + offset

                    # Determine fx code based on pin
                    fx_code = _fx_for_pin(pin)

                    name = pin.get("name", f"offset_{offset}")
                    log_ctx = self._format_log_ctx(device_id, model, name, addr)

                    # Handle pin-level special behavior first (ex: DOut bit monitor)
                    handled = False
                    for handler in self.pin_handlers:
                        try:
                            if handler.should_handle(model, pin):
                                handler.handle(self.context, fx_code, addr, pin, self._log, log_ctx)
                                handled = True
                                break
                        except Exception as e:
                            self._log(f"{log_ctx} [WARN] pin handler error: {e}")

                    if handled:
                        # If handler processed this pin, skip profile write
                        continue

                    # General profile logic
                    profile = pin.get("profile")
                    if not profile:
                        continue

                    # Current value
                    current_val = self.reader.get_current_value(pin, fx_code, addr)

                    # Use ProfileGenerator to produce the next value (base value)
                    val = ProfileGenerator.generate(profile, t, current_val)

                    # ---------- ★ CARRY: only active when ramp + carry_to is configured ----------
                    carry_to = None
                    try:
                        ptype = str(profile.get("type", "")).lower()
                        carry_to = profile.get("carry_to")
                    except Exception:
                        ptype = ""

                    if ptype == "ramp" and carry_to:
                        # Read ramp parameters
                        step = profile.get("step", 1)
                        try:
                            step = float(step)  # float step supported
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

                        # Use theoretical next value to detect wrap (avoid clamp/cycle inside generator)
                        try:
                            base_cv = int(current_val)
                        except Exception:
                            base_cv = min_v

                        theoretical_next = base_cv + step

                        # Count wrap occurrences (only forward ramp; reverse would need extra logic)
                        carry_count = 0
                        if theoretical_next > max_v:
                            try:
                                carry_count = int((theoretical_next - min_v) // wrap_range) + 1
                            except Exception:
                                carry_count = 1

                            # Compute wrapped low word value
                            try:
                                wrapped = min_v + int((theoretical_next - min_v) % wrap_range)
                            except Exception:
                                wrapped = min_v
                            val = wrapped

                        # Apply carry: add to high word with 16-bit wrap
                        if carry_count > 0:
                            target_pin = None
                            for tp_pin in pin_list:
                                if tp_pin.get("name") == carry_to:
                                    target_pin = tp_pin
                                    break

                            if target_pin:
                                fx_target = _fx_for_pin(target_pin)
                                target_addr = base_address + int(target_pin.get("offset", 0))
                                current_hi = self.reader.get_current_value(target_pin, fx_target, target_addr)
                                try:
                                    current_hi = int(current_hi)
                                except Exception:
                                    current_hi = 0

                                new_hi = (current_hi + carry_count) % 65536  # 16-bit wrap
                                self.writer.write(target_pin, fx_target, target_addr, new_hi, log_ctx + " [carry]")
                            else:
                                self._log(f"{log_ctx} [WARN] carry_to target '{carry_to}' not found")
                    # ---------- ★ END CARRY ----------

                    # Write final value to pin
                    self.writer.write(pin, fx_code, addr, val, log_ctx)

                # Device-level handlers (most device state registers use holding type)
                for handler in self.device_handlers:
                    try:
                        handler.handle(_fx_for_pin({"register_type": "holding"}))
                    except Exception as e:
                        self._log(f"[{device_id}][{model}] [WARN] device handler error: {e}")

                t += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        """Stop simulation loop"""
        self.running = False

    def _format_log_ctx(self, device_id: str, model: str, name: str, addr: int) -> str:
        return f"[{device_id}][{model}] {name} (addr={addr})"

    @staticmethod
    def _log(msg: str):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
