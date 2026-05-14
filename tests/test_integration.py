"""End-to-end integration tests for the alert delivery pipeline.

Tests the full lifecycle: SignalR callbacks -> alert handler -> HA services,
and SignalR callbacks -> glucose coordinator -> data update.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from custom_components.nocturne.alert_handler import NocturneAlertHandler
from custom_components.nocturne.coordinator import GlucoseCoordinator
from custom_components.nocturne.signalr import NocturneSignalRClient
from custom_components.nocturne.const import (
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
)

# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

SAMPLE_ALERT_DISPATCH = {
    "excursionId": "exc-001",
    "ruleName": "Urgent Low",
    "severity": "critical",
    "glucoseValue": 55,
    "trend": "SingleDown",
    "trendRate": -2.0,
    "readingTimestamp": "2026-05-14T12:00:00Z",
    "instanceId": "inst-1",
    "tenantId": "tenant-1",
    "subjectName": "Test User",
    "activeExcursionCount": 1,
    "ackAble": True,
    "channelId": "chan-1",
}

SAMPLE_ALERT_RESOLVED = {
    "excursionId": "exc-001",
    "ruleName": "Urgent Low",
    "resolvedAt": "2026-05-14T12:30:00Z",
}

SAMPLE_ALERT_ACKNOWLEDGED = {
    "excursionId": "exc-001",
    "ruleName": "Urgent Low",
    "acknowledgedBy": "homeassistant:test-hub",
    "acknowledgedAt": "2026-05-14T12:15:00Z",
}

SAMPLE_GLUCOSE_READING = {
    "mgdl": 120,
    "direction": "Flat",
    "mills": 1700000000000,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_hass():
    """Minimal mock HomeAssistant with bus and services."""
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def single_notify_handler(mock_hass):
    """Alert handler with a single notify service."""
    return NocturneAlertHandler(
        hass=mock_hass,
        notify_services=["notify.mobile_app_phone"],
        instance_name="test-hub",
    )


@pytest.fixture
def multi_notify_handler(mock_hass):
    """Alert handler with multiple notify services."""
    return NocturneAlertHandler(
        hass=mock_hass,
        notify_services=["notify.mobile_app_phone", "notify.mobile_app_tablet"],
        instance_name="test-hub",
    )


@pytest.fixture
def no_notify_handler(mock_hass):
    """Alert handler with no notify services configured."""
    return NocturneAlertHandler(
        hass=mock_hass,
        notify_services=[],
        instance_name="test-hub",
    )


# ---------------------------------------------------------------------------
# 1. Full alert dispatch flow
# ---------------------------------------------------------------------------

class TestFullAlertDispatchFlow:
    """Simulate an alert_dispatch arriving via SignalR and verify all outputs."""

    @pytest.mark.asyncio
    async def test_ha_event_fired_with_correct_data(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT
        assert event_data["excursion_id"] == "exc-001"
        assert event_data["rule_name"] == "Urgent Low"
        assert event_data["severity"] == "critical"
        assert event_data["glucose_value"] == 55
        assert event_data["trend"] == "SingleDown"
        assert event_data["ack_able"] is True
        assert event_data["channel_id"] == "chan-1"
        assert event_data["fired_at"] == "2026-05-14T12:00:00Z"

    @pytest.mark.asyncio
    async def test_persistent_notification_created(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "create"
        service_data = pn_calls[0][0][2]
        assert "Urgent Low" in service_data["title"]
        assert "55" in service_data["message"]
        assert service_data["notification_id"] == "nocturne_alert_exc-001"

    @pytest.mark.asyncio
    async def test_mobile_push_sent(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 1
        assert notify_calls[0][0][1] == "mobile_app_phone"

    @pytest.mark.asyncio
    async def test_critical_severity_push_metadata(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "critical"

    @pytest.mark.asyncio
    async def test_all_three_services_called(self, single_notify_handler, mock_hass):
        """Dispatch should produce exactly 3 service calls: persistent_notification + notify."""
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        calls = mock_hass.services.async_call.call_args_list
        domains_called = [c[0][0] for c in calls]
        assert "persistent_notification" in domains_called
        assert "notify" in domains_called
        # persistent_notification.create + 1 notify
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# 2. Alert resolved flow
# ---------------------------------------------------------------------------

class TestAlertResolvedFlow:
    """Simulate alert_resolved and verify event + notification dismissed."""

    @pytest.mark.asyncio
    async def test_resolved_event_fired(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_resolved(SAMPLE_ALERT_RESOLVED)

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT_RESOLVED
        assert event_data["excursion_id"] == "exc-001"
        assert event_data["rule_name"] == "Urgent Low"
        assert event_data["resolved_at"] == "2026-05-14T12:30:00Z"

    @pytest.mark.asyncio
    async def test_persistent_notification_dismissed(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_resolved(SAMPLE_ALERT_RESOLVED)

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "dismiss"
        assert pn_calls[0][0][2]["notification_id"] == "nocturne_alert_exc-001"


# ---------------------------------------------------------------------------
# 3. Alert acknowledged flow
# ---------------------------------------------------------------------------

class TestAlertAcknowledgedFlow:
    """Simulate alert_acknowledged and verify event + notification updated."""

    @pytest.mark.asyncio
    async def test_acknowledged_event_fired(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_acknowledged(SAMPLE_ALERT_ACKNOWLEDGED)

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT_ACKNOWLEDGED
        assert event_data["excursion_id"] == "exc-001"
        assert event_data["rule_name"] == "Urgent Low"
        assert event_data["acknowledged_by"] == "homeassistant:test-hub"
        assert event_data["acknowledged_at"] == "2026-05-14T12:15:00Z"

    @pytest.mark.asyncio
    async def test_notification_updated_with_ack_info(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_acknowledged(SAMPLE_ALERT_ACKNOWLEDGED)

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "create"
        service_data = pn_calls[0][0][2]
        assert "(Acknowledged)" in service_data["title"]
        assert "homeassistant:test-hub" in service_data["message"]
        assert service_data["notification_id"] == "nocturne_alert_exc-001"


# ---------------------------------------------------------------------------
# 4. Glucose push flow
# ---------------------------------------------------------------------------

class TestGlucosePushFlow:
    """Simulate glucose_reading via coordinator push and verify data updated."""

    @pytest.fixture(autouse=True)
    def _patch_frame_report(self, monkeypatch):
        """Prevent HA frame helper from raising during unit tests."""
        monkeypatch.setattr(
            "homeassistant.helpers.frame.report_usage", lambda *a, **kw: None
        )

    @pytest.mark.asyncio
    async def test_push_glucose_data_updates_coordinator(self, mock_hass):
        client = AsyncMock()
        coordinator = GlucoseCoordinator(hass=mock_hass, client=client)
        # async_set_updated_data requires the coordinator to be set up
        coordinator.async_set_updated_data = MagicMock()

        coordinator.push_glucose_data(SAMPLE_GLUCOSE_READING)

        coordinator.async_set_updated_data.assert_called_once_with(SAMPLE_GLUCOSE_READING)

    @pytest.mark.asyncio
    async def test_signalr_active_flag(self, mock_hass):
        client = AsyncMock()
        coordinator = GlucoseCoordinator(hass=mock_hass, client=client)

        assert coordinator.signalr_active is False
        coordinator.signalr_active = True
        assert coordinator.signalr_active is True

    @pytest.mark.asyncio
    async def test_signalr_active_skips_api_poll(self, mock_hass):
        """When signalr_active is True, _async_update_data returns existing data."""
        client = AsyncMock()
        coordinator = GlucoseCoordinator(hass=mock_hass, client=client)
        coordinator._signalr_active = True
        coordinator.data = {"glucose": "existing"}

        result = await coordinator._async_update_data()

        assert result == {"glucose": "existing"}
        client.get_latest_glucose.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Acknowledge service flow (SignalR client)
# ---------------------------------------------------------------------------

class TestAcknowledgeServiceFlow:
    """Verify acknowledge_alert calls SignalR client correctly."""

    @pytest.mark.asyncio
    async def test_acknowledge_sends_via_signalr(self, mock_hass):
        signalr_client = NocturneSignalRClient(
            hass=mock_hass,
            instance_url="https://nocturne.example.com",
            access_token="test-token",
            instance_id="test-client-id",
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=AsyncMock(),
            on_alert_resolved=AsyncMock(),
            on_alert_acknowledged=AsyncMock(),
        )
        # Simulate a connected state with a mock underlying client
        signalr_client._connected = True
        signalr_client._client = MagicMock()
        signalr_client._client.send = AsyncMock()

        await signalr_client.acknowledge("exc-001", "homeassistant:test-hub")

        signalr_client._client.send.assert_awaited_once_with(
            "Acknowledge", ["exc-001", "homeassistant:test-hub"]
        )

    @pytest.mark.asyncio
    async def test_acknowledge_raises_when_disconnected(self, mock_hass):
        signalr_client = NocturneSignalRClient(
            hass=mock_hass,
            instance_url="https://nocturne.example.com",
            access_token="test-token",
            instance_id="test-client-id",
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=AsyncMock(),
            on_alert_resolved=AsyncMock(),
            on_alert_acknowledged=AsyncMock(),
        )
        with pytest.raises(ConnectionError):
            await signalr_client.acknowledge("exc-001", "homeassistant:test-hub")


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for alert dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_without_glucose_value(self, single_notify_handler, mock_hass):
        """Alert dispatch with no glucose value should still work."""
        payload = {
            "excursionId": "exc-002",
            "ruleName": "Connection Lost",
            "severity": "warning",
            "readingTimestamp": "2026-05-14T12:00:00Z",
        }
        await single_notify_handler.handle_alert_dispatch(payload)

        # Event fired
        mock_hass.bus.async_fire.assert_called_once()
        event_data = mock_hass.bus.async_fire.call_args[0][1]
        assert event_data["glucose_value"] is None

        # Persistent notification still created
        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1

        # Notify still sent
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 1

    @pytest.mark.asyncio
    async def test_dispatch_with_no_notify_services(self, no_notify_handler, mock_hass):
        """With no notify services, event + persistent notification still fire."""
        await no_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        # Event fired
        mock_hass.bus.async_fire.assert_called_once()

        # Persistent notification created
        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1

        # No notify calls
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 0

    @pytest.mark.asyncio
    async def test_dispatch_with_multiple_notify_services(self, multi_notify_handler, mock_hass):
        """Multiple notify services should each receive a push."""
        await multi_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 2
        service_names = {c[0][1] for c in notify_calls}
        assert service_names == {"mobile_app_phone", "mobile_app_tablet"}

    @pytest.mark.asyncio
    async def test_dispatch_warning_severity_push_level(self, single_notify_handler, mock_hass):
        """Warning severity should map to time-sensitive push."""
        payload = {**SAMPLE_ALERT_DISPATCH, "severity": "warning"}
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "time-sensitive"

    @pytest.mark.asyncio
    async def test_dispatch_info_severity_push_level(self, single_notify_handler, mock_hass):
        """Info severity should map to active push."""
        payload = {**SAMPLE_ALERT_DISPATCH, "severity": "info"}
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "active"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_severity_defaults_to_active(self, single_notify_handler, mock_hass):
        """Unknown severity should default to active push level."""
        payload = {**SAMPLE_ALERT_DISPATCH, "severity": "banana"}
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "active"

    @pytest.mark.asyncio
    async def test_message_without_glucose_is_just_rule_name(self, single_notify_handler, mock_hass):
        """When no glucose value, message should be just the rule name."""
        payload = {
            "excursionId": "exc-003",
            "ruleName": "Sensor Expired",
            "severity": "info",
        }
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        message = notify_calls[0][0][2]["message"]
        assert message == "Sensor Expired"


# ---------------------------------------------------------------------------
# 7. Full lifecycle: dispatch -> acknowledge -> resolve
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """Test the complete lifecycle of an alert through all stages."""

    @pytest.mark.asyncio
    async def test_dispatch_then_acknowledge_then_resolve(self, single_notify_handler, mock_hass):
        """Walk an alert through its entire lifecycle."""
        # Step 1: Dispatch
        await single_notify_handler.handle_alert_dispatch(SAMPLE_ALERT_DISPATCH)

        assert mock_hass.bus.async_fire.call_count == 1
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT

        # Step 2: Acknowledge
        mock_hass.bus.async_fire.reset_mock()
        mock_hass.services.async_call.reset_mock()

        await single_notify_handler.handle_alert_acknowledged(SAMPLE_ALERT_ACKNOWLEDGED)

        assert mock_hass.bus.async_fire.call_count == 1
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT_ACKNOWLEDGED

        # Persistent notification updated with ack info
        pn_calls = [
            c for c in mock_hass.services.async_call.call_args_list
            if c[0][0] == "persistent_notification"
        ]
        assert pn_calls[0][0][1] == "create"
        assert "(Acknowledged)" in pn_calls[0][0][2]["title"]

        # Step 3: Resolve
        mock_hass.bus.async_fire.reset_mock()
        mock_hass.services.async_call.reset_mock()

        await single_notify_handler.handle_alert_resolved(SAMPLE_ALERT_RESOLVED)

        assert mock_hass.bus.async_fire.call_count == 1
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT_RESOLVED

        pn_calls = [
            c for c in mock_hass.services.async_call.call_args_list
            if c[0][0] == "persistent_notification"
        ]
        assert pn_calls[0][0][1] == "dismiss"

    @pytest.mark.asyncio
    async def test_signalr_callback_to_alert_handler_pipeline(self, mock_hass):
        """Simulate the full SignalR -> alert handler chain."""
        handler = NocturneAlertHandler(
            hass=mock_hass,
            notify_services=["notify.mobile_app_phone"],
            instance_name="test-hub",
        )

        signalr_client = NocturneSignalRClient(
            hass=mock_hass,
            instance_url="https://nocturne.example.com",
            access_token="test-token",
            instance_id="test-client-id",
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=handler.handle_alert_dispatch,
            on_alert_resolved=handler.handle_alert_resolved,
            on_alert_acknowledged=handler.handle_alert_acknowledged,
        )

        # Simulate SignalR delivering an alert (wrapped in list as SignalR does)
        await signalr_client._handle_alert_dispatch([SAMPLE_ALERT_DISPATCH])

        # Verify the full chain executed
        mock_hass.bus.async_fire.assert_called_once()
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT

        calls = mock_hass.services.async_call.call_args_list
        assert any(c[0][0] == "persistent_notification" for c in calls)
        assert any(c[0][0] == "notify" for c in calls)
