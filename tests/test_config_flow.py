"""Tests for the Nocturne OAuth2 config flow."""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import MY_AUTH_CALLBACK_PATH
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
    CONF_REDIRECT_URI,
    CONF_REQUESTED_SCOPES,
    CONF_TOKEN_URL,
    DEFAULT_SCOPES,
    DOMAIN,
)

from .conftest import AUTHORIZE_URL, CLIENT_ID, INSTANCE_URL, TOKEN_URL, make_entry


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


async def _start_user_flow(hass, aioclient_mock, instance_url, client_id):
    aioclient_mock.get(
        f"{instance_url}/.well-known/openid-configuration",
        json={
            "authorization_endpoint": f"{instance_url}/oauth/authorize",
            "token_endpoint": f"{instance_url}/oauth/token",
            "registration_endpoint": f"{instance_url}/oauth/register",
        },
    )
    aioclient_mock.post(f"{instance_url}/oauth/register", json={"client_id": client_id})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch("custom_components.nocturne.config_flow.NocturneApiClient") as api:
        api.return_value.validate_connection = AsyncMock(return_value=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_INSTANCE_URL: instance_url}
        )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    query = parse_qs(urlparse(result["url"]).query)
    assert query["client_id"] == [client_id]
    assert query["scope"] == [" ".join(DEFAULT_SCOPES)]
    assert query["code_challenge_method"] == ["S256"]
    return result["flow_id"], query


