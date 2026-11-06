"""Tests for frame encoding and decoding."""

import pytest

from custom_components.bru_tea_maker.const import (
    BREW_PHASES,
    MESSAGE_NONE,
    MESSAGES,
    STATUS_NAMES,
)
from custom_components.bru_tea_maker.protocol import (
    OP_START_BREW,
    OP_STATUS,
    BruPreset,
    build_frame,
    is_valid_frame,
    parse_preset,
    parse_preset_count,
    parse_status,
    start_brew_payload,
)

from .conftest import PRESET_REPLIES, SETUP_REPLY, STATUS_MENU, STATUS_STANDBY


def test_build_frame_matches_captured_commands() -> None:
    """Frames are byte for byte what the tea maker was sent."""
    assert build_frame(OP_STATUS).hex() == "ff044483"
    assert (
        build_frame(OP_START_BREW, start_brew_payload(95, 250, 300)).hex()
        == "ff0f40000b1d0400000001000000bf"
    )


@pytest.mark.parametrize(
    "frame",
    [STATUS_STANDBY, STATUS_MENU, SETUP_REPLY, *PRESET_REPLIES.values()],
)
def test_captured_replies_are_valid(frame: bytes) -> None:
    """Every reply captured from the tea maker passes the frame check."""
    assert is_valid_frame(frame)


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        bytes.fromhex("ff04"),
        bytes.fromhex("fe0a45010212345678a1"),  # wrong start byte
        bytes.fromhex("ff0b45010212345678a1"),  # wrong length
        bytes.fromhex("ff0a45010212345678a2"),  # wrong checksum
    ],
)
def test_invalid_frames(frame: bytes) -> None:
    """Truncated or damaged notifications are ignored."""
    assert not is_valid_frame(frame)


def test_parse_status_standby_and_menu() -> None:
    """Standby is asleep; the menu is awake."""
    assert parse_status(STATUS_STANDBY).status == "standby"
    assert not parse_status(STATUS_STANDBY).awake
    assert parse_status(STATUS_MENU).status == "function_menu"
    assert parse_status(STATUS_MENU).awake


def _status(code: int, sub_code: int) -> bytes:
    return build_frame(0x45, bytes([code, sub_code, 0x12, 0x34, 0x56, 0x78]))


@pytest.mark.parametrize(("code", "name"), STATUS_NAMES.items())
def test_parse_status_codes(code: int, name: str) -> None:
    """Every known status code is decoded."""
    if code == 5:
        name = "brewing"
    assert parse_status(_status(code, 0)).status == name


@pytest.mark.parametrize(("sub_code", "phase"), BREW_PHASES.items())
def test_parse_brew_phases(sub_code: int, phase: str) -> None:
    """While brewing, the sub-code names the phase."""
    assert parse_status(_status(5, sub_code)).status == phase


def test_parse_unknown_brew_phase() -> None:
    """An unknown sub-code while brewing still reads as brewing."""
    # 0xe3 shows for about a second before filling.
    assert parse_status(_status(5, 0xE3)).status == "brewing"


@pytest.mark.parametrize(("sub_code", "message"), MESSAGES.items())
def test_parse_messages(sub_code: int, message: str) -> None:
    """Prompts on the screen are decoded."""
    assert parse_status(_status(12, sub_code)).message == message


def test_parse_no_message() -> None:
    """Without a prompt the message is none."""
    assert parse_status(STATUS_STANDBY).message == MESSAGE_NONE


def test_parse_status_wrong_length() -> None:
    """A status reply of the wrong length is rejected."""
    with pytest.raises(ValueError):
        parse_status(SETUP_REPLY)


def test_parse_preset_count() -> None:
    """The setup reply holds the number of presets."""
    assert parse_preset_count(SETUP_REPLY) == 3


def test_parse_presets() -> None:
    """The captured presets decode to what the tea maker shows."""
    assert [parse_preset(frame) for frame in PRESET_REPLIES.values()] == [
        BruPreset("GREEN TEA", temperature=80, water_amount=250, brew_time=120),
        BruPreset("BLACK TEA", temperature=95, water_amount=250, brew_time=300),
        BruPreset("HERBAL TEA", temperature=90, water_amount=250, brew_time=300),
    ]


@pytest.mark.parametrize("frame", PRESET_REPLIES.values())
def test_start_payload_round_trips_presets(frame: bytes) -> None:
    """Brewing a preset sends exactly the preset's codes."""
    preset = parse_preset(frame)
    payload = start_brew_payload(
        preset.temperature, preset.water_amount, preset.brew_time
    )
    _slot, brew_time, temperature, water_amount = frame[3:7]
    assert payload[1:4] == bytes([temperature, brew_time, water_amount])
