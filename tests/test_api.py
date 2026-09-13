"""Shape tests for the recovered contract.

No network and no token: every response here is built to the field names in
`proto/`, so these pin what the code does with the contract as recovered. They
cannot prove the contract itself is right - nothing has been seen on the wire -
and the cases that matter most are the ones where a wrong guess would be
invisible: a missing power value reading as a confident 0.0, or an unfamiliar
snapshot variant taking the integration down.
"""

from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from api import (
    BasePowerAuthError,
    BasePowerClient,
    BasePowerError,
    BatterySnapshot,
    Location,
    PowerFlow,
    _error_for,
    _samples,
)


def snapshot(variant: str, **fields) -> dict:
    """A GetBatterySnapshotResponse with one variant of the union set."""
    return {"snapshot": {variant: fields}}


# ------------------------------------------------------------ the state union


def test_on_grid_is_the_normal_state():
    s = BatterySnapshot.from_json(
        snapshot(
            "onGrid",
            observedAt="2026-09-13T01:02:03Z",
            powerFlow={"fromGridKw": 1.5, "toHomeKw": 1.5},
            estimatedBackupHoursAtCurrentUsage=12.5,
        )
    )
    assert s.state == "on_grid"
    assert s.is_off_grid is False
    assert s.is_grid_outage is False
    assert s.telemetry_available is True
    assert s.power_flow.from_grid_kw == 1.5
    assert s.estimated_backup_hours_at_current_usage == 12.5
    assert s.observed_at is not None
    assert s.observed_at.tzinfo is not None
    assert s.observed_at.astimezone(UTC).hour == 1


def test_the_on_grid_variant_carries_no_state_of_charge():
    """The descriptor gives state_of_energy_percent to every OFF-grid variant
    and not to on_grid. That is odd enough to pin: if a live response proves
    otherwise this test is the thing that should fail and be corrected."""
    s = BatterySnapshot.from_json(snapshot("onGrid", observedAt="2026-09-13T01:02:03Z"))
    assert s.state_of_energy_percent is None


def test_grid_outage_is_the_headline_condition():
    s = BatterySnapshot.from_json(
        snapshot(
            "offGridOutage",
            observedAt="2026-09-13T01:02:03Z",
            stateOfEnergyPercent=87,
            powerFlow={"fromStorageKw": 2.0, "toHomeKw": 2.0},
            estimatedBackupHoursAtCurrentUsage=6.25,
        )
    )
    assert s.state == "off_grid_outage"
    assert s.is_grid_outage is True
    assert s.is_off_grid is True
    assert s.state_of_energy_percent == 87
    assert s.power_flow.from_storage_kw == 2.0


@pytest.mark.parametrize(
    "variant,state",
    [
        ("offGridNoHomePower", "off_grid_no_home_power"),
        ("offGridOvercurrent", "off_grid_overcurrent"),
        ("offGridOvercurrentStandby", "off_grid_overcurrent_standby"),
    ],
)
def test_the_other_off_grid_variants_are_off_grid_but_not_an_outage(variant, state):
    s = BatterySnapshot.from_json(snapshot(variant, stateOfEnergyPercent=50))
    assert s.state == state
    assert s.is_off_grid is True
    assert s.is_grid_outage is False, "only offGridOutage is the grid being down"


def test_overcurrent_carries_its_limit():
    s = BatterySnapshot.from_json(
        snapshot("offGridOvercurrent", stateOfEnergyPercent=40, overcurrentLimitKw=7.6)
    )
    assert s.overcurrent_limit_kw == 7.6


def test_telemetry_unavailable_is_reported_not_hidden():
    s = BatterySnapshot.from_json(snapshot("telemetryUnavailable"))
    assert s.state == "telemetry_unavailable"
    assert s.telemetry_available is False
    assert s.state_of_energy_percent is None


def test_an_unknown_variant_degrades_rather_than_raising():
    """A shape never seen live must not take the integration down."""
    s = BatterySnapshot.from_json({"snapshot": {"someFutureState": {"observedAt": "x"}}})
    assert s.state == "unknown"
    assert s.telemetry_available is False


def test_an_empty_response_is_unknown():
    assert BatterySnapshot.from_json({}).state == "unknown"


