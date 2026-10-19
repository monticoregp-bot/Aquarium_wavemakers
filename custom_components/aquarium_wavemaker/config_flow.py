"""Config flow: aquarium name and master toggle, then the first pump.

More pumps and all schedule editing are done from the Lovelace card.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .coordinator import available_actions
from .const import (
    CONF_INITIAL_CHANNEL,
    CONF_INITIAL_PUMP,
    CONF_INITIAL_SERVICE,
    CONF_MASTER_ENTITY,
    DEFAULT_MASTER_ENTITY,
    DOMAIN,
)
from .wave_engine import MAX_CHANNEL, SERVICE_RE, validate_pump_name


class AquariumWavemakerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._settings: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_MASTER_ENTITY])
            self._abort_if_unique_id_configured()
            self._settings = user_input
            return await self.async_step_pump()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Aquarium"): TextSelector(),
                vol.Required(CONF_MASTER_ENTITY): EntitySelector(EntitySelectorConfig(domain="input_boolean")),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_pump(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["name"].strip().lower().replace(" ", "_")
            service = (user_input.get("service") or "").strip()
            try:
                validate_pump_name(name)
            except ValueError:
                errors["name"] = "invalid_name"
            if service and not SERVICE_RE.match(service):
                errors["service"] = "invalid_service"
            if not errors:
                return self.async_create_entry(
                    title=f"Aquarium Wavemaker ({self._settings.get(CONF_NAME, 'Aquarium')})",
                    data={
                        **self._settings,
                        CONF_INITIAL_PUMP: name,
                        CONF_INITIAL_SERVICE: service,
                        CONF_INITIAL_CHANNEL: int(user_input.get("channel", 0)),
                    },
                )
        # Offer the wavemaker actions Home Assistant can see right now. Typing another
        # name is still allowed (for example if the ESP32 is offline at the moment).
        found = available_actions(self.hass)
        service_selector = (
            SelectSelector(SelectSelectorConfig(options=found, custom_value=True, mode=SelectSelectorMode.DROPDOWN))
            if found
            else TextSelector()
        )
        schema = vol.Schema(
            {
                vol.Required("name", default="pump_1"): TextSelector(),
                vol.Optional("service", default=found[0] if len(found) == 1 else vol.UNDEFINED): service_selector,
                vol.Optional("channel", default=0): NumberSelector(
                    NumberSelectorConfig(min=0, max=MAX_CHANNEL, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id="pump",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "found": (
                    "Found on your system: " + ", ".join(found) + "."
                    if found
                    else "None found yet. Flash the example wavemaker.yaml (or check the ESP32 is online), "
                    "or type the action name, for example esphome.wavemaker_set_wave."
                )
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AquariumWavemakerOptionsFlow()


class AquariumWavemakerOptionsFlow(config_entries.OptionsFlow):
    """Change the master toggle. Reloads the entry on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_MASTER_ENTITY, default=current.get(CONF_MASTER_ENTITY) or DEFAULT_MASTER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="input_boolean")
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
