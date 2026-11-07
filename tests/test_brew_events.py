"""Tests for the brewing and needs-attention sensors and the brew event."""

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeBru, status_frame

EVENT = "event.bru_tea_maker_brew_progress"
BREWING = "binary_sensor.bru_tea_maker_brewing"
ATTENTION = "binary_sensor.bru_tea_maker_needs_attention"

STANDBY = (1, 2)
MENU = (2, 4)
STARTING = (5, 2)  # Accepted, before the first phase.
FILLING = (5, 25)
STEEPING = (5, 26)
SERVING = (5, 28)
RINSING = (5, 29)
ENJOY = (5, 30)
CLOSE_LID = (12, 122)
OPEN_LID = (12, 123)
ADD_WATER = (12, 124)
ERROR = (13, 0)


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, bru: FakeBru, status: tuple[int, int]
) -> None:
    bru.status = status_frame(*status)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _run(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    bru: FakeBru,
    *statuses: tuple[int, int],
) -> list[str]:
    """Poll each status in turn and return the events fired."""
    events = []
    last = hass.states.get(EVENT).state
    for status in statuses:
        bru.status = status_frame(*status)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        state = hass.states.get(EVENT)
        if state.state != last:
            events.append(state.attributes["event_type"])
            last = state.state
    return events


async def test_brew_fires_started_and_finished_once(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A whole brew fires one started and one finished event."""
    await _setup(hass, config_entry, fake_bru, MENU)
    assert hass.states.get(EVENT).state == STATE_UNKNOWN
    assert hass.states.get(BREWING).state == STATE_OFF

    events = await _run(hass, config_entry, fake_bru, STARTING, FILLING, STEEPING)
    assert events == ["started"]
    assert hass.states.get(BREWING).state == STATE_ON

    events = await _run(hass, config_entry, fake_bru, SERVING, RINSING, ENJOY, MENU)
    assert events == ["finished"]
    assert hass.states.get(BREWING).state == STATE_OFF


async def test_missed_enjoy_still_finishes(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """ "Done" shows for about 3 s; a brew that skips it after serving finished."""
    await _setup(hass, config_entry, fake_bru, MENU)
    events = await _run(
        hass, config_entry, fake_bru, FILLING, STEEPING, SERVING, RINSING, MENU
    )
    assert events == ["started", "finished"]


async def test_held_back_start(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A start with the lid open starts once the lid is closed (H1)."""
    await _setup(hass, config_entry, fake_bru, MENU)
    events = await _run(hass, config_entry, fake_bru, STARTING, CLOSE_LID)
    assert events == []
    assert hass.states.get(ATTENTION).state == STATE_ON

    events = await _run(hass, config_entry, fake_bru, FILLING, STEEPING, SERVING, ENJOY)
    assert events == ["started", "finished"]
    assert hass.states.get(ATTENTION).state == STATE_OFF


async def test_prompt_during_brew_does_not_end_it(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A prompt in the middle of a brew is not a second start or a finish."""
    await _setup(hass, config_entry, fake_bru, MENU)
    events = await _run(
        hass, config_entry, fake_bru, FILLING, ADD_WATER, FILLING, STEEPING
    )
    assert events == ["started"]


async def test_cancelled_brew_does_not_finish(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A brew stopped before serving fires no finished event."""
    await _setup(hass, config_entry, fake_bru, MENU)
    events = await _run(hass, config_entry, fake_bru, FILLING, STEEPING, MENU)
    assert events == ["started"]
    events = await _run(hass, config_entry, fake_bru, FILLING)
    assert events == ["started"]


async def test_startup_in_enjoy_fires_nothing(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """The state the tea maker is in when Home Assistant starts is not an event."""
    await _setup(hass, config_entry, fake_bru, ENJOY)
    assert await _run(hass, config_entry, fake_bru, ENJOY, MENU) == []


async def test_startup_mid_brew_finishes(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """A brew already running at startup fires finished but not started."""
    await _setup(hass, config_entry, fake_bru, STEEPING)
    assert await _run(hass, config_entry, fake_bru, SERVING, ENJOY) == ["finished"]


async def test_needs_attention(
    hass: HomeAssistant,
    enable_bluetooth: None,
    fake_bru: FakeBru,
    config_entry: MockConfigEntry,
) -> None:
    """Prompts that stop a brew and errors turn needs-attention on."""
    await _setup(hass, config_entry, fake_bru, ADD_WATER)
    assert hass.states.get(ATTENTION).state == STATE_ON
    await _run(hass, config_entry, fake_bru, STANDBY)
    assert hass.states.get(ATTENTION).state == STATE_OFF
    await _run(hass, config_entry, fake_bru, ERROR)
    assert hass.states.get(ATTENTION).state == STATE_ON
    # The reminder after every brew is only a display message.
    await _run(hass, config_entry, fake_bru, OPEN_LID)
    assert hass.states.get(ATTENTION).state == STATE_OFF
