import struct
import unittest
from pathlib import Path

import yaml

from base.modbus_context_builder import ModbusContextBuilder


ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "res"


class FirstEnterpriseTopologyTest(unittest.TestCase):
    def test_site_matches_production_slave_topology(self):
        config = yaml.safe_load(
            (RES / "sites" / "first_enterprise.yml").read_text(encoding="utf-8")
        )
        slaves = sorted(int(device["slave_id"]) for device in config["devices"])
        self.assertEqual(slaves, [1, 2, 3, 4, 5, 7, 8, 51])

    def test_site_maps_all_eight_remote_outputs_to_run_feedbacks(self):
        config = yaml.safe_load(
            (RES / "sites" / "first_enterprise.yml").read_text(encoding="utf-8")
        )
        behaviors = {
            behavior["id"]: behavior
            for behavior in config["simulation"]["behaviors"]
            if behavior.get("type") == "actuator_feedback"
        }

        expected = {
            "chwp1_feedback": (0, 0),
            "chwp2_feedback": (1, 1),
            "chwp3_feedback": (2, 2),
            "tower1_feedback": (3, 3),
            "cwp1_feedback": (4, 8),
            "cwp2_feedback": (5, 9),
            "cwp3_feedback": (6, 10),
            "tower2_feedback": (7, 11),
        }

        for behavior_id, (do_offset, di_offset) in expected.items():
            with self.subTest(behavior=behavior_id):
                behavior = behaviors[behavior_id]
                command = behavior["command"]
                feedback = behavior["feedback"]
                self.assertEqual(int(command["slave_id"]), 2)
                self.assertEqual(command["register_type"], "coil")
                self.assertEqual(int(command["offset"]), do_offset)
                self.assertEqual(int(feedback["slave_id"]), 2)
                self.assertEqual(feedback["register_type"], "discrete")
                self.assertEqual(int(feedback["offset"]), di_offset)

    def test_fum01_registers_decode_like_talos_driver(self):
        config = yaml.safe_load(
            (RES / "sensor_config" / "fum01.yml").read_text(encoding="utf-8")
        )
        context, _ = ModbusContextBuilder(config).build()

        def read_f32_be(offset: int) -> float:
            words = context.getValues(3, offset, count=2)
            return struct.unpack(">f", struct.pack(">HH", *words))[0]

        self.assertAlmostEqual(read_f32_be(1), 170.51, places=2)
        self.assertAlmostEqual(read_f32_be(33), 11.30, places=2)
        self.assertAlmostEqual(read_f32_be(35), 9.78, places=2)
        self.assertAlmostEqual(read_f32_be(37), 28.05, places=2)
        self.assertAlmostEqual(read_f32_be(39), 30.29, places=2)
        self.assertAlmostEqual(read_f32_be(41), -1.50, places=2)
        self.assertAlmostEqual(read_f32_be(97), 102.3, places=1)

        words = context.getValues(3, 8, count=2)
        flow_consumption = struct.unpack("<I", struct.pack("<HH", *words))[0]
        self.assertEqual(flow_consumption, 108476)
        self.assertEqual(context.getValues(3, 91, count=1)[0] & 0xFF, 86)


if __name__ == "__main__":
    unittest.main()
