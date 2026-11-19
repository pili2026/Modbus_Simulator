# Power meter energy counters: ramp carry and `load_schedule`

## Ramp `carry_to`

A `ramp` profile with `carry_to: <pin>` treats its register as the low word of a
multi-word counter. When the next value would exceed `max`, the low word wraps
and the overflow is carried into the named pin:

```yaml
- name: KWH_W3_LO
  offset: 2
  value: 1000
  profile:
    type: ramp
    step: 3
    min: 0
    max: 65535
    carry_to: KWH_W2_MD
```

The low word holds `wrap_range = max - min + 1` values. For a next value `n`:

- `n <= max`: no wrap, carry 0;
- otherwise the low word becomes `min + (n - min) % wrap_range`, and the target
  word gets `(n - min) // wrap_range` added (modulo 65536).

So one overflow carries exactly 1 and `k` full wraps carry `k`. Before this fix
the carry was `(n - min) // wrap_range + 1`, so a single overflow added 2 to the
high word.

The carry is one level deep: the target word wraps at 65536 without carrying
further. For the CPM-10 default ramp (+3 raw per second into `KWH_W2_MD`) that
takes about 4.5 years.

For a counter that must never decrease, use `max: 65535` (a full 16-bit word).
With a smaller `max`, the decoded value still never decreases, but jumps forward
by `65536 - wrap_range` extra raw units at each wrap. `dae_pm210.yml` `KWH_LO`
(`max: 65000`) jumps +540 raw (+5.40 kWh) at a wrap instead of its normal
+5 raw (+0.05 kWh), about every 3.6 h at 1 tick/s.

### ADTEK CPM-10 defaults

`res/power_meter/adtek_cpm_10.yml` stores kWh and kvarh as 48-bit big-endian
counters, `W1·2³² + W2·2¹⁶ + W3`, as Talos reads them (`u48_be`). Both low words
(`KWH_W3_LO`, `KVARH_W3_LO`) ramp to `max: 65535` and carry into the middle word,
so the decoded counters never decrease. Without a schedule the kWh counter still
rises +3 raw (0.3 kWh) per tick, about 1080 kW at 1 tick/s.

## `load_schedule` (ADTEK_CPM10 only)

A site device entry can replace the CPM-10 energy ramp with an hourly kW
schedule. The block goes in the site file entry (`res/sites/*.yml`); the loader
copies it into that one device instance's config, like `interval_sec`:

```yaml
devices:
  - id: ADTEK_CPM10
    serial_port: /tmp/ttyV1
    slave_id: 1
    model_file: power_meter/adtek_cpm_10.yml
    load_schedule:
      steps:            # minute of the hour -> kW from that minute on
        - {minute: 0,  kw: 150}
        - {minute: 6,  kw: 230}
        - {minute: 15, kw: 150}
        - {minute: 30, kw: 150}
        - {minute: 36, kw: 230}
        - {minute: 45, kw: 150}
```

The schedule is piecewise constant and repeats every hour. Each step's kW holds
from its minute until the next step's minute; the last step holds until the end
of the hour. Minute of the hour comes from epoch seconds, so it is the same in
UTC and Asia/Taipei and the container's time zone does not matter.

### What it drives

Each update tick the handler:

1. measures Δt since the previous tick on a monotonic clock (not
   `interval_sec`);
2. integrates the schedule exactly over the wall-clock interval
   `[now - Δt, now]`, splitting at step boundaries, and adds the kWh to a float
   accumulator (fractional raw units are kept between ticks);
3. writes `floor(accumulator)` as 48 bits into `KWH_W1_HI`/`KWH_W2_MD`/`KWH_W3_LO`;
4. writes `KW` (offset 9) as the current step's kW in raw units.

The accumulator starts from the configured `value`s of the three energy words.
The raw unit comes from `ENERGY_INDEX` the way the Talos CPM-10 driver scales it:
`energy_table[index] × 0.001`, with
`energy_table = [0.1, 1, 10, 100, 1000, 10000, 100000, 1000000]`. The model's
default index 3 gives 0.1 kWh (and 0.1 kW) per raw unit.

While the schedule is active, the handler claims `KWH_W1_HI`, `KWH_W2_MD`,
`KWH_W3_LO` and `KW` through the pin-handler path, so their `ramp`/`wave`
profiles do not run. Other pins (voltages, currents, kvarh, …) keep their
profiles. Without a `load_schedule` block the handler does nothing and the device
behaves exactly as before.

### Validation

The block is checked at start-up. Any of these logs an error naming the device
and the problem, and the simulator exits with status 2:

- `steps` missing, empty, or not a list; unknown keys;
- a minute that is not an integer, or outside 0–59;
- minutes not strictly increasing, or a first step other than minute 0;
- a kW that is not a number or is negative;
- a model other than `ADTEK_CPM10`, a missing energy/`KW`/`ENERGY_INDEX` pin, an
  `ENERGY_INDEX` outside 0–7, or a kW too large for the i16 `KW` register.

### First Enterprise

`res/sites/first_enterprise.yml` uses the schedule above. With a 200 kW contract
and a 190 kW line, the Taipower 15-minute windows give:

| Window | Energy | Demand (4 × ΔkWh) |
| --- | --- | --- |
| :00–:15 | (150 × 6 + 230 × 9) / 60 = 49.5 kWh | 198 kW, crossing the line at minute 6 |
| :15–:30 | 150 × 15 / 60 = 37.5 kWh | 150 kW |
| :30–:45 | 49.5 kWh | 198 kW |
| :45–:00 | 37.5 kWh | 150 kW |

`tests/test_load_schedule.py` checks these numbers with fake monotonic and wall
clocks, without sleeping.
