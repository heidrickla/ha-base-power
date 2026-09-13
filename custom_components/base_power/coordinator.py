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

    async def _async_update_data(self) -> BatterySnapshot:
        try:
            return await self.client.get_snapshot(self.address_id)
        except BasePowerAuthError as err:
            # The API refused the token. A minted token can simply have aged
            # out mid-flight, so give the credential one chance to prove it
            # is still good before sending the user to re-authenticate -
            # asking someone to sign in again because a 60 s token expired
            # two seconds early would be the wrong answer.
            try:
                await self._auth.async_refresh()
                return await self.client.get_snapshot(self.address_id)
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
