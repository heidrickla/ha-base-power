# The Base Power mobile API, as recovered from the app

Everything here was read out of the shipped Android app, not from
documentation. **The transport, the auth flow, `LocationsService/ListLocations`
and `BatteryService/GetSnapshot` have since been confirmed against the live
service** (2026-09-13, read-only, `tools/live_probe.py`). Anything not marked
confirmed is still a reading of the binary rather than a measurement, and says
so.

## `telemetry_unavailable` observed, 2026-09-13

The first real setup found the site answering with **no current telemetry**:
`GetSnapshot` returns the `telemetry_unavailable` variant, every `power_flow`
field null, and `wifi.status = BATTERY_WIFI_CONNECTION_STATUS_UNAVAILABLE`.
The poll itself is healthy — `last_update_success` true, no exception — so
whatever this is, it is not a client fault.

**SOLVED the same day, and it was never the battery.** The access point the
unit associates with had its PoE injector unplugged on the *ethernet* side —
so the AP looked powered, carried no traffic, and the battery fell back to its
cellular link. Cellular reports on a minutes-scale cadence, the API drops a
snapshot once it goes stale rather than serving it, and `telemetry_available`
oscillated for the rest of the day. Restoring the AP restored 32-second
reporting. See the `wifi_status` section below for what that sequence cost.

The careful phrasing this section carried while it was open was still the
right call, and it is worth keeping the reason: what was *observed* was only
that the snapshot was stale beyond whatever threshold the API applies.
*Stale* and *broken* were not distinguishable from one poll, Base's own "No
battery data" banner turned out to be surfacing exactly that staleness, and an
earlier draft that called it a blackout was claiming more than the evidence
carried. The conservative wording survived contact with the answer; the
confident one would not have.

Two things it matters for:

- It is the **first live confirmation of the telemetry-unavailable variant**,
  which until then was only a name in the descriptor.
- The availability design got its first real test and behaved: every value
  entity went `unavailable` rather than showing a confident zero, and
  `battery_state` stayed available reading `telemetry_unavailable` to say
  why. A reading of `0 kW` there would have been indistinguishable from a
  house drawing nothing.

**Base's own app shows the same thing**, in a red banner: "No battery data —
Your battery is not currently sending data. Don't worry—in the event of a grid
outage, it will still provide power to your home." So two independent sources
agree, and one of them is the vendor. That also settles something worth
stating in the integration: **telemetry loss is not backup loss.** The battery
still carries the house through an outage while it is not reporting, and the
entities going unavailable must not be read as "no protection".

**Telemetry returned the same day**, and the recovery is what proved the
point below. Measured live at 19:13 UTC: `telemetry_available` true, a current
`observed_at`, and `wifi_status` reading `NOT_CONNECTED` **at the same time**.

**The battery has a cellular link.** From the owner: *"The battery also has a
cellular connection they use to connect to the battery when wifi isn't
working."* That is the mechanism behind every observation here, and it is not
visible to end users anywhere in the app or the API.

### `wifi_status`: what it does and does not tell you — corrected twice

This section has now been wrong in both directions, and the sequence is worth
keeping, because the second error was made while correcting the first.

`BatteryWifiConnectionStatus` distinguishes `UNSPECIFIED`, `UNAVAILABLE`,
`NOT_CONNECTED`, `CONNECTING` and `CONNECTED`. The strings are separable by
length (the prefix is 31 characters, so `NOT_CONNECTED` is 44, `UNAVAILABLE`
42, `CONNECTED` 40). **Three of the five were observed on one day:**

| when | telemetry | `wifi.status` | observed cadence |
|---|---|---|---|
| 02:41, `onGrid` with real power flows | flowing | 40 = `CONNECTED` | — |
| midday, `telemetryUnavailable` | absent | 42 = `UNAVAILABLE` | — |
| 19:18 | available, snapshot already 4m51s stale | 44 = `NOT_CONNECTED` | cellular: **minutes** |
| evening, after the AP was restored | flowing | 40 = `CONNECTED`, `wifi_ssid_present` true | Wi-Fi: **32 seconds** |

