# ha-base-power

A Home Assistant integration for Base Power home batteries, and the reverse-engineering record it is built on.

Base ships no public API and no local interface. The contract here was recovered from the Android app (`com.basepowercompany.basemobileapp` 1.14.0), which is React Native on Hermes and talks Connect RPC to `https://dashboard.baseapis.net` with a Clerk session token.

- [docs/API.md](docs/API.md): the recovered contract. Transport, auth, every service and method, the message fields, and what each maps to.
- [proto/](proto/): the `dashboard.mobile.v2` `.proto` files, decoded from the descriptors the app embeds. Authoritative field list.
- [tools/decode_descriptors.py](tools/decode_descriptors.py): recovers those files from a Hermes bundle's string table.

## Accuracy

Three cross-checks on one installation:

| Check | Result |
|---|---|
| Power flow balance | `from_grid` + `from_storage` = `to_home` to 0.000 kW, three separately parsed fields |
| Stored energy | The 750 W-reference derivation agrees with the backup-at-current-usage figure to 0.4% |
| Another vendor | `to_home` within 1.9% of a whole-panel CT monitor on the same house, which shares no code path with this integration |

`docs/API.md` separates what the live service returned from what was read out of the app binary.

## Setting it up

Base Power has no password. Clerk emails a six-digit code, and the config flow does that for you:

1. Add the integration and choose "Sign in with an emailed code".
2. Enter the email address on your Base account.
3. Enter the code they send.

Home Assistant keeps the durable credential Clerk returns and mints short-lived tokens from it, the same way the mobile app does.

A second menu option, "paste a credential", covers what the code route cannot: an account that signs in only with Google or Apple, an account with two-factor authentication, or a change at Clerk that breaks the code flow. It takes the `__client` cookie from a browser session, described in `docs/API.md`.

## Supported devices

One Base Power battery installation, as one device per site on the account. Everything is scoped by the site's address id, which the integration discovers at setup. An account with two sites gets two config entries.

It talks to Base's cloud. There is no local API and no local network path to the unit.

Any Base battery: the original units and Base Core, 39.2 kWh or 78.4 kWh as two units. No pack size is assumed anywhere. Stored energy and backup time come from Base's own backup estimate, which already reflects the installed pack.

## Entities

All from `BatteryService/GetSnapshot`, polled together, except the bills.

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
| Last energy bill | currency. One per billing account the site has, from `BillingService/ListAccounts`, read at setup and every 6 hours. Only on sites that declare billing. |
| Last battery bill | as above, for the battery membership account |

Stored energy is derived because Base publishes no state of charge while on grid, so there is no direct "how full is it" reading in normal operation. What it does publish is estimated backup hours at a 750 W reference load, and hours times 0.75 kW is the energy those hours imply. It is arithmetic on a published figure, not a measurement, and it inherits Base's assumptions about the reference load.

Grid voltage and the one-minute power history are not used. The power history repeats Power to home.

Buttons for `StartManualBackup` and `ResetOvercurrent` are not provided either. The API exposes both, and both act on real hardware in someone's house.

## Energy dashboard

Hourly energy and cost go into long-term statistics, with no entity. `UsageService/GetDailyOverview` publishes the last 24 completed hours, and a statistic keyed by the hour is what the Energy dashboard reads. The integration reads the overview at setup and every 30 minutes. Each read rewrites every hour it carries, carrying the running sum on from the last stored hour, so an hour Base revises is corrected and a Home Assistant restart leaves no step in the sum.

| Statistic | Unit | Notes |
|---|---|---|
| `base_power:<address digest>_energy_to_home` | kWh | Everything the house used, from grid, battery and solar together |
| `base_power:<address digest>_grid_cost` | the instance currency | Base's estimate of the grid energy cost |
| `base_power:<address digest>_solar_to_home` | kWh | Sites that declare solar |
| `base_power:<address digest>_solar_export` | kWh | Sites that declare solar |

The address digest is the 12 characters diagnostics show as `address_id_fingerprint`. Cost is written when Base prices in the instance's currency. Add them in Settings > Dashboards > Energy. Developer tools > Statistics lists them. They need the recorder, which every default configuration has.

## How data updates

Cloud polling, every 30 seconds by default, configurable with a floor of 15 s. There is no push channel; the mobile app polls too.

The app refreshes every second, but only while its screen is open and focused. Home Assistant polls continuously, so copying that rate would mean 86,400 requests a day against Base's production service for data that moves far slower.

Entities go unavailable when a poll fails, and when Base answers `telemetry_unavailable`, which is the service saying it has no current reading. Holding the last value through either would show a stale number as current. Battery state is the deliberate exception: it stays available to say why the others went away.

### Reporting gaps and the Wi-Fi link

The battery has its own reporting cadence, separate from the poll interval, and Home Assistant cannot speed it up. It reports over Wi-Fi when it can and falls back to cellular when it cannot. Measured on one battery:

| Link | Between observations |
|---|---|
| Wi-Fi | 32 seconds |
| Cellular | minutes. A snapshot already 4m51s old when sampled, and no current telemetry about ten minutes after the last report. |

