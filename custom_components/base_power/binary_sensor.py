"""Binary sensors for Base Power.

The grid-outage sensor is the reason most people would install this, so it is
the one entity whose behaviour is worth being fussy about: it must be `on`
only when the API actually says the grid is down, and `unavailable` rather
than `off` when nothing is reporting. An outage sensor that reads a confident
`off` because the poll failed is worse than no sensor.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BasePowerConfigEntry, BasePowerCoordinator
from .entity import BasePowerEntity

PARALLEL_UPDATES = 0

GRID_OUTAGE = BinarySensorEntityDescription(
    key="grid_outage",
    translation_key="grid_outage",
    device_class=BinarySensorDeviceClass.PROBLEM,
)

OFF_GRID = BinarySensorEntityDescription(
    key="off_grid",
    translation_key="off_grid",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BasePowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            BasePowerGridOutage(coordinator, entry),
            BasePowerOffGrid(coordinator, entry),
        ]
    )


class BasePowerGridOutage(BasePowerEntity, BinarySensorEntity):
    """On while the API reports the grid down and the battery carrying."""

    def __init__(self, coordinator: BasePowerCoordinator, entry: BasePowerConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = GRID_OUTAGE
        self._attr_unique_id = f"{coordinator.address_id}_grid_outage"

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.snapshot
        return None if snapshot is None else snapshot.is_grid_outage


class BasePowerOffGrid(BasePowerEntity, BinarySensorEntity):
    """On for any off-grid state, not only a plain outage.

    Separate from the outage sensor because the other off-grid states -
    overcurrent, standby, no home power - are the battery not delivering for
    reasons that are not "the grid went away", and an automation that wants
    "am I running on battery at all" should not have to enumerate them.
    """

    def __init__(self, coordinator: BasePowerCoordinator, entry: BasePowerConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = OFF_GRID
        self._attr_unique_id = f"{coordinator.address_id}_off_grid"

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.snapshot
        return None if snapshot is None else snapshot.is_off_grid
