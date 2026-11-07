"""Actions of the BRU Tea Maker integration."""

import probatio as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DEFAULT_SETTINGS, DOMAIN
from .coordinator import BruCoordinator
from .protocol import SETTING_RANGES, snap_setting

ATTR_PRESET = "preset"

BREW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_PRESET): cv.string,
        **{
            vol.Optional(key): vol.All(vol.Coerce(float), vol.Range(low, high))
            for key, (low, high, _step) in SETTING_RANGES.items()
        },
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(DOMAIN, "brew", _async_brew, schema=BREW_SCHEMA)


async def _async_brew(call: ServiceCall) -> None:
    """Brew a preset or the current settings, with any values given instead.

    The number and preset entities are left as they are.
    """
    coordinator = _coordinator(call.hass, call.data[ATTR_DEVICE_ID])
    settings = dict(coordinator.settings)
    if (name := call.data.get(ATTR_PRESET)) is not None:
        if (preset := coordinator.presets.get(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_found",
                translation_placeholders={
                    "preset": name,
                    "presets": ", ".join(coordinator.presets) or "none",
                },
            )
        settings = {key: getattr(preset, key) for key in DEFAULT_SETTINGS}
    for key in DEFAULT_SETTINGS:
        if key in call.data:
            settings[key] = snap_setting(key, call.data[key])
    await coordinator.async_start_brew(settings)


def _coordinator(hass: HomeAssistant, device_id: str) -> BruCoordinator:
    """Return the coordinator of the tea maker ``device_id``."""
    if device := dr.async_get(hass).async_get(device_id):
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if (
                entry is not None
                and entry.domain == DOMAIN
                and entry.state is ConfigEntryState.LOADED
            ):
                coordinator: BruCoordinator = entry.runtime_data
                return coordinator
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="device_not_loaded"
    )
