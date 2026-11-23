import contextlib
import copy
import io
import sys
import threading
import unittest
from pathlib import Path
from typing import Callable

import yaml

from base.modbus_context_builder import ModbusContextBuilder
from simulator.profile_updater import ProfileUpdater, ramp_wrap_and_carry

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "res"
HR = 3  # holding registers


def load_model(rel_path: str) -> dict:
    return yaml.safe_load((RES / rel_path).read_text(encoding="utf-8"))


def quiet_updater(
    config: dict, context_wrapper: Callable | None = None, **kwargs
) -> ProfileUpdater:
    context, _ = ModbusContextBuilder(config).build()
    if context_wrapper is not None:
        context = context_wrapper(context)
    with contextlib.redirect_stdout(io.StringIO()):
        updater = ProfileUpdater(config, context, **kwargs)
    updater.writer.logger = lambda _msg: None
    updater._log = lambda _msg: None
    for handler in updater.device_handlers:
        if hasattr(handler, "logger"):
            handler.logger = lambda _msg: None
    return updater


def read_u48_be(context, offset: int) -> int:
    hi, md, lo = context.getValues(HR, offset, count=3)
    return (hi << 32) | (md << 16) | lo


def read_u32_le(context, offset: int) -> int:
    lo, hi = context.getValues(HR, offset, count=2)
    return (hi << 16) | lo


class SnapshotContext:
    """Slave context wrapper that decodes a counter after every setValues.

    Each snapshot is a state a concurrent Modbus read could have observed.
    """

    def __init__(self, inner, decode: Callable):
        self.inner = inner
        self.decode = decode
        self.snapshots = [decode(inner)]
        self.writes = []

    def getValues(self, fx, address, count=1):
        return self.inner.getValues(fx, address, count)

    def setValues(self, fx, address, values):
        self.inner.setValues(fx, address, values)
        self.writes.append((fx, address, list(values)))
        self.snapshots.append(self.decode(self.inner))


def read_concurrently(read: Callable[[], int], drive: Callable[[], list[int]]):
    """Run ``drive`` while another thread calls ``read`` in a tight loop.

    Returns ``(observed, published)``: every value the reader saw, and the
    true value after each tick as reported by ``drive``.
    """
    observed: list[int] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            observed.append(read())

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    thread = threading.Thread(target=reader)
    try:
        thread.start()
        published = drive()
    finally:
        stop.set()
        thread.join()
        sys.setswitchinterval(old_interval)
    return observed, published


def set_pin_value(config: dict, name: str, value: int):
    for pin in config["pins"]:
        if pin["name"] == name:
            pin["value"] = value
            return
    raise KeyError(name)


class RampWrapAndCarryTest(unittest.TestCase):
    def test_no_overflow_carries_zero(self):
        self.assertEqual(ramp_wrap_and_carry(100, 3, 0, 65535), (None, 0))

    def test_reaching_max_exactly_is_not_an_overflow(self):
        self.assertEqual(ramp_wrap_and_carry(65532, 3, 0, 65535), (None, 0))

    def test_single_overflow_carries_one(self):
        # max 65535, next 65537 -> low word 1, carry exactly 1 (was 2).
        self.assertEqual(ramp_wrap_and_carry(65534, 3, 0, 65535), (1, 1))
        self.assertEqual(ramp_wrap_and_carry(65535, 1, 0, 65535), (0, 1))

    def test_k_full_wraps_carry_k(self):
        self.assertEqual(ramp_wrap_and_carry(100, 2 * 65536, 0, 65535), (100, 2))
        self.assertEqual(ramp_wrap_and_carry(65535, 3 * 65536, 0, 65535), (65535, 3))

    def test_nonzero_min_and_short_range(self):
        # DAE PM210 KWH_LO: max 65000 -> 65001 values per wrap.
        self.assertEqual(ramp_wrap_and_carry(64998, 5, 0, 65000), (2, 1))
        self.assertEqual(ramp_wrap_and_carry(19, 3, 10, 20), (11, 1))


