from base.base_pin_handler import BasePinHandler


class DOutMonitorHandler(BasePinHandler):
    """
    Monitors digital output (DO) pins and logs their current state.
    Works for both IMA_C (holding+bit) and JY_DAM0204 (coil).
    """

    def should_handle(self, model: str, pin: dict) -> bool:
        name = pin.get("name", "").lower()
        regtype = (pin.get("register_type") or "").lower()

        # Accept if any of the following conditions are met:
        # 1. Model is IMA_C and the name starts with "DOut"
        # 2. register_type is "coil" (e.g., JY_DAM0204)
        # 3. Name contains "dout" (case-insensitive loose match)
        return (
            (model.upper() == "IMA_C" and name.startswith("dout"))
            or (regtype == "coil")
            or ("dout" in name)
        )

    def handle(self, context, fx_code: int, addr: int, pin: dict, log_fn, log_ctx: str):
        bit = pin.get("bit")

        try:
            values = context.getValues(fx_code, addr, count=1)
        except Exception as e:
            log_fn(
                f"[ERROR] DOutMonitorHandler: failed to read fx={fx_code}, addr={addr} ({e})"
            )
            return

        if not values:
            log_fn(f"[WARN] Cannot read address {addr} from fx={fx_code}, skipping.")
            return

        origin_val = values[0]
        if bit is not None:
            val = (origin_val >> bit) & 1
            log_fn(f"{log_ctx} [DOut monitor] bit={bit} ← master_val={val}")
        else:
            log_fn(f"{log_ctx} [DOut monitor] ← coil_val={origin_val}")
