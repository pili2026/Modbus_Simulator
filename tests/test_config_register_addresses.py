import unittest
from pathlib import Path

import yaml

from base.modbus_context_builder import ModbusContextBuilder
from simulator.profile_generator import ProfileGenerator
from utils.constants import fx_code_for_register_type
from utils.pin_processor.pin_value_reader import PinValueReader


ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "res"


class ConfigRegisterAddressTest(unittest.TestCase):
    def test_every_model_yaml_pin_address_is_readable_and_writable(self):
        for path in sorted(RES.rglob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            pins = data.get("pins")
            if not isinstance(pins, list):
                continue

            with self.subTest(path=str(path.relative_to(ROOT))):
                context, _ = ModbusContextBuilder(data).build()
                base = int(data.get("base_address", 0))
                default_type = data.get("register_type", "holding")

                for pin in pins:
                    if "offset" not in pin:
                        continue
                    address = base + int(pin["offset"])
                    fx = fx_code_for_register_type(
                        pin.get("register_type"), default_type
                    )
                    count = (
                        2
                        if str(pin.get("type", "")).lower()
                        in {"float", "uint32", "int32"}
                        else 1
                    )
                    values = context.getValues(fx, address, count=count)
                    self.assertEqual(len(values), count, f"{path}: {pin.get('name')}")
                    context.setValues(fx, address, values)
                    self.assertEqual(
                        context.getValues(fx, address, count=count), values
                    )

    def test_jy_dam0816d_all_16_discrete_inputs_fit_context(self):
        path = RES / "dio_config" / "jy_dam0816d.yml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        context, _ = ModbusContextBuilder(config).build()
        pins = [pin for pin in config["pins"] if pin["name"].startswith("DIn")]
        self.assertEqual(len(pins), 16)

        for index, pin in enumerate(pins):
            context.setValues(2, int(pin["offset"]), [index % 2])
            self.assertEqual(
                context.getValues(2, int(pin["offset"]), count=1)[0], index % 2
            )

    def test_initial_values_honor_per_pin_register_type(self):
        config = {
            "type": "io_module",
            "model": "MIXED",
            "register_type": "holding",
            "pins": [
                {
                    "name": "DI",
                    "offset": 0,
                    "register_type": "discrete",
                    "value": 1,
                },
                {
                    "name": "DO",
                    "offset": 0,
                    "register_type": "coil",
                    "value": 1,
                },
            ],
        }
        context, _ = ModbusContextBuilder(config).build()
        self.assertEqual(context.getValues(2, 0, count=1)[0], 1)
        self.assertEqual(context.getValues(1, 0, count=1)[0], 1)

    def test_suto_flow_value_first_update_reads_initial_float(self):
        path = RES / "flowmeter_config" / "suto_flow.yml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        context, _ = ModbusContextBuilder(config).build()
        pin = next(pin for pin in config["pins"] if pin["name"] == "FLOW_VALUE")
        reader = PinValueReader(context)
        current = reader.get_current_value(pin, 3, int(pin["offset"]))
        self.assertAlmostEqual(current, 0.0, places=5)
        first = ProfileGenerator.generate(pin["profile"], 0.0, current, {})
        self.assertAlmostEqual(first, 0.1, places=5)


if __name__ == "__main__":
    unittest.main()
