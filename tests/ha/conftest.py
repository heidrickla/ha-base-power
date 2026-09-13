"""Fixtures for the tests that need a real Home Assistant.

tests/ha runs against pytest-homeassistant-custom-component and needs a Linux
host with Home Assistant installed. The tests one directory up do not, and
keeping the two apart is what makes an HA import creeping into api.py a
visible failure.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fixtures_const import ADDRESS_ID, CREDENTIAL, EMAIL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.base_power.const import CONF_ADDRESS_ID, CONF_CLIENT_JWT, DOMAIN

# No `pytest_plugins` line here on purpose. pytest refuses it outside a
# top-level conftest, and pytest-homeassistant-custom-component registers
# itself through entry points anyway - declaring it would only break
# collecting tests/ and tests/ha together.


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> Generator[None, None, None]:
    """Without this Home Assistant will not load a custom component at all."""
    yield


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A configured entry for one site, as the config flow would create it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=ADDRESS_ID,
        data={CONF_CLIENT_JWT: CREDENTIAL, CONF_ADDRESS_ID: ADDRESS_ID, "email": EMAIL},
    )
