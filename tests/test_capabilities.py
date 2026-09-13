"""Capability parsing, against the real GetLocation response shape.

proto3 omits false booleans, so a capability the site does not have is not
`false` on the wire - it is absent. That is the whole reason these default to
False and the parser uses `bool(...)` on a possibly-missing key: reading an
absent `hasSolar` as anything but "no solar" would create a sensor that can
never have a value.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from api import LocationCapabilities

# Captured from the live API on 2026-09-13. The site has no solar, and note
# what that looks like: no `hasSolar` key at all, not `"hasSolar": false`.
LIVE_NO_SOLAR = {
    "location": {
        "summary": {"addressId": "xxxxxxxx", "status": "LOCATION_STATUS_ACTIVE"},
        "energy": {"active": {}},
        "battery": {
            "onsite": {
                "capabilities": {
                    "telemetry": True,
                    "automaticBackup": True,
                    "wifiManagement": True,
                }
            }
        },
        "capabilities": {"billing": True},
    }
}


def test_the_live_response_reports_no_solar_and_the_battery_capabilities():
    caps = LocationCapabilities.from_json(LIVE_NO_SOLAR)
    assert caps.has_solar is False, "absent hasSolar must read as no solar"
    assert caps.billing is True
    assert caps.telemetry is True
    assert caps.automatic_backup is True
    assert caps.wifi_management is True


def test_a_site_with_solar_is_recognised():
    """The gate has to work in both directions - other accounts have solar."""
    data = {"location": {"capabilities": {"hasSolar": True, "billing": True}}}
    caps = LocationCapabilities.from_json(data)
    assert caps.has_solar is True
    assert caps.billing is True


def test_an_empty_response_declares_nothing():
    """The conservative default: claim no capability nobody stated."""
    caps = LocationCapabilities.from_json({})
    assert caps == LocationCapabilities()
    assert caps.has_solar is False
    assert caps.telemetry is False


def test_the_inner_location_object_is_accepted_too():
    caps = LocationCapabilities.from_json(LIVE_NO_SOLAR["location"])
    assert caps.billing is True
    assert caps.telemetry is True


def test_a_battery_that_is_not_onsite_declares_no_battery_capabilities():
    """`onsite` is one variant of the battery state; anything else leaves the
    battery capabilities unset rather than inventing them."""
    data = {"location": {"battery": {"notInstalled": {}}, "capabilities": {}}}
    caps = LocationCapabilities.from_json(data)
    assert caps.telemetry is False
    assert caps.automatic_backup is False


def test_a_capabilities_field_of_the_wrong_type_declares_nothing():
    """This parser decides whether the solar sensor exists at all, and it runs
    once at setup. A shape it did not expect has to come out as "declares
    nothing" - a setup that raises here is a setup that never completes."""
    data = {"location": {"battery": "unavailable", "capabilities": ["billing"]}}
    caps = LocationCapabilities.from_json(data)
    assert caps.has_solar is False
    assert caps.billing is False
    assert caps.telemetry is False
