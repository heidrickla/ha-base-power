"""Clerk session tokens for the Base Power API.

Base authenticates with Clerk and issues two credentials. The bearer JWT the
API wants (`__session`) lives about 60 seconds, which is useless to store. The
durable one is the client credential (`__client`), which mints fresh session
JWTs on demand, as the mobile app does.

Four things Clerk's frontend API requires, each established by being refused
without it:

- the path is `GET /v1/client`, not `/v1/client/sync`
- the API-version query parameters must be present
- `Origin` must be set, and `Authorization` must not be sent alongside it
- a browser `User-Agent`; the identical request is refused 403 as
  `Python-urllib`

A minted token is cached and reused until it is close to expiry, so a poll
costs one API call rather than three.

Only a refusal of the credential asks the user to sign in again. A timeout, a
rate limit or a server error says nothing about the credential, so it fails
the poll and the next one retries.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

CLERK_HOST = "https://clerk.basepowercompany.com"
PORTAL = "https://account.basepowercompany.com"
CLERK_PARAMS = "__clerk_api_version=2025-04-10&_clerk_js_version=5.40.0"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Session JWTs last ~60 s. Re-mint with this much left rather than racing the
# expiry: a token that dies in flight costs a whole poll.
REFRESH_MARGIN = 15.0
# What a mint is assumed to buy when Clerk does not say. Deliberately short:
# guessing long would hand out a dead token.
ASSUMED_LIFETIME = 50.0
# Statuses that describe Clerk or the path to it, never the credential.
TRANSIENT_STATUSES = frozenset({408, 425, 429})
# Clerk's session timestamps, in epoch milliseconds, copied for diagnostics.
SESSION_TIMES = ("created_at", "last_active_at", "expire_at", "abandon_at")


class ClerkAuthError(Exception):
    """The client credential is missing, rejected or no longer has a session.

    This is the one the user has to fix by signing in again, so it is
    distinct from a transport failure.
    """


class ClerkUnavailableError(Exception):
    """Clerk did not answer usefully: no response, a rate limit or a server error.

    The stored credential may be fine, so this fails one poll and never starts
    reauthentication.
    """


def _iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


class ClerkSessionProvider:
    """Mints and caches Base Power session tokens from a client credential."""

    def __init__(self, session: Any, client_jwt: str) -> None:
        self._session = session
        self._client_jwt = client_jwt
        self._token: str | None = None
        self._expires_at = 0.0
        self._session_id: str | None = None
        self.session_times: dict[str, str | None] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": f"__client={self._client_jwt}",
            "Origin": PORTAL,
            "Referer": f"{PORTAL}/",
            "Content-Type": "application/json",
            "User-Agent": BROWSER_UA,
        }

    async def async_get_token(self) -> str:
        """A currently-valid session JWT, minting one only when needed."""
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        return await self.async_refresh()

    async def async_refresh(self) -> str:
        """Mint a new session token, discovering the session if necessary."""
        if not self._session_id:
            self._session_id = await self._async_active_session_id()
        try:
            token = await self._async_mint(self._session_id)
        except ClerkAuthError:
            # The remembered session may simply have ended; look again once
            # before declaring the credential dead, so an ordinary session
            # rotation does not surface as "sign in again".
            self._session_id = await self._async_active_session_id()
            token = await self._async_mint(self._session_id)
        self._token = token
        self._expires_at = time.monotonic() + ASSUMED_LIFETIME - REFRESH_MARGIN
        return token

    async def _async_request(self, method: str, url: str) -> tuple[int, Any]:
        """(status, decoded body or None); ClerkUnavailableError when the answer
        says nothing about the credential. A transport error reaches the caller
        unchanged: Home Assistant's coordinator fails the poll on it."""
        async with getattr(self._session, method)(url, headers=self._headers) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                body = None
                if status == 200:
                    # A 200 that is not JSON is a proxy or a challenge page.
                    raise ClerkUnavailableError("Clerk answered HTTP 200 without JSON") from None
        if status in TRANSIENT_STATUSES or status >= 500:
            raise ClerkUnavailableError(f"Clerk answered HTTP {status}; the sign-in is kept")
        return status, body

    async def _async_active_session_id(self) -> str:
        status, body = await self._async_request("get", f"{CLERK_HOST}/v1/client?{CLERK_PARAMS}")
        if status != 200 or not isinstance(body, dict):
            raise ClerkAuthError(
                f"Clerk refused the stored credential (HTTP {status}); sign in again to renew it"
            )
        # Every step here degrades to "no active session" rather than walking
        # into whatever shape arrived. The failure that matters is not a
        # malformed response - it is raising AttributeError instead of
        # ClerkAuthError, because only ClerkAuthError starts reauthentication.
        # Anything else surfaces as an unexpected error and the user is never
        # asked to sign in again.
        nested = body.get("response")
        response = nested if isinstance(nested, dict) else body
        raw_sessions = response.get("sessions")
        sessions = raw_sessions if isinstance(raw_sessions, list) else []
        active = next(
            (
                s
                for s in sessions
                if isinstance(s, dict) and s.get("status") == "active" and s.get("id")
            ),
            None,
        )
        if active is None:
            raise ClerkAuthError("the stored credential has no active session; sign in again")
        self.session_times = {key: _iso(active.get(key)) for key in SESSION_TIMES}
        return str(active["id"])

    async def _async_mint(self, session_id: str) -> str:
        status, body = await self._async_request(
            "post", f"{CLERK_HOST}/v1/client/sessions/{session_id}/tokens?{CLERK_PARAMS}"
        )
        if status != 200 or not isinstance(body, dict) or not body.get("jwt"):
            raise ClerkAuthError(f"could not mint a session token (HTTP {status})")
        return str(body["jwt"])
