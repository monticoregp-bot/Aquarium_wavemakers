"""Pure wavemaker engine for Aquarium Wavemaker (no Home Assistant imports).

Two jobs live here so both can be unit-tested stand-alone:

1. *Which* wave settings a pump should be running at a given time of day:
   time blocks, follow relationships (slave / inverse, all day or only part of
   the day) and how they combine. Home Assistant sends the result to the ESP32
   once a minute.
2. *What* each wave mode looks like: ``shape_value`` is the reference
   implementation of the waveform. The ESP32 firmware (``wave_engine.h``)
   implements the same maths and is cross-checked against this file by the tests.

All waveforms are functions of absolute time and the block's start (the
"anchor"), so two pumps on the same block, a slave, and an inverse pump all stay
phase-locked without any per-pump phase to send.
"""
from __future__ import annotations

import math
import re
import zlib
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, time, timedelta
from typing import Any

MINUTES_PER_DAY = 1440
MODES = ("constant", "wave", "pulse", "surge", "random")
RELATIONS = ("slave", "inverse")
CONSTANT_BEHAVIOURS = ("mirror", "invert")
MAX_FOLLOW_DEPTH = 8
NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
SERVICE_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
MAX_CHANNEL = 15


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def parse_hhmm(value: Any) -> int:
    try:
        hours_s, minutes_s = str(value).strip().split(":")[:2]
        hours, minutes = int(hours_s), int(minutes_s)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid time {value!r}; expected HH:MM") from None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f"Invalid time {value!r}; expected HH:MM (00:00-23:59)")
    return hours * 60 + minutes


def format_hhmm(minutes: int) -> str:
    minutes %= MINUTES_PER_DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def validate_pump_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(
            "Pump names may only contain lowercase letters, digits and "
            "underscores (max 32 characters)"
        )
    return name


def _num(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number") from None


# --------------------------------------------------------------------------- #
# waveform maths (reference implementation; the firmware mirrors it)
# --------------------------------------------------------------------------- #
def hash01(seed: int, k: int) -> float:
    """Deterministic pseudo-random number in [0, 1) for (seed, slot k).

    Pure 32-bit integer maths so the ESP32 produces the same sequence: two pumps
    sharing a seed (a slave, an inverse pump) follow the very same random walk.
    """
    x = ((seed & 0xFFFFFFFF) * 0x9E3779B1 + (k & 0xFFFFFFFF) * 0x85EBCA6B) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x2C1B3C6D) & 0xFFFFFFFF
    x ^= x >> 12
    x = (x * 0x297A2D39) & 0xFFFFFFFF
    x ^= x >> 15
    return x / 4294967296.0


