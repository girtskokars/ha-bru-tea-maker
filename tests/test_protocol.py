"""Tests for frame encoding and decoding."""

import pytest

from custom_components.bru_tea_maker.const import (
    BREW_PHASES,
    MESSAGE_NONE,
    MESSAGES,
    STATUS_NAMES,
)
from custom_components.bru_tea_maker.protocol import (
    OP_PRESET,
    OP_START_BREW,
    OP_STATUS,
    BruPreset,
    build_frame,
    is_valid_frame,
    parse_preset,
    parse_preset_count,
    parse_status,
    serial_from_name,
    snap_setting,
    start_accepted,
    start_brew_payload,
)

from .conftest import (
    PRESET_REPLIES,
    SETUP_REPLY,
    START_ACCEPTED,
    START_REFUSED,
    STATUS_MENU,
    STATUS_STANDBY,
)


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
        BruPreset(1, "GREEN TEA", temperature=80, water_amount=250, brew_time=120),
        BruPreset(2, "BLACK TEA", temperature=95, water_amount=250, brew_time=300),
        BruPreset(3, "HERBAL TEA", temperature=90, water_amount=250, brew_time=300),
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


def test_parse_longer_preset() -> None:
    """Firmware 061026-1 sends four 0xFF bytes after the name field."""
    frame = PRESET_REPLIES[1]
    longer = build_frame(OP_PRESET + 1, frame[3:-1] + b"\xff" * 4)
    assert len(longer) == 60
    assert parse_preset(longer) == parse_preset(frame)


def test_parse_short_preset() -> None:
    """A preset reply shorter than 56 bytes is rejected."""
    with pytest.raises(ValueError):
        parse_preset(PRESET_REPLIES[1][:40])


def test_extended_start_payload() -> None:
    """The extended frame adds the initiator, name and recipe fields."""
    short = start_brew_payload(95, 250, 300)
    extended = start_brew_payload(95, 250, 300, name="Home Assistant")
    assert len(build_frame(OP_START_BREW, short)) == 15
    assert len(build_frame(OP_START_BREW, extended)) == 75
    assert extended[: len(short)] == short
    assert extended[11:19] == b"\xff" * 8
    assert extended[19:67] == "Home Assistant".encode("utf-16-le").ljust(48, b"\0")
    assert extended[67:] == b"\xff" * 4


def test_start_accepted() -> None:
    """The start reply payload is 0 when BRU accepts and 1 when it refuses."""
    assert start_accepted(START_ACCEPTED)
    assert not start_accepted(START_REFUSED)


@pytest.mark.parametrize(
    ("key", "value", "snapped"),
    [
        ("temperature", 92, 90),
        ("temperature", 93, 95),
        ("temperature", 39.9, 40),
        ("temperature", 101, 100),
        ("water_amount", 275, 300),
        ("water_amount", 260, 250),
        ("brew_time", 185, 190),
        ("brew_time", 600, 600),
    ],
)
def test_snap_setting(key: str, value: float, snapped: int) -> None:
    """Values snap to the nearest step the tea maker accepts."""
    assert snap_setting(key, value) == snapped


@pytest.mark.parametrize("temperature", range(40, 101, 5))
def test_start_payload_round_trips_settings(temperature: int) -> None:
    """Every accepted temperature is sent as exactly that temperature."""
    payload = start_brew_payload(temperature, 500, 600)
    assert payload[1] * 5 + 40 == temperature
    assert (payload[2] + 1) * 10 == 600
    assert (payload[3] + 1) * 50 == 500


@pytest.mark.parametrize(
    ("name", "serial"),
    [
        ("BRU.M2-1422E73F", "1422E73F"),
        ("BRU.M2", None),
        ("BRU.M2-", None),
        ("BRU_U", None),
        ("F4:14:14:22:E7:3F", None),
        (None, None),
    ],
)
def test_serial_from_name(name: str | None, serial: str | None) -> None:
    """The serial is the Bluetooth name's suffix, when it has one."""
    assert serial_from_name(name) == serial
