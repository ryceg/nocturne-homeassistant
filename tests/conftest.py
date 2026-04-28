"""Shared fixtures for Nocturne tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nocturne.const import DOMAIN


@pytest.fixture
def glucose_data() -> dict:
    """Realistic glucose coordinator data."""
    return {
        "entry": {"sgv": 120, "direction": "Flat", "date": 1700000000000},
        "device_status": {
            "openaps": {
                "iob": {"iob": 1.5, "activity": 0.02},
                "enacted": {
                    "COB": 20,
                    "rate": 0.8,
                    "predBGs": {"IOB": [120, 115, 110]},
                },
            }
        },
    }


@pytest.fixture
def device_data() -> dict:
    """Realistic device coordinator data."""
    return {
        "device_status": {
            "pump": {"reservoir": 150.0, "battery": {"percent": 80}},
            "uploader": {"battery": 90, "signal": -70, "sensorAge": 5},
        },
        "profile": {"name": "Default"},
        "report": {"timeInRange": 72.5},
    }


@pytest.fixture
def mock_api_client() -> AsyncMock:
    """AsyncMock of NocturneApiClient with sensible defaults."""
    client = AsyncMock()
    client.validate_connection.return_value = True
    client.get_latest_entry.return_value = {"sgv": 120, "direction": "Flat"}
    client.get_latest_device_status.return_value = {
        "openaps": {"iob": {"iob": 1.5}},
        "pump": {"reservoir": 150.0, "battery": {"percent": 80}},
        "uploader": {"battery": 90},
    }
    client.get_active_profile.return_value = {"name": "Default"}
    client.get_report_summary.return_value = {"timeInRange": 72.5}
    client.post_entry.return_value = True
    client.post_treatment.return_value = True
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
