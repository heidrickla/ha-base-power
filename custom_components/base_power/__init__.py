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
    # Which entities exist depends on what the site declares, so this has to
    # happen before the platforms are forwarded.
    await coordinator.async_load_capabilities()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener here on purpose: the options flow is an
    # OptionsFlowWithReload, which reloads the entry itself when the poll
    # interval changes. Registering a listener as well would reload twice for
    # one edit.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
