"""Tests for sensor platform: determine_available_sensors, NocturneSensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nocturne_py import ApsSnapshot, SensorGlucose, GlucoseDirection

from custom_components.nocturne.sensor import (
    DEVICE_SENSORS,
    GLUCOSE_SENSORS,
    NocturneSensor,
    determine_available_sensors,
)


class TestDetermineAvailableSensors:
    def test_full_data_returns_all_glucose_sensors(self, glucose_data):
        available = determine_available_sensors(glucose_data, None)
        glucose_keys = {s.key for s in available}
        assert "current_glucose" in glucose_keys
        assert "glucose_trend" in glucose_keys
        assert "iob" in glucose_keys
        assert "cob" in glucose_keys

    def test_full_data_returns_all_device_sensors(self, device_data):
        available = determine_available_sensors(None, device_data)
        device_keys = {s.key for s in available}
        assert "pump_reservoir" in device_keys
        assert "pump_battery" in device_keys
        assert "time_in_range" in device_keys

    def test_none_glucose_data(self, device_data):
        available = determine_available_sensors(None, device_data)
        keys = {s.key for s in available}
        # No glucose sensors should appear
        for sensor in GLUCOSE_SENSORS:
            assert sensor.key not in keys

    def test_none_device_data(self, glucose_data):
        available = determine_available_sensors(glucose_data, None)
        keys = {s.key for s in available}
        for sensor in DEVICE_SENSORS:
            assert sensor.key not in keys

    def test_both_none_returns_empty(self):
        assert determine_available_sensors(None, None) == []

    def test_partial_glucose_data(self):
        # Only glucose, no APS — should get glucose/trend but not iob/cob
        partial = {
            "glucose": SensorGlucose(mgdl=100, direction=GlucoseDirection("Flat")),
            "aps": None,
        }
        available = determine_available_sensors(partial, None)
        keys = {s.key for s in available}
        assert "current_glucose" in keys
        assert "iob" not in keys


class TestNocturneSensor:
    @pytest.fixture
    def sensor(self, mock_glucose_coordinator) -> NocturneSensor:
        desc = GLUCOSE_SENSORS[0]  # current_glucose
        return NocturneSensor(mock_glucose_coordinator, desc, "test_entry")

    def test_unique_id(self, sensor):
        assert sensor._attr_unique_id == "test_entry_current_glucose"

    def test_device_info(self, sensor):
        assert ("nocturne", "test_entry") in sensor._attr_device_info["identifiers"]

    def test_native_value(self, sensor, glucose_data):
        assert sensor.native_value == 120

    def test_native_value_none_when_no_data(self, sensor):
        sensor.coordinator.data = None
        assert sensor.native_value is None

    def test_available_true(self, sensor):
        # super().available checks last_update_success
        sensor.coordinator.last_update_success = True
        assert sensor.available is True

    def test_available_false_when_no_data(self, sensor):
        sensor.coordinator.data = None
        assert sensor.available is False


class TestSensorSetupEntry:
    async def test_creates_correct_entities(
        self, mock_hass, mock_config_entry, mock_glucose_coordinator, mock_device_coordinator
    ):
        from custom_components.nocturne.sensor import async_setup_entry

        added_entities = []

        async_add_entities = MagicMock(side_effect=lambda entities: added_entities.extend(entities))

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        assert len(added_entities) > 0
        # All created entities should be NocturneSensor instances
        for entity in added_entities:
            assert isinstance(entity, NocturneSensor)

    async def test_assigns_correct_coordinators(
        self, mock_hass, mock_config_entry, mock_glucose_coordinator, mock_device_coordinator
    ):
        from custom_components.nocturne.sensor import async_setup_entry

        added_entities = []
        async_add_entities = MagicMock(side_effect=lambda entities: added_entities.extend(entities))

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        for entity in added_entities:
            if entity.entity_description.coordinator_key == "glucose":
                assert entity.coordinator is mock_glucose_coordinator
            else:
                assert entity.coordinator is mock_device_coordinator
