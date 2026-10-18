"""select entities: which pump a pump follows, and how."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PumpEntity, async_setup_pump_entities
from .wave_engine import RELATIONS

NONE = "none"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_pump_entities(
        hass, entry, async_add_entities,
        lambda coord, e, pump: [
            FollowSource(coord, e, pump, "follow_source", "Follows", "mdi:link-variant"),
            FollowRelation(coord, e, pump, "follow_relation", "Follow relation", "mdi:swap-horizontal"),
        ],
    )


class FollowSource(PumpEntity, SelectEntity):
    """Which other pump this one follows ("none" = its own schedule)."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self) -> list[str]:
        return [NONE, *sorted(n for n in self._coordinator.pumps if n != self._pump)]

    @property
    def current_option(self) -> str | None:
        cfg = self._cfg
        return None if cfg is None else (cfg.follow_source or NONE)

    async def async_select_option(self, option: str) -> None:
        await self._async_change(follow_source="" if option == NONE else option)


class FollowRelation(PumpEntity, SelectEntity):
    """slave = same output as the source; inverse = opposite phase."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(RELATIONS)

    @property
    def current_option(self) -> str | None:
        cfg = self._cfg
        return None if cfg is None else cfg.follow_relation

    async def async_select_option(self, option: str) -> None:
        await self._async_change(follow_relation=option)
