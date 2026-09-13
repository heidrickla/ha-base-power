"""Config flow for Base Power.

Sign-in is passwordless - Clerk issues a code by email or a social login - so
there is no username and password to collect here. What the integration needs
is the durable `__client` credential from a completed sign-in, which the user
copies out of the browser once. `docs/API.md` has the steps.

The credential is validated before the entry is created, and the site is
discovered rather than typed: `ListLocations` is the call that yields the
`address_id` everything else is scoped by.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BasePowerAuthError, BasePowerClient, BasePowerError
from .clerk import ClerkAuthError, ClerkSessionProvider
from .const import (
    CONF_ADDRESS_ID,
    CONF_CLIENT_JWT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .coordinator import BasePowerConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema({vol.Required(CONF_CLIENT_JWT): str})


class BasePowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Take the client credential, prove it works, discover the site."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            credential = user_input[CONF_CLIENT_JWT].strip()
            session = async_get_clientsession(self.hass)
            auth = ClerkSessionProvider(session, credential)
            client = BasePowerClient(session, auth.async_get_token)
            try:
                locations = await client.list_locations()
            except (ClerkAuthError, BasePowerAuthError):
                errors["base"] = "invalid_auth"
            except BasePowerError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - a config flow must never crash
                _LOGGER.exception("unexpected error validating the Base Power credential")
                errors["base"] = "unknown"
            else:
                if not locations:
                    errors["base"] = "no_locations"
                else:
                    # One site is the normal case. Picking the first rather
                    # than asking keeps the common path to a single screen;
                    # a second entry can be added for another site.
                    location = locations[0]
                    await self.async_set_unique_id(location.address_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=location.name or "Base Power",
                        data={
                            CONF_CLIENT_JWT: credential,
                            CONF_ADDRESS_ID: location.address_id,
                        },
                    )

        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The credential died - a Clerk session ends or is revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            credential = user_input[CONF_CLIENT_JWT].strip()
            session = async_get_clientsession(self.hass)
            auth = ClerkSessionProvider(session, credential)
            client = BasePowerClient(session, auth.async_get_token)
            try:
                await client.list_locations()
            except (ClerkAuthError, BasePowerAuthError):
                errors["base"] = "invalid_auth"
            except BasePowerError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CLIENT_JWT: credential}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_USER, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: BasePowerConfigEntry) -> OptionsFlowWithReload:
        return BasePowerOptionsFlow()


class BasePowerOptionsFlow(OptionsFlowWithReload):
    """Just the poll interval.

    OptionsFlowWithReload reloads the entry itself when options change, which
    is why __init__.py registers no update listener: doing both would reload
    twice for one edit.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=3600)
                    )
                }
            ),
        )
