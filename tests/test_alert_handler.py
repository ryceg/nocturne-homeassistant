"""Tests for the alert event handler."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.nocturne.alert_handler import NocturneAlertHandler


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def handler(mock_hass):
    return NocturneAlertHandler(
        hass=mock_hass,
        notify_services=["notify.mobile_app_phone"],
        instance_name="test-hub",
    )


SAMPLE_ALERT = {
    "excursionId": "abc-123",
    "ruleName": "Urgent Low",
    "severity": "critical",
    "glucoseValue": 55,
    "trend": "SingleDown",
    "trendRate": -2.0,
    "readingTimestamp": "2026-05-14T12:00:00Z",
    "instanceId": "inst-1",
    "tenantId": "tenant-1",
    "subjectName": "Rhys",
    "activeExcursionCount": 1,
}


@pytest.mark.asyncio
async def test_dispatch_fires_ha_event(handler, mock_hass):
    await handler.handle_alert_dispatch(SAMPLE_ALERT)
    mock_hass.bus.async_fire.assert_called_once()
    call_args = mock_hass.bus.async_fire.call_args
    assert call_args[0][0] == "nocturne_alert"
    assert call_args[0][1]["excursion_id"] == "abc-123"
    assert call_args[0][1]["severity"] == "critical"
    assert call_args[0][1]["glucose_value"] == 55


@pytest.mark.asyncio
async def test_dispatch_creates_persistent_notification(handler, mock_hass):
    await handler.handle_alert_dispatch(SAMPLE_ALERT)
    calls = mock_hass.services.async_call.call_args_list
    pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
    assert len(pn_calls) >= 1
    assert pn_calls[0][0][1] == "create"


@pytest.mark.asyncio
async def test_dispatch_sends_mobile_push(handler, mock_hass):
    await handler.handle_alert_dispatch(SAMPLE_ALERT)
    calls = mock_hass.services.async_call.call_args_list
    notify_calls = [c for c in calls if c[0][0] == "notify"]
    assert len(notify_calls) == 1
    assert notify_calls[0][0][1] == "mobile_app_phone"


@pytest.mark.asyncio
async def test_dispatch_critical_push_data(handler, mock_hass):
    await handler.handle_alert_dispatch(SAMPLE_ALERT)
    calls = mock_hass.services.async_call.call_args_list
    notify_calls = [c for c in calls if c[0][0] == "notify"]
    push_data = notify_calls[0][0][2]["data"]
    assert push_data["push"]["interruption-level"] == "critical"


@pytest.mark.asyncio
async def test_resolved_dismisses_notification(handler, mock_hass):
    await handler.handle_alert_resolved({"excursionId": "abc-123", "ruleName": "Urgent Low"})
    mock_hass.bus.async_fire.assert_called_once()
    assert mock_hass.bus.async_fire.call_args[0][0] == "nocturne_alert_resolved"
    calls = mock_hass.services.async_call.call_args_list
    pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
    assert pn_calls[0][0][1] == "dismiss"


@pytest.mark.asyncio
async def test_acknowledged_updates_notification(handler, mock_hass):
    await handler.handle_alert_acknowledged({"excursionId": "abc-123", "ruleName": "Urgent Low", "acknowledgedBy": "homeassistant:test"})
    mock_hass.bus.async_fire.assert_called_once()
    assert mock_hass.bus.async_fire.call_args[0][0] == "nocturne_alert_acknowledged"


@pytest.mark.asyncio
async def test_severity_mapping_warning(handler):
    result = handler._map_push_severity("warning")
    assert result["push"]["interruption-level"] == "time-sensitive"


@pytest.mark.asyncio
async def test_severity_mapping_info(handler):
    result = handler._map_push_severity("info")
    assert result["push"]["interruption-level"] == "active"
