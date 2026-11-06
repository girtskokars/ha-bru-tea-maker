"""Tests for pairing and the BlueZ pairing agent."""

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakOutOfConnectionSlotsError
from dbus_fast import DBusError, Variant
import pytest

from custom_components.bru_tea_maker import pairing
from custom_components.bru_tea_maker.pairing import (
    LOCKOUT,
    BruPairing,
    BruPairingError,
    lockout_remaining,
    record_failure,
)

from .conftest import ADDRESS, ble_device

DEVICE_PATH = "/org/bluez/hci0/dev_" + ADDRESS.replace(":", "_")
OTHER_PATH = "/org/bluez/hci0/dev_11_22_33_44_55_66"


@pytest.fixture(autouse=True)
def reset_pairing_state() -> Generator[None]:
    """Start every test without failures or a registered agent."""
    pairing._failures.clear()
    yield
    pairing._failures.clear()
    assert pairing._agent_users == 0
    assert not pairing._pairings


class FakeBus:
    """The system bus with BlueZ on it, recording the calls made."""

    def __init__(self, adapters: dict[str, bool]) -> None:
        self.adapters = dict(adapters)
        self.registered: list[str] = []
        self.unregistered: list[str] = []
        self.exported: Any = None
        self.disconnected = False

    async def introspect(self, _service: str, path: str) -> str:
        return path

    def get_proxy_object(self, _service: str, path: str, _node: str) -> Any:
        bus = self
        proxy = MagicMock()

        def get_interface(name: str) -> Any:
            interface = MagicMock()
            if name == "org.freedesktop.DBus.ObjectManager":
                interface.call_get_managed_objects = AsyncMock(
                    return_value={
                        adapter: {
                            "org.bluez.Adapter1": {"Pairable": Variant("b", value)}
                        }
                        for adapter, value in bus.adapters.items()
                    }
                )
            elif name == "org.freedesktop.DBus.Properties":

                async def set_property(_iface: str, _name: str, value: Any) -> None:
                    bus.adapters[path] = value.value

                interface.call_set = set_property
            elif name == "org.bluez.AgentManager1":

                async def register(agent_path: str, _capability: str) -> None:
                    bus.registered.append(agent_path)

                async def unregister(agent_path: str) -> None:
                    bus.unregistered.append(agent_path)

                interface.call_register_agent = register
                interface.call_request_default_agent = AsyncMock()
                interface.call_unregister_agent = unregister
            return interface

        proxy.get_interface = get_interface
        return proxy

    def export(self, _path: str, interface: Any) -> None:
        self.exported = interface

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def bus() -> Generator[FakeBus]:
    """Replace the D-Bus system bus."""
    fake = FakeBus({"/org/bluez/hci0": False, "/org/bluez/hci1": True})
    message_bus = MagicMock()
    message_bus.return_value.connect = AsyncMock(return_value=fake)
    with patch.object(pairing, "MessageBus", message_bus):
        yield fake


class FakeClient:
    """A connection to the tea maker for one pairing attempt."""

    def __init__(self, pair: Any = None) -> None:
        self.pair = pair or AsyncMock()
        self.disconnect = AsyncMock()


def _connect(client: FakeClient | BaseException) -> Any:
    side_effect = client if isinstance(client, BaseException) else None
    return patch.object(
        pairing,
        "establish_connection",
        AsyncMock(return_value=client, side_effect=side_effect),
    )


async def test_agent_held_only_while_pairing(bus: FakeBus) -> None:
    """The agent is registered for the pairing and removed afterwards."""
    client = FakeClient()
    with _connect(client):
        await BruPairing(ble_device()).async_run()
    assert bus.registered == [pairing._AGENT_PATH]
    assert bus.unregistered == [pairing._AGENT_PATH]
    assert bus.disconnected
    # hci0 was made pairable for the pairing and is back to unpairable.
    assert bus.adapters == {"/org/bluez/hci0": False, "/org/bluez/hci1": True}
    client.disconnect.assert_awaited_once()


async def test_agent_removed_after_failure(bus: FakeBus) -> None:
    """A failed pairing also unregisters the agent."""
    with (
        _connect(FakeClient(pair=AsyncMock(side_effect=BleakError("rejected")))),
        pytest.raises(BruPairingError),
    ):
        await BruPairing(ble_device()).async_run()
    assert bus.unregistered == [pairing._AGENT_PATH]
    assert bus.adapters["/org/bluez/hci0"] is False


