"""Clerk session tokens for the Base Power API.

Base authenticates with Clerk and issues **two** credentials. The bearer JWT
the API wants (`__session`) lives about 60 seconds, which is useless to store.
The durable one is the client credential (`__client`), and it mints fresh
session JWTs on demand - which is what the mobile app does, and what this
does.

Four things Clerk's frontend API requires, each established by being refused
without it (see `docs/API.md`):

- the path is `GET /v1/client`, not `/v1/client/sync`
- the API-version query parameters must be present
- `Origin` must be set, and `Authorization` must NOT be sent alongside it -
  Clerk rejects both together outright
- a browser `User-Agent`; the identical request is refused 403 as
  `Python-urllib`

A minted token is cached and reused until it is close to expiry, so a poll
costs one API call rather than three.
"""

from __future__ import annotations

import logging
import time
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


class ClerkAuthError(Exception):
    """The client credential is missing, rejected or no longer has a session.

    This is the one the user has to fix by signing in again, so it is
    distinct from a transport failure.
    """


class ClerkSessionProvider:
    """Mints and caches Base Power session tokens from a client credential."""

    def __init__(self, session: Any, client_jwt: str) -> None:
        self._session = session
        self._client_jwt = client_jwt
        self._token: str | None = None
        self._expires_at = 0.0
        self._session_id: str | None = None

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

    async def _async_active_session_id(self) -> str:
        async with self._session.get(
            f"{CLERK_HOST}/v1/client?{CLERK_PARAMS}", headers=self._headers
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(body, dict):
                raise ClerkAuthError(
                    f"Clerk refused the stored credential (HTTP {resp.status}); "
                    "sign in again to renew it"
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
        return str(active["id"])

    async def _async_mint(self, session_id: str) -> str:
        async with self._session.post(
            f"{CLERK_HOST}/v1/client/sessions/{session_id}/tokens?{CLERK_PARAMS}",
            headers=self._headers,
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(body, dict) or not body.get("jwt"):
                raise ClerkAuthError(f"could not mint a session token (HTTP {resp.status})")
        return str(body["jwt"])
