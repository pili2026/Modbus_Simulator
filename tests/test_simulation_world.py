import unittest

from simulator.simulation_world import PointRef, SimulationWorld


class FakeContext:
    def __init__(self):
        self.values = {}

    def getValues(self, fx_code, address, count=1):
        return [
            self.values.get((fx_code, address + offset), 0) for offset in range(count)
        ]

    def setValues(self, fx_code, address, values):
        for offset, value in enumerate(values):
            self.values[(fx_code, address + offset)] = int(value)


class SimulationWorldTest(unittest.TestCase):
    def make_registry(self):
        return {
            ("/tmp/ttyV1", 2): {"context": FakeContext()},
            ("/tmp/ttyV1", 3): {"context": FakeContext()},
            ("/tmp/ttyV1", 51): {"context": FakeContext()},
        }

    def test_actuator_feedback_is_delayed_and_cross_context_safe(self):
        registry = self.make_registry()
        config = {
            "behaviors": [
                {
                    "id": "pump",
                    "type": "actuator_feedback",
                    "command": {
                        "slave_id": 2,
                        "register_type": "coil",
                        "offset": 4,
                        "name": "DOut05",
                    },
                    "feedback": {
                        "slave_id": 2,
                        "register_type": "discrete",
                        "offset": 8,
                        "name": "DIn09",
                    },
                    "on_delay_sec": 1.0,
                    "off_delay_sec": 1.0,
                }
            ]
        }
        world = SimulationWorld(registry, config)
        world._prime_command_state()
        context = registry[("/tmp/ttyV1", 2)]["context"]
        feedback = PointRef.from_dict(config["behaviors"][0]["feedback"])

        context.setValues(1, 4, [1])
        world._detect_commands(10.0)
        world._run_due(10.5)
        self.assertEqual(world.read_point(feedback), 0)
        world._run_due(11.0)
        self.assertEqual(world.read_point(feedback), 1)

        context.setValues(1, 4, [0])
        world._detect_commands(12.0)
        world._run_due(12.5)
        self.assertEqual(world.read_point(feedback), 1)
        world._run_due(13.0)
        self.assertEqual(world.read_point(feedback), 0)

        sources = [event["source"] for event in world.events]
        self.assertEqual(
            sources,
            [
                "master_command",
                "plant_behavior",
                "master_command",
                "plant_behavior",
            ],
        )

    def test_blocked_actuator_retries_when_interlock_becomes_satisfied(self):
        registry = self.make_registry()
        config = {
            "behaviors": [
                {
                    "id": "chwp3",
                    "type": "actuator_feedback",
                    "command": {
                        "slave_id": 2,
                        "register_type": "coil",
                        "offset": 2,
                        "name": "DOut03_CHWP3",
                    },
                    "feedback": {
                        "slave_id": 2,
                        "register_type": "discrete",
                        "offset": 2,
                        "name": "DIn03_CHWP3",
                    },
                    "on_delay_sec": 0.5,
                    "off_delay_sec": 0.5,
                    "requires": [
                        {
                            "slave_id": 2,
                            "register_type": "discrete",
                            "offset": 8,
                            "name": "DIn09_CWP1",
                            "equals": 1,
                        }
                    ],
                }
            ]
        }
        world = SimulationWorld(registry, config)
        world._prime_command_state()
        context = registry[("/tmp/ttyV1", 2)]["context"]
        feedback = PointRef.from_dict(config["behaviors"][0]["feedback"])

        context.setValues(1, 2, [1])
        world._detect_commands(20.0)
        world._run_due(21.0)
        self.assertEqual(world.read_point(feedback), 0)
        self.assertIn("chwp3", world._blocked_on)

        context.setValues(2, 8, [1])
        world._detect_commands(22.0)
        world._run_due(22.4)
        self.assertEqual(world.read_point(feedback), 0)
        world._run_due(22.5)
        self.assertEqual(world.read_point(feedback), 1)
        self.assertNotIn("chwp3", world._blocked_on)

        # The prerequisite is a start permissive, not an automatic trip condition.
        context.setValues(2, 8, [0])
        world._detect_commands(23.0)
        world._run_due(24.0)
        self.assertEqual(world.read_point(feedback), 1)

        context.setValues(1, 2, [0])
        world._detect_commands(25.0)
        world._run_due(25.5)
        self.assertEqual(world.read_point(feedback), 0)

        sources = [event["source"] for event in world.events]
        self.assertEqual(
            sources,
            [
                "master_command",
                "interlock_blocked",
                "interlock_released",
                "plant_behavior",
                "master_command",
                "plant_behavior",
            ],
        )

    def test_blocked_chiller_starts_when_interlocks_recover(self):
        registry = self.make_registry()
        config = {
            "behaviors": [
                {
                    "id": "liling",
                    "type": "chiller_status",
                    "command": {
                        "slave_id": 3,
                        "register_type": "holding",
                        "offset": 3,
                        "bit": 0,
                        "name": "DOut01_LILING",
                    },
                    "status_points": [
                        {
                            "slave_id": 51,
                            "register_type": "holding",
                            "offset": 6010,
                            "name": "COMP_A_STATUS",
                        },
                        {
                            "slave_id": 51,
                            "register_type": "holding",
                            "offset": 6020,
                            "name": "COMP_B_STATUS",
                        },
                    ],
                    "starting_status": 1,
                    "running_status": 2,
                    "start_delay_sec": 3.0,
                    "requires": [
                        {
                            "slave_id": 2,
                            "register_type": "discrete",
                            "offset": 2,
                            "name": "DIn03_CHWP3",
                            "equals": 1,
                        }
                    ],
                }
            ]
        }
        world = SimulationWorld(registry, config)
        world._prime_command_state()
        command_context = registry[("/tmp/ttyV1", 3)]["context"]
        prerequisite_context = registry[("/tmp/ttyV1", 2)]["context"]
        status_context = registry[("/tmp/ttyV1", 51)]["context"]

        command_context.setValues(3, 3, [1])
        world._detect_commands(30.0)
        world._run_due(31.0)
        self.assertEqual(status_context.getValues(3, 6010, 1)[0], 0)
        self.assertEqual(status_context.getValues(3, 6020, 1)[0], 0)

        prerequisite_context.setValues(2, 2, [1])
        world._detect_commands(32.0)
        world._run_due(32.0)
        self.assertEqual(status_context.getValues(3, 6010, 1)[0], 1)
        self.assertEqual(status_context.getValues(3, 6020, 1)[0], 1)

        world._run_due(35.0)
        self.assertEqual(status_context.getValues(3, 6010, 1)[0], 2)
        self.assertEqual(status_context.getValues(3, 6020, 1)[0], 2)

        sources = [event["source"] for event in world.events]
        self.assertIn("interlock_blocked", sources)
        self.assertIn("interlock_released", sources)
        self.assertIn("plant_behavior", sources)

    def test_chiller_off_enters_unloading_before_stopped(self):
        registry = self.make_registry()
        config = {
            "behaviors": [
                {
                    "id": "liling",
                    "type": "chiller_status",
                    "command": {
                        "slave_id": 3,
                        "register_type": "holding",
                        "offset": 3,
                        "bit": 0,
                        "name": "DOut01",
                    },
                    "status_points": [
                        {
                            "slave_id": 51,
                            "register_type": "holding",
                            "offset": 6010,
                            "name": "COMP_A_STATUS",
                        },
                        {
                            "slave_id": 51,
                            "register_type": "holding",
                            "offset": 6020,
                            "name": "COMP_B_STATUS",
                        },
                    ],
                    "unloading_status": 9,
                    "stopped_status": 0,
                    "stop_delay_sec": 2.0,
                }
            ]
        }
        command_context = registry[("/tmp/ttyV1", 3)]["context"]
        status_context = registry[("/tmp/ttyV1", 51)]["context"]
        command_context.setValues(3, 3, [1])
        status_context.setValues(3, 6010, [2])
        status_context.setValues(3, 6020, [2])

        world = SimulationWorld(registry, config)
        world._prime_command_state()
        command_context.setValues(3, 3, [0])
        world._detect_commands(40.0)
        world._run_due(40.0)
        self.assertEqual(status_context.getValues(3, 6010, 1)[0], 9)
        self.assertEqual(status_context.getValues(3, 6020, 1)[0], 9)
        world._run_due(42.0)
        self.assertEqual(status_context.getValues(3, 6010, 1)[0], 0)
        self.assertEqual(status_context.getValues(3, 6020, 1)[0], 0)

    def test_stuck_off_fault_suppresses_on_feedback(self):
        registry = self.make_registry()
        config = {
            "behaviors": [
                {
                    "id": "pump",
                    "type": "actuator_feedback",
                    "command": {
                        "slave_id": 2,
                        "register_type": "coil",
                        "offset": 4,
                    },
                    "feedback": {
                        "slave_id": 2,
                        "register_type": "discrete",
                        "offset": 8,
                    },
                    "on_delay_sec": 0,
                }
            ]
        }
        scenario = {"faults": [{"behavior": "pump", "mode": "stuck_off"}]}
        world = SimulationWorld(registry, config, scenario)
        world._prime_command_state()
        registry[("/tmp/ttyV1", 2)]["context"].setValues(1, 4, [1])
        world._detect_commands(50.0)
        world._run_due(51.0)
        feedback = PointRef.from_dict(config["behaviors"][0]["feedback"])
        self.assertEqual(world.read_point(feedback), 0)


if __name__ == "__main__":
    unittest.main()
