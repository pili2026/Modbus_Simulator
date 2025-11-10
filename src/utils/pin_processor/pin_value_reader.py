import struct

from pymodbus.datastore import ModbusSlaveContext


class PinValueReader:
    def __init__(self, context: ModbusSlaveContext):
        self.context = context

    def get_current_value(self, pin: dict, fx_code: int, addr: int) -> float:
        """Read current value from context based on pin type"""
        pin_type: str = pin.get("type", "").lower()
        bit = pin.get("bit")

        if pin_type == "float":
            return self._read_float(fx_code, addr)
        elif pin_type == "float32_wordswap":
            return self._read_float32_wordswap(fx_code, addr)
        elif pin_type == "float32_be":
            return self._read_float32_be(fx_code, addr)
        elif bit is not None:
            # Read bit
            values = self.context.getValues(fx_code, addr, count=1)
            if values:
                return (values[0] >> bit) & 1
            return 0
        else:
            # Read single word as int
            values = self.context.getValues(fx_code, addr, count=1)
            return values[0] if values else 0

    def _read_float(self, fx_code: int, addr: int) -> float:
        """Standard big-endian float"""
        values = self.context.getValues(fx_code, addr, count=2)
        if not values or len(values) < 2:
            return 0.0
        float_bytes = struct.pack(">HH", values[0], values[1])
        return struct.unpack(">f", float_bytes)[0]

    def _read_float32_wordswap(self, fx_code: int, addr: int) -> float:
        """DO750 O2: big-endian + word-swap (BADC)"""
        values = self.context.getValues(fx_code, addr, count=2)
        if not values or len(values) < 2:
            return 0.0
        # values = [low_word, high_word]，need change sequence
        low_word, high_word = values[0], values[1]
        float_bytes = struct.pack(">HH", high_word, low_word)
        return struct.unpack(">f", float_bytes)[0]

    def _read_float32_be(self, fx_code: int, addr: int) -> float:
        """Standard big-endian (ABCD)"""
        values = self.context.getValues(fx_code, addr, count=2)
        if not values or len(values) < 2:
            return 0.0
        high_word, low_word = values[0], values[1]
        float_bytes = struct.pack(">HH", high_word, low_word)
        return struct.unpack(">f", float_bytes)[0]