@dataclass(frozen=True)
class WaveParams:
    """Everything the ESP32 needs to draw one pump's waveform."""

    mode: str = "constant"
    low: float = 0.0  # % (wave, pulse, surge, random)
    high: float = 0.0
    level: float = 50.0  # % (constant)
    period: float = 10.0  # s (wave, pulse, surge)
    pulse: float = 5.0  # s on at `high` per period (pulse)
    interval: float = 10.0  # s between random targets (random)
    seed: int = 0  # random sequence seed (random)
    anchor: int = 0  # epoch seconds the phase counts from (block start)
    invert: bool = False  # mirror level about the midpoint of low..high

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def shape_value(p: WaveParams, now: float) -> float:
    """Waveform level in % at time ``now`` (epoch seconds), before pump limits."""
    if p.mode == "constant":
        return p.level
    t = now - p.anchor
    if p.mode == "pulse":
        value = p.high if (t % p.period) < p.pulse else p.low
    elif p.mode == "random":
        pos = t / p.interval
        k = math.floor(pos)
        a, b = hash01(p.seed, k), hash01(p.seed, k + 1)
        weight = (1.0 - math.cos(math.pi * (pos - k))) / 2.0
        value = p.low + (p.high - p.low) * (a + (b - a) * weight)
    else:
        phase = t / p.period
        phase -= math.floor(phase)
        if p.mode == "wave":  # smooth sine: starts at low, peaks at high mid-period
            value = p.low + (p.high - p.low) * (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0
        else:  # surge: linear build from low to high, then back to low
            value = p.low + (p.high - p.low) * phase
    if p.invert:
        value = p.low + p.high - value
    return value


def waveform_samples(p: WaveParams, duration: float, points: int = 240) -> list[float]:
    """Samples of one waveform from its anchor over ``duration`` seconds."""
    points = max(2, points)
    return [round(shape_value(p, p.anchor + duration * i / (points - 1)), 3) for i in range(points)]


def describe(p: WaveParams) -> str:
    """Short human-readable description of what a pump is doing."""
    if p.mode == "constant":
        text = f"constant {p.level:g}%"
    elif p.mode == "wave":
        text = f"wave {p.low:g}-{p.high:g}%, {p.period:g} s"
    elif p.mode == "pulse":
        text = f"pulse {p.low:g}-{p.high:g}%, {p.period:g} s ({p.pulse:g} s on)"
    elif p.mode == "surge":
        text = f"surge {p.low:g}-{p.high:g}%, {p.period:g} s"
    else:
        text = f"random {p.low:g}-{p.high:g}%, every {p.interval:g} s"
    return text + (", inverted" if p.invert and p.mode != "constant" else "")


# --------------------------------------------------------------------------- #
# schedule blocks
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    """One time range with a wave mode and its settings."""

    start: str = "00:00"
    end: str = "00:00"  # end == start means the whole day
    mode: str = "constant"
    low: float = 20.0
    high: float = 80.0
    level: float = 50.0
    period: float = 10.0
    pulse: float = 5.0
    interval: float = 10.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self._s, self._e = parse_hhmm(self.start), parse_hhmm(self.end)
        self.start, self.end = format_hhmm(self._s), format_hhmm(self._e)
        if self.mode not in MODES:
            raise ValueError(f"Wave mode must be one of {', '.join(MODES)}")
        self.low = _num(self.low, "Minimum")
        self.high = _num(self.high, "Maximum")
        self.level = _num(self.level, "Level")
        self.period = _num(self.period, "Period")
        self.pulse = _num(self.pulse, "Pulse time")
        self.interval = _num(self.interval, "Random interval")
        for label, value in (("Minimum", self.low), ("Maximum", self.high), ("Level", self.level)):
            if not 0 <= value <= 100:
                raise ValueError(f"{label} must be between 0 and 100 %")
        if self.low > self.high:
            raise ValueError("Minimum cannot be above maximum")
        if not 1 <= self.period <= 3600:
            raise ValueError("Period must be 1-3600 seconds")
        if self.mode == "pulse" and not 0 < self.pulse < self.period:
            raise ValueError(
                f"Pulse time must be above 0 and below the period ({self.period:g} s); "
                "the pump is low for the rest of the period"
            )
        if not 1 <= self.interval <= 600:
            raise ValueError("Random interval must be 1-600 seconds")

    @property
    def minutes(self) -> int:
        return (self._e - self._s) % MINUTES_PER_DAY or MINUTES_PER_DAY

    def covers(self, minute: int) -> bool:
        return (minute - self._s) % MINUTES_PER_DAY < self.minutes

    @property
    def label(self) -> str:
        return f"{self.start}-{self.end}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Block":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict(data).items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def params(self, seed: int, anchor: int) -> WaveParams:
        return WaveParams(
            mode=self.mode, low=self.low, high=self.high, level=self.level,
            period=self.period, pulse=self.pulse, interval=self.interval,
            seed=seed, anchor=anchor, invert=False,
        )


def _occupancy(ranges: list[tuple[int, int]]) -> list[int]:
    """How many of the given (start, length) minute ranges cover each minute."""
    cover = [0] * MINUTES_PER_DAY
    for start, length in ranges:
        for i in range(length):
            cover[(start + i) % MINUTES_PER_DAY] += 1
    return cover


# --------------------------------------------------------------------------- #
# pump configuration
# --------------------------------------------------------------------------- #
@dataclass
class PumpConfig:
    """Settings for one wavemaker pump."""

    # how Home Assistant reaches the ESP32 (an ESPHome action) and which channel
    service: str = ""
    channel: int = 0
    # motor protection, applied on the ESP32
    floor: float = 10.0  # % minimum running speed (a computed 0 still means off)
    cap: float = 100.0  # % maximum speed
    slew: float = 100.0  # % per second the output may change by
    fade_seconds: float = 60.0  # cross-fade between blocks
    default_level: float = 50.0  # % when no block is active
    blocks: list[Block] = field(default_factory=list)
    # follow another pump (slave: same output; inverse: opposite phase)
    follow_source: str = ""
    follow_relation: str = "slave"
    follow_all_day: bool = True
    follow_windows: list[list[str]] = field(default_factory=list)  # [["HH:MM","HH:MM"], ...]
    follow_constant: str = "mirror"  # inverse pumps in constant mode: mirror | invert

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self.service = (self.service or "").strip()
        if self.service and not SERVICE_RE.match(self.service):
            raise ValueError("The ESPHome action must look like esphome.node_set_wave")
        self.channel = int(_num(self.channel, "Channel"))
        if not 0 <= self.channel <= MAX_CHANNEL:
            raise ValueError(f"Channel must be 0-{MAX_CHANNEL}")
        self.floor = _num(self.floor, "Minimum speed")
        self.cap = _num(self.cap, "Maximum speed")
        self.slew = _num(self.slew, "Ramp limit")
        self.fade_seconds = _num(self.fade_seconds, "Cross-fade time")
        self.default_level = _num(self.default_level, "Default level")
        if not (0 <= self.floor <= self.cap <= 100):
            raise ValueError("Speeds need 0 <= minimum <= maximum <= 100 %")
        if not 1 <= self.slew <= 1000:
            raise ValueError("Ramp limit must be 1-1000 % per second")
        if not 0 <= self.fade_seconds <= 600:
            raise ValueError("Cross-fade time must be 0-600 seconds")
        if not 0 <= self.default_level <= 100:
            raise ValueError("Default level must be between 0 and 100 %")
        if self.follow_relation not in RELATIONS:
            raise ValueError(f"Follow relation must be one of {', '.join(RELATIONS)}")
        if self.follow_constant not in CONSTANT_BEHAVIOURS:
            raise ValueError(f"Constant behaviour must be one of {', '.join(CONSTANT_BEHAVIOURS)}")
        self.follow_all_day = bool(self.follow_all_day)

        blocks = [b if isinstance(b, Block) else Block.from_dict(b) for b in (self.blocks or [])]
        blocks.sort(key=lambda b: b._s)  # noqa: SLF001
        cover = _occupancy([(b._s, b.minutes) for b in blocks])  # noqa: SLF001
        if any(c > 1 for c in cover):
            for i, a in enumerate(blocks):
                for b in blocks[i + 1:]:
                    if any(a.covers(m) and b.covers(m) for m in range(MINUTES_PER_DAY)):
                        raise ValueError(f"Blocks {a.label} and {b.label} overlap")
        self.blocks = blocks

        windows: list[list[str]] = []
        for window in self.follow_windows or []:
            try:
                start, end = parse_hhmm(window[0]), parse_hhmm(window[1])
            except (TypeError, IndexError):
                raise ValueError('Follow windows look like [["HH:MM", "HH:MM"], ...]') from None
            if start == end:
                raise ValueError("A follow window needs different start and end times (or follow all day)")
            windows.append([format_hhmm(start), format_hhmm(end)])
        self.follow_windows = windows
        self._windows = [(parse_hhmm(s), (parse_hhmm(e) - parse_hhmm(s)) % MINUTES_PER_DAY) for s, e in windows]

        if self.follow_source:
            validate_pump_name(self.follow_source)
            if not self.follow_all_day:
                if not self._windows:
                    raise ValueError("Add at least one follow window, or follow all day")
                # "locked" times: own blocks may not cover the times this pump follows another
                for block in self.blocks:
                    for m in range(MINUTES_PER_DAY):
                        if block.covers(m) and self._in_windows(m):
                            raise ValueError(
                                f"Block {block.label} overlaps a time this pump follows "
                                f"{self.follow_source}; those times are locked"
                            )

    # -- queries -------------------------------------------------------------- #
    def _in_windows(self, minute: int) -> bool:
        return any((minute - s) % MINUTES_PER_DAY < length for s, length in self._windows)

    def follows_at(self, minute: int) -> bool:
        """True if, at this minute of the day, the pump follows its source."""
        if not self.follow_source:
            return False
        return self.follow_all_day or self._in_windows(minute)

    def block_at(self, minute: int) -> Block | None:
        return next((b for b in self.blocks if b.covers(minute)), None)

    # -- (de)serialisation ------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PumpConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict(data).items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["blocks"] = [b.to_dict() for b in self.blocks]
        return d


def validate_network(pumps: dict[str, PumpConfig]) -> None:
    """Cross-pump rules: sources exist, nothing follows itself, no cycles, bounded depth."""
    for name, cfg in pumps.items():
        seen, current = [name], cfg
        while current.follow_source:
            source = current.follow_source
            if source not in pumps:
                raise ValueError(f"{name} follows {source!r}, which does not exist")
            if source in seen:
                raise ValueError(f"{name} cannot follow {source}: that would make a loop ({' -> '.join(seen + [source])})")
            seen.append(source)
            current = pumps[source]
            if len(seen) > MAX_FOLLOW_DEPTH:
                raise ValueError(f"Follow chains are limited to {MAX_FOLLOW_DEPTH} pumps")


# --------------------------------------------------------------------------- #
# resolving what a pump should do right now
# --------------------------------------------------------------------------- #
def _default_params(cfg: PumpConfig) -> WaveParams:
    return WaveParams(mode="constant", level=cfg.default_level)


def _own_params(name: str, cfg: PumpConfig, when: datetime) -> WaveParams:
    minute = when.hour * 60 + when.minute
    block = cfg.block_at(minute)
    if block is None:
        return _default_params(cfg)
    start = datetime.combine(when.date(), time(block._s // 60, block._s % 60), tzinfo=when.tzinfo)  # noqa: SLF001
    if start > when:
        start -= timedelta(days=1)  # block began before midnight
    seed = zlib.crc32(f"{name}:{block.start}".encode()) & 0x7FFFFFFF
    return block.params(seed=seed, anchor=int(start.timestamp()))


def _relate(base: WaveParams, cfg: PumpConfig) -> WaveParams:
    """Apply a follow relation to the source's parameters."""
    if cfg.follow_relation != "inverse":
        return base  # slave: identical output
    if base.mode == "constant":
        level = 100.0 - base.level if cfg.follow_constant == "invert" else base.level
        return WaveParams(**{**base.to_dict(), "level": level})
    return WaveParams(**{**base.to_dict(), "invert": not base.invert})


def resolve(pumps: dict[str, PumpConfig], name: str, when: datetime, _depth: int = 0) -> WaveParams:
    """The wave parameters ``name`` should be running at ``when``."""
    cfg = pumps[name]
    minute = when.hour * 60 + when.minute
    if cfg.follows_at(minute):
        if _depth >= MAX_FOLLOW_DEPTH or cfg.follow_source not in pumps:
            return _default_params(cfg)
        return _relate(resolve(pumps, cfg.follow_source, when, _depth + 1), cfg)
    return _own_params(name, cfg, when)


def next_change(pumps: dict[str, PumpConfig], name: str, when: datetime) -> datetime | None:
    """Next minute at which the pump's settings change (within 24 h), if any."""
    def signature(at: datetime) -> tuple:
        p = resolve(pumps, name, at)
        return (p.mode, p.low, p.high, p.level, p.period, p.pulse, p.interval, p.invert, p.seed)

    base = when.replace(second=0, microsecond=0)
    current = signature(base)
    for i in range(1, MINUTES_PER_DAY + 1):
        at = base + timedelta(minutes=i)
        if signature(at) != current:
            return at
    return None


def block_preview(block: Block) -> dict[str, Any]:
    """Waveform samples of one block for the card (a few periods of the shape)."""
    if block.mode == "random":
        duration = min(600.0, block.interval * 12)
    elif block.mode == "constant":
        duration = 10.0
    else:
        duration = min(3600.0, block.period * 3)
    params = block.params(seed=1, anchor=0)
    return {"duration": duration, "values": waveform_samples(params, duration, 240)}
