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

## Intended entities

From `BatteryService/GetSnapshot` plus `UsageService`:

- power flow (grid / storage / solar / home) — `fromStorageKw` is signed, so
  one sensor covers charge and discharge
- estimated backup hours, and stored energy derived from the 750 W figure
- **not** grid voltage or recent-power history yet: `UsageService` answers
  200 with no samples for this site, so a sensor there would sit at `unknown`
  for ever (see `docs/API.md`)
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
