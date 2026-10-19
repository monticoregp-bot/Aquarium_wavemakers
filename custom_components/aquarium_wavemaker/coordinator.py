"""Pushes the right wave settings to each pump's ESP32, once a minute.

Home Assistant only decides WHICH wave a pump should run (time blocks, slaves,
inverse pumps). The ESP32 draws the waveform itself at 50 Hz (``wave_engine.h``),
cross-fades when settings change, and keeps running if Home Assistant or Wi-Fi go
away. So this module sends parameters, never levels.

* Every tick (top of each minute, and on any change) each pump's settings are
  resolved from schedule + clock and sent with one ESPHome action call. The ESP32
  ignores settings identical to what it already runs, so resending every minute
  costs nothing and lets a rebooted ESP32 recover within a minute.
* A new block therefore reaches the pump within a second of its start minute and
  the ESP32 cross-fades into it.
* While the master toggle is off nothing is sent and the ESP32 keeps its last wave.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import datetime
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EVENT_HOMEASSISTANT_STARTED, STATE_ON
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_INITIAL_CHANNEL,
    CONF_INITIAL_PUMP,
    CONF_INITIAL_SERVICE,
    CONF_MASTER_ENTITY,
    DEFAULT_MASTER_ENTITY,
    DOMAIN,
    STORAGE_VERSION,
)
from .wave_engine import (
    Block,
    PumpConfig,
    WaveParams,
    describe,
    display_name,
    next_change,
    resolve,
    validate_network,
    validate_pump_name,
)

_LOGGER = logging.getLogger(__name__)


def available_actions(hass: HomeAssistant) -> list[str]:
    """ESPHome actions ending in `_set_wave`, as "esphome.<device>_set_wave"."""
    return sorted(f"esphome.{n}" for n in hass.services.async_services_for_domain("esphome") if n.endswith("_set_wave"))


class AquariumWavemakerCoordinator:
    """Owns the pump configs and the once-a-minute push loop."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.pumps: dict[str, PumpConfig] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._auto_event = asyncio.Event()  # set <=> the schedule drives the pumps
        self._kick = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._failing: set[str] = set()

    # ------------------------------------------------------------------ #
    # options
    # ------------------------------------------------------------------ #
    @property
    def _options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def tank_name(self) -> str:
        return self._options.get(CONF_NAME) or "Aquarium"

    @property
    def master_entity(self) -> str:
        return self._options.get(CONF_MASTER_ENTITY) or DEFAULT_MASTER_ENTITY

    @property
    def auto_enabled(self) -> bool:
        return self._auto_event.is_set()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            data = {"pumps": {}}
            name = self._options.get(CONF_INITIAL_PUMP)
            if name:
                data["pumps"][name] = PumpConfig(
                    service=self._options.get(CONF_INITIAL_SERVICE, ""),
                    channel=int(self._options.get(CONF_INITIAL_CHANNEL, 0)),
                ).to_dict()
            await self._store.async_save(data)
        for name, raw in data.get("pumps", {}).items():
            try:
                self.pumps[name] = PumpConfig.from_dict(raw)
            except ValueError as err:
                _LOGGER.error("Ignoring invalid configuration for pump %s: %s", name, err)

    async def _async_save(self) -> None:
        await self._store.async_save({"pumps": {n: c.to_dict() for n, c in self.pumps.items()}})

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_state_change_event(self.hass, [self.master_entity], self._handle_master)
        self._sync_master()
        self._task = self.entry.async_create_background_task(self.hass, self._run(), f"{DOMAIN}_loop")

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ------------------------------------------------------------------ #
    # listeners
    # ------------------------------------------------------------------ #
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(cb)

        return _remove

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------------ #
    # master toggle
    # ------------------------------------------------------------------ #
    @callback
    def _handle_master(self, event: Event) -> None:
        new = event.data.get("new_state")
        self._set_auto(new is not None and new.state == STATE_ON)

    @callback
    def _sync_master(self) -> None:
        state = self.hass.states.get(self.master_entity)
        self._set_auto(state is not None and state.state == STATE_ON)

    @callback
    def _set_auto(self, enabled: bool) -> None:
        if enabled == self._auto_event.is_set():
            return
        if enabled:
            self._auto_event.set()
            self._kick.set()  # send the current settings straight away
        else:
            self._auto_event.clear()  # nothing is sent; pumps keep their last wave
        self._notify()

    # ------------------------------------------------------------------ #
    # configuration changes
    # ------------------------------------------------------------------ #
    def _pump(self, name: str) -> PumpConfig:
        cfg = self.pumps.get(name)
        if cfg is None:
            raise ValueError(f"Unknown pump {name!r}")
        return cfg

    async def async_set_pump(self, name: str, data: dict[str, Any]) -> None:
        validate_pump_name(name)
        new = PumpConfig.from_dict(data)  # may raise ValueError
        validate_network({**self.pumps, name: new})  # unique names, sources exist, no loops
        old = self.pumps.get(name)
        self.pumps[name] = new
        await self._async_save()
        if old is not None and old.label != new.label:
            self._rename_device(name)  # entity ids stay; names shown everywhere follow the label
        self._kick.set()
        self._notify()

    def pump_title(self, name: str) -> str:
        """The name people see for a pump (its label, else its id made readable)."""
        return display_name(name, self.pumps[name].label if name in self.pumps else "")

    @callback
    def _rename_device(self, name: str) -> None:
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get_device(identifiers={(DOMAIN, f"{self.entry.entry_id}_{name}")})
        if device:
            dev_reg.async_update_device(device.id, name=f"{self.tank_name} {self.pump_title(name)}")

    async def async_update_pump(self, name: str, changes: dict[str, Any]) -> None:
        cfg = self._pump(name)
        await self.async_set_pump(name, {**cfg.to_dict(), **changes})

    async def async_delete_pump(self, name: str) -> None:
        self._pump(name)
        followers = sorted(n for n, c in self.pumps.items() if c.follow_source == name and n != name)
        if followers:
            raise ValueError(f"{', '.join(followers)} follow(s) {name}; change that first")
        del self.pumps[name]
        await self._async_save()
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get_device(identifiers={(DOMAIN, f"{self.entry.entry_id}_{name}")})
        if device:
            dev_reg.async_remove_device(device.id)  # also removes its entities
        self._notify()

    # ------------------------------------------------------------------ #
    # derived state
    # ------------------------------------------------------------------ #
    def live(self, name: str, now: datetime | None = None) -> WaveParams:
        """What the pump should be running now (derived, never stored)."""
        return resolve(self.pumps, name, now or dt_util.now())

    def next_change(self, name: str, now: datetime | None = None) -> datetime | None:
        return next_change(self.pumps, name, now or dt_util.now())

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or dt_util.now()
        minute = now.hour * 60 + now.minute
        pumps: dict[str, Any] = {}
        for name, cfg in self.pumps.items():
            params = self.live(name, now)
            change = self.next_change(name, now)
            pumps[name] = {
                "label": display_name(name, cfg.label),
                "config": cfg.to_dict(),
                "current": {**params.to_dict(), "description": describe(params)},
                "following": cfg.follow_source if cfg.follows_at(minute) else "",
                "followers": sorted(n for n, c in self.pumps.items() if c.follow_source == name),
                "next_change": change.isoformat() if change else None,
            }
        return {
            "name": self.tank_name,
            "master_entity": self.master_entity,
            "master_on": self.auto_enabled,
            "actions": self.available_actions(),
            "now": now.isoformat(),
            "now_minute": now.hour * 60 + now.minute + now.second / 60,
            "pumps": pumps,
        }

    def available_actions(self) -> list[str]:
        """ESPHome actions that look like this integration's `set_wave` (for pickers)."""
        return available_actions(self.hass)

    # ------------------------------------------------------------------ #
    # the push loop
    # ------------------------------------------------------------------ #
    async def _wait_for_start(self) -> None:
        if self.hass.state is CoreState.running:
            return
        started = asyncio.Event()
        unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _e: started.set())
        try:
            await started.wait()
        finally:
            unsub()

    async def _run(self) -> None:
        await self._wait_for_start()
        self._sync_master()
        if self.hass.states.get(self.master_entity) is None:
            _LOGGER.warning(
                "Master toggle %s does not exist. Create a Toggle helper with that "
                "entity id; nothing is sent to the pumps until it exists and is on",
                self.master_entity,
            )
        while True:
            await self._auto_event.wait()  # blocks completely while the master toggle is off
            self._kick.clear()
            try:
                await self.async_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must never die
                _LOGGER.exception("Unexpected error while sending wave settings")
            now = dt_util.utcnow()
            delay = 60.0 - now.second - now.microsecond / 1e6 + 0.25  # next :00
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._kick.wait(), timeout=delay)

    async def async_tick(self, now: datetime | None = None) -> None:
        """Send every pump's current settings. ``now`` is injectable for tests."""
        now = now or dt_util.now()
        names = [n for n, c in self.pumps.items() if c.service]
        results = await asyncio.gather(
            *(self._push(n, self.pumps[n], self.live(n, now), self.pumps[n].fade_seconds) for n in names),
            return_exceptions=True,
        )
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                if name not in self._failing:  # warn once, not every minute
                    _LOGGER.warning("Pump %s: could not send wave settings: %s", name, result)
                self._failing.add(name)
            elif name in self._failing:
                _LOGGER.info("Pump %s: recovered", name)
                self._failing.discard(name)
        self._notify()

    async def _push(self, name: str, cfg: PumpConfig, params: WaveParams, fade: float) -> None:
        domain, _, service = cfg.service.partition(".")
        if not self.hass.services.has_service(domain, service):
            raise HomeAssistantError(f"{cfg.service} not found (is the ESP32 online?)")
        data = {
            "channel": cfg.channel,
            "mode": params.mode,
            "low": params.low,
            "high": params.high,
            "level": params.level,
            "period": params.period,
            "pulse": params.pulse,
            "change_every": params.interval,  # ESPHome reserves the name "interval"
            "seed": params.seed,
            "anchor": params.anchor,
            "invert": params.invert,
            "fade": fade,
            "speed_min": cfg.floor,
            "speed_max": cfg.cap,
            "ramp": cfg.slew,
        }
        await self.hass.services.async_call(domain, service, data, blocking=False)

    async def async_set_wave(self, name: str, data: dict[str, Any]) -> None:
        """Send a wave straight to a pump for testing. Not saved."""
        cfg = self._pump(name)
        if not cfg.service:
            raise ValueError("This pump has no ESPHome action configured")
        fields = {k: data[k] for k in ("mode", "low", "high", "level", "period", "pulse", "interval") if k in data}
        block = Block(**fields)  # validates ranges and "pulse time below the period"
        now = dt_util.now()
        params = block.params(seed=random.getrandbits(31), anchor=int(now.timestamp()))
        try:
            await self._push(name, cfg, params, float(data.get("fade", 5)))
        except HomeAssistantError as err:
            raise ValueError(str(err)) from err
