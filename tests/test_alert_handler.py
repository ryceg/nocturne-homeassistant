"""Tests for the alert event handler, run against a real Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.nocturne import alert_handler
from custom_components.nocturne.alert_handler import NocturneAlertHandler
from custom_components.nocturne.const import (
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
)
from tests import fixtures

from .conftest import Notifications, make_alert_handler

NOTIFICATION_ID = f"nocturne_alert_{fixtures.EXCURSION_ID}"
OTHER_NOTIFICATION_ID = f"nocturne_alert_{fixtures.OTHER_EXCURSION_ID}"


@pytest.fixture
def phone(hass: HomeAssistant) -> list:
    return async_mock_service(hass, "notify", "mobile_app_phone")


@pytest.fixture
def handler(hass: HomeAssistant, notifications: Notifications, phone: list) -> NocturneAlertHandler:
    return make_alert_handler(hass, "notify.mobile_app_phone")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(alert_handler, "monotonic", lambda: now[0])
    return now


async def _dispatch(hass: HomeAssistant, handler: NocturneAlertHandler, **overrides) -> None:
    await handler.handle_alert_dispatch(fixtures.alert_dispatch(**overrides))
    await hass.async_block_till_done()


async def _acknowledge(hass: HomeAssistant, handler: NocturneAlertHandler, payload: dict) -> None:
    await handler.handle_alert_acknowledged(payload)
    await hass.async_block_till_done()


async def _resolve(hass: HomeAssistant, handler: NocturneAlertHandler) -> None:
    await handler.handle_alert_resolved(fixtures.alert_resolved())
    await hass.async_block_till_done()


class TestDispatch:
    async def test_fires_the_alert_event(self, hass, handler):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(hass, handler)

        assert [event.data for event in events] == [
            {
                "excursion_id": fixtures.EXCURSION_ID,
                "rule_name": "Urgent Low",
                "severity": "critical",
                "message": "Urgent Low: Glucose is 55 mg/dL",
                "glucose_value": 55,
                "trend": None,
                "fired_at": "2026-05-14T12:00:00Z",
            }
        ]

    async def test_opens_a_persistent_notification_naming_the_instance(
        self, hass, handler, notifications
    ):
        await _dispatch(hass, handler)

        assert list(notifications.open) == [NOTIFICATION_ID]
        assert notifications.open[NOTIFICATION_ID]["title"] == "Nocturne (test-hub): Urgent Low"
        assert notifications.open[NOTIFICATION_ID]["message"] == "Urgent Low: Glucose is 55 mg/dL"

    async def test_pushes_to_the_phone_naming_the_instance(self, hass, handler, phone):
        await _dispatch(hass, handler)

        assert [call.data["title"] for call in phone] == ["Nocturne (test-hub): Urgent Low"]
        assert [call.data["message"] for call in phone] == ["Urgent Low: Glucose is 55 mg/dL"]

    @pytest.mark.parametrize(
        ("severity", "level", "priority", "channel"),
        [
            ("critical", "critical", "high", "nocturne_critical"),
            ("warning", "time-sensitive", "high", "nocturne_warning"),
            ("info", "active", "default", "nocturne_info"),
            ("banana", "active", "default", "nocturne_info"),
        ],
    )
    async def test_push_urgency_follows_severity(
        self, hass, handler, phone, severity, level, priority, channel
    ):
        await _dispatch(hass, handler, severity=severity)

        assert phone[0].data["data"] == {
            "push": {"interruption-level": level},
            "priority": priority,
            "channel": channel,
        }

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({}, "Urgent Low: Glucose is 55 mg/dL"),
            ({"trend": "SingleDown"}, "Urgent Low: Glucose is 55 mg/dL SingleDown"),
        ],
    )
    async def test_message_carries_the_reading_and_its_trend(
        self, hass, handler, phone, overrides, message
    ):
        await _dispatch(hass, handler, **overrides)

        assert phone[0].data["message"] == message

    async def test_an_alert_without_a_reading_is_just_the_rule_name(
        self, hass, handler, phone, notifications
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await handler.handle_alert_dispatch(
            fixtures.alert_dispatch_without_reading(ruleName="Sensor Expired", severity="info")
        )
        await hass.async_block_till_done()

        assert events[0].data["glucose_value"] is None
        assert events[0].data["message"] == "Sensor Expired"
        assert notifications.open[NOTIFICATION_ID]["message"] == "Sensor Expired"
        assert [call.data["message"] for call in phone] == ["Sensor Expired"]

    async def test_with_no_notify_services_the_event_and_notification_still_happen(
        self, hass, notifications, phone
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(hass, make_alert_handler(hass))

        assert len(events) == 1
        assert list(notifications.open) == [NOTIFICATION_ID]
        assert phone == []

    async def test_every_configured_notify_service_gets_a_push(self, hass, notifications, phone):
        tablet = async_mock_service(hass, "notify", "mobile_app_tablet")

        await _dispatch(
            hass,
            make_alert_handler(hass, "notify.mobile_app_phone", "notify.mobile_app_tablet"),
        )

        assert len(phone) == 1
        assert len(tablet) == 1

    async def test_a_missing_notify_service_does_not_stop_the_others(
        self, hass, notifications, phone
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(
            hass, make_alert_handler(hass, "notify.removed_phone", "notify.mobile_app_phone")
        )

        assert len(events) == 1
        assert list(notifications.open) == [NOTIFICATION_ID]
        assert len(phone) == 1


class TestDuplicateDispatch:
    async def test_a_repeat_fires_one_event_and_one_set_of_notifications(
        self, hass, handler, phone, notifications, clock
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(hass, handler)
        clock[0] += 1
        await _dispatch(hass, handler)

        assert len(events) == 1
        assert len(notifications.created) == 1
        assert len(phone) == 1

    async def test_a_dispatch_after_the_window_is_handled_again(self, hass, handler, phone, clock):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(hass, handler)
        clock[0] += alert_handler.DISPATCH_DEDUPE_WINDOW_SECONDS + 1
        await _dispatch(hass, handler)

        assert len(events) == 2
        assert len(phone) == 2

    async def test_a_different_excursion_or_instance_is_not_a_repeat(self, hass, handler, clock):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)

        await _dispatch(hass, handler)
        await _dispatch(hass, handler, excursionId=fixtures.OTHER_EXCURSION_ID)
        await _dispatch(hass, handler, instanceId=fixtures.OTHER_INSTANCE_ID)

        assert len(events) == 3

    async def test_the_oldest_dispatch_is_forgotten_first_once_there_are_too_many(
        self, hass, handler, clock
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)
        newest = alert_handler.DISPATCH_DEDUPE_MAX_ENTRIES
        for index in range(newest + 1):
            await _dispatch(hass, handler, instanceId=f"instance-{index}")
        handled = len(events)

        await _dispatch(hass, handler, instanceId=f"instance-{newest}")
        assert len(events) == handled

        await _dispatch(hass, handler, instanceId="instance-0")
        assert len(events) == handled + 1


class TestResolved:
    async def test_fires_the_event_and_dismisses_the_notification(
        self, hass, handler, notifications
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT_RESOLVED)
        await _dispatch(hass, handler)

        await _resolve(hass, handler)

        assert [event.data for event in events] == [
            {"excursion_id": fixtures.EXCURSION_ID, "resolved_at": "2026-05-14T12:30:00Z"}
        ]
        assert notifications.open == {}
        assert notifications.dismissed == [NOTIFICATION_ID]


class TestAcknowledged:
    async def test_an_excursion_acknowledgement_fires_the_event(self, hass, handler):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT_ACKNOWLEDGED)

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_for_excursion())

        assert [event.data for event in events] == [
            {
                "excursion_id": fixtures.EXCURSION_ID,
                "acknowledged_by": "homeassistant:test-hub",
                "acknowledged_at": "2026-05-14T12:15:00Z",
            }
        ]

    async def test_an_excursion_acknowledgement_updates_its_notification(
        self, hass, handler, notifications
    ):
        await _dispatch(hass, handler)

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_for_excursion())

        assert list(notifications.open) == [NOTIFICATION_ID]
        assert (
            notifications.open[NOTIFICATION_ID]["title"]
            == "Nocturne (test-hub): Urgent Low (Acknowledged)"
        )
        assert "homeassistant:test-hub" in notifications.open[NOTIFICATION_ID]["message"]

    async def test_acknowledging_an_unknown_excursion_creates_nothing(
        self, hass, handler, notifications
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT_ACKNOWLEDGED)

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_for_excursion())

        assert len(events) == 1
        assert notifications.created == []
        assert notifications.dismissed == []

    async def test_a_tenant_wide_acknowledgement_dismisses_every_open_notification(
        self, hass, handler, notifications, clock
    ):
        events = async_capture_events(hass, EVENT_NOCTURNE_ALERT_ACKNOWLEDGED)
        await _dispatch(hass, handler)
        await _dispatch(
            hass,
            handler,
            excursionId=fixtures.OTHER_EXCURSION_ID,
            instanceId=fixtures.OTHER_INSTANCE_ID,
        )
        created = len(notifications.created)

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_tenant_wide())

        assert notifications.open == {}
        assert sorted(notifications.dismissed) == sorted([NOTIFICATION_ID, OTHER_NOTIFICATION_ID])
        assert len(notifications.created) == created
        assert events[0].data["excursion_id"] is None

    async def test_a_tenant_wide_acknowledgement_forgets_the_excursions_it_dismissed(
        self, hass, handler, notifications, clock
    ):
        await _dispatch(hass, handler)
        await _acknowledge(hass, handler, fixtures.alert_acknowledged_tenant_wide())

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_for_excursion())

        assert notifications.open == {}
        assert len(notifications.created) == 1

    async def test_acknowledging_a_resolved_excursion_creates_nothing(
        self, hass, handler, notifications
    ):
        await _dispatch(hass, handler)
        await _resolve(hass, handler)

        await _acknowledge(hass, handler, fixtures.alert_acknowledged_for_excursion())

        assert notifications.open == {}
        assert len(notifications.created) == 1
