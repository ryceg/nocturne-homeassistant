"""Tests for the sensor platform."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from nocturne_py import ApsSnapshot, PumpSnapshot

from custom_components.nocturne.const import DOMAIN

from .conftest import make_api_client, make_entry, set_up


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


class TestSensorRegistration:
    async def test_every_sensor_is_registered_under_its_entry_and_device(self, reauth_hass, stubs):
        first = make_entry("https://first.example.com", "first-client", expires_in=3600)
        second = make_entry("https://second.example.com", "second-client", expires_in=3600)
        await set_up(reauth_hass, first, second)

        entities = er.async_get(reauth_hass)
        devices = dr.async_get(reauth_hass)
        for entry in (first, second):
            registered = er.async_entries_for_config_entry(entities, entry.entry_id)
            assert len(registered) == 12
            assert all(e.unique_id.startswith(f"{entry.entry_id}_") for e in registered)
            assert {e.device_id for e in registered} == {
                devices.async_get_device({(DOMAIN, entry.entry_id)}).id
            }
        assert (
            entities.async_get_entity_id("sensor", DOMAIN, f"{first.entry_id}_current_glucose")
            != entities.async_get_entity_id("sensor", DOMAIN, f"{second.entry_id}_current_glucose")
        )
