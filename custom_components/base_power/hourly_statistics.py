"""Hourly energy and cost from GetDailyOverview, imported as long-term statistics.

The overview carries the last 24 completed hours. Each import rewrites every
hour it carries, with the running sum carried on from the last stored hour
before them, so an hour Base revises is corrected on the next import and an
outage of Home Assistant leaves a gap without breaking the sum. A response
older than the newest stored hour is not written, since the stored hours after
it would keep sums from the window it replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from homeassistant.util.unit_conversion import EnergyConverter

from .api import DailyOverview
from .const import DOMAIN, address_fingerprint

# How far back to look for the hour before the window. Longer than any
# plausible Home Assistant outage; a longer gap starts the sum from zero.
_BASELINE_LOOKBACK = timedelta(days=30)


@dataclass(frozen=True)
class Series:
    key: str
    name: str
    unit: str | None
    unit_class: str | None
    solar: bool = False


SERIES: tuple[Series, ...] = (
    Series(
        "energy_to_home", "home energy", UnitOfEnergy.KILO_WATT_HOUR, EnergyConverter.UNIT_CLASS
    ),
    Series(
        "solar_to_home",
        "solar energy to home",
        UnitOfEnergy.KILO_WATT_HOUR,
        EnergyConverter.UNIT_CLASS,
        solar=True,
    ),
    Series(
        "solar_export",
        "solar export",
        UnitOfEnergy.KILO_WATT_HOUR,
        EnergyConverter.UNIT_CLASS,
        solar=True,
    ),
    # Cost carries no unit, as in core's utility integrations: the Energy
    # dashboard prices it in the instance's configured currency, so it is
    # written only when Base prices in that currency.
    Series("grid_cost", "grid energy cost", None, None),
)


def statistic_id(address_id: str, key: str) -> str:
    """The address digest diagnostics show, so ids differing only in case or
    punctuation stay apart and the id itself is not republished."""
    return f"{DOMAIN}:{address_fingerprint(address_id)}_{key}"


async def _baseline(
    hass: HomeAssistant, stat_id: str, first: datetime, last: datetime
) -> float | None:
    """The running sum at the last stored hour before `first`, 0.0 if none.

    None when a stored hour is newer than `last`. Writes still queued, from an
    import made just before a reload among them, are committed before reading.
    """
    recorder = get_instance(hass)
    if future := recorder.async_get_commit_future():
        await future
    newest = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    rows = newest.get(stat_id) or []
    if not rows:
        return 0.0
    if rows[0]["start"] > last.timestamp():
        return None
    if rows[0]["start"] < first.timestamp():
        return float(rows[0].get("sum") or 0.0)
    before = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        first - _BASELINE_LOOKBACK,
        first,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    prior = before.get(stat_id) or []
    return float(prior[-1].get("sum") or 0.0) if prior else 0.0


async def async_import(
    hass: HomeAssistant,
    address_id: str,
    title: str,
    overview: DailyOverview,
    include_solar: bool,
) -> dict[str, int]:
    """Write the overview's hours; returns rows written per series."""
    written: dict[str, int] = {}
    for series in SERIES:
        if series.solar and not include_solar:
            continue
        if series.unit is None and overview.currency != hass.config.currency:
            continue
        samples: tuple[tuple[datetime, float], ...] = getattr(overview, series.key)
        if not samples:
            continue
        stat_id = statistic_id(address_id, series.key)
        baseline = await _baseline(hass, stat_id, samples[0][0], samples[-1][0])
        if baseline is None:
            continue
        total = baseline
        rows: list[StatisticData] = []
        for start, value in samples:
            total += value
            rows.append(StatisticData(start=start, state=value, sum=total))
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{title} {series.name}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=series.unit_class,
            unit_of_measurement=series.unit,
        )
        async_add_external_statistics(hass, metadata, rows)
        written[series.key] = len(rows)
    return written
