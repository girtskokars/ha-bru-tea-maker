"""Buttons for the BRU Tea Maker integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BruConfigEntry
from .entity import BruEntity

# One command at a time, as the tea maker takes them.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_entities(
        [
            BruStartBrewButton(entry.runtime_data, "start_brew"),
            BruSyncPresetsButton(entry.runtime_data, "sync_presets"),
        ]
    )


class BruStartBrewButton(BruEntity, ButtonEntity):
    """Starts a brew with the current settings."""

    async def async_press(self) -> None:
        """Start the brew."""
        await self.coordinator.async_start_brew()


class BruSyncPresetsButton(BruEntity, ButtonEntity):
    """Reads the presets from the tea maker again."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Read the presets."""
        await self.coordinator.async_sync_presets()
