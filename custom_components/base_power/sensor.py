"""Sensors for Base Power.

The power flow is the useful part and it is signed: a negative
`from_storage_kw` is the battery charging. That is one sensor covering both
directions, which is what Home Assistant's energy handling expects, rather
than two sensors that are each wrong half the time.

Values the API omits stay `None` and the entity reads `unknown`. That matters
more than it sounds: a site with no solar does not receive `fromSolarKw: 0.0`,
it receives nothing at all, and a solar sensor sitting at a confident zero is
indistinguishable from a real measurement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BatterySnapshot
from .const import BACKUP_REFERENCE_KW
from .coordinator import BasePowerConfigEntry
from .entity import BasePowerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BasePowerSensorDescription(SensorEntityDescription):
    """A sensor and the function that reads it out of a snapshot."""

    value_fn: Callable[[BatterySnapshot], float | int | str | None]


def _stored_energy_kwh(snapshot: BatterySnapshot) -> float | None:
    """Energy left, derived from the backup-hours-at-750 W figure.

    The on-grid snapshot carries no state of charge (confirmed live), so this
    is the only route to "how full is it" in the normal case. It is a
    derivation, not a reading: the API reports hours at a 750 W reference
    load, so hours x 0.75 kW is the energy those hours imply.
    """
    hours = snapshot.estimated_backup_hours_at_750_watts
    return None if hours is None else round(hours * BACKUP_REFERENCE_KW, 2)


SENSORS: tuple[BasePowerSensorDescription, ...] = (
    BasePowerSensorDescription(
        key="power_to_home",
        translation_key="power_to_home",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.power_flow.to_home_kw,
    ),
    BasePowerSensorDescription(
        key="power_from_grid",
        translation_key="power_from_grid",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.power_flow.from_grid_kw,
    ),
    BasePowerSensorDescription(
        key="power_from_storage",
        translation_key="power_from_storage",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        # Signed: negative is the battery charging.
        value_fn=lambda s: s.power_flow.from_storage_kw,
    ),
    BasePowerSensorDescription(
        key="power_from_solar",
        translation_key="power_from_solar",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.power_flow.from_solar_kw,
    ),
    BasePowerSensorDescription(
        key="backup_hours",
        translation_key="backup_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.estimated_backup_hours_at_current_usage,
    ),
    BasePowerSensorDescription(
        key="stored_energy",
        translation_key="stored_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_stored_energy_kwh,
    ),
    BasePowerSensorDescription(
        key="battery_state",
        translation_key="battery_state",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "on_grid",
            "off_grid_outage",
            "off_grid_no_home_power",
            "off_grid_overcurrent",
            "off_grid_overcurrent_standby",
            "telemetry_unavailable",
            "unknown",
        ],
        value_fn=lambda s: s.state,
    ),
    # Only populated while off grid; the API does not publish it on grid.
    BasePowerSensorDescription(
        key="state_of_charge",
        translation_key="state_of_charge",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.state_of_energy_percent,
    ),
    BasePowerSensorDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.wifi_ssid,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BasePowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BasePowerSensor(coordinator, entry, d) for d in SENSORS)


class BasePowerSensor(BasePowerEntity, SensorEntity):
    """One field of the battery snapshot."""

    entity_description: BasePowerSensorDescription

    def __init__(self, coordinator, entry, description: BasePowerSensorDescription) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address_id}_{description.key}"

    @property
    def native_value(self) -> float | int | str | None:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        return self.entity_description.value_fn(snapshot)

    @property
    def available(self) -> bool:
        """The battery-state sensor stays available when telemetry is not.

        Every other sensor goes unavailable with the telemetry, because it
        has nothing to report. This one's whole job is to say WHY, so taking
        it away at that moment would hide the answer.
        """
        if self.entity_description.key == "battery_state":
            return super(BasePowerEntity, self).available and self.coordinator.snapshot is not None
        return super().available
