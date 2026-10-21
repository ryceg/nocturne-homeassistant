"""Tests for GlucoseCoordinator and DeviceCoordinator."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError, ClientResponseError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from nocturne_py import ApiException, ApsSnapshot, PumpSnapshot, SensorGlucose, UploaderSnapshot
from nocturne_py.models import DailySummaryDay, DeviceAgeInfo, ProfileSummary
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nocturne.const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
    DOMAIN,
)
from custom_components.nocturne.coordinator import (
    DeviceCoordinator,
    GlucoseCoordinator,
)

TOKEN_URL = "https://nocturne.example.com/oauth/token"


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

    async def test_glucose_push_requests_refresh(self, mock_hass, mock_client):
        """A pushed glucose_reading triggers a poll instead of replacing the data."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord.async_request_refresh = AsyncMock()

        await coord.async_handle_glucose_push()

        coord.async_request_refresh.assert_awaited_once()

    async def test_polls_after_glucose_push(self, mock_hass, mock_client):
        """Polling is never paused by SignalR: APS state is not pushed."""
        coord = GlucoseCoordinator(mock_hass, mock_client)
        coord.async_request_refresh = AsyncMock()
        await coord.async_handle_glucose_push()

        mock_client.get_latest_glucose.return_value = SensorGlucose(mgdl=120)
        mock_client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=1.5)

        result = await coord._async_update_data()

        mock_client.get_latest_glucose.assert_called_once()
        assert result["glucose"].mgdl == 120


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


def _refresh_error(status: int) -> ClientResponseError:
    return ClientResponseError(MagicMock(), (), status=status)


@pytest.mark.parametrize("coordinator_cls", [GlucoseCoordinator, DeviceCoordinator])
class TestRefusedTokenRefresh:
    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_refused_refresh_raises_auth_failed(
        self, mock_hass, mock_client, coordinator_cls, status
    ):
        mock_client.get_latest_glucose.side_effect = _refresh_error(status)
        mock_client.get_latest_pump_snapshot.side_effect = _refresh_error(status)
        coord = coordinator_cls(mock_hass, mock_client)
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    @pytest.mark.parametrize(
        "error",
        [_refresh_error(429), _refresh_error(500), _refresh_error(503), ClientConnectionError()],
    )
    async def test_server_and_network_errors_stay_retryable(
        self, mock_hass, mock_client, coordinator_cls, error
    ):
        mock_client.get_latest_glucose.side_effect = error
        mock_client.get_latest_pump_snapshot.side_effect = error
        coord = coordinator_cls(mock_hass, mock_client)
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_refused_refresh_at_setup_starts_reauth(
    hass: HomeAssistant, enable_custom_integrations: None, aioclient_mock: AiohttpClientMocker
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="https://nocturne.example.com",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "old-access",
                "refresh_token": "revoked",
                "expires_at": time.time() - 10,
                "expires_in": 3600,
            },
            CONF_INSTANCE_URL: "https://nocturne.example.com",
            CONF_CLIENT_ID: "client-id",
            CONF_AUTHORIZE_URL: "https://nocturne.example.com/oauth/authorize",
            CONF_TOKEN_URL: TOKEN_URL,
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert flows[0]["context"]["entry_id"] == entry.entry_id
