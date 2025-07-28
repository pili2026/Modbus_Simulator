import struct

from base.base_device_handler import BaseDeviceHandler
from utils.log_formatter import simulate_log


class SutoFlowHandler(BaseDeviceHandler):
    def __init__(self, context, config: dict):
        self.context = context
        self.config = config
        self.logger = simulate_log

    def handle(self, fx_code: int):
        if self.config.get("model") != "SUTO_FLOW":
            return  # skip if not a flow model

        pins = {p.get("name"): p for p in self.config.get("pins", [])}
        consumption_pin = pins.get("FLOW_CONSUMPTION")
        rev_pin = pins.get("FLOW_REVCONSUMPTION")
        direction_pin = pins.get("FLOW_DIRECTION")

        if not direction_pin:
            self._log("FLOW_DIRECTION not defined, skipping update.")
            return

        direction_addr = int(direction_pin["offset"])
        direction_vals = self.context.getValues(fx_code, direction_addr, count=1)
        if not direction_vals:
            self._log(f"FLOW_DIRECTION read failed at addr={direction_addr}")
            return

        direction_raw = direction_vals[0]

        if not (0 <= direction_raw <= 0xFFFF):
            self._log(f"[ERROR] Invalid raw direction value: {direction_raw}")
            return

        direction = struct.unpack("<h", struct.pack("<H", direction_raw))[0]
        self._log(f"FLOW_DIRECTION raw={direction_raw} → signed={direction}")

        if direction == 1 and consumption_pin:
            self._increment_counter(consumption_pin, fx_code, "FLOW_CONSUMPTION")
        elif direction == -1 and rev_pin:
            self._increment_counter(rev_pin, fx_code, "FLOW_REVCONSUMPTION")

    def _increment_counter(self, pin: dict, fx_code: int, label: str):
        addr = int(pin["offset"])
        val = self.context.getValues(fx_code, addr, count=2)
        if not val or len(val) < 2:
            self._log(f"{label} read failed at addr={addr}")
            return
        value = self._decode_uint32_le(val) + 1
        self.context.setValues(fx_code, addr, self._encode_uint32_le(value))
        self._log(f"{label} +=1 → {value}")

    def _log(self, msg: str):
        device_id = self.config.get("device_id", "Unknown")
        model = self.config.get("model", "Unknown")
        self.logger(f"[{device_id}][{model}] {msg}")

    @staticmethod
    def _decode_uint32_le(words: list[int]) -> int:
        return struct.unpack("<I", struct.pack("<HH", *words))[0]

    @staticmethod
    def _encode_uint32_le(value: int) -> list[int]:
        return list(struct.unpack("<HH", struct.pack("<I", value)))
