"""Integration tests that run in a real Home Assistant (test harness).

    pip install pytest-homeassistant-custom-component     # Python 3.13+
    pytest tests/
"""
import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "aquarium_wavemaker"
MASTER = "input_boolean.aquarium_wavemaker"
ACTION = "esphome.wm_set_wave"


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


class Esp:
    """A fake ESP32 action that records every call."""

    def __init__(self, hass, name="wm_set_wave"):
        self.calls = []
        hass.services.async_register("esphome", name, self._handle)

    async def _handle(self, call):
        self.calls.append(dict(call.data))


async def _setup(hass, *, master_on=True, esp=True):
    fake = Esp(hass) if esp else None
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"aquarium_wavemaker": {}}})
    if master_on:
        await hass.services.async_call("input_boolean", "turn_on", {"entity_id": MASTER}, blocking=True)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MASTER, title="Aquarium Wavemaker (Reef)",
        data={"name": "Reef", "master_entity": MASTER, "initial_pump": "pump_1",
              "initial_service": ACTION, "initial_channel": 0},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN][entry.entry_id], fake


def today(h, m=0, s=0):
    return dt_util.now().replace(hour=h, minute=m, second=s, microsecond=0)


def blk(start, end, mode, **kw):
    return {"start": start, "end": end, "mode": mode, **kw}


# the example schedule from the brief
EXAMPLE = [
    blk("06:00", "16:00", "wave", low=20, high=80, period=10),
    blk("16:00", "21:00", "random", low=10, high=90, interval=10),
    blk("21:00", "21:10", "constant", level=100),
    blk("21:10", "06:00", "surge", low=30, high=60, period=20),
]


async def settle(hass):
    """Let the immediate background push that follows any config change finish."""
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)


async def _until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


# ------------------------------------------------------------------ entities
async def test_entities_are_created_for_a_pump(hass):
    await _setup(hass)
    ids = sorted(e for e in hass.states.async_entity_ids() if "reef_pump_1" in e)
    print("ENTITIES:", ids)
    assert len(ids) == 12
    assert hass.states.get("number.reef_pump_1_minimum_running_speed").state == "10.0"
    assert hass.states.get("number.reef_pump_1_cross_fade_time").state == "60.0"
    assert hass.states.get("select.reef_pump_1_follows").state == "none"
    assert hass.states.get("sensor.reef_pump_1_mode").state == "constant"  # no blocks yet: default level


