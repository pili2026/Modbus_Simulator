import math
import struct

from model.profile_enum import ProfileEnum


class ProfileGenerator:
    @staticmethod
    def generate(profile: dict, t: int, current_value: float | None = None) -> float:
        ptype = profile.get("type")

        if ptype == ProfileEnum.RAMP:
            return ProfileGenerator._ramp(profile, current_value)

        if ptype == ProfileEnum.WAVE:
            return ProfileGenerator._wave(profile, t)

        if ptype == ProfileEnum.CONSTANT:
            return ProfileGenerator._constant(profile)

        if ptype == ProfileEnum.TOGGLE:
            return ProfileGenerator._toggle(profile, t)

        if ptype == ProfileEnum.PULSE:
            return ProfileGenerator._pulse(profile, t)

        return 0.0

    @staticmethod
    def _ramp(profile: dict, current: float | None) -> float:
        step = profile.get("step", 1.0)
        min_val = profile.get("min", 0.0)
        max_val = profile.get("max", 100.0)
        current = current if current is not None else min_val
        next_val = current + step
        return min_val if next_val > max_val else next_val

    @staticmethod
    def _wave(profile: dict, t: int) -> float:
        period = profile.get("period", 20)
        amplitude = profile.get("amplitude", 50)
        base = profile.get("base", 500)
        radians = 2 * math.pi * (t % period) / period
        return base + amplitude * math.sin(radians)

    @staticmethod
    def _constant(profile: dict) -> float:
        return float(profile.get("value", 0))

    @staticmethod
    def _toggle(profile: dict, t: int) -> int:
        interval = profile.get("interval", 1)
        return int((t // interval) % 2)

    @staticmethod
    def _pulse(profile: dict, t: int) -> int:
        high = profile.get("high_time", 1)
        low = profile.get("low_time", 1)
        cycle = high + low
        return int((t % cycle) < high)

    @staticmethod
    def decode_float(words: list[int]) -> float:
        return struct.unpack(">f", struct.pack(">HH", *words))[0]
