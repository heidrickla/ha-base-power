"""Constants for the Base Power integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "base_power"

CONF_CLIENT_JWT = "client_jwt"
CONF_ADDRESS_ID = "address_id"

# The app polls GetSnapshot every 1000 ms, but React Query only refetches
# while the screen is mounted and focused - a burst while someone is looking
# at it, not a standing load. A Home Assistant integration polls 24/7, so
# copying 1 s here would turn a few hundred calls a day into 86,400 against
# somebody else's production service for data that moves far slower than that.
# 30 s keeps the outage sensor responsive enough to be useful and is the
# default rather than a limit; the user can raise it.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
# A floor, not a suggestion. At 5 s a user could issue 17,280 calls a day
# against Base's production service; 15 s caps it near 5,760 and is still far
# more responsive than anything the data justifies.
MIN_SCAN_INTERVAL_SECONDS = 15

# 750 W is the reference load the API reports backup hours against, so
# hours x 0.75 kW is the stored energy it implies. This is the only route to
# stored energy while on grid, because the on-grid snapshot carries no state
# of charge at all (confirmed live, see docs/API.md).
BACKUP_REFERENCE_KW = 0.75

MANUFACTURER = "Base Power"

# The repair issue raised when the battery stops reporting, matching the
# banner Base's own app shows for the same condition.
ISSUE_TELEMETRY_UNAVAILABLE = "telemetry_unavailable"

# How long the battery must be silent before the issue is raised. A single
# quiet poll is not worth a notification - at the default interval a blip
# would otherwise raise and clear a repair twice a minute - and a fixed COUNT
# would misbehave at either end of the interval range: ten polls is five
# minutes at the 30 s default but over an hour and a half at the 3600 s
# maximum. So the threshold is a DURATION, converted to polls against
# whatever interval is configured, with at least two polls always required.
# **Thirty minutes, set by the owner**, and the number matters as much as the
# shape. The first attempt at five minutes was calibrated against the wrong
# process entirely and would have fired constantly on a healthy battery.
#
# `telemetry_available` does NOT mean "the battery is fine". It means "a
# recent-enough snapshot exists", and how recent depends entirely on which
# LINK the battery is using. It reports over Wi-Fi when it can and falls back
# to cellular when it cannot, and both were measured on the same healthy unit
# on the same day:
#
#   Wi-Fi     32 seconds between successive observations
#   cellular  a snapshot already 4m51s old at 19:18Z, and
#             `telemetry_available` false by 19:23Z
#
# The API drops a snapshot once it goes stale rather than serving it
# indefinitely, so on cellular the flag oscillates in ordinary service. On
# Wi-Fi it essentially does not.
#
# Thirty minutes sits above even the cellular cadence and well below the
# multi-hour absence Base's own app flagged, which is what keeps a WARNING
# saying "contact Base Support" rare enough to mean something. On a Wi-Fi
# battery it should effectively never fire - and if it does, the link is the
# first thing to check, not the battery. The one real occurrence so far was an
# access point with its PoE injector unplugged on the ethernet side; the
# battery fell back to cellular and stayed there until the AP came back.
TELEMETRY_GRACE = timedelta(minutes=30)
MIN_TELEMETRY_POLLS = 2


def silent_polls_before_issue(interval: timedelta | None) -> int:
    """How many quiet polls the grace period is worth at a given interval.

    Module-level and pure so it can be tested without Home Assistant. That is
    not incidental: this is the number that was wrong once already, and a
    threshold nobody can exercise is one nobody notices drifting.

    The floor matters at the long end. At an hourly interval two polls is two
    hours rather than thirty minutes - correct, because a thirty-minute
    condition simply is not detectable from hourly samples, and pretending
    otherwise would raise the issue off a single reading.
    """
    seconds = (interval or DEFAULT_SCAN_INTERVAL).total_seconds()
    return max(MIN_TELEMETRY_POLLS, round(TELEMETRY_GRACE.total_seconds() / seconds))
