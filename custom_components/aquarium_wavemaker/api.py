"""Websocket API for the Aquarium Wavemaker Lovelace cards.

Every command takes an optional ``master_entity`` (the aquarium's master toggle) to
say which aquarium you mean; with a single aquarium it can be omitted.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import AquariumWavemakerCoordinator
from .wave_engine import Block, PumpConfig, block_preview, validate_network

_AQ = {vol.Optional("master_entity"): str}
_Conn = websocket_api.ActiveConnection


def _resolve(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> AquariumWavemakerCoordinator | None:
    coordinators: list[AquariumWavemakerCoordinator] = list(hass.data.get(DOMAIN, {}).values())
    wanted = msg.get("master_entity")
    if wanted:
        for coordinator in coordinators:
            if coordinator.master_entity == wanted:
                return coordinator
        connection.send_error(msg["id"], "not_found", f"No Aquarium Wavemaker entry uses {wanted}. Check master_entity in the card config.")
        return None
    if len(coordinators) == 1:
        return coordinators[0]
    if not coordinators:
        connection.send_error(msg["id"], "not_configured", "Aquarium Wavemaker is not set up")
    else:
        connection.send_error(msg["id"], "ambiguous", "Several aquariums are configured; set master_entity in the card config.")
    return None


async def _action(connection: _Conn, msg: dict[str, Any], coordinator: AquariumWavemakerCoordinator, coro) -> None:
    try:
        await coro
    except ValueError as err:
        connection.send_error(msg["id"], "refused", str(err))
        return
    connection.send_result(msg["id"], coordinator.snapshot())


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (ws_get, ws_preview, ws_set_pump, ws_delete_pump, ws_set_wave):
        websocket_api.async_register_command(hass, command)


@websocket_api.websocket_command({vol.Required("type"): "aquarium_wavemaker/get", **_AQ})
@callback
def ws_get(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> None:
    if (coordinator := _resolve(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], coordinator.snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aquarium_wavemaker/preview",
        vol.Required("name"): str,
        vol.Required("config"): dict,
        vol.Optional("index"): int,
        **_AQ,
    }
)
@callback
def ws_preview(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> None:
    """Validate an UNSAVED draft and draw one of its blocks with the real engine."""
    if (coordinator := _resolve(hass, connection, msg)) is None:
        return
    try:
        cfg = PumpConfig.from_dict(msg["config"])
        validate_network({**coordinator.pumps, msg["name"]: cfg})
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_config", str(err))
        return
    result: dict[str, Any] = {"ok": True, "wave": None}
    index = msg.get("index")
    if index is not None and 0 <= index < len(cfg.blocks):
        block: Block = cfg.blocks[index]
        result["wave"] = {**block_preview(block), "low": block.low, "high": block.high, "mode": block.mode, "level": block.level}
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "aquarium_wavemaker/set_pump", vol.Required("name"): str, vol.Required("config"): dict, **_AQ}
)
@websocket_api.async_response
async def ws_set_pump(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> None:
    if (c := _resolve(hass, connection, msg)) is not None:
        await _action(connection, msg, c, c.async_set_pump(msg["name"], msg["config"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "aquarium_wavemaker/delete_pump", vol.Required("name"): str, **_AQ})
@websocket_api.async_response
async def ws_delete_pump(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> None:
    if (c := _resolve(hass, connection, msg)) is not None:
        await _action(connection, msg, c, c.async_delete_pump(msg["name"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "aquarium_wavemaker/set_wave", vol.Required("name"): str, vol.Required("wave"): dict, **_AQ}
)
@websocket_api.async_response
async def ws_set_wave(hass: HomeAssistant, connection: _Conn, msg: dict[str, Any]) -> None:
    if (c := _resolve(hass, connection, msg)) is not None:
        await _action(connection, msg, c, c.async_set_wave(msg["name"], msg["wave"]))
