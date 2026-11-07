"""Brew event for the BRU Tea Maker integration."""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTIVE_BREW_PHASES, ALERT_STATUSES, SERVED_PHASES
from .coordinator import BruConfigEntry
from .entity import BruEntity

EVENT_STARTED = "started"
EVENT_FINISHED = "finished"
EVENT_TYPES = [EVENT_STARTED, EVENT_FINISHED]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the brew event."""
    async_add_entities([BruBrewEvent(entry.runtime_data, "brew")])


class BruBrewEvent(BruEntity, EventEntity):
    """Fires when a brew starts and when the tea is ready, once per brew."""

    _attr_event_types = EVENT_TYPES

    _phase: str | None = None
    """The brew phase last seen during the running brew, None between brews."""

    async def async_added_to_hass(self) -> None:
        """Pick up a brew that was already running, without an event."""
        await super().async_added_to_hass()
        if (data := self.coordinator.data) and data.status in ACTIVE_BREW_PHASES:
            self._phase = data.status

    @callback
    def _handle_coordinator_update(self) -> None:
        if (event := self._event()) is not None:
            self._trigger_event(event)
        super()._handle_coordinator_update()

    def _event(self) -> str | None:
        """Follow the status through a brew and return the event it causes."""
        status = self.coordinator.data.status
        if status in ACTIVE_BREW_PHASES:
            # A prompt such as "Close lid" can hold back a started brew, so
            # it counts as started once the tea maker really is at work.
            started, self._phase = self._phase is None, status
            return EVENT_STARTED if started else None
        if self._phase is None or status in ALERT_STATUSES | {"brewing"}:
            # Between brews, or a prompt or an unknown phase within one.
            return None
        served, self._phase = self._phase in SERVED_PHASES, None
        return EVENT_FINISHED if status == "enjoy" or served else None
