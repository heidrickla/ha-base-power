# Changelog

Everything a user of this integration would notice, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the version numbers are the ones in `manifest.json`.

## [0.3.0] - 2026-10-30

### Changed

- The setup opens on the email form. The menu that asked how to sign in is gone.
- Pasting a credential is offered only when Base cannot sign the account in with an emailed code: an account that signs in with Google or Apple, or one with two-factor authentication. That screen also leads back to the email form. Reauthentication reaches it the same way, so an entry set up by pasting can be renewed by pasting.
- After an error the email form keeps the address that was typed, and on reauthentication it offers the stored one.
- The README opens the repository in HACS and starts the setup with My Home Assistant buttons, and lists the setup screens.

### Removed

- The "Sign-in could not be completed" message, replaced by the screen above.

## [0.2.2] - 2026-10-22

### Changed

- The pasted credential field is a masked password field with autocomplete off.
- Reauthentication renews the entry's own site and aborts when the sign-in is for a Base account that does not include it.

## [0.2.1] - 2026-10-20

### Fixed

- A rate limit, a server error, a timeout, a connection error or a reply that is not JSON from Base's sign-in service fails that poll, and the next poll retries. Only a session Base has ended or revoked asks for a new sign-in. Setup reports "Could not reach the Base Power API." during such an outage.

### Added

- Diagnostics show `sign_in_session`: the stored session's `created_at`, `last_active_at`, `expire_at` and `abandon_at`.

## [0.2.0] - 2026-10-19

### Added

- Hourly energy and cost in long-term statistics for the Energy dashboard, from `UsageService/GetDailyOverview`, read at setup and every 30 minutes: `energy_to_home` and `grid_cost`, plus `solar_to_home` and `solar_export` on sites that declare solar. Each read rewrites the last 24 completed hours from the stored running sum.
- Last energy bill and Last battery bill sensors, read at setup and every 6 hours.
- Base Core support: no pack size is assumed, and stored energy and backup time come from Base's own estimate.
- Diagnostics show the import time, rows per series and the billing accounts found, without amounts.

## [0.1.0] - 2026-09-13

### Added

- First release. Sign-in with an emailed one-time code in the config flow.
- Power to home, from grid and from storage (signed), stored energy, state of charge, estimated backup time, battery state, grid outage and running off grid, the battery's Wi-Fi network (disabled by default), and power from solar on sites that declare it.
- A repair issue after 30 minutes without telemetry.

