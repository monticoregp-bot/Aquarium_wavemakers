// Test harness: reads commands on stdin, prints results. Driven by tests/test_cpp_parity.py.
//   shape mode low high level period pulse interval seed anchor invert now
//   chan floor cap slew                      (configure channel 0)
//   set mode low high level period pulse interval seed anchor invert fade now
//   target now | update now dt | hash seed k
#include <cstdio>
#include <iostream>
#include <string>

#include "../wave_engine.h"

using namespace wavemaker;

static Params read_params() {
  Params p;
  std::string mode;
  unsigned seed = 0;
  int invert = 0;
  std::cin >> mode >> p.low >> p.high >> p.level >> p.period >> p.pulse >> p.interval >> seed >> p.anchor >> invert;
  p.mode = mode_from_string(mode.c_str());
  p.seed = seed;
  p.invert = invert != 0;
  return p;
}

int main() {
  std::string c;
  Channel ch;
  while (std::cin >> c) {
    if (c == "shape") {
      Params p = read_params();
      double now = 0;
      std::cin >> now;
      std::printf("%.12f\n", shape_value(p, now));
    } else if (c == "chan") {
      std::cin >> ch.floor_pct >> ch.cap_pct >> ch.slew_pct_s;
    } else if (c == "set") {
      Params p = read_params();
      double fade = 0, now = 0;
      std::cin >> fade >> now;
      ch.set(p, fade, now);
    } else if (c == "target") {
      double now = 0;
      std::cin >> now;
      std::printf("%.9f\n", ch.target(now));
    } else if (c == "update") {
      double now = 0, dt = 0;
      std::cin >> now >> dt;
      std::printf("%.9f\n", ch.update(now, dt));
    } else if (c == "hash") {
      unsigned seed = 0;
      int k = 0;
      std::cin >> seed >> k;
      std::printf("%.12f\n", hash01(seed, k));
    }
  }
  return 0;
}
