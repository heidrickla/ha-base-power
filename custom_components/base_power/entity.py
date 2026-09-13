"""Shared entity base for Base Power."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BasePowerConfigEntry, BasePowerCoordinator


class BasePowerEntity(CoordinatorEntity[BasePowerCoordinator]):
    """One device per site, identified by its address id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BasePowerCoordinator, entry: BasePowerConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address_id)},
            manufacturer=MANUFACTURER,
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        """Available while the poll works AND the panel is reporting telemetry.

        Two different failures, and both mean the reading is not to be
        trusted: the poll failing, and the API answering with
        `telemetry_unavailable`, which is the service itself saying it has no
        current data. Holding the last value through either would show a
        stale number as current.
        """
        snapshot = self.coordinator.snapshot
        return (
            super().available
            and snapshot is not None
            and snapshot.telemetry_available
        )
