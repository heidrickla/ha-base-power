"""Root pytest configuration.

`tests/` and `tests/ha/` need different environments on purpose, and this is
what lets one `pytest` command work in both:

- `tests/` imports the parsing layer BY PATH and must run anywhere, including
  a Windows host where Home Assistant cannot be installed at all. That is not
  a limitation being worked around - it is the check. If a Home Assistant
  import ever creeps into api.py or clerk.py, that suite stops running and
  says so, which no amount of mocking would tell us.

- `tests/ha/` needs a real Home Assistant, so it is skipped where there is
  none rather than failing collection and taking the other suite down with it.

Running the full suite therefore needs Linux with
pytest-homeassistant-custom-component installed; see the test-coverage entry
in quality_scale.yaml for the exact versions.
"""

from __future__ import annotations

from importlib.util import find_spec

collect_ignore: list[str] = []

if find_spec("pytest_homeassistant_custom_component") is None:
    # Not an error, and deliberately not silent either: a run that quietly
    # covers half of what you think it covers is the failure being avoided.
    collect_ignore.append("tests/ha")


def pytest_report_header(config: object) -> str:
    """Say which half ran, every time, in the header nobody can miss."""
    if collect_ignore:
        return "base_power: Home Assistant NOT installed - tests/ha skipped, pure layer only"
    return "base_power: Home Assistant present - running the full suite"
