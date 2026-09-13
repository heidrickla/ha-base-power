"""Coordinator for the Base Power integration.

Holds one battery snapshot per site and refreshes it on an interval. There is
no push channel; the app polls too.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BasePowerAuthError,
    BasePowerClient,
    BasePowerError,
    BatterySnapshot,
    LocationCapabilities,
)
from .clerk import ClerkAuthError, ClerkSessionProvider
from .const import (
    CONF_ADDRESS_ID,
    CONF_CLIENT_JWT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_TELEMETRY_UNAVAILABLE,
    silent_polls_before_issue,
)

_LOGGER = logging.getLogger(__name__)

type BasePowerConfigEntry = ConfigEntry[BasePowerCoordinator]


class BasePowerCoordinator(DataUpdateCoordinator[BatterySnapshot]):
    """Polls BatteryService/GetSnapshot for one site."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BasePowerConfigEntry,
        scan_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=scan_interval,
            # Small object, changes most polls; comparing is not worth it.
            always_update=True,
        )
        self.address_id: str = entry.data[CONF_ADDRESS_ID]
        session = async_get_clientsession(hass)
        self._auth = ClerkSessionProvider(session, entry.data[CONF_CLIENT_JWT])
        self.client = BasePowerClient(session, self._auth.async_get_token)
        # Read once at setup; decides which entities exist at all.
        self.capabilities = LocationCapabilities()
        # None until the first poll, so a battery already silent at startup
        # still gets a log line rather than looking like no transition.
        self._telemetry_available: bool | None = None
        self._silent_polls = 0

    async def async_load_capabilities(self) -> None:
        """Ask the site what it has, before any entity is created.

        A failure is not fatal: refusing to set the entry up because one extra
        call failed would lose the battery sensors too. The default declares
        nothing.
        """
        try:
            self.capabilities = await self.client.get_capabilities(self.address_id)
        except (BasePowerError, ClerkAuthError) as err:
            _LOGGER.warning(
                "Could not read the site's capabilities (%s); continuing without the "
                "capability-gated entities",
                err,
            )

    def _note_telemetry(self, snapshot: BatterySnapshot) -> None:
        """Track the battery falling silent: log it, and raise a repair issue.

        The log line matters because this condition takes almost every entity
        unavailable while the poll itself stays healthy, and
        DataUpdateCoordinator only speaks when a poll FAILS. Logged once per
        transition, not per poll.

        The repair issue waits out the grace period and clears the moment
        telemetry returns.
        """
        available = snapshot.telemetry_available

        if available:
            self._silent_polls = 0
            # Cleared unconditionally, not only on an observed transition: a
            # restart while the battery was dark leaves the issue in the
            # repairs list with this instance never having seen it raised.
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE)
            if self._telemetry_available is False:
                _LOGGER.info("The Base Power battery is reporting telemetry again")
            self._telemetry_available = True
            return

        self._silent_polls += 1
        if self._telemetry_available is not False:
            self._telemetry_available = False
            # The live wifi_status is not quoted: during a gap it reads
            # UNAVAILABLE, as stale as everything else in a snapshot nobody
            # sent, so it would say nothing. The diagnostic sensor is the
            # useful surface once the link is back.
            #
            # INFO not WARNING: on cellular fallback this fires between every
            # report. The repair issue at 30 minutes is what escalates.
            _LOGGER.info(
                "The Base Power battery has no current telemetry (state %s). "
                "This poll succeeded, so the battery is either between reports "
                "or has stopped sending. If it repeats, check the Wi-Fi access "
                "point it associates with: on Wi-Fi it reports about every 32 "
                "seconds, on cellular fallback far less often. Entities read "
                "unavailable rather than showing a stale value, and backup "
                "during a grid outage is unaffected",
                snapshot.state,
            )

        if self._silent_polls >= silent_polls_before_issue(self.update_interval):
            # Re-created every poll past the threshold, which is how it comes
            # back after a restart. async_create_issue is idempotent per id.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_TELEMETRY_UNAVAILABLE,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_TELEMETRY_UNAVAILABLE,
                translation_placeholders={
                    "name": self.config_entry.title if self.config_entry else "Base Power",
                },
            )

    async def _async_update_data(self) -> BatterySnapshot:
        try:
            snapshot = await self.client.get_snapshot(self.address_id)
            self._note_telemetry(snapshot)
            return snapshot
        except BasePowerAuthError:
            # A minted token can age out mid-flight, so give the credential one
            # chance before sending the user to re-authenticate.
            try:
                await self._auth.async_refresh()
                snapshot = await self.client.get_snapshot(self.address_id)
                # A poll that only succeeded on the retry still observed the
                # battery; skipping this would lose a transition.
                self._note_telemetry(snapshot)
                return snapshot
            except (BasePowerAuthError, ClerkAuthError) as retry_err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                    translation_placeholders={"error": str(retry_err)},
                ) from retry_err
        except ClerkAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except BasePowerError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

    @property
    def snapshot(self) -> BatterySnapshot | None:
        return self.data
