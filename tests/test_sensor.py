"""Tests for the sensor platform."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from nocturne_py import ApsSnapshot, PumpSnapshot

from custom_components.nocturne.sensor import GLUCOSE_SENSORS, NocturneSensor

from .conftest import make_api_client, make_entry, set_up


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
    async def test_every_sensor_exists_and_waits_for_its_data(self, reauth_hass, stubs):
        entry = make_entry(expires_in=3600)
        client = make_api_client()
        client.get_latest_aps_snapshot.return_value = None
        client.get_latest_pump_snapshot.return_value = None
        stubs.clients[entry.data["instance_url"]] = client
        await set_up(reauth_hass, entry)

        def states() -> dict[str, str]:
            return {state.entity_id: state.state for state in reauth_hass.states.async_all("sensor")}

        assert states() == {
            "sensor.nocturne_current_glucose": "120",
            "sensor.nocturne_glucose_trend": "Flat",
            "sensor.nocturne_insulin_on_board": STATE_UNAVAILABLE,
            "sensor.nocturne_carbs_on_board": STATE_UNAVAILABLE,
            "sensor.nocturne_predicted_bg": STATE_UNAVAILABLE,
            "sensor.nocturne_loop_status": STATE_UNAVAILABLE,
            "sensor.nocturne_active_basal_rate": STATE_UNAVAILABLE,
            "sensor.nocturne_pump_reservoir": STATE_UNAVAILABLE,
            "sensor.nocturne_pump_battery": STATE_UNAVAILABLE,
            "sensor.nocturne_cgm_battery": "90",
            "sensor.nocturne_sensor_age": "5",
            "sensor.nocturne_time_in_range": "72.5",
        }

        client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=2.5)
        client.get_latest_pump_snapshot.return_value = PumpSnapshot(reservoir=90.0)
        await entry.runtime_data.glucose_coordinator.async_refresh()
        await entry.runtime_data.device_coordinator.async_refresh()
        await reauth_hass.async_block_till_done()

        assert states()["sensor.nocturne_insulin_on_board"] == "2.5"
        assert states()["sensor.nocturne_pump_reservoir"] == "90.0"
