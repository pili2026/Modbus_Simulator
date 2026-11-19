"""Opt-in hourly kW load schedule for energy meters.

A device config with a ``load_schedule`` block gets a piecewise-constant kW
profile keyed by minute of the hour, repeating every hour::

    load_schedule:
      steps:
        - {minute: 0, kw: 150}
        - {minute: 6, kw: 230}

Each tick the handler integrates the schedule over the time elapsed since the
previous tick (measured on a monotonic clock) into a float kWh accumulator, and
writes the 48-bit big-endian energy counter (``KWH_W1_HI``/``KWH_W2_MD``/
``KWH_W3_LO``) plus instant power ``KW``. Without the block it does nothing.
"""

import math
import time
from typing import Callable

from base.base_device_handler import BaseDeviceHandler
from base.base_pin_handler import BasePinHandler
from utils.constants import fx_code_for_register_type
from utils.log_formatter import simulate_log

# Talos res/driver/adtek_cpm_10.yml: tables.energy_table and
# energy_post_multiplier. Factor = energy_table[ENERGY_INDEX] * 0.001, which
# scales both the 48-bit kWh counter (modes.kwh: auto) and the Kw register.
CPM10_ENERGY_TABLE = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0)
CPM10_ENERGY_POST_MULTIPLIER = 0.001

SECONDS_PER_HOUR = 3600.0
U48_MOD = 1 << 48
I16_MAX = 32767
# Guards floor() against float sums that land a hair below a whole raw unit.
RAW_EPSILON = 1e-6


class LoadScheduleConfigError(ValueError):
    """Invalid ``load_schedule`` block; raised at start-up."""


def parse_load_schedule(block, where: str = "load_schedule") -> list[tuple[int, float]]:
    """Validate a ``load_schedule`` block and return ``[(minute, kw), ...]``."""
    if not isinstance(block, dict):
        raise LoadScheduleConfigError(f"{where}: must be a mapping with a 'steps' list")
    unknown = set(block) - {"steps"}
    if unknown:
        raise LoadScheduleConfigError(f"{where}: unknown keys {sorted(unknown)}")

    steps = block.get("steps")
    if not isinstance(steps, list) or not steps:
        raise LoadScheduleConfigError(f"{where}.steps: must be a non-empty list")

    parsed: list[tuple[int, float]] = []
    for i, step in enumerate(steps):
        loc = f"{where}.steps[{i}]"
        if not isinstance(step, dict):
            raise LoadScheduleConfigError(f"{loc}: must be a mapping {{minute, kw}}")
        unknown = set(step) - {"minute", "kw"}
        if unknown:
            raise LoadScheduleConfigError(f"{loc}: unknown keys {sorted(unknown)}")
        if "minute" not in step or "kw" not in step:
            raise LoadScheduleConfigError(f"{loc}: needs both 'minute' and 'kw'")

        minute = step["minute"]
        if isinstance(minute, bool) or not isinstance(minute, int):
            raise LoadScheduleConfigError(
                f"{loc}: minute must be an integer, got {minute!r}"
            )
        if not 0 <= minute <= 59:
            raise LoadScheduleConfigError(
                f"{loc}: minute must be in 0..59, got {minute}"
            )

        kw = step["kw"]
        if isinstance(kw, bool) or not isinstance(kw, (int, float)):
            raise LoadScheduleConfigError(f"{loc}: kw must be a number, got {kw!r}")
        if not math.isfinite(kw) or kw < 0:
            raise LoadScheduleConfigError(f"{loc}: kw must be >= 0, got {kw}")

        if i == 0 and minute != 0:
            raise LoadScheduleConfigError(
                f"{loc}: the first step must be minute 0, got {minute}"
            )
        if parsed and minute <= parsed[-1][0]:
            raise LoadScheduleConfigError(
                f"{loc}: minutes must be strictly increasing, "
                f"got {minute} after {parsed[-1][0]}"
            )
        parsed.append((minute, float(kw)))
    return parsed


class HourlySchedule:
    """Piecewise-constant kW over the hour, with exact interval integration."""

    def __init__(self, steps: list[tuple[int, float]]):
        self.steps = steps
        self.starts = [m * 60.0 for m, _ in steps]
        self.kws = [kw for _, kw in steps]
        self.ends = self.starts[1:] + [SECONDS_PER_HOUR]
        # Cumulative kWh at the start of each segment, and for a whole hour.
        self.cum_kwh = []
        total = 0.0
        for start, end, kw in zip(self.starts, self.ends, self.kws):
            self.cum_kwh.append(total)
            total += kw * (end - start) / SECONDS_PER_HOUR
        self.hour_kwh = total

    def kw_at(self, wall_ts: float) -> float:
        return self.kws[self._segment(wall_ts % SECONDS_PER_HOUR)]

    def energy_kwh(self, wall_start: float, wall_end: float) -> float:
        """kWh delivered by the schedule over the wall-clock interval."""
        if wall_end <= wall_start:
            return 0.0
        h0 = math.floor(wall_start / SECONDS_PER_HOUR)
        h1 = math.floor(wall_end / SECONDS_PER_HOUR)
        return (
            (h1 - h0) * self.hour_kwh
            + self._cumulative(wall_end - h1 * SECONDS_PER_HOUR)
            - self._cumulative(wall_start - h0 * SECONDS_PER_HOUR)
        )

    def _segment(self, sec_of_hour: float) -> int:
        idx = 0
        for i, start in enumerate(self.starts):
            if sec_of_hour >= start:
                idx = i
        return idx

    def _cumulative(self, sec_of_hour: float) -> float:
        idx = self._segment(sec_of_hour)
        return (
            self.cum_kwh[idx]
            + self.kws[idx] * (sec_of_hour - self.starts[idx]) / SECONDS_PER_HOUR
        )


