import argparse
import os
import threading
from collections import defaultdict
from typing import Any

import yaml
from pymodbus.datastore import ModbusServerContext
from pymodbus.server import StartSerialServer

from simulator.generic_simulator import GenericModbusSimulator
from simulator.simulation_world import SimulationWorld


def load_yaml(file_path):
    with open(file_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_simulation(
    device_config_path: str,
    model_base_dir: str,
    scenario_path: str | None = None,
) -> tuple[dict, SimulationWorld | None]:
    device_data = load_yaml(device_config_path)
    devices = device_data.get("devices", [])
    port_slave_map = defaultdict(dict)
    device_registry: dict[tuple[str, int], dict[str, Any]] = {}

    for device in devices:
        model_path = os.path.join(model_base_dir, device["model_file"])
        model_config = load_yaml(model_path)

        slave_id = int(device["slave_id"])
        device_id = device["id"]
        model_config["device_id"] = f"{device_id}_{slave_id}"
        if "interval_sec" in device:
            model_config["interval_sec"] = device["interval_sec"]

        simulator = GenericModbusSimulator(model_config)
        slave_context = simulator.build_context()

        port = device["serial_port"]
        port_slave_map[port][slave_id] = slave_context
        device_registry[(port, slave_id)] = {
            "context": slave_context,
            "device": device,
            "model_config": model_config,
            "simulator": simulator,
        }

    simulation_config = device_data.get("simulation") or {}
    scenario = load_yaml(scenario_path) if scenario_path else None
    world = None
    if simulation_config.get("behaviors"):
        world = SimulationWorld(device_registry, simulation_config, scenario)
        world.start()

    return port_slave_map, world


def load_all_devices(device_config_path: str, model_base_dir: str) -> dict:
    port_slave_map, _ = load_simulation(device_config_path, model_base_dir)
    return port_slave_map


def start_server_for_port(port, slave_context_map):
    context = ModbusServerContext(slaves=slave_context_map, single=False)
    print(
        f"[INFO] Starting RTU server on port: {port} "
        f"for slaves: {list(slave_context_map.keys())}"
    )
    StartSerialServer(
        context=context,
        port=port,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=1,
    )


def main():
    parser = argparse.ArgumentParser(description="Run Modbus RTU Simulator")
    parser.add_argument(
        "--config",
        "-c",
        default="./res/modbus_device.yml",
        help="Path to device config YAML file (default: ./res/modbus_device.yml)",
    )
    parser.add_argument(
        "--model-dir",
        "-m",
        default="./res",
        help="Base directory for model files (default: ./res)",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Optional simulation scenario YAML (faults/delay overrides)",
    )

    args = parser.parse_args()
    port_slave_map, _world = load_simulation(args.config, args.model_dir, args.scenario)

    threads = []
    for port, slave_map in port_slave_map.items():
        thread = threading.Thread(
            target=start_server_for_port,
            args=(port, slave_map),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
