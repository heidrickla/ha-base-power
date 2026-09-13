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

Sign-in is passwordless (emailed code or Google/Apple) at
https://account.basepowercompany.com/sign-in — see `docs/API.md`.

## Intended entities

From `BatteryService/GetSnapshot` plus `UsageService`:

- power flow (grid / storage / solar / home) — `fromStorageKw` is signed, so
  one sensor covers charge and discharge
- estimated backup hours, and stored energy derived from the 750 W figure
- grid voltage from `UsageService`
- **not** state of charge in the normal case: the live `onGrid` response
  carries no `stateOfEnergyPercent`, only the off-grid variants do
- a grid-outage binary sensor, which is the reason most people would install
  this
- daily energy to home, solar to home and solar export, for the energy
  dashboard
- buttons for `StartManualBackup` and `ResetOvercurrent`, both of which act on
  real hardware

## Scope and conduct

This is interoperability work on hardware the owner owns, for their own data.
It reads the app that is already installed on the owner's phone. It is not a
licence to hammer Base's service: poll conservatively, and treat the two
control methods as what they are — commands to a live battery.
