"""Pairing with the BRU tea maker.

BRU only talks over a bond made by LE Secure Connections Numeric Comparison:
both sides show a 6-digit code and each has to confirm it. BRU's side is
confirmed on its own screen. On a local adapter, BlueZ asks its pairing agent
to confirm the other side, so one is registered while a pairing runs;
anything else (such as an ESPHome Bluetooth proxy) answers on its own and
never reaches the agent.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)
from dbus_fast import BusType, DBusError, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt32
from dbus_fast.service import ServiceInterface, method

from .const import LOGGER

_AGENT_PATH = "/homeassistant/bru_tea_maker/agent"
_REJECTED = "org.bluez.Error.Rejected"

# Give up on a pairing after this long. BRU's own window is not known (H13);
# this is long enough that the user, not this timeout, ends the attempt.
PAIR_TIMEOUT = 120
# BRU refuses new pairing attempts for about this long after a failed one.
LOCKOUT = 300

_pairings: dict[str, BruPairing] = {}
_failures: dict[str, float] = {}


class BruPairingError(Exception):
    """Pairing failed. ``reason`` names the failure for the config flow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def record_failure(address: str) -> None:
    """Remember that BRU just saw a failed attempt."""
    _failures[address.upper()] = time.monotonic()


def lockout_remaining(address: str) -> float:
    """Return the seconds until BRU accepts a new attempt, or 0."""
    failed_at = _failures.get(address.upper())
    if failed_at is None:
        return 0
    return max(LOCKOUT - (time.monotonic() - failed_at), 0)


class BruPairing:
    """One pairing attempt, driven by the config flow.

    ``passkey`` resolves with the code when a local adapter asks for it to be
    confirmed; the pairing then waits for ``confirm()``.
    """

    def __init__(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        loop = asyncio.get_running_loop()
        self.passkey: asyncio.Future[int] = loop.create_future()
        self._confirmed: asyncio.Future[bool] = loop.create_future()

    def confirm(self) -> None:
        """Accept the code on Home Assistant's side."""
        if not self._confirmed.done():
            self._confirmed.set_result(True)

    def cancel(self) -> None:
        """Give up on the code, for example when BlueZ cancels the request."""
        if not self._confirmed.done():
            self._confirmed.set_result(False)

    async def async_wait_confirmed(self) -> bool:
        """Return whether the code was accepted before the attempt ended."""
        return await self._confirmed

    async def async_run(self) -> None:
        """Connect, pair and disconnect. Raises BruPairingError on failure."""
        address = self._ble_device.address.upper()
        _pairings[address] = self
        try:
            async with async_pairing_agent():
                await self._pair(address)
        finally:
            del _pairings[address]
            self.cancel()

    async def _pair(self, address: str) -> None:
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self._ble_device.name or address,
                max_attempts=2,
                use_services_cache=False,
            )
        except BleakOutOfConnectionSlotsError as err:
            raise BruPairingError("no_connection_slots") from err
        except (BleakError, TimeoutError) as err:
            # The attempt never reached BRU, so it does not start a lockout.
            raise BruPairingError("cannot_connect") from err
        try:
            async with asyncio.timeout(PAIR_TIMEOUT):
                await client.pair()
        except TimeoutError as err:
            record_failure(address)
            raise BruPairingError("pair_timeout") from err
        except BleakError as err:
            record_failure(address)
            raise BruPairingError("pair_rejected") from err
        finally:
            with suppress(BleakError, TimeoutError):
                await client.disconnect()


def _pairing_for(device: str) -> BruPairing:
    """Return the pairing for a BlueZ device path, or reject the request."""
    # /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF -> AA:BB:CC:DD:EE:FF
    address = device.rsplit("/dev_", 1)[-1].replace("_", ":").upper()
    if (pairing := _pairings.get(address)) is None:
        raise DBusError(_REJECTED, "Not a tea maker being paired")
    return pairing


