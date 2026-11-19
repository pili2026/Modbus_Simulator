import contextlib
import io
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import main
from handler.load_schedule_handler import (
    LoadScheduleConfigError,
    LoadScheduleHandler,
    parse_load_schedule,
)
from test_ramp_carry import HR, load_model, quiet_updater, read_u48_be

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "res"

# An hour boundary in epoch seconds (minute 0 in UTC and in Asia/Taipei).
HOUR0 = 3600.0 * 491_000
KWH_PER_RAW = 0.1  # CPM-10 ENERGY_INDEX 3


def site_schedule() -> dict:
    site = yaml.safe_load((RES / "sites" / "first_enterprise.yml").read_text("utf-8"))
    (cpm,) = [d for d in site["devices"] if d["id"] == "ADTEK_CPM10"]
    return cpm["load_schedule"]


class FakeClocks:
    def __init__(self, wall: float, mono: float = 1000.0):
        self.wall_now = wall
        self.mono_now = mono

    def monotonic(self) -> float:
        return self.mono_now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, dt: float):
        self.wall_now += dt
        self.mono_now += dt


def scheduled_cpm10(clocks: FakeClocks, schedule: dict | None = None):
    config = load_model("power_meter/adtek_cpm_10.yml")
    config["device_id"] = "ADTEK_CPM10_1"
    config["load_schedule"] = schedule if schedule is not None else site_schedule()
    return quiet_updater(
        config, monotonic_clock=clocks.monotonic, wall_clock=clocks.wall
    )


def run_until(updater, clocks: FakeClocks, wall_target: float, dts):
    """Tick with the given dt sequence, landing exactly on ``wall_target``."""
    while clocks.wall_now < wall_target - 1e-9:
        dt = min(next(dts), wall_target - clocks.wall_now)
        clocks.advance(dt)
        updater.run_tick(clocks.mono_now)


def constant(dt: float):
    while True:
        yield dt


