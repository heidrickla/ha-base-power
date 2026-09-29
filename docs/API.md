# The Base Power mobile API, as recovered from the app

Read out of the shipped Android app, not from documentation. Recovered from Base Power 1.14.0 (versionCode 87), package `com.basepowercompany.basemobileapp`, pulled from a phone over ADB.

Measured against the live service, read-only, with `tools/live_probe.py`: the transport, the auth flow, `LocationsService/ListLocations`, `LocationsService/GetLocation` and `BatteryService/GetSnapshot`, and the emailed-code sign-in end to end. [What the live calls returned](#what-the-live-calls-returned) and [Findings](#findings-that-shape-the-integration) are measurements; the rest is read from the binary.

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

Connect's JSON codec means no protobuf runtime is needed: the same methods accept and return JSON with lowerCamelCase field names. The `.proto` files in `proto/` are the authoritative field list either way.

A bare GET to `https://dashboard.baseapis.net/` answers HTTP 415, a Connect endpoint refusing a request with no usable content type.

## Auth

The app authenticates with Clerk, not with a Base-issued credential.

- Publishable key, shipped in the bundle and public by design: `pk_live_Y2xlcmsuYmFzZXBvd2VyY29tcGFueS5jb20k`, which base64-decodes to the frontend API host `clerk.basepowercompany.com`
- Account portal `https://account.basepowercompany.com/`, which redirects to `/sign-in`
- The app reads the token via Clerk's `useAuth()` and attaches it per request

Sign-in is passwordless. The only strategies in the bundle are `email_code`, `email_link`, `phone_code`, `google` and `oauth_apple`. There is no `password` strategy, so an integration cannot take an email and password.

No custom JWT template is used. Clerk's `getToken()` accepts `{ template, leewayInSeconds, skipCache }`, and the only occurrences of `template` in the bundle are that generic options object and Expo's icon `renderingMode: 'template'`. The API accepts a plain Clerk session token.

### Two tokens

| cookie | lifetime | use |
|---|---|---|
| `__session` | ~60 s | the bearer JWT for API calls. Fine for one manual test, useless to store. |
| `__client` | long-lived | the durable credential. Mints fresh session JWTs via `POST https://clerk.basepowercompany.com/v1/client/sessions/<session_id>/tokens`. |

The integration stores the client credential and mints a session token per poll, caching each until close to expiry. That is what `BasePowerClient`'s `token_provider` callable exists for. When Clerk ends the session the integration starts reauthentication; it assumes no credential lifetime.

### The sign-in sequence

Every name below is a string the app bundle carries: the SDK methods (`signIn.create`, `prepareFirstFactor`, `attemptFirstFactor`), the fields (`identifier`, `strategy`, `emailAddressId`, `code`, `supportedFirstFactors`, `createdSessionId`) and the statuses (`needs_first_factor`, `needs_second_factor`, `complete`).

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

The client credential comes back in the `Authorization` response header on each call and can rotate mid-flow, so keep the last one.

### Native mode, not browser mode

All of the above carries `?_is_native=1` and authenticates with `Authorization`, never `Origin`. Clerk rejects a request sending both, which is how the two modes were told apart. The bundle's `_is_native`, `__clerk_db_jwt` and `Clerk-Db-Jwt` identify the app as a native client.

Native is the better side for a headless integration. The browser flow additionally needs an `Origin` and a browser `User-Agent`: a request identical but for the UA is refused 403 as `Python-urllib`.

Clerk's frontend API also requires: the path `GET /v1/client`, not `/v1/client/sync`; the API-version query parameters; and never both `Origin` and `Authorization` on one request.

## Services and methods

All under `dashboard.mobile.v2`. Every request naming a site takes `address_id` (JSON `addressId`).

### BatteryService

| Method | Request | Returns |
|---|---|---|
| `GetSnapshot` | `address_id` | `BatterySnapshot` |
| `ResetOvercurrent` | `address_id` | `BatteryControlAccepted` |
| `StartManualBackup` | `address_id` | `BatteryControlAccepted` |
| `ListWifiNetworks` | `address_id` | networks + `observed_at` |
| `ConnectWifi` | `address_id`, `ssid`, `password` | `BatteryControlAccepted` |

`ResetOvercurrent` and `StartManualBackup` act on the hardware. No entity exposes them, and `tools/live_probe.py` refuses every method outside its read-only allowlist.

`BatterySnapshot` is a oneof-style state union. Exactly one of these is populated, and which one is the battery's operating state:

- `telemetry_unavailable`, no data
- `on_grid`, normal
- `off_grid_outage`, grid down and battery carrying the house
- `off_grid_no_home_power`
- `off_grid_overcurrent`
- `off_grid_overcurrent_standby`

The populated variant carries:

- `observed_at` (timestamp)
- `state_of_energy_percent` (int32), the state of charge. Absent from the `on_grid` variant.
- `power_flow`
- `estimated_backup_hours_at_current_usage` (double)
- `estimated_backup_hours_at_750_watts` (double)
- `overcurrent_limit_kw` (double, overcurrent variants only)

`BatteryPowerFlow`, all doubles in kW: `from_grid_kw`, `from_storage_kw`, `from_solar_kw`, `non_solar_to_home_kw`, `to_home_kw`.

### UsageService

| Method | Returns |
|---|---|
| `GetRecentGridVoltage` | `GridVoltageSample[]`: `observed_at`, `voltage_v` |
| `GetRecentPower` | `PowerSample[]`: `interval_start`, `power_to_home_kw`, `power_from_solar_kw` |
| `GetDailyEnergy` | `DailyEnergySample[]`: `energy_to_home_kwh`, `solar_to_home_kwh`, `solar_export_kwh`, `estimated_cost`. Takes a service period. |
| `GetDailyOverview` | home power, backup duration, grid-support intervals, hourly energy and costs, `energy_source_mix` |

`DailyEnergyCost`: `grid_energy_cents`, `solar_self_consumption_savings_cents`, `solar_export_credit_cents`.

The bundle has two `getRecentPower`s. The first is the app's mock (`useMockContext` / `isMock`), which manufactures samples with `Math.sin` over `Array.from({length})`. The real client (function #40185, the same shape for `getRecentGridVoltage` and `getDailyEnergy`) calls `client.getRecentPower({ addressId })` and maps `samples`, with no time window and no extra field. This integration calls it the same way.

### LocationsService

`ListLocations` and `GetLocation`. A `Location` carries `summary`, `energy`, `battery`, `capabilities`, `referrals`, `support`. This is how to discover the `address_id` every other call needs.

### Others

- `UserService`: `GetCurrentUser`, `GetIntercomIdentity`
- `BillingService`: accounts, obligations, payments, billing cycle, usage cycles, payment-method mutations (Stripe-backed)
- `NotificationsService`: `RegisterPushDevice`, list/update preferences
- `CompatibilityService`: `CheckAppCompatibility`

## What the live calls returned

- Clerk minting works: browser `__client`, `GET /v1/client`, active session, `POST /v1/client/sessions/<sid>/tokens`, a fresh session JWT.
- `ListLocations` 200, one location: `addressId`, `address` (line1, city, state, postalCode, country, timezone), `status`.
- `GetSnapshot` 200, `onGrid` variant, `powerFlow` with `fromGridKw` 2.9, `fromStorageKw` -0.3, `nonSolarToHomeKw` 2.6, `toHomeKw` 2.6, plus `estimatedBackupHoursAtCurrentUsage`, `estimatedBackupHoursAt750Watts` and a `wifi` block.
- `GetRecentPower` and `GetRecentGridVoltage` 200 with an empty body. proto3 JSON omits empty repeated fields, so `{}` means `samples` is empty, not a failed call. No sensor is built on them.

### Cross-checks

| check | result |
|---|---|
| Power flow balances | `from_grid` 6.70 + `from_storage` 0.40 = 7.10 kW against `to_home` 7.10 kW. Zero error across three separately parsed fields, which a mis-mapped key does not survive. |
| Stored energy derivation is self-consistent | derived (hours at 750 W times 0.75) = 43.50 kWh; independently, backup-at-current-usage 6.10 h times 7.10 kW = 43.32 kWh. 0.4% apart, so the 750 W reference assumption holds. |
| Against a different vendor | Base's `to_home` 7.10 kW against Emporia whole-panel CTs 6.97 kW, 1.9% apart. Different vendor, hardware and code path, same house. |

## Findings that shape the integration

### `on_grid` carries no `state_of_energy_percent`

The descriptor implies it and the live response shows it: state of charge is published only in the off-grid variants. A state-of-charge sensor cannot be fed from `GetSnapshot` in the normal case. `estimated_backup_hours_at_750_watts` is the usable proxy for stored energy: 59.33 h at 0.75 kW is about 44.5 kWh.

### `from_storage_kw` is signed

The live value was -0.3, the battery charging from grid. Direction is in the sign, so it must not be clamped. The negative value reads the same on the wire and through the Home Assistant sensor.

### Absent fields are absent, not zero

On a site with no solar, `from_solar_kw` is omitted entirely rather than sent as 0.0. proto3 also omits false booleans, so a missing `hasSolar` in `capabilities` means no solar, which is why `LocationCapabilities` defaults to False rather than None.

### `wifi_status` says which cadence to expect, not whether data is flowing

`telemetry_available` is the field for whether data is current. The Wi-Fi field says which link the battery reports over: Wi-Fi when it can, cellular when it cannot, and the two cadences differ by an order of magnitude. `BatteryWifiConnectionStatus` distinguishes `UNSPECIFIED`, `UNAVAILABLE`, `NOT_CONNECTED`, `CONNECTING` and `CONNECTED`.

| `wifi.status` | observed with |
|---|---|
| `CONNECTED` | `onGrid`, telemetry flowing; Wi-Fi cadence 32 seconds |
| `NOT_CONNECTED` | cellular; a snapshot already 4m51s stale when sampled, then `telemetryUnavailable` |
| `UNAVAILABLE` | `telemetryUnavailable` while the battery's access point was down |

The API drops a stale snapshot rather than serving it, so on cellular `telemetry_available` oscillates, and Base's own app shows "No battery data". The cadence figures come from one battery on one day, which is enough for the order-of-magnitude contrast the repair threshold is calibrated against.

### Telemetry loss is not backup loss

Base's own banner says so: "No battery data. Your battery is not currently sending data. Don't worry, in the event of a grid outage, it will still provide power to your home." Entities going unavailable must not be read as no protection.

## Source artefacts

Kept outside this repo in a gitignored working directory, so re-analysis never needs the phone again:

| file | |
|---|---|
| `base-power-1.14.0-base.apk` | md5 `e92bcb53`, 120 MB, the pulled app |
| `hbc-parse.txt` | hermes-dec's parse of the bundle |
| `strings.literal.txt` | the delimited string literals, which `decode_descriptors.py` reads |
| `strings.identifier.txt` | the identifier table |

The repo holds the derived contract, not Base's app. `.gitignore` refuses `*.apk`, `bundle.hasm` and `strings.*.txt` so they cannot be added by accident. The 96 MB disassembly is not kept, since `hbc-disassembler` regenerates it from the APK in a couple of minutes.

Tooling: jadx 1.5.6, and a venv with `hermes-dec`, `androguard` and `protobuf`. On Windows, put that venv at a short path. MAX_PATH rejects a deep install partway through, which looks like a broken package rather than a path-length problem.

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

The app is React Native and Expo SDK 55 on Hermes, so the JS is compiled bytecode and the DEX holds only RN/Expo glue. jadx on the APK does not reach the API client. The contract survives because `@bufbuild/protobuf` embeds each `.proto` as a base64 `FileDescriptorProto`, which is what `decode_descriptors.py` recovers.

Other third parties in the app: Sentry (org `base-power-company`), PostHog, Intercom (`cwv51e9k`), Stripe, and Firebase messaging for push.
