import argparse

import yaml


def load_config():
    parser = argparse.ArgumentParser(description="Load YAML configuration")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to YAML config file (e.g., config_master.yml)"
    )
    args = parser.parse_args()

    with open(args.config, "r") as f:
        return yaml.safe_load(f)

config = load_config()

def get_serial_port():
    return config["serial_port"]

def get_slave_id():
    return config["slave_id"]
