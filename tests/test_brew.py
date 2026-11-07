"""Tests for pre-brew checks, translated errors and the brew action."""

import json
from pathlib import Path

import pytest

from custom_components.bru_tea_maker.const import ATTENTION_MESSAGES, DOMAIN, MESSAGES
from custom_components.bru_tea_maker.protocol import OP_START_BREW, build_frame
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ADDRESS, FakeBru

BREW_BUTTON = "button.bru_tea_maker_brew"
TRANSLATIONS = Path(__file__).parents[1] / (
    "custom_components/bru_tea_maker/translations/en.json"
)
CODES = {name: code for code, name in MESSAGES.items()}


def _status(code: int, sub_code: int) -> bytes:
    return build_frame(0x45, bytes([code, sub_code, 0x12, 0x34, 0x56, 0x78]))


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _starts(bru: FakeBru) -> list[bytes]:
    return [frame for frame in bru.written if frame[2] == OP_START_BREW]


def _device_id(hass: HomeAssistant) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS), hass.config_entries.async_entries(DOMAIN)[0].entry_id
    )
    assert device is not None
    return device.id


def test_every_prompt_has_a_message() -> None:
    """Each prompt that blocks a brew has a translated explanation."""
    exceptions = json.loads(TRANSLATIONS.read_text())["exceptions"]
    for message in ATTENTION_MESSAGES:
        assert f"prompt_{message}" in exceptions


@pytest.mark.parametrize(
    ("code", "sub_code", "key"),
    [
        (12, CODES["place_cup"], "prompt_place_cup"),
        (12, CODES["add_water"], "prompt_add_water"),
        (12, CODES["close_lid"], "prompt_close_lid"),
        (5, 26, "busy"),
        (5, 28, "busy"),
        (3, 0, "busy"),
        (22, 0, "busy"),
    ],
)
@pytest.mark.usefixtures("enable_bluetooth")
async def test_brew_blocked(
    hass: HomeAssistant,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
    *,
    code: int,
    sub_code: int,
    key: str,
) -> None:
    """A busy tea maker or one showing a prompt gets no start command."""
    await _setup(hass, config_entry)
    fake_bru.status = _status(code, sub_code)
    await _poll(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": BREW_BUTTON}, blocking=True
        )
    assert err.value.translation_key == key
    assert not _starts(fake_bru)


@pytest.mark.parametrize(
    ("code", "sub_code"), [(1, 2), (2, 4), (12, CODES["open_lid"])]
)
@pytest.mark.usefixtures("enable_bluetooth")
async def test_brew_allowed(
    hass: HomeAssistant,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
    code: int,
    sub_code: int,
) -> None:
    """Standby, the menu and the "Open lid" prompt let a brew start."""
    await _setup(hass, config_entry)
    fake_bru.status = _status(code, sub_code)
    await _poll(hass, config_entry)
    await hass.services.async_call(
        "button", "press", {"entity_id": BREW_BUTTON}, blocking=True
    )
    assert _starts(fake_bru)


async def test_brew_while_disconnected(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Without a connection the button says so."""
    await _setup(hass, config_entry)
    fake_bru.connected = False
    fake_bru.reachable = False
    await _poll(hass, config_entry)
    with pytest.raises(HomeAssistantError) as err:
        await config_entry.runtime_data.async_start_brew()
    assert err.value.translation_key == "not_connected"


async def test_brew_timeout(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A lost connection while starting raises a translated error."""
    await _setup(hass, config_entry)

    async def lost(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError

    fake_bru.write_gatt_char = lost  # type: ignore[method-assign]
    with pytest.raises(HomeAssistantError) as err:
        await config_entry.runtime_data.async_start_brew()
    assert err.value.translation_key == "start_failed"


def _sent_settings(bru: FakeBru) -> tuple[int, int, int]:
    """Decode the last start frame back to °C, ml and s."""
    frame = _starts(bru)[-1]
    temperature, brew_time, water = frame[4:7]
    return temperature * 5 + 40, (water + 1) * 50, (brew_time + 1) * 10


@pytest.mark.parametrize(
    ("data", "sent"),
    [
        ({}, (90, 250, 180)),
        ({"preset": "GREEN TEA"}, (80, 250, 120)),
        ({"preset": "GREEN TEA", "water_amount": 300}, (80, 300, 120)),
        ({"temperature": 92, "brew_time": 185}, (90, 250, 190)),
    ],
)
@pytest.mark.usefixtures("enable_bluetooth")
async def test_brew_action(
    hass: HomeAssistant,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
    data: dict[str, object],
    sent: tuple[int, int, int],
) -> None:
    """The action brews a preset or the settings, with overrides, snapped."""
    await _setup(hass, config_entry)
    before = {
        entity: hass.states.get(entity).state
        for entity in (
            "select.bru_tea_maker_preset",
            "number.bru_tea_maker_water_temperature",
            "number.bru_tea_maker_water_quantity",
            "number.bru_tea_maker_steeping_time",
        )
    }
    await hass.services.async_call(
        DOMAIN, "brew", {"device_id": _device_id(hass), **data}, blocking=True
    )
    assert _sent_settings(fake_bru) == sent
    assert {entity: hass.states.get(entity).state for entity in before} == before


async def test_brew_action_unknown_preset(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """An unknown preset lists the presets the tea maker has."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "brew",
            {"device_id": _device_id(hass), "preset": "Oolong"},
            blocking=True,
        )
    assert err.value.translation_key == "preset_not_found"
    assert err.value.translation_placeholders == {
        "preset": "Oolong",
        "presets": "GREEN TEA, BLACK TEA, HERBAL TEA",
    }
    assert not _starts(fake_bru)


async def test_brew_action_other_device(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A device that is not a loaded tea maker is refused."""
    await _setup(hass, config_entry)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "1")}
    )
    for device_id in (device.id, "missing"):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, "brew", {"device_id": device_id}, blocking=True
            )
        assert err.value.translation_key == "device_not_loaded"
