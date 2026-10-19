"""Tests for the pure wavemaker engine (no Home Assistant needed)."""
import importlib.util
import math
import pathlib
import sys
from datetime import datetime, timezone

import pytest

_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "aquarium_wavemaker" / "wave_engine.py"
_spec = importlib.util.spec_from_file_location("wave_engine", _PATH)
we = importlib.util.module_from_spec(_spec)
sys.modules["wave_engine"] = we  # needed by dataclasses
_spec.loader.exec_module(we)

TZ = timezone.utc


def at(hhmm, day=14, sec=0):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, h, m, sec, tzinfo=TZ)


def P(**kw):
    return we.WaveParams(**kw)


def block(start="06:00", end="16:00", mode="wave", **kw):
    return {"start": start, "end": end, "mode": mode, **kw}


def pump(**kw):
    return we.PumpConfig.from_dict(kw)


# ------------------------------------------------------------- wave shapes
def test_wave_is_a_smooth_sine_from_low_to_high_and_back():
    p = P(mode="wave", low=20, high=80, period=10)
    assert we.shape_value(p, 0) == pytest.approx(20)
    assert we.shape_value(p, 2.5) == pytest.approx(50)  # quarter period: halfway up
    assert we.shape_value(p, 5) == pytest.approx(80)  # peak at mid-period
    assert we.shape_value(p, 7.5) == pytest.approx(50)
    assert we.shape_value(p, 10) == pytest.approx(20)
    assert we.shape_value(p, 12.5) == pytest.approx(we.shape_value(p, 2.5))  # periodic
    # smooth: no step bigger than the sine's maximum slope allows at 20 ms resolution
    vals = [we.shape_value(p, i * 0.02) for i in range(1000)]
    assert max(abs(b - a) for a, b in zip(vals, vals[1:])) < 60 * math.pi / 10 * 0.02 + 1e-9


def test_surge_is_a_slow_build_that_steepens_then_drops_each_period():
    p = P(mode="surge", low=30, high=60, period=20)
    assert we.shape_value(p, 0) == pytest.approx(30)  # each period starts at min
    assert we.shape_value(p, 5) == pytest.approx(30 + 30 * 0.25 ** 2)  # still barely moving at 25 %
    assert we.shape_value(p, 10) == pytest.approx(37.5)  # only a quarter of the way up at the halfway point
    assert we.shape_value(p, 15) == pytest.approx(30 + 30 * 0.75 ** 2)
    assert we.shape_value(p, 19.999) == pytest.approx(60, abs=0.05)  # peaks right at the end of the period
    assert we.shape_value(p, 20) == pytest.approx(30)  # then drops straight back to min
    assert we.shape_value(p, 30) == pytest.approx(37.5)  # and repeats
    # convex: the second half of the period climbs far more than the first
    assert we.shape_value(p, 10) - 30 < 60 - we.shape_value(p, 10)
    steps = [we.shape_value(p, i * 0.1) for i in range(200)]
    assert all(b >= a for a, b in zip(steps, steps[1:]))  # never dips while building


def test_pulse_is_high_for_the_pulse_time_and_low_for_the_remainder():
    p = P(mode="pulse", low=20, high=80, period=15, pulse=7)
    assert we.shape_value(p, 0) == 80
    assert we.shape_value(p, 6.99) == 80
    assert we.shape_value(p, 7) == 20  # low for the remaining 8 s
    assert we.shape_value(p, 14.99) == 20
    assert we.shape_value(p, 15) == 80  # next period
    # exactly 7 s high and 8 s low per period
    high = sum(1 for i in range(1500) if we.shape_value(p, i / 100) == 80)
    assert high == 700


def test_constant_ignores_min_max_and_period():
    assert we.shape_value(P(mode="constant", level=100, low=0, high=10), 123.4) == 100


def test_random_stays_in_range_is_smooth_and_deterministic():
    p = P(mode="random", low=10, high=90, interval=8, seed=42)
    vals = [we.shape_value(p, i * 0.1) for i in range(3000)]
    assert all(10 <= v <= 90 for v in vals)
    assert max(vals) - min(vals) > 40  # actually wanders
    assert vals == [we.shape_value(p, i * 0.1) for i in range(3000)]  # same seed, same walk
    assert max(abs(b - a) for a, b in zip(vals, vals[1:])) < 3  # cosine-interpolated: no jumps
    other = [we.shape_value(P(mode="random", low=10, high=90, interval=8, seed=43), i * 0.1) for i in range(100)]
    assert other != vals[:100]


