"""Diagnostics for Base Power.

A report carries which contract the API answered with, what the coordinator
believes, and why an entity is missing or unavailable.

The client credential and any minted session JWT are absent, not truncated or
masked. The street address and the battery's Wi-Fi SSID identify a household
and diagnose nothing, so they are absent too.

The address id appears only as a short digest. It is opaque but stable, this
output lands in bug reports, and a digest is enough to tell two entries apart
and correlate a report with a log line.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS_ID, CONF_CLIENT_JWT, address_fingerprint
from .coordinator import BasePowerConfigEntry


def _fingerprint(value: str | None) -> str | None:
    """A short stable digest: correlation without the identifier."""
    return address_fingerprint(value) if value else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BasePowerConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    snapshot = coordinator.snapshot

    return {
        "entry": {
            # The credential is deliberately absent rather than redacted to a
            # placeholder: there is no version of it that belongs in a file
            # users paste into issue trackers. Same for the address id - a
            # digest of it correlates reports without republishing it.
            "address_id_fingerprint": _fingerprint(entry.data.get(CONF_ADDRESS_ID)),
            "has_credential": bool(entry.data.get(CONF_CLIENT_JWT)),
            "options": dict(entry.options),
        },
        "coordinator": {
            # The poll: whether it is working, and how often it runs. A
            # report where this is False explains every unavailable entity at
            # once.
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
            "last_exception": str(coordinator.last_exception)
            if coordinator.last_exception
            else None,
        },
        # What the site declares it has. This is the answer to "why is there
        # no solar sensor" - the commonest question this integration will be
        # asked, and one that looks like a bug until you see the capability.
        # When Base's sign-in session started, was last used and ends. Times only:
        # they answer "why was I asked to sign in again" without the credential.
        "sign_in_session": coordinator.sign_in_times,
        "capabilities": asdict(coordinator.capabilities),
        "snapshot": _snapshot_diagnostics(snapshot),
        # Whether the hourly series reached long-term statistics, and how many
        # hours each carried.
        "energy": {
            "imported_at": (
                coordinator.energy_imported_at.isoformat()
                if coordinator.energy_imported_at
                else None
            ),
            "rows": coordinator.energy_rows,
        },
        # Which accounts exist, not what they owe.
        "billing_accounts": [
            {"type": a.account_type, "has_last_bill": a.last_bill is not None}
            for a in coordinator.accounts
        ],
    }


def _snapshot_diagnostics(snapshot: Any) -> dict[str, Any] | None:
    """The battery snapshot, minus what identifies the household.

    `raw` is not included: it is the whole API response and would carry the
    Wi-Fi SSID straight back in.
    """
    if snapshot is None:
        return None
    return {
        "state": snapshot.state,
        "telemetry_available": snapshot.telemetry_available,
        "is_off_grid": snapshot.is_off_grid,
        "is_grid_outage": snapshot.is_grid_outage,
        "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
        # None and 0.0 mean different things here, so both survive the report:
        # a null is a field the API omitted, which is the usual explanation
        # for an entity reading unknown.
        "state_of_energy_percent": snapshot.state_of_energy_percent,
        "power_flow": asdict(snapshot.power_flow),
        "estimated_backup_hours_at_current_usage": (
            snapshot.estimated_backup_hours_at_current_usage
        ),
        "estimated_backup_hours_at_750_watts": snapshot.estimated_backup_hours_at_750_watts,
        "overcurrent_limit_kw": snapshot.overcurrent_limit_kw,
        # The connection status is a useful signal; the network's name is not
        # this integration's business to publish.
        "wifi_status": snapshot.wifi_status,
        "wifi_ssid_present": snapshot.wifi_ssid is not None,
    }
