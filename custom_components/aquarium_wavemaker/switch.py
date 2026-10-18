"""switch entities: follow all day, and invert constant levels."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PumpEntity, async_setup_pump_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_pump_entities(
        hass, entry, async_add_entities,
        lambda coord, e, pump: [
            FollowAllDay(coord, e, pump, "follow_all_day", "Follow all day", "mdi:calendar-today"),
            InvertConstant(coord, e, pump, "follow_constant", "Invert constant levels", "mdi:invert-colors"),
        ],
    )


class FollowAllDay(PumpEntity, SwitchEntity):
    """On: follow the source all day. Off: only during the follow windows set in the card."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        cfg = self._cfg
        return None if cfg is None else cfg.follow_all_day

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_change(follow_all_day=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_change(follow_all_day=False)


class InvertConstant(PumpEntity, SwitchEntity):
    """Inverse pumps in constant mode: off = mirror the source's level, on = 100 % minus it."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        cfg = self._cfg
        return None if cfg is None else cfg.follow_constant == "invert"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_change(follow_constant="invert")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_change(follow_constant="mirror")
