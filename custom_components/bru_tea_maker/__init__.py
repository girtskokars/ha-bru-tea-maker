"""The BRU Tea Maker integration."""

from homeassistant.components import bluetooth
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import BruConfigEntry, BruCoordinator, async_clear_pair_failures
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BruConfigEntry) -> bool:
    """Set up a BRU tea maker from a config entry."""
    coordinator = BruCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _async_stop(_event: Event) -> None:
        # Config entries are not unloaded on shutdown, and a local adapter
        # left connected keeps a stale record of the link across the restart.
        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    # Not used to mark the tea maker unavailable: it may stop advertising
    # while it is connected. Only changed advertisements arrive here, so this
    # mostly helps once the tea maker was gone long enough to be forgotten;
    # polling keeps retrying meanwhile. Clearing the advertisement history
    # would let every one through, but would also hide the tea maker from the
    # immediate reconnect until it advertises again.
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            coordinator.async_on_advertisement,
            bluetooth.BluetoothCallbackMatcher(
                address=coordinator.address, connectable=True
            ),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BruConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Offer the tea maker again once its entry is removed, without a
        # restart. A discovery for an entry that is still there just aborts.
        bluetooth.async_rediscover_address(hass, entry.runtime_data.address)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: BruConfigEntry) -> None:
    """Forget the removed tea maker's pairing failures."""
    async_clear_pair_failures(hass, entry.entry_id)
