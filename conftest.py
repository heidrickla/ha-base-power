"""Root pytest configuration.

`tests/` and `tests/ha/` need different environments, and this lets one
`pytest` command work in both.

- `tests/` imports the parsing layer by path and runs anywhere, including a
  Windows host where Home Assistant cannot be installed. That is the check:
  if an HA import reaches api.py or clerk.py, that suite stops running.
- `tests/ha/` needs a real Home Assistant, so it is skipped where there is
  none rather than failing collection and taking the other suite with it.

The full suite needs Linux with pytest-homeassistant-custom-component; the
versions are in the test-coverage entry of quality_scale.yaml.
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
