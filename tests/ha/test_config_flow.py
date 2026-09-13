"""The config flow, against a real Home Assistant.

The flow is the only part of this integration a user drives by hand, and it is
the part where a mistake is expensive in a way a wrong sensor value is not:
requesting a code sends a real email, and a wrong-code path that silently
re-requests will rate-limit somebody's account. So the error branches matter
here at least as much as the happy path, and most of this file is them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from fixtures_const import ADDRESS_ID, CREDENTIAL, EMAIL
from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.base_power.api import (
    BasePowerAuthError,
    BasePowerError,
    Location,
)
from custom_components.base_power.clerk import ClerkAuthError
from custom_components.base_power.clerk_signin import (
    ClerkBadCode,
    ClerkCodeExpired,
    ClerkRateLimited,
    ClerkSecondFactorRequired,
    ClerkSignInError,
    ClerkUnknownEmail,
    SignInAttempt,
)
from custom_components.base_power.const import (
    CONF_ADDRESS_ID,
    CONF_CLIENT_JWT,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
)

ATTEMPT = SignInAttempt(sign_in_id="si_1", email_address_id="idn_9", client_jwt=None)
LOCATIONS = [Location(address_id=ADDRESS_ID, name="Home")]

SIGNIN = "custom_components.base_power.config_flow.ClerkSignIn"
CLIENT = "custom_components.base_power.config_flow.BasePowerClient"


def _signin(start=None, finish=None, start_error=None, finish_error=None):
    """A ClerkSignIn whose two calls are controlled independently."""
    instance = AsyncMock()
    instance.async_start = AsyncMock(
        return_value=start if start is not None else ATTEMPT, side_effect=start_error
    )
    instance.async_finish = AsyncMock(
        return_value=finish if finish is not None else CREDENTIAL, side_effect=finish_error
    )
    return instance


def _client(locations=None, error=None):
    instance = AsyncMock()
    instance.list_locations = AsyncMock(
        return_value=LOCATIONS if locations is None else locations, side_effect=error
    )
    return instance


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# ------------------------------------------------------------ the happy path


async def test_the_menu_offers_the_code_route_first(hass: HomeAssistant) -> None:
    """Order is deliberate: the paste route needs DevTools and most people
    should never see it."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["email", "manual"]


