"""Aquarium Wavemaker - time-scheduled wave modes for BLDC wavemaker pumps."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import api, card_resource
from .const import DOMAIN
from .coordinator import AquariumWavemakerCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]
_GLOBAL_KEY = f"{DOMAIN}_global_setup_done"


def resolve_pump(hass: HomeAssistant, entity_id: str) -> tuple[AquariumWavemakerCoordinator, str]:
    """Find the coordinator and pump an entity of this integration belongs to."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.platform != DOMAIN or entity.config_entry_id not in hass.data.get(DOMAIN, {}):
        raise ServiceValidationError(f"{entity_id} is not an Aquarium Wavemaker entity")
    coordinator: AquariumWavemakerCoordinator = hass.data[DOMAIN][entity.config_entry_id]
    rest = (entity.unique_id or "").removeprefix(f"{entity.config_entry_id}_")
    matches = [p for p in coordinator.pumps if rest.startswith(f"{p}_")]
    if not matches:
        raise ServiceValidationError(f"Could not work out which pump {entity_id} belongs to")
    return coordinator, max(matches, key=len)


async def _async_setup_global(hass: HomeAssistant) -> None:
    """Register the websocket API, service and Lovelace cards once per HA run."""
    if hass.data.get(_GLOBAL_KEY):
        return
    api.async_register(hass)
    await card_resource.async_setup(hass)

    async def _set_wave(call: ServiceCall) -> None:
        data = {k: v for k, v in call.data.items() if k != ATTR_ENTITY_ID}
        for entity_id in call.data[ATTR_ENTITY_ID]:
            coordinator, pump = resolve_pump(hass, entity_id)
            try:
                await coordinator.async_set_wave(pump, data)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err

    number = lambda lo, hi: vol.All(vol.Coerce(float), vol.Range(min=lo, max=hi))  # noqa: E731
    hass.services.async_register(
        DOMAIN, "set_wave", _set_wave,
        vol.Schema({
            vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
            vol.Required("mode"): vol.In(["constant", "wave", "pulse", "surge", "random"]),
            vol.Optional("low"): number(0, 100), vol.Optional("high"): number(0, 100),
            vol.Optional("level"): number(0, 100), vol.Optional("period"): number(1, 3600),
            vol.Optional("pulse"): number(0.1, 3600), vol.Optional("interval"): number(1, 600),
            vol.Optional("fade"): number(0, 600),
        }),
    )
    hass.data[_GLOBAL_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_setup_global(hass)
    coordinator = AquariumWavemakerCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: AquariumWavemakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the dashboard resource when the last entry is deleted."""
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        await card_resource.async_remove_resource(hass)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
