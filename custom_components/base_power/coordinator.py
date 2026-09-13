"""Coordinator for the Base Power integration.

Holds one battery snapshot per site and refreshes it on an interval. There is
no push channel: the app polls, so this polls too - see DEFAULT_SCAN_INTERVAL
in const.py for why it does not copy the app's 1 s rate.
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
    MIN_TELEMETRY_POLLS,
    TELEMETRY_GRACE,
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
            # The snapshot is a small object that changes most polls, and
            # entities derive from several of its fields, so comparing is not
            # worth the equality machinery.
            always_update=True,
        )
        self.address_id: str = entry.data[CONF_ADDRESS_ID]
        session = async_get_clientsession(hass)
        self._auth = ClerkSessionProvider(session, entry.data[CONF_CLIENT_JWT])
        self.client = BasePowerClient(session, self._auth.async_get_token)
        # What the site declares it has. Read once at setup and used to
        # decide which entities exist at all - a solar sensor on a site
        # without solar would sit at unknown for ever and read as broken.
        self.capabilities = LocationCapabilities()
        # Whether the battery was last seen reporting. None until the first
        # poll, so a battery that is already silent at startup still gets a
        # line rather than being mistaken for a transition that never
        # happened.
        self._telemetry_available: bool | None = None
        # Consecutive polls with no telemetry, so a blip does not raise a
        # repair issue the user then has to dismiss.
        self._silent_polls = 0

    async def async_load_capabilities(self) -> None:
        """Ask the site what it has, before any entity is created.

        A failure here is not fatal: the capabilities only widen or narrow
        the entity set, and refusing to set the entry up because one extra
        call failed would lose the battery sensors too. The default is the
        conservative one - nothing declared.
        """
        try:
            self.capabilities = await self.client.get_capabilities(self.address_id)
        except (BasePowerError, ClerkAuthError) as err:
            _LOGGER.warning(
                "Could not read the site's capabilities (%s); continuing without the "
                "capability-gated entities",
                err,
            )

    @property
    def _silent_polls_before_issue(self) -> int:
        """How many quiet polls the grace period is worth at this interval."""
        seconds = (
            self.update_interval.total_seconds()
            if self.update_interval
            else DEFAULT_SCAN_INTERVAL.total_seconds()
        )
        return max(MIN_TELEMETRY_POLLS, round(TELEMETRY_GRACE.total_seconds() / seconds))

    def _note_telemetry(self, snapshot: BatterySnapshot) -> None:
        """Track the battery falling silent: log it, and raise a repair issue.

        Two different jobs. The log line exists because this is the condition
        that takes almost every entity unavailable while the poll itself stays
        perfectly healthy - DataUpdateCoordinator only speaks when a poll
        FAILS, so without this the logs are silent exactly when the
        integration looks broken. Once per transition, not per poll.

        The repair issue is the user-facing half, and it deliberately waits:
        a single quiet poll is not worth a notification, and at the default
        interval raising and clearing on blips would produce two repairs a
        minute. It clears the moment telemetry returns.
        """
        available = snapshot.telemetry_available

        if available:
            self._silent_polls = 0
            # Cleared unconditionally rather than only on an observed
            # transition: if Home Assistant restarted while the battery was
            # dark, this instance never saw the issue raised, but the issue
            # is still sitting in the repairs list.
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_TELEMETRY_UNAVAILABLE)
            if self._telemetry_available is False:
                _LOGGER.info("The Base Power battery is reporting telemetry again")
            self._telemetry_available = True
            return

        self._silent_polls += 1
        if self._telemetry_available is not False:
            self._telemetry_available = False
            # The battery's Wi-Fi status is deliberately NOT quoted here. It
            # reads NOT_CONNECTED on a battery that is reporting perfectly
            # well, because the unit also has a cellular link and uses it when
            # Wi-Fi is down - so naming it alongside a telemetry fault invites
            # exactly the wrong action, chasing a network that is not the
            # transport. Base's own banner never mentions Wi-Fi either.
            _LOGGER.warning(
                "The Base Power battery is not reporting telemetry (state %s). "
                "The connection to Base is fine - this poll succeeded - so the "
                "battery itself is not sending data, and its entities are "
                "unavailable rather than showing a stale or zero reading. "
                "Backup during a grid outage is unaffected. If it does not "
                "clear, contact Base Support",
                snapshot.state,
            )

        if self._silent_polls >= self._silent_polls_before_issue:
            # Re-created every poll once over the threshold, which is how the
            # issue comes back by itself after a restart. async_create_issue
            # is idempotent for the same id.
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
        except BasePowerAuthError as err:
            # The API refused the token. A minted token can simply have aged
            # out mid-flight, so give the credential one chance to prove it
            # is still good before sending the user to re-authenticate -
            # asking someone to sign in again because a 60 s token expired
            # two seconds early would be the wrong answer.
            try:
                await self._auth.async_refresh()
                snapshot = await self.client.get_snapshot(self.address_id)
                # Same bookkeeping as the first-attempt path: a poll that only
                # succeeded on the retry still observed the battery, and
                # skipping it here would lose a transition.
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
