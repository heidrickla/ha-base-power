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
from .const import CONF_ADDRESS_ID, CONF_CLIENT_JWT, DEFAULT_SCAN_INTERVAL, DOMAIN

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

    def _note_telemetry(self, snapshot: BatterySnapshot) -> None:
        """Say once when the battery stops reporting, and once when it returns.

        This is the condition that takes almost every entity unavailable while
        the poll itself stays perfectly healthy, so without a line saying so
        the logs show nothing at all and the integration looks broken when it
        is working exactly as designed. Once per transition, not per poll:
        at 30 s intervals that would be 2,880 identical lines a day.
        """
        available = snapshot.telemetry_available
        if available == self._telemetry_available:
            return
        # Nothing is logged for the very first poll of a healthy battery -
        # there is no transition worth reporting.
        if self._telemetry_available is None and available:
            self._telemetry_available = True
            return
        self._telemetry_available = available
        if not available:
            _LOGGER.warning(
                "The Base Power battery is not reporting telemetry (state %s, "
                "Wi-Fi %s). The connection to Base is fine - this poll succeeded - "
                "so the battery itself is not sending data, and its entities are "
                "unavailable rather than showing a stale or zero reading. Check "
                "the battery's network connection; the Base app will show the "
                "same gap",
                snapshot.state,
                snapshot.wifi_status or "unknown",
            )
        else:
            _LOGGER.info("The Base Power battery is reporting telemetry again")

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
                    f"Base Power rejected the stored credential: {retry_err}"
                ) from retry_err
        except ClerkAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BasePowerError as err:
            raise UpdateFailed(f"Base Power API error: {err}") from err

    @property
    def snapshot(self) -> BatterySnapshot | None:
        return self.data
