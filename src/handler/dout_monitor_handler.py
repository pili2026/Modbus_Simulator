from base.base_pin_handler import BasePinHandler


class DOutMonitorHandler(BasePinHandler):
    def should_handle(self, model: str, pin: dict) -> bool:
        name: str = pin.get("name", "")
        return model.upper() == "IMA_C" and name.startswith("DOut")

    def handle(self, context, fx_code: int, addr: int, pin: dict, log_fn, log_ctx: str):
        bit = pin.get("bit")
        values = context.getValues(fx_code, addr, count=1)
        if not values:
            log_fn(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
            return

        origin_val = values[0]
        if bit is not None:
            val = (origin_val >> bit) & 1
            log_fn(f"{log_ctx} [DOut monitor] bit={bit} ← master_val={val}")
