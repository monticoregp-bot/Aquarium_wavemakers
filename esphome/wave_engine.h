// Waveform engine for the Aquarium Wavemaker integration (ESPHome / ESP32).
//
// Home Assistant decides WHICH wave mode a pump should run and sends its settings
// (see `set_wave` in wavemaker.yaml). This header draws the waveform locally at
// 50 Hz, so it is smooth no matter what Wi-Fi or Home Assistant are doing, and it
// keeps running if either goes away.
//
// The maths mirrors `shape_value` in custom_components/aquarium_wavemaker/
// wave_engine.py exactly; tests/test_cpp_parity.py compiles this file and checks
// both against each other. Plain C++17, no ESPHome types, so it can be unit tested
// on a PC.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstring>

namespace wavemaker {

constexpr double PI = 3.141592653589793;
constexpr int MAX_CHANNELS = 8;

enum Mode : uint8_t { CONSTANT = 0, WAVE = 1, PULSE = 2, SURGE = 3, RANDOM = 4 };

inline Mode mode_from_string(const char *s) {
  if (!strcmp(s, "wave")) return WAVE;
  if (!strcmp(s, "pulse")) return PULSE;
  if (!strcmp(s, "surge")) return SURGE;
  if (!strcmp(s, "random")) return RANDOM;
  return CONSTANT;
}

// Deterministic pseudo-random number in [0, 1) for (seed, slot k). Same integer
// maths as the Python reference, so a slave / inverse pump follows the same walk.
inline double hash01(uint32_t seed, int32_t k) {
  uint32_t x = seed * 0x9E3779B1u + static_cast<uint32_t>(k) * 0x85EBCA6Bu;
  x ^= x >> 15;
  x *= 0x2C1B3C6Du;
  x ^= x >> 12;
  x *= 0x297A2D39u;
  x ^= x >> 15;
  return x / 4294967296.0;
}

struct Params {
  Mode mode = CONSTANT;
  double low = 0, high = 0;  // % (wave, pulse, surge, random)
  double level = 50;         // % (constant)
  double period = 10;        // s (wave, pulse, surge)
  double pulse = 5;          // s at `high` per period (pulse)
  double interval = 10;      // s between random targets (random)
  uint32_t seed = 0;         // random sequence seed
  double anchor = 0;         // epoch seconds the phase counts from (block start)
  bool invert = false;       // mirror about the midpoint of low..high

  bool same(const Params &o) const {
    return mode == o.mode && low == o.low && high == o.high && level == o.level && period == o.period &&
           pulse == o.pulse && interval == o.interval && seed == o.seed && anchor == o.anchor && invert == o.invert;
  }
};

// Waveform level in % at time `now` (epoch seconds), before the pump limits.
inline double shape_value(const Params &p, double now) {
  if (p.mode == CONSTANT) return p.level;
  const double t = now - p.anchor;
  double value;
  if (p.mode == PULSE) {
    double tm = std::fmod(t, p.period);
    if (tm < 0) tm += p.period;
    value = tm < p.pulse ? p.high : p.low;
  } else if (p.mode == RANDOM) {
    const double pos = t / p.interval;
    const double kf = std::floor(pos);
    const int32_t k = static_cast<int32_t>(kf);
    const double a = hash01(p.seed, k), b = hash01(p.seed, k + 1);
    const double w = (1.0 - std::cos(PI * (pos - kf))) / 2.0;
    value = p.low + (p.high - p.low) * (a + (b - a) * w);
  } else {
    double phase = t / p.period;
    phase -= std::floor(phase);
    if (p.mode == WAVE)
      value = p.low + (p.high - p.low) * (1.0 - std::cos(2.0 * PI * phase)) / 2.0;
    else  // SURGE: slow build (starts gently, steepens), then drops back to low
      value = p.low + (p.high - p.low) * phase * phase;
  }
  if (p.invert) value = p.low + p.high - value;
  return value;
}

// Wall clock that never steps backwards between syncs: epoch time from the
// synchronised clock plus ESP32 milliseconds.
struct Clock {
  double base_epoch = 0;
  uint32_t base_ms = 0;
  bool valid = false;