Every row now agrees with the others: telemetry flows freely when Wi-Fi is
`CONNECTED`, and the one degraded sample is the one where it is not.

**Draft one said: check the Wi-Fi.**

**Draft two said `wifi_status` is not a telemetry diagnostic at all and must
not be presented as one.** It justified that with "the battery reported
perfectly well while its Wi-Fi was not connected, on two separate occasions",
and cited the 02:41 capture as one of them.

**The 02:41 capture says `CONNECTED`.** It is a pinned test fixture, captured
straight off the live API in 3bf6b09 and never edited since, and it reads
`BATTERY_WIFI_CONNECTION_STATUS_CONNECTED`. Draft two recorded it as
`NOT_CONNECTED` in this very table — a value inferred from string length
rather than read off the capture sitting in the repo. So one of its two
occasions never happened, and the surviving one (19:18, a snapshot already
4m51s stale) is the cellular-fallback sample, which supports the opposite
conclusion.

That is the failure worth naming: **the contradicting evidence was already in
the repository, pinned, and was not read.** Draft two reasoned from a
reconstruction of the data when the data itself was one file away. The
inference was also too wide — "the battery can report without Wi-Fi" does not
establish "Wi-Fi is irrelevant to whether it reports" — but the wideness of
the inference is the smaller problem. It was built on a fact that was not a
fact.

**What actually happened**: the access point the battery associates with had
its PoE injector unplugged on the *ethernet* side, so the AP looked powered
while carrying no traffic. The battery fell back to cellular, cellular reports
on a minutes-scale cadence, Base drops a stale snapshot rather than serving
it, and `telemetry_available` oscillated all day — with Base's own app showing
"No battery data" throughout. Restoring the AP restored 32-second reporting.

So the correct reading, which neither earlier draft had:

- **Wi-Fi is the normal transport; cellular is the fallback.** 32 s versus
  minutes is not a detail, it is the difference between an integration that
  updates and one that gaps.
- **`wifi_status` does not tell you whether data is flowing right now** — that
  is what draft two got right, and `telemetry_available` remains the field for
  that.
- **It does tell you which cadence to expect, which is the actionable part.**
  A battery that keeps going unavailable is very likely on cellular, and the
  thing to check is the AP, not the battery and not Base Support.

The honest generalisation: a single observation refuted the narrow claim
("Wi-Fi must be up for telemetry to flow") and was then used to assert a broad
one ("Wi-Fi does not matter") that it never supported. Retracting correct
advice costs as much as giving wrong advice, and this cost a day of looking at
the wrong component.

## The numbers are real: three cross-checks, 2026-09-13

Once telemetry returned, the readings were checked rather than assumed:

| check | result |
|---|---|
| **Power flow balances** | `from_grid` 6.70 + `from_storage` 0.40 = 7.10 kW against `to_home` 7.10 kW — **0.000 kW error**. Three separately parsed fields summing to zero is not something a mis-mapped key survives. |
| **Stored energy derivation is self-consistent** | derived (hours@750 W × 0.75) = 43.50 kWh; independently, backup-at-current-usage 6.10 h × 7.10 kW = 43.32 kWh. **0.4% apart**, so the 750 W reference assumption holds. |
| **Against a different vendor's hardware** | Base's `to_home` 7.10 kW vs Emporia whole-panel CTs 6.97 kW — **1.9% apart**. Different vendor, different hardware, different code path, same house. |

That third one is the strongest evidence available that these are real
measurements rather than plausible-looking garbage, because nothing in this
integration's code path touches the Emporia figure.

**A caveat on every cadence number in this document, including the new ones.**
From 2026-09-13 evening the owner was working on the battery's Wi-Fi, and
Base's own UI warns the unit may disconnect during that process. Any gap
measured inside that window is a radio being reconfigured, not service
behaviour. What stands either side of it:

- **Cellular, 19:18 and 19:23** — available with a 4m51s-stale snapshot, then
  unavailable. Predates the work.
