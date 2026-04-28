"""Tests for GlucoseCoordinator and DeviceCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponseError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nocturne.coordinator import (
    DeviceCoordinator,
    GlucoseCoordinator,
)


def _make_client_response_error(status: int) -> ClientResponseError:
    return ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message=f"HTTP {status}"
    )


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_latest_entry.return_value = {"sgv": 120}
    client.get_latest_device_status.return_value = {"pump": {}}
    client.get_active_profile.return_value = {"name": "Default"}
    client.get_report_summary.return_value = {"timeInRange": 72.5}
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
        assert result["entry"] == {"sgv": 120}
        assert result["device_status"] == {"pump": {}}
        assert coord._refresh_failed is False

    async def test_401_first_attempt_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_entry.side_effect = _make_client_response_error(401)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Auth failed"):
            await coord._async_update_data()
        assert coord._refresh_failed is True

    async def test_401_second_attempt_raises_auth_failed(self, mock_hass, mock_client):
        mock_client.get_latest_entry.side_effect = _make_client_response_error(401)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord._refresh_failed = True
        with pytest.raises(ConfigEntryAuthFailed, match="OAuth token expired"):
            await coord._async_update_data()

    async def test_other_http_error_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_entry.side_effect = _make_client_response_error(500)
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="API error: 500"):
            await coord._async_update_data()

    async def test_generic_exception_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_entry.side_effect = OSError("DNS failure")
        coord = GlucoseCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coord._async_update_data()


class TestDeviceCoordinator:
    async def test_success(self, mock_hass, mock_client):
        coord = DeviceCoordinator(mock_hass, mock_client)
        result = await coord._async_update_data()
        assert result["device_status"] == {"pump": {}}
        assert result["profile"] == {"name": "Default"}
        assert result["report"] == {"timeInRange": 72.5}

    async def test_401_first_attempt_raises_update_failed(self, mock_hass, mock_client):
        mock_client.get_latest_device_status.side_effect = _make_client_response_error(401)
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Auth failed"):
            await coord._async_update_data()

    async def test_401_second_attempt_raises_auth_failed(self, mock_hass, mock_client):
        mock_client.get_latest_device_status.side_effect = _make_client_response_error(401)
        coord = DeviceCoordinator(mock_hass, mock_client)
        coord._refresh_failed = True
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    async def test_other_http_error(self, mock_hass, mock_client):
        mock_client.get_latest_device_status.side_effect = _make_client_response_error(503)
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="API error: 503"):
            await coord._async_update_data()

    async def test_generic_exception(self, mock_hass, mock_client):
        mock_client.get_latest_device_status.side_effect = RuntimeError("boom")
        coord = DeviceCoordinator(mock_hass, mock_client)
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coord._async_update_data()
