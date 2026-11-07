"""Fixtures for the BRU Tea Maker tests."""

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
import pytest

from custom_components.bru_tea_maker.const import DOMAIN
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ADDRESS = "F4:14:14:22:E7:3F"
NAME = "BRU.M2-1422E73F"

# Replies captured from a BRU Maker One on its original firmware.
STATUS_STANDBY = bytes.fromhex("ff0a45010212345678a1")
STATUS_MENU = bytes.fromhex("ff0a45020412345678a2")
SETUP_REPLY = bytes.fromhex("ff082b00010003f0")
PRESET_REPLIES = {
    1: bytes.fromhex(
        "ff3851010b080447005200450045004e002000540045004100000000"
        "000000000000000000000000000000000000000000000000000000cf"
    ),
    2: bytes.fromhex(
        "ff3851021d0b0442004c00410043004b002000540045004100000000"
        "000000000000000000000000000000000000000000000000000000c9"
    ),
    3: bytes.fromhex(
        "ff3851031d0a04480045005200420041004c00200054004500410000"
        "00000000000000000000000000000000000000000000000000000098"
    ),
}
START_ACCEPTED = bytes.fromhex("ff05410081")
START_REFUSED = bytes.fromhex("ff05410180")
# Start frame lengths: 160626-2 takes only the short frame, 061026-1 only
# the extended one.
SHORT_START = 15
EXTENDED_START = 75


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


def ble_device() -> BLEDevice:
    """Return the tea maker as bleak sees it."""
    return BLEDevice(ADDRESS, NAME, {})


def service_info() -> BluetoothServiceInfoBleak:
    """Return an advertisement from the tea maker."""
    return BluetoothServiceInfoBleak(
        name=NAME,
        address=ADDRESS,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="00:00:00:00:00:01",
        device=ble_device(),
        advertisement=AdvertisementData(
            local_name=NAME,
            manufacturer_data={},
            service_data={},
            service_uuids=[],
            tx_power=-127,
            rssi=-60,
            platform_data=(),
        ),
        connectable=True,
        time=0,
        tx_power=-127,
    )


class FakeBru:
    """A connected tea maker that answers commands like the real one."""

    def __init__(self) -> None:
        self.status = STATUS_STANDBY
        self.written: list[bytes] = []
        self.paired = False
        self.connected = True
        self.reachable = True
        self.start_frames = {SHORT_START}
        self.pair_error: BaseException | None = None
        self._notify: Callable[[Any, bytearray], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def pair(self) -> None:
        if self.pair_error is not None:
            raise self.pair_error
        self.paired = True

    async def start_notify(
        self, _uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        self._notify = callback

    async def disconnect(self) -> None:
        self.connected = False

    async def write_gatt_char(
        self, _uuid: str, data: bytes, response: bool = False
    ) -> None:
        self.written.append(bytes(data))
        if (reply := self.reply(bytes(data))) is not None and self._notify:
            self._notify(None, bytearray(reply))

    def reply(self, command: bytes) -> bytes | None:
        """Return BRU's answer to ``command``."""
        match command[2]:
            case 0x44:
                return self.status
            case 0x2A:
                return SETUP_REPLY
            case 0x50:
                return PRESET_REPLIES[command[3]]
            case 0x40:
                accepted = len(command) in self.start_frames
                return START_ACCEPTED if accepted else START_REFUSED
        return None


@pytest.fixture
def fake_bru() -> Generator[FakeBru]:
    """Connect the coordinator to a fake tea maker."""
    bru = FakeBru()

    async def connect(*_args: Any, **_kwargs: Any) -> FakeBru:
        if not bru.reachable:
            raise BleakError("The tea maker is out of range")
        bru.connected = True
        return bru

    with (
        patch(
            "custom_components.bru_tea_maker.coordinator.establish_connection",
            side_effect=connect,
        ),
        patch(
            "custom_components.bru_tea_maker.coordinator.bluetooth."
            "async_ble_device_from_address",
            return_value=ble_device(),
        ),
    ):
        yield bru


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a set-up-ready config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
    )
    entry.add_to_hass(hass)
    return entry
