"""Tests for the alert event handler."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nocturne import alert_handler
from custom_components.nocturne.alert_handler import NocturneAlertHandler
from tests import fixtures


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
        display_name=lambda: "test-hub",
    )


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(alert_handler, "monotonic", lambda: now[0])
    return now


def _calls(mock_hass, domain):
    return [c for c in mock_hass.services.async_call.call_args_list if c[0][0] == domain]


def _dismissed_ids(mock_hass):
    return [c[0][2]["notification_id"] for c in _calls(mock_hass, "persistent_notification") if c[0][1] == "dismiss"]


def _notification_id(excursion_id):
    return f"nocturne_alert_{excursion_id}"


@pytest.mark.asyncio
async def test_dispatch_fires_ha_event(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    mock_hass.bus.async_fire.assert_called_once()
    call_args = mock_hass.bus.async_fire.call_args
    assert call_args[0][0] == "nocturne_alert"
    assert call_args[0][1]["excursion_id"] == fixtures.EXCURSION_ID
    assert call_args[0][1]["severity"] == "critical"
    assert call_args[0][1]["glucose_value"] == 55


@pytest.mark.asyncio
async def test_dispatch_creates_persistent_notification(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    pn_calls = _calls(mock_hass, "persistent_notification")
    assert len(pn_calls) == 1
    assert pn_calls[0][0][1] == "create"
    assert pn_calls[0][0][2]["notification_id"] == _notification_id(fixtures.EXCURSION_ID)


@pytest.mark.asyncio
async def test_dispatch_sends_mobile_push(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    notify_calls = _calls(mock_hass, "notify")
    assert len(notify_calls) == 1
    assert notify_calls[0][0][1] == "mobile_app_phone"


@pytest.mark.asyncio
async def test_dispatch_critical_push_data(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    push_data = _calls(mock_hass, "notify")[0][0][2]["data"]
    assert push_data["push"]["interruption-level"] == "critical"


@pytest.mark.asyncio
async def test_repeated_dispatch_fires_one_event_and_one_set_of_notifications(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    clock[0] += 1
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())

    assert mock_hass.bus.async_fire.call_count == 1
    assert len(_calls(mock_hass, "persistent_notification")) == 1
    assert len(_calls(mock_hass, "notify")) == 1


@pytest.mark.asyncio
async def test_dispatch_after_the_window_is_handled_again(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    clock[0] += alert_handler.DISPATCH_DEDUPE_WINDOW_SECONDS + 1
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())

    assert mock_hass.bus.async_fire.call_count == 2
    assert len(_calls(mock_hass, "notify")) == 2


@pytest.mark.asyncio
async def test_different_excursion_or_instance_is_not_a_repeat(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    await handler.handle_alert_dispatch(fixtures.alert_dispatch(excursionId=fixtures.OTHER_EXCURSION_ID))
    await handler.handle_alert_dispatch(fixtures.alert_dispatch(instanceId=fixtures.OTHER_INSTANCE_ID))

    assert mock_hass.bus.async_fire.call_count == 3


@pytest.mark.asyncio
async def test_dedupe_memory_is_bounded(handler, mock_hass, clock):
    for index in range(alert_handler.DISPATCH_DEDUPE_MAX_ENTRIES * 2):
        await handler.handle_alert_dispatch(fixtures.alert_dispatch(instanceId=f"instance-{index}"))

    assert len(handler._recent_dispatches) <= alert_handler.DISPATCH_DEDUPE_MAX_ENTRIES


@pytest.mark.asyncio
async def test_dedupe_memory_drops_entries_past_the_window(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    clock[0] += alert_handler.DISPATCH_DEDUPE_WINDOW_SECONDS + 1
    await handler.handle_alert_dispatch(fixtures.alert_dispatch(instanceId=fixtures.OTHER_INSTANCE_ID))

    assert list(handler._recent_dispatches) == [(fixtures.EXCURSION_ID, fixtures.OTHER_INSTANCE_ID)]


@pytest.mark.asyncio
async def test_resolved_dismisses_notification(handler, mock_hass):
    await handler.handle_alert_resolved(fixtures.alert_resolved())
    mock_hass.bus.async_fire.assert_called_once()
    assert mock_hass.bus.async_fire.call_args[0][0] == "nocturne_alert_resolved"
    assert _dismissed_ids(mock_hass) == [_notification_id(fixtures.EXCURSION_ID)]


@pytest.mark.asyncio
async def test_dispatch_notifications_name_the_instance(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())

    titles = [c[0][2]["title"] for c in mock_hass.services.async_call.call_args_list]
    assert titles == ["Nocturne (test-hub): Urgent Low"] * 2


@pytest.mark.asyncio
async def test_excursion_acknowledgement_updates_its_notification(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    mock_hass.services.async_call.reset_mock()

    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_for_excursion())

    assert mock_hass.bus.async_fire.call_args[0][0] == "nocturne_alert_acknowledged"
    pn_calls = _calls(mock_hass, "persistent_notification")
    assert len(pn_calls) == 1
    assert pn_calls[0][0][1] == "create"
    assert pn_calls[0][0][2]["title"] == "Nocturne (test-hub): Urgent Low (Acknowledged)"


@pytest.mark.asyncio
async def test_excursion_acknowledgement_for_an_unknown_excursion_creates_nothing(handler, mock_hass):
    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_for_excursion())

    assert mock_hass.bus.async_fire.call_count == 1
    assert mock_hass.services.async_call.await_count == 0


@pytest.mark.asyncio
async def test_tenant_wide_acknowledgement_dismisses_every_open_notification_and_creates_none(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    await handler.handle_alert_dispatch(
        fixtures.alert_dispatch(excursionId=fixtures.OTHER_EXCURSION_ID, instanceId=fixtures.OTHER_INSTANCE_ID)
    )
    mock_hass.services.async_call.reset_mock()
    mock_hass.bus.async_fire.reset_mock()

    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_tenant_wide())

    assert sorted(_dismissed_ids(mock_hass)) == sorted(
        [_notification_id(fixtures.EXCURSION_ID), _notification_id(fixtures.OTHER_EXCURSION_ID)]
    )
    creates = [c for c in _calls(mock_hass, "persistent_notification") if c[0][1] == "create"]
    assert creates == []
    event_name, event_data = mock_hass.bus.async_fire.call_args[0]
    assert event_name == "nocturne_alert_acknowledged"
    assert event_data["excursion_id"] is None


@pytest.mark.asyncio
async def test_tenant_wide_acknowledgement_leaves_nothing_open(handler, mock_hass, clock):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_tenant_wide())
    mock_hass.services.async_call.reset_mock()

    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_tenant_wide())

    assert mock_hass.services.async_call.await_count == 0


@pytest.mark.asyncio
async def test_resolved_excursion_is_not_dismissed_again_by_tenant_wide_acknowledgement(handler, mock_hass):
    await handler.handle_alert_dispatch(fixtures.alert_dispatch())
    await handler.handle_alert_resolved(fixtures.alert_resolved())
    mock_hass.services.async_call.reset_mock()

    await handler.handle_alert_acknowledged(fixtures.alert_acknowledged_tenant_wide())

    assert _dismissed_ids(mock_hass) == []


@pytest.mark.asyncio
async def test_severity_mapping_warning(handler):
    result = handler._map_push_severity("warning")
    assert result["push"]["interruption-level"] == "time-sensitive"


@pytest.mark.asyncio
async def test_severity_mapping_info(handler):
    result = handler._map_push_severity("info")
    assert result["push"]["interruption-level"] == "active"