- **Wi-Fi, 32 s between successive observations** — measured after the access
  point was restored, so it is steady state rather than mid-reconfiguration.

Both are **single-session observations on one battery**, which is enough to
establish the order-of-magnitude contrast that the repair threshold is
calibrated against and is not enough to quote as a specification. Neither has
been replicated on a second site, and no other site has been seen at all.

**A negative `from_storage` HAS been observed — on the API, not yet through an
entity.** The 02:41 capture below carries `fromStorageKw: -0.3`, the battery
charging from grid, and that exact response is pinned as a test fixture. What
has *not* happened is a negative value reaching a Home Assistant sensor: since
the entry was created the field has read positive throughout.

So the signed-sensor decision rests on real data — the wire genuinely carries
both signs — while the end-to-end path for a negative value is still
unexercised. Those are different claims and it is worth not collapsing them,
in either direction.

## Confirmed live, 2026-09-13

- Clerk minting works: browser `__client` → `GET /v1/client` → active session
  → `POST /v1/client/sessions/<sid>/tokens` → a fresh session JWT.
- `ListLocations` → 200, one location: `addressId`, `address` (line1, city,
  state, postalCode, country, timezone), `status`.
- `GetSnapshot` → 200, `onGrid` variant, with `powerFlow`
  (`fromGridKw` 2.9, `fromStorageKw` **-0.3**, `nonSolarToHomeKw` 2.6,
  `toHomeKw` 2.6), `estimatedBackupHoursAtCurrentUsage`,
  `estimatedBackupHoursAt750Watts`, and a `wifi` block.

**`UsageService` returns no samples for this site.** `GetRecentPower` and
`GetRecentGridVoltage` both answer `200` with an empty body. proto3 JSON omits
empty repeated fields, so `{}` means `samples` is empty rather than the call
having failed - the methods exist and are authorised, they just have nothing
to give. Why is not yet known: it may need a metering capability this site
does not have, a time window the request does not carry, or simply history
that has not accumulated. **Until that is understood, no sensor should be
built on them**, because it would sit at `unknown` for ever and look broken.
`GetDailyEnergy` takes a service period and has not been called at all.

Four findings that change the integration design:

1. **`onGrid` carries no `stateOfEnergyPercent`.** The descriptor implied it
   and the live response confirms it: state of charge is simply not published
   while the battery is on grid, only in the off-grid variants. A SoC sensor
   cannot be fed from `GetSnapshot` in the normal case.
   `estimatedBackupHoursAt750Watts` is the usable proxy for stored energy
   (59.33 h × 0.75 kW ≈ 44.5 kWh available at the time of the call).
2. **`fromStorageKw` is signed** — the live value was `-0.3`, the battery
   charging from grid. Direction is in the sign, so it must not be clamped.
3. **Absent fields are absent, not zero.** The site has no solar and
   `fromSolarKw` was omitted entirely rather than sent as `0.0`.

Three things Clerk's frontend API requires, each learned by being refused:
the path is `GET /v1/client` (not `/v1/client/sync`); `Origin` and the
API-version query params must be present; and `Origin` and `Authorization`
must never both be sent. A **browser `User-Agent` is also required** — the
identical request is refused `403` as `Python-urllib`.

Recovered from **Base Power `1.14.0` (versionCode 87)**, package
`com.basepowercompany.basemobileapp`, pulled from a Pixel over ADB on
2026-09-13.

## Transport

| | |
|---|---|
| Host | `https://dashboard.baseapis.net` |
| Protocol | **Connect RPC** (`@connectrpc` / `@bufbuild/protobuf`), not REST |
| URL shape | `POST /<package>.<Service>/<Method>` |
| Package | `dashboard.mobile.v2` |
| Auth | **Clerk** session JWT as a bearer token |
| Cert pinning | none declared (no `networkSecurityConfig` in the manifest) |

So a call looks like:

