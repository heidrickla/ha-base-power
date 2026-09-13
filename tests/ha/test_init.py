"""Setup, the entities it creates, and what they do when data stops.

Most of this is about absence: an entity reading `unavailable` rather than a
confident zero, a solar sensor that is not created at all, a state of charge
legitimately unknown while on grid. A wrong guess there is invisible, because
a plausible number looks like a real one.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fixtures_const import ADDRESS_ID
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.base_power.api import (
    BasePowerAuthError,
    BasePowerError,
    BatterySnapshot,
    LocationCapabilities,
)
from custom_components.base_power.clerk import ClerkAuthError
from custom_components.base_power.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_TELEMETRY_UNAVAILABLE,
    silent_polls_before_issue,
)

COORDINATOR_CLIENT = "custom_components.base_power.coordinator.BasePowerClient"

# The real 02:41 on-grid response, same shape the pure tests pin.
ON_GRID = {
    "snapshot": {
        "wifi": {
            "status": "BATTERY_WIFI_CONNECTION_STATUS_CONNECTED",
            "ssid": "ExampleNet-5G",
        },
        "onGrid": {
            "observedAt": "2026-09-13T02:41:09.000Z",
            "powerFlow": {
                "fromGridKw": 2.9,
                "fromStorageKw": -0.3,
                "nonSolarToHomeKw": 2.6,
                "toHomeKw": 2.6,
            },
            "estimatedBackupHoursAtCurrentUsage": 17.2,
            "estimatedBackupHoursAt750Watts": 59.3,
        },
    }
}
OFFLINE = {"snapshot": {"telemetryUnavailable": {}}}


def _client(snapshot=None, capabilities=None, snapshot_error=None):
    instance = AsyncMock()
    instance.get_snapshot = AsyncMock(
        return_value=BatterySnapshot.from_json(snapshot if snapshot is not None else ON_GRID),
        side_effect=snapshot_error,
    )
    instance.get_capabilities = AsyncMock(
        return_value=capabilities if capabilities is not None else LocationCapabilities()
    )
    return instance


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client=None) -> None:
    entry.add_to_hass(hass)
    with patch(COORDINATOR_CLIENT, return_value=client or _client()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str):
    """Find an entity by its unique id, which is stable, unlike entity ids."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS_ID}_{key}"
    ) or registry.async_get_entity_id("binary_sensor", DOMAIN, f"{ADDRESS_ID}_{key}")
    assert entity_id is not None, f"no entity with unique id {ADDRESS_ID}_{key}"
    return hass.states.get(entity_id)


# --------------------------------------------------------------- setup/unload


