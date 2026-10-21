"""Tests for the Nocturne OAuth2 config flow."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nocturne.config_flow import (
    NocturneOAuth2FlowHandler,
    NocturneOAuth2Implementation,
    NocturneOptionsFlowHandler,
)
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


def _entry(unique_id: str = INSTANCE_URL) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Nocturne",
        unique_id=unique_id,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": time.time() - 10,
                "expires_in": 3600,
            },
            CONF_INSTANCE_URL: INSTANCE_URL,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_AUTHORIZE_URL: AUTHORIZE_URL,
            CONF_TOKEN_URL: TOKEN_URL,
        },
    )


@pytest.fixture
async def reauth_hass(hass: HomeAssistant, enable_custom_integrations: None) -> HomeAssistant:
    hass.config.external_url = "https://ha.example.com"
    assert await async_setup_component(hass, "auth", {})
    return hass


async def _authorise(hass, hass_client_no_auth, aioclient_mock, entry, token_response):
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    authorise = urlparse(result["url"])
    query = parse_qs(authorise.query)
    assert f"{authorise.scheme}://{authorise.netloc}{authorise.path}" == AUTHORIZE_URL
    assert query["client_id"] == [CLIENT_ID]

    client = await hass_client_no_auth()
    resp = await client.get(
        f"/auth/external/callback?code=auth-code&state={query['state'][0]}"
    )
    assert resp.status == 200

    aioclient_mock.post(TOKEN_URL, json=token_response)
    with patch("custom_components.nocturne.async_setup_entry", return_value=True) as setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        await hass.async_block_till_done()
    return result, setup


NEW_TOKEN = {
    "access_token": "new-access",
    "refresh_token": "new-refresh",
    "expires_in": 3600,
    "token_type": "Bearer",
}


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


class TestReauth:
    async def test_updates_existing_entry_with_its_own_client(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = _entry()
        entry.add_to_hass(reauth_hass)

        result, setup = await _authorise(
            reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert reauth_hass.config_entries.async_entries(DOMAIN) == [entry]
        assert entry.data["token"]["access_token"] == "new-access"
        assert entry.data[CONF_CLIENT_ID] == CLIENT_ID
        assert entry.data[CONF_AUTHORIZE_URL] == AUTHORIZE_URL
        assert entry.data[CONF_TOKEN_URL] == TOKEN_URL
        assert entry.data[CONF_INSTANCE_URL] == INSTANCE_URL
        token_request = aioclient_mock.mock_calls[-1][2]
        assert token_request["client_id"] == CLIENT_ID
        assert token_request["code"] == "auth-code"
        setup.assert_called_once()

    async def test_aborts_when_unique_id_disagrees_with_stored_instance(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = _entry(unique_id="https://other.example.com")
        entry.add_to_hass(reauth_hass)

        result, setup = await _authorise(
            reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_instance"
        assert entry.data["token"]["access_token"] == "old-access"
        setup.assert_not_called()


class TestRefreshToken:
    async def test_keeps_refresh_token_when_response_omits_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ):
        aioclient_mock.post(TOKEN_URL, json={"access_token": "new-access", "expires_in": 60})
        impl = NocturneOAuth2Implementation.from_entry_data(hass, _entry().data)

        token = await impl.async_refresh_token(
            {"access_token": "old-access", "refresh_token": "old-refresh", "expires_in": 60}
        )

        assert token["access_token"] == "new-access"
        assert token["refresh_token"] == "old-refresh"


class TestAsyncOAuthCreateEntry:
    async def test_persists_all_oauth_data(self):
        flow = NocturneOAuth2FlowHandler()
        flow.hass = MagicMock()
        flow.context = {"source": SOURCE_USER}
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


class TestOptionsFlow:
    def test_get_options_flow_does_not_assign_config_entry(self):
        handler = NocturneOAuth2FlowHandler.async_get_options_flow(MagicMock())

        assert isinstance(handler, NocturneOptionsFlowHandler)