class CarryThroughUpdaterTest(unittest.TestCase):
    def test_cpm10_low_word_overflow_carries_one_into_middle_word(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        set_pin_value(config, "KWH_W2_MD", 7)
        set_pin_value(config, "KWH_W3_LO", 65534)
        updater = quiet_updater(config)
        before = read_u48_be(updater.context, 0)
        updater.run_tick(0.0)
        self.assertEqual(updater.context.getValues(HR, 0, count=3), [0, 8, 1])
        self.assertEqual(read_u48_be(updater.context, 0) - before, 3)

    def test_carry_target_word_wraps_at_65536(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        set_pin_value(config, "KWH_W2_MD", 65535)
        set_pin_value(config, "KWH_W3_LO", 65535)
        updater = quiet_updater(config)
        updater.run_tick(0.0)
        hi, md, lo = updater.context.getValues(HR, 0, count=3)
        self.assertEqual((md, lo), (0, 2))
        # The ramp path carries one level only; W2 -> W1 is not chained.
        self.assertEqual(hi, 0)

    def test_dae_pm210_single_overflow_carries_one(self):
        config = load_model("power_meter/dae_pm210.yml")
        set_pin_value(config, "KWH_LO", 64998)
        updater = quiet_updater(config)
        updater.run_tick(0.0)
        lo, hi = updater.context.getValues(HR, 48, count=2)
        self.assertEqual((lo, hi), (2, 1))


class Cpm10DefaultSevenHoursTest(unittest.TestCase):
    def test_48bit_counters_never_decrease_or_jump(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        self.assertNotIn("load_schedule", config)
        updater = quiet_updater(copy.deepcopy(config))
        ctx = updater.context
        steps = {}
        for pin in config["pins"]:
            if pin["name"] in ("KWH_W3_LO", "KVARH_W3_LO"):
                steps[pin["name"]] = pin["profile"]["step"]

        kwh_prev = read_u48_be(ctx, 0)
        kvarh_prev = read_u48_be(ctx, 3)
        kwh_start = kwh_prev
        carries = 0
        for tick in range(7 * 3600):
            updater.run_tick(float(tick))
            kwh = read_u48_be(ctx, 0)
            kvarh = read_u48_be(ctx, 3)
            self.assertEqual(kwh - kwh_prev, steps["KWH_W3_LO"], f"tick {tick}")
            self.assertEqual(kvarh - kvarh_prev, steps["KVARH_W3_LO"], f"tick {tick}")
            if (kwh >> 16) != (kwh_prev >> 16):
                carries += 1
            kwh_prev, kvarh_prev = kwh, kvarh

        self.assertEqual(kwh_prev - kwh_start, 3 * 7 * 3600)
        # Default start 1·65536 + 1000 plus 75600 raw crosses one low-word wrap.
        self.assertEqual(carries, 1)


def fast_carry_cpm10(step: int = 40000) -> dict:
    """CPM-10 default config whose kWh low word overflows on most ticks."""
    config = load_model("power_meter/adtek_cpm_10.yml")
    for pin in config["pins"]:
        if pin["name"] == "KWH_W3_LO":
            pin["profile"]["step"] = step
    return config


class AtomicRampCarryTest(unittest.TestCase):
    def assert_never_torn(self, observed, published, start):
        """Every read is a value some tick published, in non-decreasing order."""
        self.assertTrue(observed)
        allowed = set(published) | {start}
        prev = start
        for i, value in enumerate(observed):
            self.assertIn(value, allowed, f"read {i}: torn value {value}")
            self.assertGreaterEqual(value, prev, f"read {i}: {value} < {prev}")
            prev = value
        self.assertLessEqual(max(observed), published[-1])

    def test_concurrent_reader_never_sees_a_torn_carry(self):
        updater = quiet_updater(fast_carry_cpm10())
        ctx = updater.context
        start = read_u48_be(ctx, 0)

        def drive():
            published = []
            for tick in range(3000):
                updater.run_tick(float(tick))
                published.append(read_u48_be(ctx, 0))
            return published

        observed, published = read_concurrently(lambda: read_u48_be(ctx, 0), drive)
        self.assertGreater((published[-1] >> 16) - (start >> 16), 1000)  # carries
        self.assert_never_torn(observed, published, start)

    def test_every_intermediate_datastore_state_is_monotonic(self):
        updater = quiet_updater(
            fast_carry_cpm10(),
            context_wrapper=lambda inner: SnapshotContext(
                inner, lambda c: read_u48_be(c, 0)
            ),
        )
        for tick in range(300):
            updater.run_tick(float(tick))
        snaps = updater.context.snapshots
        self.assertEqual(snaps, sorted(snaps))
        # KWH_W3_LO (offset 2) -> KWH_W2_MD (offset 1): one write at 1, in order.
        carry_writes = [w for w in updater.context.writes if w[1] == 1]
        self.assertTrue(carry_writes)
        self.assertTrue(all(len(values) == 2 for _, _, values in carry_writes))

    def test_kvarh_carry_is_one_write(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        set_pin_value(config, "KVARH_W2_MD", 4)
        set_pin_value(config, "KVARH_W3_LO", 65535)
        updater = quiet_updater(
            config,
            context_wrapper=lambda inner: SnapshotContext(
                inner, lambda c: read_u48_be(c, 3)
            ),
        )
        updater.run_tick(0.0)
        self.assertIn((HR, 4, [5, 0]), updater.context.writes)
        self.assertEqual(updater.context.snapshots[-1], 5 * 65536)
        snaps = updater.context.snapshots
        self.assertEqual(snaps, sorted(snaps))

    def test_dae_pm210_low_word_below_carry_word_is_one_write(self):
        config = load_model("power_meter/dae_pm210.yml")
        set_pin_value(config, "KWH_LO", 64998)
        updater = quiet_updater(
            config,
            context_wrapper=lambda inner: SnapshotContext(
                inner, lambda c: read_u32_le(c, 48)
            ),
        )
        updater.run_tick(0.0)
        # Address order: KWH_LO @48, then KWH_HI @49.
        self.assertIn((HR, 48, [2, 1]), updater.context.writes)
        self.assertEqual(set(updater.context.snapshots), {64998, 65536 + 2})

    def test_non_adjacent_target_warns_once_and_still_carries(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        config["device_id"] = "CPM_TEST"
        for pin in config["pins"]:
            if pin["name"] == "KWH_W3_LO":
                pin["profile"]["carry_to"] = "KWH_W1_HI"  # offset 0, not 1
        set_pin_value(config, "KWH_W3_LO", 65534)
        context, _ = ModbusContextBuilder(config).build()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            updater = ProfileUpdater(config, context)
        warnings = [line for line in out.getvalue().splitlines() if "[WARN]" in line]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("KWH_W3_LO", warnings[0])
        self.assertIn("not atomic", warnings[0])
        self.assertEqual(updater.atomic_carry_pins, {("KVARH_W3_LO", 5)})

        updater.writer.logger = lambda _msg: None
        updater._log = lambda _msg: None
        updater.run_tick(0.0)
        self.assertEqual(context.getValues(HR, 0, count=3), [1, 1, 1])

    def test_shipped_carry_pins_are_all_atomic(self):
        for rel, expected in (
            ("power_meter/adtek_cpm_10.yml", {("KWH_W3_LO", 2), ("KVARH_W3_LO", 5)}),
            ("power_meter/dae_pm210.yml", {("KWH_LO", 48)}),
        ):
            config = load_model(rel)
            context, _ = ModbusContextBuilder(config).build()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                updater = ProfileUpdater(config, context)
            self.assertNotIn("not atomic", out.getvalue())
            self.assertEqual(updater.atomic_carry_pins, expected, rel)


if __name__ == "__main__":
    unittest.main()
