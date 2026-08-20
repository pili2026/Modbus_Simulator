# Sequence Control simulation environment

This repository has two simulation layers:

1. **Register/profile simulation**: ramp, wave, random walk, constant, toggle,
   pulse, and hold profiles update Modbus data blocks.
2. **Plant behavior simulation**: `SimulationWorld` observes Modbus command points
   and updates delayed feedback/status points, including points on other slave IDs.

The second layer exists so Talos Sequence Control can wait for physical feedback
instead of passing merely because a command register changed.

## First Enterprise

Use `res/sites/first_enterprise.yml` for the current intended plant mapping:

- Liling: CWP1 (`DOut05` -> `DIn09`) -> CHWP3 (`DOut03` -> `DIn03`) ->
  Tower1 + Tower2 -> IMA_C slave 3 `DOut01` -> Leading slave 51 compressor status.
- Tianji: CWP2 (`DOut06` -> `DIn10`) -> CHWP1 (`DOut01` -> `DIn01`) ->
  Tower1 + Tower2 -> IMA_C slave 4 `DOut01` -> Hanbell slave 7 compressor status.

The earlier site configuration variant that used CHWP2 for Liling is intentionally
not hard-coded in Python. Change only the site YAML point mapping if that wiring is
needed again.

### Chiller stop semantics

Liling enters status `9` (unloading) before status `0` (stopped). Tianji enters
status `4` before status `0`. This deliberately makes a Sequence STOP wait for
fresh physical confirmation instead of seeing an immediate stopped state.

## Run with socat

Terminal 1:

```bash
./bin/start_socat.sh
```

Terminal 2:

```bash
python src/main.py \
  --config res/sites/first_enterprise.yml \
  --scenario res/scenarios/first_enterprise_happy_path.yml
```

Talos should use the opposite PTY (`/tmp/ttyV0`) while the simulator uses
`/tmp/ttyV1`.

Fault examples:

```bash
python src/main.py -c res/sites/first_enterprise.yml \
  --scenario res/scenarios/first_enterprise_cwp1_stuck.yml

python src/main.py -c res/sites/first_enterprise.yml \
  --scenario res/scenarios/first_enterprise_liling_slow_stop.yml
```

`simulation.event_history_file` records JSONL entries containing monotonic time,
slave, point, old/new value and source. Use this file to assert actual command and
feedback ordering in an E2E test.

## Profile validation

`di_16_pin_module.yml` now runs with `interval_sec: 0.5`, which makes a toggle
profile with `interval: 0.5` observable instead of sampling it only once per
second. `ai_16_pin_module_fc03.yml` contains random-walk, bounce-ramp, and
min/max wave examples.

After starting the default simulator, use your normal `modpoll` commands to read
FC2/FC3/FC4 points across several seconds and verify:

- random stays within min/max and moves by bounded steps;
- wave honors `period_sec`, min/max, phase and optional noise;
- bounce reverses at both endpoints instead of wrapping;
- hold preserves external master writes;
- `DIn16` changes every 0.5 s when sampled at 0.5 s or faster.

The no-socat regression suite is:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

It verifies profile compatibility, float LITTLE_SWAP round-trips, every YAML pin
address get/set path, mixed register types, all 16 JY_DAM0816D discrete inputs,
SUTO `FLOW_VALUE` initial float reading, delayed plant feedback, chiller unloading,
and fault injection.

## Formatting

The repository pins `black==25.1.0`. Format Python changes with:

```bash
black src tests
```
