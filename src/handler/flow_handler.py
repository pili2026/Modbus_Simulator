import struct

from base.base_device_handler import BaseDeviceHandler
from utils.float_codec import decode_float32_little_swap, encode_float32_little_swap
from utils.log_formatter import simulate_log


class SutoFlowHandler(BaseDeviceHandler):
    SUPPORTED_MODELS = {"SUTO_FLOW", "YUDEN_FLOW"}

    def __init__(self, context, config: dict):
        self.context = context
        self.config = config
        self.logger = simulate_log

    def handle(self, fx_code: int):
        if self.config.get("model") not in self.SUPPORTED_MODELS:
            return

        pins = {pin.get("name"): pin for pin in self.config.get("pins", [])}
        consumption_pin = pins.get("FLOW_CONSUMPTION")
        rev_pin = pins.get("FLOW_REVCONSUMPTION")
        direction_pin = pins.get("FLOW_DIRECTION")

        if not direction_pin:
            self._log("FLOW_DIRECTION not defined, skipping update.")
            return

        direction_addr = int(direction_pin["offset"])
        words = self.context.getValues(fx_code, direction_addr, count=1)
        if not words:
            self._log(f"FLOW_DIRECTION read failed at addr={direction_addr}")
            return

        raw = words[0]
        if not (0 <= raw <= 0xFFFF):
            self._log(f"[ERROR] Invalid raw direction value: {raw}")
            return
        direction = struct.unpack("<h", struct.pack("<H", raw))[0]
        self._log(f"FLOW_DIRECTION raw={raw} → signed={direction}")

        if direction == 1 and consumption_pin:
            self._increment_counter(consumption_pin, fx_code, "FLOW_CONSUMPTION")
        elif direction == -1 and rev_pin:
            self._increment_counter(rev_pin, fx_code, "FLOW_REVCONSUMPTION")

    def _increment_counter(self, pin: dict, fx_code: int, label: str):
        addr = int(pin["offset"])
        pin_type = (pin.get("type") or "").lower()
        words = self.context.getValues(fx_code, addr, count=2)
        if not words or len(words) < 2:
            self._log(f"{label} read failed at addr={addr}")
            return

        if pin_type == "float":
            current = decode_float32_little_swap(words)
            new_value = float(current) + 1.0
            self.context.setValues(fx_code, addr, encode_float32_little_swap(new_value))
            self._log(f"{label} (float32) +=1 → {new_value}")
        else:
            current = self._decode_uint32_le(words)
            new_value = int(current) + 1
            self.context.setValues(fx_code, addr, self._encode_uint32_le(new_value))
            self._log(f"{label} (uint32) +=1 → {new_value}")

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

    @staticmethod
    def _decode_float32_le_swap(words: list[int]) -> float:
        return decode_float32_little_swap(words)

    @staticmethod
    def _encode_float32_le_swap(value: float) -> list[int]:
        return encode_float32_little_swap(value)
