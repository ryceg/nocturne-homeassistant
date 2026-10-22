"""Tests for integration setup, teardown, and service registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from nocturne_py import CreateBolusRequest, CreateCarbIntakeRequest, CreateStateSpanRequest, StateSpanCategory
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nocturne import (
    _access_token_provider,
    _get_client,
    _register_services,
)
from custom_components.nocturne.const import (
    CONF_CLIENT_ID,
    CONF_REQUESTED_SCOPES,
    CONF_TOKEN_URL,
    DATA_SOURCE_HOME_ASSISTANT,
    DEFAULT_SCOPES,
    DOMAIN,
    missing_scopes,
)

from .conftest import make_entry


def _token_requests(aioclient_mock: AiohttpClientMocker) -> dict[str, dict]:
    return {str(url): data for method, url, data, _ in aioclient_mock.mock_calls if method == "POST"}


@pytest.fixture
def setup_hass(hass: HomeAssistant, enable_custom_integrations: None):
    """Run the real setup up to the first token refresh, without the API, SignalR or platforms."""

    def coordinator(hass, client):
        coord = MagicMock()
        coord.async_config_entry_first_refresh = AsyncMock(side_effect=client._ensure_token)
        return coord

    with (
        patch("custom_components.nocturne.GlucoseCoordinator", side_effect=coordinator),
        patch("custom_components.nocturne.DeviceCoordinator", side_effect=coordinator),
        patch("custom_components.nocturne.NocturneSignalRClient", return_value=AsyncMock()),
        patch("custom_components.nocturne.PLATFORMS", []),
    ):
        yield hass


class TestTokenRefreshPerEntry:
    async def test_each_entry_refreshes_against_its_own_client(
        self, setup_hass, aioclient_mock: AiohttpClientMocker
    ):
        first = make_entry("https://first.example.com", "first-client")
        second = make_entry("https://second.example.com", "second-client")
        first.add_to_hass(setup_hass)
        second.add_to_hass(setup_hass)
        for entry in (first, second):
            aioclient_mock.post(
                entry.data[CONF_TOKEN_URL],
                json={"access_token": f"new-{entry.data[CONF_CLIENT_ID]}", "expires_in": 3600},
            )

        assert await setup_hass.config_entries.async_setup(first.entry_id)
        await setup_hass.async_block_till_done()

        assert first.state is ConfigEntryState.LOADED
        assert second.state is ConfigEntryState.LOADED
        requests = _token_requests(aioclient_mock)
        assert requests["https://first.example.com/oauth/token"]["client_id"] == "first-client"
        assert requests["https://first.example.com/oauth/token"]["refresh_token"] == "refresh-first-client"
        assert requests["https://second.example.com/oauth/token"]["client_id"] == "second-client"
        assert requests["https://second.example.com/oauth/token"]["refresh_token"] == "refresh-second-client"
        assert first.data["token"]["access_token"] == "new-first-client"
        assert second.data["token"]["access_token"] == "new-second-client"


class TestAccessTokenProvider:
    async def test_each_call_refreshes_and_returns_the_current_token(self):
        oauth_session = MagicMock()
        oauth_session.token = {"access_token": "expired"}
        refreshed = iter(["fresh-1", "fresh-2"])

        async def _refresh() -> None:
            oauth_session.token = {"access_token": next(refreshed)}

        oauth_session.async_ensure_token_valid = AsyncMock(side_effect=_refresh)
        get_token = _access_token_provider(oauth_session)

        assert await get_token() == "fresh-1"
        assert await get_token() == "fresh-2"
        assert oauth_session.async_ensure_token_valid.await_count == 2


class TestGetClient:
    def test_returns_client_from_first_entry(self, mock_hass, mock_api_client):
        result = _get_client(mock_hass)
        assert result is mock_api_client


class TestRegisterServices:
    def test_registers_all_services(self):
        hass = MagicMock()
        hass.services.has_service.return_value = False

        _register_services(hass)

        registered = {
            call.args[1] for call in hass.services.async_register.call_args_list
        }
        assert registered == {"log_carbs", "log_insulin", "log_glucose", "log_activity", "acknowledge_alert"}

    def test_skips_if_already_registered(self):
        hass = MagicMock()
        hass.services.has_service.return_value = True

        _register_services(hass)

        hass.services.async_register.assert_not_called()


class TestServiceHandlers:
    @pytest.fixture
    def hass_with_services(self, mock_hass, mock_api_client):
        """Register real service handlers against mock_hass."""
        mock_hass.services = MagicMock()
        mock_hass.services.has_service.return_value = False
        _register_services(mock_hass)
        # Extract handlers keyed by service name
        handlers = {}
        for call in mock_hass.services.async_register.call_args_list:
            service_name = call.args[1]
            handler_fn = call.args[2]
            handlers[service_name] = handler_fn
        return handlers

    async def test_log_carbs(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_carbs"]
        call = MagicMock()
        call.data = {"carbs": 30.0, "notes": "lunch"}

        await handler(call)

        mock_api_client.create_carb_intake.assert_awaited_once()
        carb_intake = mock_api_client.create_carb_intake.call_args[0][0]
        assert isinstance(carb_intake, CreateCarbIntakeRequest)
        assert carb_intake.carbs == 30.0
        assert carb_intake.data_source == DATA_SOURCE_HOME_ASSISTANT

    async def test_log_insulin(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_insulin"]
        call = MagicMock()
        call.data = {"insulin": 5.0, "notes": ""}

        await handler(call)

        mock_api_client.create_bolus.assert_awaited_once()
        bolus_request = mock_api_client.create_bolus.call_args[0][0]
        assert isinstance(bolus_request, CreateBolusRequest)
        assert bolus_request.insulin == 5.0
        assert bolus_request.data_source == DATA_SOURCE_HOME_ASSISTANT

    async def test_log_glucose(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_glucose"]
        call = MagicMock()
        call.data = {"value": 110.0, "type": "sgv"}

        await handler(call)

        mock_api_client.create_glucose.assert_awaited_once_with(
            mgdl=110.0,
            data_source=DATA_SOURCE_HOME_ASSISTANT,
        )

    async def test_log_activity(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_activity"]
        call = MagicMock()
        call.data = {"duration": 30, "activity_type": "walking", "notes": "walk"}

        await handler(call)

        mock_api_client.create_state_span.assert_awaited_once()
        state_span_request = mock_api_client.create_state_span.call_args[0][0]
        assert isinstance(state_span_request, CreateStateSpanRequest)
        assert state_span_request.category == StateSpanCategory.EXERCISE
        assert state_span_request.source == DATA_SOURCE_HOME_ASSISTANT
        assert state_span_request.end_mills - state_span_request.start_mills == 30 * 60 * 1000

    async def test_acknowledge_alert(self, mock_hass, mock_api_client):
        """Test acknowledge_alert calls signalr_client.acknowledge."""
        # Set up a mock signalr_client in the per-entry namespace
        mock_signalr = AsyncMock()
        mock_signalr.connected = True
        entry_id = "test_entry_id"
        mock_hass.data[DOMAIN][entry_id]["signalr_client"] = mock_signalr
        mock_hass.data[DOMAIN][entry_id]["instance_name"] = "my-instance"

        # Build a mock entry to pass to _register_services
        mock_entry = MagicMock()
        mock_entry.entry_id = entry_id
        mock_entry.data = {"client_id": "my-instance"}

        # Register services and extract handler
        mock_hass.services = MagicMock()
        mock_hass.services.has_service.return_value = False
        _register_services(mock_hass, mock_entry)
        handlers = {}
        for call in mock_hass.services.async_register.call_args_list:
            service_name = call.args[1]
            handler_fn = call.args[2]
            handlers[service_name] = handler_fn

        handler = handlers["acknowledge_alert"]
        call = MagicMock()
        call.data = {"excursion_id": "exc-123"}

        await handler(call)

        mock_signalr.acknowledge.assert_awaited_once_with(
            "exc-123", "homeassistant:my-instance"
        )

    async def test_acknowledge_alert_not_connected(self, mock_hass, mock_api_client):
        """Test acknowledge_alert raises when signalr not connected."""
        # No signalr_client in hass.data
        mock_hass.services = MagicMock()
        mock_hass.services.has_service.return_value = False
        _register_services(mock_hass)
        handlers = {}
        for call in mock_hass.services.async_register.call_args_list:
            service_name = call.args[1]
            handler_fn = call.args[2]
            handlers[service_name] = handler_fn

        handler = handlers["acknowledge_alert"]
        call = MagicMock()
        call.data = {"excursion_id": "exc-123"}

        with pytest.raises(HomeAssistantError):
            await handler(call)

    async def test_acknowledge_alert_propagates_client_failure(self, mock_hass, mock_api_client):
        mock_signalr = AsyncMock()
        mock_signalr.connected = True
        mock_signalr.acknowledge.side_effect = HomeAssistantError("refused")
        entry_id = "test_entry_id"
        mock_hass.data[DOMAIN][entry_id]["signalr_client"] = mock_signalr
        mock_entry = MagicMock()
        mock_entry.entry_id = entry_id
        mock_entry.data = {"client_id": "my-instance"}
        mock_hass.services = MagicMock()
        mock_hass.services.has_service.return_value = False
        _register_services(mock_hass, mock_entry)
        handler = {c.args[1]: c.args[2] for c in mock_hass.services.async_register.call_args_list}[
            "acknowledge_alert"
        ]
        call = MagicMock()
        call.data = {"excursion_id": "exc-123"}

        with pytest.raises(HomeAssistantError, match="refused"):
            await handler(call)


class TestScopeReauth:
    @pytest.fixture
    async def scope_hass(self, reauth_hass, mock_api_client):
        with (
            patch("custom_components.nocturne.NocturneApiClient", return_value=mock_api_client),
            patch("custom_components.nocturne.NocturneSignalRClient"),
        ):
            yield reauth_hass

    async def _set_up(self, hass, scope, **data):
        entry = make_entry(expires_in=3600, **data)
        entry.data["token"]["scope"] = scope
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        return entry

    async def test_old_entry_with_a_narrower_grant_starts_reauth(self, scope_hass):
        granted = " ".join(s for s in DEFAULT_SCOPES if s != "reports.read")

        entry = await self._set_up(scope_hass, granted)

        flows = scope_hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
        assert flows[0]["context"]["entry_id"] == entry.entry_id

    async def test_entry_asked_for_every_scope_starts_no_reauth_on_a_capped_grant(self, scope_hass):
        await self._set_up(
            scope_hass, "glucose.readwrite", **{CONF_REQUESTED_SCOPES: list(DEFAULT_SCOPES)}
        )

        assert scope_hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []

    async def test_entry_asked_before_a_scope_was_added_starts_reauth(self, scope_hass):
        asked = [s for s in DEFAULT_SCOPES if s != "alerts.readwrite"]

        await self._set_up(scope_hass, " ".join(DEFAULT_SCOPES), **{CONF_REQUESTED_SCOPES: asked})

        flows = scope_hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]

    @pytest.mark.parametrize("scope", [" ".join(DEFAULT_SCOPES), None])
    async def test_full_or_unknown_grant_starts_no_reauth(self, scope_hass, scope):
        await self._set_up(scope_hass, scope)

        assert scope_hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


class TestMissingScopes:
    def test_a_scope_is_not_satisfied_by_one_it_prefixes(self):
        data = {"token": {"scope": "devices.readwrite"}}

        assert "devices.read" in missing_scopes(data)

    def test_a_scope_is_not_satisfied_by_a_longer_requested_one(self):
        data = {"token": {"scope": "devices.read"}, CONF_REQUESTED_SCOPES: ["devices.readwrite"]}

        assert "devices.read" in missing_scopes(data)

    def test_null_scope_means_unknown(self):
        assert missing_scopes({"token": {"scope": None}}) == []
