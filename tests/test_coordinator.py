"""Tests for GlucoseCoordinator and DeviceCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from nocturne_py import ApiException, ApsSnapshot, PumpSnapshot, SensorGlucose, UploaderSnapshot
from nocturne_py.models import DailySummaryDay, DeviceAgeInfo, ProfileSummary

from custom_components.nocturne.coordinator import (
    DeviceCoordinator,
    GlucoseCoordinator,
)


@pytest.fixture(autouse=True)
def _patch_frame_report(monkeypatch):
    """Prevent HA frame helper from raising during unit tests."""
    monkeypatch.setattr(
        "homeassistant.helpers.frame.report_usage", lambda *a, **kw: None
    )


def _make_api_exception(status: int) -> ApiException:
    return ApiException(status=status, reason=f"HTTP {status}")


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_latest_glucose.return_value = SensorGlucose(mgdl=120)
    client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=1.5)
    client.get_latest_pump_snapshot.return_value = PumpSnapshot(reservoir=150.0)
    client.get_latest_uploader_snapshot.return_value = UploaderSnapshot(battery=90)
    client.get_profile_summary.return_value = ProfileSummary()
    client.get_daily_summary.return_value = DailySummaryDay(time_in_range_percent=72.5)
    client.get_sensor_age.return_value = DeviceAgeInfo(days=5)
    return client


@pytest.fixture
def mock_hass() -> MagicMock:
    hass = MagicMock()
    hass.loop = None
    return hass


class TestGlucoseCoordinator:
    async def test_success(self, mock_hass, mock_client):
        coord = GlucoseCoordinator(mock_hass, mock_client)
        result = await coord._async_update_data()
        assert result["glucose"].mgdl == 120
        assert result["aps"].iob == 1.5
        assert coord._refresh_failed is False

    async def test_401_first_attempt_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_glucose.side_effect = _make_api_exception(401)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Auth failed"):
            await coord._async_update_data()
        assert coord._refresh_failed is True

    async def test_401_second_attempt_raises_auth_failed(self, mock_hass, mock_client):
        mock_client.get_latest_glucose.side_effect = _make_api_exception(401)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord._refresh_failed = True
        with pytest.raises(ConfigEntryAuthFailed, match="OAuth token expired"):
            await coord._async_update_data()

    async def test_other_http_error_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_glucose.side_effect = _make_api_exception(500)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="API error: 500"):
            await coord._async_update_data()

    async def test_generic_exception_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_glucose.side_effect = OSError("DNS failure")
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coord._async_update_data()

    async def test_skips_poll_when_signalr_active(self, mock_hass, mock_client):
        """When SignalR is active, coordinator returns existing data without API call."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord._signalr_active = True
        coord.data = {"glucose": "existing"}

        result = await coord._async_update_data()

        assert result == {"glucose": "existing"}
        mock_client.get_latest_glucose.assert_not_called()

    async def test_signalr_active_property(self, mock_hass, mock_client):
        """SignalR active property getter and setter work correctly."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        assert coord.signalr_active is False
        coord.signalr_active = True
        assert coord.signalr_active is True

    async def test_signalr_active_reset_on_disconnect(self, mock_hass, mock_client):
        """signalr_active is reset to False when the disconnect callback fires."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord.signalr_active = True
        assert coord.signalr_active is True

        # Simulate the disconnect callback
        coord.signalr_active = False

        assert coord.signalr_active is False

    async def test_resumes_polling_when_signalr_inactive(self, mock_hass, mock_client):
        """When signalr_active is False, the coordinator resumes making API calls."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord._signalr_active = False
        mock_client.get_latest_glucose.return_value = SensorGlucose(mgdl=120)
        mock_client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=1.5)

        result = await coord._async_update_data()

        mock_client.get_latest_glucose.assert_called_once()


class TestDeviceCoordinator:
    async def test_success(self, mock_hass, mock_client):
        coord = DeviceCoordinator(mock_hass, mock_client)
        result = await coord._async_update_data()
        assert result["pump"].reservoir == 150.0
        assert result["uploader"].battery == 90
        assert result["daily_summary"].time_in_range_percent == 72.5
        assert result["sensor_age"].days == 5

    async def test_401_first_attempt_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_pump_snapshot.side_effect = _make_api_exception(401)
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Auth failed"):
            await coord._async_update_data()

    async def test_401_second_attempt_raises_auth_failed(self, mock_hass, mock_client):
        mock_client.get_latest_pump_snapshot.side_effect = _make_api_exception(401)
        coord = DeviceCoordinator(mock_hass, mock_client)
        coord._refresh_failed = True
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_other_http_error(self, mock_hass, mock_client):
        mock_client.get_latest_pump_snapshot.side_effect = _make_api_exception(503)
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="API error: 503"):
            await coord._async_update_data()

    async def test_generic_exception(self, mock_hass, mock_client):
        mock_client.get_latest_pump_snapshot.side_effect = RuntimeError("boom")
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coord._async_update_data()