async def test_email_then_code_creates_the_entry(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    assert result["step_id"] == "email"

    with patch(SIGNIN, return_value=_signin()):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": EMAIL})
    assert result["step_id"] == "code"
    # The address is echoed back so the user can see where to look.
    assert result["description_placeholders"] == {"email": EMAIL}

    with (
        patch(SIGNIN, return_value=_signin()),
        patch(CLIENT, return_value=_client()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_CLIENT_JWT: CREDENTIAL,
        CONF_ADDRESS_ID: ADDRESS_ID,
        "email": EMAIL,
    }
    assert result["result"].unique_id == ADDRESS_ID


async def test_a_site_with_no_name_falls_back_to_the_literal_title(
    hass: HomeAssistant,
) -> None:
    """Base returns no name for some sites, and the title becomes the device
    name, which becomes the entity id prefix. This is the case on the only
    production instance, so it decides what the README examples must say.
    """
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    unnamed = [Location(address_id=ADDRESS_ID, name=None)]
    with patch(CLIENT, return_value=_client(locations=unnamed)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: CREDENTIAL}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Base Power"


async def test_the_email_is_stripped_before_use(hass: HomeAssistant) -> None:
    """Copy-paste from a mail client brings whitespace, and Clerk would treat
    ' a@b.test' as a different, unknown address."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    signin = _signin()
    with patch(SIGNIN, return_value=signin):
        await hass.config_entries.flow.async_configure(result["flow_id"], {"email": f"  {EMAIL}  "})
    signin.async_start.assert_awaited_once_with(EMAIL)


# --------------------------------------------------------- the email step's errors


@pytest.mark.parametrize(
    "error,expected",
    [
        (ClerkUnknownEmail("no account"), "unknown_email"),
        (ClerkCodeExpired("too late"), "code_expired"),
        (ClerkRateLimited("slow down"), "too_many_requests"),
        # Not in EMAIL_STEP_ERRORS: a 2FA account cannot finish this route at
        # all, so it lands on the generic message that points at the paste
        # option rather than one implying the code is nearly working.
        (ClerkSecondFactorRequired("2fa"), "signin_failed"),
        (ClerkSignInError("something else"), "signin_failed"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_the_email_step_reports_each_failure_distinctly(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    """Each of these tells the user a different thing to do, so collapsing
    them into one message would be the difference between 'use the other
    option' and 'wait five minutes'."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    with patch(SIGNIN, return_value=_signin(start_error=error)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": EMAIL})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "email"
    assert result["errors"] == {"base": expected}


# ---------------------------------------------------------- the code step's errors


async def _to_code_step(hass: HomeAssistant):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    with patch(SIGNIN, return_value=_signin()):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": EMAIL})
    return result


async def test_a_wrong_code_is_retryable_on_the_same_screen(hass: HomeAssistant) -> None:
    """It must NOT bounce back to the email step: that would re-request a
    code, and repeated requests are what rate-limits the account."""
    result = await _to_code_step(hass)
    with patch(SIGNIN, return_value=_signin(finish_error=ClerkBadCode("nope"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "000000"}
        )
    assert result["step_id"] == "code"
    assert result["errors"] == {"base": "invalid_code"}


@pytest.mark.parametrize(
    "error,expected",
    [
        (ClerkCodeExpired("too late"), "code_expired"),
        (ClerkRateLimited("slow down"), "too_many_requests"),
    ],
)
async def test_an_unusable_code_sends_the_user_back_to_request_another(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    """Expired and rate-limited are the two where retyping cannot ever work,
    so the flow returns to the email step - the only place a new code comes
    from - rather than leaving someone retrying a dead code."""
    result = await _to_code_step(hass)
    with patch(SIGNIN, return_value=_signin(finish_error=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
    assert result["step_id"] == "email"
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize(
    "error,expected",
    [
        (ClerkSignInError("other"), "signin_failed"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_the_code_step_handles_the_remaining_failures(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    result = await _to_code_step(hass)
    with patch(SIGNIN, return_value=_signin(finish_error=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )
    assert result["step_id"] == "code"
    assert result["errors"] == {"base": expected}


# ------------------------------------------------------------- the manual path


async def test_the_pasted_credential_creates_the_entry(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"

    with patch(CLIENT, return_value=_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: f"  {CREDENTIAL}  "}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Stripped, and no email recorded because this path never asked for one.
    assert result["data"] == {CONF_CLIENT_JWT: CREDENTIAL, CONF_ADDRESS_ID: ADDRESS_ID}


@pytest.mark.parametrize(
    "error,locations,expected",
    [
        (None, [], "no_locations"),
        (RuntimeError("boom"), None, "unknown"),
    ],
)
async def test_the_manual_path_reports_validation_failures(
    hass: HomeAssistant, error: Exception | None, locations: list | None, expected: str
) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(CLIENT, return_value=_client(locations=locations, error=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: CREDENTIAL}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_an_account_with_no_sites_is_not_an_auth_failure(
    hass: HomeAssistant,
) -> None:
    """A valid credential on an account with nothing installed. Telling that
    user their credential is wrong would send them to re-copy a cookie that
    was never the problem."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(CLIENT, return_value=_client(locations=[])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: CREDENTIAL}
        )
    assert result["errors"] == {"base": "no_locations"}


# ------------------------------------------------------------------ duplicates


async def test_the_same_site_cannot_be_added_twice(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Scoped by address id, so a second entry for one battery is a duplicate
    even if the credential differs - the same account on two browsers."""
    mock_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(CLIENT, return_value=_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: "a-different-credential"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --------------------------------------------------------------------- reauth


async def test_reauth_reuses_the_remembered_address(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The whole point of storing the email: reauthentication should not make
    someone retype the address they signed up with."""
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    # Subset, not equality: Home Assistant adds `name` to a reauth flow's
    # placeholders itself, and asserting the exact dict would make this test
    # fail on an upstream change that has nothing to do with the integration.
    assert result["description_placeholders"]["email"] == EMAIL

    with patch(SIGNIN, return_value=_signin()):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "code"

    with (
        patch(SIGNIN, return_value=_signin(finish="a-fresh-credential")),
        patch(CLIENT, return_value=_client()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "123456"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_CLIENT_JWT] == "a-fresh-credential"


async def test_reauth_on_an_entry_with_no_email_asks_for_one(
    hass: HomeAssistant,
) -> None:
    """Entries created by the paste route predate the email being stored, and
    must not dead-end at a confirm screen with nothing to confirm."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=ADDRESS_ID,
        data={CONF_CLIENT_JWT: CREDENTIAL, CONF_ADDRESS_ID: ADDRESS_ID},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "email"


async def test_a_reauth_failure_is_reported_on_the_confirm_screen(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    with patch(SIGNIN, return_value=_signin(start_error=ClerkRateLimited("slow"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "too_many_requests"}


@pytest.mark.parametrize(
    "error,expected",
    [
        (ClerkSignInError("other"), "signin_failed"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_reauth_handles_the_remaining_failures(
    hass: HomeAssistant, mock_entry: MockConfigEntry, error: Exception, expected: str
) -> None:
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    with patch(SIGNIN, return_value=_signin(start_error=error)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": expected}


# ------------------------------------------------------- credential validation


@pytest.mark.parametrize(
    "error,expected",
    [
        (ClerkAuthError("credential dead"), "invalid_auth"),
        (BasePowerAuthError("token refused"), "invalid_auth"),
        (BasePowerError("service down"), "cannot_connect"),
    ],
)
async def test_a_credential_that_does_not_work_says_which_kind_of_failure(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    """invalid_auth tells the user to get a new credential; cannot_connect
    tells them to wait. Getting these the wrong way round sends someone
    re-copying a cookie while Base is simply down."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(CLIENT, return_value=_client(error=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_JWT: CREDENTIAL}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_submitting_a_code_with_no_attempt_returns_to_the_email_step(
    hass: HomeAssistant,
) -> None:
    """Reachable when a flow is resumed after the attempt is gone. It has to
    recover to somewhere usable - raising would abandon a flow the user can
    still complete."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    with patch(SIGNIN, return_value=_signin()):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": EMAIL})

    flow = hass.config_entries.flow._progress[result["flow_id"]]
    flow._attempt = None

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert result["step_id"] == "email"


# -------------------------------------------------------------- options flow


async def test_the_options_flow_offers_the_current_interval(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_the_options_flow_saves_a_new_interval(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SCAN_INTERVAL: 60}


@pytest.mark.parametrize("bad", [MIN_SCAN_INTERVAL_SECONDS - 1, 3601])
async def test_the_options_flow_refuses_an_interval_outside_the_range(
    hass: HomeAssistant, mock_entry: MockConfigEntry, bad: int
) -> None:
    """The floor is not cosmetic: at 5 s a user would issue 17,280 calls a day
    against somebody else's production service."""
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: bad}
        )
