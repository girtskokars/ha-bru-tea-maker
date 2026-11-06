"""Brew settings for the BRU Tea Maker integration."""

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BruConfigEntry, BruCoordinator
from .entity import BruEntity

NUMBERS = (
    NumberEntityDescription(
        key="temperature",
        native_min_value=40,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    NumberEntityDescription(
        key="water_amount",
        native_min_value=50,
        native_max_value=500,
        native_step=50,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
    ),
    NumberEntityDescription(
        key="brew_time",
        native_min_value=10,
        native_max_value=600,
        native_step=10,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the brew settings."""
    async_add_entities(
        BruNumber(entry.runtime_data, description) for description in NUMBERS
    )


class BruNumber(BruEntity, RestoreNumber):
    """One setting for the next brew. It lives in Home Assistant only."""

    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        """The setting lives in Home Assistant, so it is always available."""
        return True

    def __init__(
        self, coordinator: BruCoordinator, description: NumberEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Restore the setting from before the restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.settings[self._key] = int(last.native_value)

    @property
    def native_value(self) -> int:
        """Return the setting."""
        return self.coordinator.settings[self._key]

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting."""
        self.coordinator.async_set_setting(self._key, int(value))
