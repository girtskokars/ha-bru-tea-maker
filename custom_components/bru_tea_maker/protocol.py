"""Frame encoding and decoding for the BRU tea maker.

Every command and notification is ``FF <length> <opcode> <payload>
<checksum>``, where ``length`` counts the whole frame. BRU answers a command
with a frame whose opcode is the command's plus one.
"""

from dataclasses import dataclass

from .const import (
    BREW_PHASES,
    MESSAGE_NONE,
    MESSAGES,
    STATUS_BREWING,
    STATUS_NAMES,
    STATUS_STANDBY,
)

OP_SETUP = 0x2A
OP_START_BREW = 0x40
OP_STATUS = 0x44
OP_PRESET = 0x50

_STATUS_FRAME_LENGTH = 10
_SETUP_FRAME_LENGTH = 8
_PRESET_FRAME_LENGTH = 56
_PRESET_NAME_LENGTH = 48
_PRESET_SLOTS = 10
_START_REPLY_FRAME_LENGTH = 5


@dataclass(frozen=True)
class BruStatus:
    """What the tea maker is doing and the prompt on its screen."""

    status: str | None
    message: str
    awake: bool


@dataclass(frozen=True)
class BruPreset:
    """One of the presets stored on the tea maker."""

    name: str
    temperature: int
    water_amount: int
    brew_time: int


def _checksum(data: bytes) -> int:
    return (-sum(data) ^ 0x3A) & 0xFF


def build_frame(opcode: int, payload: bytes = b"") -> bytes:
    """Build a command frame."""
    frame = bytes([0xFF, len(payload) + 4, opcode]) + payload
    return frame + bytes([_checksum(frame)])


def is_valid_frame(frame: bytes) -> bool:
    """Return whether a notification is a complete, intact frame."""
    return (
        len(frame) >= 4
        and frame[0] == 0xFF
        and frame[1] == len(frame)
        and frame[-1] == _checksum(frame[:-1])
    )


def _payload(frame: bytes, length: int, *, exact: bool = True) -> bytes:
    too_short = len(frame) < length
    if too_short or (exact and len(frame) != length):
        raise ValueError(f"Unexpected reply: {frame.hex()}")
    return frame[3:-1]


def start_brew_payload(
    temperature: int, water_amount: int, brew_time: int, name: str | None = None
) -> bytes:
    """Encode a brew of ``temperature`` °C, ``water_amount`` ml, ``brew_time`` s.

    The BRU app follows the parameters with an 8-byte initiator field, the
    program name (48 bytes, UTF-16) and a 4-byte recipe field. A brew started
    on the machine itself leaves the initiator all 0xFF, and a brew without a
    recipe leaves the recipe all 0xFF. Without a ``name`` only the parameters
    are sent, which is the layout older firmware takes.
    """
    parameters = bytes(
        [
            0,
            (temperature - 40) // 5,
            brew_time // 10 - 1,
            water_amount // 50 - 1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ]
    )
    if name is None:
        return parameters
    name_field = name.encode("utf-16-le")[:_PRESET_NAME_LENGTH]
    name_field = name_field.ljust(_PRESET_NAME_LENGTH, b"\0")
    return parameters + b"\xff" * 8 + name_field + b"\xff" * 4


def start_accepted(frame: bytes) -> bool:
    """Return whether BRU accepted a start command: its reply payload is 0."""
    return _payload(frame, _START_REPLY_FRAME_LENGTH)[0] == 0


def parse_status(frame: bytes) -> BruStatus:
    """Decode the reply to OP_STATUS."""
    code, sub_code = _payload(frame, _STATUS_FRAME_LENGTH)[:2]
    status = STATUS_NAMES.get(code)
    if code == STATUS_BREWING:
        status = BREW_PHASES.get(sub_code, status)
    return BruStatus(
        status=status,
        message=MESSAGES.get(sub_code, MESSAGE_NONE),
        awake=code != STATUS_STANDBY,
    )


def parse_preset_count(frame: bytes) -> int:
    """Decode how many presets the tea maker shows from the reply to OP_SETUP."""
    count = _payload(frame, _SETUP_FRAME_LENGTH)[3]
    if not 1 <= count <= _PRESET_SLOTS:
        raise ValueError(f"Unexpected reply: {frame.hex()}")
    return count


def parse_preset(frame: bytes) -> BruPreset:
    """Decode the reply to OP_PRESET.

    Some firmware sends four extra bytes after the name field, so a longer
    frame is accepted and only the name field is read.
    """
    slot, brew_time, temperature, water_amount, *name = _payload(
        frame, _PRESET_FRAME_LENGTH, exact=False
    )
    text = (
        bytes(name[:_PRESET_NAME_LENGTH])
        .decode("utf-16-le", errors="replace")
        .split("\0", 1)[0]
        .strip()
    )
    return BruPreset(
        name=text or f"Preset {slot}",
        temperature=temperature * 5 + 40,
        water_amount=(water_amount + 1) * 50,
        brew_time=(brew_time + 1) * 10,
    )
