"""Bill sensors, the overview without a recorder, and pack-size independence."""

from __future__ import annotations

import pytest
from energy_helpers import ACCOUNTS, BILLED, H, _client, _overview, _setup
from fixtures_const import ADDRESS_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_init import OFFLINE

from custom_components.base_power.api import Account, BasePowerError
from custom_components.base_power.const import DOMAIN


async def test_without_the_recorder_the_overview_is_not_read(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(overview=_overview([(H[0], 1.0)]))
    await _setup(hass, mock_entry, client)
    client.get_daily_overview.assert_not_awaited()


# -------------------------------------------------------------- bill sensors


def _bill(hass: HomeAssistant, account_type: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS_ID}_last_bill_{account_type}"
    )
    return None if entity_id is None else hass.states.get(entity_id)


async def test_each_account_gets_a_last_bill_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry, _client(capabilities=BILLED, accounts=ACCOUNTS))
    energy, battery = _bill(hass, "energy"), _bill(hass, "battery")
    assert energy.state == "188.53"
    assert energy.attributes["unit_of_measurement"] == "USD"
    assert energy.attributes["device_class"] == "monetary"
    assert battery.state == "19.0"


async def test_no_bill_sensors_without_the_billing_capability(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(accounts=ACCOUNTS)
    await _setup(hass, mock_entry, client)
    assert _bill(hass, "energy") is None
    client.list_accounts.assert_not_awaited()


async def test_unreadable_accounts_cost_the_bill_sensors_only(
    hass: HomeAssistant, mock_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client(capabilities=BILLED, accounts_error=BasePowerError("HTTP 500"))
    await _setup(hass, mock_entry, client)
    assert _bill(hass, "energy") is None
    assert "Could not read Base Power billing data" in caplog.text
    assert hass.states.get("sensor.home_power_to_home").state == "2.6"


async def test_a_bill_stays_available_while_telemetry_is_not(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(
        hass, mock_entry, _client(capabilities=BILLED, accounts=ACCOUNTS, snapshot=OFFLINE)
    )
    assert hass.states.get("sensor.home_power_to_home").state == "unavailable"
    assert _bill(hass, "energy").state == "188.53"


async def test_a_billing_refresh_updates_the_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    client = _client(capabilities=BILLED, accounts=ACCOUNTS)
    await _setup(hass, mock_entry, client)
    client.list_accounts.return_value = [
        Account(account_type="energy", currency="USD", last_bill=201.0)
    ]
    coordinator = mock_entry.runtime_data
    coordinator._billing_next = 0.0
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _bill(hass, "energy").state == "201.0"
    # The battery account vanished from the response: its sensor says so.
    assert _bill(hass, "battery").state == "unavailable"


# ---------------------------------------------------------------- Base Core


async def test_stored_energy_assumes_no_pack_size(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """A two-unit Base Core holds 78.4 kWh; the derivation has no ceiling to hit."""
    core = {
        "snapshot": {
            "onGrid": {
                "observedAt": "2026-10-19T02:00:00Z",
                "estimatedBackupHoursAt750Watts": 104.5333,
            }
        }
    }
    await _setup(hass, mock_entry, _client(snapshot=core))
    assert hass.states.get("sensor.home_stored_energy").state == "78.4"
