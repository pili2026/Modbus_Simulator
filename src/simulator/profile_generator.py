import math
import random
from typing import Any

from model.profile_enum import ProfileEnum
from utils.float_codec import decode_float32_little_swap


class ProfileGenerator:
    @staticmethod
    def generate(
        profile: dict,
        t: float,
        current_value: float | None = None,
        state: dict[str, Any] | None = None,
    ) -> float:
        ptype = str(profile.get("type", "")).lower()

        if ptype == ProfileEnum.RAMP:
            value = ProfileGenerator._ramp(profile, current_value, state)
        elif ptype == ProfileEnum.WAVE:
            value = ProfileGenerator._wave(profile, t, state)
        elif ptype == ProfileEnum.CONSTANT:
            value = ProfileGenerator._constant(profile)
        elif ptype == ProfileEnum.TOGGLE:
            value = ProfileGenerator._toggle(profile, t)
        elif ptype == ProfileEnum.PULSE:
            value = ProfileGenerator._pulse(profile, t)
        elif ptype == ProfileEnum.RANDOM:
            value = ProfileGenerator._random_walk(profile, current_value, state)
        elif ptype == ProfileEnum.HOLD:
            value = ProfileGenerator._hold(profile, current_value)
        else:
            value = 0.0

        return ProfileGenerator._clamp(value, profile)

    @staticmethod
    def _ramp(
        profile: dict,
        current: float | None,
        state: dict[str, Any] | None = None,
    ) -> float:
        step = float(profile.get("step", 1.0))
        min_val = float(profile.get("min", 0.0))
        max_val = float(profile.get("max", 100.0))
        current = float(current) if current is not None else min_val
        runtime = state if state is not None else {}

        if "phase_deg" in profile and not runtime.get("phase_applied", False):
            span = max_val - min_val
            if span > 0:
                phase_fraction = (
                    float(profile.get("phase_deg", 0.0)) % 360.0
                ) / 360.0
                current = min_val + (
                    (current - min_val + span * phase_fraction) % span
                )
            runtime["phase_applied"] = True

        if profile.get("bounce", False):
            if max_val <= min_val:
                return min_val

            direction = int(runtime.get("direction", 1 if step >= 0 else -1))
            next_val = current + abs(step) * direction
            while next_val > max_val or next_val < min_val:
                if next_val > max_val:
                    next_val = max_val - (next_val - max_val)
                    direction = -1
                elif next_val < min_val:
                    next_val = min_val + (min_val - next_val)
                    direction = 1

            runtime["direction"] = direction
            value = next_val
        else:
            # Preserve the legacy sawtooth sequence exactly when bounce is absent.
            next_val = current + step
            value = min_val if next_val > max_val else next_val

        return ProfileGenerator._with_noise(value, profile, state)

    @staticmethod
    def _wave(profile: dict, t: float, state: dict[str, Any] | None = None) -> float:
        period = float(profile.get("period_sec", profile.get("period", 20)))
        if period <= 0:
            period = 20.0

        if "min" in profile and "max" in profile:
            min_val = float(profile["min"])
            max_val = float(profile["max"])
            base = (min_val + max_val) / 2.0
            amplitude = (max_val - min_val) / 2.0
        else:
            amplitude = float(profile.get("amplitude", 50))
            base = float(profile.get("base", 500))

        phase_radians = math.radians(float(profile.get("phase_deg", 0.0)))
        radians = 2 * math.pi * (float(t) % period) / period + phase_radians
        value = base + amplitude * math.sin(radians)
        return ProfileGenerator._with_noise(value, profile, state)

    @staticmethod
    def _constant(profile: dict) -> float:
        return float(profile.get("value", 0))

    @staticmethod
    def _toggle(profile: dict, t: float) -> int:
        interval = float(profile.get("interval", 1))
        if interval <= 0:
            interval = 1.0
        return int(math.floor(float(t) / interval) % 2)

    @staticmethod
    def _pulse(profile: dict, t: float) -> int:
        high = float(profile.get("high_time", 1))
        low = float(profile.get("low_time", 1))
        cycle = high + low
        if cycle <= 0:
            return 0
        return int((float(t) % cycle) < high)

    @staticmethod
    def _random_walk(
        profile: dict,
        current: float | None,
        state: dict[str, Any] | None = None,
    ) -> float:
        min_val = float(profile.get("min", 0.0))
        max_val = float(profile.get("max", 100.0))
        if current is None:
            current = float(profile.get("value", (min_val + max_val) / 2.0))

        step = abs(float(profile.get("step", 1.0)))
        rng = ProfileGenerator._rng(profile, state)
        value = float(current) + rng.uniform(-step, step)
        return ProfileGenerator._with_noise(value, profile, state, rng=rng)

    @staticmethod
    def _hold(profile: dict, current: float | None) -> float:
        if current is not None:
            return float(current)
        return float(profile.get("value", 0.0))

    @staticmethod
    def _with_noise(
        value: float,
        profile: dict,
        state: dict[str, Any] | None,
        rng: random.Random | Any | None = None,
    ) -> float:
        noise = abs(float(profile.get("noise", 0.0)))
        if noise == 0:
            return float(value)
        rng = rng or ProfileGenerator._rng(profile, state)
        return float(value) + rng.uniform(-noise, noise)

    @staticmethod
    def _rng(profile: dict, state: dict[str, Any] | None):
        seed = profile.get("seed")
        if state is None:
            return random.Random(seed) if seed is not None else random

        rng = state.get("rng")
        if rng is None:
            rng = random.Random(seed)
            state["rng"] = rng
        return rng

    @staticmethod
    def _clamp(value: float, profile: dict) -> float:
        if "min" in profile:
            value = max(float(profile["min"]), value)
        if "max" in profile:
            value = min(float(profile["max"]), value)
        return value

    @staticmethod
    def decode_float(words: list[int]) -> float:
        return decode_float32_little_swap(words)
