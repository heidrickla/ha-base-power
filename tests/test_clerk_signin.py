"""The email-code sign-in, against faked Clerk responses.

Requesting a real code sends a real email to a real person, so this is the
layer that has to be right before anyone runs it once. The state machine is
the part where a mistake is invisible until a user hits it: a wrong code that
silently re-requests will rate-limit the account, and an expired code that
looks like a wrong one leaves the user retyping something that can never work.

Shapes here follow Clerk's frontend API and the names read out of the app
bundle. They are NOT captured from the live service - see clerk_signin.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from clerk_signin import (
    ClerkBadCode,
    ClerkCodeExpired,
    ClerkRateLimited,
    ClerkSecondFactorRequired,
    ClerkSignIn,
    ClerkSignInError,
    ClerkUnknownEmail,
)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records every call so the request shape itself can be asserted."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": data or {}, "headers": headers or {}})
        if not self._responses:
            raise AssertionError(f"unexpected extra call to {url}")
        return self._responses.pop(0)


def created(sign_in_id="si_1", status="needs_first_factor", factors=None):
    return {
        "response": {
            "id": sign_in_id,
            "status": status,
            "supported_first_factors": factors
            if factors is not None
            else [{"strategy": "email_code", "email_address_id": "idn_9"}],
        }
    }


def completed(session_id="sess_1"):
    return {"response": {"id": "si_1", "status": "complete", "created_session_id": session_id}}


AUTH = {"Authorization": "Bearer client_jwt_v1"}
AUTH2 = {"Authorization": "Bearer client_jwt_v2"}


async def test_the_happy_path_returns_the_durable_credential():
    session = FakeSession(
        FakeResponse(200, created(), AUTH),
        FakeResponse(200, {"response": {}}, AUTH),
        FakeResponse(200, completed(), AUTH2),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    credential = await signin.async_finish(attempt, "123456")

    # The LAST credential wins: Clerk rotates it and the final one is the
    # one that will still work tomorrow.
    assert credential == "client_jwt_v2"
    assert len(session.calls) == 3


async def test_the_request_shape_matches_what_the_app_sends():
    session = FakeSession(
        FakeResponse(200, created(), AUTH),
        FakeResponse(200, {"response": {}}, AUTH),
        FakeResponse(200, completed(), AUTH2),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    await signin.async_finish(attempt, " 123456 ")

    create, prepare, attempt_call = session.calls
    assert create["data"] == {"identifier": "someone@example.test"}
    assert "/v1/client/sign_ins?" in create["url"]
    assert "_is_native=1" in create["url"], "native mode, not the browser flow"
    # Origin must never be sent: Clerk rejects Origin and Authorization together.
    assert not any(h.lower() == "origin" for h in create["headers"])

    assert prepare["data"] == {"strategy": "email_code", "email_address_id": "idn_9"}
    assert "/prepare_first_factor" in prepare["url"]

    assert attempt_call["data"] == {"strategy": "email_code", "code": "123456"}, "code trimmed"
    assert "/attempt_first_factor" in attempt_call["url"]
    assert attempt_call["headers"]["Authorization"] == "Bearer client_jwt_v1"


async def test_an_unknown_email_is_its_own_error():
    session = FakeSession(
        FakeResponse(422, {"errors": [{"code": "form_identifier_not_found", "message": "no"}]})
    )
    with pytest.raises(ClerkUnknownEmail):
        await ClerkSignIn(session).async_start("nobody@example.test")


async def test_a_wrong_code_is_distinct_from_an_expired_one():
    """The whole point of separating them: a wrong code is retyped on the same
    screen, an expired one has to start again. Conflating them either sends
    the user round a loop that cannot succeed, or re-requests a code on every
    typo and rate-limits the account."""
    session = FakeSession(
        FakeResponse(200, created(), AUTH),
        FakeResponse(200, {"response": {}}, AUTH),
        FakeResponse(422, {"errors": [{"code": "form_code_incorrect", "message": "wrong"}]}),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    with pytest.raises(ClerkBadCode):
        await signin.async_finish(attempt, "000000")

    session = FakeSession(
        FakeResponse(422, {"errors": [{"code": "verification_expired", "message": "old"}]})
    )
    signin = ClerkSignIn(session)
    with pytest.raises(ClerkCodeExpired):
        await signin.async_finish(
            type(attempt)(sign_in_id="si_1", email_address_id="idn_9", client_jwt="c"), "000000"
        )


async def test_rate_limiting_is_not_reported_as_a_connection_failure():
    """429 means wait. Telling the user to retry is how an account gets
    locked out."""
    session = FakeSession(FakeResponse(429, {"errors": [{"code": "too_many_requests"}]}))
    with pytest.raises(ClerkRateLimited):
        await ClerkSignIn(session).async_start("someone@example.test")


async def test_an_account_without_email_code_is_refused_clearly():
    """Social-only accounts exist; saying so beats a generic failure."""
    session = FakeSession(
        FakeResponse(200, created(factors=[{"strategy": "oauth_google"}]), AUTH)
    )
    with pytest.raises(ClerkSignInError, match="Google or Apple"):
        await ClerkSignIn(session).async_start("someone@example.test")


async def test_two_factor_accounts_are_refused_rather_than_half_completed():
    session = FakeSession(FakeResponse(200, created(status="needs_second_factor"), AUTH))
    with pytest.raises(ClerkSecondFactorRequired):
        await ClerkSignIn(session).async_start("someone@example.test")


async def test_a_sign_in_that_does_not_complete_is_an_error_not_a_silent_pass():
    session = FakeSession(
        FakeResponse(200, created(), AUTH),
        FakeResponse(200, {"response": {}}, AUTH),
        FakeResponse(200, {"response": {"status": "needs_first_factor"}}, AUTH),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    with pytest.raises(ClerkSignInError, match="did not complete"):
        await signin.async_finish(attempt, "123456")


async def test_the_credential_is_also_taken_from_the_clerk_db_jwt_header():
    """Which header carries the native client credential is the biggest
    inference in this module. Authorization is what it was built against;
    Clerk-Db-Jwt is a real alternative, because the app bundle carries that
    exact header name. Accepting both removes a whole failure mode from the
    one real sign-in anybody gets to run."""
    session = FakeSession(
        FakeResponse(200, created(), {"Clerk-Db-Jwt": "db_jwt_v1"}),
        FakeResponse(200, {"response": {}}, {"Clerk-Db-Jwt": "db_jwt_v1"}),
        FakeResponse(200, completed(), {"Clerk-Db-Jwt": "db_jwt_v2"}),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    assert await signin.async_finish(attempt, "123456") == "db_jwt_v2"


async def test_a_bare_credential_without_the_bearer_prefix_is_accepted():
    session = FakeSession(
        FakeResponse(200, created(), {"Authorization": "raw_jwt"}),
        FakeResponse(200, {"response": {}}, {"Authorization": "raw_jwt"}),
        FakeResponse(200, completed(), {"Authorization": "raw_jwt"}),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    assert await signin.async_finish(attempt, "123456") == "raw_jwt"


async def test_an_unmapped_error_code_is_logged_by_name(caplog):
    """The mapping was read from Clerk's documented codes, not observed here,
    so an unmapped one is the likeliest way this is wrong. The log has to name
    the code, or diagnosing it costs the user another round trip."""
    session = FakeSession(
        FakeResponse(422, {"errors": [{"code": "form_code_not_allowed", "message": "nope"}]})
    )
    with pytest.raises(ClerkSignInError):
        await ClerkSignIn(session).async_start("someone@example.test")
    assert "form_code_not_allowed" in caplog.text


async def test_completing_without_a_credential_is_refused():
    """A 'complete' with no Authorization header would otherwise store None
    and fail later, far from the cause."""
    session = FakeSession(
        FakeResponse(200, created(), {}),
        FakeResponse(200, {"response": {}}, {}),
        FakeResponse(200, completed(), {}),
    )
    signin = ClerkSignIn(session)
    attempt = await signin.async_start("someone@example.test")
    with pytest.raises(ClerkSignInError, match="no client credential"):
        await signin.async_finish(attempt, "123456")
