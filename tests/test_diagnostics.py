"""Tests for the diagnostics and device information."""

import json
from types import MappingProxyType

from custom_components.bru_tea_maker.const import DOMAIN
from custom_components.bru_tea_maker.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.discovery_flow import DiscoveryKey
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ADDRESS, FakeBru


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_diagnostics(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics hold the firmware and frames, but no address."""
    hass.config_entries.async_update_entry(
        config_entry,
        discovery_keys=MappingProxyType(
            {"bluetooth": (DiscoveryKey(domain="bluetooth", key=ADDRESS, version=1),)}
        ),
    )
    await _setup(hass, config_entry)
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(result)
    assert ADDRESS not in text
    assert ADDRESS.replace(":", "") not in text.upper()
    assert result["firmware"] == "061026-1"
    assert result["status"]["status"] == "standby"
    # The preset reads are kept, the repeating status polls are not.
    frames = [frame.split()[-1] for frame in result["frames"]]
    assert "ff05500191" in frames
    assert "ff044483" not in frames
    assert len(result["presets"]) == 3


async def test_firmware_on_device_page(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """The device shows the firmware and hardware versions, and follows updates."""
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, ADDRESS), config_entry.entry_id
    )
    assert device is not None
    assert (device.sw_version, device.hw_version) == ("061026-1", "V1.0.1")

    fake_bru.device_information = {}
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    device = registry.async_get_device_by_identifier(
        (DOMAIN, ADDRESS), config_entry.entry_id
    )
    assert device is not None
    assert device.sw_version is None
