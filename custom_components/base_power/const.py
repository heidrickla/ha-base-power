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
TELEMETRY_GRACE = timedelta(minutes=5)
MIN_TELEMETRY_POLLS = 2
