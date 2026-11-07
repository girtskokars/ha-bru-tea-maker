"""Tests for the entities."""

from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from .conftest import FakeBru


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_names_and_main_entity(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Entities are named for what they are; the status is the main entity."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    names = {
        entry.translation_key: hass.states.get(entry.entity_id).name
        for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    assert names == {
        "status": "BRU Tea Maker",
        "message": "BRU Tea Maker Display message",
        "temperature": "BRU Tea Maker Water temperature",
        "water_amount": "BRU Tea Maker Water quantity",
        "brew_time": "BRU Tea Maker Steeping time",
        "preset": "BRU Tea Maker Preset",
        "start_brew": "BRU Tea Maker Brew",
        "sync_presets": "BRU Tea Maker Sync presets",
    }
    assert hass.states.get("sensor.bru_tea_maker").state == "standby"


async def test_custom_preset(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Changing a setting by hand shows "custom" instead of a blank select."""
    await _setup(hass, config_entry)
    select = "select.bru_tea_maker_preset"
    assert hass.states.get(select).attributes["options"] == [
        "GREEN TEA",
        "BLACK TEA",
        "HERBAL TEA",
        "custom",
    ]
    assert hass.states.get(select).state == "custom"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "GREEN TEA"},
        blocking=True,
    )
    assert hass.states.get(select).state == "GREEN TEA"
    assert hass.states.get("number.bru_tea_maker_water_temperature").state == "80"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bru_tea_maker_water_temperature", "value": 85},
        blocking=True,
    )
    assert hass.states.get(select).state == "custom"

    # Choosing "custom" leaves the settings as they are.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "custom"},
        blocking=True,
    )
    assert hass.states.get("number.bru_tea_maker_water_temperature").state == "85"
    assert hass.states.get(select).state == "custom"


async def test_settings_available_while_disconnected(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """The brew settings can be changed while the tea maker is out of range."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    fake_bru.connected = False
    fake_bru.reachable = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.bru_tea_maker").state == STATE_UNAVAILABLE
    number = "number.bru_tea_maker_water_quantity"
    assert hass.states.get(number).state == "250"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 300}, blocking=True
    )
    assert hass.states.get(number).state == "300"


async def test_connect_uses_no_pairing_agent(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Resuming the bond on connect needs no agent, so D-Bus is not touched."""
    with patch("custom_components.bru_tea_maker.pairing.MessageBus") as bus:
        await _setup(hass, config_entry)
    assert fake_bru.paired
    bus.assert_not_called()


async def test_typed_and_restored_values_snap(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """The number shown is always a value the tea maker brews."""
    temperature = "number.bru_tea_maker_water_temperature"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(temperature, "92"),
                {
                    "native_max_value": 100,
                    "native_min_value": 40,
                    "native_step": 5,
                    "native_unit_of_measurement": "°C",
                    "native_value": 92,
                },
            )
        ],
    )
    await _setup(hass, config_entry)
    assert hass.states.get(temperature).state == "90"

    quantity = "number.bru_tea_maker_water_quantity"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": quantity, "value": 275}, blocking=True
    )
    assert hass.states.get(quantity).state == "300"
