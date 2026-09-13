"""The session-token provider, against faked Clerk responses.

The auth hot path: every poll goes through async_get_token, so a mistake here
is an integration that stops working and does not say why.

The contract these pin is "degrades to sign in again", not "does not crash".
An unexpected Clerk response must raise ClerkAuthError, which is what the
entry translates into a reauth prompt. Any other exception escapes as an
unexpected error, the user is never prompted, and the integration stays
broken.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "base_power"))

from clerk import (
    ASSUMED_LIFETIME,
    CLERK_HOST,
    REFRESH_MARGIN,
    ClerkAuthError,
    ClerkSessionProvider,
)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Queued responses, and every call recorded so the request shape is
    assertable - the header contract is half of what makes Clerk answer."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _next(self, method, url, headers):
        self.calls.append({"method": method, "url": url, "headers": headers or {}})
        if not self._responses:
            raise AssertionError(f"unexpected extra {method} to {url}")
        return self._responses.pop(0)

    def get(self, url, headers=None):
        return self._next("GET", url, headers)

    def post(self, url, headers=None, data=None):
        return self._next("POST", url, headers)


def client_ok(session_id="sess_1", status="active"):
    return FakeResponse(200, {"response": {"sessions": [{"id": session_id, "status": status}]}})


def minted(jwt="jwt_abc"):
    return FakeResponse(200, {"jwt": jwt})


# ------------------------------------------------------------ the happy path


async def test_a_token_is_minted_from_the_stored_credential():
    session = FakeSession(client_ok(), minted())
    provider = ClerkSessionProvider(session, "__client_value")
    assert await provider.async_get_token() == "jwt_abc"
    assert [c["method"] for c in session.calls] == ["GET", "POST"]
    assert session.calls[1]["url"].startswith(f"{CLERK_HOST}/v1/client/sessions/sess_1/tokens")


async def test_the_second_call_reuses_the_cached_token():
    """A poll every 30 s against a ~60 s token must not cost three requests."""
    session = FakeSession(client_ok(), minted())
    provider = ClerkSessionProvider(session, "__client_value")
    await provider.async_get_token()
    assert await provider.async_get_token() == "jwt_abc"
    assert len(session.calls) == 2  # no further traffic


async def test_the_cache_expires_before_the_token_does(monkeypatch):
    """The margin exists so a token cannot die in flight. Pinned as an
    inequality rather than a number: what matters is that the cache gives up
    while the token is still valid, not by how much."""
    import clerk

    now = [1000.0]
    monkeypatch.setattr(clerk.time, "monotonic", lambda: now[0])
    session = FakeSession(client_ok(), minted("first"), minted("second"))
    provider = ClerkSessionProvider(session, "__client_value")
    assert await provider.async_get_token() == "first"

    now[0] += ASSUMED_LIFETIME - REFRESH_MARGIN + 0.1
    assert await provider.async_get_token() == "second"
    assert REFRESH_MARGIN > 0


async def test_a_rotated_session_is_rediscovered_rather_than_declared_dead():
    """Clerk rotating the session id is ordinary, not a revoked credential.
    Minting fails, the provider looks the session up again and succeeds -
    without this the user is told to sign in again for nothing."""
    session = FakeSession(
        client_ok("sess_old"),
        FakeResponse(401, {}),
        client_ok("sess_new"),
        minted("jwt_after_rotation"),
    )
    provider = ClerkSessionProvider(session, "__client_value")
    assert await provider.async_get_token() == "jwt_after_rotation"
    assert session.calls[3]["url"].startswith(f"{CLERK_HOST}/v1/client/sessions/sess_new/tokens")


# ------------------------------------------------- the shapes that must not crash


@pytest.mark.parametrize(
    "body",
    [
        {"response": "not an object"},
        {"response": {"sessions": "not a list"}},
        {"response": {"sessions": ["not an object"]}},
        {"response": {"sessions": [{"status": "active"}]}},  # active, but no id
        {"response": {"sessions": []}},
        {"response": {}},
        {},
    ],
    ids=[
        "response-not-an-object",
        "sessions-not-a-list",
        "session-entry-not-an-object",
        "active-session-without-an-id",
        "no-sessions",
        "empty-response",
        "empty-body",
    ],
)
async def test_an_unexpected_shape_asks_for_a_new_sign_in_rather_than_raising(body):
    """Every one of these used to reach `.get` on a str, or index a missing
    key. AttributeError and KeyError both escape the reauth path: the entry
    goes to error, and the user is never shown the sign-in prompt that would
    actually fix it."""
    session = FakeSession(FakeResponse(200, body))
    provider = ClerkSessionProvider(session, "__client_value")
    with pytest.raises(ClerkAuthError):
        await provider.async_get_token()


async def test_only_an_active_session_is_used():
    session = FakeSession(client_ok(status="ended"))
    provider = ClerkSessionProvider(session, "__client_value")
    with pytest.raises(ClerkAuthError):
        await provider.async_get_token()


async def test_a_refused_credential_is_an_auth_error_not_a_transport_one():
    session = FakeSession(FakeResponse(401, {}))
    provider = ClerkSessionProvider(session, "__client_value")
    with pytest.raises(ClerkAuthError):
        await provider.async_get_token()


async def test_a_mint_that_returns_no_jwt_is_an_auth_error():
    """200 with the wrong body. Without the jwt check this returns the string
    "None" as a bearer token and every API call fails 401 instead."""
    # Four responses because the first failed mint triggers the rediscover
    # retry; the second mint has to fail too for the error to surface.
    session = FakeSession(
        client_ok(),
        FakeResponse(200, {"not_jwt": "x"}),
        client_ok(),
        FakeResponse(200, {}),
    )
    provider = ClerkSessionProvider(session, "__client_value")
    with pytest.raises(ClerkAuthError):
        await provider.async_get_token()


# ---------------------------------------------------------- the header contract


async def test_the_headers_are_the_ones_clerk_actually_requires():
    """Each of these was established by being refused without it. The
    Authorization absence is the subtle one: Clerk rejects Origin and
    Authorization together, so sending both - the obvious thing to do - fails
    outright. See the module docstring in clerk.py."""
    session = FakeSession(client_ok(), minted())
    provider = ClerkSessionProvider(session, "__client_value")
    await provider.async_get_token()

    for call in session.calls:
        h = call["headers"]
        assert h["Cookie"] == "__client=__client_value"
        assert h["Origin"].startswith("https://")
        assert "Mozilla/5.0" in h["User-Agent"]
        assert "Authorization" not in h
