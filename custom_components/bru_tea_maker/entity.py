"""Base entity for the BRU Tea Maker integration."""

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BruCoordinator


class BruEntity(CoordinatorEntity[BruCoordinator]):
    """An entity of a BRU tea maker, identified by ``key``."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BruCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.address}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name="BRU Tea Maker",
            manufacturer="BRU",
            model="BRU Maker One",
            model_id="BRU 100",
            sw_version=coordinator.firmware,
            hw_version=coordinator.hardware,
        )