async def test_entity_edits_are_validated_and_synced(hass):
    entry, coordinator, _ = await _setup(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.reef_pump_1_minimum_running_speed", "value": 15}, blocking=True)
    assert coordinator.pumps["pump_1"].floor == 15
    with pytest.raises(ServiceValidationError, match="minimum <= maximum"):  # cap below the 15 % floor
        await hass.services.async_call("number", "set_value", {"entity_id": "number.reef_pump_1_maximum_speed", "value": 5}, blocking=True)
    assert coordinator.pumps["pump_1"].cap == 100


# -------------------------------------------------------------------- pushes
async def test_example_schedule_is_pushed_with_the_right_settings(hass):
    entry, coordinator, esp = await _setup(hass)
    await coordinator.async_update_pump("pump_1", {"blocks": EXAMPLE})
    await settle(hass)

    async def at(h, m=0, s=0):
        esp.calls.clear()
        await coordinator.async_tick(now=today(h, m, s))
        await hass.async_block_till_done()
        return esp.calls[-1]

    wave = await at(7)
    assert (wave["mode"], wave["low"], wave["high"], wave["period"], wave["channel"]) == ("wave", 20, 80, 10, 0)
    assert wave["anchor"] == int(today(6).timestamp()) and wave["fade"] == 60
    assert (wave["speed_min"], wave["speed_max"], wave["ramp"]) == (10, 100, 100)
    rnd = await at(16, 30)
    assert (rnd["mode"], rnd["low"], rnd["high"], rnd["change_every"]) == ("random", 10, 90, 10)
    assert (await at(21, 5))["level"] == 100 and (await at(21, 5))["mode"] == "constant"
    surge = await at(22)
    assert (surge["mode"], surge["low"], surge["high"], surge["period"]) == ("surge", 30, 60, 20)
    assert surge["anchor"] == int(today(21, 10).timestamp())
    # the block change lands in the very first push of its minute
    assert (await at(16, 0, 1))["mode"] == "random" and (await at(15, 59, 59))["mode"] == "wave"
    # after midnight the surge block still counts from yesterday evening's start
    after_midnight = await at(3)
    assert after_midnight["mode"] == "surge" and after_midnight["anchor"] == int((today(21, 10) - timedelta(days=1)).timestamp())


async def test_pulse_settings_reach_the_pump(hass):
    entry, coordinator, esp = await _setup(hass)
    await coordinator.async_update_pump("pump_1", {"blocks": [blk("00:00", "00:00", "pulse", low=20, high=80, period=15, pulse=7)]})
    await coordinator.async_tick(now=today(12))
    call = esp.calls[-1]
    assert (call["mode"], call["period"], call["pulse"]) == ("pulse", 15, 7)
    with pytest.raises(ValueError, match="Pulse time"):
        await coordinator.async_update_pump("pump_1", {"blocks": [blk("00:00", "00:00", "pulse", period=15, pulse=15)]})


async def test_master_toggle_gates_everything(hass):
    entry, coordinator, esp = await _setup(hass, master_on=False)
    await coordinator.async_update_pump("pump_1", {"blocks": EXAMPLE})
    await asyncio.sleep(0.3)
    assert esp.calls == []  # off: nothing is sent, the pump keeps its last wave
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": MASTER}, blocking=True)
    assert await _until(lambda: esp.calls), "turning the toggle on sends the current settings at once"
    esp.calls.clear()
    await hass.services.async_call("input_boolean", "turn_off", {"entity_id": MASTER}, blocking=True)
    await hass.async_block_till_done()
    await asyncio.sleep(0.3)
    assert esp.calls == []


async def test_an_offline_esp32_is_retried_and_recovers(hass):
    entry, coordinator, _ = await _setup(hass, esp=False)
    await coordinator.async_update_pump("pump_1", {"blocks": EXAMPLE})
    await coordinator.async_tick(now=today(7))  # service missing: logged once, nothing raised
    assert "pump_1" in coordinator._failing
    esp = Esp(hass)  # the device comes online
    await coordinator.async_tick(now=today(7, 1))
    assert esp.calls and "pump_1" not in coordinator._failing


# -------------------------------------------------- slaves and inverse pumps
async def test_slave_and_inverse_pumps_follow_the_schedule(hass):
    entry, coordinator, esp = await _setup(hass)
    await coordinator.async_update_pump("pump_1", {"blocks": EXAMPLE})
    await coordinator.async_set_pump("pump_2", {"service": ACTION, "channel": 1, "follow_source": "pump_1", "follow_relation": "inverse"})
    await coordinator.async_set_pump("pump_3", {"service": ACTION, "channel": 2, "follow_source": "pump_1", "follow_relation": "slave"})
    await settle(hass)
    esp.calls.clear()
    await coordinator.async_tick(now=today(7))
    await hass.async_block_till_done()
    by_channel = {c["channel"]: c for c in esp.calls}
    assert by_channel[0]["invert"] is False and by_channel[2]["invert"] is False
    assert by_channel[1]["invert"] is True  # opposite phase
    for ch in (1, 2):  # same wave, same phase reference and random seed as the source
        for key in ("mode", "low", "high", "period", "seed", "anchor"):
            assert by_channel[ch][key] == by_channel[0][key]
    # constant mode: mirror by default, invert by toggle
    esp.calls.clear()
    await coordinator.async_tick(now=today(21, 5))
    assert {c["channel"]: c["level"] for c in esp.calls} == {0: 100, 1: 100, 2: 100}
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.reef_pump_2_invert_constant_levels"}, blocking=True)
    await settle(hass)
    esp.calls.clear()
    await coordinator.async_tick(now=today(21, 5))
    assert {c["channel"]: c["level"] for c in esp.calls}[1] == 0  # 100 - 100
    assert hass.states.get("select.reef_pump_2_follows").state == "pump_1"


async def test_partial_inversion_locks_the_followed_times(hass):
    entry, coordinator, esp = await _setup(hass)
    await coordinator.async_update_pump("pump_1", {"blocks": EXAMPLE})
    await coordinator.async_set_pump("pump_2", {
        "service": ACTION, "channel": 1, "follow_source": "pump_1", "follow_relation": "inverse",
        "follow_all_day": False, "follow_windows": [["16:00", "21:10"]],
        "blocks": [blk("06:00", "16:00", "constant", level=40), blk("21:10", "06:00", "constant", level=30)],
    })
    await settle(hass)
    esp.calls.clear()
    await coordinator.async_tick(now=today(18))
    inside = [c for c in esp.calls if c["channel"] == 1][0]
    assert inside["mode"] == "random" and inside["invert"] is True  # source's block, inverted
    esp.calls.clear()
    await coordinator.async_tick(now=today(8))
    assert [c for c in esp.calls if c["channel"] == 1][0]["level"] == 40  # its own schedule
    with pytest.raises(ValueError, match="locked"):
        await coordinator.async_update_pump("pump_2", {"blocks": [blk("17:00", "19:00", "constant", level=40)]})
    # editing the follow windows from an entity is validated against the blocks too
    with pytest.raises(ValueError, match="locked"):
        await coordinator.async_update_pump("pump_2", {"follow_windows": [["05:00", "07:00"]]})


async def test_loops_and_dangling_follows_are_refused(hass):
    entry, coordinator, _ = await _setup(hass)
    await coordinator.async_set_pump("pump_2", {"follow_source": "pump_1"})
    with pytest.raises(ValueError, match="loop"):
        await coordinator.async_update_pump("pump_1", {"follow_source": "pump_2"})
    with pytest.raises(ValueError, match="does not exist"):
        await coordinator.async_update_pump("pump_2", {"follow_source": "ghost"})
    with pytest.raises(ValueError, match="follow"):
        await coordinator.async_delete_pump("pump_1")  # pump_2 still follows it
    await hass.services.async_call("select", "select_option", {"entity_id": "select.reef_pump_2_follows", "option": "none"}, blocking=True)
    await coordinator.async_delete_pump("pump_1")
    assert "pump_1" not in coordinator.pumps


# ------------------------------------------------------------ service & websocket
async def test_set_wave_service_sends_a_test_wave(hass):
    entry, coordinator, esp = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "set_wave",
        {"entity_id": "sensor.reef_pump_1_setting", "mode": "pulse", "low": 20, "high": 80, "period": 15, "pulse": 7},
        blocking=True,
    )
    call = esp.calls[-1]
    assert (call["mode"], call["pulse"], call["period"], call["fade"], call["invert"]) == ("pulse", 7, 15, 5, False)
    with pytest.raises(ServiceValidationError, match="Pulse time"):
        await hass.services.async_call(DOMAIN, "set_wave", {"entity_id": "sensor.reef_pump_1_setting", "mode": "pulse", "period": 10, "pulse": 10}, blocking=True)