def test_invert_mirrors_every_periodic_mode_so_the_sum_is_constant():
    for mode in ("wave", "pulse", "surge", "random"):
        a = P(mode=mode, low=20, high=80, period=10, pulse=4, interval=5, seed=7, anchor=100)
        b = P(**{**a.to_dict(), "invert": True})
        for i in range(400):
            t = 100 + i * 0.137
            assert we.shape_value(a, t) + we.shape_value(b, t) == pytest.approx(100)  # low + high


def test_hash_is_stable_across_implementations():
    # pinned values: the ESP32 firmware must produce these exactly (see test_cpp_parity.py)
    assert we.hash01(0, 0) == 0.0
    assert we.hash01(1, 0) == pytest.approx(0.12644983571954072, abs=1e-12)
    assert we.hash01(42, 3) == pytest.approx(0.09065641951747239, abs=1e-12)
    assert we.hash01(123456, -5) == pytest.approx(0.8546205551829189, abs=1e-12)
    assert all(0 <= we.hash01(s, k) < 1 for s in (1, 99, 2**31 - 1) for k in range(-50, 50))


def test_describe():
    assert we.describe(P(mode="wave", low=20, high=80, period=10)) == "wave 20-80%, 10 s"
    assert we.describe(P(mode="pulse", low=20, high=80, period=15, pulse=7)) == "pulse 20-80%, 15 s (7 s on)"
    assert we.describe(P(mode="surge", low=30, high=60, period=20, invert=True)) == "surge 30-60%, 20 s, inverted"
    assert we.describe(P(mode="constant", level=100)) == "constant 100%"


# ------------------------------------------------------------ block validation
def test_pulse_time_must_be_below_the_period():
    assert we.Block(mode="pulse", period=15, pulse=7)
    for bad in (15, 20, 0):
        with pytest.raises(ValueError, match="Pulse time"):
            we.Block(mode="pulse", period=15, pulse=bad)


@pytest.mark.parametrize(
    "bad",
    [{"low": -1}, {"high": 101}, {"low": 90, "high": 10}, {"period": 0}, {"period": 5000},
     {"interval": 0}, {"mode": "tsunami"}, {"start": "25:00"}, {"level": 101}],
)
def test_block_validation(bad):
    with pytest.raises(ValueError):
        we.Block.from_dict({**block(), **bad})


def test_midnight_crossing_and_full_day_blocks():
    b = we.Block(start="21:10", end="06:00")
    assert b.covers(21 * 60 + 10) and b.covers(0) and b.covers(5 * 60 + 59)
    assert not b.covers(6 * 60) and not b.covers(12 * 60) and not b.covers(21 * 60 + 9)
    assert we.Block(start="00:00", end="00:00").minutes == 1440


def test_blocks_may_not_overlap_but_may_touch_and_leave_gaps():
    assert pump(blocks=[block("06:00", "16:00"), block("16:00", "21:00", "random")])
    with pytest.raises(ValueError, match="overlap"):
        pump(blocks=[block("06:00", "16:00"), block("15:59", "21:00")])
    with pytest.raises(ValueError, match="overlap"):
        pump(blocks=[block("20:00", "02:00"), block("01:00", "03:00")])  # across midnight
    with pytest.raises(ValueError, match="overlap"):
        pump(blocks=[block("00:00", "00:00"), block("06:00", "07:00")])  # full day + another


# ------------------------------------------------------ the example schedule
def example_pumps():
    return {
        "p1": pump(
            blocks=[
                block("06:00", "16:00", "wave", low=20, high=80, period=10),
                block("16:00", "21:00", "random", low=10, high=90, interval=10),
                block("21:00", "21:10", "constant", level=100),
                block("21:10", "06:00", "surge", low=30, high=60, period=20),
            ]
        )
    }


