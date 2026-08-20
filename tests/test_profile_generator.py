import math
import unittest

from simulator.profile_generator import ProfileGenerator
from utils.float_codec import decode_float32_little_swap, encode_float32_little_swap


class ProfileGeneratorTest(unittest.TestCase):
    def test_legacy_ramp_sequence_is_preserved(self):
        profile = {"type": "ramp", "step": 3, "min": 0, "max": 10}
        current = 0
        values = []
        for tick in range(5):
            current = ProfileGenerator.generate(profile, tick, current)
            values.append(current)
        self.assertEqual(values, [3, 6, 9, 0, 3])

    def test_toggle_supports_fractional_seconds(self):
        profile = {"type": "toggle", "interval": 0.5}
        values = [
            ProfileGenerator.generate(profile, elapsed)
            for elapsed in (0, 0.5, 1.0, 1.5)
        ]
        self.assertEqual(values, [0, 1, 0, 1])

    def test_wave_supports_min_max_period_sec_and_phase(self):
        profile = {
            "type": "wave",
            "min": 10,
            "max": 30,
            "period_sec": 20,
            "phase_deg": 90,
        }
        self.assertTrue(math.isclose(ProfileGenerator.generate(profile, 0), 30.0))

    def test_ramp_phase_is_applied_once(self):
        profile = {
            "type": "ramp",
            "step": 1,
            "min": 0,
            "max": 100,
            "phase_deg": 180,
        }
        state = {}
        first = ProfileGenerator.generate(profile, 0, 0, state)
        second = ProfileGenerator.generate(profile, 1, first, state)
        self.assertEqual(first, 51)
        self.assertEqual(second, 52)

    def test_bounce_ramp_keeps_direction_in_runtime_state(self):
        profile = {"type": "ramp", "step": 4, "min": 0, "max": 10, "bounce": True}
        state = {}
        current = 8
        current = ProfileGenerator.generate(profile, 0, current, state)
        self.assertEqual(current, 8)
        self.assertEqual(state["direction"], -1)
        current = ProfileGenerator.generate(profile, 1, current, state)
        self.assertEqual(current, 4)

    def test_random_walk_seed_is_reproducible(self):
        profile = {"type": "random", "min": 0, "max": 10, "step": 2, "seed": 42}
        state_a = {}
        state_b = {}
        current_a = current_b = 5.0
        seq_a = []
        seq_b = []
        for tick in range(5):
            current_a = ProfileGenerator.generate(profile, tick, current_a, state_a)
            current_b = ProfileGenerator.generate(profile, tick, current_b, state_b)
            seq_a.append(current_a)
            seq_b.append(current_b)
        self.assertEqual(seq_a, seq_b)
        self.assertTrue(all(0 <= value <= 10 for value in seq_a))

    def test_hold_returns_current_value(self):
        self.assertEqual(ProfileGenerator.generate({"type": "hold"}, 0, 123), 123)

    def test_little_swap_float_codec_round_trip(self):
        words = encode_float32_little_swap(123.25)
        self.assertEqual(len(words), 2)
        self.assertAlmostEqual(decode_float32_little_swap(words), 123.25, places=5)


if __name__ == "__main__":
    unittest.main()
