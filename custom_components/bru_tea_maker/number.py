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
from .protocol import SETTING_RANGES, snap_setting

# The settings live in Home Assistant: no limit needed.
PARALLEL_UPDATES = 0

_UNITS = {
    "temperature": UnitOfTemperature.CELSIUS,
    "water_amount": UnitOfVolume.MILLILITERS,
    "brew_time": UnitOfTime.SECONDS,
}
NUMBERS = tuple(
    NumberEntityDescription(
        key=key,
        native_min_value=low,
        native_max_value=high,
        native_step=step,
        native_unit_of_measurement=_UNITS[key],
    )
    for key, (low, high, step) in SETTING_RANGES.items()
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
            self.coordinator.settings[self._key] = snap_setting(
                self._key, last.native_value
            )

    @property
    def native_value(self) -> int:
        """Return the setting."""
        return self.coordinator.settings[self._key]

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting."""
        self.coordinator.async_set_setting(self._key, value)
