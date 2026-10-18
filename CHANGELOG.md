# Changelog

## 0.1.0
- Initial release: time-block schedules per pump with wave, pulse, surge, random and
  constant modes; slave and inverse pumps (all day or only part of the day, with the
  followed times locked); 60 s cross-fade between blocks
- The ESP32 draws the waveform locally (`esphome/wave_engine.h`); Home Assistant only
  sends settings once a minute and at every change
- Every motor-protection setting is an entity; two Lovelace cards (editor and summary)
