# The Base Power mobile API, as recovered from the app

Read out of the shipped Android app, not from documentation. Recovered from
Base Power 1.14.0 (versionCode 87), package
`com.basepowercompany.basemobileapp`, pulled from a Pixel over ADB on
2026-09-13.

Confirmed live on 2026-09-13, read-only, via `tools/live_probe.py`: the
transport, the auth flow, `LocationsService/ListLocations`,
`LocationsService/GetLocation` and `BatteryService/GetSnapshot`. The emailed
code sign-in has since been completed end to end by a real user. Anything not
marked confirmed is a reading of the binary rather than a measurement, and
says so.

## Transport

| | |
|---|---|
| Host | `https://dashboard.baseapis.net` |
| Protocol | Connect RPC (`@connectrpc` / `@bufbuild/protobuf`), not REST |
| URL shape | `POST /<package>.<Service>/<Method>` |
| Package | `dashboard.mobile.v2` |
| Auth | Clerk session JWT as a bearer token |
| Cert pinning | none declared (no `networkSecurityConfig` in the manifest) |

```
POST https://dashboard.baseapis.net/dashboard.mobile.v2.BatteryService/GetSnapshot
Authorization: Bearer <clerk session jwt>
Content-Type: application/json        # Connect also accepts application/proto
{"addressId": "<address id>"}
```

Connect's JSON codec means no protobuf runtime is needed: the same methods
accept and return JSON with lowerCamelCase field names. The `.proto` files in
`proto/` are the authoritative field list either way.

A bare GET to `https://dashboard.baseapis.net/` answers HTTP 415, a Connect
endpoint refusing a request with no usable content type.

## Auth

The app authenticates with Clerk, not with a Base-issued credential.

- Publishable key, shipped in the bundle and public by design:
  `pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k`, which base64-decodes to the
  frontend API host `clerk.basepowercompany.com`
- Account portal `https://account.basepowercompany.com/`, which redirects to
  `/sign-in`
- The app reads the token via Clerk's `useAuth()` and attaches it per request

Sign-in is passwordless. The only strategies in the bundle are `email_code`,
`email_link`, `phone_code`, `google` and `oauth_apple`. There is no `password`
strategy, so an integration cannot take an email and password.

No custom JWT template is used. Clerk's `getToken()` accepts
`{ template, leewayInSeconds, skipCache }`, and the only occurrences of
`template` in the bundle are that generic options object and Expo's icon
`renderingMode: 'template'`. The API accepts a plain Clerk session token.

### Two tokens, and the difference decides the design

| cookie | lifetime | use |
|---|---|---|
| `__session` | ~60 s | the bearer JWT for API calls. Fine for one manual test, useless to store. |
| `__client` | long-lived | the durable credential. Mints fresh session JWTs via `POST https://clerk.basepowercompany.com/v1/client/sessions/<session_id>/tokens`. |

The integration stores the client credential and mints a session token per
poll, caching each until close to expiry. That is what `BasePowerClient`'s
`token_provider` callable exists for.

### The sign-in sequence

Every name below is a string the app bundle carries: the SDK methods
(`signIn.create`, `prepareFirstFactor`, `attemptFirstFactor`), the fields
(`identifier`, `strategy`, `emailAddressId`, `code`, `supportedFirstFactors`,
`createdSessionId`) and the statuses (`needs_first_factor`,
`needs_second_factor`, `complete`).

```
POST /v1/client/sign_ins                              identifier=<email>
  -> status needs_first_factor, supported_first_factors[]
POST /v1/client/sign_ins/<id>/prepare_first_factor    strategy=email_code
                                                      email_address_id=<from above>
  -> Clerk emails a six-digit code
POST /v1/client/sign_ins/<id>/attempt_first_factor    strategy=email_code
                                                      code=<typed>
  -> status complete, created_session_id
```

The client credential comes back in the `Authorization` response header on
each call and can rotate mid-flow, so keep the last one.

### Native mode, not browser mode

