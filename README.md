# ha-base-power

A Home Assistant integration for **Base Power** home batteries, and the
reverse-engineering record it is built on.

Base ships no public API and no local interface, so the contract here was
recovered from the Android app (`com.basepowercompany.basemobileapp` 1.14.0).
The app is React Native on Hermes and talks **Connect RPC** to
`https://dashboard.baseapis.net`, authenticated with a **Clerk** session token.

- **[docs/API.md](docs/API.md)** — the full recovered contract: transport, auth,
  every service and method, the message fields, and what each maps to in Home
  Assistant.
- **[proto/](proto/)** — the `dashboard.mobile.v2` `.proto` files, decoded from
  the descriptors the app embeds. Authoritative field list.
- **[tools/decode_descriptors.py](tools/decode_descriptors.py)** — recovers
  those `.proto` files from a Hermes bundle's string table.

## Status

**The contract is confirmed live** (2026-09-13, read-only): auth, `ListLocations`
and `GetSnapshot` all answered. Statements in
`docs/API.md` still marked unconfirmed remain readings of the app binary. The
integration itself does not exist yet; this repo holds the contract it will
be written against.

## Setting it up

Base Power has no password — Clerk emails a six-digit code — so the config
flow does that for you:

1. Add the integration and choose **Sign in with an emailed code**.
2. Enter the email address on your Base account.
3. Enter the code they send.

That is all. Home Assistant keeps the durable credential Clerk returns and
mints short-lived tokens from it as needed, the same way the mobile app does.

There is a second menu option, **paste a credential**, for the cases the code
route cannot cover: an account that signs in only with Google or Apple, an
account with two-factor authentication, or a change at Clerk that breaks the
code flow. It takes the `__client` cookie from a browser session — see
`docs/API.md`.

**Neither path has been run against the live service yet.** Requesting a code
emails a real person, so the sign-in is built from the app's own protocol and
covered by tests with faked responses; the first real setup is the first test
of it.

## Supported devices

One Base Power battery installation, as one Home Assistant device per site on
the account. Everything is scoped by the site's address id, which the
integration discovers for itself at setup — there is nothing to enter. An
account with two sites gets two config entries.

It talks to Base's cloud, not to the battery directly: there is no local API
and no local network path to the unit.

## Entities

All from `BatteryService/GetSnapshot`, polled together.

| Entity | Notes |
|---|---|
| **Power to home** | kW |
| **Power from grid** | kW |
| **Power from storage** | kW, and **signed** — negative means the battery is charging, so one sensor covers both directions |
| **Power from solar** | kW. **Only created if the site declares solar.** Base omits the field entirely on a site without it, so the sensor would otherwise sit at `unknown` for ever |
| **Estimated backup time** | hours, at the current household draw |
| **Stored energy** | kWh, *derived* as backup-hours-at-750 W × 0.75 kW. Not a reading — see below |
| **State of charge** | % — **`unknown` while on grid**, and that is correct, not a fault. Base only publishes it in the off-grid states |
| **Battery state** | `on_grid`, `off_grid_outage`, `off_grid_no_home_power`, `off_grid_overcurrent`, `off_grid_overcurrent_standby`, `telemetry_unavailable` |
| **Grid outage** (binary) | `problem` — on when the grid is down and the battery is carrying the house |
| **Running off grid** (binary) | `problem` — on for *any* off-grid state, not only an outage |
| **Battery Wi-Fi network** | diagnostic, disabled by default |

**Why stored energy is derived.** Base does not publish state of charge while
on grid, so there is no direct "how full is it" reading in normal operation.
What it does publish is estimated backup hours at a 750 W reference load, and
hours × 0.75 kW is the energy those hours imply. It is arithmetic on a
published figure, not a measurement, and it inherits whatever assumptions Base
makes about the reference load.

**Not provided:** grid voltage and recent-power history. Those methods exist
and are authorised, but return no samples for the site this was built against
— see the open question in `docs/API.md`. A sensor fed by them would read
`unknown` indefinitely, which looks like a broken integration rather than an
empty data source.

