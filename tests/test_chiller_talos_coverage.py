import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHILLER_CONFIG = ROOT / "res" / "chiller_config"


HANBELL_READABLE = {
    "CHILLED_WATER_TEMP_IN": ("holding", 55),
    "CHILLED_WATER_TEMP_OUT": ("holding", 56),
    "COOLING_WATER_TEMP_IN": ("holding", 57),
    "COOLING_WATER_TEMP_OUT": ("holding", 58),
    "COMP1_STATUS": ("holding", 101),
    "COMP2_STATUS": ("holding", 102),
    "LOCAL_MODE": ("coil", 2710),
    "REMOTE_MODE": ("coil", 2810),
    "V_RS": ("holding", 6307),
    "V_ST": ("holding", 6309),
    "V_TR": ("holding", 6311),
    "Phase_A_Current": ("holding", 6513),
    "Phase_B_Current": ("holding", 6515),
    "Phase_C_Current": ("holding", 6517),
    "Kw": ("holding", 6541),
    "AveragePowerFactor": ("holding", 6554),
    "Kwh": ("holding", 6556),
}

LEADING_READABLE = {
    "CHILLED_WATER_TEMP_IN": ("holding", 6320),
    "CHILLED_WATER_TEMP_OUT": ("holding", 6321),
    "COOLING_WATER_TEMP_IN": ("holding", 6322),
    "COOLING_WATER_TEMP_OUT": ("holding", 6323),
    "INVERTER_OUTPUT_HZ": ("holding", 6275),
    "COMP_A_STATUS": ("holding", 6010),
    "COMP_B_STATUS": ("holding", 6020),
    "COMP_A_RUN_DAYS": ("holding", 9514),
    "COMP_A_RUN_HOURS": ("holding", 9513),
    "COMP_A_RUN_MINUTES": ("holding", 9512),
    "COMP_B_RUN_DAYS": ("holding", 9524),
    "COMP_B_RUN_HOURS": ("holding", 9523),
    "COMP_B_RUN_MINUTES": ("holding", 9522),
    "COMP_A_FAULT": ("coil", 2025),
    "COMP_B_FAULT": ("coil", 2026),
    "LOCAL_CONTROL_SIGNAL": ("coil", 3001),
    "REMOTE_CONTROL_SIGNAL": ("coil", 3002),
}


class ChillerTalosCoverageTest(unittest.TestCase):
    def _load_pin_map(self, filename: str):
        config = yaml.safe_load((CHILLER_CONFIG / filename).read_text(encoding="utf-8"))
        default_register_type = config.get("register_type", "holding")
        return config, {
            pin["name"]: (
                pin.get("register_type", default_register_type),
                int(pin["offset"]),
                pin,
            )
            for pin in config["pins"]
        }

    def _assert_readable_coverage(self, filename: str, expected: dict):
        _, pins = self._load_pin_map(filename)
        for name, (register_type, offset) in expected.items():
            self.assertIn(name, pins, f"{filename} missing Talos readable pin {name}")
            actual_type, actual_offset, pin = pins[name]
            self.assertEqual(actual_type, register_type, name)
            self.assertEqual(actual_offset, offset, name)
            self.assertEqual(pin.get("profile", {}).get("type"), "hold", name)

    def test_hanbell_covers_talos_readable_points(self):
        self._assert_readable_coverage("hanbell_310_410.yml", HANBELL_READABLE)
        _, pins = self._load_pin_map("hanbell_310_410.yml")
        self.assertEqual(pins["Kwh"][2].get("type"), "uint32")

    def test_leading_covers_talos_readable_points(self):
        self._assert_readable_coverage("leading_lwf126_240sv.yml", LEADING_READABLE)

    def test_water_temperatures_are_real_values_not_offline_sentinel(self):
        for filename in ("hanbell_310_410.yml", "leading_lwf126_240sv.yml"):
            _, pins = self._load_pin_map(filename)
            for name in (
                "CHILLED_WATER_TEMP_IN",
                "CHILLED_WATER_TEMP_OUT",
                "COOLING_WATER_TEMP_IN",
                "COOLING_WATER_TEMP_OUT",
            ):
                self.assertNotEqual(pins[name][2].get("value"), -1, f"{filename}:{name}")


if __name__ == "__main__":
    unittest.main()