async def test_the_entry_loads_and_creates_its_entities(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.LOADED
    assert _state(hass, "power_to_home").state == "2.6"
    assert _state(hass, "power_from_grid").state == "2.9"
    assert _state(hass, "battery_state").state == "on_grid"


async def test_a_negative_from_storage_reaches_the_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The signed-sensor decision, end to end at last. Charging is negative on
    the wire and must stay negative on the entity: one sensor covers both
    directions, and clamping it would silently erase charging."""
    await _setup(hass, mock_entry)
    assert _state(hass, "power_from_storage").state == "-0.3"


async def test_the_entry_unloads_cleanly(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.NOT_LOADED


async def test_the_poll_interval_option_is_applied(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_SCAN_INTERVAL: 90})
    with patch(COORDINATOR_CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_entry.runtime_data.update_interval == timedelta(seconds=90)


async def test_an_interval_below_the_floor_is_raised_to_it(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Defence in depth: the options schema already refuses it, but an entry
    edited by hand in .storage must not be able to hammer Base's service."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_SCAN_INTERVAL: 1})
    with patch(COORDINATOR_CLIENT, return_value=_client()):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_entry.runtime_data.update_interval == timedelta(seconds=15)


# ------------------------------------------------------- what is NOT created


async def test_no_solar_sensor_on_a_site_without_solar(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Base omits fromSolarKw entirely on a site with no solar, so the sensor
    would read unknown for ever. Absent is the honest answer."""
    await _setup(hass, mock_entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{ADDRESS_ID}_power_from_solar") is None


async def test_a_site_with_solar_gets_the_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_entry,
        client=_client(capabilities=LocationCapabilities(has_solar=True)),
    )
    assert _state(hass, "power_from_solar") is not None


async def test_state_of_charge_is_unknown_while_on_grid(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Correct, not a fault: Base publishes state of charge only off grid."""
    await _setup(hass, mock_entry)
    assert _state(hass, "state_of_charge").state == STATE_UNKNOWN


# -------------------------------------------------------- telemetry going away


async def test_no_telemetry_makes_values_unavailable_but_keeps_the_reason(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The core availability decision. Every value entity drops out, and
    battery_state deliberately does not - it is the one that says why."""
    await _setup(hass, mock_entry, client=_client(snapshot=OFFLINE))
    assert _state(hass, "power_to_home").state == STATE_UNAVAILABLE
    assert _state(hass, "power_from_grid").state == STATE_UNAVAILABLE
    assert _state(hass, "battery_state").state == "telemetry_unavailable"


async def test_a_failed_poll_makes_entities_unavailable(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry)
    assert _state(hass, "power_to_home").state == "2.6"

    client = _client(snapshot_error=BasePowerError("service down"))
    with patch(COORDINATOR_CLIENT, return_value=client):
        mock_entry.runtime_data.client = client
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
        await hass.async_block_till_done()

    assert _state(hass, "power_to_home").state == STATE_UNAVAILABLE


async def test_an_auth_failure_starts_reauthentication(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """ClerkAuthError must reach the user as a sign-in prompt. Anything else
    leaves the entry broken with nobody asked to fix it."""
    await _setup(hass, mock_entry)
    client = _client(snapshot_error=ClerkAuthError("credential dead"))
    mock_entry.runtime_data.client = client
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    flows = [
        f
        for f in hass.config_entries.flow.async_progress()
        if f["context"].get("source") == "reauth"
    ]
    assert flows, "an auth failure did not start a reauth flow"


async def test_an_api_auth_error_also_triggers_reauth_after_one_retry(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """A minted token can age out mid-flight, so the coordinator refreshes and
    retries once before troubling anyone. Both attempts failing is real."""
    await _setup(hass, mock_entry)
    client = _client(snapshot_error=BasePowerAuthError("401"))
    coordinator = mock_entry.runtime_data
    coordinator.client = client
    # The refresh between the two attempts is a real Clerk call; stub it so
    # this test measures the retry and not the network.
    coordinator._auth.async_refresh = AsyncMock(return_value="a-fresh-token")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    assert client.get_snapshot.await_count >= 2, "the retry did not happen"
    coordinator._auth.async_refresh.assert_awaited()


# ------------------------------------------------------------- repair issue


async def _poll_past_the_grace_period(hass: HomeAssistant) -> None:
    """Drive enough real polls to exhaust the grace period.

    The threshold is a DURATION converted to a poll count, so advancing the
    clock by thirty minutes in one jump is not the same thing: the coordinator
    polls once per fire, and one jump is one silent poll, not sixty. This
    fires at the actual 30 s interval, which is what the code counts.
    """
    polls = silent_polls_before_issue(DEFAULT_SCAN_INTERVAL) + 5
    start = dt_util.utcnow()
    for i in range(1, polls + 1):
        async_fire_time_changed(hass, start + DEFAULT_SCAN_INTERVAL * i)
        await hass.async_block_till_done()


async def test_the_repair_issue_waits_for_the_grace_period(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Thirty minutes, not one poll. A cellular battery is quiet between
    reports and a notice per gap would tell the owner to contact Base Support
    several times an hour."""
    await _setup(hass, mock_entry, client=_client(snapshot=OFFLINE))
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE) is None

    await _poll_past_the_grace_period(hass)

    assert registry.async_get_issue(DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE) is not None


async def test_telemetry_returning_clears_the_issue(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry, client=_client(snapshot=OFFLINE))
    await _poll_past_the_grace_period(hass)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE) is not None

    mock_entry.runtime_data.client = _client()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE) is None
    assert _state(hass, "power_to_home").state == "2.6"


# ------------------------------------------------------------- diagnostics


async def test_diagnostics_carry_no_credential_and_no_network_name(
    hass: HomeAssistant, hass_client, mock_entry: MockConfigEntry
) -> None:
    """The redaction promise, checked through the real diagnostics endpoint -
    the thing a user actually downloads and pastes into an issue - rather than
    the helper the pure tests reach directly."""
    import json

    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

    assert await async_setup_component(hass, "diagnostics", {})
    await _setup(hass, mock_entry)
    report = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)
    blob = json.dumps(report)

    assert "client_credential_value" not in blob
    assert "ExampleNet-5G" not in blob
    assert ADDRESS_ID not in blob