def test_the_inner_object_is_accepted_too():
    """Callers hand either the response or the snapshot; both must work."""
    inner = {"onGrid": {"powerFlow": {"toHomeKw": 1.0}}}
    assert BatterySnapshot.from_json(inner).state == "on_grid"


def test_wifi_rides_alongside_the_variant():
    data = {"snapshot": {"onGrid": {}, "wifi": {"ssid": "ExampleNet", "status": "CONNECTED"}}}
    s = BatterySnapshot.from_json(data)
    assert s.wifi_ssid == "ExampleNet"
    assert s.wifi_status == "CONNECTED"


# ------------------------------------------------- the live response, confirmed

# Captured from the real API on 2026-09-13 (GetSnapshot, on-grid, no solar).
# Field names and shapes are verbatim; the wifi SSID and the address are not
# reproduced because they are not needed to pin the contract.
LIVE_ON_GRID = {
    "snapshot": {
        # CONNECTED, straight off the wire. Worth noting because docs/API.md
        # spent a draft asserting this capture read NOT_CONNECTED, reasoning
        # from string length instead of reading the fixture sitting here.
        "wifi": {
            "status": "BATTERY_WIFI_CONNECTION_STATUS_CONNECTED",
            "observedAt": "2026-09-13T02:41:09.000Z",
        },
        "onGrid": {
            "observedAt": "2026-09-13T02:41:09.000Z",
            "powerFlow": {
                "fromGridKw": 2.9,
                "fromStorageKw": -0.3,
                "nonSolarToHomeKw": 2.6,
                "toHomeKw": 2.6,
            },
            "estimatedBackupHoursAtCurrentUsage": 17.227362471449034,
            "estimatedBackupHoursAt750Watts": 59.333333333333336,
        },
    }
}


def test_the_real_on_grid_response_parses():
    s = BatterySnapshot.from_json(LIVE_ON_GRID)
    assert s.state == "on_grid"
    assert s.is_grid_outage is False
    assert s.telemetry_available is True
    assert s.power_flow.from_grid_kw == 2.9
    assert s.power_flow.to_home_kw == 2.6
    assert s.estimated_backup_hours_at_current_usage == pytest.approx(17.2273624)
    assert s.estimated_backup_hours_at_750_watts == pytest.approx(59.3333333)
    assert s.wifi_status == "BATTERY_WIFI_CONNECTION_STATUS_CONNECTED"


def test_on_grid_really_does_omit_state_of_charge():
    """Confirmed against the live API, not just the descriptor: there is no
    stateOfEnergyPercent while on grid. A state-of-charge sensor therefore
    cannot be fed from GetSnapshot in the normal case, which is a design
    constraint rather than a bug to code around."""
    assert BatterySnapshot.from_json(LIVE_ON_GRID).state_of_energy_percent is None


def test_a_negative_from_storage_means_charging():
    """The live reading was -0.3 kW: sign carries direction. It must survive
    parsing rather than being clamped, because charging and discharging are
    the same sensor in Home Assistant."""
    assert BatterySnapshot.from_json(LIVE_ON_GRID).power_flow.from_storage_kw == -0.3


def test_absent_solar_is_none_in_the_real_response():
    """The live response omitted fromSolarKw entirely on a site with no
    solar. Reporting 0.0 there would be a measurement nobody made."""
    assert BatterySnapshot.from_json(LIVE_ON_GRID).power_flow.from_solar_kw is None


# ------------------------------------------------------------------ power flow


def test_an_absent_power_value_is_none_not_zero():
    """The distinction the sensors depend on: "not reported" is not "zero".
    A power sensor reading a confident 0.0 for a value the panel never sent
    would be indistinguishable from real, and wrong."""
    pf = PowerFlow.from_json({"toHomeKw": 2.0})
    assert pf.to_home_kw == 2.0
    assert pf.from_solar_kw is None
    assert pf.from_grid_kw is None


def test_a_real_zero_survives():
    pf = PowerFlow.from_json({"fromSolarKw": 0.0})
    assert pf.from_solar_kw == 0.0


def test_a_double_sent_as_a_string_is_read():
    """Connect JSON may render a double as a string."""
    assert PowerFlow.from_json({"toHomeKw": "1.25"}).to_home_kw == 1.25


