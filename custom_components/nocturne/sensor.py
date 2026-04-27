"""Sensor platform for Nocturne."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SENSOR_RECHECK_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class NocturneSensorDescription(SensorEntityDescription):
    """Describes a Nocturne sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    available_fn: Callable[[dict[str, Any]], bool]
    coordinator_key: str  # "glucose" or "device"
    enum_options: list[str] | None = None


def _safe_get(data: dict[str, Any], *keys: str | int) -> Any:
    """Safely traverse nested dicts and lists."""
    current = data
    for key in keys:
        if isinstance(current, dict) and isinstance(key, str):
            current = current.get(key)
        elif isinstance(current, (list, tuple)) and isinstance(key, int):
            try:
                current = current[key]
            except IndexError:
                return None
        else:
            return None
    return current


GLUCOSE_SENSORS: list[NocturneSensorDescription] = [
    NocturneSensorDescription(
        key="current_glucose",
        name="Current Glucose",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE,
        native_unit_of_measurement="mg/dL",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:diabetes",
        coordinator_key="glucose",
        value_fn=lambda d: _safe_get(d, "entry", "sgv"),
        available_fn=lambda d: _safe_get(d, "entry") is not None,
    ),
    NocturneSensorDescription(
        key="glucose_trend",
        name="Glucose Trend",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:trending-up",
        coordinator_key="glucose",
        value_fn=lambda d: _safe_get(d, "entry", "direction"),
        available_fn=lambda d: _safe_get(d, "entry") is not None,
        enum_options=[
            "None", "DoubleUp", "SingleUp", "FortyFiveUp", "Flat",
            "FortyFiveDown", "SingleDown", "DoubleDown",
            "NOT COMPUTABLE", "RATE OUT OF RANGE",
        ],
    ),
    NocturneSensorDescription(
        key="iob",
        name="Insulin on Board",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:needle",
        coordinator_key="glucose",
        value_fn=lambda d: _safe_get(d, "device_status", "openaps", "iob", "iob"),
        available_fn=lambda d: _safe_get(d, "device_status", "openaps", "iob") is not None,
    ),
    NocturneSensorDescription(
        key="cob",
        name="Carbs on Board",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:food-apple",
        coordinator_key="glucose",
        value_fn=lambda d: _safe_get(d, "device_status", "openaps", "enacted", "COB"),
        available_fn=lambda d: _safe_get(d, "device_status", "openaps", "enacted") is not None,
    ),
    NocturneSensorDescription(
        key="predicted_bg",
        name="Predicted BG",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE,
        native_unit_of_measurement="mg/dL",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:crystal-ball",
        coordinator_key="glucose",
        value_fn=lambda d: (
            _safe_get(d, "device_status", "openaps", "enacted", "predBGs", "IOB", -1)
            if isinstance(
                _safe_get(d, "device_status", "openaps", "enacted", "predBGs", "IOB"), list
            )
            else None
        ),
        available_fn=lambda d: isinstance(
            _safe_get(d, "device_status", "openaps", "enacted", "predBGs", "IOB"), list
        ),
    ),
    NocturneSensorDescription(
        key="loop_status",
        name="Loop Status",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:sync",
        coordinator_key="glucose",
        enum_options=["enacted", "open", "unknown"],
        value_fn=lambda d: (
            "enacted"
            if _safe_get(d, "device_status", "openaps", "enacted")
            else ("open" if _safe_get(d, "device_status", "openaps") else "unknown")
        ),
        available_fn=lambda d: _safe_get(d, "device_status", "openaps") is not None,
    ),
    NocturneSensorDescription(
        key="active_basal_rate",
        name="Active Basal Rate",
        native_unit_of_measurement="U/hr",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:iv-bag",
        coordinator_key="glucose",
        value_fn=lambda d: _safe_get(d, "device_status", "openaps", "enacted", "rate"),
        available_fn=lambda d: _safe_get(
            d, "device_status", "openaps", "enacted", "rate"
        )
        is not None,
    ),
]

