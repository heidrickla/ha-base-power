"""What diagnostics must never leak.

The report builder itself imports homeassistant and cannot run on this host,
so what is pinned here is the part that matters and is pure: the snapshot
section, which is where the household-identifying fields live. If the
redaction rule is ever loosened, this is what should fail.

The rule, stated once: the credential is absent rather than masked, and the
Wi-Fi SSID and the raw response are never included - `raw` is the entire API
body and would carry the SSID straight back in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from api import BatterySnapshot  # noqa: E402

DIAG = Path(__file__).resolve().parents[1] / "custom_components" / "base_power" / "diagnostics.py"
sys.path.insert(0, str(DIAG.parent))


def _snapshot_diagnostics(snapshot):
    """Import just the pure helper, without pulling in homeassistant.

    diagnostics.py imports homeassistant at module scope, which will not
    install on Windows, so the function is loaded from source rather than
    imported. Reading it out of the shipped file rather than copying it is
    the point: a copy would drift and the test would pass while the real
    redaction regressed.
    """
    src = DIAG.read_text(encoding="utf-8")
    start = src.index("def _snapshot_diagnostics")
    namespace: dict = {"Any": object, "asdict": __import__("dataclasses").asdict}
    exec(compile(src[start:], str(DIAG), "exec"), namespace)  # noqa: S102
    return namespace["_snapshot_diagnostics"](snapshot)


SNAPSHOT = BatterySnapshot.from_json(
    {
        "snapshot": {
            "wifi": {"ssid": "ExampleNet-5G", "status": "BATTERY_WIFI_CONNECTION_STATUS_CONNECTED"},
            "onGrid": {
                "observedAt": "2026-09-13T02:41:09.000Z",
                "powerFlow": {"fromGridKw": 2.9, "fromStorageKw": -0.3, "toHomeKw": 2.6},
                "estimatedBackupHoursAtCurrentUsage": 17.2,
                "estimatedBackupHoursAt750Watts": 59.3,
            },
        }
    }
)


def test_the_wifi_ssid_never_appears():
    """A network name identifies a household and diagnoses nothing."""
    report = _snapshot_diagnostics(SNAPSHOT)
    assert "ExampleNet-5G" not in json.dumps(report)
    # but whether it is connected is genuinely useful, so that survives
    assert report["wifi_status"] == "BATTERY_WIFI_CONNECTION_STATUS_CONNECTED"
    assert report["wifi_ssid_present"] is True


def test_there_is_no_raw_response_to_leak():
    """Stronger than redacting it: `BatterySnapshot` never keeps the raw API
    body in the first place, so no future edit to the report can reintroduce
    the SSID or the address through it. Pinned in both directions - the
    dataclass has no `raw`, and the report has no such key."""
    assert not hasattr(SNAPSHOT, "raw")
    report = _snapshot_diagnostics(SNAPSHOT)
    assert "raw" not in report


def test_the_useful_fields_do_survive():
    """Redaction that removed the diagnosis would be its own failure."""
    report = _snapshot_diagnostics(SNAPSHOT)
    assert report["state"] == "on_grid"
    assert report["is_grid_outage"] is False
    assert report["power_flow"]["from_storage_kw"] == -0.3
    assert report["estimated_backup_hours_at_750_watts"] == 59.3


def test_an_omitted_field_stays_null_rather_than_zero():
    """The commonest question a report answers is 'why is this unknown', and
    null vs 0.0 is the whole answer. This site has no solar."""
    report = _snapshot_diagnostics(SNAPSHOT)
    assert report["power_flow"]["from_solar_kw"] is None
    assert report["state_of_energy_percent"] is None


def test_no_snapshot_reports_none_rather_than_an_empty_shell():
    assert _snapshot_diagnostics(None) is None
