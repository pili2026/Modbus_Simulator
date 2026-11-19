import copy
import unittest
from pathlib import Path

import yaml

from base.modbus_context_builder import ModbusContextBuilder
from simulator.profile_updater import ProfileUpdater, ramp_wrap_and_carry

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "res"
HR = 3  # holding registers


def load_model(rel_path: str) -> dict:
    return yaml.safe_load((RES / rel_path).read_text(encoding="utf-8"))


def quiet_updater(config: dict, **kwargs) -> ProfileUpdater:
    context, _ = ModbusContextBuilder(config).build()
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


if __name__ == "__main__":
    unittest.main()
