"""Tests for integration setup, reload and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nocturne import _access_token_provider
from custom_components.nocturne.const import (
    CONF_CLIENT_ID,
    CONF_NOTIFY_SERVICES,
    CONF_REQUESTED_SCOPES,
    CONF_TOKEN_URL,
    DEFAULT_SCOPES,
    DOMAIN,
    missing_scopes,
)

from .conftest import make_entry, set_up


def _token_requests(aioclient_mock: AiohttpClientMocker) -> dict[str, dict]:
    return {str(url): data for method, url, data, _ in aioclient_mock.mock_calls if method == "POST"}


@pytest.fixture
def setup_hass(hass: HomeAssistant, enable_custom_integrations: None):
    """Run the real setup up to the first token refresh, without the API, SignalR or platforms."""

    def coordinator(hass, entry, client):
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


class TestEntryLifecycle:
    @pytest.fixture
    async def entry(self, reauth_hass, stubs) -> MockConfigEntry:
        entry = make_entry(expires_in=3600)
        await set_up(reauth_hass, entry)
        return entry

    async def test_coordinators_belong_to_their_entry(self, entry):
        assert entry.runtime_data.glucose_coordinator.config_entry is entry
        assert entry.runtime_data.device_coordinator.config_entry is entry

    async def test_changing_options_reloads_with_the_new_notify_services(
        self, reauth_hass, stubs, entry
    ):
        reauth_hass.services.async_register("notify", "mobile_app_phone", AsyncMock())
        before = entry.runtime_data

        flow = await reauth_hass.config_entries.options.async_init(entry.entry_id)
        await reauth_hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_NOTIFY_SERVICES: ["mobile_app_phone"]}
        )
        await reauth_hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not before
        assert entry.runtime_data.alert_handler._notify_services == ["mobile_app_phone"]
        before.signalr_client.stop.assert_awaited_once()

    async def test_a_token_refresh_does_not_reload(self, reauth_hass, entry):
        before = entry.runtime_data

        reauth_hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": {**entry.data["token"], "access_token": "new"}}
        )
        await reauth_hass.async_block_till_done()

        assert entry.runtime_data is before
        before.signalr_client.stop.assert_not_awaited()

    async def test_unload_stops_signalr(self, reauth_hass, entry):
        signalr_client = entry.runtime_data.signalr_client

        assert await reauth_hass.config_entries.async_unload(entry.entry_id)

        signalr_client.stop.assert_awaited_once()

    async def test_a_failed_platform_unload_leaves_signalr_running(self, reauth_hass, entry):
        with patch.object(
            reauth_hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
        ):
            assert not await reauth_hass.config_entries.async_unload(entry.entry_id)

        assert entry.state is ConfigEntryState.FAILED_UNLOAD
        entry.runtime_data.signalr_client.stop.assert_not_awaited()


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
