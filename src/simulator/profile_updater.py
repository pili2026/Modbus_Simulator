import struct
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
        device_id = self.config.get("device_id", "Unknown")

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

                    if model.upper() == "IMA_C" and name.startswith("DOut"):
                        values = self.context.getValues(fx_code, addr, count=1)
                        if not values:
                            self._log(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
                            continue
                        origin_val = values[0]
                        if bit is not None:
                            val = (origin_val >> bit) & 1
                            self._log(
                                f"[{device_id}][{model}] [DOut monitor] {name} (addr={addr}, bit={bit}) ← master_val={val}"
                            )
                        continue

                    if not profile:
                        continue

                    val = self._generate_value_from_profile(profile, t, fx_code, addr, pin.get("type"))

                    # Float Register Writing (2 words)
                    if pin.get("type") == "float":
                        float_bytes = struct.pack(">f", val)
                        float_regs = list(struct.unpack(">HH", float_bytes))
                        self.context.setValues(fx_code, addr, float_regs)
                        self._log(f"[{device_id}][{model}] {name} (addr={addr}) FLOAT = {val:.2f} → raw={float_regs}")
                        continue

                    # Bit Writing (1 word)
                    if bit is not None:
                        values = self.context.getValues(fx_code, addr, count=1)
                        if not values:
                            self._log(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
                            continue
                        origin_val = values[0]
                        mask = 1 << bit
                        new_val = (origin_val & ~mask) | ((int(val) << bit) & mask)
                        self.context.setValues(fx_code, addr, [new_val])
                        self._log(
                            f"[{device_id}][{model}] {name} (addr={addr}, bit={bit}, t={t}) val={val} → raw={new_val}"
                        )
                    else:
                        # Integer Writing (1 word)
                        self.context.setValues(fx_code, addr, [int(val)])
                        decoded_val = self._decode_value(int(val), n1, n2, n3)
                        self._log(
                            f"[{device_id}][{model}] {name} (addr={addr}, t={t}) raw={int(val)} → value={decoded_val:.2f}"
                        )

                # SUTO_FLOW model specific updates
                if model == "SUTO_FLOW":
                    self._update_suto_flow(fx_code)

                t += 1
                time.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _update_suto_flow(self, fx_code: int):
        pins = {p.get("name"): p for p in self.config.get("pins", [])}

        flow_pin = pins.get("FLOW_FLOW")
        consumption_pin = pins.get("FLOW_CONSUMPTION")
        rev_pin = pins.get("FLOW_REVCONSUMPTION")
        direction_pin = pins.get("FLOW_DIRECTION")

        if consumption_pin:
            addr = int(consumption_pin["offset"])
            val = self.context.getValues(fx_code, addr, count=2)
            if not val or len(val) < 2:
                self._log(f"[WARN] FLOW_CONSUMPTION read failed at addr={addr}")
                return
            value = self._decode_uint32_le(val)
            value += 1
            self.context.setValues(fx_code, addr, self._encode_uint32_le(value))
            self._log(f"[{self.config.get('device_id')}] FLOW_CONSUMPTION +=1 → {value}")

        if rev_pin:
            addr = int(rev_pin["offset"])
            val = self.context.getValues(fx_code, addr, count=2)
            if not val or len(val) < 2:
                self._log(f"[WARN] FLOW_REVCONSUMPTION read failed at addr={addr}")
                return
            value = self._decode_uint32_le(val)
            self.context.setValues(fx_code, addr, self._encode_uint32_le(value))

        if flow_pin and direction_pin:
            flow_addr = int(flow_pin["offset"])
            flow_raw = self.context.getValues(fx_code, flow_addr, count=2)
            if not flow_raw or len(flow_raw) < 2:
                self._log(f"[WARN] FLOW_FLOW read failed at addr={flow_addr}")
                return
            flow_val = self._decode_float(flow_raw)
            direction = 1 if flow_val % 2 < 1 else 0
            direction_addr = int(direction_pin["offset"])
            self.context.setValues(fx_code, direction_addr, [direction])
            self._log(f"[{self.config.get('device_id')}] FLOW_DIRECTION ← {direction}")

    def _generate_value_from_profile(self, profile, t, fx_code, addr, pin_type=None):
        ptype = profile.get("type")

        # Float Ramp
        if ptype == "ramp" and pin_type == "float":
            step = profile.get("step", 1.0)
            min_val = profile.get("min", 0.0)
            max_val = profile.get("max", 100.0)
            try:
                raw = self.context.getValues(fx_code, addr, count=2)
                curr_val = self._decode_float(raw) if raw and len(raw) == 2 else min_val
            except Exception:
                curr_val = min_val
            next_val = curr_val + step
            return min_val if next_val > max_val else next_val

        # Normal Ramp
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
    def _decode_uint32_le(words: list[int]) -> int:
        return struct.unpack("<I", struct.pack("<HH", *words))[0]

    @staticmethod
    def _encode_uint32_le(value: int) -> list[int]:
        return list(struct.unpack("<HH", struct.pack("<I", value)))

    @staticmethod
    def _decode_float(words: list[int]) -> float:
        return struct.unpack(">f", struct.pack(">HH", *words))[0]

    @staticmethod
    def _decode_value(raw: int, n1: float, n2: float, n3: float) -> float:
        return n1 + n2 * raw + n3 * (raw**2)
