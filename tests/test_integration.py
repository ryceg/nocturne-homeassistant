"""The pipeline from SignalR callbacks to what Home Assistant shows and sends."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from nocturne_py import ApsSnapshot, GlucoseDirection, SensorGlucose
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.nocturne.const import (
    CONF_NOTIFY_SERVICES,
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
    GLUCOSE_UPDATE_INTERVAL_SECONDS,
)
from tests import fixtures

from .conftest import (
    INSTANCE_URL,
    Notifications,
    Stubs,
    deliver,
    make_alert_handler,
    make_entry,
    make_signalr_client,
    set_up,
)

NOTIFICATION_ID = f"nocturne_alert_{fixtures.EXCURSION_ID}"


@pytest.fixture
async def entry(
    reauth_hass: HomeAssistant, stubs: Stubs, notifications: Notifications
) -> MockConfigEntry:
    entry = make_entry(expires_in=3600, options={CONF_NOTIFY_SERVICES: ["mobile_app_phone"]})
    await set_up(reauth_hass, entry)
    return entry


def callback(stubs: Stubs, name: str):
    return stubs.signalr[INSTANCE_URL].callbacks[name]


async def test_the_signalr_client_unwraps_an_alert_and_the_handler_delivers_it(
    hass: HomeAssistant, notifications: Notifications
):
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    events = async_capture_events(hass, EVENT_NOCTURNE_ALERT)
    handler = make_alert_handler(hass, "notify.mobile_app_phone")
    client = make_signalr_client(hass, on_alert_dispatch=handler.handle_alert_dispatch)

    await deliver(client, "alert_dispatch", [fixtures.alert_dispatch()])
    await hass.async_block_till_done()

    assert [event.data["excursion_id"] for event in events] == [fixtures.EXCURSION_ID]
    assert list(notifications.open) == [NOTIFICATION_ID]
    assert len(phone) == 1


class TestAnEntryWiredToSignalR:
    async def test_an_alert_walks_from_dispatch_through_acknowledgement_to_resolution(
        self, reauth_hass, stubs, entry, notifications
    ):
        phone = async_mock_service(reauth_hass, "notify", "mobile_app_phone")
        dispatched = async_capture_events(reauth_hass, EVENT_NOCTURNE_ALERT)
        acknowledged = async_capture_events(reauth_hass, EVENT_NOCTURNE_ALERT_ACKNOWLEDGED)
        resolved = async_capture_events(reauth_hass, EVENT_NOCTURNE_ALERT_RESOLVED)

        await callback(stubs, "on_alert_dispatch")(fixtures.alert_dispatch())
        await reauth_hass.async_block_till_done()
        assert len(dispatched) == 1
        assert len(phone) == 1
        assert phone[0].data["title"] == "Nocturne (nocturne.example.com): Urgent Low"
        assert list(notifications.open) == [NOTIFICATION_ID]

        await callback(stubs, "on_alert_acknowledged")(fixtures.alert_acknowledged_for_excursion())
        await reauth_hass.async_block_till_done()
        assert len(acknowledged) == 1
        assert "(Acknowledged)" in notifications.open[NOTIFICATION_ID]["title"]

        await callback(stubs, "on_alert_resolved")(fixtures.alert_resolved())
        await reauth_hass.async_block_till_done()
        assert len(resolved) == 1
        assert notifications.open == {}

    async def test_a_glucose_push_refreshes_the_reading(self, reauth_hass, stubs, entry):
        stubs.clients[INSTANCE_URL].get_latest_glucose.return_value = SensorGlucose(
            mgdl=64, direction=GlucoseDirection("FortyFiveDown")
        )

        await callback(stubs, "on_glucose_reading")({"mgdl": 1})
        await reauth_hass.async_block_till_done()

        assert reauth_hass.states.get("sensor.nocturne_current_glucose").state == "64"
        assert reauth_hass.states.get("sensor.nocturne_glucose_trend").state == "FortyFiveDown"

    async def test_polling_carries_on_after_a_glucose_push(self, reauth_hass, stubs, entry):
        await callback(stubs, "on_glucose_reading")({"mgdl": 1})
        await reauth_hass.async_block_till_done()
        stubs.clients[INSTANCE_URL].get_latest_aps_snapshot.return_value = ApsSnapshot(iob=3.5)

        async_fire_time_changed(
            reauth_hass,
            dt_util.utcnow() + timedelta(seconds=GLUCOSE_UPDATE_INTERVAL_SECONDS + 1),
        )
        await reauth_hass.async_block_till_done()

        assert reauth_hass.states.get("sensor.nocturne_insulin_on_board").state == "3.5"
