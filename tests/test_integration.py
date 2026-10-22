"""End-to-end integration tests for the alert delivery pipeline.

Tests the full lifecycle: SignalR callbacks -> alert handler -> HA services,
and SignalR callbacks -> glucose coordinator -> data update.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.nocturne.alert_handler import NocturneAlertHandler
from custom_components.nocturne.coordinator import GlucoseCoordinator
from custom_components.nocturne.signalr import NocturneSignalRClient
from custom_components.nocturne.const import (
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
)
from tests import fixtures

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
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT
        assert event_data["excursion_id"] == fixtures.EXCURSION_ID
        assert event_data["rule_name"] == "Urgent Low"
        assert event_data["severity"] == "critical"
        assert event_data["glucose_value"] == 55
        assert event_data["trend"] is None
        assert event_data["fired_at"] == "2026-05-14T12:00:00Z"
        assert set(event_data) == {
            "excursion_id", "rule_name", "severity", "message", "glucose_value", "trend", "fired_at",
        }

    @pytest.mark.asyncio
    async def test_persistent_notification_created(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "create"
        service_data = pn_calls[0][0][2]
        assert "Urgent Low" in service_data["title"]
        assert "55" in service_data["message"]
        assert service_data["notification_id"] == f"nocturne_alert_{fixtures.EXCURSION_ID}"

    @pytest.mark.asyncio
    async def test_mobile_push_sent(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 1
        assert notify_calls[0][0][1] == "mobile_app_phone"

    @pytest.mark.asyncio
    async def test_critical_severity_push_metadata(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "critical"

    @pytest.mark.asyncio
    async def test_all_three_services_called(self, single_notify_handler, mock_hass):
        """Dispatch should produce exactly 3 service calls: persistent_notification + notify."""
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

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
        await single_notify_handler.handle_alert_resolved(fixtures.alert_resolved())

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT_RESOLVED
        assert event_data["excursion_id"] == fixtures.EXCURSION_ID
        assert "rule_name" not in event_data
        assert event_data["resolved_at"] == "2026-05-14T12:30:00Z"

    @pytest.mark.asyncio
    async def test_persistent_notification_dismissed(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_resolved(fixtures.alert_resolved())

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "dismiss"
        assert pn_calls[0][0][2]["notification_id"] == f"nocturne_alert_{fixtures.EXCURSION_ID}"


# ---------------------------------------------------------------------------
# 3. Alert acknowledged flow
# ---------------------------------------------------------------------------

class TestAlertAcknowledgedFlow:
    """Simulate alert_acknowledged and verify event + notification updated."""

    @pytest.mark.asyncio
    async def test_acknowledged_event_fired(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_acknowledged(fixtures.alert_acknowledged_for_excursion())

        mock_hass.bus.async_fire.assert_called_once()
        event_name, event_data = mock_hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_NOCTURNE_ALERT_ACKNOWLEDGED
        assert event_data["excursion_id"] == fixtures.EXCURSION_ID
        assert "rule_name" not in event_data
        assert event_data["acknowledged_by"] == "homeassistant:test-hub"
        assert event_data["acknowledged_at"] == "2026-05-14T12:15:00Z"

    @pytest.mark.asyncio
    async def test_notification_updated_with_ack_info(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())
        mock_hass.services.async_call.reset_mock()

        await single_notify_handler.handle_alert_acknowledged(fixtures.alert_acknowledged_for_excursion())

        calls = mock_hass.services.async_call.call_args_list
        pn_calls = [c for c in calls if c[0][0] == "persistent_notification"]
        assert len(pn_calls) == 1
        assert pn_calls[0][0][1] == "create"
        service_data = pn_calls[0][0][2]
        assert service_data["title"] == "Nocturne: Urgent Low (Acknowledged)"
        assert "homeassistant:test-hub" in service_data["message"]
        assert service_data["notification_id"] == f"nocturne_alert_{fixtures.EXCURSION_ID}"


# ---------------------------------------------------------------------------
# 4. Glucose push flow
# ---------------------------------------------------------------------------

class TestGlucosePushFlow:
    """Simulate glucose_reading via coordinator push and verify it triggers a poll."""

    @pytest.mark.asyncio
    async def test_glucose_push_triggers_refresh(self, mock_hass):
        client = AsyncMock()
        coordinator = GlucoseCoordinator(hass=mock_hass, config_entry=MagicMock(), client=client)
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_handle_glucose_push()

        coordinator.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. Acknowledge service flow (SignalR client)
# ---------------------------------------------------------------------------

class TestAcknowledgeServiceFlow:
    """Verify acknowledge_alert calls SignalR client correctly."""

    @pytest.mark.asyncio
    async def test_acknowledge_invokes_hub_and_awaits_completion(self, mock_hass, connect_hub):
        signalr_client = NocturneSignalRClient(
            hass=mock_hass,
            instance_url="https://nocturne.example.com",
            instance_id="test-client-id",
            async_get_token=AsyncMock(return_value="test-token"),
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=AsyncMock(),
            on_alert_resolved=AsyncMock(),
            on_alert_acknowledged=AsyncMock(),
        )
        hub = connect_hub(signalr_client, reply={"result": "acknowledged"})
        sent = []
        transport_send = hub._transport.send

        async def recording_send(message):
            sent.append((message.target, message.arguments))
            await transport_send(message)

        hub._transport.send = recording_send

        await signalr_client.acknowledge(fixtures.EXCURSION_ID, "homeassistant:test-hub")

        assert sent == [("Acknowledge", [fixtures.EXCURSION_ID, "homeassistant:test-hub"])]

    @pytest.mark.asyncio
    async def test_acknowledge_raises_when_disconnected(self, mock_hass):
        signalr_client = NocturneSignalRClient(
            hass=mock_hass,
            instance_url="https://nocturne.example.com",
            instance_id="test-client-id",
            async_get_token=AsyncMock(return_value="test-token"),
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=AsyncMock(),
            on_alert_resolved=AsyncMock(),
            on_alert_acknowledged=AsyncMock(),
        )
        with pytest.raises(HomeAssistantError):
            await signalr_client.acknowledge(fixtures.EXCURSION_ID, "homeassistant:test-hub")


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for alert dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_without_glucose_value(self, single_notify_handler, mock_hass):
        """Alert dispatch with no glucose value should still work."""
        payload = fixtures.alert_dispatch_without_reading()
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
        await no_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

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
        await multi_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        assert len(notify_calls) == 2
        service_names = {c[0][1] for c in notify_calls}
        assert service_names == {"mobile_app_phone", "mobile_app_tablet"}

    @pytest.mark.asyncio
    async def test_dispatch_warning_severity_push_level(self, single_notify_handler, mock_hass):
        """Warning severity should map to time-sensitive push."""
        payload = fixtures.alert_dispatch(severity="warning")
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "time-sensitive"

    @pytest.mark.asyncio
    async def test_dispatch_info_severity_push_level(self, single_notify_handler, mock_hass):
        """Info severity should map to active push."""
        payload = fixtures.alert_dispatch(severity="info")
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "active"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_severity_defaults_to_active(self, single_notify_handler, mock_hass):
        """Unknown severity should default to active push level."""
        payload = fixtures.alert_dispatch(severity="banana")
        await single_notify_handler.handle_alert_dispatch(payload)

        calls = mock_hass.services.async_call.call_args_list
        notify_calls = [c for c in calls if c[0][0] == "notify"]
        push_data = notify_calls[0][0][2]["data"]
        assert push_data["push"]["interruption-level"] == "active"

    @pytest.mark.asyncio
    async def test_message_with_null_trend_has_no_trend_text(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        message = mock_hass.services.async_call.call_args_list[0][0][2]["message"]
        assert message == "Urgent Low: Glucose is 55 mg/dL"

    @pytest.mark.asyncio
    async def test_message_with_trend_appends_it(self, single_notify_handler, mock_hass):
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch(trend="SingleDown"))

        message = mock_hass.services.async_call.call_args_list[0][0][2]["message"]
        assert message == "Urgent Low: Glucose is 55 mg/dL SingleDown"

    @pytest.mark.asyncio
    async def test_message_without_glucose_is_just_rule_name(self, single_notify_handler, mock_hass):
        """When no glucose value, message should be just the rule name."""
        payload = fixtures.alert_dispatch_without_reading(ruleName="Sensor Expired", severity="info")
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
        await single_notify_handler.handle_alert_dispatch(fixtures.alert_dispatch())

        assert mock_hass.bus.async_fire.call_count == 1
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT

        # Step 2: Acknowledge
        mock_hass.bus.async_fire.reset_mock()
        mock_hass.services.async_call.reset_mock()

        await single_notify_handler.handle_alert_acknowledged(fixtures.alert_acknowledged_for_excursion())

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

        await single_notify_handler.handle_alert_resolved(fixtures.alert_resolved())

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
            instance_id="test-client-id",
            async_get_token=AsyncMock(return_value="test-token"),
            on_glucose_reading=AsyncMock(),
            on_alert_dispatch=handler.handle_alert_dispatch,
            on_alert_resolved=handler.handle_alert_resolved,
            on_alert_acknowledged=handler.handle_alert_acknowledged,
        )

        # Simulate SignalR delivering an alert (wrapped in list as SignalR does)
        await signalr_client._handle_alert_dispatch([fixtures.alert_dispatch()])

        # Verify the full chain executed
        mock_hass.bus.async_fire.assert_called_once()
        assert mock_hass.bus.async_fire.call_args[0][0] == EVENT_NOCTURNE_ALERT

        calls = mock_hass.services.async_call.call_args_list
        assert any(c[0][0] == "persistent_notification" for c in calls)
        assert any(c[0][0] == "notify" for c in calls)