class _ClaimedPins(BasePinHandler):
    """Pin-handler view: claims the energy/KW pins so profiles skip them."""

    def __init__(self, names: frozenset[str]):
        self.names = names

    def should_handle(self, model: str, pin: dict) -> bool:
        return pin.get("name") in self.names

    def handle(self, context, fx_code: int, addr: int, pin: dict, log_fn, log_ctx: str):
        # The device handler writes these registers once per tick.
        return


class LoadScheduleHandler(BaseDeviceHandler):
    SUPPORTED_MODELS = {"ADTEK_CPM10"}
    ENERGY_PINS = ("KWH_W1_HI", "KWH_W2_MD", "KWH_W3_LO")
    KW_PIN = "KW"
    ENERGY_INDEX_PIN = "ENERGY_INDEX"

    def __init__(
        self,
        context,
        config: dict,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        logger: Callable[[str], None] = simulate_log,
    ):
        self.context = context
        self.config = config
        self.monotonic_clock = monotonic_clock
        self.wall_clock = wall_clock
        self.logger = logger
        self.active = "load_schedule" in config
        self.pin_claim = _ClaimedPins(frozenset())
        if not self.active:
            return

        device_id = config.get("device_id", "Unknown")
        where = f"[{device_id}] load_schedule"
        model = config.get("model")
        if model not in self.SUPPORTED_MODELS:
            raise LoadScheduleConfigError(
                f"{where}: model {model!r} is not supported "
                f"(supported: {sorted(self.SUPPORTED_MODELS)})"
            )

        self.schedule = HourlySchedule(
            parse_load_schedule(config["load_schedule"], where)
        )

        pins = {pin.get("name"): pin for pin in config.get("pins", []) or []}
        missing = [
            name
            for name in (*self.ENERGY_PINS, self.KW_PIN, self.ENERGY_INDEX_PIN)
            if name not in pins
        ]
        if missing:
            raise LoadScheduleConfigError(f"{where}: model is missing pins {missing}")

        energy_index = pins[self.ENERGY_INDEX_PIN].get("value")
        if (
            isinstance(energy_index, bool)
            or not isinstance(energy_index, int)
            or not 0 <= energy_index < len(CPM10_ENERGY_TABLE)
        ):
            raise LoadScheduleConfigError(
                f"{where}: unsupported ENERGY_INDEX {energy_index!r} "
                f"(supported: 0..{len(CPM10_ENERGY_TABLE) - 1})"
            )
        self.kwh_per_raw = (
            CPM10_ENERGY_TABLE[energy_index] * CPM10_ENERGY_POST_MULTIPLIER
        )

        max_kw = max(self.schedule.kws)
        if round(max_kw / self.kwh_per_raw) > I16_MAX:
            raise LoadScheduleConfigError(
                f"{where}: kw {max_kw} exceeds the i16 KW register at "
                f"ENERGY_INDEX {energy_index} (max {I16_MAX * self.kwh_per_raw:g} kW)"
            )

        base_address = int(config.get("base_address", 0))
        default_register_type = config.get("register_type", "holding")
        self.energy_pins = [pins[name] for name in self.ENERGY_PINS]
        self.kw_pin = pins[self.KW_PIN]
        self._addr = {
            name: (
                fx_code_for_register_type(
                    pins[name].get("register_type"), default_register_type
                ),
                base_address + int(pins[name].get("offset", 0)),
            )
            for name in (*self.ENERGY_PINS, self.KW_PIN)
        }

        hi, md, lo = (
            int(pin.get("value", 0) or 0) & 0xFFFF for pin in self.energy_pins
        )
        self.acc_raw = float((hi << 32) | (md << 16) | lo)
        self.last_mono: float | None = None
        self.pin_claim = _ClaimedPins(frozenset((*self.ENERGY_PINS, self.KW_PIN)))
        self._log(
            f"active: steps(minute, kW)={self.schedule.steps} "
            f"kwh_per_raw={self.kwh_per_raw:g} hour_kwh={self.schedule.hour_kwh:g}"
        )

    def handle(self, fx_code: int):
        if not self.active:
            return

        now_mono = self.monotonic_clock()
        now_wall = self.wall_clock()
        if self.last_mono is not None:
            dt = max(0.0, now_mono - self.last_mono)
            kwh = self.schedule.energy_kwh(now_wall - dt, now_wall)
            self.acc_raw += kwh / self.kwh_per_raw
        self.last_mono = now_mono

        raw = math.floor(self.acc_raw + RAW_EPSILON) % U48_MOD
        words = [(raw >> 32) & 0xFFFF, (raw >> 16) & 0xFFFF, raw & 0xFFFF]
        for name, word in zip(self.ENERGY_PINS, words):
            fx, addr = self._addr[name]
            self.context.setValues(fx, addr, [word])

        kw = self.schedule.kw_at(now_wall)
        kw_raw = int(round(kw / self.kwh_per_raw))
        fx, addr = self._addr[self.KW_PIN]
        self.context.setValues(fx, addr, [kw_raw & 0xFFFF])

        self._log(
            f"load_schedule kw={kw:g} (raw={kw_raw}) "
            f"kwh={raw * self.kwh_per_raw:.1f} words={words}"
        )

    def _log(self, msg: str):
        device_id = self.config.get("device_id", "Unknown")
        model = self.config.get("model", "Unknown")
        self.logger(f"[{device_id}][{model}] {msg}")
