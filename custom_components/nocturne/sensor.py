"""Sensor platform for Nocturne."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import NocturneConfigEntry
from .const import DOMAIN


@dataclass(frozen=True, kw_only=True)
class NocturneSensorDescription(SensorEntityDescription):
    """Describes a Nocturne sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    available_fn: Callable[[dict[str, Any]], bool]
    coordinator_key: str  # "glucose" or "device"
    enum_options: list[str] | None = None


GLUCOSE_SENSORS: list[NocturneSensorDescription] = [
    NocturneSensorDescription(
        key="current_glucose",
        translation_key="current_glucose",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement="mg/dL",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:diabetes",
        coordinator_key="glucose",
        value_fn=lambda d: d["glucose"].mgdl if d.get("glucose") else None,
        available_fn=lambda d: d.get("glucose") is not None,
    ),
    NocturneSensorDescription(
        key="glucose_trend",
        translation_key="glucose_trend",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:trending-up",
        coordinator_key="glucose",
        value_fn=lambda d: d["glucose"].direction.value if d.get("glucose") and d["glucose"].direction else None,
        available_fn=lambda d: d.get("glucose") is not None and d["glucose"].direction is not None,
        enum_options=[
            "None", "DoubleUp", "SingleUp", "FortyFiveUp", "Flat",
            "FortyFiveDown", "SingleDown", "DoubleDown",
            "NotComputable", "RateOutOfRange",
        ],
    ),
    NocturneSensorDescription(
        key="iob",
        translation_key="iob",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:needle",
        coordinator_key="glucose",
        value_fn=lambda d: d["aps"].iob if d.get("aps") else None,
        available_fn=lambda d: d.get("aps") is not None and d["aps"].iob is not None,
    ),
    NocturneSensorDescription(
        key="cob",
        translation_key="cob",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:food-apple",
        coordinator_key="glucose",
        value_fn=lambda d: d["aps"].cob if d.get("aps") else None,
        available_fn=lambda d: d.get("aps") is not None and d["aps"].cob is not None,
    ),
    NocturneSensorDescription(
        key="predicted_bg",
        translation_key="predicted_bg",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement="mg/dL",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:crystal-ball",
        coordinator_key="glucose",
        value_fn=lambda d: d["aps"].eventual_bg if d.get("aps") else None,
        available_fn=lambda d: d.get("aps") is not None and d["aps"].eventual_bg is not None,
    ),
    NocturneSensorDescription(
        key="loop_status",
        translation_key="loop_status",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:sync",
        coordinator_key="glucose",
        enum_options=["enacted", "open", "unknown"],
        value_fn=lambda d: (
            "enacted" if d.get("aps") and d["aps"].enacted
            else ("open" if d.get("aps") else "unknown")
        ),
        available_fn=lambda d: d.get("aps") is not None,
    ),
    NocturneSensorDescription(
        key="active_basal_rate",
        translation_key="active_basal_rate",
        native_unit_of_measurement="U/hr",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:iv-bag",
        coordinator_key="glucose",
        value_fn=lambda d: d["aps"].enacted_rate if d.get("aps") else None,
        available_fn=lambda d: d.get("aps") is not None and d["aps"].enacted_rate is not None,
    ),
]

DEVICE_SENSORS: list[NocturneSensorDescription] = [
    NocturneSensorDescription(
        key="pump_reservoir",
        translation_key="pump_reservoir",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:beaker",
        coordinator_key="device",
        value_fn=lambda d: d["pump"].reservoir if d.get("pump") else None,
        available_fn=lambda d: d.get("pump") is not None and d["pump"].reservoir is not None,
    ),
    NocturneSensorDescription(
        key="pump_battery",
        translation_key="pump_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        coordinator_key="device",
        value_fn=lambda d: d["pump"].battery_percent if d.get("pump") else None,
        available_fn=lambda d: d.get("pump") is not None and d["pump"].battery_percent is not None,
    ),
    NocturneSensorDescription(
        key="cgm_battery",
        translation_key="cgm_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        coordinator_key="device",
        value_fn=lambda d: d["uploader"].battery if d.get("uploader") else None,
        available_fn=lambda d: d.get("uploader") is not None and d["uploader"].battery is not None,
    ),
    NocturneSensorDescription(
        key="sensor_age",
        translation_key="sensor_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        coordinator_key="device",
        value_fn=lambda d: d["sensor_age"].days if d.get("sensor_age") else None,
        available_fn=lambda d: d.get("sensor_age") is not None and d["sensor_age"].days is not None,
    ),
    NocturneSensorDescription(
        key="time_in_range",
        translation_key="time_in_range",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:target",
        coordinator_key="device",
        value_fn=lambda d: d["daily_summary"].time_in_range_percent if d.get("daily_summary") else None,
        available_fn=lambda d: d.get("daily_summary") is not None and d["daily_summary"].time_in_range_percent is not None,
    ),
]

ALL_SENSORS = GLUCOSE_SENSORS + DEVICE_SENSORS


class NocturneSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Nocturne sensor."""

    entity_description: NocturneSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: NocturneSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        if description.enum_options is not None:
            self._attr_options = description.enum_options
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Nocturne",
            "manufacturer": "Nocturne",
        }

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        return self.entity_description.available_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NocturneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up every Nocturne sensor; each stays unavailable until its data arrives."""
    data = entry.runtime_data
    async_add_entities(
        NocturneSensor(
            data.glucose_coordinator
            if desc.coordinator_key == "glucose"
            else data.device_coordinator,
            desc,
            entry.entry_id,
        )
        for desc in ALL_SENSORS
    )
