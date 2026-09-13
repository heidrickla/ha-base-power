"""Constants for the Base Power integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "base_power"

CONF_CLIENT_JWT = "client_jwt"
CONF_ADDRESS_ID = "address_id"

# The app polls every 1 s but only while its screen is focused. Home Assistant
# polls continuously, so 1 s here would be 86,400 calls a day against Base's
# production service for data that moves far slower.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
# A floor, not a suggestion: 5 s would allow 17,280 calls a day.
MIN_SCAN_INTERVAL_SECONDS = 15

# The reference load the API reports backup hours against, so hours x 0.75 kW
# is the stored energy implied. The only route to stored energy while on grid,
# since the on-grid snapshot carries no state of charge.
BACKUP_REFERENCE_KW = 0.75

MANUFACTURER = "Base Power"

ISSUE_TELEMETRY_UNAVAILABLE = "telemetry_unavailable"

# How long the battery must be silent before the repair issue is raised.
#
# `telemetry_available` means "a recent-enough snapshot exists", not "the
# battery is fine", and how recent depends on the link. Measured on one healthy
# unit: Wi-Fi 32 s between observations, cellular a snapshot already 4m51s old.
# The API drops a stale snapshot rather than serving it, so on cellular the
# flag oscillates in ordinary service.
#
# 30 minutes sits above the cellular cadence and below the multi-hour absence
# Base's own app flags. An earlier five-minute threshold was calibrated against
# the wrong process and would have fired constantly on a healthy battery.
TELEMETRY_GRACE = timedelta(minutes=30)
MIN_TELEMETRY_POLLS = 2


def silent_polls_before_issue(interval: timedelta | None) -> int:
    """How many quiet polls the grace period is worth at a given interval.

    A duration rather than a fixed poll count, because ten polls is five
    minutes at the 30 s default and over ninety at the 3600 s maximum.

    The two-poll floor stretches the wait at the long end: at hourly polling
    it gives two hours, which is correct, since a thirty-minute condition is
    not detectable from hourly samples and the alternative is raising the
    issue off a single reading.
    """
    seconds = (interval or DEFAULT_SCAN_INTERVAL).total_seconds()
    return max(MIN_TELEMETRY_POLLS, round(TELEMETRY_GRACE.total_seconds() / seconds))
