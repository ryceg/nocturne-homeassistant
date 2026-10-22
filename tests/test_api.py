"""Tests for NocturneApiClient (SDK-based, mocking nocturne_py API classes)."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
            ctx.__enter__ = MagicMock(side_effect=ConnectionRefusedError("Connection refused"))
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


STAMP = datetime(2026, 10, 22, 1, 2, 3, tzinfo=UTC)


class TestCreateGlucose:
    async def test_meter_reading_goes_to_the_meter_endpoint(self, client):
        with (
            patch("custom_components.nocturne.api.MeterGlucoseApi") as meter_cls,
            patch("custom_components.nocturne.api.SensorGlucoseApi") as sensor_cls,
        ):
            await client.create_meter_glucose(mgdl=110.0, timestamp=STAMP, data_source="ha")

        request = meter_cls.return_value.meter_glucose_create.call_args[0][0]
        assert (request.mgdl, request.timestamp, request.data_source) == (110.0, STAMP, "ha")
        sensor_cls.return_value.sensor_glucose_create.assert_not_called()

    async def test_sensor_reading_goes_to_the_sensor_endpoint(self, client):
        with (
            patch("custom_components.nocturne.api.MeterGlucoseApi") as meter_cls,
            patch("custom_components.nocturne.api.SensorGlucoseApi") as sensor_cls,
        ):
            await client.create_glucose(mgdl=110.0, timestamp=STAMP, data_source="ha")

        request = sensor_cls.return_value.sensor_glucose_create.call_args[0][0]
        assert (request.mgdl, request.timestamp, request.data_source) == (110.0, STAMP, "ha")
        meter_cls.return_value.meter_glucose_create.assert_not_called()


class TestGetDailySummaryScope:
    async def test_forbidden_returns_none(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.side_effect = (
                ApiException(status=403, reason="Forbidden")
            )
            assert await client.get_daily_summary(TODAY) is None

    async def test_other_errors_propagate(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.side_effect = (
                ApiException(status=500, reason="Server Error")
            )
            with pytest.raises(ApiException):
                await client.get_daily_summary(TODAY)


TODAY = date(2026, 10, 22)


def _summary(*days: tuple[str, float]) -> DailySummaryResponse:
    return DailySummaryResponse(
        days=[DailySummaryDay(var_date=d, time_in_range_percent=t) for d, t in days]
    )


class TestGetDailySummaryToday:
    async def test_selects_today_when_it_is_not_the_last_day(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.return_value = (
                _summary(("2026-10-21", 60.0), ("2026-10-22", 80.0), ("2026-10-23", 10.0))
            )
            day = await client.get_daily_summary(TODAY)
        assert day.time_in_range_percent == 80.0

    async def test_none_when_latest_day_is_not_today(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.return_value = (
                _summary(("2026-10-20", 60.0), ("2026-10-21", 70.0))
            )
            assert await client.get_daily_summary(TODAY) is None

    async def test_requests_the_year_of_today(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            api = mock_api_cls.return_value.data_overview_get_daily_summary
            api.return_value = _summary()
            await client.get_daily_summary(date(2027, 1, 1))
        api.assert_called_once_with(year=2027)

    async def test_found_day_is_cached(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            api = mock_api_cls.return_value.data_overview_get_daily_summary
            api.return_value = _summary(("2026-10-22", 80.0))
            await client.get_daily_summary(TODAY)
            await client.get_daily_summary(TODAY)
        api.assert_called_once()

    async def test_cache_expires_after_thirty_minutes(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            api = mock_api_cls.return_value.data_overview_get_daily_summary
            api.return_value = _summary(("2026-10-22", 80.0))
            with patch("custom_components.nocturne.api.time.monotonic") as clock:
                clock.return_value = 1000.0
                await client.get_daily_summary(TODAY)
                clock.return_value = 1000.0 + 31 * 60
                await client.get_daily_summary(TODAY)
        assert api.call_count == 2

    async def test_cache_does_not_cross_days(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            api = mock_api_cls.return_value.data_overview_get_daily_summary
            api.return_value = _summary(("2026-10-22", 80.0), ("2026-10-23", 50.0))
            await client.get_daily_summary(TODAY)
            day = await client.get_daily_summary(date(2026, 10, 23))
        assert day.time_in_range_percent == 50.0

    async def test_missing_day_is_cached_too(self, client):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            api = mock_api_cls.return_value.data_overview_get_daily_summary
            api.return_value = _summary()
            await client.get_daily_summary(TODAY)
            api.return_value = _summary(("2026-10-22", 80.0))
            day = await client.get_daily_summary(TODAY)
        assert day is None
        assert api.call_count == 1

    async def test_forbidden_warns_once(self, client, caplog):
        with patch("custom_components.nocturne.api.DataOverviewApi") as mock_api_cls:
            mock_api_cls.return_value.data_overview_get_daily_summary.side_effect = (
                ApiException(status=403, reason="Forbidden")
            )
            await client.get_daily_summary(TODAY)
            await client.get_daily_summary(TODAY)
        assert caplog.text.count("reports.read") == 1
