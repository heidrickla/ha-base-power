"""The Base Power integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_SCAN_INTERVAL, MIN_SCAN_INTERVAL_SECONDS
from .coordinator import BasePowerConfigEntry, BasePowerCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> bool:
    seconds = entry.options.get(CONF_SCAN_INTERVAL)
    interval = (
        timedelta(seconds=max(int(seconds), MIN_SCAN_INTERVAL_SECONDS))
        if seconds
        else DEFAULT_SCAN_INTERVAL
    )
    coordinator = BasePowerCoordinator(hass, entry, interval)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(hass: HomeAssistant, entry: BasePowerConfigEntry) -> None:
    """The poll interval lives in options, and only a reload re-reads it."""
    await hass.config_entries.async_reload(entry.entry_id)
