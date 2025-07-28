from model.profile_enum import ProfileEnum
from simulator.profile_generator import ProfileGenerator


class PinValueReader:
    def __init__(self, context):
        self.context = context

    def get_current_value(self, pin: dict, fx_code: int, addr: int) -> int | float | None:
        profile = pin.get("profile", {})
        if profile.get("type") != ProfileEnum.RAMP:
            return None

        try:
            if pin.get("type") == "float":
                raw = self.context.getValues(fx_code, addr, count=2)
                if raw and len(raw) == 2:
                    return ProfileGenerator.decode_float(raw)
            else:
                return self.context.getValues(fx_code, addr, count=1)[0]
        except Exception:
            return None
