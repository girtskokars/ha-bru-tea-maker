"""Pairing with the BRU tea maker.

BRU only talks over a bond made by LE Secure Connections Numeric Comparison:
both sides show a 6-digit code and each has to confirm it. BRU's side is
confirmed on its own screen. On a local adapter, BlueZ asks its pairing agent
to confirm the other side, so one is registered here; anything else (such as
an ESPHome Bluetooth proxy) answers on its own and never reaches the agent.
"""

import asyncio
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from dbus_fast import BusType, DBusError, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt32
from dbus_fast.service import ServiceInterface, method

from .const import LOGGER

_AGENT_PATH = "/homeassistant/bru_tea_maker/agent"

# BRU waits this long for its code to be accepted.
_PAIR_TIMEOUT = 60

_pairings: dict[str, BruPairing] = {}
_agent_bus: MessageBus | None = None
_agent_lock = asyncio.Lock()


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

    async def async_wait_confirmed(self) -> bool:
        """Return whether the code was accepted before the attempt ended."""
        return await self._confirmed

    async def async_run(self) -> None:
        """Connect, pair and disconnect. Raises if pairing fails."""
        address = self._ble_device.address.upper()
        _pairings[address] = self
        try:
            await async_ensure_pairing_agent()
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self._ble_device.name or address,
                max_attempts=2,
                use_services_cache=False,
            )
            try:
                async with asyncio.timeout(_PAIR_TIMEOUT):
                    await client.pair()
            finally:
                await client.disconnect()
        finally:
            del _pairings[address]
            if not self._confirmed.done():
                self._confirmed.set_result(False)


class _PairingAgent(ServiceInterface):
    """org.bluez.Agent1 that hands the code to the pairing in progress."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @method()
    async def RequestConfirmation(  # noqa: N802
        self, device: DBusObjectPath, passkey: DBusUInt32
    ) -> None:
        # /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF -> AA:BB:CC:DD:EE:FF
        address = device.rsplit("/dev_", 1)[-1].replace("_", ":").upper()
        pairing = _pairings.get(address)
        if pairing is None or pairing.passkey.done():
            # As the adapter's default agent this is also asked about other
            # integrations' devices; accept those.
            return
        pairing.passkey.set_result(passkey)
        if not await pairing.async_wait_confirmed():
            raise DBusError("org.bluez.Error.Rejected", "Code not confirmed")

    @method()
    def RequestAuthorization(self, device: DBusObjectPath) -> None:  # noqa: N802
        pass

    @method()
    def AuthorizeService(  # noqa: N802
        self, device: DBusObjectPath, uuid: DBusStr
    ) -> None:
        pass

    @method()
    def Cancel(self) -> None:  # noqa: N802
        pass

    @method()
    def Release(self) -> None:  # noqa: N802
        pass


async def async_ensure_pairing_agent() -> None:
    """Make every local adapter pairable and register the agent, once.

    Home Assistant picks the adapter when it connects, so all of them are
    prepared. Does nothing on a host without BlueZ.
    """
    global _agent_bus  # noqa: PLW0603
    async with _agent_lock:
        if _agent_bus is not None:
            return
        bus: MessageBus | None = None
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            async def interface(path: str, name: str) -> Any:
                node = await bus.introspect("org.bluez", path)
                return bus.get_proxy_object("org.bluez", path, node).get_interface(name)

            objects = await (
                await interface("/", "org.freedesktop.DBus.ObjectManager")
            ).call_get_managed_objects()
            for path, interfaces in objects.items():
                if "org.bluez.Adapter1" in interfaces:
                    properties = await interface(
                        path, "org.freedesktop.DBus.Properties"
                    )
                    await properties.call_set(
                        "org.bluez.Adapter1", "Pairable", Variant("b", True)
                    )
                    LOGGER.debug("Made %s pairable", path)

            bus.export(_AGENT_PATH, _PairingAgent())
            manager = await interface("/org/bluez", "org.bluez.AgentManager1")
            await manager.call_register_agent(_AGENT_PATH, "KeyboardDisplay")
            await manager.call_request_default_agent(_AGENT_PATH)
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("No BlueZ pairing agent registered: %r", err)
            if bus is not None:
                bus.disconnect()
            return
        _agent_bus = bus
