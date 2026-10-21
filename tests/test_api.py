"""Tests for NocturneApiClient (SDK-based, mocking nocturne_py API classes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nocturne_py import (
    ApiException,
    ApsSnapshot,
    PumpSnapshot,
    SensorGlucose,
    UploaderSnapshot,
)
from nocturne_py.models import DailySummaryDay, DailySummaryResponse, ProfileSummary

from custom_components.nocturne.api import NocturneApiClient


@pytest.fixture
def client() -> NocturneApiClient:
    return NocturneApiClient("https://example.com", access_token="test-token")


class TestValidateConnection:
    async def test_success(self, client):
        with patch(
            "custom_components.nocturne.api.StatusApi"
        ) as mock_api_cls:
            mock_api = MagicMock()
            mock_api.status_get_status.return_value = MagicMock(status="ok")
            mock_api_cls.return_value.__enter__ = MagicMock(return_value=mock_api)
            mock_api_cls.return_value.__exit__ = MagicMock(return_value=False)
            # The SDK is called inside asyncio.to_thread, so we mock at the API class level
            with patch("custom_components.nocturne.api.ApiClient") as mock_client_cls:
                ctx = MagicMock()
                ctx.__enter__ = MagicMock(return_value=ctx)
                ctx.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = ctx
                mock_api_cls.return_value = mock_api
                assert await client.validate_connection() is True

    async def test_exception_returns_false(self, client):
        with patch(
            "custom_components.nocturne.api.ApiClient"
        ) as mock_client_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(side_effect=Exception("Connection refused"))
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx
            assert await client.validate_connection() is False


class TestEnsureToken:
    async def test_uses_static_token(self, client):
        await client._ensure_token()
        assert client._config.access_token == "test-token"

    async def test_uses_oauth_session(self):
        oauth_session = AsyncMock()
        oauth_session.token = {"access_token": "oauth-token"}
        client = NocturneApiClient(
            "https://example.com", oauth_session=oauth_session
        )
        await client._ensure_token()
        assert client._config.access_token == "oauth-token"
        oauth_session.async_ensure_token_valid.assert_awaited_once()


class TestSdkSendsBearerToken:
    """Exercises the real SDK request path: the mocks above cannot see auth headers."""

    async def test_authorization_header_reaches_the_wire(self, client):
        captured: dict = {}

        def _capture(self, method, url, headers=None, body=None, post_params=None,
                     _request_timeout=None):
            captured["headers"] = headers
            raise RuntimeError("stop before network")

        with patch("nocturne_py.rest.RESTClientObject.request", _capture):
            with pytest.raises(RuntimeError):
                await client.get_latest_glucose()

        assert captured["headers"]["Authorization"] == "Bearer test-token"


class TestGetDailySummaryScope:
    async def test_forbidden_returns_none(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.side_effect = (
                ApiException(status=403, reason="Forbidden")
            )
            assert await client.get_daily_summary() is None

    async def test_other_errors_propagate(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.side_effect = (
                ApiException(status=500, reason="Server Error")
            )
            with pytest.raises(ApiException):
                await client.get_daily_summary()
