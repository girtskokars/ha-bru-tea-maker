"""Sensors for the BRU Tea Maker integration."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BREW_PHASES, MESSAGE_NONE, MESSAGES, STATUS_NAMES
from .coordinator import BruConfigEntry, BruCoordinator
from .entity import BruEntity

# Coordinator entities: updates need no limit.
PARALLEL_UPDATES = 0

# Sensor key (a field of BruStatus) -> the states it can have.
SENSORS = {
    "status": sorted({*STATUS_NAMES.values(), *BREW_PHASES.values()}),
    "message": [MESSAGE_NONE, *MESSAGES.values()],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        BruSensor(entry.runtime_data, key, options) for key, options in SENSORS.items()
    )


class BruSensor(BruEntity, SensorEntity):
    """What the tea maker is doing, or the prompt on its screen."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self, coordinator: BruCoordinator, key: str, options: list[str]
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_options = options
        if key == "status":
            # The tea maker's main state: shown with the device name alone.
            self._attr_name = None

    @property
    def native_value(self) -> str | None:
        """Return the current state."""
        value: str | None = getattr(self.coordinator.data, self._key)
        return value
