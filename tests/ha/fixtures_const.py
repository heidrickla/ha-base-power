"""Shared constants for the Home Assistant tests.

A module of its own rather than importing from conftest: pytest may import a
conftest under a different module name, so importing from one gets you two
copies of these objects and identity comparisons start failing for no visible
reason.

Not named `const.py` on purpose - the pure tests put
`custom_components/base_power` on sys.path, and a second `const` on the path
is a collision waiting for whoever runs both suites in one process.
"""

from __future__ import annotations

ADDRESS_ID = "addr_test_1"
CREDENTIAL = "client_credential_value"
EMAIL = "someone@example.test"
