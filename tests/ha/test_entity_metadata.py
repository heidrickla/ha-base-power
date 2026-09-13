"""Names, units and categories, against a running Home Assistant.

The README's entity table promises a unit and a display name for each row, and
those come from entity descriptions and translation files that nothing else
compares. A wrong unit here is worse than a missing one: the value still
renders, the graph still draws, and it is simply wrong by a factor.

Read off a real registry and real states rather than off the descriptions,
because the descriptions are the thing under test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fixtures_const import ADDRESS_ID, CREDENTIAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_init import COORDINATOR_CLIENT, _client

from custom_components.base_power.api import LocationCapabilities
from custom_components.base_power.const import CONF_ADDRESS_ID, CONF_CLIENT_JWT, DOMAIN

# suffix -> (display name, unit, device class, state class)
EXPECTED = {
    "binary_sensor.base_power_grid_outage": ("Grid outage", None, "problem", None),
    "binary_sensor.base_power_running_off_grid": (
        "Running off grid",
        None,
        "problem",
        None,
    ),
    "sensor.base_power_battery_state": ("Battery state", None, "enum", None),
    "sensor.base_power_estimated_backup_time": (
        "Estimated backup time",
        "h",
        "duration",
        "measurement",
    ),
    "sensor.base_power_power_from_grid": (
        "Power from grid",
        "kW",
        "power",
        "measurement",
    ),
    "sensor.base_power_power_from_solar": (
        "Power from solar",
        "kW",
        "power",
        "measurement",
    ),
    "sensor.base_power_power_from_storage": (
        "Power from storage",
        "kW",
        "power",
        "measurement",
    ),
    "sensor.base_power_power_to_home": ("Power to home", "kW", "power", "measurement"),
    "sensor.base_power_state_of_charge": (
        "State of charge",
        "%",
        "battery",
        "measurement",
    ),
    "sensor.base_power_stored_energy": (
        "Stored energy",
        "kWh",
        "energy_storage",
        "measurement",
    ),
    # Diagnostic and disabled by default, so it is in the registry with no
    # state. Its units are asserted nowhere else for that reason.
    "sensor.base_power_battery_wi_fi_network": (
        "Battery Wi-Fi network",
        None,
        None,
        None,
    ),
}


@pytest.fixture
async def loaded(hass: HomeAssistant) -> MockConfigEntry:
    """A site declaring solar, so every documented entity exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Base Power",
        unique_id=ADDRESS_ID,
        data={CONF_CLIENT_JWT: CREDENTIAL, CONF_ADDRESS_ID: ADDRESS_ID},
    )
    entry.add_to_hass(hass)
    with patch(
        COORDINATOR_CLIENT,
        return_value=_client(capabilities=LocationCapabilities(has_solar=True)),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_display_names_match_the_readme_table(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    names = {
        e.entity_id: e.original_name
        for e in er.async_entries_for_config_entry(registry, loaded.entry_id)
    }
    assert names == {k: v[0] for k, v in EXPECTED.items()}


# The Wi-Fi sensor is excluded rather than skipped inside the test: a case
# that returns early asserts nothing while still reporting as passed.
WITH_STATE = sorted(k for k in EXPECTED if not k.endswith("battery_wi_fi_network"))


@pytest.mark.parametrize("entity_id", WITH_STATE)
async def test_units_and_classes(
    hass: HomeAssistant, loaded: MockConfigEntry, entity_id: str
) -> None:
    """A power sensor in W instead of kW reads plausibly and is 1000x wrong."""
    _, unit, device_class, state_class = EXPECTED[entity_id]
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    assert state.attributes.get("unit_of_measurement") == unit
    assert state.attributes.get("device_class") == device_class
    assert state.attributes.get("state_class") == state_class


async def test_the_wifi_sensor_is_diagnostic_and_off_by_default(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """It names a household network and diagnoses nothing about the battery's
    reporting, so it must not appear for someone who did not ask for it."""
    registry = er.async_get(hass)
    entry = registry.async_get("sensor.base_power_battery_wi_fi_network")
    assert entry is not None
    assert entry.entity_category is not None
    assert entry.entity_category.value == "diagnostic"
    assert entry.disabled_by is not None
    assert hass.states.get("sensor.base_power_battery_wi_fi_network") is None


async def test_stored_energy_is_the_documented_derivation(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    """The README says backup-hours-at-750 W times 0.75 kW. The fixture gives
    59.3 h, so the entity must read 44.47 kWh and not the raw hours."""
    state = hass.states.get("sensor.base_power_stored_energy")
    assert state is not None
    assert float(state.state) == pytest.approx(59.3 * 0.75, abs=0.01)
