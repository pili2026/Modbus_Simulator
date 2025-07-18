import math

from pymodbus.datastore import ModbusSlaveContext


def generate_value_from_profile(
    profile: dict, t: int, fx_code: int = None, addr: int = None, context: ModbusSlaveContext = None
) -> float:
    ptype = profile.get("type")

    if ptype == "ramp":
        step = profile.get("step", 1)
        min_val = profile.get("min", 0)
        max_val = profile.get("max", 100)

        curr_val = min_val
        if context and fx_code is not None and addr is not None:
            try:
                curr_val = context.getValues(fx_code, addr, count=1)[0]
            except Exception:
                pass

        next_val = curr_val + step
        return next_val if next_val <= max_val else min_val

    if ptype == "wave":
        period = profile.get("period", 20)
        amplitude = profile.get("amplitude", 50)
        base = profile.get("base", 500)
        radians = 2 * math.pi * (t % period) / period
        return base + amplitude * math.sin(radians)

    if ptype == "constant":
        return int(profile.get("value", 0))

    if ptype == "toggle":
        interval = profile.get("interval", 1)
        return int((t // interval) % 2)

    if ptype == "pulse":
        high = profile.get("high_time", 1)
        low = profile.get("low_time", 1)
        return int((t % (high + low)) < high)

    return 0.0
