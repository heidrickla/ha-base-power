"""Clients and fixtures shared by the energy and billing tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fixtures_const import ADDRESS_ID
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_init import COORDINATOR_CLIENT, ON_GRID

from custom_components.base_power.api import (
    Account,
    BatterySnapshot,
    DailyOverview,
    LocationCapabilities,
)
from custom_components.base_power.hourly_statistics import statistic_id

H = [datetime(2026, 10, 19, h, tzinfo=UTC) for h in range(6)]
ENERGY = statistic_id(ADDRESS_ID, "energy_to_home")
COST = statistic_id(ADDRESS_ID, "grid_cost")
SOLAR = statistic_id(ADDRESS_ID, "solar_to_home")


def _overview(hours: list[tuple[datetime, float]], cost: float = 0.5) -> DailyOverview:
    return DailyOverview(
        energy_to_home=tuple(hours),
        solar_to_home=tuple((t, 0.25) for t, _ in hours),
        solar_export=tuple((t, 0.0) for t, _ in hours),
        grid_cost=tuple((t, cost) for t, _ in hours),
        currency="USD",
    )


def _client(
    overview=None,
    overview_error=None,
    capabilities=None,
    accounts=None,
    accounts_error=None,
    snapshot=None,
):
    instance = AsyncMock()
    instance.get_snapshot = AsyncMock(return_value=BatterySnapshot.from_json(snapshot or ON_GRID))
    instance.get_capabilities = AsyncMock(return_value=capabilities or LocationCapabilities())
    instance.get_daily_overview = AsyncMock(
        return_value=overview or _overview([]), side_effect=overview_error
    )
    instance.list_accounts = AsyncMock(return_value=accounts or [], side_effect=accounts_error)
    return instance


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client) -> None:
    entry.add_to_hass(hass)
    with patch(COORDINATOR_CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)


async def _rerun_energy(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data
    coordinator._energy_next = 0.0
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


BILLED = LocationCapabilities(billing=True)
ACCOUNTS = [
    Account(account_type="energy", currency="USD", last_bill=188.53),
    Account(account_type="battery", currency="USD", last_bill=19.0),
]