def test_a_missing_power_flow_is_an_empty_one():
    s = BatterySnapshot.from_json(snapshot("onGrid"))
    assert s.power_flow.to_home_kw is None


def test_a_malformed_timestamp_does_not_cost_the_reading():
    s = BatterySnapshot.from_json(
        snapshot("offGridOutage", observedAt="not-a-time", stateOfEnergyPercent=42)
    )
    assert s.observed_at is None
    assert s.state_of_energy_percent == 42


@pytest.mark.parametrize("wrong", ["a string", 7, [], True])
def test_a_nested_object_of_the_wrong_type_does_not_raise(wrong):
    """`wifi` is read for two diagnostic fields nobody would miss. It must not
    be able to take the reading beside it down, so anything that is not an
    object is treated as absent rather than walked into."""
    data = {"snapshot": {"onGrid": {"powerFlow": {"toHomeKw": 1.5}}, "wifi": wrong}}
    s = BatterySnapshot.from_json(data)
    assert s.state == "on_grid"
    assert s.power_flow.to_home_kw == 1.5
    assert s.wifi_ssid is None


# -------------------------------------------------------------------- locations


def test_locations_yield_the_address_id_everything_else_needs():
    data = {
        "locations": [
            {"summary": {"addressId": "addr_1", "name": "Home"}},
            {"addressId": "addr_2"},
            {"summary": {}},  # no id at all - skipped rather than a blank entity
        ]
    }
    locs = Location.list_from_json(data)
    assert [locale.address_id for locale in locs] == ["addr_1", "addr_2"]
    assert locs[0].name == "Home"


def test_no_locations_is_an_empty_list():
    assert Location.list_from_json({}) == []


# ------------------------------------------------------------------- usage samples


@pytest.mark.parametrize(
    "data",
    [{}, {"samples": None}, {"samples": "abc"}, {"samples": 7}, {"samples": {}}],
    ids=["absent", "null", "a-string", "a-number", "an-object"],
)
def test_a_usage_response_without_a_sample_list_is_empty_not_a_crash(data):
    """`list(data.get("samples") or [])` raised TypeError on the number and,
    worse, turned the string into three one-character entries handed back past
    a signature promising dicts. mypy sees none of this: the value is Any, and
    Any is iterable as far as it knows."""
    assert _samples(data) == []


def test_non_dict_entries_are_dropped_rather_than_returned():
    assert _samples({"samples": [{"w": 1}, "junk", None, {"w": 2}]}) == [{"w": 1}, {"w": 2}]


# ----------------------------------------------------------------- error mapping


@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_statuses_are_auth_errors(status):
    assert isinstance(_error_for(status, {}), BasePowerAuthError)


@pytest.mark.parametrize("code", ["unauthenticated", "permission_denied"])
def test_connect_auth_codes_are_auth_errors(code):
    """Connect reports its own code in the body alongside an HTTP status, and
    a short-lived token expiring is the ordinary case, not a fault."""
    err = _error_for(400, {"code": code, "message": "token expired"})
    assert isinstance(err, BasePowerAuthError)
    assert "token expired" in str(err)


def test_other_failures_are_plain_errors():
    err = _error_for(500, {"code": "internal", "message": "boom"})
    assert isinstance(err, BasePowerError)
    assert not isinstance(err, BasePowerAuthError)


def test_a_non_json_body_still_produces_a_usable_error():
    assert "HTTP 502" in str(_error_for(502, "<html>gateway</html>"))


# ------------------------------------------------------------------- url shape


def test_the_url_is_the_connect_shape():
    c = BasePowerClient(session=None, token_provider=None)
    assert (
        c.url("BatteryService", "GetSnapshot")
        == "https://dashboard.baseapis.net/dashboard.mobile.v2.BatteryService/GetSnapshot"
    )


def test_a_trailing_slash_on_the_host_does_not_double_up():
    c = BasePowerClient(session=None, token_provider=None, host="https://example.test/")
    assert (
        c.url("UserService", "GetCurrentUser")
        == "https://example.test/dashboard.mobile.v2.UserService/GetCurrentUser"
    )
