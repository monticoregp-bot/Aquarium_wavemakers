"""number entities: motor protection and timing settings of each pump."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PumpEntity, async_setup_pump_entities

# key, name, icon, min, max, step, unit
_NUMBERS = (
    ("floor", "Minimum running speed", "mdi:speedometer-slow", 0, 100, 1, PERCENTAGE),
    ("cap", "Maximum speed", "mdi:speedometer", 0, 100, 1, PERCENTAGE),
    ("slew", "Speed change limit", "mdi:slope-uphill", 1, 1000, 1, "%/s"),
    ("default_level", "Default level", "mdi:water-percent", 0, 100, 1, PERCENTAGE),
    ("fade_seconds", "Cross-fade time", "mdi:transition", 0, 600, 1, "s"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_pump_entities(
        hass, entry, async_add_entities,
        lambda coord, e, pump: [PumpNumber(coord, e, pump, *spec) for spec in _NUMBERS],
    )


class PumpNumber(PumpEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry, pump, key, name, icon, low, high, step, unit) -> None:
        super().__init__(coordinator, entry, pump, key, name, icon)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | None:
        cfg = self._cfg
        return None if cfg is None else float(getattr(cfg, self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self._async_change(**{self._key: value})