@pytest.mark.parametrize(
    "hhmm, text",
    [
        ("05:59", "surge 30-60%, 20 s"), ("06:00", "wave 20-80%, 10 s"), ("15:59", "wave 20-80%, 10 s"),
        ("16:00", "random 10-90%, every 10 s"), ("20:59", "random 10-90%, every 10 s"),
        ("21:00", "constant 100%"), ("21:09", "constant 100%"), ("21:10", "surge 30-60%, 20 s"),
        ("23:30", "surge 30-60%, 20 s"), ("03:00", "surge 30-60%, 20 s"),
    ],
)
def test_example_schedule_picks_the_right_block(hhmm, text):
    assert we.describe(we.resolve(example_pumps(), "p1", at(hhmm))) == text


def test_gap_uses_the_default_level():
    pumps = {"p1": pump(blocks=[block("06:00", "10:00")], default_level=35)}
    assert we.resolve(pumps, "p1", at("12:00")) == P(mode="constant", level=35)


def test_anchor_is_the_block_start_even_after_midnight():
    pumps = example_pumps()
    late = we.resolve(pumps, "p1", at("23:30"))
    after_midnight = we.resolve(pumps, "p1", at("03:00", day=15))
    assert late.anchor == int(at("21:10").timestamp())
    assert after_midnight.anchor == late.anchor  # same block, started yesterday evening
    assert late.seed == after_midnight.seed


def test_next_change_finds_the_block_boundary():
    assert we.next_change(example_pumps(), "p1", at("15:30", sec=20)) == at("16:00")
    assert we.next_change(example_pumps(), "p1", at("21:05")) == at("21:10")
    assert we.next_change({"p": pump(blocks=[block("00:00", "00:00", "constant", level=60)])}, "p", at("12:00")) is None


# ---------------------------------------------------- slaves and inverse pumps
def with_follower(**follow):
    pumps = example_pumps()
    pumps["p2"] = pump(follow_source="p1", **follow)
    return pumps


def test_slave_runs_the_same_schedule_in_phase():
    pumps = with_follower(follow_relation="slave")
    for hhmm in ("07:00", "17:00", "21:05", "22:00", "03:00"):
        assert we.resolve(pumps, "p2", at(hhmm)) == we.resolve(pumps, "p1", at(hhmm))


def test_inverse_pump_is_out_of_phase_in_every_mode_except_constant():
    pumps = with_follower(follow_relation="inverse")
    for hhmm in ("07:00", "17:00", "22:00"):
        a, b = we.resolve(pumps, "p1", at(hhmm)), we.resolve(pumps, "p2", at(hhmm))
        assert (a.mode, a.low, a.high, a.seed, a.anchor) == (b.mode, b.low, b.high, b.seed, b.anchor)
        assert b.invert and not a.invert
        t = a.anchor + 33.3
        assert we.shape_value(a, t) + we.shape_value(b, t) == pytest.approx(a.low + a.high)


def test_inverse_in_constant_mode_mirrors_or_inverts_by_toggle():
    mirror = with_follower(follow_relation="inverse", follow_constant="mirror")
    invert = with_follower(follow_relation="inverse", follow_constant="invert")
    assert we.resolve(mirror, "p2", at("21:05")).level == 100  # follows the source's level
    flipped = we.resolve(invert, "p2", at("21:05"))
    assert flipped.mode == "constant" and flipped.level == 0  # 100 - 100
    # the toggle only matters in constant mode
    assert we.resolve(invert, "p2", at("07:00")).invert is True


def test_inversion_for_only_part_of_the_schedule_is_locked_to_those_times():
    pumps = example_pumps()
    pumps["p2"] = pump(
        follow_source="p1", follow_relation="inverse", follow_all_day=False,
        follow_windows=[["16:00", "21:10"]],
        blocks=[block("06:00", "16:00", "pulse", low=10, high=70, period=12, pulse=4),
                block("21:10", "06:00", "constant", level=40)],
    )
    inside = we.resolve(pumps, "p2", at("18:00"))
    assert inside.mode == "random" and inside.invert  # follows p1's random block, inverted
    assert we.describe(we.resolve(pumps, "p2", at("08:00"))) == "pulse 10-70%, 12 s (4 s on)"  # its own schedule
    assert we.resolve(pumps, "p2", at("23:00")).level == 40
    # locked: a block may not cover the followed times
    with pytest.raises(ValueError, match="locked"):
        pump(follow_source="p1", follow_all_day=False, follow_windows=[["16:00", "21:00"]],
             blocks=[block("20:00", "22:00")])
    with pytest.raises(ValueError, match="at least one follow window"):
        pump(follow_source="p1", follow_all_day=False, follow_windows=[])


