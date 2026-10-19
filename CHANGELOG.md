# Changelog

## 0.2.0
- Pumps can be renamed after creation: the name shown changes, while the pump's id, entity
  ids, followers and wave timing stay the same. Names must be unique
- The schedule timeline draws each block as a 50 % band with a sketch of its mode (wave, surge,
  pulse, random, constant) on top, in the editor and the summary card
- **Surge is now a slow build** (starts gently, steepens, then drops) instead of linear
- Clearer connection wording: the setup step and the card explain that a pump is connected through
  an ESPHome action plus a channel (not a speed entity), and offer the actions Home Assistant can see
- Entity names clarified: "Speed change limit", "Current mode", "Current wave"

## 0.1.0
- Initial release: time-block schedules per pump with wave, pulse, surge, random and
  constant modes; slave and inverse pumps (all day or only part of the day, with the
  followed times locked); 60 s cross-fade between blocks
- The ESP32 draws the waveform locally (`esphome/wave_engine.h`); Home Assistant only
  sends settings once a minute and at every change
- Every motor-protection setting is an entity; two Lovelace cards (editor and summary)