All of the above carries `?_is_native=1` and authenticates with
`Authorization`, never `Origin`. Clerk rejects a request sending both, which
is how the two modes were told apart. The bundle's `_is_native`,
`__clerk_db_jwt` and `Clerk-Db-Jwt` identify the app as a native client.

Native is the better side for a headless integration. The browser flow
additionally needs an `Origin` and a browser `User-Agent`: a request identical
but for the UA is refused 403 as `Python-urllib`.

Three further things Clerk's frontend API requires, each learned by being
refused without it: the path is `GET /v1/client`, not `/v1/client/sync`; the
API-version query parameters must be present; and `Origin` and `Authorization`
must never both be sent.

## Services and methods

All under `dashboard.mobile.v2`. Every request naming a site takes
`address_id` (JSON `addressId`).

### BatteryService

| Method | Request | Returns |
|---|---|---|
| `GetSnapshot` | `address_id` | `BatterySnapshot` |
| `ResetOvercurrent` | `address_id` | `BatteryControlAccepted` |
| `StartManualBackup` | `address_id` | `BatteryControlAccepted` |
| `ListWifiNetworks` | `address_id` | networks + `observed_at` |
| `ConnectWifi` | `address_id`, `ssid`, `password` | `BatteryControlAccepted` |

`BatterySnapshot` is a oneof-style state union. Exactly one of these is
populated, and which one is the battery's operating state:

- `telemetry_unavailable`, no data
- `on_grid`, normal
- `off_grid_outage`, grid down and battery carrying the house
- `off_grid_no_home_power`
- `off_grid_overcurrent`
- `off_grid_overcurrent_standby`

The populated variant carries:

- `observed_at` (timestamp)
- `state_of_energy_percent` (int32), the state of charge. Absent from the
  `on_grid` variant.
- `power_flow`
- `estimated_backup_hours_at_current_usage` (double)
- `estimated_backup_hours_at_750_watts` (double)
- `overcurrent_limit_kw` (double, overcurrent variants only)

`BatteryPowerFlow`, all doubles in kW: `from_grid_kw`, `from_storage_kw`,
`from_solar_kw`, `non_solar_to_home_kw`, `to_home_kw`.

### UsageService

| Method | Returns |
|---|---|
| `GetRecentGridVoltage` | `GridVoltageSample[]`: `observed_at`, `voltage_v` |
| `GetRecentPower` | `PowerSample[]`: `interval_start`, `power_to_home_kw`, `power_from_solar_kw` |
| `GetDailyEnergy` | `DailyEnergySample[]`: `energy_to_home_kwh`, `solar_to_home_kwh`, `solar_export_kwh`, `estimated_cost`. Takes a service period. |
| `GetDailyOverview` | home power, backup duration, grid-support intervals, hourly energy and costs, `energy_source_mix` |

`DailyEnergyCost`: `grid_energy_cents`,
`solar_self_consumption_savings_cents`, `solar_export_credit_cents`.

### LocationsService

`ListLocations` and `GetLocation`. A `Location` carries `summary`, `energy`,
`battery`, `capabilities`, `referrals`, `support`. This is how to discover the
`address_id` every other call needs.

### Others

- `UserService`: `GetCurrentUser`, `GetIntercomIdentity`
- `BillingService`: accounts, obligations, payments, billing cycle, usage
  cycles, payment-method mutations (Stripe-backed)
- `NotificationsService`: `RegisterPushDevice`, list/update preferences
- `CompatibilityService`: `CheckAppCompatibility`

## What the live calls returned

- Clerk minting works: browser `__client`, `GET /v1/client`, active session,
  `POST /v1/client/sessions/<sid>/tokens`, a fresh session JWT.
- `ListLocations` 200, one location: `addressId`, `address` (line1, city,
  state, postalCode, country, timezone), `status`.
- `GetSnapshot` 200, `onGrid` variant, `powerFlow` with `fromGridKw` 2.9,
  `fromStorageKw` -0.3, `nonSolarToHomeKw` 2.6, `toHomeKw` 2.6, plus
  `estimatedBackupHoursAtCurrentUsage`, `estimatedBackupHoursAt750Watts` and a
  `wifi` block.

