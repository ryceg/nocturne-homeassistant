"""Tests for the Nocturne diagnostics download."""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.nocturne.const import CONF_NOTIFY_SERVICES

from .conftest import Stubs, make_entry, set_up

INSTANCE_URL = "https://secret-host.example.net"
CLIENT_ID = "secret-client-id"
ACCESS_TOKEN = "secret-access-token"
REFRESH_TOKEN = "secret-refresh-token"


@pytest.fixture
async def set_up_entry(reauth_hass: HomeAssistant, stubs: Stubs) -> MockConfigEntry:
    assert await async_setup_component(reauth_hass, "diagnostics", {})
    entry = make_entry(
        INSTANCE_URL,
        CLIENT_ID,
        expires_in=3600,
        access_token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        options={CONF_NOTIFY_SERVICES: ["notify.mobile_app_phone"]},
    )
    await set_up(reauth_hass, entry)
    return entry


async def test_diagnostics_report_health_for_a_set_up_entry(
    hass: HomeAssistant, hass_client, set_up_entry: MockConfigEntry
):
    result = await get_diagnostics_for_config_entry(hass, hass_client, set_up_entry)

    assert result["signalr_connected"] is True
    assert result["coordinators"] == {
        "glucose": {"last_update_success": True, "data_keys": ["aps", "glucose"]},
        "device": {
            "last_update_success": True,
            "data_keys": ["daily_summary", "profile", "pump", "sensor_age", "uploader"],
        },
    }
    assert result["entry"]["options"] == {CONF_NOTIFY_SERVICES: ["notify.mobile_app_phone"]}


async def test_diagnostics_leak_no_secrets_or_glucose_values(
    hass: HomeAssistant, hass_client, set_up_entry: MockConfigEntry
):
    result = await get_diagnostics_for_config_entry(hass, hass_client, set_up_entry)
    serialised = json.dumps(result)

    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_ID, INSTANCE_URL, "secret-host"):
        assert secret not in serialised
    for glucose_marker in ('"mgdl"', '"mmol"', "SensorGlucose", "time_in_range_percent"):
        assert glucose_marker not in serialised
