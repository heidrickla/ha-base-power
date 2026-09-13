"""Passwordless sign-in against Clerk's frontend API.

This exists so a user never has to paste a cookie out of a browser: they type
their email, Clerk sends a code, they type the code, and what comes back is
the durable client credential `ClerkSessionProvider` already knows how to use.
`entry.data` keeps exactly the shape it had before - this is a new way to
OBTAIN the credential, not a new credential model.

**Native mode, not browser mode.** The app is a native Clerk client and the
bundle carries `_is_native`, `__clerk_db_jwt` and the header `Clerk-Db-Jwt`.
Native clients authenticate with `Authorization` and browser clients with
`Origin` - and Clerk rejects a request carrying both, which is how that
distinction was discovered. Native is the right side for a headless
integration: no `Origin` to fake and no browser `User-Agent` to imitate.

The flow, and every name in it read out of the app bundle rather than assumed
(`signIn.create`, `prepareFirstFactor`, `attemptFirstFactor`, and the keys
`identifier`, `strategy`, `emailAddressId`, `code`, `supportedFirstFactors`,
`createdSessionId`):

    POST /v1/client/sign_ins                    identifier=<email>
      -> status needs_first_factor, supported_first_factors[]
    POST /v1/client/sign_ins/<id>/prepare_first_factor
                                                strategy=email_code
                                                email_address_id=<from above>
      -> Clerk emails a code
    POST /v1/client/sign_ins/<id>/attempt_first_factor
                                                strategy=email_code, code=<typed>
      -> status complete, created_session_id

The client credential is returned in the `Authorization` RESPONSE header on
every call, and the final one is what gets stored.

**Not exercised against the live service.** Requesting a code sends a real
email to a real person, so this is built from the bundle and covered by tests
with faked responses; the first real run is the first test of it. What it is
built on - the endpoint paths, the field names, the statuses and the native
mode - is evidence, not guesswork.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

_LOGGER = logging.getLogger(__name__)

CLERK_HOST = "https://clerk.basepowercompany.com"
# `_is_native=1` selects the Authorization-header flow. The version params are
# the same ones the browser client sends; Clerk refuses some calls without them.
CLERK_QUERY = {
    "_is_native": "1",
    "__clerk_api_version": "2025-04-10",
    "_clerk_js_version": "5.40.0",
}
EMAIL_CODE = "email_code"

# Sign-in statuses the app's own bundle carries.
STATUS_NEEDS_FIRST_FACTOR = "needs_first_factor"
STATUS_NEEDS_SECOND_FACTOR = "needs_second_factor"
STATUS_COMPLETE = "complete"


class ClerkSignInError(Exception):
    """Sign-in failed in a way the user can act on."""


class ClerkUnknownEmail(ClerkSignInError):
    """No account for that address."""


class ClerkBadCode(ClerkSignInError):
    """The code was wrong. The user retypes it; the code is NOT re-requested."""


class ClerkCodeExpired(ClerkSignInError):
    """The code is too old or has had too many attempts; start again."""


class ClerkRateLimited(ClerkSignInError):
    """Clerk is rate-limiting. Distinct because the answer is to wait.

    Retrying immediately is what gets an account locked out, so this must
    never be reported as a generic connection failure.
    """


class ClerkSecondFactorRequired(ClerkSignInError):
    """The account has 2FA, which this flow does not implement."""


@dataclass
class SignInAttempt:
    """One in-progress sign-in. Held on the config-flow instance, never global."""

    sign_in_id: str
    email_address_id: str | None
    client_jwt: str | None


class ClerkSignIn:
    """Drives Clerk's email-code sign-in and returns a client credential."""

    def __init__(self, session: Any, host: str = CLERK_HOST) -> None:
        self._session = session
        self._host = host.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self._host}{path}?{urlencode(CLERK_QUERY)}"

    async def _post(
        self, path: str, data: dict[str, str], client_jwt: str | None
    ) -> tuple[dict[str, Any], str | None]:
        """POST form-encoded, returning the body and any refreshed credential.

        Clerk hands the native client credential back in the `Authorization`
        response header on every call, so it is read here rather than at the
        end - the value can rotate mid-flow and the last one is the one worth
        keeping.
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if client_jwt:
            # Never alongside Origin: Clerk rejects both together.
            headers["Authorization"] = f"Bearer {client_jwt}"
        async with self._session.post(self._url(path), data=data, headers=headers) as resp:
            body = await resp.json(content_type=None)
            refreshed = _credential_from(resp.headers) or client_jwt
            if resp.status == 429:
                raise ClerkRateLimited(
                    "Clerk is rate-limiting sign-in for this account; wait before retrying"
                )
            if resp.status >= 400:
                raise _error_for(body, resp.status)
        if not isinstance(body, dict):
            raise ClerkSignInError(f"unexpected response from Clerk at {path}")
        return body, refreshed

    async def async_start(self, email: str) -> SignInAttempt:
        """Create the sign-in and ask Clerk to email a code."""
        body, client_jwt = await self._post(
            "/v1/client/sign_ins", {"identifier": email}, None
        )
        response = body.get("response") or body
        sign_in_id = response.get("id")
        if not sign_in_id:
            raise ClerkSignInError("Clerk did not return a sign-in id")

        status = response.get("status")
        if status == STATUS_NEEDS_SECOND_FACTOR:
            raise ClerkSecondFactorRequired(
                "this account uses two-factor authentication, which this integration "
                "cannot complete"
            )

        email_address_id = _email_factor_id(response)
        if not email_address_id:
            raise ClerkSignInError(
                "this account cannot sign in with an emailed code - it may be "
                "Google or Apple only"
            )

        attempt = SignInAttempt(sign_in_id, email_address_id, client_jwt)
        body, attempt.client_jwt = await self._post(
            f"/v1/client/sign_ins/{sign_in_id}/prepare_first_factor",
            {"strategy": EMAIL_CODE, "email_address_id": email_address_id},
            attempt.client_jwt,
        )
        return attempt

    async def async_finish(self, attempt: SignInAttempt, code: str) -> str:
        """Submit the code and return the durable client credential."""
        body, client_jwt = await self._post(
            f"/v1/client/sign_ins/{attempt.sign_in_id}/attempt_first_factor",
            {"strategy": EMAIL_CODE, "code": code.strip()},
            attempt.client_jwt,
        )
        attempt.client_jwt = client_jwt
        response = body.get("response") or body
        status = response.get("status")
        if status == STATUS_NEEDS_SECOND_FACTOR:
            raise ClerkSecondFactorRequired(
                "this account uses two-factor authentication, which this integration "
                "cannot complete"
            )
        if status != STATUS_COMPLETE or not response.get("created_session_id"):
            raise ClerkSignInError(f"sign-in did not complete (status {status!r})")
        if not client_jwt:
            raise ClerkSignInError(
                "sign-in completed but Clerk returned no client credential"
            )
        return client_jwt


def _credential_from(headers: Any) -> str | None:
    """The client credential out of a Clerk response, from either header.

    `Authorization` is the documented native-mode carrier and the one this
    was built against. `Clerk-Db-Jwt` is checked as well because the app
    bundle carries that exact header name alongside `__clerk_db_jwt`, so it
    is a real alternative rather than a guess - and which one a given Clerk
    version uses is the single biggest inference in this module. Accepting
    both costs nothing and removes a whole failure mode from the first real
    sign-in.
    """
    for name in ("Authorization", "Clerk-Db-Jwt"):
        value = headers.get(name)
        if not value:
            continue
        if value.lower().startswith("bearer "):
            value = value[7:]
        value = value.strip()
        if value:
            return value
    return None


def _email_factor_id(response: dict[str, Any]) -> str | None:
    """The email_address_id of the email_code factor, if the account has one."""
    for factor in response.get("supported_first_factors") or []:
        if isinstance(factor, dict) and factor.get("strategy") == EMAIL_CODE:
            return factor.get("email_address_id")
    return None


def _error_for(body: Any, status: int) -> ClerkSignInError:
    """Map Clerk's error codes to something the user can act on.

    Clerk reports `{"errors": [{"code": ..., "message": ...}]}`. The codes
    matter more than the status: a wrong code and an expired one are both
    422, and they need different answers - retype it, versus start again.
    """
    code = ""
    message = ""
    if isinstance(body, dict):
        errors = body.get("errors") or []
        if errors and isinstance(errors[0], dict):
            code = str(errors[0].get("code") or "")
            message = str(errors[0].get("long_message") or errors[0].get("message") or "")
    detail = message or f"Clerk returned HTTP {status}"

    if code == "form_identifier_not_found":
        return ClerkUnknownEmail(detail)
    if code in ("form_code_incorrect", "verification_failed"):
        return ClerkBadCode(detail)
    if code in ("verification_expired", "form_code_expired", "too_many_attempts"):
        return ClerkCodeExpired(detail)
    if code in ("too_many_requests", "rate_limit_exceeded"):
        return ClerkRateLimited(detail)

    # An unmapped code is the likeliest way this module is wrong, because the
    # mapping was read from Clerk's documented codes rather than observed on
    # this instance. Log the code itself at warning: one failed sign-in then
    # names the value needed to fix it, instead of costing another round trip
    # through a user who has to request a fresh code to try again.
    _LOGGER.warning(
        "Clerk returned an unrecognised sign-in error (HTTP %s, code %r): %s. "
        "If this was a wrong or expired code, that code belongs in "
        "clerk_signin._error_for so the flow can respond properly",
        status,
        code or "<none>",
        message or "<no message>",
    )
    return ClerkSignInError(detail)