**Not provided:** buttons for `StartManualBackup` and `ResetOvercurrent`. The
API exposes both, and both act on real hardware in someone's house. They are
deliberately not wired to anything.

## How data updates

Cloud polling, every 30 seconds by default. There is no push channel — the
mobile app polls too.

The app refreshes every second, but only while its screen is open and focused;
Home Assistant polls continuously, so copying that rate would mean 86,400
requests a day against Base's production service for data that moves far more
slowly. 30 s keeps the outage sensor responsive, which is the point of the
integration. The interval is configurable with a floor of 15 s.

Entities go **unavailable** when a poll fails, and also when Base answers
`telemetry_unavailable` — the service itself saying it has no current reading.
Holding the last value through either would show a stale number as current.
The **Battery state** sensor is the deliberate exception: it stays available to
say *why* the others went away.

## Configuration

One option, under the integration's **Configure** button:

- **Poll interval (seconds)** — default 30, minimum 15, maximum 3600. Changing
  it reloads the entry.

## Use cases

- **Know the power is out before you notice.** The **Grid outage** sensor is a
  `problem` binary sensor, so an automation can notify on it directly — an
  unavailable entity cannot be notified on, which is why this exists as its own
  sensor.
- **Watch the battery drain during an outage.** **Estimated backup time** and
  **State of charge** both populate once off grid.
- **See charge and discharge on one graph.** **Power from storage** is signed,
  so a single history card shows both.
- **Alert on a battery fault.** **Battery state** distinguishes an overcurrent
  trip from an ordinary outage.

### Example: notify on a grid outage

```yaml
automation:
  - alias: "Power is out"
    trigger:
      - trigger: state
        entity_id: binary_sensor.base_power_grid_outage
        to: "on"
    action:
      - action: notify.mobile_app
        data:
          message: >-
            Grid is down. Battery has about
            {{ states('sensor.base_power_estimated_backup_time') }} hours left.
```

### Example: template sensor for charge/discharge direction

```yaml
template:
  - sensor:
      - name: "Battery direction"
        state: >-
          {% set kw = states('sensor.base_power_power_from_storage') | float(0) %}
          {{ 'charging' if kw < 0 else 'discharging' if kw > 0 else 'idle' }}
```

Entity ids follow your site's device name, so adjust them to match.

## Troubleshooting

| Symptom | What it means |
|---|---|
| **State of charge is `unknown`** | Expected while on grid. Base only publishes it off grid. Use **Stored energy** instead. |
| **No solar sensor** | The site does not declare solar. Base omits the field, so the sensor is not created rather than reading a false zero. |
| **Everything is unavailable, but the integration looks fine** | Either the poll is failing, or Base answered `telemetry_unavailable`. Check **Battery state** — it stays available and says which. If it reads `telemetry_unavailable`, the integration is working and **the battery is not reporting to Base**. Check the **Battery Wi-Fi network** diagnostic sensor (enable it) and the Base app: a unit that has lost its network connection cannot report, and the app will show the same gap. This is not something Home Assistant can fix. |
| **Asked to sign in again** | A Base session ended or was revoked. Reauthentication re-sends a code to the stored address. |
| **"Base Power is temporarily refusing sign-in attempts"** | Clerk rate-limiting. Wait a few minutes; retrying immediately makes it worse. |
| **Sign-in fails and mentions Google, Apple or two-factor** | The emailed-code route cannot complete those. Use the paste option in the setup menu. |

Diagnostics are available from the entry menu. They contain no credential, no
Wi-Fi network name, and the address id only as a short digest.

## Removing it

Delete the config entry from **Settings → Devices & services**. That removes
the device and its entities and forgets the stored credential. Nothing is left
behind in Home Assistant, and nothing is changed at Base — the Home Assistant
session is one of several on the account and ending it does not affect the app.

To revoke access at Base's end as well, sign out of all sessions from your Base
account.

## Scope and conduct

This is interoperability work on hardware the owner owns, for their own data.
It reads the app that is already installed on the owner's phone. It is not a
licence to hammer Base's service: poll conservatively, and treat the two
control methods as what they are — commands to a live battery.
