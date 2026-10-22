"""Tests for integration setup, reload and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nocturne import _display_name
from custom_components.nocturne.auth import oauth_token_provider
from custom_components.nocturne.const import (
    CONF_CLIENT_ID,
    CONF_NOTIFY_SERVICES,
    CONF_REQUESTED_SCOPES,
    CONF_TOKEN_URL,
    DEFAULT_SCOPES,
    DOMAIN,
    missing_scopes,
)

from .conftest import TOKEN_URL, issue_tokens, make_entry, set_up


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
        patch("custom_components.nocturne.NocturneSignalRClient", return_value=MagicMock()),
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


class TestOauthTokenProvider:
    async def test_a_token_that_is_due_is_refreshed_on_every_call(
        self, oauth_session, aioclient_mock: AiohttpClientMocker
    ):
        issue_tokens(aioclient_mock, "fresh-1", "fresh-2", expires_in=0)
        get_token = oauth_token_provider(oauth_session)

        assert await get_token() == "fresh-1"
        assert await get_token() == "fresh-2"

        assert list(_token_requests(aioclient_mock)) == [TOKEN_URL]
        assert len(aioclient_mock.mock_calls) == 2

    async def test_a_token_that_is_still_valid_is_reused(
        self, oauth_session, aioclient_mock: AiohttpClientMocker
    ):
        issue_tokens(aioclient_mock, "fresh-1", "fresh-2", expires_in=3600)
        get_token = oauth_token_provider(oauth_session)

        assert await get_token() == "fresh-1"
        assert await get_token() == "fresh-1"

        assert len(aioclient_mock.mock_calls) == 1


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


class TestInstanceNaming:
    def test_a_renamed_entry_is_named_by_its_title(self):
        assert _display_name(make_entry(title="Dad")) == "Dad"

    def test_the_default_title_is_replaced_by_the_instance_host(self):
        assert _display_name(make_entry()) == "nocturne.example.com"

    async def test_alerts_follow_an_entry_rename(self, reauth_hass, stubs):
        entry = make_entry(expires_in=3600)
        await set_up(reauth_hass, entry)
        reauth_hass.config_entries.async_update_entry(entry, title="Dad")
        assert entry.runtime_data.alert_handler._display_name() == "Dad"

    async def test_the_client_id_stays_the_acknowledging_identity(self, reauth_hass, stubs):
        entry = make_entry(expires_in=3600, title="Dad")
        await set_up(reauth_hass, entry)
        assert entry.runtime_data.instance_name == entry.data[CONF_CLIENT_ID]


class TestScopeReauth:
    async def _set_up(self, hass, scope, **data):
        entry = make_entry(expires_in=3600, **data)
        entry.data["token"]["scope"] = scope
        await set_up(hass, entry)
        return entry

    async def test_old_entry_with_a_narrower_grant_starts_reauth(self, reauth_hass, stubs):
        granted = " ".join(s for s in DEFAULT_SCOPES if s != "reports.read")

        entry = await self._set_up(reauth_hass, granted)

        flows = reauth_hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
        assert flows[0]["context"]["entry_id"] == entry.entry_id

    async def test_entry_asked_for_every_scope_starts_no_reauth_on_a_capped_grant(
        self, reauth_hass, stubs
    ):
        await self._set_up(
            reauth_hass, "glucose.readwrite", **{CONF_REQUESTED_SCOPES: list(DEFAULT_SCOPES)}
        )

        assert reauth_hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []

    async def test_entry_asked_before_a_scope_was_added_starts_reauth(self, reauth_hass, stubs):
        asked = [s for s in DEFAULT_SCOPES if s != "alerts.readwrite"]

        await self._set_up(reauth_hass, " ".join(DEFAULT_SCOPES), **{CONF_REQUESTED_SCOPES: asked})

        flows = reauth_hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]

    @pytest.mark.parametrize("scope", [" ".join(DEFAULT_SCOPES), None])
    async def test_full_or_unknown_grant_starts_no_reauth(self, reauth_hass, stubs, scope):
        await self._set_up(reauth_hass, scope)

        assert reauth_hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


class TestMissingScopes:
    def test_a_scope_is_not_satisfied_by_one_it_prefixes(self):
        data = {"token": {"scope": "devices.readwrite"}}

        assert "devices.read" in missing_scopes(data)

    def test_a_scope_is_not_satisfied_by_a_longer_requested_one(self):
        data = {"token": {"scope": "devices.read"}, CONF_REQUESTED_SCOPES: ["devices.readwrite"]}

        assert "devices.read" in missing_scopes(data)

    def test_null_scope_means_unknown(self):
        assert missing_scopes({"token": {"scope": None}}) == []