Base drops a snapshot once it goes stale rather than serving an old one, so on cellular `telemetry_unavailable` comes and goes in ordinary service and entities drop out for a few minutes at a time. At the Wi-Fi cadence they do not.

Repeated gaps usually mean the battery has fallen back to cellular. Enable the Battery Wi-Fi network diagnostic; if it reads anything but connected, check the access point the battery associates with. An access point whose PoE injector is unplugged on the Ethernet side looks powered while carrying no traffic, and Base's own app then shows "No battery data".

Polling faster does not help: it asks more often for data the battery has not sent. An automation or template sensor can hold the last value; this integration does not, because a stale kW figure is indistinguishable from a real one.

A repair notice appears after 30 minutes without telemetry. That is longer than the cellular cadence and short enough to catch a battery that has stopped reporting.

## Configuration

One option, under Configure: poll interval in seconds. Default 30, minimum 15, maximum 3600. Changing it reloads the entry.

## Use cases

- Know the power is out before you notice. Grid outage is a `problem` binary sensor, so an automation can notify on it directly. An unavailable entity cannot be notified on, which is why this exists as its own sensor.
- Watch the battery drain during an outage. Estimated backup time and State of charge both populate once off grid.
- See charge and discharge on one graph, because Power from storage is signed.
- Alert on a battery fault. Battery state distinguishes an overcurrent trip from an ordinary outage.
- Track the house's energy and its cost in the Energy dashboard, hour by hour.

Check your entity ids before copying the examples below. The prefix is your device name, not the domain, and it differs between accounts.

Base returns a name for some sites and not others. When it returns none, the integration falls back to the literal "Base Power", giving `base_power_`. When it returns one, say Home, you get `home_` instead. Open Settings > Devices & services, click the device, and read the prefix off any entity.

The examples use the `base_power_` fallback. For a named site, substitute:

| Entity | Unnamed site | Site named Home |
|---|---|---|
| Grid outage | `binary_sensor.base_power_grid_outage` | `binary_sensor.home_grid_outage` |
| Running off grid | `binary_sensor.base_power_running_off_grid` | `binary_sensor.home_running_off_grid` |
| Battery state | `sensor.base_power_battery_state` | `sensor.home_battery_state` |
| Estimated backup time | `sensor.base_power_estimated_backup_time` | `sensor.home_estimated_backup_time` |
| Power to home | `sensor.base_power_power_to_home` | `sensor.home_power_to_home` |
| Power from grid | `sensor.base_power_power_from_grid` | `sensor.home_power_from_grid` |
| Power from storage | `sensor.base_power_power_from_storage` | `sensor.home_power_from_storage` |
| State of charge | `sensor.base_power_state_of_charge` | `sensor.home_state_of_charge` |
| Stored energy | `sensor.base_power_stored_energy` | `sensor.home_stored_energy` |
| Battery Wi-Fi network | `sensor.base_power_battery_wi_fi_network` | `sensor.home_battery_wi_fi_network` |

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

## Troubleshooting

| Symptom | What it means |
|---|---|
| State of charge is `unknown` | Expected while on grid. Use Stored energy instead. |
| No solar sensor | The site does not declare solar, so the sensor is not created rather than reading a false zero. |
| No bill sensors | The site declares no billing, or its accounts could not be read at setup, which is logged. Reload the entry to read them again. |
| No hourly statistics | The recorder is not loaded, or the overview could not be read, which is logged once until it recovers. |
| Entities go unavailable for a few minutes, repeatedly | The battery is very likely on cellular rather than Wi-Fi. Enable the Battery Wi-Fi network diagnostic; if it is not connected, check the access point. Polling faster will not help. |
| Everything is unavailable but the integration looks fine | Either the poll is failing or Base answered `telemetry_unavailable`. Battery state stays available and says which. Check the Wi-Fi before contacting Base: cellular fallback is the usual cause. Your backup is unaffected, since the battery still powers the house in an outage while it is not reporting. A repair notice appears after 30 minutes. |
| Asked to sign in again | A Base session ended or was revoked. Reauthentication re-sends a code to the stored address. |
| "Base Power is temporarily refusing sign-in attempts" | Clerk rate-limiting. Wait a few minutes; retrying immediately makes it worse. |
| Sign-in fails and mentions Google, Apple or two-factor | The emailed-code route cannot complete those. Use the paste option. |

Diagnostics are available from the entry menu. They contain no credential, no Wi-Fi network name, and the address id only as a short digest.

## Removing it

Delete the config entry from Settings > Devices & services. That removes the device, its entities and the stored credential. Nothing is changed at Base: the Home Assistant session is one of several on the account and ending it does not affect the app. To revoke access at Base's end, sign out of all sessions from your Base account.

## Scope and conduct

This is interoperability work on hardware the owner owns, for their own data. It reads the app already installed on the owner's phone. It is not a licence to hammer Base's service: poll conservatively, and treat the two control methods as what they are, commands to a live battery.

Not affiliated with, endorsed by, or supported by Base Power. Base can change or withdraw this API at any time without notice.

## Licence

MIT, see [LICENSE](LICENSE). That covers the code here. It does not cover Base's application, which is not redistributed: this repo holds the derived contract in `proto/` and `docs/`.