  void sync(double epoch, uint32_t ms) {
    base_epoch = epoch;
    base_ms = ms;
    valid = true;
  }
  double now(uint32_t ms) const {
    return valid ? base_epoch + static_cast<uint32_t>(ms - base_ms) / 1000.0 : ms / 1000.0;
  }
};

// One pump: cross-fades between parameter sets and protects the motor.
class Channel {
 public:
  double floor_pct = 0;     // minimum running speed (a computed 0 still means off)
  double cap_pct = 100;     // maximum speed
  double slew_pct_s = 100;  // largest change per second

  // Home Assistant resends the current settings every minute; identical settings
  // are ignored, new ones cross-fade in over `fade_s`.
  void set(const Params &next, double fade_s, double now) {
    if (have_ && cur_.same(next)) return;
    if (!have_) {
      prev_ = cur_ = next;
      fade_len_ = 0;
    } else {
      prev_ = cur_;
      cur_ = next;
      fade_len_ = fade_s;
      fade_start_ = now;
    }
    have_ = true;
  }

  bool configured() const { return have_; }

  // Blended waveform with the speed limits applied, before slew limiting.
  double target(double now) const {
    if (!have_) return 0;
    double v = shape_value(cur_, now);
    if (fade_len_ > 0) {
      double a = (now - fade_start_) / fade_len_;
      if (a < 1.0) {
        if (a < 0) a = 0;
        a = a * a * (3.0 - 2.0 * a);  // smoothstep
        v = (1.0 - a) * shape_value(prev_, now) + a * v;
      }
    }
    return limit(v);
  }

  // Advance by dt seconds; returns the output in % (0 = off).
  double update(double now, double dt) {
    const double want = target(now);
    const double step = slew_pct_s * dt;
    if (want > out_ + step)
      out_ += step;
    else if (want < out_ - step)
      out_ -= step;
    else
      out_ = want;
    return out_;
  }

  double output() const { return out_; }

 private:
  double limit(double v) const {
    if (v <= 0.0) return 0.0;
    const double lo = floor_pct < cap_pct ? floor_pct : cap_pct;
    if (v < lo) v = lo;
    if (v > cap_pct) v = cap_pct;
    return v;
  }

  Params prev_, cur_;
  bool have_ = false;
  double fade_start_ = 0, fade_len_ = 0;
  double out_ = 0;
};

// ---- ready-made state for the ESPHome configuration -------------------------
inline Clock clock_state;
inline Channel channels[MAX_CHANNELS];
inline uint32_t last_tick_ms = 0;

// Called from the `set_wave` API action.
inline void apply(int channel, const char *mode, double low, double high, double level, double period, double pulse,
                  double interval, int32_t seed, int32_t anchor, bool invert, double fade, double speed_min,
                  double speed_max, double ramp, uint32_t ms) {
  if (channel < 0 || channel >= MAX_CHANNELS) return;
  Params p;
  p.mode = mode_from_string(mode);
  p.low = low;
  p.high = high;
  p.level = level;
  p.period = period > 0 ? period : 1;
  p.pulse = pulse;
  p.interval = interval > 0 ? interval : 1;
  p.seed = static_cast<uint32_t>(seed);
  p.invert = invert;
  const double now = clock_state.now(ms);
  // Without a synchronised clock, count the phase from the moment settings arrive.
  p.anchor = clock_state.valid ? static_cast<double>(anchor) : now;
  Channel &c = channels[channel];
  c.floor_pct = speed_min;
  c.cap_pct = speed_max;
  c.slew_pct_s = ramp > 0 ? ramp : 100;
  c.set(p, fade, now);
}

// Advance every channel; call every ~20 ms. Returns seconds since the last call.
inline double tick(uint32_t ms) {
  double dt = last_tick_ms == 0 ? 0.02 : static_cast<uint32_t>(ms - last_tick_ms) / 1000.0;
  last_tick_ms = ms;
  if (dt > 0.5) dt = 0.5;  // after a long stall, do not leap
  const double now = clock_state.now(ms);
  for (int i = 0; i < MAX_CHANNELS; i++)
    if (channels[i].configured()) channels[i].update(now, dt);
  return dt;
}

}  // namespace wavemaker
