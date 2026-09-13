"""Diagnostics for Base Power.

What a report needs to be useful: which contract the API answered with, what
the coordinator believes, and why an entity is missing or unavailable. What it
must never contain: the credential, or where the user lives.

Redaction here is deliberate rather than reflexive. The client credential and
any minted session JWT are live secrets and are not included at all - not
truncated, not masked, absent. The street address and the battery's Wi-Fi
SSID identify a household, and nothing about diagnosing this integration
needs them.

The address id gets a middle answer. It is opaque, but it is a stable
identifier of one person's home and this output lands in bug reports, so the
raw value does not appear. What appears is a short digest of it, which is
enough to tell two entries apart and to correlate a report with a log line,
and is not the identifier itself.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS_ID, CONF_CLIENT_JWT
from .coordinator import BasePowerConfigEntry


def _fingerprint(value: str | None) -> str | None:
    """A short stable digest: correlation without the identifier."""
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:12]


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
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_exception": str(coordinator.last_exception)
            if coordinator.last_exception
            else None,
        },
        # What the site declares it has. This is the answer to "why is there
        # no solar sensor" - the commonest question this integration will be
        # asked, and one that looks like a bug until you see the capability.
        "capabilities": asdict(coordinator.capabilities),
        "snapshot": _snapshot_diagnostics(snapshot),
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