### The numbers are real: three cross-checks

| check | result |
|---|---|
| Power flow balances | `from_grid` 6.70 + `from_storage` 0.40 = 7.10 kW against `to_home` 7.10 kW. Zero error. Three separately parsed fields summing exactly is not something a mis-mapped key survives. |
| Stored energy derivation is self-consistent | derived (hours at 750 W times 0.75) = 43.50 kWh; independently, backup-at-current-usage 6.10 h times 7.10 kW = 43.32 kWh. 0.4% apart, so the 750 W reference assumption holds. |
| Against a different vendor | Base's `to_home` 7.10 kW against Emporia whole-panel CTs 6.97 kW, 1.9% apart. Different vendor, hardware and code path, same house. |

The third is the strongest evidence available that these are measurements
rather than plausible-looking garbage, because nothing in this code path
touches the Emporia figure.

## Findings that shape the integration

### `on_grid` carries no `state_of_energy_percent`

The descriptor implied it
and the live response confirms it: state of charge is published only in the
off-grid variants. A state-of-charge sensor cannot be fed from `GetSnapshot`
in the normal case. `estimated_backup_hours_at_750_watts` is the usable proxy
for stored energy: 59.33 h at 0.75 kW is about 44.5 kWh.

### `from_storage_kw` is signed

The live value was -0.3, the battery charging
from grid. Direction is in the sign, so it must not be clamped. A negative
value has been confirmed both on the wire and through a Home Assistant sensor.

### Absent fields are absent, not zero

The site has no solar and
`from_solar_kw` was omitted entirely rather than sent as 0.0. proto3 also
omits false booleans, so a missing `hasSolar` in `capabilities` means no
solar, which is why `LocationCapabilities` defaults to False rather than None.

### `wifi_status` says which cadence to expect, not whether data is flowing

`telemetry_available` is the field for whether data is current. The Wi-Fi field is still worth reading, because the battery
reports over Wi-Fi when it can and falls back to cellular when it cannot, and
those cadences differ by an order of magnitude. `BatteryWifiConnectionStatus`
distinguishes `UNSPECIFIED`, `UNAVAILABLE`, `NOT_CONNECTED`, `CONNECTING` and
`CONNECTED`.

| when | telemetry | `wifi.status` | cadence |
|---|---|---|---|
| 02:41, `onGrid` with real power flows | flowing | `CONNECTED` | |
| midday, `telemetryUnavailable` | absent | `UNAVAILABLE` | |
| 19:18 | available, snapshot already 4m51s stale | `NOT_CONNECTED` | cellular, minutes |
| evening, after the AP was restored | flowing | `CONNECTED` | Wi-Fi, 32 seconds |

The all-day telemetry gap was an access point whose PoE injector had been
unplugged on the ethernet side, so the AP looked powered while carrying no
traffic. The battery fell back to cellular, the API drops a stale snapshot
rather than serving it, and `telemetry_available` oscillated for the rest of
the day with Base's own app showing "No battery data" throughout.

Two traps in that sequence, both of which cost a day:

- The enum strings are separable by length, and an earlier draft of this
  document recorded the 02:41 capture as `NOT_CONNECTED` on that basis. The
  capture is a pinned test fixture and says `CONNECTED`. Read the data, not a
  reconstruction of it.
- That wrong value was then used to argue Wi-Fi was irrelevant to telemetry,
  which sent attention at the wrong component. One observation refuting a
  narrow claim does not establish a broad one.

### Telemetry loss is not backup loss

Base's own banner says so: "No battery
data. Your battery is not currently sending data. Don't worry, in the event of
a grid outage, it will still provide power to your home." Entities going
unavailable must not be read as no protection.

### A caveat on every cadence number here

From the evening of 2026-09-13 the
owner was working on the battery's Wi-Fi, and Base's UI warns the unit may
disconnect during that. Any gap measured inside that window is a radio being
reconfigured. What stands either side: cellular at 19:18 and 19:23, available
with a 4m51s-stale snapshot then unavailable; Wi-Fi at 32 seconds, measured
after the AP was restored. Both are single-session observations on one
battery, enough for the order-of-magnitude contrast the repair threshold is
calibrated against, not enough to quote as a specification.

