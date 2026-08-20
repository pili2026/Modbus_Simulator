from model.profile_enum import ProfileEnum
from simulator.profile_generator import ProfileGenerator


class PinValueReader:
    def __init__(self, context):
        self.context = context

    def get_current_value(self, pin: dict, fx_code: int, addr: int) -> int | float | None:
        profile = pin.get("profile", {})
        profile_type = str(profile.get("type", "")).lower()
        if profile_type not in {
            ProfileEnum.RAMP,
            ProfileEnum.RANDOM,
            ProfileEnum.HOLD,
        }:
            return None

        try:
            if pin.get("type") == "float":
                raw = self.context.getValues(fx_code, addr, count=2)
                if raw and len(raw) == 2:
                    return ProfileGenerator.decode_float(raw)
            else:
                values = self.context.getValues(fx_code, addr, count=1)
                if values:
                    return values[0]
        except Exception:
            return None

        return None