DEVICE_SENSORS: list[NocturneSensorDescription] = [
    NocturneSensorDescription(
        key="pump_reservoir",
        name="Pump Reservoir",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:beaker",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "device_status", "pump", "reservoir"),
        available_fn=lambda d: _safe_get(d, "device_status", "pump", "reservoir") is not None,
    ),
    NocturneSensorDescription(
        key="pump_battery",
        name="Pump Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "device_status", "pump", "battery", "percent"),
        available_fn=lambda d: _safe_get(d, "device_status", "pump", "battery") is not None,
    ),
    NocturneSensorDescription(
        key="cgm_battery",
        name="CGM Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "device_status", "uploader", "battery"),
        available_fn=lambda d: _safe_get(d, "device_status", "uploader", "battery") is not None,
    ),
    NocturneSensorDescription(
        key="cgm_signal",
        name="CGM Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:signal",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "device_status", "uploader", "signal"),
        available_fn=lambda d: _safe_get(d, "device_status", "uploader", "signal") is not None,
    ),
    NocturneSensorDescription(
        key="sensor_age",
        name="Sensor Age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "device_status", "uploader", "sensorAge"),
        available_fn=lambda d: _safe_get(d, "device_status", "uploader", "sensorAge") is not None,
    ),
    NocturneSensorDescription(
        key="active_profile",
        name="Active Profile",
        icon="mdi:account-cog",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "profile", "name"),
        available_fn=lambda d: _safe_get(d, "profile") is not None,
    ),
    NocturneSensorDescription(
        key="time_in_range",
        name="Time in Range",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:target",
        coordinator_key="device",
        value_fn=lambda d: _safe_get(d, "report", "timeInRange"),
        available_fn=lambda d: _safe_get(d, "report", "timeInRange") is not None,
    ),
]

ALL_SENSORS = GLUCOSE_SENSORS + DEVICE_SENSORS


def determine_available_sensors(
    glucose_data: dict[str, Any] | None,
    device_data: dict[str, Any] | None,
) -> list[NocturneSensorDescription]:
    """Determine which sensors have data available."""
    available = []
    for sensor in ALL_SENSORS:
        data = glucose_data if sensor.coordinator_key == "glucose" else device_data
        if data is not None and sensor.available_fn(data):
            available.append(sensor)
    return available


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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nocturne sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    glucose_coordinator = data["glucose_coordinator"]
    device_coordinator = data["device_coordinator"]

    available = determine_available_sensors(
        glucose_coordinator.data, device_coordinator.data
    )

    registered_keys: set[str] = set()
    entities: list[NocturneSensor] = []
    for desc in available:
        coordinator = (
            glucose_coordinator
            if desc.coordinator_key == "glucose"
            else device_coordinator
        )
        entities.append(NocturneSensor(coordinator, desc, entry.entry_id))
        registered_keys.add(desc.key)

    async_add_entities(entities)

    async def _recheck_sensors(_now: Any) -> None:
        """Re-check for newly available sensors (e.g. user started looping)."""
        now_available = determine_available_sensors(
            glucose_coordinator.data, device_coordinator.data
        )
        new_entities: list[NocturneSensor] = []
        for desc in now_available:
            if desc.key not in registered_keys:
                coordinator = (
                    glucose_coordinator
                    if desc.coordinator_key == "glucose"
                    else device_coordinator
                )
                new_entities.append(
                    NocturneSensor(coordinator, desc, entry.entry_id)
                )
                registered_keys.add(desc.key)
        if new_entities:
            _LOGGER.info(
                "Discovered %d new sensor(s): %s",
                len(new_entities),
                [e.entity_description.key for e in new_entities],
            )
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _recheck_sensors,
            timedelta(seconds=SENSOR_RECHECK_INTERVAL_SECONDS),
        )
    )
