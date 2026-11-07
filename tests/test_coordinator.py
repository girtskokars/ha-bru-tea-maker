"""Tests for starting brews and for lost pairings."""

import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import EXTENDED_START, SHORT_START, FakeBru

BREW_BUTTON = "button.bru_tea_maker_brew"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _start_frames(bru: FakeBru) -> list[int]:
    return [len(frame) for frame in bru.written if frame[2] == 0x40]


async def _brew(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": BREW_BUTTON}, blocking=True
    )


@pytest.mark.parametrize(
    ("accepted", "sent"),
    [
        ({EXTENDED_START}, [EXTENDED_START]),
        ({SHORT_START}, [EXTENDED_START, SHORT_START]),
    ],
    ids=["061026-1", "160626-2"],
)
@pytest.mark.usefixtures("enable_bluetooth")
async def test_brew_on_each_firmware(
    hass: HomeAssistant,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
    accepted: set[int],
    sent: list[int],
) -> None:
    """The extended frame is tried first, then the short one if it is refused."""
    fake_bru.start_frames = accepted
    await _setup(hass, config_entry)
    await _brew(hass)
    assert _start_frames(fake_bru) == sent


async def test_refused_brew_is_an_error(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A start the tea maker refuses in both layouts is reported."""
    fake_bru.start_frames = set()
    await _setup(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="refused"):
        await _brew(hass)


def _reauth_flows(hass: HomeAssistant) -> list[object]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_reauth_after_three_failed_pairings(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Two failed pairings are retried; the third asks to pair again."""
    fake_bru.pair_error = TimeoutError()
    await _setup(hass, config_entry)
    for _ in range(2):
        assert config_entry.state is ConfigEntryState.SETUP_RETRY
        assert not _reauth_flows(hass)
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_successful_pairing_resets_the_count(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Failures only count when they come in a row."""
    fake_bru.pair_error = TimeoutError()
    await _setup(hass, config_entry)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    fake_bru.pair_error = None
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    fake_bru.pair_error = TimeoutError()
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not _reauth_flows(hass)