class _PairingAgent(ServiceInterface):
    """org.bluez.Agent1 that only answers for the tea maker being paired.

    While it is registered it is the adapter's default agent, so it is also
    asked about other devices pairing with the host; those are rejected.
    """

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @method()
    async def RequestConfirmation(  # noqa: N802
        self, device: DBusObjectPath, passkey: DBusUInt32
    ) -> None:
        pairing = _pairing_for(device)
        if pairing.passkey.done():
            raise DBusError(_REJECTED, "Code already handed over")
        pairing.passkey.set_result(passkey)
        if not await pairing.async_wait_confirmed():
            raise DBusError(_REJECTED, "Code not confirmed")

    @method()
    def RequestAuthorization(self, device: DBusObjectPath) -> None:  # noqa: N802
        _pairing_for(device)

    @method()
    def AuthorizeService(  # noqa: N802
        self, device: DBusObjectPath, uuid: DBusStr
    ) -> None:
        _pairing_for(device)

    @method()
    def Cancel(self) -> None:  # noqa: N802
        # BlueZ does not say which request it cancels; only one code can be
        # waiting per tea maker, and the flow shows the failure either way.
        for pairing in _pairings.values():
            pairing.cancel()

    @method()
    def Release(self) -> None:  # noqa: N802
        pass


@dataclass
class _Agent:
    """A registered agent, and each adapter's Pairable value from before."""

    bus: MessageBus
    pairable: dict[str, bool] = field(default_factory=dict)


_agent: _Agent | None = None
_agent_users = 0
_agent_lock = asyncio.Lock()


async def _interface(bus: MessageBus, path: str, name: str) -> Any:
    node = await bus.introspect("org.bluez", path)
    return bus.get_proxy_object("org.bluez", path, node).get_interface(name)


async def _set_pairable(bus: MessageBus, adapter: str, pairable: bool) -> None:
    properties = await _interface(bus, adapter, "org.freedesktop.DBus.Properties")
    await properties.call_set("org.bluez.Adapter1", "Pairable", Variant("b", pairable))
    LOGGER.debug("Set %s Pairable to %s", adapter, pairable)


async def _register_agent() -> _Agent:
    """Make every local adapter pairable and register the agent.

    Home Assistant picks the adapter when it connects, so all of them are
    prepared.
    """
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = _Agent(bus)
    try:
        manager = await _interface(bus, "/", "org.freedesktop.DBus.ObjectManager")
        objects = await manager.call_get_managed_objects()
        for path, interfaces in objects.items():
            if (adapter := interfaces.get("org.bluez.Adapter1")) is None:
                continue
            agent.pairable[path] = bool(adapter["Pairable"].value)
            if not agent.pairable[path]:
                await _set_pairable(bus, path, True)
        bus.export(_AGENT_PATH, _PairingAgent())
        agents = await _interface(bus, "/org/bluez", "org.bluez.AgentManager1")
        await agents.call_register_agent(_AGENT_PATH, "KeyboardDisplay")
        await agents.call_request_default_agent(_AGENT_PATH)
    except BaseException:
        await _unregister_agent(agent)
        raise
    return agent


async def _unregister_agent(agent: _Agent) -> None:
    """Unregister the agent and put each adapter's Pairable value back."""
    try:
        agents = await _interface(agent.bus, "/org/bluez", "org.bluez.AgentManager1")
        with suppress(DBusError):
            await agents.call_unregister_agent(_AGENT_PATH)
        for path, pairable in agent.pairable.items():
            if not pairable:
                await _set_pairable(agent.bus, path, False)
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Could not fully unregister the pairing agent: %r", err)
    finally:
        agent.bus.disconnect()


@asynccontextmanager
async def async_pairing_agent() -> AsyncIterator[None]:
    """Hold the pairing agent while at least one pairing runs.

    Does nothing on a host without BlueZ.
    """
    global _agent, _agent_users  # noqa: PLW0603
    async with _agent_lock:
        if _agent_users == 0:
            try:
                _agent = await _register_agent()
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("No BlueZ pairing agent registered: %r", err)
        _agent_users += 1
    try:
        yield
    finally:
        async with _agent_lock:
            _agent_users -= 1
            if _agent_users == 0 and _agent is not None:
                agent, _agent = _agent, None
                await _unregister_agent(agent)
