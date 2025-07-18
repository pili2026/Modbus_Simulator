import threading
import time
from datetime import datetime

from pymodbus.datastore import ModbusSlaveContext


class ProfileUpdater:
    def __init__(self, config: dict, context: ModbusSlaveContext):
        self.config = config
        self.context = context
        self.running = True

    def start(self, fx_code: int, base_address: int = 0, interval_sec: float = 1.0):
        t = 0
        model = self.config.get("model", "")
        pin_list = self.config.get("pins", [])

        def _loop():
            nonlocal t
            while self.running:
                for pin in pin_list:
                    offset = pin.get("offset")
                    profile = pin.get("profile")
                    name = pin.get("name", f"offset_{offset}")
                    bit = pin.get("bit")
                    n1 = float(pin.get("n1", 0))
                    n2 = float(pin.get("n2", 1))
                    n3 = float(pin.get("n3", 0))
                    addr = base_address + int(offset)

                    # DOut: Master-controlled, skip profile update, only log value
                    if model.upper() == "IMA_C" and name.startswith("DOut"):
                        values = self.context.getValues(fx_code, addr, count=1)
                        if not values:
                            self._log(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
                            continue
                        origin_val = values[0]
                        if bit is not None:
                            val = (origin_val >> bit) & 1
                            self._log(f"[{model}] [DOut monitor] {name} (addr={addr}, bit={bit}) ← master_val={val}")
                        continue

                    # No profile defined, skip
                    if not profile:
                        continue

                    # Generate value based on profile
                    val = int(self._generate_value_from_profile(profile, t, fx_code, addr))

                    # Bit-masked value writing (for DIn01~DIn04)
                    if bit is not None:
                        values = self.context.getValues(fx_code, addr, count=1)
                        if not values:
                            self._log(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
                            continue
                        origin_val = values[0]
                        mask = 1 << bit
                        new_val = (origin_val & ~mask) | ((val << bit) & mask)
                        self.context.setValues(fx_code, addr, [new_val])
                        self._log(f"[{model}] {name} (addr={addr}, bit={bit}, t={t}) val={val} → raw={new_val}")
                    else:
                        # Full-register write (e.g., ByPass)
                        self.context.setValues(fx_code, addr, [val])
                        decoded_val = self._decode_value(val, n1, n2, n3)
                        self._log(f"[{model}] {name} (addr={addr}, t={t}) raw={val} → value={decoded_val:.2f}")
                t += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _generate_value_from_profile(self, profile, t, fx_code, addr):
        ptype = profile.get("type")
        if ptype == "ramp":
            step = profile.get("step", 1)
            min_val = profile.get("min", 0)
            max_val = profile.get("max", 100)
            try:
                curr_val = self.context.getValues(fx_code, addr, count=1)[0]
            except Exception:
                curr_val = min_val
            next_val = curr_val + step
            return min_val if next_val > max_val else next_val
        if ptype == "wave":
            import math

            period = profile.get("period", 20)
            amplitude = profile.get("amplitude", 50)
            base = profile.get("base", 500)
            radians = 2 * math.pi * (t % period) / period
            return base + amplitude * math.sin(radians)
        if ptype == "constant":
            return int(profile.get("value", 0))
        if ptype == "toggle":
            interval = profile.get("interval", 1)
            return int((t // interval) % 2)
        if ptype == "pulse":
            high = profile.get("high_time", 1)
            low = profile.get("low_time", 1)
            cycle = high + low
            return int((t % cycle) < high)
        return 0.0

    @staticmethod
    def _log(msg: str):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")

    @staticmethod
    def _decode_value(raw: int, n1: float, n2: float, n3: float) -> float:
        return n1 + n2 * raw + n3 * (raw**2)