async def test_agent_removed_after_cancel(bus: FakeBus) -> None:
    """Closing the flow mid-pairing unregisters the agent."""
    started = asyncio.Event()

    async def pair() -> None:
        started.set()
        await asyncio.sleep(3600)

    with _connect(FakeClient(pair=pair)):
        task = asyncio.create_task(BruPairing(ble_device()).async_run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert bus.unregistered == [pairing._AGENT_PATH]


async def test_agent_shared_by_two_pairings(bus: FakeBus) -> None:
    """Two pairings at once register the agent once."""
    release = asyncio.Event()

    async def pair() -> None:
        await release.wait()

    first = BruPairing(ble_device())
    second = BruPairing(BLEDevice("11:22:33:44:55:66", "BRU.M2-44556666", {}))
    with _connect(FakeClient(pair=pair)):
        tasks = [asyncio.create_task(p.async_run()) for p in (first, second)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)
    assert bus.registered == [pairing._AGENT_PATH]
    assert bus.unregistered == [pairing._AGENT_PATH]


async def test_no_bluez(caplog: pytest.LogCaptureFixture) -> None:
    """Without BlueZ, pairing still runs (a proxy confirms on its own)."""
    message_bus = MagicMock()
    message_bus.return_value.connect = AsyncMock(side_effect=OSError("no bus"))
    with patch.object(pairing, "MessageBus", message_bus), _connect(FakeClient()):
        await BruPairing(ble_device()).async_run()
    assert pairing._agent is None


async def test_agent_confirms_only_the_tea_maker(bus: FakeBus) -> None:
    """The agent hands over the code for the tea maker and rejects others."""
    release = asyncio.Event()

    async def pair() -> None:
        await release.wait()

    attempt = BruPairing(ble_device())
    with _connect(FakeClient(pair=pair)):
        task = asyncio.create_task(attempt.async_run())
        for _ in range(5):
            await asyncio.sleep(0)
        agent = bus.exported
        assert agent is not None

        with pytest.raises(DBusError):
            await agent.RequestConfirmation.__wrapped__(agent, OTHER_PATH, 1)
        with pytest.raises(DBusError):
            agent.RequestAuthorization.__wrapped__(agent, OTHER_PATH)
        with pytest.raises(DBusError):
            agent.AuthorizeService.__wrapped__(agent, OTHER_PATH, "uuid")
        agent.RequestAuthorization.__wrapped__(agent, DEVICE_PATH)

        confirmation = asyncio.create_task(
            agent.RequestConfirmation.__wrapped__(agent, DEVICE_PATH, 123456)
        )
        assert await attempt.passkey == 123456
        attempt.confirm()
        await confirmation
        release.set()
        await task


async def test_agent_rejects_unconfirmed_code(bus: FakeBus) -> None:
    """A code that is cancelled is rejected."""
    release = asyncio.Event()

    async def pair() -> None:
        await release.wait()

    attempt = BruPairing(ble_device())
    with _connect(FakeClient(pair=pair)):
        task = asyncio.create_task(attempt.async_run())
        for _ in range(5):
            await asyncio.sleep(0)
        agent = bus.exported
        confirmation = asyncio.create_task(
            agent.RequestConfirmation.__wrapped__(agent, DEVICE_PATH, 42)
        )
        await attempt.passkey
        agent.Cancel.__wrapped__(agent)
        with pytest.raises(DBusError):
            await confirmation
        release.set()
        await task


@pytest.mark.parametrize(
    ("connect", "pair_error", "reason", "locked"),
    [
        (BleakOutOfConnectionSlotsError("full"), None, "no_connection_slots", False),
        (BleakError("gone"), None, "cannot_connect", False),
        (TimeoutError(), None, "cannot_connect", False),
        (None, TimeoutError(), "pair_timeout", True),
        (None, BleakError("rejected"), "pair_rejected", True),
    ],
)
async def test_failure_reasons(
    connect: BaseException | None,
    pair_error: BaseException | None,
    reason: str,
    locked: bool,
) -> None:
    """Each failure names its reason; only failures at BRU start a lockout."""
    client = FakeClient(pair=AsyncMock(side_effect=pair_error))
    message_bus = MagicMock()
    message_bus.return_value.connect = AsyncMock(side_effect=OSError("no bus"))
    with (
        patch.object(pairing, "MessageBus", message_bus),
        _connect(connect or client),
        pytest.raises(BruPairingError) as err,
    ):
        await BruPairing(ble_device()).async_run()
    assert err.value.reason == reason
    assert bool(lockout_remaining(ADDRESS)) is locked


def test_lockout_runs_out() -> None:
    """The remembered lockout runs out after LOCKOUT seconds."""
    assert lockout_remaining(ADDRESS) == 0
    with patch.object(pairing.time, "monotonic", return_value=1000.0):
        record_failure(ADDRESS.lower())
        assert lockout_remaining(ADDRESS) == LOCKOUT
    with patch.object(pairing.time, "monotonic", return_value=1000.0 + LOCKOUT):
        assert lockout_remaining(ADDRESS) == 0
