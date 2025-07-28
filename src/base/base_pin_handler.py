class BasePinHandler:
    def should_handle(self, model: str, pin: dict) -> bool:
        raise NotImplementedError

    def handle(self, context, fx_code: int, addr: int, pin: dict, log_fn, log_ctx: str):
        raise NotImplementedError