## Open questions

### Why `UsageService` returns no samples

`GetRecentPower` and `GetRecentGridVoltage` both answer 200 with an empty
body. proto3 JSON omits empty repeated fields, so `{}` means `samples` is
empty rather than the call having failed. It may need a metering capability
this site lacks, a time window the request does not carry, or history that has
not accumulated. No sensor is built on them until it is understood; one would
sit at `unknown` for ever and look broken.

Ruled out: that this client calls it differently from the app. The bundle has
two `getRecentPower`s. The one that surfaces first is the app's mock
(`useMockContext` / `isMock`), which manufactures samples with `Math.sin` over
`Array.from({length})`. Do not mistake it for the real client. The real one
(function #40185, same shape for `getRecentGridVoltage` and `getDailyEnergy`)
calls `client.getRecentPower({ addressId })` and maps `samples`, with no time
window and no extra field. The request is authorised, answering 200 rather
than a permission error.

The decisive test costs nothing: open the usage screen in the Base app. If it
shows history, the emptiness is about this client. If the app is equally
empty, the data is not there for this site.

### Smaller open questions

Each is dated, with what would close it, so staleness is visible rather than
silent.

- How long a `__client` credential lasts before Clerk ends the session.
  Unknown as of 2026-09-13; the first reauth prompt in normal service answers
  it. The integration raises a reauth flow rather than assuming a lifetime.
- `GetDailyEnergy`, uncalled as of 2026-09-13. It takes a service period, so a
  window has to be chosen first. It is the route to the energy dashboard.
- `StartManualBackup` and `ResetOvercurrent`, unexercised as of 2026-09-13.
  Both act on real hardware, the probe refuses them by allowlist, and no
  entity exposes them. Only the owner running one deliberately closes this.

## Where the source artefacts are kept

Outside this repo, in a gitignored working directory on the analysis machine,
so re-analysis never needs the phone plugged in again:

| file | |
|---|---|
| `base-power-1.14.0-base.apk` | md5 `e92bcb53`, 120 MB, the pulled app |
| `hbc-parse.txt` | hermes-dec's parse of the bundle |
| `strings.literal.txt` | the delimited string literals, which `decode_descriptors.py` reads |
| `strings.identifier.txt` | the identifier table |

They are not committed: the repo holds the derived contract, not Base's app.
`.gitignore` refuses `*.apk`, `bundle.hasm` and `strings.*.txt` so they cannot
be added by accident. The 96 MB disassembly is not kept, since
`hbc-disassembler` regenerates it from the APK in a couple of minutes.

Tooling: jadx 1.5.6, and a venv with `hermes-dec`, `androguard` and
`protobuf`. On Windows, put that venv at a short path. MAX_PATH rejects a deep
install partway through, which looks like a broken package rather than a
path-length problem.

## How to reproduce this

```bash
adb shell pm path com.basepowercompany.basemobileapp
adb pull <base.apk>
unzip base.apk -d base_extract
hbc-file-parser base_extract/assets/index.android.bundle > hbc-parse.txt   # hermes-dec
# the string table, properly delimited. Do NOT use a naive strings(1) run:
# Hermes packs strings back to back and a run extractor merges them into
# hostnames that do not exist.
sed -nE "s/^=> <StringKind\.String: 0>: '(.*)'$/\1/p" hbc-parse.txt > strings.literal.txt
python tools/decode_descriptors.py strings.literal.txt proto/
```

The app is React Native and Expo SDK 55 on Hermes, so the JS is compiled
bytecode and the DEX holds only RN/Expo glue. jadx on the APK does not reach
the API client. The contract survives because `@bufbuild/protobuf` embeds each
`.proto` as a base64 `FileDescriptorProto`, which is what
`decode_descriptors.py` recovers.

Other third parties in the app, for completeness: Sentry (org
`base-power-company`), PostHog, Intercom (`cwv51e9k`), Stripe, and Firebase
messaging for push.
