# The Base Power mobile API, as recovered from the app

Everything here was read out of the shipped Android app, not from
documentation. **The transport, the auth flow, `LocationsService/ListLocations`
and `BatteryService/GetSnapshot` have since been confirmed against the live
service** (2026-09-13, read-only, `tools/live_probe.py`). Anything not marked
confirmed is still a reading of the binary rather than a measurement, and says
so.

## Confirmed live, 2026-09-13

- Clerk minting works: browser `__client` → `GET /v1/client` → active session
  → `POST /v1/client/sessions/<sid>/tokens` → a fresh session JWT.
- `ListLocations` → 200, one location: `addressId`, `address` (line1, city,
  state, postalCode, country, timezone), `status`.
- `GetSnapshot` → 200, `onGrid` variant, with `powerFlow`
  (`fromGridKw` 2.9, `fromStorageKw` **-0.3**, `nonSolarToHomeKw` 2.6,
  `toHomeKw` 2.6), `estimatedBackupHoursAtCurrentUsage`,
  `estimatedBackupHoursAt750Watts`, and a `wifi` block.

Three findings that change the integration design:

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
Connect endpoint refusing a request with no usable content type. That is the
only live confirmation obtained so far, and it confirms the host and protocol
only, nothing about the methods.

**No custom JWT template is used.** Clerk's `getToken()` accepts
`{ template, leewayInSeconds, skipCache }`, and the only occurrences of
`template` in the bundle are that generic options object and Expo's icon
`renderingMode: 'template'` — no template name is ever passed. So the API
accepts a **plain Clerk session token** (`__session`), which is the simplest
case for a headless client.

**Not yet established:** which Clerk sign-in flow a headless integration
should use to obtain that session, and how to refresh it before expiry (Clerk
session JWTs are short-lived, typically ~60 s, and are refreshed against the
frontend API). That is the first open question and it needs a real sign-in to
answer.

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
- **energy dashboard**: `GetDailyEnergy` gives kWh to home, solar to home and
  solar export, with cost — the right shape for statistics
- **button**: `StartManualBackup`, `ResetOvercurrent` (both mutations, both
  affect real hardware — worth a confirm-style helper rather than a bare
  button)

## Open questions, in the order they block work

1. **Clerk token acquisition and refresh** for a headless client. Everything
   else is ready; nothing can be called without this.
2. **Poll interval.** The app sets `refetchInterval` on the snapshot query;
   the value was not read out. Pick conservatively until measured — this is
   somebody's production service.
3. **Whether `on_grid` really omits state of charge.** The descriptor says so;
   that would be odd and is worth confirming against a live response before
   designing the SoC sensor around it.
4. **Nothing here has been run against the live API.** Every statement above
   is from the app binary. The first live call is also the first test of all
   of it.

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
