"""Preset select for the BRU Tea Maker integration."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import BruConfigEntry
from .entity import BruEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BruConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the preset select."""
    async_add_entities([BruPresetSelect(entry.runtime_data, "preset")])


class BruPresetSelect(BruEntity, SelectEntity, RestoreEntity):
    """Copies one of the tea maker's presets into the brew settings."""

    async def async_added_to_hass(self) -> None:
        """Restore the selection from before the restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.coordinator.presets:
            self.coordinator.preset = last.state

    @property
    def options(self) -> list[str]:
        """Return the presets, in the tea maker's order."""
        return list(self.coordinator.presets)

    @property
    def current_option(self) -> str | None:
        """Return the preset the brew settings were copied from."""
        return self.coordinator.preset

    async def async_select_option(self, option: str) -> None:
        """Copy a preset into the brew settings."""
        self.coordinator.async_select_preset(option)
