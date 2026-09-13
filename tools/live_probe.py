#!/usr/bin/env python3
"""One read-only call against the live API, to test the recovered contract.

Reads credentials from a file so no secret is ever pasted into a terminal or
a transcript, and **never prints a token** - not even truncated. What it
prints is the structure of the response, which is the thing under test.

The file is `key=value` lines:

    session=<the __session cookie: the bearer JWT, ~60 s lifetime>
    client=<the __client cookie: durable, used to mint a fresh session>

Either line alone is enough. With `client` it mints a fresh session token the
way the app does, so the 60 s expiry stops mattering.

    python tools/live_probe.py path/to/creds.txt [--snapshot]

Read-only by construction: it calls ListLocations, and GetSnapshot only with
--snapshot. It can reach no method that changes anything - StartManualBackup
and ResetOvercurrent are not wired here on purpose.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

API_HOST = "https://dashboard.baseapis.net"
CLERK_HOST = "https://clerk.basepowercompany.com"
PORTAL = "https://account.basepowercompany.com"
CLERK_PARAMS = "__clerk_api_version=2025-04-10&_clerk_js_version=5.40.0"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
PACKAGE = "dashboard.mobile.v2"
READ_ONLY = {
    ("LocationsService", "ListLocations"),
    ("BatteryService", "GetSnapshot"),
    ("UsageService", "GetRecentPower"),
    ("UsageService", "GetRecentGridVoltage"),
    ("LocationsService", "GetLocation"),
}


def load_creds(path: str) -> dict[str, str]:
    creds: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip().lower()] = v.strip().strip('"').strip("'")
    return {k: v for k, v in creds.items() if v}


def get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    return _request(urllib.request.Request(url, headers=headers, method="GET"))


def post(url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, object]:
    req = urllib.request.Request(url, data=body if body is not None else b"", headers=headers)
    return _request(req)


def _request(req: urllib.request.Request) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except urllib.error.URLError as e:
        return 0, {"transport_error": str(e.reason)}
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception:
        return status, raw[:400].decode("utf-8", "replace")


def mint_session_token(client_jwt: str) -> str | None:
    """Swap the durable client credential for a fresh session JWT.

    This is the mechanism the integration will use per poll: the client
    endpoint reports the sessions this client owns, and the active one mints
    a token at /tokens.

    Three things Clerk's frontend API requires, each learned by being refused
    without it:

    - the path is `GET /v1/client`, not `/v1/client/sync`
    - `Origin` must be set (a browser client is bound to its origin), and
      the API-version query params must be present
    - `Origin` and `Authorization` must NEVER both be sent - Clerk rejects
      that outright as a security measure, so a cookie client uses Origin
      and a native client uses Authorization, never both
    """
    # A browser User-Agent is required, not cosmetic: the identical request
    # succeeds under curl and is refused 403 as Python-urllib. The cookie
    # credential belongs to a browser client bound to this origin, so the
    # request has to look like what that client is.
    headers = {
        "Cookie": f"__client={client_jwt}",
        "Origin": PORTAL,
        "Referer": f"{PORTAL}/",
        "Content-Type": "application/json",
        "User-Agent": BROWSER_UA,
    }
    status, body = get(f"{CLERK_HOST}/v1/client?{CLERK_PARAMS}", headers)
    if status != 200 or not isinstance(body, dict):
        print(f"  GET /v1/client -> HTTP {status} (cannot mint; falling back to session=)")
        return None
    response = body.get("response") or body
    sessions = response.get("sessions") or []
    active = next((s for s in sessions if s.get("status") == "active"), None) or (
        sessions[0] if sessions else None
    )
    if not active:
        print("  /v1/client ok but no session on this client")
        return None
    sid = active.get("id")
    status, body = post(
        f"{CLERK_HOST}/v1/client/sessions/{sid}/tokens?{CLERK_PARAMS}", b"", headers
    )
    if status == 200 and isinstance(body, dict) and body.get("jwt"):
        print(f"  minted a fresh session token from the client credential (session {sid[:8]}...)")
        return str(body["jwt"])
    print(f"  token mint -> HTTP {status}")
    return None


def call(service: str, method: str, payload: dict, token: str) -> tuple[int, object]:
    if (service, method) not in READ_ONLY:
        raise SystemExit(f"refusing {service}/{method}: this probe is read-only")
    return post(
        f"{API_HOST}/{PACKAGE}.{service}/{method}",
        json.dumps(payload).encode(),
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def redact(obj, depth=0):
    """Show structure and types, not personal detail.

    The point of the probe is whether the contract is right, so values are
    replaced by their shape. Addresses and names are not needed to learn that
    and would otherwise land in a transcript.
    """
    pad = "  " * depth
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            rendered = redact(v, depth + 1)
            if isinstance(v, (dict, list)):
                rendered = rendered.lstrip()
            out.append(f"{pad}{k}: {rendered}")
        return "\n" + "\n".join(out) if out else "{}"
    if isinstance(obj, list):
        if not obj:
            return "[] (empty)"
        return f"[{len(obj)} item(s)], first:" + redact(obj[0], depth + 1)
    if isinstance(obj, bool):
        return f"<bool {obj}>"
    if isinstance(obj, (int, float)):
        return f"<number {obj}>"
    if isinstance(obj, str):
        return f"<str len={len(obj)}>"
    return f"<{type(obj).__name__}>"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("creds")
    ap.add_argument(
        "--snapshot", action="store_true", help="also call BatteryService/GetSnapshot"
    )
    ap.add_argument(
        "--usage", action="store_true", help="also call the read-only UsageService methods"
    )
    ap.add_argument(
        "--location", action="store_true", help="also call LocationsService/GetLocation"
    )
    a = ap.parse_args()

    creds = load_creds(a.creds)
    if not creds:
        raise SystemExit(f"no usable credentials in {a.creds}")
    print(f"loaded: {', '.join(sorted(creds))} (values never printed)")

    token = mint_session_token(creds["client"]) if "client" in creds else None
    if not token:
        token = creds.get("session")
    if not token:
        raise SystemExit("no session token available")

    print("\n=== LocationsService/ListLocations ===")
    status, body = call("LocationsService", "ListLocations", {}, token)
    print(f"HTTP {status}")
    if status != 200:
        print(f"body: {body}")
        if status in (401, 403):
            print("\n-> the token was refused or has expired; re-copy __session and retry")
        raise SystemExit(1)
    print(redact(body))

    address_ids = []
    for loc in (body or {}).get("locations", []) if isinstance(body, dict) else []:
        summary = loc.get("summary") if isinstance(loc.get("summary"), dict) else {}
        aid = loc.get("addressId") or summary.get("addressId") or summary.get("id")
        if aid:
            address_ids.append(aid)
    print(f"\naddress_id(s) found: {len(address_ids)}")

    if a.location and address_ids:
        print("\n=== LocationsService/GetLocation ===")
        status, body = call("LocationsService", "GetLocation", {"addressId": address_ids[0]}, token)
        print(f"HTTP {status}")
        print(redact(body) if status == 200 else f"body: {body}")

    if a.usage and address_ids:
        for method in ("GetRecentPower", "GetRecentGridVoltage"):
            print(f"\n=== UsageService/{method} ===")
            status, body = call("UsageService", method, {"addressId": address_ids[0]}, token)
            print(f"HTTP {status}")
            print(redact(body) if status == 200 else f"body: {body}")

    if a.snapshot and address_ids:
        print("\n=== BatteryService/GetSnapshot ===")
        status, body = call("BatteryService", "GetSnapshot", {"addressId": address_ids[0]}, token)
        print(f"HTTP {status}")
        print(redact(body) if status == 200 else f"body: {body}")
        if status == 200 and isinstance(body, dict):
            snap = body.get("snapshot") or {}
            variants = [k for k in snap if k != "wifi"]
            print(f"\nsnapshot variant(s) present: {variants}")


if __name__ == "__main__":
    main()