```
POST https://dashboard.baseapis.net/dashboard.mobile.v2.BatteryService/GetSnapshot
Authorization: Bearer <clerk session jwt>
Content-Type: application/json        # Connect also accepts application/proto
{"addressId": "<address id>"}
```

Connect's JSON codec means **protobuf is not required on the wire** — the same
methods accept and return JSON with lowerCamelCase field names. That is the
easy path for a Home Assistant integration; the `.proto` files in `proto/` are
the authoritative field list either way.

### Auth

The app authenticates with **Clerk**, not with a Base-issued credential:

- Clerk publishable key (shipped in the bundle, public by design):
  `pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k`
- which base64-decodes to the frontend API host `clerk.basepowercompany.com`
- account portal: `https://account.basepowercompany.com/`
- the app reads the token via Clerk's `useAuth()` and attaches it per request

**Sign-in is passwordless.** The only strategies in the bundle are
`email_code`, `email_link`, `phone_code`, `google` and `oauth_apple` — there
is **no `password` strategy**, so an integration cannot take an email and
password. The user completes a code or social flow once.

- Hosted portal: `https://account.basepowercompany.com/` → redirects to
  `/sign-in` (verified 2026-09-13, HTTP 200)
- Clerk frontend API: `https://clerk.basepowercompany.com` (HTTP 200)
- The app drives it over the Clerk endpoints it ships:
  `/client/sign_ins`, `/verify/prepare_first_factor`,
  `/verify/attempt_first_factor`, `/client/sessions`, `/client/sessions/.../tokens`

**Two tokens, and the difference decides the design:**

| cookie | lifetime | use |
|---|---|---|
| `__session` | ~60 s | the bearer JWT for API calls; fine for one manual test, useless to store |
| `__client` | long-lived | the durable credential; mints fresh session JWTs via `POST https://clerk.basepowercompany.com/v1/client/sessions/<session_id>/tokens` |

So the integration stores the **client** credential and mints a session token
per poll, which is what `BasePowerClient`'s `token_provider` callable exists
for.

`https://dashboard.baseapis.net/` answers **HTTP 415** to a bare GET — a
Connect endpoint refusing a request with no usable content type.

**No custom JWT template is used.** Clerk's `getToken()` accepts
`{ template, leewayInSeconds, skipCache }`, and the only occurrences of
`template` in the bundle are that generic options object and Expo's icon
`renderingMode: 'template'` — no template name is ever passed. So the API
accepts a **plain Clerk session token** (`__session`), which is the simplest
case for a headless client.

**Established 2026-09-13, and implemented in `clerk.py` and
`clerk_signin.py`:** the integration signs in itself with an emailed code and
keeps the durable client credential, then mints session JWTs from it, caching
each until close to expiry. Refresh needs no further interaction, and the
user never opens developer tools.

### The sign-in, as the app performs it

Every name below is a string the app bundle carries - the SDK methods
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

**Native mode, not browser mode.** All of the above carries
`?_is_native=1` and authenticates with `Authorization`, never `Origin` -
Clerk rejects a request sending both, which is how the two modes were told
apart. The bundle's `_is_native`, `__clerk_db_jwt` and `Clerk-Db-Jwt` are
what identify the app as a native client. This is the better side for a
headless integration: the browser flow additionally needs an `Origin` and a
browser `User-Agent` (a request identical but for the UA is refused `403` as
`Python-urllib`), and native needs neither.

The client credential comes back in the `Authorization` **response** header
on each call, and can rotate mid-flow, so the last one is the one to keep.

**Not exercised live.** Requesting a code emails a real person. The two
inferences most worth checking on the first real run are the exact error
codes Clerk returns for a wrong versus an expired code, and whether the
credential really arrives in that response header every time.

What remains open is only how long a `__client` lasts before Clerk ends the
session and the user has to sign in again — unknown, so the integration
raises a reauth flow when it happens rather than assuming it will not.

## Services and methods

All under `dashboard.mobile.v2`. Every request that names a site takes
`address_id` (JSON: `addressId`).

### BatteryService — the one that matters for HA

