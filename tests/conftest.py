"""Shared fixtures for Nocturne tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from nocturne_sdk import (
    ApsSnapshot,
    DeviceAgeInfo,
    GlucoseDirection,
    PumpSnapshot,
    SensorGlucose,
    UploaderSnapshot,
)
from nocturne_sdk.models import DailySummaryDay, ProfileSummary

from custom_components.nocturne.const import DOMAIN


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
    client.create_treatment.return_value = MagicMock()
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
