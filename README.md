# ha-base-power

A Home Assistant integration for Base Power home batteries, and the
reverse-engineering record it is built on.

Base ships no public API and no local interface. The contract here was
recovered from the Android app (`com.basepowercompany.basemobileapp` 1.14.0),
which is React Native on Hermes and talks Connect RPC to
`https://dashboard.baseapis.net` with a Clerk session token.

- [docs/API.md](docs/API.md): the recovered contract. Transport, auth, every
  service and method, the message fields, and what each maps to.
- [proto/](proto/): the `dashboard.mobile.v2` `.proto` files, decoded from the
  descriptors the app embeds. Authoritative field list.
- [tools/decode_descriptors.py](tools/decode_descriptors.py): recovers those
  files from a Hermes bundle's string table.

## Status

Running on Home Assistant 2026.9.2 since 2026-09-13. Sign-in, `ListLocations`,
`GetLocation` and `GetSnapshot` all confirmed against the live service.

The numbers are cross-checked three ways rather than assumed. The power flow
balances to 0.000 kW (`from_grid` + `from_storage` = `to_home`, three
separately parsed fields). The stored-energy derivation agrees with the
independent backup-at-current-usage figure to 0.4%. And `to_home` lands within
1.9% of a different vendor's whole-panel CTs on the same house, which is the
strongest evidence available that these are measurements rather than
plausible-looking garbage: nothing in this code path touches that figure.

Unobserved as of 2026-09-13, and what would close each:

| Unobserved | What closes it |
|---|---|
| The off-grid states, and State of charge with a value | A real grid outage. `on_grid` is confirmed. |
| Power from solar | A site that declares solar. The test site has none, so Base omits the field. |
| `UsageService` samples | Opening the usage screen in the Base app: if it shows history, the fault is in this client. Both methods answer and return empty for this site. |

Statements in `docs/API.md` marked unconfirmed remain readings of the app
binary.

## Setting it up

Base Power has no password. Clerk emails a six-digit code, and the config flow
does that for you:

1. Add the integration and choose "Sign in with an emailed code".
2. Enter the email address on your Base account.
3. Enter the code they send.

Home Assistant keeps the durable credential Clerk returns and mints
short-lived tokens from it, the same way the mobile app does.

A second menu option, "paste a credential", covers what the code route cannot:
an account that signs in only with Google or Apple, an account with two-factor
authentication, or a change at Clerk that breaks the code flow. It takes the
`__client` cookie from a browser session, described in `docs/API.md`. The
emailed-code path completed against the live service on 2026-09-13; the paste
path is untested as of that date, and the first Google or Apple account to use
it is the test.

## Supported devices

One Base Power battery installation, as one device per site on the account.
Everything is scoped by the site's address id, which the integration discovers
at setup. An account with two sites gets two config entries.

It talks to Base's cloud. There is no local API and no local network path to
the unit.

## Entities

All from `BatteryService/GetSnapshot`, polled together.

| Entity | Notes |
|---|---|
| Power to home | kW |
| Power from grid | kW |
| Power from storage | kW, signed. Negative means charging, so one sensor covers both directions. |
| Power from solar | kW. Only created if the site declares solar, because Base omits the field entirely otherwise and the sensor would sit at `unknown` for ever. |
| Estimated backup time | hours, at the current household draw |
| Stored energy | kWh, derived. See below. |
| State of charge | %. Reads `unknown` while on grid, which is correct: Base publishes it only in the off-grid states. |
| Battery state | `on_grid`, `off_grid_outage`, `off_grid_no_home_power`, `off_grid_overcurrent`, `off_grid_overcurrent_standby`, `telemetry_unavailable` |
| Grid outage (binary) | `problem`, on when the grid is down and the battery is carrying the house |
| Running off grid (binary) | `problem`, on for any off-grid state, not only an outage |
| Battery Wi-Fi network | diagnostic, disabled by default |

Stored energy is derived because Base publishes no state of charge while on
grid, so there is no direct "how full is it" reading in normal operation. What
it does publish is estimated backup hours at a 750 W reference load, and hours
times 0.75 kW is the energy those hours imply. It is arithmetic on a published
figure, not a measurement, and it inherits Base's assumptions about the
reference load.

Grid voltage and recent-power history are not provided. Those methods exist
and are authorised, but return no samples for the site this was built against.
A sensor fed by them would read `unknown` indefinitely, which looks like a
broken integration rather than an empty data source.

Buttons for `StartManualBackup` and `ResetOvercurrent` are not provided
either. The API exposes both, and both act on real hardware in someone's
house.

## How data updates

Cloud polling, every 30 seconds by default, configurable with a floor of 15 s.
There is no push channel; the mobile app polls too.

