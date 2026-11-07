"""Connection to, and state of, a BRU tea maker."""

import asyncio
from collections import deque
from collections.abc import Mapping

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    ATTENTION_MESSAGES,
    AWAKE_UPDATE_INTERVAL,
    BREW_NAME,
    BREW_PHASES,
    BUSY_STATUSES,
    COMMAND_UUID,
    DEFAULT_SETTINGS,
    DOMAIN,
    FIRMWARE_UUID,
    HARDWARE_UUID,
    LOGGER,
    STANDBY_UPDATE_INTERVAL,
    STATUS_BREWING,
    STATUS_UUID,
)
from .pairing import record_failure
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
    serial_from_name,
    snap_setting,
    start_accepted,
    start_brew_payload,
)

# Stays below the time Home Assistant gives a config entry to set up.
_CONNECT_TIMEOUT = 90
# Resuming a bond takes a fraction of a second. Anything near this long means
# there is none, and BRU is showing a pairing code nobody is there to accept.
_ENCRYPT_TIMEOUT = 20
# BRU answers within about 100 ms.
_REPLY_TIMEOUT = 3
# bleak waits up to 10 s for BlueZ to confirm a disconnect and only then
# closes the client's D-Bus connection. Cutting it short leaves that
# connection's notification subscription in BlueZ, and every later
# notification then arrives once more per leftover subscription.
_DISCONNECT_TIMEOUT = 15
# One slow or lost pairing attempt is not a missing bond: a link that drops
# mid-attempt can cut one short. Only this many failures in a row are taken to
# mean the bond is gone.
_PAIR_FAILURES_BEFORE_REAUTH = 3
_PAIR_FAILURES_KEY: HassKey[dict[str, int]] = HassKey(f"{DOMAIN}_pair_failures")
# Commands and replies kept for the diagnostics.
_FRAME_HISTORY = 50

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
        self.firmware: str | None = None
        self.hardware: str | None = None
        self.serial_number: str | None = None
        self.frames: deque[str] = deque(maxlen=_FRAME_HISTORY)
        self.last_status_frame: bytes | None = None
        self._unknown_statuses: set[tuple[int, int]] = set()

    @property
    def _pair_failures(self) -> int:
        """Pairing failures in a row, kept across setup retries.

        Setup builds a new coordinator on every retry, so the count lives in
        ``hass.data`` for three failures in a row to add up.
        """
        failures = self.hass.data.get(_PAIR_FAILURES_KEY, {})
        return failures.get(self.config_entry.entry_id, 0)

    @_pair_failures.setter
    def _pair_failures(self, value: int) -> None:
        failures = self.hass.data.setdefault(_PAIR_FAILURES_KEY, {})
        failures[self.config_entry.entry_id] = value

    @callback
    def async_set_setting(self, key: str, value: float) -> None:
        """Change one setting for the next brew."""
        self.settings[key] = snap_setting(key, value)
        self.preset = None
        self.async_update_listeners()

    @callback
    def async_select_preset(self, name: str) -> None:
        """Copy a preset into the settings for the next brew."""
        preset = self.presets[name]
        self.settings = {key: getattr(preset, key) for key in DEFAULT_SETTINGS}
        self.preset = name
        self.async_update_listeners()

    async def async_start_brew(self, settings: Mapping[str, int] | None = None) -> None:
        """Start a brew with ``settings``, or with the current settings."""
        settings = self.settings if settings is None else settings
        self._check_can_brew()
        try:
            try:
                accepted = await self._start_brew(settings, BREW_NAME)
            except TimeoutError:
                accepted = False
            if not accepted:
                # Older firmware refuses, or does not answer, the longer frame.
                accepted = await self._start_brew(settings, None)
        except (BleakError, TimeoutError, ValueError) as err:
            LOGGER.debug("Starting the brew failed: %r", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="start_failed"
            ) from err
        if not accepted:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="start_refused"
            )
        await self.async_request_refresh()

    def _check_can_brew(self) -> None:
        """Raise if the tea maker cannot start a brew right now."""
        if self.data is None or not self.last_update_success or not self.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            )
        if self.data.message in ATTENTION_MESSAGES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=f"prompt_{self.data.message}",
            )
        if self.data.status in BUSY_STATUSES:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="busy"
            )

    async def _start_brew(self, settings: Mapping[str, int], name: str | None) -> bool:
        payload = start_brew_payload(**settings, name=name)
        return start_accepted(await self._request(OP_START_BREW, payload))

    async def async_sync_presets(self) -> None:
        """Read the presets from the tea maker."""
        try:
            await self._read_presets()
        except (BleakError, TimeoutError, ValueError) as err:
            LOGGER.debug("Reading the presets failed: %r", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="sync_failed"
            ) from err
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop polling and disconnect."""
        await super().async_shutdown()
        await self._disconnect()

    @property
    def connected(self) -> bool:
        """Return whether the tea maker is connected."""
        return self._client is not None and self._client.is_connected

    async def _async_update_data(self) -> BruStatus:
        try:
            if not self.connected:
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
            LOGGER.debug("Lost the tea maker: %r", err)
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="connection_lost"
            ) from err
        self.last_status_frame = frame
        self._missed_poll = False
        if status.status is None or (
            status.code == STATUS_BREWING and status.sub_code not in BREW_PHASES
        ):
            self._log_unknown_status(status)
        self.update_interval = (
            AWAKE_UPDATE_INTERVAL if status.awake else STANDBY_UPDATE_INTERVAL
        )
        return status

    async def _connect(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="not_in_range"
            )
        async with asyncio.timeout(_CONNECT_TIMEOUT):
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or self.address,
                use_services_cache=False,
            )
        self._client = client
        self.serial_number = serial_from_name(ble_device.name) or self.serial_number
        # BRU rejects everything, including the notification subscription,
        # until the link is encrypted.
        try:
            async with asyncio.timeout(_ENCRYPT_TIMEOUT):
                await client.pair()
        except (BleakError, TimeoutError) as err:
            if isinstance(err, BleakError) and not client.is_connected:
                raise
            await self._disconnect()
            # Each failed attempt makes the tea maker refuse new ones for a
            # while; the re-pair flow tells the user how long to wait.
            record_failure(self.address)
            self._pair_failures += 1
            LOGGER.warning(
                "Pairing attempt %d of %d failed: %r. If it keeps failing, "
                "the tea maker has probably lost its pairing: put it in "
                "pairing mode and pair it again from the integration's page",
                self._pair_failures,
                _PAIR_FAILURES_BEFORE_REAUTH,
                err,
            )
            if self._pair_failures < _PAIR_FAILURES_BEFORE_REAUTH:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="pair_attempt_failed",
                    translation_placeholders={
                        "attempt": str(self._pair_failures),
                        "attempts": str(_PAIR_FAILURES_BEFORE_REAUTH),
                    },
                ) from err
            # Retrying cannot fix a missing bond, and every failed attempt
            # makes BRU refuse new ones for about five minutes.
            self._pair_failures = 0
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="not_paired"
            ) from err
        self._pair_failures = 0
        async with asyncio.timeout(_ENCRYPT_TIMEOUT):
            await client.start_notify(STATUS_UUID, self._on_notify)
        await self._read_device_information(client)
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
            async with asyncio.timeout(_DISCONNECT_TIMEOUT):
                await client.disconnect()
        except (BleakError, TimeoutError) as err:
            LOGGER.debug("Disconnect did not complete: %r", err)

    async def _read_device_information(
        self, client: BleakClientWithServiceCache
    ) -> None:
        """Read the firmware and hardware versions once per connection.

        They and the serial number go to the device registry when they
        change, such as after a firmware update.
        """
        values: list[str | None] = []
        for uuid in (FIRMWARE_UUID, HARDWARE_UUID):
            try:
                async with asyncio.timeout(_REPLY_TIMEOUT):
                    raw = await client.read_gatt_char(uuid)
            except (BleakError, TimeoutError) as err:
                LOGGER.debug("Could not read %s: %r", uuid, err)
                values.append(None)
                continue
            values.append(bytes(raw).decode("ascii", errors="replace").strip() or None)
        self.firmware, self.hardware = values
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, self.address), self.config_entry.entry_id
        )
        if device is not None and (
            device.sw_version != self.firmware
            or device.hw_version != self.hardware
            or device.serial_number != self.serial_number
        ):
            registry.async_update_device(
                device.id,
                sw_version=self.firmware,
                hw_version=self.hardware,
                serial_number=self.serial_number,
            )

    def _log_unknown_status(self, status: BruStatus) -> None:
        key = (status.code, status.sub_code)
        if key in self._unknown_statuses:
            return
        self._unknown_statuses.add(key)
        LOGGER.info(
            "Unknown status %s/%s from the tea maker; please report it with the "
            "diagnostics",
            *key,
        )

    def _record(self, direction: str, frame: bytes) -> None:
        """Keep ``frame`` for the diagnostics."""
        time = dt_util.utcnow().isoformat(timespec="milliseconds")
        self.frames.append(f"{time} {direction} {frame.hex()}")

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
            if not self.connected:
                raise BleakError("Not connected")
            assert self._client is not None
            command = build_frame(opcode, payload)
            LOGGER.debug("Sending %s", command.hex())
            if opcode != OP_STATUS:
                self._record(">", command)
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
        if not is_valid_frame(frame):
            return
        # Status replies repeat every poll; keep only the ones that change.
        if frame[2] != OP_STATUS + 1 or frame != self.last_status_frame:
            self._record("<", frame)
        if self._reply is None:
            return
        opcode, reply = self._reply
        if frame[2] == opcode and not reply.done():
            reply.set_result(frame)


@callback
def async_clear_pair_failures(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the pairing failures of a removed tea maker."""
    hass.data.get(_PAIR_FAILURES_KEY, {}).pop(entry_id, None)
