"""sensor entities: what each pump is doing now, and when that next changes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PumpEntity, async_setup_pump_entities
from .wave_engine import MODES, describe


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_pump_entities(
        hass, entry, async_add_entities,
        lambda coord, e, pump: [
            ScheduledMode(coord, e, pump, "mode", "Mode", "mdi:sine-wave"),
            ScheduledSetting(coord, e, pump, "setting", "Setting", "mdi:waves"),
            NextChange(coord, e, pump, "next_change", "Next change", "mdi:clock-outline"),
        ],
    )


class ScheduledMode(PumpEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODES)

    @property
    def native_value(self) -> str | None:
        return self._coordinator.live(self._pump).mode if self._cfg else None


class ScheduledSetting(PumpEntity, SensorEntity):
    """What the schedule asks of this pump now, for example "wave 20-80%, 10 s"."""

    @property
    def native_value(self) -> str | None:
        return describe(self._coordinator.live(self._pump)) if self._cfg else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cfg = self._cfg
        if cfg is None:
            return {}
        minute = self._coordinator.snapshot()["now_minute"] // 1
        return {
            **self._coordinator.live(self._pump).to_dict(),
            "following": cfg.follow_source if cfg.follows_at(int(minute)) else None,
            "master_on": self._coordinator.auto_enabled,
        }


class NextChange(PumpEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self._coordinator.next_change(self._pump) if self._cfg else None
