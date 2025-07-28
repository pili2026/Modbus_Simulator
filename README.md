# RS485 Device Simulator

## Table of Contents

- [RS485 Device Simulator](#rs485-device-simulator)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Features](#features)
  - [Environment Setup](#environment-setup)
    - [Option 1 — Using `venv` (standard)](#option-1--using-venv-standard)
    - [Option 2 — Using `pyenv` + `pyenv-virtualenv`](#option-2--using-pyenv--pyenv-virtualenv)
  - [Notes on AI and DIO Module Configuration](#notes-on-ai-and-dio-module-configuration)
    - [AI Modules](#ai-modules)
    - [DIO Modules](#dio-modules)
    - [Flow Meter Modules](#flow-meter-modules)
      - [Design Principles](#design-principles)
      - [Benefits](#benefits)
  - [Execution](#execution)
    - [Virtual RS485 Simulation with `socat`](#virtual-rs485-simulation-with-socat)
    - [Shell Scripts](#shell-scripts)
      - [1. `start_socat.sh`](#1-start_socatsh)
      - [2. `run_socat_with_simulator.sh`](#2-run_socat_with_simulatorsh)
    - [Example Testing Workflow](#example-testing-workflow)

---

## Overview

This simulator is designed to emulate the behavior of various RS485-based industrial devices, including:

- **Variable Frequency Drives (VFDs)**
- **Analog Input (AI) Modules**
- **Digital Input/Output (DIO) Modules**

The primary goal is to allow developers to perform **local integration and functional testing** without requiring physical hardware. The simulator supports **Modbus RTU protocol**, focusing on **read operations via Function Code 3 (holding registers)** and **Function Code 4 (input registers)**.

It is built with a **modular, configuration-driven architecture**:

- `res/` — defines reusable driver configuration files per device model.
- `modbus_device.yml` — specifies which devices to simulate in a given run, including ports, slave IDs, and associated models.

---

## Features

- **Multi-device support** — Simulate multiple RS485 devices simultaneously.
- **Multi-threaded simulation** — Each device runs independently in a dedicated thread for real-time behavior.
- **Custom register mapping** — Register layouts and access rules are defined per device via YAML.
- **Profile-based simulation** — Simulate analog and digital values using ramp, waveform, toggle, and constant profiles.
- **AI module support (8 or 16 channels)** — Fully supports **Function Code 3** and **Function Code 4** for analog input simulation.
- **New device models require datasheet review** — Proper simulation depends on accurate specification and register mapping.

---

## Environment Setup

Before running the simulator, it is **strongly recommended** to create a dedicated Python virtual environment. This ensures isolation and avoids conflicts with system-wide packages.

You may use either `venv` (built-in) or `pyenv` (preferred for managing multiple Python versions).  
The simulator currently targets **Python 3.12**.

### Option 1 — Using `venv` (standard)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Option 2 — Using `pyenv` + `pyenv-virtualenv`

```bash
pyenv install 3.12.3
pyenv virtualenv 3.12.3 rs485-sim
pyenv activate rs485-sim
pip install -r requirements.txt
```

Once the environment is ready, you can proceed to the [Execution](#execution) section to start the simulator.

---

## Notes on AI and DIO Module Configuration

### AI Modules

AI (Analog Input) modules follow a **general-purpose configuration approach**:

* Only distinguish between **8-channel** and **16-channel** variants.
* No need to create device-specific YAML files for common models.
* Both variants support **FC3 (holding register)** and **FC4 (input register)** read operations.

This keeps configuration clean and efficient for most simulation needs.

### DIO Modules

DIO (Digital Input/Output) modules also follow a **generic simulation pattern** in most cases.
However, **some custom or proprietary devices**, such as `IMA_C`, may use a **dedicated configuration** due to unique register structures or control logic.

* General-purpose DIO models are supported via shared templates.
* Custom devices like `IMA_C` require their own YAML and specific logic.

This hybrid approach balances reusability with the flexibility to simulate more complex or specialized devices.


### Flow Meter Modules

Flow meter modules simulate real-time and cumulative flow metrics using an extended IO-based configuration. This approach supports a wide range of flow-related values, including instantaneous flow rate, forward/reverse consumption, and flow direction.

#### Design Principles

- **Generic Configuration**: Flow meters are configured as `io_module` devices, reusing the same profile-based simulation logic as AI modules.
- **Floating Point Support**: The `FLOW_VALUE` value is written as a 32-bit float (split into two 16-bit registers) to simulate realistic decimal precision.
- **Cumulative Flow Simulation**:
  - `FLOW_CONSUMPTION` and `FLOW_REVCONSUMPTION` use 32-bit unsigned integers (two registers) to simulate forward and reverse accumulated flow.
  - `FLOW_CONSUMPTION` is automatically incremented over time for demonstration purposes.
- **Flow Direction Logic**: The `FLOW_DIRECTION` field is determined by a simple even/odd check on the simulated `FLOW_VALUE` value (`flow % 2 < 1` → forward).
- **Profile Support**: The `FLOW_VALUE` value can be driven by various profiles (e.g., `ramp`, `wave`) to simulate dynamic changes over time.

#### Benefits

- **Extensibility**: Easily simulate a variety of flow sensor behaviors using profiles and custom scaling.
- **Integration Ready**: Compatible with alert/control logic and device interaction scenarios.
- **Test-Ready Outputs**: Provides realistic flow-related values suitable for automated system validation and monitoring tools.

This hybrid approach combines the flexibility of simulation with the clarity of real-world flow meter structure. 
Additional devices (e.g., water meters, gas meters) can follow a similar schema for expanded testing coverage.

---

## Execution

### Virtual RS485 Simulation with `socat`

Since native RS485 communication is not feasible in most local environments (e.g., Ubuntu PC), this simulator uses `socat` to create **virtual serial port pairs** to emulate RS485 communication.

A typical virtual serial link created by `socat` looks like this:

```text
    +-------------------+           +-------------------+
    |   /tmp/ttyV1      | <=======> |     /tmp/ttyV0    |
    | (Simulator port)  |           |  (Master testing) |
    +-------------------+           +-------------------+
```

* The simulator binds to `/tmp/ttyV1`
* Your testing tool (e.g., `modpoll`, `minimalmodbus`, etc.) connects to `/tmp/ttyV1`

---

### Shell Scripts

You can use the following helper scripts from the `bin/` directory:

#### 1. `start_socat.sh`

Starts only the virtual RS485 port pair:

```bash
./bin/start_socat.sh
```

Use this if you want to run the simulator separately (e.g., inside an IDE or debugger).

After socat is running, you can launch the simulator manually using the default config:

```bash
python3 src/main.py
```
Or specify a custom config file:
```bash
python3 src/main.py --config ./custom/device_config.yml --model-dir ./custom/models
```

#### 2. `run_socat_with_simulator.sh`

Starts both `socat` and the device simulator together:

```bash
./bin/run_socat_with_simulator.sh
```
Or with custom config and model directory:
```bash
./bin/run_socat_with_simulator.sh --config ./custom/devices.yml --model-dir ./custom/models
```

This script:

* Launches a virtual port pair (e.g., `/tmp/ttyV0` ↔ `/tmp/ttyV1`)
* Starts the simulator based on the `modbus_device.yml` configuration
  
> ℹ️ Note: If you want to use a custom device config or model directory, you will need to either:
> Modify the script to pass --config and --model-dir, or
> Run the simulator manually as shown above.

---

### Example Testing Workflow

1. Edit `modbus_device.yml` to configure the devices to simulate.
2. Launch simulator with socat:

```bash
./bin/run_socat_with_simulator.sh
```

3. *(Optional)* In another terminal, use your Modbus master to test against `/tmp/ttyV1`:

```bash
modpoll -m rtu -b 9600 -p none -d 8 -s 1 -a 1 -r 0 -c 4 /tmp/ttyV1
```

The simulator will respond with data according to the specified register mappings and profiles.
