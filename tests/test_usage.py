"""Hourly energy, cost and billing accounts, parsed from the live response shapes.

The overview fixture keeps the shape GetDailyOverview returned for a site
without solar: the hourly samples carry `energyToHomeKwh` only, and the cost
samples `gridEnergyCents` only.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from api import Account, DailyOverview

OVERVIEW = {
    "homePower": [{"intervalStart": "2026-10-19T02:00:00Z", "powerToHomeKw": 3.9886}],
    "hourlyEnergy": [
        {"intervalStart": "2026-10-19T02:00:00Z", "energyToHomeKwh": 3.6502},
        {"intervalStart": "2026-10-19T03:00:00Z", "energyToHomeKwh": 2.8633},
    ],
    "hourlyEnergyCosts": [
        {"intervalStart": "2026-10-19T02:00:00Z", "gridEnergyCents": 55.53226587187097},
        {"intervalStart": "2026-10-19T03:00:00Z", "gridEnergyCents": 33.46551816056989},
    ],
    "costCurrency": "USD",
    "energySourceMix": {"gridToHomeKwh": 82.675, "batteryToHomeKwh": 0.3726},
}

H02 = datetime(2026, 10, 19, 2, tzinfo=UTC)
H03 = datetime(2026, 10, 19, 3, tzinfo=UTC)


def test_the_live_overview_parses_to_hourly_series():
    o = DailyOverview.from_json(OVERVIEW)
    assert o.energy_to_home == ((H02, 3.6502), (H03, 2.8633))
    assert o.currency == "USD"


def test_cost_is_converted_from_cents():
    o = DailyOverview.from_json(OVERVIEW)
    assert [round(v, 4) for _, v in o.grid_cost] == [0.5553, 0.3347]


def test_a_field_absent_from_a_present_sample_is_zero():
    """proto3 JSON omits a zero, so a sample without solar fields produced none."""
    o = DailyOverview.from_json(OVERVIEW)
    assert o.solar_to_home == ((H02, 0.0), (H03, 0.0))
    assert o.solar_export == ((H02, 0.0), (H03, 0.0))


def test_samples_come_back_oldest_first():
    reversed_overview = {"hourlyEnergy": list(reversed(OVERVIEW["hourlyEnergy"]))}
    assert [t for t, _ in DailyOverview.from_json(reversed_overview).energy_to_home] == [H02, H03]


@pytest.mark.parametrize(
    "sample",
    [
        {"intervalStart": "2026-10-19T02:15:00Z", "energyToHomeKwh": 1.0},
        {"intervalStart": "2026-10-19T02:00:30Z", "energyToHomeKwh": 1.0},
        {"intervalStart": "not a time", "energyToHomeKwh": 1.0},
        {"energyToHomeKwh": 1.0},
        "a string",
    ],
)
def test_a_sample_off_the_hour_or_without_a_start_is_dropped(sample):
    assert DailyOverview.from_json({"hourlyEnergy": [sample]}).energy_to_home == ()


@pytest.mark.parametrize("value", [None, {}, "x", 3])
def test_a_missing_or_malformed_series_is_empty(value):
    o = DailyOverview.from_json({"hourlyEnergy": value, "hourlyEnergyCosts": value})
    assert o.energy_to_home == () and o.grid_cost == ()


def test_an_empty_overview_has_no_currency():
    assert DailyOverview.from_json({}).currency is None


ACCOUNTS = {
    "accounts": [
        {
            "accountType": "ACCOUNT_TYPE_ENERGY",
            "currency": "USD",
            "status": "ACCOUNT_STATUS_NO_PAYMENT_DUE",
            "lastBillAmountCents": "18853",
            "hasBillingHistory": True,
        },
        {
            "accountType": "ACCOUNT_TYPE_BATTERY",
            "currency": "USD",
            "status": "ACCOUNT_STATUS_NO_PAYMENT_DUE",
            "lastBillAmountCents": "1900",
            "hasBillingHistory": True,
        },
    ],
    "energyService": {"status": "ENERGY_SERVICE_STATUS_ACTIVE"},
}


def test_accounts_carry_the_last_bill_in_currency_units():
    accounts = Account.list_from_json(ACCOUNTS)
    assert accounts == [
        Account(account_type="energy", currency="USD", last_bill=188.53),
        Account(account_type="battery", currency="USD", last_bill=19.0),
    ]


def test_an_account_without_billing_history_has_no_last_bill():
    data = {"accounts": [{"accountType": "ACCOUNT_TYPE_ENERGY", "lastBillAmountCents": "500"}]}
    assert Account.list_from_json(data)[0].last_bill is None


def test_with_history_an_absent_amount_is_a_zero_bill():
    data = {"accounts": [{"accountType": "ACCOUNT_TYPE_BATTERY", "hasBillingHistory": True}]}
    assert Account.list_from_json(data)[0].last_bill == 0.0


@pytest.mark.parametrize(
    "raw",
    [
        {"accountType": "ACCOUNT_TYPE_UNSPECIFIED"},
        {"accountType": "ACCOUNT_TYPE_SOMETHING_NEW"},
        {},
        "a string",
    ],
)
def test_unfamiliar_accounts_are_skipped(raw):
    assert Account.list_from_json({"accounts": [raw]}) == []


def test_no_accounts_is_an_empty_list():
    assert Account.list_from_json({}) == []