| Method | Request | Returns |
|---|---|---|
| `GetSnapshot` | `address_id` | `BatterySnapshot` |
| `ResetOvercurrent` | `address_id` | `BatteryControlAccepted` |
| `StartManualBackup` | `address_id` | `BatteryControlAccepted` |
| `ListWifiNetworks` | `address_id` | networks + `observed_at` |
| `ConnectWifi` | `address_id`, `ssid`, `password` | `BatteryControlAccepted` |

`BatterySnapshot` is a **oneof-style state union** — exactly one of these is
populated, which is itself the battery's operating state:

- `telemetry_unavailable` — no data
- `on_grid` — normal
- `off_grid_outage` — grid is down, battery carrying the house
- `off_grid_no_home_power` — off grid, no power to home
- `off_grid_overcurrent` — tripped on overcurrent
- `off_grid_overcurrent_standby`

The populated variant carries:

- `observed_at` (timestamp)
- `state_of_energy_percent` (int32) — **state of charge**; note it is absent
  from the `on_grid` variant in the descriptor
- `power_flow` — see below
- `estimated_backup_hours_at_current_usage` (double)
- `estimated_backup_hours_at_750_watts` (double)
- `overcurrent_limit_kw` (double, overcurrent variants only)

`BatteryPowerFlow`, all doubles in **kW**:
`from_grid_kw`, `from_storage_kw`, `from_solar_kw`, `non_solar_to_home_kw`,
`to_home_kw`.

### UsageService

| Method | Returns |
|---|---|
| `GetRecentGridVoltage` | `GridVoltageSample[]`: `observed_at`, `voltage_v` |
| `GetRecentPower` | `PowerSample[]`: `interval_start`, `power_to_home_kw`, `power_from_solar_kw` |
| `GetDailyEnergy` | `DailyEnergySample[]`: `energy_to_home_kwh`, `solar_to_home_kwh`, `solar_export_kwh`, `estimated_cost`; takes a service period |
| `GetDailyOverview` | home power, backup duration, grid-support intervals, hourly energy + costs, `energy_source_mix` |

`DailyEnergyCost`: `grid_energy_cents`, `solar_self_consumption_savings_cents`,
`solar_export_credit_cents`.

### LocationsService

`ListLocations`, `GetLocation`. A `Location` carries `summary`, `energy`,
`battery`, `capabilities`, `referrals`, `support`. **This is how you discover
the `address_id`** every other call needs.

### Others

- `UserService`: `GetCurrentUser`, `GetIntercomIdentity`
- `BillingService`: accounts, obligations, payments, billing cycle, usage
  cycles, and payment-method mutations (Stripe-backed)
- `NotificationsService`: `RegisterPushDevice`, list/update preferences
- `CompatibilityService`: `CheckAppCompatibility`

## What this buys a Home Assistant integration

Directly mappable, from `GetSnapshot` polled on an interval:

- **sensor**: state of charge (`state_of_energy_percent`, `%`)
- **sensor**: grid / storage / solar / home power (kW, `device_class: power`)
- **sensor**: estimated backup hours (two variants)
- **binary_sensor**: grid outage — `off_grid_outage` variant populated
  (`device_class: problem`), which is the headline entity
- **sensor**: battery state — which snapshot variant is set
- **sensor**: grid voltage from `GetRecentGridVoltage`
- **energy dashboard**: `GetDailyEnergy` would give kWh to home, solar to home and
  solar export, with cost — the right shape for statistics
- **button**: `StartManualBackup`, `ResetOvercurrent` (both mutations, both
  affect real hardware — worth a confirm-style helper rather than a bare
  button)

## Open questions, in the order they block work

The first four (token acquisition, poll interval, whether `on_grid` really
omits state of charge, and whether any of it worked live) are all answered
above. What is left:

