"""Tests for NocturneApiClient (aiohttp-level mocks)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponseError

from custom_components.nocturne.api import NocturneApiClient


def _mock_response(status: int = 200, json_data=None) -> AsyncMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=status,
            message=f"HTTP {status}",
        )
    return resp


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(session: AsyncMock) -> NocturneApiClient:
    return NocturneApiClient(session, "https://example.com", access_token="test-token")


class TestValidateConnection:
    async def test_success(self, client, session):
        session.get.return_value = _mock_response(200)
        assert await client.validate_connection() is True

    async def test_non_200(self, client, session):
        session.get.return_value = _mock_response(404)
        assert await client.validate_connection() is False

    async def test_network_error(self, client, session):
        session.get.side_effect = Exception("Connection refused")
        assert await client.validate_connection() is False


class TestGetLatestEntry:
    async def test_returns_first_entry(self, client, session):
        session.get.return_value = _mock_response(200, [{"sgv": 120}])
        result = await client.get_latest_entry()
        assert result == {"sgv": 120}

    async def test_empty_list_returns_none(self, client, session):
        session.get.return_value = _mock_response(200, [])
        result = await client.get_latest_entry()
        assert result is None

    async def test_client_error_raises(self, client, session):
        session.get.return_value = _mock_response(401)
        with pytest.raises(ClientResponseError):
            await client.get_latest_entry()


class TestGetLatestDeviceStatus:
    async def test_returns_first_status(self, client, session):
        session.get.return_value = _mock_response(200, [{"pump": {}}])
        result = await client.get_latest_device_status()
        assert result == {"pump": {}}

    async def test_empty_list_returns_none(self, client, session):
        session.get.return_value = _mock_response(200, [])
        assert await client.get_latest_device_status() is None


class TestGetActiveProfile:
    async def test_returns_profile(self, client, session):
        session.get.return_value = _mock_response(200, {"name": "Default"})
        result = await client.get_active_profile()
        assert result == {"name": "Default"}


class TestGetReportSummary:
    async def test_returns_report(self, client, session):
        session.get.return_value = _mock_response(200, {"timeInRange": 72.5})
        result = await client.get_report_summary()
        assert result == {"timeInRange": 72.5}


class TestPostEntry:
    async def test_success(self, client, session):
        session.post.return_value = _mock_response(200)
        assert await client.post_entry({"sgv": 120}) is True

    async def test_client_error_raises(self, client, session):
        session.post.return_value = _mock_response(401)
        with pytest.raises(ClientResponseError):
            await client.post_entry({"sgv": 120})

    async def test_generic_error_returns_false(self, client, session):
        session.post.side_effect = OSError("Network error")
        assert await client.post_entry({"sgv": 120}) is False


class TestPostTreatment:
    async def test_success(self, client, session):
        session.post.return_value = _mock_response(200)
        assert await client.post_treatment({"carbs": 30}) is True

    async def test_client_error_raises(self, client, session):
        session.post.return_value = _mock_response(500)
        with pytest.raises(ClientResponseError):
            await client.post_treatment({"carbs": 30})

    async def test_generic_error_returns_false(self, client, session):
        session.post.side_effect = OSError("Network error")
        assert await client.post_treatment({"carbs": 30}) is False


class TestHeaders:
    async def test_uses_access_token(self, client):
        headers = await client._headers
        assert headers == {"Authorization": "Bearer test-token"}

    async def test_uses_oauth_session_when_available(self, session):
        oauth_session = AsyncMock()
        oauth_session.token = {"access_token": "oauth-token"}
        client = NocturneApiClient(
            session, "https://example.com", oauth_session=oauth_session
        )
        headers = await client._headers
        assert headers == {"Authorization": "Bearer oauth-token"}
        oauth_session.async_ensure_token_valid.assert_awaited_once()


class TestGetGenericErrors:
    async def test_non_client_error_returns_none(self, client, session):
        session.get.side_effect = OSError("DNS failure")
        result = await client.get_active_profile()
        assert result is None
