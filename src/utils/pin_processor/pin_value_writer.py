from utils.float_codec import encode_float32_little_swap


class PinValueWriter:
    def __init__(self, context, logger=print):
        self.context = context
        self.logger = logger

    def write(self, pin: dict, fx_code: int, addr: int, val: int | float, log_ctx: str):
        pin_type = pin.get("type")
        bit = pin.get("bit")

        if pin_type == "float":
            self._write_float(fx_code, addr, val, log_ctx)
        elif bit is not None:
            self._write_bit(fx_code, addr, bit, int(val), log_ctx)
        else:
            n1 = float(pin.get("n1", 0))
            n2 = float(pin.get("n2", 1))
            n3 = float(pin.get("n3", 0))
            self._write_int(fx_code, addr, int(val), n1, n2, n3, log_ctx)

    def _write_float(self, fx_code: int, addr: int, val: float, log_ctx: str):
        float_regs = encode_float32_little_swap(float(val))
        self.context.setValues(fx_code, addr, float_regs)
        self.logger(f"{log_ctx} FLOAT = {val:.2f} → raw={float_regs}")

    def _write_bit(self, fx_code: int, addr: int, bit: int, val: int, log_ctx: str):
        values = self.context.getValues(fx_code, addr, count=1)
        if not values:
            self.logger(
                f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping."
            )
            return
        origin_val = values[0]
        mask = 1 << bit
        new_val = (origin_val & ~mask) | ((val << bit) & mask)
        self.context.setValues(fx_code, addr, [new_val])
        self.logger(f"{log_ctx} (bit={bit}) val={val} → raw={new_val}")

    def _write_int(
        self,
        fx_code: int,
        addr: int,
        val: int,
        n1: float,
        n2: float,
        n3: float,
        log_ctx: str,
    ):
        raw = int(val) & 0xFFFF
        self.context.setValues(fx_code, addr, [raw])
        decoded_val = n1 + n2 * val + n3 * (val**2)
        self.logger(f"{log_ctx} raw={raw} → value={decoded_val:.2f}")