1. **Why `UsageService` returns no samples.** Both read-only methods answer
   200 with nothing. Until this is understood, the grid-voltage and
   recent-power sensors are deliberately not built.

   **Ruled out: that this client calls it differently from the app.** The
   bundle has two `getRecentPower`s. The one that surfaces first is the app's
   **mock** (`useMockContext` / `isMock`), which manufactures samples with
   `Math.sin` over `Array.from({length})` - do not mistake it for the real
   client. The real one (function #40185, and the same shape for
   `getRecentGridVoltage` and `getDailyEnergy`) calls
   `client.getRecentPower({ addressId })` and maps `samples`: **no time
   window, no extra field, nothing the probe did not send.** The request is
   also authorised, answering 200 rather than a permission error. So the
   empty body is the server's answer for this site, not a malformed ask.

   **The decisive test costs nothing: open the usage/energy screen in the
   Base app.** If it shows recent power history, the emptiness is something
   about how this client calls it. If the app is equally empty, the data
   genuinely is not there for this site and no integration can invent it.
2. **How long a `__client` credential lasts** before Clerk ends the session.
   Unknown, so the integration raises a reauth flow rather than assuming.
3. **`GetDailyEnergy` has never been called.** It takes a service period, so
   it needs a sensible window chosen first; it is the route to the energy
   dashboard.
4. **The two control methods have never been exercised** —
   `StartManualBackup` and `ResetOvercurrent` act on real hardware, the probe
   refuses them by allowlist, and no entity exposes them yet. Testing them is
   the owner's call, not a thing to slip into a verification run.
5. **The Home Assistant layer is byte-compiled, not import-verified.** Home
   Assistant needs `fcntl` and will not install on the Windows host this was
   built on, so `coordinator.py`, `sensor.py`, `binary_sensor.py` and
   `config_flow.py` have not been loaded by a real Home Assistant. `api.py`
   and `clerk.py` are pure and covered by the test suite. This is the same
   constraint as the `ha-tuxedo-touch` repo, which runs that layer in CI on
   Linux.

## Where the source artefacts are kept

Deliberately **outside this repo**, at `<artifacts-dir>\artifacts\` on the
workstation, so re-analysis never needs the phone plugged in again:

| file | |
|---|---|
| `base-power-1.14.0-base.apk` | md5 `e92bcb53`, 120 MB - the pulled app |
| `hbc-parse.txt` | hermes-dec's parse of the bundle |
| `strings.literal.txt` | the delimited string literals (what `decode_descriptors.py` reads) |
| `strings.identifier.txt` | the identifier table |

They are not committed: the repo holds the *derived* contract (`proto/`,
`docs/`), not Base's app. `.gitignore` refuses `*.apk`, `bundle.hasm` and
`strings.*.txt` so they cannot be added by accident. The 96 MB disassembly is
not kept - `hbc-disassembler` regenerates it from the APK in a couple of
minutes.

The tooling that produced them: `jadx` 1.5.6, and a venv at
`<artifacts-dir>\venv` with `hermes-dec`, `androguard` and `protobuf` (a short
path, because Windows MAX_PATH rejects a deep scratchpad install).

## How to reproduce this

```bash
adb shell pm path com.basepowercompany.basemobileapp
adb pull <base.apk>
unzip base.apk -d base_extract
hbc-file-parser base_extract/assets/index.android.bundle > hbc-parse.txt   # hermes-dec
# the string table, properly delimited (do NOT use a naive strings(1) run -
# Hermes packs strings back to back and a run extractor merges them)
sed -nE "s/^=> <StringKind\.String: 0>: '(.*)'$/\1/p" hbc-parse.txt > strings.literal.txt
python tools/decode_descriptors.py strings.literal.txt proto/
```

The app is React Native + Expo SDK 55 on **Hermes**, so the JS is compiled
bytecode and the DEX holds only RN/Expo glue — jadx on the APK does not reach
the API client. The contract survives because `@bufbuild/protobuf` embeds each
`.proto` as a base64 `FileDescriptorProto`, which is what `decode_descriptors.py`
recovers.

Other third parties in the app, for completeness: Sentry (org
`base-power-company`), PostHog, Intercom (`cwv51e9k`), Stripe, Firebase
messaging for push.
