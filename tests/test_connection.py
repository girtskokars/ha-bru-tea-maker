"""Tests for noticing disconnects and reconnecting on advertisements."""

import time
from unittest.mock import patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ADDRESS, FakeBru, service_info

STATUS = "sensor.bru_tea_maker"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _advertise(entry: MockConfigEntry) -> None:
    entry.runtime_data.async_on_advertisement(
        service_info(), BluetoothChange.ADVERTISEMENT
    )


async def test_disconnect_is_noticed_at_once(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A dropped link makes the entities unavailable without waiting for a poll."""
    await _setup(hass, config_entry)
    assert hass.states.get(STATUS).state == "standby"
    fake_bru.reachable = False
    fake_bru.drop()
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE
    assert not config_entry.runtime_data.connected


async def test_reconnect_after_disconnect(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """After a drop the coordinator reconnects at once, not at the next poll."""
    await _setup(hass, config_entry)
    connects = len([frame for frame in fake_bru.written if frame[2] == 0x2A])
    fake_bru.drop()
    await hass.async_block_till_done()
    assert config_entry.runtime_data.connected
    assert hass.states.get(STATUS).state == "standby"
    # The presets are read again on every connect.
    assert (
        len([frame for frame in fake_bru.written if frame[2] == 0x2A]) == connects + 1
    )


async def test_old_client_disconnect_is_ignored(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A disconnect reported by a client from before changes nothing."""
    await _setup(hass, config_entry)
    config_entry.runtime_data._on_disconnected(object())
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "standby"
    assert config_entry.runtime_data.connected


async def test_disconnect_during_request(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A link lost mid-request fails the poll at once, not after the timeout."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    fake_bru.drop_on_status = True
    fake_bru.reachable = False
    start = time.monotonic()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert time.monotonic() - start < 1
    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE


async def test_reconnect_on_advertisement(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """An advertisement after a disconnect reconnects without waiting for a poll."""
    await _setup(hass, config_entry)
    fake_bru.reachable = False
    fake_bru.drop()
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE

    fake_bru.reachable = True
    _advertise(config_entry)
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "standby"
    assert config_entry.runtime_data.connected


async def test_advertisement_while_connected(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """While connected, advertisements cause no refresh."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    with patch.object(coordinator, "async_request_refresh") as refresh:
        _advertise(config_entry)
        await hass.async_block_till_done()
    refresh.assert_not_called()


async def test_no_reconnect_after_bond_lost(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Once re-pairing is needed, advertisements start no pairing attempts."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    fake_bru.pair_error = TimeoutError()
    # The reconnect straight after the drop is the first failed attempt.
    fake_bru.drop()
    await hass.async_block_till_done()
    for _ in range(2):
        await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert any(config_entry.async_get_active_flows(hass, {"reauth"}))
    with patch.object(coordinator, "async_request_refresh") as refresh:
        _advertise(config_entry)
        await hass.async_block_till_done()
    refresh.assert_not_called()


async def test_advertisement_callback_registered(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Setup listens for the tea maker's advertisements and stops on unload."""
    with patch(
        "custom_components.bru_tea_maker.bluetooth.async_register_callback"
    ) as register:
        await _setup(hass, config_entry)
    matcher = register.call_args.args[2]
    assert matcher["address"] == ADDRESS
    assert matcher["connectable"]
    unregister = register.return_value
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    unregister.assert_called_once()


async def test_unload_offers_the_tea_maker_again(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """After unloading, the tea maker can be discovered again without a restart."""
    await _setup(hass, config_entry)
    with patch(
        "custom_components.bru_tea_maker.bluetooth.async_rediscover_address"
    ) as rediscover:
        assert await hass.config_entries.async_unload(config_entry.entry_id)
    rediscover.assert_called_once_with(hass, ADDRESS)
