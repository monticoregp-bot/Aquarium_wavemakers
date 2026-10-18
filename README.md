# Aquarium Wavemaker for Home Assistant

Time-scheduled wave modes for BLDC wavemaker pumps (for example Jebao pumps driven by
an ESP32). Give each pump a daily schedule of wave, pulse, surge, random and constant
blocks, with smooth cross-fades, followers, and opposite-phase pumps.

- Unlimited pumps; each has its own time blocks
- Modes: **constant**, **wave** (sine), **pulse**, **surge**, **random**, each with its own settings
- **Slave** pumps inherit another pump's schedule; **inverse** pumps run in opposite phase
- Inversion can apply to the whole day or only to chosen times (those times are locked)
- 60 s cross-fade between blocks (adjustable), with motor protection on the device
- The ESP32 draws the waveform itself, so a 10 s wave is smooth whatever Wi-Fi does
- Every protection setting is an entity; two Lovelace cards (editor and summary)

Requires Home Assistant **2024.11 or newer** and ESPHome **2024.8 or newer**.

---

## How it fits together

```
 Home Assistant                          ESP32 (ESPHome)
 ─────────────────                       ──────────────────────────────
 schedule + clock  ──  once a minute ──▶  wave_engine.h draws the waveform
 decides which wave     and at every      50 times a second, cross-fades,
 each pump runs now     change            limits speed, drives PWM + enable
```

Home Assistant never streams levels. A sine with a 10 s period would need an update every
100 to 200 ms, and over Wi-Fi that stutters. Instead it sends **one action call** per pump
with the mode and its settings; the ESP32 does the rest. If Home Assistant or Wi-Fi go away,
the pump keeps running its last wave. Settings identical to what the ESP32 already runs are
ignored, so resending every minute is free and lets a rebooted ESP32 recover within a minute.

---

## Install

### 1. Home Assistant side

**HACS (recommended):** HACS → ⋮ → **Custom repositories**, add this repository's URL with
category **Integration**, download **Aquarium Wavemaker**, restart Home Assistant.

**Manual:** copy `custom_components/aquarium_wavemaker/` into
`<config>/custom_components/aquarium_wavemaker/` and restart.

### 2. ESP32 side

1. Copy **both** `esphome/wavemaker.yaml` and `esphome/wave_engine.h` into your ESPHome
   configuration folder (HACS does not install these; they are examples in the repository).
2. Adjust the pins, PWM frequency and device name to your motor drivers (the example drives
   two pumps, each with a PWM input and an enable pin; add `inverted: true` for active-low
   drivers), add your `secrets.yaml`, and flash it.
3. The device now offers the Home Assistant action `esphome.<name>_set_wave`
   (for the default name, `esphome.wavemaker_set_wave`). Check under
   Developer tools → Actions.

### 3. Set up

1. **Create the master toggle** (one per aquarium): Settings → Devices & services → Helpers →
   Create helper → Toggle, for example `Aquarium Wavemaker`
   (`input_boolean.aquarium_wavemaker`). ON lets the schedule drive the pumps; OFF stops
   sending and the pumps keep their last wave.
2. **Add integration → Aquarium Wavemaker.** Step 1: aquarium name and the toggle. Step 2:
   the first pump's name, its **ESPHome action** (`esphome.wavemaker_set_wave`) and its
   **channel** on the ESP32 (0 for the first output). Run it again for another aquarium.
3. Add the cards (see **Lovelace cards**), add the rest of your pumps, build the schedules.
4. Turn the master toggle on.

**The cards load automatically.** On start the integration registers its script as a dashboard
resource (Settings → Dashboards → Resources shows
`/aquarium_wavemaker/aquarium-wavemaker-card.js?v=...`), updates it when you upgrade, and
removes it when you delete the integration. If your dashboards use **YAML resource mode**
this cannot be done from code; add it to your dashboard YAML instead:

```yaml
lovelace:
  resources:
    - url: /aquarium_wavemaker/aquarium-wavemaker-card.js
      type: module
```

If a card still says "Custom element doesn't exist", restart Home Assistant and hard-refresh
the browser (Ctrl+Shift+R).

---

## Schedules

Each pump has a list of **time blocks**: a start, an end, a mode, and that mode's settings.