class LoadScheduleIntegrationTest(unittest.TestCase):
    def _check_windows(self, dts):
        clocks = FakeClocks(wall=HOUR0 - 30.0)
        updater = scheduled_cpm10(clocks)
        updater.run_tick(clocks.mono_now)
        ctx = updater.context

        readings = {}
        for minute in (0, 15, 30, 45, 60):
            run_until(updater, clocks, HOUR0 + minute * 60, dts)
            readings[minute] = read_u48_be(ctx, 0) * KWH_PER_RAW

        energy_0_15 = readings[15] - readings[0]
        self.assertAlmostEqual(energy_0_15, (150 * 6 + 230 * 9) / 60, places=6)
        self.assertAlmostEqual(energy_0_15, 49.50, places=6)
        window_kw = {m: 4 * (readings[m + 15] - readings[m]) for m in (0, 15, 30, 45)}
        self.assertAlmostEqual(window_kw[0], 198.0, delta=0.1)
        self.assertAlmostEqual(window_kw[15], 150.0, delta=0.1)
        self.assertAlmostEqual(window_kw[30], 198.0, delta=0.1)
        self.assertAlmostEqual(window_kw[45], 150.0, delta=0.1)

    def test_one_second_ticks(self):
        self._check_windows(constant(1.0))

    def test_irregular_ticks_use_monotonic_dt_not_interval(self):
        rng = random.Random(7)
        self._check_windows(iter(lambda: rng.uniform(0.2, 2.5), None))

    def test_kw_register_follows_schedule(self):
        clocks = FakeClocks(wall=HOUR0)
        updater = scheduled_cpm10(clocks)
        updater.run_tick(0.0)
        kw_raw = lambda: updater.context.getValues(HR, 9, count=1)[0]
        self.assertEqual(kw_raw() * KWH_PER_RAW, 150.0)
        run_until(updater, clocks, HOUR0 + 7 * 60, constant(1.0))
        self.assertAlmostEqual(kw_raw() * KWH_PER_RAW, 230.0, places=6)
        run_until(updater, clocks, HOUR0 + 20 * 60, constant(1.0))
        self.assertAlmostEqual(kw_raw() * KWH_PER_RAW, 150.0, places=6)

    def test_counter_starts_from_configured_words_and_carries(self):
        clocks = FakeClocks(wall=HOUR0)
        config = load_model("power_meter/adtek_cpm_10.yml")
        config["load_schedule"] = {"steps": [{"minute": 0, "kw": 3000}]}
        for pin in config["pins"]:
            if pin["name"] == "KWH_W3_LO":
                pin["value"] = 65000
        updater = quiet_updater(
            config, monotonic_clock=clocks.monotonic, wall_clock=clocks.wall
        )
        updater.run_tick(0.0)
        self.assertEqual(updater.context.getValues(HR, 0, count=3), [0, 1, 65000])

        prev = read_u48_be(updater.context, 0)
        for _ in range(3600):
            clocks.advance(1.0)
            updater.run_tick(clocks.mono_now)
            now = read_u48_be(updater.context, 0)
            self.assertGreaterEqual(now, prev)
            prev = now
        # 3000 kW for one hour = 3000 kWh = 30000 raw, crossing the low word.
        self.assertEqual(prev - (65536 + 65000), 30000)
        self.assertEqual(updater.context.getValues(HR, 1, count=1)[0], 2)

    def test_profiles_do_not_touch_claimed_registers(self):
        clocks = FakeClocks(wall=HOUR0)
        updater = scheduled_cpm10(clocks)
        updater.run_tick(0.0)
        words = updater.context.getValues(HR, 0, count=3)
        kw = updater.context.getValues(HR, 9, count=1)
        # Ticks with no time passing: ramp (+3) and wave (≈350 raw) would show.
        for tick in range(1, 20):
            updater.run_tick(float(tick))
        self.assertEqual(updater.context.getValues(HR, 0, count=3), words)
        self.assertEqual(updater.context.getValues(HR, 9, count=1), kw)
        # Unclaimed profiles still run (KVARH ramp).
        self.assertEqual(updater.context.getValues(HR, 5, count=1)[0], 500 + 20)


