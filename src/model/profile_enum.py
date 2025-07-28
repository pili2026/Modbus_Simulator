from enum import StrEnum


class ProfileEnum(StrEnum):
    RAMP = "ramp"
    WAVE = "wave"
    CONSTANT = "constant"
    TOGGLE = "toggle"
    PULSE = "pulse"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_