- Blocks cannot overlap but may touch, and may cross midnight (21:10 to 06:00).
  Start equal to end means the whole day.
- Any gap runs the pump's **default level** (a constant, default 50 %).
- Your example is four blocks:

| Time | Mode | Settings |
|---|---|---|
| 06:00 to 16:00 | wave | 20 to 80 %, 10 s period |
| 16:00 to 21:00 | random | 10 to 90 % |
| 21:00 to 21:10 | constant | 100 % |
| 21:10 to 06:00 | surge | 30 to 60 %, 20 s period |

### Modes

All waves are computed from the time since the block started, so every pump on the same block
(and any slave or inverse pump) stays phase-locked with no per-pump phase to send.

| Mode | Settings | Behaviour |
|---|---|---|
| **constant** | level | One speed |
| **wave** | min, max, period | Smooth sine: starts at min, peaks at max mid-period, back to min |
| **pulse** | min, max, period, pulse time | At max for the pulse time, at min for the rest of the period. The pulse time must be below the period, so 15 s with 7 s on is 8 s low |
| **surge** | min, max, period | Builds linearly from min to max over the period; each period starts again at min |
| **random** | min, max, change every (s) | Picks a new target between min and max every N seconds (default 10) and glides between targets with a smooth curve. Deterministic per block, so slaves and inverse pumps follow the same walk |

### Transitions

When a block changes, the old and new waves **cross-fade** over the pump's cross-fade time
(default 60 s, set per pump with a smooth ease). Separately, a **ramp limit** (default 100 %
per second) stops the motor ever changing faster than you allow, which also softens pulse
edges and the surge drop. A pulse too short for the ramp limit to complete is softened rather
than skipped.

---

## Followers, slaves and inverse pumps

Any pump can follow another pump's schedule, so you set a rig up once.

- **Slave:** runs exactly what its source runs, in phase.
- **Inverse:** runs the source's schedule in **opposite phase**: whenever the source is at its
  maximum, the inverse pump is at its minimum (its level is mirrored within min and max, so the
  two always sum to the same total flow). This works for wave, pulse, surge and random.