class NoScheduleUnchangedTest(unittest.TestCase):
    def test_handler_inactive_and_claims_nothing(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        updater = quiet_updater(config)
        (handler,) = [
            h for h in updater.device_handlers if isinstance(h, LoadScheduleHandler)
        ]
        self.assertFalse(handler.active)
        for pin in config["pins"]:
            self.assertFalse(handler.pin_claim.should_handle(config["model"], pin))

    def test_ramp_and_wave_still_drive_energy_and_kw(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        updater = quiet_updater(config)
        start = read_u48_be(updater.context, 0)
        kws = set()
        for tick in range(40):
            updater.run_tick(float(tick))
            kws.add(updater.context.getValues(HR, 9, count=1)[0])
        self.assertEqual(read_u48_be(updater.context, 0) - start, 3 * 40)
        self.assertTrue(all(320 <= kw <= 380 for kw in kws))
        self.assertGreater(len(kws), 10)


class LoadScheduleValidationTest(unittest.TestCase):
    def steps(self, *pairs):
        return {"steps": [{"minute": m, "kw": kw} for m, kw in pairs]}

    def assertRejects(self, block, fragment):
        with self.assertRaises(LoadScheduleConfigError) as cm:
            parse_load_schedule(block)
        self.assertIn(fragment, str(cm.exception))

    def test_site_schedule_is_valid(self):
        self.assertEqual(
            parse_load_schedule(site_schedule()),
            [
                (0, 150.0),
                (6, 230.0),
                (15, 150.0),
                (30, 150.0),
                (36, 230.0),
                (45, 150.0),
            ],
        )

    def test_unsorted_minutes(self):
        self.assertRejects(self.steps((0, 1), (15, 1), (6, 1)), "strictly increasing")

    def test_duplicate_minutes(self):
        self.assertRejects(self.steps((0, 1), (6, 1), (6, 2)), "strictly increasing")

    def test_minute_60(self):
        self.assertRejects(self.steps((0, 1), (60, 1)), "0..59")

    def test_negative_kw(self):
        self.assertRejects(self.steps((0, 1), (6, -5)), "kw must be >= 0")

    def test_first_minute_not_zero(self):
        self.assertRejects(
            self.steps((1, 150), (6, 230)), "first step must be minute 0"
        )

    def test_non_integer_minute(self):
        self.assertRejects(self.steps((0, 1), (6.5, 1)), "minute must be an integer")

    def test_missing_or_empty_steps(self):
        self.assertRejects({}, "non-empty list")
        self.assertRejects({"steps": []}, "non-empty list")
        self.assertRejects(None, "must be a mapping")

    def test_unknown_key(self):
        self.assertRejects(
            {"steps": [{"minute": 0, "kw": 1, "kwh": 2}]}, "unknown keys"
        )

    def test_unsupported_model(self):
        config = load_model("power_meter/dae_pm210.yml")
        config["load_schedule"] = self.steps((0, 1))
        with self.assertRaisesRegex(LoadScheduleConfigError, "not supported"):
            LoadScheduleHandler(None, config)

    def test_unsupported_energy_index(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        config["load_schedule"] = self.steps((0, 1))
        for pin in config["pins"]:
            if pin["name"] == "ENERGY_INDEX":
                pin["value"] = 9
        with self.assertRaisesRegex(
            LoadScheduleConfigError, "unsupported ENERGY_INDEX 9"
        ):
            LoadScheduleHandler(None, config)

    def test_kw_beyond_i16_register(self):
        config = load_model("power_meter/adtek_cpm_10.yml")
        config["load_schedule"] = self.steps((0, 3300))
        with self.assertRaisesRegex(LoadScheduleConfigError, "exceeds the i16 KW"):
            LoadScheduleHandler(None, config)


class SiteLoaderTest(unittest.TestCase):
    def write_site(self, tmp: str, devices: list[dict]) -> str:
        path = Path(tmp) / "site.yml"
        path.write_text(yaml.safe_dump({"devices": devices}), encoding="utf-8")
        return str(path)

    def cpm_entry(self, slave_id: int, **extra) -> dict:
        return {
            "id": "ADTEK_CPM10",
            "serial_port": "/tmp/ttyV1",
            "slave_id": slave_id,
            "model_file": "power_meter/adtek_cpm_10.yml",
            **extra,
        }

    def test_block_is_merged_per_instance(self):
        captured = []

        class FakeSimulator:
            def __init__(self, config):
                captured.append(config)

            def build_context(self):
                return object()

        with tempfile.TemporaryDirectory() as tmp:
            site = self.write_site(
                tmp,
                [
                    self.cpm_entry(
                        1, load_schedule={"steps": [{"minute": 0, "kw": 5}]}
                    ),
                    self.cpm_entry(2),
                ],
            )
            with mock.patch.object(main, "GenericModbusSimulator", FakeSimulator):
                main.load_simulation(site, str(RES))

        self.assertEqual(
            captured[0]["load_schedule"], {"steps": [{"minute": 0, "kw": 5}]}
        )
        self.assertNotIn("load_schedule", captured[1])

    def test_invalid_block_fails_startup_with_message(self):
        bad = {"steps": [{"minute": 0, "kw": 150}, {"minute": 60, "kw": 230}]}
        with tempfile.TemporaryDirectory() as tmp:
            site = self.write_site(tmp, [self.cpm_entry(1, load_schedule=bad)])
            argv = ["main.py", "-c", site, "-m", str(RES)]
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(
                stderr
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    main.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("ADTEK_CPM10_1", stderr.getvalue())
        self.assertIn("minute must be in 0..59, got 60", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
