"""Shared fixtures for Nocturne tests."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pysignalr.client import SignalRClient
from pysignalr.messages import CompletionMessage
from nocturne_py import (
    ApsSnapshot,
    DeviceAgeInfo,
    GlucoseDirection,
    PumpSnapshot,
    SensorGlucose,
    UploaderSnapshot,
)
from nocturne_py.models import DailySummaryDay, ProfileSummary

from custom_components.nocturne.const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
    DOMAIN,
)

INSTANCE_URL = "https://nocturne.example.com"
AUTHORIZE_URL = f"{INSTANCE_URL}/oauth/authorize"
TOKEN_URL = f"{INSTANCE_URL}/oauth/token"
CLIENT_ID = "stored-client-id"


def make_entry(
    instance_url: str = INSTANCE_URL,
    client_id: str = CLIENT_ID,
    unique_id: str | None = None,
    expires_in: float = -10,
    **data: object,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Nocturne",
        unique_id=unique_id or instance_url,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "old-access",
                "refresh_token": f"refresh-{client_id}",
                "expires_at": time.time() + expires_in,
                "expires_in": 3600,
            },
            CONF_INSTANCE_URL: instance_url,
            CONF_CLIENT_ID: client_id,
            CONF_AUTHORIZE_URL: f"{instance_url}/oauth/authorize",
            CONF_TOKEN_URL: f"{instance_url}/oauth/token",
            **data,
        },
    )


@pytest.fixture
async def reauth_hass(hass: HomeAssistant, enable_custom_integrations: None) -> HomeAssistant:
    hass.config.external_url = "https://ha.example.com"
    assert await async_setup_component(hass, "auth", {})
    return hass


@pytest.fixture
async def my_hass(hass: HomeAssistant, enable_custom_integrations: None) -> HomeAssistant:
    hass.config.external_url = None
    hass.config.components.add("my")
    assert await async_setup_component(hass, "auth", {})
    return hass


@pytest.fixture
def glucose_data() -> dict:
    """Realistic glucose coordinator data using SDK models."""
    return {
        "glucose": SensorGlucose(
            mgdl=120,
            direction=GlucoseDirection("Flat"),
            mills=1700000000000,
        ),
        "aps": ApsSnapshot(
            iob=1.5,
            cob=20,
            enacted_rate=0.8,
            eventual_bg=110,
            enacted=True,
        ),
    }


@pytest.fixture
def device_data() -> dict:
    """Realistic device coordinator data using SDK models."""
    return {
        "pump": PumpSnapshot(reservoir=150.0, battery_percent=80),
        "uploader": UploaderSnapshot(battery=90),
        "profile": ProfileSummary(),
        "daily_summary": DailySummaryDay(time_in_range_percent=72.5),
        "sensor_age": DeviceAgeInfo(days=5),
    }


@pytest.fixture
def mock_api_client() -> AsyncMock:
    """AsyncMock of NocturneApiClient with sensible defaults."""
    client = AsyncMock()
    client.validate_connection.return_value = True
    client.get_latest_glucose.return_value = SensorGlucose(
        mgdl=120, direction=GlucoseDirection("Flat")
    )
    client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=1.5)
    client.get_latest_pump_snapshot.return_value = PumpSnapshot(
        reservoir=150.0, battery_percent=80
    )
    client.get_latest_uploader_snapshot.return_value = UploaderSnapshot(battery=90)
    client.get_profile_summary.return_value = ProfileSummary()
    client.get_daily_summary.return_value = DailySummaryDay(time_in_range_percent=72.5)
    client.get_sensor_age.return_value = DeviceAgeInfo(days=5)
    client.create_glucose.return_value = SensorGlucose(mgdl=120)
    client.create_carb_intake.return_value = MagicMock()
    client.create_bolus.return_value = MagicMock()
    client.create_state_span.return_value = MagicMock()
    return client


@pytest.fixture
def mock_config_entry() -> MagicMock:
    """MockConfigEntry with OAuth token and instance URL."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        "token": {"access_token": "fake-token"},
        "instance_url": "https://nocturne.example.com",
    }
    return entry


@pytest.fixture
def mock_glucose_coordinator(glucose_data: dict) -> MagicMock:
    """MagicMock glucose coordinator with fixture data."""
    coordinator = MagicMock()
    coordinator.data = glucose_data
    coordinator.last_update_success = True
    return coordinator


@pytest.fixture
def mock_device_coordinator(device_data: dict) -> MagicMock:
    """MagicMock device coordinator with fixture data."""
    coordinator = MagicMock()
    coordinator.data = device_data
    coordinator.last_update_success = True
    return coordinator


@pytest.fixture
def mock_hass(
    mock_config_entry, mock_api_client, mock_glucose_coordinator, mock_device_coordinator
) -> MagicMock:
    """MagicMock HomeAssistant with data populated."""
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            mock_config_entry.entry_id: {
                "client": mock_api_client,
                "glucose_coordinator": mock_glucose_coordinator,
                "device_coordinator": mock_device_coordinator,
            }
        }
    }
    return hass


@pytest.fixture
def connect_hub():
    """Connect a Nocturne client to a real pysignalr client whose transport is stubbed.

    The server's completion, when given, is fed back through pysignalr's own message dispatch.
    """

    def connect(client, reply=None, send_error=None):
        hub = SignalRClient("https://nocturne.example.com/hubs/home-assistant")

        async def transport_send(message):
            if send_error is not None:
                raise send_error
            if reply is not None:
                asyncio.get_running_loop().call_soon(
                    asyncio.ensure_future,
                    hub._on_message(CompletionMessage(message.invocation_id, **reply)),
                )

        hub._transport.send = transport_send
        client._client = hub
        client._connected = True
        return hub

    return connect
