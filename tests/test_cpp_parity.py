"""The ESP32 firmware engine (esphome/wave_engine.h) must match the Python reference.

Compiles esphome/test/parity.cpp with g++ and drives it with random cases. Skipped
if no C++ compiler is available.
"""
import importlib.util
import pathlib
import random
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("wave_engine", ROOT / "custom_components" / "aquarium_wavemaker" / "wave_engine.py")
we = importlib.util.module_from_spec(_spec)
sys.modules["wave_engine"] = we
_spec.loader.exec_module(we)

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("cpp") / "parity"
    subprocess.run(
        ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", str(ROOT / "esphome" / "test" / "parity.cpp"), "-o", str(out)],
        check=True, capture_output=True, text=True,
    )
    return out


def run(binary, script: str) -> list[float]:
    result = subprocess.run([str(binary)], input=script, capture_output=True, text=True, check=True)
    return [float(x) for x in result.stdout.split()]


def params_line(p) -> str:
    return f"{p.mode} {p.low!r} {p.high!r} {p.level!r} {p.period!r} {p.pulse!r} {p.interval!r} {p.seed} {p.anchor} {int(p.invert)}"


def test_hash_matches_python(binary):
    cases = [(0, 0), (1, 0), (42, 3), (123456, -5), (0x7FFFFFFF, 99999), (7, -123456)]
    got = run(binary, "".join(f"hash {s} {k}\n" for s, k in cases))
    for (s, k), value in zip(cases, got):
        assert value == pytest.approx(we.hash01(s, k), abs=1e-11)


def test_shapes_match_python_on_random_cases(binary):
    rng = random.Random(1234)
    cases, script = [], ""
    for _ in range(600):
        mode = rng.choice(we.MODES)
        low = rng.uniform(0, 60)
        p = we.WaveParams(
            mode=mode, low=low, high=rng.uniform(low, 100), level=rng.uniform(0, 100),
            period=rng.uniform(1, 120), pulse=0, interval=rng.uniform(1, 60),
            seed=rng.randrange(0, 2**31), anchor=rng.randrange(1_700_000_000, 1_900_000_000),
            invert=rng.random() < 0.5,
        )
        p = we.WaveParams(**{**p.to_dict(), "pulse": rng.uniform(0.1, p.period - 0.1)})
        now = p.anchor + rng.uniform(-50, 200_000)  # includes times before the anchor
        cases.append((p, now))
        script += f"shape {params_line(p)} {now!r}\n"
    for (p, now), value in zip(cases, run(binary, script)):
        assert value == pytest.approx(we.shape_value(p, now), abs=1e-6), (p, now)


def test_example_modes_in_firmware(binary):
    # the modes exactly as specified: wave 20-80 / 10 s, surge 30-60 / 20 s, pulse 15 s with 7 s on
    script = ""
    wave = we.WaveParams(mode="wave", low=20, high=80, period=10)
    surge = we.WaveParams(mode="surge", low=30, high=60, period=20)
    pulse = we.WaveParams(mode="pulse", low=20, high=80, period=15, pulse=7)
    for p, times in ((wave, (0, 2.5, 5)), (surge, (0, 10, 20)), (pulse, (0, 6.99, 7, 15))):
        for t in times:
            script += f"shape {params_line(p)} {t}\n"
    got = run(binary, script)
    assert got[:3] == pytest.approx([20, 50, 80]) and got[3:6] == pytest.approx([30, 45, 30]) and got[6:] == [80, 80, 20, 80]


def test_idempotent_set_is_ignored_and_new_settings_cross_fade(binary):
    base = we.WaveParams(mode="constant", level=20)
    new = we.WaveParams(mode="constant", level=80)
    script = (
        "chan 0 100 1000\n"
        f"set {params_line(base)} 60 100\n"     # first settings: no fade
        "target 100\n"
        f"set {params_line(base)} 60 105\n"      # identical resend every minute: ignored
        "target 105\n"
        f"set {params_line(new)} 10 200\n"       # new block: 10 s cross-fade starting at t=200
        "target 200\ntarget 205\ntarget 210\ntarget 300\n"
    )
    assert run(binary, script) == pytest.approx([20, 20, 20, 50, 80, 80])  # smoothstep(0.5) = 0.5


def test_speed_limits_and_off(binary):
    def target(level, floor=10, cap=90):
        p = we.WaveParams(mode="constant", level=level)
        return run(binary, f"chan {floor} {cap} 1000\nset {params_line(p)} 0 0\ntarget 1\n")[0]

    assert target(5) == 10        # below the minimum running speed: raised to it
    assert target(0) == 0         # a computed 0 means off, not the floor
    assert target(100) == 90      # capped
    assert target(50) == 50


def test_ramp_limit_never_changes_faster_than_allowed(binary):
    # hard-edged pulse, 4 s high / 4 s low, but the motor may only change 50 % per second
    p = we.WaveParams(mode="pulse", low=10, high=90, period=8, pulse=4)
    script = "chan 0 100 50\n" + f"set {params_line(p)} 0 0\n" + "".join(f"update {i * 0.02} 0.02\n" for i in range(1, 1000))
    out = run(binary, script)
    assert max(abs(b - a) for a, b in zip(out, out[1:])) <= 50 * 0.02 + 1e-9  # <= 50 %/s at 50 Hz
    assert max(out) == pytest.approx(90) and min(out[300:]) == pytest.approx(10)  # full swing still completes


def test_a_pulse_too_short_for_the_ramp_limit_is_softened_not_skipped(binary):
    # 1 s high at 50 %/s cannot reach 90 from 10: the limit shaves the peak instead of jerking the motor
    p = we.WaveParams(mode="pulse", low=10, high=90, period=2, pulse=1)
    script = "chan 0 100 50\n" + f"set {params_line(p)} 0 0\n" + "".join(f"update {i * 0.02} 0.02\n" for i in range(1, 400))
    out = run(binary, script)
    assert 50 < max(out) < 90
