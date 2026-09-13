"""Config flow for Base Power.

Sign-in is passwordless, so the normal path is two steps: type the email, type
the code Clerk sends. Nobody has to copy a cookie out of developer tools.

The pasted credential stays as a demoted second option because the email route
cannot cover three cases: an account that signs in only with Google or Apple,
an account with two-factor turned on, and a Clerk change that breaks the code
flow before a fix can ship.

Either path stores the same thing. `entry.data` holds the durable Clerk client
credential, so `ClerkSessionProvider`, the API client and the coordinator are
untouched.
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
from homeassistant.const import CONF_EMAIL, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BasePowerAuthError, BasePowerClient, BasePowerError
from .clerk import ClerkAuthError, ClerkSessionProvider
from .clerk_signin import (
    ClerkBadCode,
    ClerkCodeExpired,
    ClerkRateLimited,
    ClerkSignIn,
    ClerkSignInError,
    ClerkUnknownEmail,
    SignInAttempt,
)
from .const import (
    CONF_ADDRESS_ID,
    CONF_CLIENT_JWT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .coordinator import BasePowerConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_EMAIL = vol.Schema({vol.Required(CONF_EMAIL): str})
STEP_CODE = vol.Schema({vol.Required("code"): str})
STEP_MANUAL = vol.Schema({vol.Required(CONF_CLIENT_JWT): str})

# Which error a failed sign-in shows, and crucially WHICH FORM it shows it on.
# A wrong code re-shows the code form, because the code is still valid and
# only the typing was wrong - re-preparing would email a fresh one, and doing
# that on every typo is how an account gets rate-limited. An expired code has
# nothing left to retype, so it goes back to the start.
EMAIL_STEP_ERRORS: dict[type[Exception], str] = {
    ClerkUnknownEmail: "unknown_email",
    ClerkCodeExpired: "code_expired",
    ClerkRateLimited: "too_many_requests",
}


class BasePowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email and code by default; a pasted credential as the fallback."""

    VERSION = 1

    def __init__(self) -> None:
        # Per-attempt state, on the instance rather than anywhere shared: two
        # people setting up at once must not collide.
        self._attempt: SignInAttempt | None = None
        self._email: str | None = None

    # -------------------------------------------------------------- entry

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["email", "manual"])

    # ---------------------------------------------------- email and code

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            signin = ClerkSignIn(async_get_clientsession(self.hass))
            try:
                self._attempt = await signin.async_start(email)
            except tuple(EMAIL_STEP_ERRORS) as err:
                errors["base"] = EMAIL_STEP_ERRORS[type(err)]
            except ClerkSignInError as err:
                # Covers the social-only and two-factor accounts, which is
                # exactly when someone should be sent to the manual path.
                _LOGGER.debug("Base Power sign-in could not start: %s", err)
                errors["base"] = "signin_failed"
            except Exception:
                _LOGGER.exception("unexpected error starting the Base Power sign-in")
                errors["base"] = "unknown"
            else:
                self._email = email
                return await self.async_step_code()

        return self.async_show_form(step_id="email", data_schema=STEP_EMAIL, errors=errors)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._attempt is None:
                # No attempt to submit against - send them back to the start
                # rather than raising, which would abandon a recoverable flow.
                return await self.async_step_email()
            signin = ClerkSignIn(async_get_clientsession(self.hass))
            try:
                credential = await signin.async_finish(self._attempt, user_input["code"])
            except ClerkBadCode:
                errors["base"] = "invalid_code"
            except (ClerkCodeExpired, ClerkRateLimited) as err:
                self._attempt = None
                return self.async_show_form(
                    step_id="email",
                    data_schema=STEP_EMAIL,
                    errors={"base": EMAIL_STEP_ERRORS[type(err)]},
                )
            except ClerkSignInError as err:
                _LOGGER.debug("Base Power sign-in did not complete: %s", err)
                errors["base"] = "signin_failed"
            except Exception:
                _LOGGER.exception("unexpected error completing the Base Power sign-in")
                errors["base"] = "unknown"
            else:
                result = await self._async_finish_with_credential(credential, errors)
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE,
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    # ------------------------------------------------------ manual paste

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_finish_with_credential(
                user_input[CONF_CLIENT_JWT].strip(), errors
            )
            if result is not None:
                return result
        return self.async_show_form(step_id="manual", data_schema=STEP_MANUAL, errors=errors)

    # ------------------------------------------------------------ shared

    async def _async_finish_with_credential(
        self, credential: str, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Prove the credential works, discover the site, create the entry.

        Returns None when it failed and `errors` has been filled in, so each
        caller re-shows its own form: the two entry paths share this
        validation but not the screen the user is looking at.
        """
        session = async_get_clientsession(self.hass)
        auth = ClerkSessionProvider(session, credential)
        client = BasePowerClient(session, auth.async_get_token)
        try:
            locations = await client.list_locations()
        except (ClerkAuthError, BasePowerAuthError):
            errors["base"] = "invalid_auth"
        except BasePowerError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("unexpected error validating the Base Power credential")
            errors["base"] = "unknown"
        else:
            if not locations:
                errors["base"] = "no_locations"
            else:
                # One site is the normal case; a second is a second entry.
                location = locations[0]
                data: dict[str, Any] = {
                    CONF_CLIENT_JWT: credential,
                    CONF_ADDRESS_ID: location.address_id,
                }
                if self._email:
                    # Kept for the reauth pre-fill, and because it is the only
                    # human-readable thing saying which account an entry is for.
                    data[CONF_EMAIL] = self._email

                if self.source == "reauth":
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=data
                    )
                await self.async_set_unique_id(location.address_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=location.name or "Base Power", data=data)
        return None

    # ------------------------------------------------------------ reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """A Clerk session ended or was revoked - sign in again the same way."""
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then send a fresh code to the remembered address.

        An entry created before the email flow existed has no address stored,
        so it falls back to asking for one rather than dead-ending.
        """
        if not self._email:
            return await self.async_step_email()

        errors: dict[str, str] = {}
        if user_input is not None:
            signin = ClerkSignIn(async_get_clientsession(self.hass))
            try:
                self._attempt = await signin.async_start(self._email)
            except tuple(EMAIL_STEP_ERRORS) as err:
                errors["base"] = EMAIL_STEP_ERRORS[type(err)]
            except ClerkSignInError:
                errors["base"] = "signin_failed"
            except Exception:
                _LOGGER.exception("unexpected error starting the Base Power reauth")
                errors["base"] = "unknown"
            else:
                return await self.async_step_code()

        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            description_placeholders={"email": self._email},
        )

    # ----------------------------------------------------------- options

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

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
