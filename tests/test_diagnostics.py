"""Tests for the Nocturne diagnostics download."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.nocturne.const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_NOTIFY_SERVICES,
    CONF_TOKEN_URL,
    DOMAIN,
)

INSTANCE_URL = "https://secret-host.example.net"
CLIENT_ID = "secret-client-id"
ACCESS_TOKEN = "secret-access-token"
REFRESH_TOKEN = "secret-refresh-token"


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Nocturne",
        unique_id=INSTANCE_URL,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "expires_at": time.time() + 3600,
                "expires_in": 3600,
            },
            CONF_INSTANCE_URL: INSTANCE_URL,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_AUTHORIZE_URL: f"{INSTANCE_URL}/oauth/authorize",
            CONF_TOKEN_URL: f"{INSTANCE_URL}/oauth/token",
        },
        options={CONF_NOTIFY_SERVICES: ["notify.mobile_app_phone"]},
    )


@pytest.fixture
async def set_up_entry(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    mock_api_client: MagicMock,
) -> MockConfigEntry:
    assert await async_setup_component(hass, "auth", {})
    assert await async_setup_component(hass, "diagnostics", {})
    entry.add_to_hass(hass)
    signalr_client = MagicMock()
    signalr_client.connected = True
    with (
        patch("custom_components.nocturne.NocturneApiClient", return_value=mock_api_client),
        patch("custom_components.nocturne.NocturneSignalRClient", return_value=signalr_client),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
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
    hass: HomeAssistant, hass_client, set_up_entry: MockConfigEntry, mock_api_client: MagicMock
):
    result = await get_diagnostics_for_config_entry(hass, hass_client, set_up_entry)
    serialised = json.dumps(result)

    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_ID, INSTANCE_URL, "secret-host"):
        assert secret not in serialised
    for glucose_marker in ('"mgdl"', '"mmol"', "SensorGlucose", "time_in_range_percent"):
        assert glucose_marker not in serialised
