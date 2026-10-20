"""Hourly statistics from the daily overview, and the read behind them."""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from energy_helpers import (
    ACCOUNTS,
    BILLED,
    COST,
    ENERGY,
    SOLAR,
    H,
    _client,
    _overview,
    _rerun_energy,
    _setup,
)
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.base_power.api import BasePowerError, LocationCapabilities
from custom_components.base_power.diagnostics import async_get_config_entry_diagnostics


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: Any
) -> Generator[None, None, None]:
    """The conftest fixture, with the recorder started before Home Assistant."""
    yield


async def _sums(hass: HomeAssistant, stat_id: str) -> list[tuple[datetime, float, float]]:
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        H[0] - timedelta(days=1),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        (datetime.fromtimestamp(r["start"], UTC), round(r["state"], 4), round(r["sum"], 4))
        for r in rows.get(stat_id, [])
    ]


async def test_hourly_energy_and_cost_reach_long_term_statistics(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(overview=_overview([(H[0], 1.0), (H[1], 2.0), (H[2], 3.0)]))
    await _setup(hass, mock_entry, client)
    assert await _sums(hass, ENERGY) == [(H[0], 1.0, 1.0), (H[1], 2.0, 3.0), (H[2], 3.0, 6.0)]
    assert await _sums(hass, COST) == [(H[0], 0.5, 0.5), (H[1], 0.5, 1.0), (H[2], 0.5, 1.5)]


async def test_a_revised_hour_is_rewritten_and_the_sum_carries_on(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(overview=_overview([(H[0], 1.0), (H[1], 2.0), (H[2], 3.0)]))
    await _setup(hass, mock_entry, client)
    # The next window starts an hour later, revises H2 and adds H3.
    client.get_daily_overview.return_value = _overview([(H[1], 2.0), (H[2], 3.5), (H[3], 4.0)])
    await _rerun_energy(hass, mock_entry)
    assert await _sums(hass, ENERGY) == [
        (H[0], 1.0, 1.0),
        (H[1], 2.0, 3.0),
        (H[2], 3.5, 6.5),
        (H[3], 4.0, 10.5),
    ]


async def test_a_gap_before_the_window_keeps_the_sum(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(overview=_overview([(H[0], 1.0), (H[1], 2.0)]))
    await _setup(hass, mock_entry, client)
    client.get_daily_overview.return_value = _overview([(H[4], 4.0), (H[5], 5.0)])
    await _rerun_energy(hass, mock_entry)
    assert await _sums(hass, ENERGY) == [
        (H[0], 1.0, 1.0),
        (H[1], 2.0, 3.0),
        (H[4], 4.0, 7.0),
        (H[5], 5.0, 12.0),
    ]


async def test_solar_series_exist_only_where_the_site_declares_solar(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry, _client(overview=_overview([(H[0], 1.0)])))
    assert await _sums(hass, SOLAR) == []


async def test_a_solar_site_gets_its_solar_series(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(
        overview=_overview([(H[0], 1.0)]), capabilities=LocationCapabilities(has_solar=True)
    )
    await _setup(hass, mock_entry, client)
    assert await _sums(hass, SOLAR) == [(H[0], 0.25, 0.25)]


async def test_a_failed_overview_is_logged_once_and_recovery_once(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(overview_error=BasePowerError("HTTP 503"))
    with caplog.at_level(logging.INFO, logger="custom_components.base_power"):
        await _setup(hass, mock_entry, client)
        await _rerun_energy(hass, mock_entry)
        assert caplog.text.count("Could not read Base Power energy data") == 1
        assert mock_entry.runtime_data.last_update_success
        client.get_daily_overview.side_effect = None
        client.get_daily_overview.return_value = _overview([(H[0], 1.0)])
        await _rerun_energy(hass, mock_entry)
        await _rerun_energy(hass, mock_entry)
    assert caplog.text.count("energy data is readable again") == 1
    assert await _sums(hass, ENERGY) == [(H[0], 1.0, 1.0)]


async def test_the_overview_is_not_reread_before_its_interval(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(overview=_overview([(H[0], 1.0)]))
    await _setup(hass, mock_entry, client)
    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert client.get_daily_overview.await_count == 1


async def test_diagnostics_name_the_accounts_but_not_the_amounts(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(
        overview=_overview([(H[0], 1.0), (H[1], 2.0)]), capabilities=BILLED, accounts=ACCOUNTS
    )
    await _setup(hass, mock_entry, client)
    report = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert report["billing_accounts"] == [
        {"type": "energy", "has_last_bill": True},
        {"type": "battery", "has_last_bill": True},
    ]
    assert report["energy"]["rows"] == {"energy_to_home": 2, "grid_cost": 2}
    assert report["energy"]["imported_at"] is not None
    assert "188.53" not in str(report)
