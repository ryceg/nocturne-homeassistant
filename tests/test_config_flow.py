"""Tests for the Nocturne OAuth2 config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nocturne.config_flow import NocturneOAuth2FlowHandler
from custom_components.nocturne.const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
)


class TestAsyncStepUser:
    @pytest.fixture
    def flow(self):
        flow = NocturneOAuth2FlowHandler()
        flow.hass = MagicMock()
        return flow

    @patch(
        "custom_components.nocturne.config_flow.async_get_clientsession",
        return_value=AsyncMock(),
    )
    @patch(
        "custom_components.nocturne.config_flow.NocturneApiClient",
    )
    async def test_shows_form_when_no_input(self, mock_client_cls, mock_session, flow):
        result = await flow.async_step_user(None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    @patch(
        "custom_components.nocturne.config_flow.async_get_clientsession",
        return_value=AsyncMock(),
    )
    @patch("custom_components.nocturne.config_flow.NocturneApiClient")
    async def test_connection_failure_shows_error(self, mock_client_cls, mock_session, flow):
        mock_client_cls.return_value.validate_connection = AsyncMock(return_value=False)

        result = await flow.async_step_user({CONF_INSTANCE_URL: "https://bad.example.com"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @patch(
        "custom_components.nocturne.config_flow.async_get_clientsession",
        return_value=AsyncMock(),
    )
    @patch("custom_components.nocturne.config_flow.NocturneApiClient")
    async def test_discovery_failure_shows_error(self, mock_client_cls, mock_session, flow):
        mock_client_cls.return_value.validate_connection = AsyncMock(return_value=True)
        flow._fetch_discovery = AsyncMock(return_value=None)

        result = await flow.async_step_user({CONF_INSTANCE_URL: "https://example.com"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @patch(
        "custom_components.nocturne.config_flow.async_get_clientsession",
        return_value=AsyncMock(),
    )
    @patch("custom_components.nocturne.config_flow.NocturneApiClient")
    async def test_dcr_failure_shows_error(self, mock_client_cls, mock_session, flow):
        mock_client_cls.return_value.validate_connection = AsyncMock(return_value=True)
        flow._fetch_discovery = AsyncMock(return_value={
            "authorization_endpoint": "https://example.com/oauth/authorize",
            "token_endpoint": "https://example.com/oauth/token",
        })
        flow._register_client = AsyncMock(return_value=None)
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        result = await flow.async_step_user({CONF_INSTANCE_URL: "https://example.com"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"


class TestAsyncStepReauth:
    async def test_stores_instance_url(self):
        flow = NocturneOAuth2FlowHandler()
        flow.hass = MagicMock()
        flow.async_step_auth = AsyncMock(return_value={"type": "form"})

        await flow.async_step_reauth({CONF_INSTANCE_URL: "https://example.com"})

        assert flow._instance_url == "https://example.com"
        flow.async_step_auth.assert_awaited_once()


class TestAsyncOAuthCreateEntry:
    async def test_persists_all_oauth_data(self):
        flow = NocturneOAuth2FlowHandler()
        flow.hass = MagicMock()
        flow._instance_url = "https://example.com"
        flow._client_id = "test-client-id"
        flow._authorize_url = "https://example.com/oauth/authorize"
        flow._token_url = "https://example.com/oauth/token"
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        await flow.async_oauth_create_entry({"token": "abc"})

        create_call = flow.async_create_entry.call_args
        data = create_call.kwargs["data"]
        assert data[CONF_INSTANCE_URL] == "https://example.com"
        assert data[CONF_CLIENT_ID] == "test-client-id"
        assert data[CONF_AUTHORIZE_URL] == "https://example.com/oauth/authorize"
        assert data[CONF_TOKEN_URL] == "https://example.com/oauth/token"
