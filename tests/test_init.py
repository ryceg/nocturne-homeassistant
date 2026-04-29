"""Tests for integration setup, teardown, and service registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nocturne_py import CreateBolusRequest, CreateCarbIntakeRequest, CreateStateSpanRequest, StateSpanCategory

from custom_components.nocturne import (
    _get_client,
    _register_services,
)
from custom_components.nocturne.const import DATA_SOURCE_HOME_ASSISTANT


class TestGetClient:
    def test_returns_client_from_first_entry(self, mock_hass, mock_api_client):
        result = _get_client(mock_hass)
        assert result is mock_api_client


class TestRegisterServices:
    def test_registers_all_four_services(self):
        hass = MagicMock()
        hass.services.has_service.return_value = False

        _register_services(hass)

        registered = {
            call.args[1] for call in hass.services.async_register.call_args_list
        }
        assert registered == {"log_carbs", "log_insulin", "log_glucose", "log_activity"}

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
