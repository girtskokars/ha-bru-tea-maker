"""Connection to, and state of, a BRU tea maker."""

import asyncio

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    AWAKE_UPDATE_INTERVAL,
    COMMAND_UUID,
    DEFAULT_SETTINGS,
    DOMAIN,
    LOGGER,
    STANDBY_UPDATE_INTERVAL,
    STATUS_UUID,
)
from .protocol import (
    OP_PRESET,
    OP_SETUP,
    OP_START_BREW,
    OP_STATUS,
    BruPreset,
    BruStatus,
    build_frame,
    is_valid_frame,
    parse_preset,
    parse_preset_count,
    parse_status,
    start_brew_payload,
)

# Stays below the time Home Assistant gives a config entry to set up.
_CONNECT_TIMEOUT = 90
# Resuming a bond takes a fraction of a second. Anything near this long means
# there is none, and BRU is showing a pairing code nobody is there to accept.
_ENCRYPT_TIMEOUT = 20
# BRU answers within about 100 ms.
_REPLY_TIMEOUT = 3

type BruConfigEntry = ConfigEntry[BruCoordinator]


class BruCoordinator(DataUpdateCoordinator[BruStatus]):
    """Keeps the connection open and polls the tea maker's status."""

    config_entry: BruConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BruConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=STANDBY_UPDATE_INTERVAL,
            always_update=False,
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.settings = dict(DEFAULT_SETTINGS)
        self.presets: dict[str, BruPreset] = {}
        self.preset: str | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._request_lock = asyncio.Lock()
        self._reply: tuple[int, asyncio.Future[bytes]] | None = None
        self._missed_poll = False

    @callback
    def async_set_setting(self, key: str, value: int) -> None:
        """Change one setting for the next brew."""
        self.settings[key] = value
        self.preset = None
        self.async_update_listeners()

    @callback
    def async_select_preset(self, name: str) -> None:
        """Copy a preset into the settings for the next brew."""
        preset = self.presets[name]
        self.settings = {key: getattr(preset, key) for key in DEFAULT_SETTINGS}
        self.preset = name
        self.async_update_listeners()

    async def async_start_brew(self) -> None:
        """Start a brew with the current settings."""
        try:
            await self._request(OP_START_BREW, start_brew_payload(**self.settings))
        except (BleakError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not start the brew: {err!r}") from err
        await self.async_request_refresh()

    async def async_sync_presets(self) -> None:
        """Read the presets from the tea maker."""
        try:
            await self._read_presets()
        except (BleakError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"Could not read the presets: {err!r}") from err
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop polling and disconnect."""
        await super().async_shutdown()
        await self._disconnect()

    @property
    def _connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def _async_update_data(self) -> BruStatus:
        try:
            if not self._connected:
                # Until the tea maker answers again, retry at the slow pace.
                self.update_interval = STANDBY_UPDATE_INTERVAL
                await self._connect()
            try:
                frame = await self._request(OP_STATUS)
            except TimeoutError:
                # One lost reply is not worth a reconnect; a second one means
                # the link is gone even if the adapter still reports it.
                if self._missed_poll or self.data is None:
                    raise
                self._missed_poll = True
                return self.data
            status = parse_status(frame)
        except (BleakError, TimeoutError, ValueError) as err:
            await self._disconnect()
            raise UpdateFailed(f"Lost the BRU tea maker: {err!r}") from err
        self._missed_poll = False
        self.update_interval = (
            AWAKE_UPDATE_INTERVAL if status.awake else STANDBY_UPDATE_INTERVAL
        )
        return status

    async def _connect(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed("The BRU tea maker is not in Bluetooth range")
        async with asyncio.timeout(_CONNECT_TIMEOUT):
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or self.address,
                use_services_cache=False,
            )
        self._client = client
        # BRU rejects everything, including the notification subscription,
        # until the link is encrypted.
        try:
            async with asyncio.timeout(_ENCRYPT_TIMEOUT):
                await client.pair()
        except (BleakError, TimeoutError) as err:
            if isinstance(err, BleakError) and not client.is_connected:
                raise
            # Retrying cannot fix a missing bond, and every failed attempt
            # makes BRU refuse new ones for about five minutes.
            await self._disconnect()
            raise ConfigEntryAuthFailed("The BRU tea maker is not paired") from err
        async with asyncio.timeout(_ENCRYPT_TIMEOUT):
            await client.start_notify(STATUS_UUID, self._on_notify)
        try:
            await self._read_presets()
        except (TimeoutError, ValueError) as err:
            LOGGER.warning("Could not read the presets from the tea maker: %r", err)

    async def _disconnect(self) -> None:
        self._missed_poll = False
        if self._client is None:
            return
        client, self._client = self._client, None
        try:
            async with asyncio.timeout(_REPLY_TIMEOUT):
                await client.disconnect()
        except (BleakError, TimeoutError) as err:
            LOGGER.debug("Disconnect did not complete: %r", err)

    async def _read_presets(self) -> None:
        count = parse_preset_count(await self._request(OP_SETUP))
        presets = [
            parse_preset(await self._request(OP_PRESET, bytes([slot])))
            for slot in range(1, count + 1)
        ]
        self.presets = {preset.name: preset for preset in presets}
        if self.preset not in self.presets:
            self.preset = None

    async def _request(self, opcode: int, payload: bytes = b"") -> bytes:
        """Send a command and return BRU's reply to it."""
        async with self._request_lock:
            if not self._connected:
                raise BleakError("Not connected")
            assert self._client is not None
            command = build_frame(opcode, payload)
            LOGGER.debug("Sending %s", command.hex())
            reply: asyncio.Future[bytes] = self.hass.loop.create_future()
            self._reply = (opcode + 1, reply)
            try:
                async with asyncio.timeout(_REPLY_TIMEOUT):
                    await self._client.write_gatt_char(
                        COMMAND_UUID, command, response=False
                    )
                    return await reply
            finally:
                self._reply = None

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        frame = bytes(data)
        LOGGER.debug("Received %s", frame.hex())
        if self._reply is None or not is_valid_frame(frame):
            return
        opcode, reply = self._reply
        if frame[2] == opcode and not reply.done():
            reply.set_result(frame)