def test_follow_chains_compose_and_double_inversion_cancels():
    pumps = example_pumps()
    pumps["p2"] = pump(follow_source="p1", follow_relation="inverse")
    pumps["p3"] = pump(follow_source="p2", follow_relation="inverse")
    pumps["p4"] = pump(follow_source="p3", follow_relation="slave")
    assert we.resolve(pumps, "p3", at("07:00")).invert is False  # inverse of an inverse = in phase
    assert we.resolve(pumps, "p4", at("07:00")).invert is False
    assert we.resolve(pumps, "p2", at("07:00")).invert is True


def test_network_rules_catch_missing_sources_and_loops():
    ok = {"a": pump(), "b": pump(follow_source="a")}
    we.validate_network(ok)
    with pytest.raises(ValueError, match="does not exist"):
        we.validate_network({"b": pump(follow_source="ghost")})
    with pytest.raises(ValueError, match="loop"):
        we.validate_network({"a": pump(follow_source="a")})
    with pytest.raises(ValueError, match="loop"):
        we.validate_network({"a": pump(follow_source="b"), "b": pump(follow_source="a")})
    chain = {f"p{i}": pump(follow_source=f"p{i + 1}") for i in range(10)}
    chain["p10"] = pump()
    with pytest.raises(ValueError, match="limited"):
        we.validate_network(chain)


# ----------------------------------------------------------- pump settings
@pytest.mark.parametrize(
    "bad",
    [{"service": "wavemaker"}, {"service": "esphome.Node Set"}, {"channel": 16}, {"channel": -1},
     {"floor": 50, "cap": 40}, {"cap": 101}, {"slew": 0}, {"fade_seconds": 601},
     {"default_level": 101}, {"follow_relation": "twin"}, {"follow_constant": "flip"}],
)
def test_pump_validation(bad):
    with pytest.raises(ValueError):
        pump(**bad)


def test_roundtrip_and_defaults():
    p = pump(service=" esphome.wm_set_wave ", blocks=[block()], follow_source="x")
    d = p.to_dict()
    assert d["service"] == "esphome.wm_set_wave" and d["fade_seconds"] == 60 and d["floor"] == 10
    assert we.PumpConfig.from_dict(d).to_dict() == d


def test_block_preview_samples():
    wave = we.block_preview(we.Block(mode="wave", low=20, high=80, period=10))
    assert wave["duration"] == 30 and len(wave["values"]) == 240
    assert min(wave["values"]) == pytest.approx(20, abs=0.1) and max(wave["values"]) == pytest.approx(80, abs=0.1)
    rnd = we.block_preview(we.Block(mode="random", low=10, high=90, interval=10))
    assert rnd["duration"] == 120 and all(10 <= v <= 90 for v in rnd["values"])


# ------------------------------------------------------------- display names
def test_display_name_defaults_to_the_id_made_readable():
    assert we.display_name("left_return") == "Left Return"
    assert we.display_name("pump_1", "Front glass") == "Front glass"


def test_label_is_normalised_and_limited():
    assert pump(label="  Front   glass ").label == "Front glass"
    with pytest.raises(ValueError, match="at most 40"):
        pump(label="x" * 41)


def test_display_names_must_be_unique_across_pumps():
    ok = {"a": pump(label="Left"), "b": pump(label="Right"), "c": pump()}
    we.validate_network(ok)
    with pytest.raises(ValueError, match="already used"):
        we.validate_network({"a": pump(label="Left"), "b": pump(label="left")})
    with pytest.raises(ValueError, match="already used"):
        we.validate_network({"pump_1": pump(), "other": pump(label="Pump 1")})  # clashes with a default name


def test_renaming_does_not_change_the_waveform_or_follow_links():
    before = {"a": pump(blocks=[block("00:00", "00:00", "random", low=10, high=90)]), "b": pump(follow_source="a")}
    after = {"a": pump(label="Renamed", blocks=[block("00:00", "00:00", "random", low=10, high=90)]), "b": pump(follow_source="a")}
    assert we.resolve(before, "a", at("12:00")) == we.resolve(after, "a", at("12:00"))  # same seed
    assert we.resolve(after, "b", at("12:00")) == we.resolve(after, "a", at("12:00"))
