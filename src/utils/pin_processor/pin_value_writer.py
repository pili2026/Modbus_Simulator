import struct


class PinValueWriter:
    def __init__(self, context, logger=print):
        self.context = context
        self.logger = logger

    def write(self, pin: dict, fx_code: int, addr: int, val: int | float, log_ctx: str):
        pin_type: str = (pin.get("type") or "").lower()
        bit = pin.get("bit")

        match pin_type:
            case "float":
                self._write_float(fx_code, addr, float(val), log_ctx)

            case "float32_wordswap":
                self._write_float32_wordswap(fx_code, addr, float(val), log_ctx)

            case "float32_be":
                self._write_float32_be(fx_code, addr, float(val), log_ctx)

            case _ if bit is not None:
                self._write_bit(fx_code, addr, bit, int(val), log_ctx)

            case _:
                n1 = float(pin.get("n1", 0))
                n2 = float(pin.get("n2", 1))
                n3 = float(pin.get("n3", 0))
                self._write_int(fx_code, addr, int(val), n1, n2, n3, log_ctx)

    def _write_float(self, fx_code: int, addr: int, val: float, log_ctx: str):
        float_bytes = struct.pack(">f", val)
        float_regs = list(struct.unpack(">HH", float_bytes))
        self.context.setValues(fx_code, addr, float_regs)
        self.logger(f"{log_ctx} FLOAT = {val:.2f} → raw={float_regs}")

    def _write_float32_wordswap(self, fx_code: int, addr: int, val: float, log_ctx: str):
        """DO750 O2: big-endian + word-swap (BADC)"""
        float_bytes = struct.pack(">f", float(val))
        high_word = int.from_bytes(float_bytes[0:2], "big")
        low_word = int.from_bytes(float_bytes[2:4], "big")
        float_regs = [low_word & 0xFFFF, high_word & 0xFFFF]  # word-swap
        self.context.setValues(fx_code, addr, float_regs)
        self.logger(f"{log_ctx} FLOAT32_WORDSWAP = {val:.2f} → raw={float_regs}")

    def _write_float32_be(self, fx_code: int, addr: int, val: float, log_ctx: str):
        """Standard big-endian (ABCD)"""
        float_bytes = struct.pack(">f", float(val))
        high_word = int.from_bytes(float_bytes[0:2], "big")
        low_word = int.from_bytes(float_bytes[2:4], "big")
        float_regs = [high_word & 0xFFFF, low_word & 0xFFFF]
        self.context.setValues(fx_code, addr, float_regs)
        self.logger(f"{log_ctx} FLOAT32_BE = {val:.2f} → raw={float_regs}")

    def _write_bit(self, fx_code: int, addr: int, bit: int, val: int, log_ctx: str):
        values = self.context.getValues(fx_code, addr, count=1)
        if not values:
            self.logger(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
            return
        origin_val = values[0]
        mask = 1 << bit
        new_val = (origin_val & ~mask) | ((val << bit) & mask)
        self.context.setValues(fx_code, addr, [new_val])
        self.logger(f"{log_ctx} (bit={bit}) val={val} → raw={new_val}")

    def _write_int(self, fx_code: int, addr: int, val: int, n1: float, n2: float, n3: float, log_ctx: str):
        self.context.setValues(fx_code, addr, [val])
        decoded_val = n1 + n2 * val + n3 * (val**2)
        self.logger(f"{log_ctx} raw={val} → value={decoded_val:.2f}")
