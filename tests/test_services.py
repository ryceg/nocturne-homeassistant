"""Tests for the Nocturne services and how they pick the entry they act on."""

from __future__ import annotations

import pytest
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from nocturne_py import (
    CreateBolusRequest,
    CreateCarbIntakeRequest,
    CreateStateSpanRequest,
    StateSpanCategory,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nocturne.const import DATA_SOURCE_HOME_ASSISTANT, DOMAIN

from .conftest import Stubs, make_entry, set_up

FIRST_URL = "https://first.example.com"
SECOND_URL = "https://second.example.com"


async def _call(hass: HomeAssistant, service: str, data: dict) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


@pytest.fixture
async def one_entry(reauth_hass: HomeAssistant, stubs: Stubs) -> MockConfigEntry:
    entry = make_entry(expires_in=3600)
    await set_up(reauth_hass, entry)
    return entry


@pytest.fixture
async def two_entries(
    reauth_hass: HomeAssistant, stubs: Stubs
) -> tuple[MockConfigEntry, MockConfigEntry]:
    first = make_entry(FIRST_URL, "first-client", expires_in=3600)
    second = make_entry(SECOND_URL, "second-client", expires_in=3600)
    await set_up(reauth_hass, first, second)
    return first, second


class TestWithOneEntry:
    async def test_log_carbs(self, reauth_hass, stubs, one_entry):
        await _call(reauth_hass, "log_carbs", {"carbs": 30, "notes": "lunch"})

        request = stubs.clients[one_entry.data["instance_url"]].create_carb_intake.call_args[0][0]
        assert isinstance(request, CreateCarbIntakeRequest)
        assert request.carbs == 30.0
        assert request.data_source == DATA_SOURCE_HOME_ASSISTANT

    async def test_log_insulin(self, reauth_hass, stubs, one_entry):
        await _call(reauth_hass, "log_insulin", {"insulin": 5})

        request = stubs.clients[one_entry.data["instance_url"]].create_bolus.call_args[0][0]
        assert isinstance(request, CreateBolusRequest)
        assert request.insulin == 5.0
        assert request.data_source == DATA_SOURCE_HOME_ASSISTANT

    async def test_log_glucose(self, reauth_hass, stubs, one_entry):
        await _call(reauth_hass, "log_glucose", {"value": 110, "type": "sgv"})

        stubs.clients[one_entry.data["instance_url"]].create_glucose.assert_awaited_once_with(
            mgdl=110.0, data_source=DATA_SOURCE_HOME_ASSISTANT
        )

    async def test_log_activity(self, reauth_hass, stubs, one_entry):
        await _call(reauth_hass, "log_activity", {"duration": 30, "activity_type": "walking"})

        request = stubs.clients[one_entry.data["instance_url"]].create_state_span.call_args[0][0]
        assert isinstance(request, CreateStateSpanRequest)
        assert request.category == StateSpanCategory.EXERCISE
        assert request.source == DATA_SOURCE_HOME_ASSISTANT
        assert request.end_mills - request.start_mills == 30 * 60 * 1000

    async def test_acknowledge_alert(self, reauth_hass, stubs, one_entry):
        await _call(reauth_hass, "acknowledge_alert", {"excursion_id": "exc-123"})

        stubs.signalr[one_entry.data["instance_url"]].acknowledge.assert_awaited_once_with(
            "exc-123", "homeassistant:stored-client-id"
        )

    async def test_acknowledge_alert_propagates_client_failure(self, reauth_hass, stubs, one_entry):
        stubs.signalr[one_entry.data["instance_url"]].acknowledge.side_effect = HomeAssistantError(
            "refused"
        )

        with pytest.raises(HomeAssistantError, match="refused"):
            await _call(reauth_hass, "acknowledge_alert", {"excursion_id": "exc-123"})

    async def test_services_refuse_once_nothing_is_loaded_and_serve_the_next_entry(
        self, reauth_hass, stubs, one_entry
    ):
        assert await reauth_hass.config_entries.async_unload(one_entry.entry_id)

        with pytest.raises(ServiceValidationError) as err:
            await _call(reauth_hass, "log_carbs", {"carbs": 10})
        assert err.value.translation_key == "no_loaded_entry"

        assert await reauth_hass.config_entries.async_remove(one_entry.entry_id)
        new_entry = make_entry(SECOND_URL, "second-client", expires_in=3600)
        new_entry.add_to_hass(reauth_hass)
        assert await reauth_hass.config_entries.async_setup(new_entry.entry_id)
        await reauth_hass.async_block_till_done()

        await _call(reauth_hass, "log_carbs", {"carbs": 20})

        stubs.clients[SECOND_URL].create_carb_intake.assert_awaited_once()
        stubs.clients[one_entry.data["instance_url"]].create_carb_intake.assert_not_awaited()


class TestWithTwoEntries:
    async def test_call_acts_on_the_named_entry(self, reauth_hass, stubs, two_entries):
        _, second = two_entries

        await _call(reauth_hass, "log_carbs", {"carbs": 30, ATTR_CONFIG_ENTRY_ID: second.entry_id})

        stubs.clients[SECOND_URL].create_carb_intake.assert_awaited_once()
        stubs.clients[FIRST_URL].create_carb_intake.assert_not_awaited()

    async def test_acknowledge_uses_the_named_entrys_connection_and_name(
        self, reauth_hass, stubs, two_entries
    ):
        _, second = two_entries

        await _call(
            reauth_hass,
            "acknowledge_alert",
            {"excursion_id": "exc-123", ATTR_CONFIG_ENTRY_ID: second.entry_id},
        )

        stubs.signalr[SECOND_URL].acknowledge.assert_awaited_once_with(
            "exc-123", "homeassistant:second-client"
        )
        stubs.signalr[FIRST_URL].acknowledge.assert_not_awaited()

    async def test_acknowledge_keeps_working_after_another_entry_unloads(
        self, reauth_hass, stubs, two_entries
    ):
        first, second = two_entries
        assert await reauth_hass.config_entries.async_unload(first.entry_id)

        await _call(
            reauth_hass,
            "acknowledge_alert",
            {"excursion_id": "exc-123", ATTR_CONFIG_ENTRY_ID: second.entry_id},
        )
        await _call(reauth_hass, "acknowledge_alert", {"excursion_id": "exc-456"})

        assert [c.args for c in stubs.signalr[SECOND_URL].acknowledge.await_args_list] == [
            ("exc-123", "homeassistant:second-client"),
            ("exc-456", "homeassistant:second-client"),
        ]
        stubs.signalr[FIRST_URL].acknowledge.assert_not_awaited()

    async def test_call_without_an_entry_is_refused(self, reauth_hass, stubs, two_entries):
        with pytest.raises(ServiceValidationError) as err:
            await _call(reauth_hass, "log_carbs", {"carbs": 30})

        assert err.value.translation_key == "entry_required"
        stubs.clients[FIRST_URL].create_carb_intake.assert_not_awaited()
        stubs.clients[SECOND_URL].create_carb_intake.assert_not_awaited()

    async def test_call_naming_an_unloaded_entry_is_refused(self, reauth_hass, two_entries):
        first, _ = two_entries
        assert await reauth_hass.config_entries.async_unload(first.entry_id)

        with pytest.raises(ServiceValidationError) as err:
            await _call(
                reauth_hass, "acknowledge_alert", {"excursion_id": "x", ATTR_CONFIG_ENTRY_ID: first.entry_id}
            )

        assert err.value.translation_key == "entry_not_loaded"


@pytest.mark.parametrize("entry_id", ["no-such-entry", "other-domain"])
async def test_call_naming_a_non_nocturne_entry_is_refused(reauth_hass, one_entry, entry_id):
    MockConfigEntry(domain="other", entry_id="other-domain").add_to_hass(reauth_hass)

    with pytest.raises(ServiceValidationError) as err:
        await _call(reauth_hass, "log_carbs", {"carbs": 30, ATTR_CONFIG_ENTRY_ID: entry_id})

    assert err.value.translation_key == "entry_not_found"
