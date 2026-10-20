"""Client for the Base Power mobile API.

Connect RPC over HTTP to `dashboard.baseapis.net`, package
`dashboard.mobile.v2`. Connect's JSON codec is used rather than protobuf, so
no protobuf runtime is needed: the wire is JSON with lowerCamelCase field
names, and `docs/API.md` plus `proto/` are the authoritative contract.

Everything that parses a response is a pure function, testable without a
network or a token. The transport is a thin wrapper over an aiohttp session.

The shapes come from descriptors embedded in the app. `GetSnapshot`,
`ListLocations`, `GetLocation`, `GetDailyOverview` and `ListAccounts` are
confirmed live; the rest are still readings of the binary, so the parsers stay
tolerant of fields they have never seen populated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DEFAULT_HOST = "https://dashboard.baseapis.net"
PACKAGE = "dashboard.mobile.v2"

# BatterySnapshot is a union: exactly one key is populated, and which one is
# the battery's operating state. JSON uses lowerCamelCase.
SNAPSHOT_STATES: dict[str, str] = {
    "onGrid": "on_grid",
    "offGridOutage": "off_grid_outage",
    "offGridNoHomePower": "off_grid_no_home_power",
    "offGridOvercurrent": "off_grid_overcurrent",
    "offGridOvercurrentStandby": "off_grid_overcurrent_standby",
    "telemetryUnavailable": "telemetry_unavailable",
}

# Running off the battery rather than the grid. `off_grid_outage` is the one
# an automation usually wants.
OFF_GRID_STATES = frozenset(
    {
        "off_grid_outage",
        "off_grid_no_home_power",
        "off_grid_overcurrent",
        "off_grid_overcurrent_standby",
    }
)


class BasePowerError(Exception):
    """Any failure talking to the API."""


class BasePowerAuthError(BasePowerError):
    """The token was missing, rejected or expired.

    Separate because a Clerk session token is short-lived by design, so this
    is the ordinary "refresh and retry" case rather than a fault.
    """


def _parse_timestamp(value: Any) -> datetime | None:
    """A google.protobuf.Timestamp as Connect JSON renders it: RFC 3339.

    None rather than raising: an unparseable observation time must not cost
    the reading beside it.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    """Connect JSON may render a double as a number or a string."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return None if f is None else int(f)


def _as_dict(value: Any) -> dict[str, Any]:
    """A nested message if it is one, an empty mapping otherwise.

    `x.get("k") or {}` is only safe while the value is a mapping or absent; a
    string there raises AttributeError two lines later. This makes "not the
    shape I expected" and "not present" the same outcome, which is what the
    callers assume.
    """
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PowerFlow:
    """BatteryPowerFlow, all kW.

    Absent fields stay None rather than 0.0. "No solar reported" and "solar is
    producing nothing" are different claims, and a sensor reading a confident
    zero for a value nobody sent is indistinguishable from a real one.
    """

    from_grid_kw: float | None = None
    from_storage_kw: float | None = None
    from_solar_kw: float | None = None
    non_solar_to_home_kw: float | None = None
    to_home_kw: float | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> PowerFlow:
        d = data or {}
        return cls(
            from_grid_kw=_as_float(d.get("fromGridKw")),
            from_storage_kw=_as_float(d.get("fromStorageKw")),
            from_solar_kw=_as_float(d.get("fromSolarKw")),
            non_solar_to_home_kw=_as_float(d.get("nonSolarToHomeKw")),
            to_home_kw=_as_float(d.get("toHomeKw")),
        )


@dataclass(frozen=True)
class BatterySnapshot:
    """One GetSnapshot response, flattened out of its state union."""

    state: str
    observed_at: datetime | None = None
    state_of_energy_percent: int | None = None
    power_flow: PowerFlow = field(default_factory=PowerFlow)
    estimated_backup_hours_at_current_usage: float | None = None
    estimated_backup_hours_at_750_watts: float | None = None
    overcurrent_limit_kw: float | None = None
    wifi_ssid: str | None = None
    wifi_status: str | None = None

    @property
    def is_off_grid(self) -> bool:
        return self.state in OFF_GRID_STATES

    @property
    def is_grid_outage(self) -> bool:
        """The headline condition: the grid is down and the battery is carrying."""
        return self.state == "off_grid_outage"

    @property
    def telemetry_available(self) -> bool:
        return self.state not in ("telemetry_unavailable", "unknown")

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BatterySnapshot:
        """Flatten GetBatterySnapshotResponse.

        The response nests the union under `snapshot`, but callers hand either
        the outer or the inner object, so accept both. An unrecognised variant
        becomes `unknown` rather than raising, so an unfamiliar shape degrades
        to "I do not know" instead of taking the integration down.
        """
        nested = data.get("snapshot")
        snapshot = nested if isinstance(nested, dict) else data
        wifi = _as_dict(snapshot.get("wifi"))

        for wire_key, state in SNAPSHOT_STATES.items():
            variant = snapshot.get(wire_key)
            if not isinstance(variant, dict):
                continue
            return cls(
                state=state,
                observed_at=_parse_timestamp(variant.get("observedAt")),
                state_of_energy_percent=_as_int(variant.get("stateOfEnergyPercent")),
                power_flow=PowerFlow.from_json(variant.get("powerFlow")),
                estimated_backup_hours_at_current_usage=_as_float(
                    variant.get("estimatedBackupHoursAtCurrentUsage")
                ),
                estimated_backup_hours_at_750_watts=_as_float(
                    variant.get("estimatedBackupHours750Watts")
                    or variant.get("estimatedBackupHoursAt750Watts")
                ),
                overcurrent_limit_kw=_as_float(variant.get("overcurrentLimitKw")),
                wifi_ssid=wifi.get("ssid"),
                wifi_status=wifi.get("status"),
            )
        return cls(state="unknown", wifi_ssid=wifi.get("ssid"), wifi_status=wifi.get("status"))


@dataclass(frozen=True)
class LocationCapabilities:
    """What a site has, as GetLocation declares it.

    proto3 omits false booleans, so an absent key is a capability the site
    does not have, which is why these default to False rather than None.
    Confirmed live: a site without solar carries no `hasSolar` key at all and
    its snapshots omit `fromSolarKw` to match.
    """

    has_solar: bool = False
    billing: bool = False
    telemetry: bool = False
    automatic_backup: bool = False
    wifi_management: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LocationCapabilities:
        nested = data.get("location")
        location = nested if isinstance(nested, dict) else data
        caps = _as_dict(location.get("capabilities"))
        battery = _as_dict(location.get("battery"))
        onsite = _as_dict(battery.get("onsite"))
        battery_caps = _as_dict(onsite.get("capabilities"))
        return cls(
            has_solar=bool(caps.get("hasSolar")),
            billing=bool(caps.get("billing")),
            telemetry=bool(battery_caps.get("telemetry")),
            automatic_backup=bool(battery_caps.get("automaticBackup")),
            wifi_management=bool(battery_caps.get("wifiManagement")),
        )


@dataclass(frozen=True)
class Location:
    """A site. Its `address_id` is what every other call is scoped by."""

    address_id: str
    name: str | None = None

    @classmethod
    def list_from_json(cls, data: dict[str, Any]) -> list[Location]:
        out: list[Location] = []
        for loc in data.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            summary = _as_dict(loc.get("summary"))
            address_id = (
                loc.get("addressId")
                or summary.get("addressId")
                or summary.get("id")
                or loc.get("id")
            )
            if not address_id:
                continue
            out.append(
                cls(
                    address_id=str(address_id),
                    name=summary.get("name") or summary.get("label") or summary.get("addressLine1"),
                )
            )
        return out


def _hour_series(samples: Any, key: str, scale: float = 1.0) -> tuple[tuple[datetime, float], ...]:
    """(interval start, value) for each hourly sample, oldest first.

    A sample in the list with the field absent is 0.0, which is how proto3
    JSON writes a zero. A sample with no parseable start, or one not on the
    hour, is dropped: long-term statistics are keyed by the top of the hour.
    """
    if not isinstance(samples, list):
        return ()
    out: dict[datetime, float] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        start = _parse_timestamp(sample.get("intervalStart"))
        if start is None or start.minute or start.second or start.microsecond:
            continue
        value = _as_float(sample.get(key))
        out[start] = (value or 0.0) * scale
    return tuple(sorted(out.items()))


@dataclass(frozen=True)
class DailyOverview:
    """The hourly series of GetDailyOverview: the last 24 completed hours.

    Energy in kWh. Cost in currency units, converted from the API's cents.
    """

    energy_to_home: tuple[tuple[datetime, float], ...] = ()
    solar_to_home: tuple[tuple[datetime, float], ...] = ()
    solar_export: tuple[tuple[datetime, float], ...] = ()
    grid_cost: tuple[tuple[datetime, float], ...] = ()
    currency: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DailyOverview:
        energy = data.get("hourlyEnergy")
        costs = data.get("hourlyEnergyCosts")
        currency = data.get("costCurrency")
        return cls(
            energy_to_home=_hour_series(energy, "energyToHomeKwh"),
            solar_to_home=_hour_series(energy, "solarToHomeKwh"),
            solar_export=_hour_series(energy, "solarExportKwh"),
            grid_cost=_hour_series(costs, "gridEnergyCents", 0.01),
            currency=currency if isinstance(currency, str) and currency else None,
        )


# AccountType without its prefix; UNSPECIFIED and anything unfamiliar are skipped.
ACCOUNT_TYPES: dict[str, str] = {
    "ACCOUNT_TYPE_ENERGY": "energy",
    "ACCOUNT_TYPE_BATTERY": "battery",
}


@dataclass(frozen=True)
class Account:
    """One billing account of a site: energy service or battery membership."""

    account_type: str
    currency: str | None = None
    last_bill: float | None = None

    @classmethod
    def list_from_json(cls, data: dict[str, Any]) -> list[Account]:
        """ListAccounts.

        `lastBillAmountCents` is an int64, which Connect JSON writes as a
        string, and proto3 omits a zero. An account with no billing history
        has no last bill at all, so it reads None; with history, absent is a
        bill of zero.
        """
        out: list[Account] = []
        for raw in data.get("accounts") or []:
            if not isinstance(raw, dict):
                continue
            account_type = ACCOUNT_TYPES.get(str(raw.get("accountType") or ""))
            if account_type is None:
                continue
            cents = _as_float(raw.get("lastBillAmountCents"))
            if raw.get("hasBillingHistory"):
                last_bill: float | None = round((cents or 0.0) / 100, 2)
            else:
                last_bill = None
            currency = raw.get("currency")
            out.append(
                cls(
                    account_type=account_type,
                    currency=currency if isinstance(currency, str) and currency else None,
                    last_bill=last_bill,
                )
            )
        return out


class BasePowerClient:
    """Thin Connect-RPC client.

    `token_provider` is an async callable returning a current Clerk session
    token. A callable rather than a string because those tokens live ~60 s, so
    the caller owns refresh and this never holds a stale one.
    """

    def __init__(self, session: Any, token_provider: Any, host: str = DEFAULT_HOST) -> None:
        self._session = session
        self._token_provider = token_provider
        self._host = host.rstrip("/")

    def url(self, service: str, method: str) -> str:
        return f"{self._host}/{PACKAGE}.{service}/{method}"

    async def call(self, service: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = await self._token_provider()
        if not token:
            raise BasePowerAuthError("no Clerk session token available")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with self._session.post(
            self.url(service, method), json=payload, headers=headers
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status == 200:
                return body if isinstance(body, dict) else {}
            raise _error_for(resp.status, body)

    async def list_locations(self) -> list[Location]:
        return Location.list_from_json(await self.call("LocationsService", "ListLocations", {}))

    async def get_capabilities(self, address_id: str) -> LocationCapabilities:
        return LocationCapabilities.from_json(
            await self.call("LocationsService", "GetLocation", {"addressId": address_id})
        )

    async def get_snapshot(self, address_id: str) -> BatterySnapshot:
        return BatterySnapshot.from_json(
            await self.call("BatteryService", "GetSnapshot", {"addressId": address_id})
        )

    async def get_daily_overview(self, address_id: str) -> DailyOverview:
        return DailyOverview.from_json(
            await self.call("UsageService", "GetDailyOverview", {"addressId": address_id})
        )

    async def list_accounts(self, address_id: str) -> list[Account]:
        return Account.list_from_json(
            await self.call("BillingService", "ListAccounts", {"addressId": address_id})
        )

    async def get_recent_power(self, address_id: str) -> list[dict[str, Any]]:
        data = await self.call("UsageService", "GetRecentPower", {"addressId": address_id})
        return _samples(data)

    async def get_recent_grid_voltage(self, address_id: str) -> list[dict[str, Any]]:
        data = await self.call("UsageService", "GetRecentGridVoltage", {"addressId": address_id})
        return _samples(data)


def _samples(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The sample list out of a usage response, dicts only.

    `list(data.get("samples") or [])` is not equivalent: it raises TypeError on
    a non-iterable, and on a string returns the right type holding the wrong
    thing, since list("abc") is three strings past a signature promising dicts.
    mypy sees neither, because the value is Any and Any is iterable.

    The annotation follows the `.proto` field list. These two methods return
    no samples for the site this was built against.
    """
    samples = data.get("samples")
    if not isinstance(samples, list):
        return []
    return [s for s in samples if isinstance(s, dict)]


def _error_for(status: int, body: Any) -> BasePowerError:
    """Map a Connect error body to an exception.

    Connect reports failures as JSON with `code` and `message`, alongside
    ordinary HTTP statuses. 401/403 and Connect's `unauthenticated` /
    `permission_denied` mean the short-lived token expired rather than a
    fault, so they get their own type.
    """
    code = ""
    message = ""
    if isinstance(body, dict):
        code = str(body.get("code") or "")
        message = str(body.get("message") or "")
    detail = f"HTTP {status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if status in (401, 403) or code in ("unauthenticated", "permission_denied"):
        return BasePowerAuthError(detail)
    return BasePowerError(detail)
