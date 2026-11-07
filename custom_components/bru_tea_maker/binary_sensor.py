"""Binary sensors for the BRU Tea Maker integration."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTENTION_MESSAGES, BREWING_STATUSES
from .coordinator import BruConfigEntry, BruCoordinator
from .entity import BruEntity
from .protocol import BruStatus

# Coordinator entities: updates need no limit.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BruBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor derived from the tea maker's status."""

    value_fn: Callable[[BruStatus], bool]


BINARY_SENSORS = (
    BruBinarySensorDescription(
        key="brewing",
        translation_key="brewing",
        device_class=BinarySensorDeviceClass.RUNNING,
        # "Done" is shown once the brew is over.
        value_fn=lambda data: data.status in BREWING_STATUSES - {"enjoy"},
    ),
    BruBinarySensorDescription(
        key="needs_attention",
        translation_key="needs_attention",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # Not every warning: "Open lid" follows each brew and needs no one.
        value_fn=lambda data: (
            data.message in ATTENTION_MESSAGES or data.status == "error_message"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities(
        BruBinarySensor(entry.runtime_data, description)
        for description in BINARY_SENSORS
    )


class BruBinarySensor(BruEntity, BinarySensorEntity):
    """Whether the tea maker is brewing, or needs someone at it."""

    entity_description: BruBinarySensorDescription

    def __init__(
        self, coordinator: BruCoordinator, description: BruBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state, or None before the first status."""
        data = self.coordinator.data
        return None if data is None else self.entity_description.value_fn(data)