- **Constant mode** has no phase, so an inverse pump has a toggle: **mirror** (same level as the
  source, the default) or **invert** (100 % minus the source's level).
- **All day or part of the day.** With "follow all day" the pump's own schedule is locked.
  Otherwise you list **follow windows** (for example 16:00 to 21:10): during them the pump
  follows its source; outside them it runs its own blocks. Its own blocks are **not allowed to
  cover the followed times**; they are locked, so what you see is what runs.
- Chains work (pump 3 follows pump 2 which follows pump 1; an inverse of an inverse is back in
  phase). Loops are refused, a followed pump cannot be deleted, and chains are limited to 8.
- A follower keeps its **own** motor settings (minimum, maximum, ramp limit, cross-fade): those
  belong to the motor, not the schedule.

Example: pump 2 inverse of pump 1, pump 4 inverse of pump 3, each pair on its own schedule.

---

## Motor protection (applied on the ESP32)

| Setting | Meaning |
|---|---|
| Minimum running speed | A computed speed below this runs at this speed (so the motor does not stall). A computed 0 still means off. Default 10 % |
| Maximum speed | Nothing exceeds this. Default 100 % |
| Ramp limit | Largest change per second. Default 100 % per second |
| Cross-fade time | Blend between blocks. Default 60 s |
| Default level | Speed when no block is scheduled. Default 50 % |

These are applied on the device, so they hold even if Home Assistant goes away. Right after a
reboot the pumps run `boot_level` (default 50 %, set in the YAML) until the first settings
arrive, which is at most a minute.

---

## Entities

Each pump becomes a device (for example **Reef Pump 1**) with:

| Kind | Entities |
|---|---|
| `number` (config) | Minimum running speed, Maximum speed, Ramp limit, Default level, Cross-fade time |
| `select` (config) | Follows (none or another pump), Follow relation (slave or inverse) |
| `switch` (config) | Follow all day, Invert constant levels |
| `sensor` | Mode, Setting (for example "wave 20-80%, 10 s", with all values as attributes), Next change |

Entity ids are built from the aquarium name and pump (`number.reef_pump_1_maximum_speed`); the
device page shows the exact ids. Settings entities are views of the pump configuration: changing
one validates and saves everything, and refuses a change that would make it invalid (with the
reason, for example a loop or a block inside locked times). **Time blocks and follow windows are
edited in the card.**

### Service

`aquarium_wavemaker.set_wave`: target any entity of a pump with a `mode` and its settings to send a
wave right now for **testing** (not saved). While the master toggle is on the schedule replaces it
within a minute; switch the toggle off to hold it.

---

## Lovelace cards

Both cards are in one file. With one aquarium no configuration is needed; with several, set
`master_entity` to the aquarium's toggle.

```yaml
type: custom:aquarium-wavemaker-card         # editor, for a config dashboard
title: Wavemakers                            # optional
master_entity: input_boolean.reef_waves      # optional with one aquarium
```

Pump tabs; what each pump is doing now; follow settings with windows; the block list with
mode-specific fields; motor settings; a **test wave** panel for bench testing; a 24 h timeline
(bars show each block's min to max, hatching marks followed times, the dashed line is the default
level); and a **waveform preview** of the selected block drawn by the same engine as the firmware.
Saving needs an admin user.

```yaml
type: custom:aquarium-wavemaker-summary-card  # compact, for a main display
title: Flow                                   # optional
master_entity: input_boolean.reef_waves       # optional with one aquarium
pumps: [pump_1, pump_2]                       # optional: only these pumps
```

---

## Details and limits

- **Time sync.** The example uses Home Assistant's clock so every ESP32 draws the same phase and
  pumps on different devices stay aligned. Without a synchronised clock the phase counts from the
  moment settings arrive; it still works but cannot align two devices.
- **Seed and anchor.** Home Assistant sends each wave's start time as a 32-bit epoch value, which
  ESPHome's action arguments allow until January 2038.
- **If Home Assistant is down.** Pumps keep their last wave. A block change that falls in the outage
  is applied when Home Assistant returns (within a minute of the ESP32 being reachable).
- **ESP32 offline.** If the action is missing the integration logs one warning and retries every minute.
- **Not included yet:** feed mode (pause or slow the flow for N minutes), and the pump's actual
  output is only exposed through the optional ESP32 sensors.
- **Test with water first.** Bench-test each motor driver with the test wave panel at low speeds, check
  the minimum speed against your motor's stall point, and keep an independent protection layer for
  anything that could harm your tank.

## Files

```
custom_components/aquarium_wavemaker/
  manifest.json        integration metadata
  __init__.py          setup, service, registers the cards and websocket API
  wave_engine.py       schedule, follow rules and waveform maths (pure Python, no HA imports)
  coordinator.py       the once-a-minute push loop
  config_flow.py       setup + options flow
  entity.py            shared base for the pump entities
  number.py select.py switch.py sensor.py
  api.py               websocket API for the cards
  card_resource.py     serves the card script and registers it as a dashboard resource
  const.py, services.yaml, strings.json, translations/en.json
  frontend/aquarium-wavemaker-card.js   editor + summary cards
esphome/
  wavemaker.yaml       example ESP32 node (PWM driver with enable pin)
  wave_engine.h        waveform engine for the ESP32 (plain C++17)
  test/parity.cpp      test harness used by the parity tests
tests/                 engine, firmware-parity and real-Home-Assistant tests
```

## Tests

```bash
pip install pytest-homeassistant-custom-component   # needs Python 3.13+ and g++ for the parity tests
pytest tests/
```

- `test_wave_engine.py`: the modes as specified, block validation, midnight crossing, the example
  schedule, slaves, inverse pumps, partial follow windows and locking, chains and loops.
- `test_cpp_parity.py`: compiles `esphome/wave_engine.h` and checks it against the Python
  reference on hundreds of random cases, plus cross-fade, speed limits and the ramp limit.
- `test_integration.py`: real Home Assistant: entities, the pushes for the example schedule,
  block boundaries, the master toggle, an offline ESP32, followers and locking, the service, and the
  websocket API.
- `test_card_resource.py`: the dashboard resource is created, updated and removed.

The example YAML was validated with `esphome config`; the engine header is compiled and tested on a
PC but has not been compiled for an ESP32 or run against your motors.
