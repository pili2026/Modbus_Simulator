import os
import threading
from collections import defaultdict

import yaml
from pymodbus.datastore import ModbusServerContext
from pymodbus.server import StartSerialServer

from simulator.generic_simulator import GenericModbusSimulator


def load_yaml(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_devices(device_config_path, model_base_dir):
    device_data = load_yaml(device_config_path)
    devices = device_data.get("devices", [])

    # { serial_port: { slave_id: context } }
    port_slave_map = defaultdict(dict)

    for device in devices:
        model_path = os.path.join(model_base_dir, device["model_file"])
        model_config = load_yaml(model_path)

        slave_id = device["slave_id"]
        device_id = device["id"]
        model_config["device_id"] = f"{device_id}_{slave_id}"

        simulator = GenericModbusSimulator(model_config)
        slave_context = simulator.build_context()

        port = device["serial_port"]

        port_slave_map[port][slave_id] = slave_context

    return port_slave_map


def start_server_for_port(port, slave_context_map):
    context = ModbusServerContext(slaves=slave_context_map, single=False)
    print(f"[INFO] Starting RTU server on port: {port} for slaves: {list(slave_context_map.keys())}")
    StartSerialServer(context=context, port=port, baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=1)


def main():
    device_config_path = "./res/modbus_device.yml"
    model_base_dir = "./res"

    port_slave_map = load_all_devices(device_config_path, model_base_dir)

    threads = []
    for port, slave_map in port_slave_map.items():
        t = threading.Thread(target=start_server_for_port, args=(port, slave_map), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