The app refreshes every second, but only while its screen is open and focused.
Home Assistant polls continuously, so copying that rate would mean 86,400
requests a day against Base's production service for data that moves far
slower.

Entities go unavailable when a poll fails, and when Base answers
`telemetry_unavailable`, which is the service saying it has no current
reading. Holding the last value through either would show a stale number as
current. Battery state is the deliberate exception: it stays available to say
why the others went away.

### Expect gaps, and check the Wi-Fi first

The battery has its own reporting cadence, separate from the poll interval,
and Home Assistant cannot speed it up. It reports over Wi-Fi when it can and
falls back to cellular when it cannot. Both measured on the same healthy
battery on the same day:

| Link | Between observations |
|---|---|
| Wi-Fi | 32 seconds |
| Cellular | minutes. A snapshot already 4m51s old when sampled, and no current telemetry about ten minutes after the last report. |

Base drops a snapshot once it goes stale rather than serving an old one, so on
cellular `telemetry_unavailable` comes and goes in ordinary service and
entities drop out for a few minutes at a time. On Wi-Fi that essentially does
not happen.

Repeated gaps are therefore a signal: they usually mean the battery has fallen
back to cellular. That is what the Battery Wi-Fi network diagnostic is for.
Enable it, and if it reads anything but connected, check the access point the
battery associates with rather than the battery. Diagnosed that way once: an
AP whose PoE injector had been unplugged on the ethernet side, so the AP
looked powered while the battery had no path. It fell back to cellular and
Base's own app showed "No battery data" for most of a day.

Polling faster does not help. It only asks more often for data the battery has
not sent. An automation or template sensor can hold the last value if the gaps
bother you; this integration will not, because a stale kW figure is
indistinguishable from a real one.

A repair notice appears after 30 minutes without telemetry. That clears even
the cellular cadence, so on Wi-Fi it should never fire, and it is short enough
to catch a battery that has genuinely stopped.

## Configuration

One option, under Configure: poll interval in seconds. Default 30, minimum 15,
maximum 3600. Changing it reloads the entry.

## Use cases

- Know the power is out before you notice. Grid outage is a `problem` binary
  sensor, so an automation can notify on it directly. An unavailable entity
  cannot be notified on, which is why this exists as its own sensor.
- Watch the battery drain during an outage. Estimated backup time and State of
  charge both populate once off grid.
- See charge and discharge on one graph, because Power from storage is signed.
- Alert on a battery fault. Battery state distinguishes an overcurrent trip
  from an ordinary outage.

### Notify on a grid outage

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

### Template sensor for charge direction

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
| State of charge is `unknown` | Expected while on grid. Use Stored energy instead. |
| No solar sensor | The site does not declare solar, so the sensor is not created rather than reading a false zero. |
| Entities go unavailable for a few minutes, repeatedly | The battery is very likely on cellular rather than Wi-Fi. Enable the Battery Wi-Fi network diagnostic; if it is not connected, check the access point. Polling faster will not help. |
| Everything is unavailable but the integration looks fine | Either the poll is failing or Base answered `telemetry_unavailable`. Battery state stays available and says which. Check the Wi-Fi before contacting Base: cellular fallback is the usual cause. Your backup is unaffected, since the battery still powers the house in an outage while it is not reporting. A repair notice appears after 30 minutes. |
| Asked to sign in again | A Base session ended or was revoked. Reauthentication re-sends a code to the stored address. |
| "Base Power is temporarily refusing sign-in attempts" | Clerk rate-limiting. Wait a few minutes; retrying immediately makes it worse. |
| Sign-in fails and mentions Google, Apple or two-factor | The emailed-code route cannot complete those. Use the paste option. |

Diagnostics are available from the entry menu. They contain no credential, no
Wi-Fi network name, and the address id only as a short digest.

## Removing it

Delete the config entry from Settings > Devices & services. That removes the
device, its entities and the stored credential. Nothing is changed at Base:
the Home Assistant session is one of several on the account and ending it does
not affect the app. To revoke access at Base's end, sign out of all sessions
from your Base account.

## Scope and conduct

This is interoperability work on hardware the owner owns, for their own data.
It reads the app already installed on the owner's phone. It is not a licence
to hammer Base's service: poll conservatively, and treat the two control
methods as what they are, commands to a live battery.

Not affiliated with, endorsed by, or supported by Base Power. Base can change
or withdraw this API at any time without notice.

## Licence

MIT, see [LICENSE](LICENSE). That covers the code here. It does not cover
Base's application, which is not redistributed: this repo holds the derived
contract in `proto/` and `docs/`.
