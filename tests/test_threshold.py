"""The repair-issue threshold.

What is pinned is the property, not the arithmetic: the threshold is a
duration and stays that duration as the user changes the poll interval. A
fixed poll count would mean five minutes at one end of the range and ninety at
the other.

An earlier five-minute version measured "the snapshot has gone stale" while
calibrated against "the battery has gone dark". On cellular the former happens
roughly every ten minutes in normal service.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from const import (
    DEFAULT_SCAN_INTERVAL,
    MIN_TELEMETRY_POLLS,
    TELEMETRY_GRACE,
    silent_polls_before_issue,
)

# Below this the grace period is worth two or more polls and comes out exact;
# at or above it the two-poll floor takes over. 30 min / 2 polls = 15 min.
FLOOR_TAKES_OVER_AT = TELEMETRY_GRACE / MIN_TELEMETRY_POLLS


@pytest.mark.parametrize("seconds", [15, 30, 60, 300, 900])
def test_the_threshold_is_the_same_duration_at_every_usable_interval(seconds):
    """The whole point of deriving it rather than fixing a poll count:
    30 minutes means 30 minutes across the intervals a user would pick."""
    assert timedelta(seconds=seconds) <= FLOOR_TAKES_OVER_AT
    polls = silent_polls_before_issue(timedelta(seconds=seconds))
    assert polls * seconds == pytest.approx(TELEMETRY_GRACE.total_seconds(), rel=0.01)


@pytest.mark.parametrize("seconds", [1800, 3600])
def test_a_long_interval_waits_longer_rather_than_firing_on_one_sample(seconds):
    """Past 15-minute polling the grace period is worth fewer than two polls,
    and the floor stretches the wait instead of shortening it.

    That asymmetry is deliberate. Raising a warning that says "contact Base
    Support" off a single reading is the worse failure, so when the interval
    is too coarse to resolve 30 minutes the threshold errs long.
    """
    polls = silent_polls_before_issue(timedelta(seconds=seconds))
    assert polls == MIN_TELEMETRY_POLLS
    assert polls * seconds > TELEMETRY_GRACE.total_seconds()


def test_the_default_interval_gives_the_intended_grace():
    polls = silent_polls_before_issue(DEFAULT_SCAN_INTERVAL)
    assert polls * DEFAULT_SCAN_INTERVAL.total_seconds() == TELEMETRY_GRACE.total_seconds()


def test_no_interval_falls_back_to_the_default():
    assert silent_polls_before_issue(None) == silent_polls_before_issue(DEFAULT_SCAN_INTERVAL)


def test_the_grace_clears_the_observed_cellular_cadence():
    """The calibration itself, pinned against the measurement that drove it.

    A healthy cellular battery was seen with a 4m51s-stale snapshot and
    `telemetry_available` false about ten minutes after its last report. The
    threshold has to sit well clear of that, or a normal reporting gap raises
    a warning telling the user to contact Base Support.
    """
    observed_cellular_gap = timedelta(minutes=10)
    assert observed_cellular_gap * 2 <= TELEMETRY_GRACE, (
        "the grace period must clear a normal cellular reporting gap with room "
        "to spare, or the repair issue fires in ordinary service"
    )