async def test_pumps_and_schedules_over_websocket(hass, hass_ws_client):
    entry, coordinator, esp = await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "aquarium_wavemaker/set_pump", "name": "pump_2",
                            "config": {"service": ACTION, "channel": 1, "blocks": EXAMPLE, "follow_source": ""}})
    msg = await client.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert hass.states.get("number.reef_pump_2_cross_fade_time") is not None
    assert len(msg["result"]["pumps"]["pump_2"]["config"]["blocks"]) == 4

    await client.send_json({"id": 2, "type": "aquarium_wavemaker/preview", "name": "pump_2",
                            "config": {"blocks": EXAMPLE}, "index": 0})
    wave = (await client.receive_json())["result"]["wave"]
    assert wave["mode"] == "wave" and wave["duration"] == 30 and len(wave["values"]) == 240
    assert min(wave["values"]) == pytest.approx(20, abs=0.1) and max(wave["values"]) == pytest.approx(80, abs=0.1)

    await client.send_json({"id": 3, "type": "aquarium_wavemaker/preview", "name": "pump_2",
                            "config": {"blocks": [blk("06:00", "16:00", "wave"), blk("15:00", "17:00", "wave")]}})
    bad = await client.receive_json()
    assert not bad["success"] and "overlap" in bad["error"]["message"]

    await client.send_json({"id": 4, "type": "aquarium_wavemaker/get"})
    snap = (await client.receive_json())["result"]
    assert snap["master_on"] is True and set(snap["pumps"]) == {"pump_1", "pump_2"}
    assert snap["pumps"]["pump_2"]["current"]["description"]

    await client.send_json({"id": 5, "type": "aquarium_wavemaker/set_wave", "name": "pump_1",
                            "wave": {"mode": "wave", "low": 10, "high": 90, "period": 8, "fade": 2}})
    assert (await client.receive_json())["success"]
    assert esp.calls[-1]["period"] == 8 and esp.calls[-1]["fade"] == 2

    await client.send_json({"id": 6, "type": "aquarium_wavemaker/delete_pump", "name": "pump_2"})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert hass.states.get("number.reef_pump_2_cross_fade_time") is None
    assert hass.states.get("number.reef_pump_1_cross_fade_time") is not None
