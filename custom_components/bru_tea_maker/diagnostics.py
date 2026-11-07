"""Diagnostics for the BRU Tea Maker integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import BruConfigEntry

TO_REDACT = {CONF_ADDRESS, "unique_id", "source", "title", "discovery_keys"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BruConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    info = bluetooth.async_last_service_info(
        hass, coordinator.address, connectable=True
    )
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "firmware": coordinator.firmware,
            "hardware": coordinator.hardware,
            "connected": coordinator.connected,
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "status": asdict(coordinator.data) if coordinator.data else None,
            "last_status_frame": (coordinator.last_status_frame or b"").hex(),
            "settings": coordinator.settings,
            "preset": coordinator.preset,
            "presets": [asdict(preset) for preset in coordinator.presets.values()],
            "advertisement": (
                {"source": info.source, "rssi": info.rssi} if info else None
            ),
            "frames": list(coordinator.frames),
        },
        TO_REDACT,
    )