async def _finish_user_flow(hass, hass_client_no_auth, aioclient_mock, flow_id, query, instance_url):
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=auth-code&state={query['state'][0]}")
    assert resp.status == 200
    aioclient_mock.post(f"{instance_url}/oauth/token", json=NEW_TOKEN)
    with patch("custom_components.nocturne.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(flow_id)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result["result"], aioclient_mock.mock_calls[-1][2]


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class TestUserFlow:
    async def test_flows_in_progress_keep_their_own_client_and_verifier(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        first_url, second_url = "https://first.example.com", "https://second.example.com"
        first_flow, first_query = await _start_user_flow(
            reauth_hass, aioclient_mock, first_url, "first-client"
        )
        second_flow, second_query = await _start_user_flow(
            reauth_hass, aioclient_mock, second_url, "second-client"
        )

        first_entry, first_request = await _finish_user_flow(
            reauth_hass, hass_client_no_auth, aioclient_mock, first_flow, first_query, first_url
        )
        second_entry, second_request = await _finish_user_flow(
            reauth_hass, hass_client_no_auth, aioclient_mock, second_flow, second_query, second_url
        )

        assert first_request["client_id"] == "first-client"
        assert _challenge(first_request["code_verifier"]) == first_query["code_challenge"][0]
        assert second_request["client_id"] == "second-client"
        assert _challenge(second_request["code_verifier"]) == second_query["code_challenge"][0]
        assert first_request["code_verifier"] != second_request["code_verifier"]
        assert "client_secret" not in first_request
        assert first_entry.data[CONF_CLIENT_ID] == "first-client"
        assert first_entry.data[CONF_TOKEN_URL] == f"{first_url}/oauth/token"
        assert second_entry.data[CONF_CLIENT_ID] == "second-client"
        assert second_entry.data[CONF_TOKEN_URL] == f"{second_url}/oauth/token"


    async def test_uses_the_registered_redirect_uri_throughout(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        redirect_uri = "https://ha.example.com/auth/external/callback"
        flow_id, query = await _start_user_flow(
            reauth_hass, aioclient_mock, INSTANCE_URL, CLIENT_ID
        )
        registration = next(
            data for _, url, data, _ in aioclient_mock.mock_calls
            if str(url) == f"{INSTANCE_URL}/oauth/register"
        )

        _, token_request = await _finish_user_flow(
            reauth_hass, hass_client_no_auth, aioclient_mock, flow_id, query, INSTANCE_URL
        )

        assert registration["redirect_uris"] == [redirect_uri]
        assert query["redirect_uri"] == [redirect_uri]
        assert token_request["redirect_uri"] == redirect_uri

    async def test_registers_with_my_home_assistant_when_there_is_no_external_url(
        self, my_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        flow_id, query = await _start_user_flow(my_hass, aioclient_mock, INSTANCE_URL, CLIENT_ID)
        registration = next(
            data for _, url, data, _ in aioclient_mock.mock_calls
            if str(url) == f"{INSTANCE_URL}/oauth/register"
        )

        entry, token_request = await _finish_user_flow(
            my_hass, hass_client_no_auth, aioclient_mock, flow_id, query, INSTANCE_URL
        )

        assert registration["redirect_uris"] == [MY_AUTH_CALLBACK_PATH]
        assert query["redirect_uri"] == [MY_AUTH_CALLBACK_PATH]
        assert token_request["redirect_uri"] == MY_AUTH_CALLBACK_PATH
        assert entry.data[CONF_REDIRECT_URI] == MY_AUTH_CALLBACK_PATH

    async def test_prefers_the_external_url_over_my_home_assistant(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        reauth_hass.config.components.add("my")
        external_callback = "https://ha.example.com/auth/external/callback"
        flow_id, query = await _start_user_flow(
            reauth_hass, aioclient_mock, INSTANCE_URL, CLIENT_ID
        )
        registration = next(
            data for _, url, data, _ in aioclient_mock.mock_calls
            if str(url) == f"{INSTANCE_URL}/oauth/register"
        )

        entry, token_request = await _finish_user_flow(
            reauth_hass, hass_client_no_auth, aioclient_mock, flow_id, query, INSTANCE_URL
        )

        assert registration["redirect_uris"] == [external_callback]
        assert query["redirect_uri"] == [external_callback]
        assert token_request["redirect_uri"] == external_callback
        assert entry.data[CONF_REDIRECT_URI] == external_callback

    async def test_stores_the_external_url_callback_it_registered(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        flow_id, query = await _start_user_flow(
            reauth_hass, aioclient_mock, INSTANCE_URL, CLIENT_ID
        )

        entry, _ = await _finish_user_flow(
            reauth_hass, hass_client_no_auth, aioclient_mock, flow_id, query, INSTANCE_URL
        )

        assert entry.data[CONF_REDIRECT_URI] == "https://ha.example.com/auth/external/callback"

    async def test_asks_for_an_external_url_when_there_is_no_way_to_redirect(
        self, hass: HomeAssistant, enable_custom_integrations: None,
        aioclient_mock: AiohttpClientMocker,
    ):
        hass.config.external_url = None
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        with patch("custom_components.nocturne.config_flow.NocturneApiClient") as api:
            api.return_value.validate_connection = AsyncMock(return_value=True)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_INSTANCE_URL: INSTANCE_URL}
            )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "no_redirect_url"}
        assert aioclient_mock.call_count == 0


class TestAsyncStepUser:
    @pytest.fixture
    def flow(self):
        flow = NocturneOAuth2FlowHandler()
        flow.hass = MagicMock()
        with patch(
            "custom_components.nocturne.config_flow.registration_redirect_uri",
            return_value="https://ha.example.com/auth/external/callback",
        ):
            yield flow

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
        entry = make_entry()
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

    async def test_stores_the_scopes_it_requested(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = make_entry()
        entry.add_to_hass(reauth_hass)

        await _authorise(
            reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN
        )

        assert entry.data[CONF_REQUESTED_SCOPES] == DEFAULT_SCOPES

    async def test_aborts_when_unique_id_disagrees_with_stored_instance(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = make_entry(unique_id="https://other.example.com")
        entry.add_to_hass(reauth_hass)

        result, setup = await _authorise(
            reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_instance"
        assert entry.data["token"]["access_token"] == "old-access"
        setup.assert_not_called()


class TestRedirectUri:
    async def test_reauth_uses_the_stored_redirect_uri_even_if_the_ha_url_changed(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = make_entry(**{CONF_REDIRECT_URI: MY_AUTH_CALLBACK_PATH})
        entry.add_to_hass(reauth_hass)

        await _authorise(reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN)

        assert aioclient_mock.mock_calls[-1][2]["redirect_uri"] == MY_AUTH_CALLBACK_PATH
        assert entry.data[CONF_REDIRECT_URI] == MY_AUTH_CALLBACK_PATH

    async def test_entry_without_one_registered_the_external_url_callback(
        self, reauth_hass, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
    ):
        entry = make_entry()
        entry.add_to_hass(reauth_hass)

        await _authorise(reauth_hass, hass_client_no_auth, aioclient_mock, entry, NEW_TOKEN)

        assert (
            aioclient_mock.mock_calls[-1][2]["redirect_uri"]
            == "https://ha.example.com/auth/external/callback"
        )

    async def test_reauth_aborts_when_the_registered_external_url_is_gone(
        self, reauth_hass
    ):
        reauth_hass.config.external_url = None
        entry = make_entry()
        entry.add_to_hass(reauth_hass)
        result = await entry.start_reauth_flow(reauth_hass)

        result = await reauth_hass.config_entries.flow.async_configure(result["flow_id"], {})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "no_url_available"

    async def test_stored_redirect_uri_beats_the_computed_one(self, my_hass):
        impl = NocturneOAuth2Implementation.from_entry_data(
            my_hass, make_entry(**{CONF_REDIRECT_URI: "https://stored.example.com/cb"}).data
        )

        assert impl.redirect_uri == "https://stored.example.com/cb"


class TestRefreshToken:
    async def test_keeps_refresh_token_when_response_omits_it(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ):
        aioclient_mock.post(TOKEN_URL, json={"access_token": "new-access", "expires_in": 60})
        impl = NocturneOAuth2Implementation.from_entry_data(hass, make_entry().data)

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
        flow.flow_impl = MagicMock(
            client_id="test-client-id", redirect_uri="https://ha.example.com/auth/external/callback"
        )
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
        assert data[CONF_REDIRECT_URI] == "https://ha.example.com/auth/external/callback"
        assert data[CONF_REQUESTED_SCOPES] == DEFAULT_SCOPES


class TestOptionsFlow:
    def test_get_options_flow_does_not_assign_config_entry(self):
        handler = NocturneOAuth2FlowHandler.async_get_options_flow(MagicMock())

        assert isinstance(handler, NocturneOptionsFlowHandler)
