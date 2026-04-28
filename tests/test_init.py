"""Tests for integration setup, teardown, and service registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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

        mock_api_client.post_treatment.assert_awaited_once()
        treatment = mock_api_client.post_treatment.call_args[0][0]
        assert treatment["eventType"] == "Carb Correction"
        assert treatment["carbs"] == 30.0
        assert treatment["dataSource"] == DATA_SOURCE_HOME_ASSISTANT

    async def test_log_insulin(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_insulin"]
        call = MagicMock()
        call.data = {"insulin": 5.0, "notes": ""}

        await handler(call)

        treatment = mock_api_client.post_treatment.call_args[0][0]
        assert treatment["eventType"] == "Correction Bolus"
        assert treatment["insulin"] == 5.0

    async def test_log_glucose(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_glucose"]
        call = MagicMock()
        call.data = {"value": 110.0, "type": "sgv"}

        await handler(call)

        mock_api_client.post_entry.assert_awaited_once()
        entry = mock_api_client.post_entry.call_args[0][0]
        assert entry["sgv"] == 110.0
        assert entry["type"] == "sgv"

    async def test_log_activity(self, hass_with_services, mock_api_client):
        handler = hass_with_services["log_activity"]
        call = MagicMock()
        call.data = {"duration": 30, "activity_type": "walking", "notes": "walk"}

        await handler(call)

        treatment = mock_api_client.post_treatment.call_args[0][0]
        assert treatment["eventType"] == "Exercise"
        assert treatment["duration"] == 30
        assert treatment["activity_type"] == "walking"
