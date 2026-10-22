"""How polling failures surface: unavailable entities, retries, and reauthentication."""

from __future__ import annotations

import pytest
from aiohttp import ClientConnectionError, ClientResponseError, RequestInfo
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from multidict import CIMultiDict, CIMultiDictProxy
from nocturne_py import ApiException
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.nocturne.const import DOMAIN

from .conftest import INSTANCE_URL, TOKEN_URL, Stubs, make_api_client, make_entry, set_up

COORDINATORS = pytest.mark.parametrize(
    ("coordinator", "method", "entity_id", "state"),
    [
        ("glucose_coordinator", "get_latest_glucose", "sensor.nocturne_current_glucose", "120"),
        ("device_coordinator", "get_latest_pump_snapshot", "sensor.nocturne_pump_reservoir", "150.0"),
    ],
    ids=["glucose", "device"],
)


def api_error(status: int) -> ApiException:
    return ApiException(status=status, reason=f"HTTP {status}")


def refresh_error(status: int) -> ClientResponseError:
    request_info = RequestInfo(
        url=URL(TOKEN_URL),
        method="POST",
        headers=CIMultiDictProxy(CIMultiDict()),
        real_url=URL(TOKEN_URL),
    )
    return ClientResponseError(request_info, (), status=status)


def reauth_sources(hass: HomeAssistant) -> list[str]:
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    return [flow["context"]["source"] for flow in flows]


@pytest.fixture
async def entry(reauth_hass: HomeAssistant, stubs: Stubs) -> MockConfigEntry:
    entry = make_entry(expires_in=3600)
    await set_up(reauth_hass, entry)
    return entry


async def refresh(hass: HomeAssistant, entry: MockConfigEntry, coordinator: str) -> None:
    await getattr(entry.runtime_data, coordinator).async_refresh()
    await hass.async_block_till_done()


@COORDINATORS
class TestPollingFailures:
    @pytest.mark.parametrize(
        "error",
        [api_error(500), api_error(503), OSError("DNS failure"), RuntimeError("boom")],
        ids=["500", "503", "network", "unexpected"],
    )
    async def test_an_error_makes_the_entities_unavailable_without_reauth(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state, error
    ):
        getattr(stubs.clients[INSTANCE_URL], method).side_effect = error

        await refresh(reauth_hass, entry, coordinator)

        assert reauth_hass.states.get(entity_id).state == STATE_UNAVAILABLE
        assert reauth_sources(reauth_hass) == []
        assert entry.state is ConfigEntryState.LOADED

    async def test_the_entities_recover_when_the_server_does(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state
    ):
        client = stubs.clients[INSTANCE_URL]
        getattr(client, method).side_effect = api_error(500)
        await refresh(reauth_hass, entry, coordinator)

        getattr(client, method).side_effect = None
        await refresh(reauth_hass, entry, coordinator)

        assert reauth_hass.states.get(entity_id).state == state

    async def test_one_unauthorised_response_is_retried_before_reauth(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state
    ):
        getattr(stubs.clients[INSTANCE_URL], method).side_effect = api_error(401)

        await refresh(reauth_hass, entry, coordinator)

        assert reauth_hass.states.get(entity_id).state == STATE_UNAVAILABLE
        assert reauth_sources(reauth_hass) == []

    async def test_a_second_unauthorised_response_in_a_row_starts_reauth(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state
    ):
        getattr(stubs.clients[INSTANCE_URL], method).side_effect = api_error(401)

        await refresh(reauth_hass, entry, coordinator)
        await refresh(reauth_hass, entry, coordinator)

        assert reauth_sources(reauth_hass) == [SOURCE_REAUTH]

    async def test_a_success_between_unauthorised_responses_forgives_the_first(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state
    ):
        endpoint = getattr(stubs.clients[INSTANCE_URL], method)
        endpoint.side_effect = api_error(401)
        await refresh(reauth_hass, entry, coordinator)
        endpoint.side_effect = None
        await refresh(reauth_hass, entry, coordinator)

        endpoint.side_effect = api_error(401)
        await refresh(reauth_hass, entry, coordinator)

        assert reauth_sources(reauth_hass) == []

    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_a_refused_token_refresh_starts_reauth_at_once(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state, status
    ):
        getattr(stubs.clients[INSTANCE_URL], method).side_effect = refresh_error(status)

        await refresh(reauth_hass, entry, coordinator)

        assert reauth_sources(reauth_hass) == [SOURCE_REAUTH]

    @pytest.mark.parametrize(
        "error",
        [refresh_error(429), refresh_error(500), refresh_error(503), ClientConnectionError()],
        ids=["429", "500", "503", "connection"],
    )
    async def test_a_server_or_network_failure_refreshing_the_token_is_retried(
        self, reauth_hass, stubs, entry, coordinator, method, entity_id, state, error
    ):
        getattr(stubs.clients[INSTANCE_URL], method).side_effect = error

        await refresh(reauth_hass, entry, coordinator)

        assert reauth_hass.states.get(entity_id).state == STATE_UNAVAILABLE
        assert reauth_sources(reauth_hass) == []


@COORDINATORS
@pytest.mark.parametrize(
    "error", [api_error(401), api_error(500), OSError("down")], ids=["401", "500", "network"]
)
async def test_a_failing_first_poll_leaves_the_entry_retrying_without_reauth(
    reauth_hass, stubs, coordinator, method, entity_id, state, error
):
    entry = make_entry(expires_in=3600)
    entry.add_to_hass(reauth_hass)
    client = stubs.clients.setdefault(INSTANCE_URL, make_api_client())
    getattr(client, method).side_effect = error

    assert not await reauth_hass.config_entries.async_setup(entry.entry_id)
    await reauth_hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert reauth_sources(reauth_hass) == []


async def test_the_device_summary_is_for_todays_date(reauth_hass, stubs, entry):
    stubs.clients[INSTANCE_URL].get_daily_summary.assert_awaited_with(dt_util.now().date())


async def test_a_refused_refresh_at_setup_starts_reauth(
    hass: HomeAssistant, enable_custom_integrations: None, aioclient_mock: AiohttpClientMocker
):
    entry = make_entry(refresh_token="revoked")
    entry.add_to_hass(hass)
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert flows[0]["context"]["entry_id"] == entry.entry_id
