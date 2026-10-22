"""Shared fixtures for Nocturne tests."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.setup import async_setup_component
from nocturne_py import (
    ApsSnapshot,
    DeviceAgeInfo,
    GlucoseDirection,
    PumpSnapshot,
    SensorGlucose,
    UploaderSnapshot,
)
from nocturne_py.models import DailySummaryDay, ProfileSummary
from pysignalr.client import SignalRClient
from pysignalr.messages import CompletionMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.nocturne.alert_handler import NocturneAlertHandler
from custom_components.nocturne.config_flow import NocturneOAuth2Implementation
from custom_components.nocturne.const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
    DOMAIN,
)
from custom_components.nocturne.signalr import NocturneSignalRClient

INSTANCE_URL = "https://nocturne.example.com"
AUTHORIZE_URL = f"{INSTANCE_URL}/oauth/authorize"
TOKEN_URL = f"{INSTANCE_URL}/oauth/token"
CLIENT_ID = "stored-client-id"


def make_entry(
    instance_url: str = INSTANCE_URL,
    client_id: str = CLIENT_ID,
    unique_id: str | None = None,
    expires_in: float = -10,
    title: str = "Nocturne",
    access_token: str = "old-access",
    refresh_token: str | None = None,
    options: dict | None = None,
    **data: object,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=unique_id or instance_url,
        options=options or {},
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": access_token,
                "refresh_token": refresh_token or f"refresh-{client_id}",
                "expires_at": time.time() + expires_in,
                "expires_in": 3600,
            },
            CONF_INSTANCE_URL: instance_url,
            CONF_CLIENT_ID: client_id,
            CONF_AUTHORIZE_URL: f"{instance_url}/oauth/authorize",
            CONF_TOKEN_URL: f"{instance_url}/oauth/token",
            **data,
        },
    )


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry in HA that has not been set up, whose token is due for refresh."""
    entry = make_entry()
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def oauth_session(hass: HomeAssistant, entry: MockConfigEntry) -> OAuth2Session:
    return OAuth2Session(hass, entry, NocturneOAuth2Implementation.from_entry_data(hass, entry.data))


def issue_tokens(aioclient_mock: AiohttpClientMocker, *tokens: str, expires_in: int) -> None:
    """Answer each refresh at the token endpoint with the next access token."""
    issued = iter(tokens)

    async def respond(method, url, data):
        return AiohttpClientMockResponse(
            method, url, json={"access_token": next(issued), "expires_in": expires_in}
        )

    aioclient_mock.post(TOKEN_URL, side_effect=respond)


@pytest.fixture
async def reauth_hass(hass: HomeAssistant, enable_custom_integrations: None) -> HomeAssistant:
    hass.config.external_url = "https://ha.example.com"
    assert await async_setup_component(hass, "auth", {})
    return hass


@pytest.fixture
async def my_hass(hass: HomeAssistant, enable_custom_integrations: None) -> HomeAssistant:
    hass.config.external_url = None
    hass.config.components.add("my")
    assert await async_setup_component(hass, "auth", {})
    return hass


def make_api_client() -> AsyncMock:
    """AsyncMock of NocturneApiClient with sensible defaults."""
    client = AsyncMock()
    client.validate_connection.return_value = True
    client.get_latest_glucose.return_value = SensorGlucose(
        mgdl=120, direction=GlucoseDirection("Flat")
    )
    client.get_latest_aps_snapshot.return_value = ApsSnapshot(iob=1.5)
    client.get_latest_pump_snapshot.return_value = PumpSnapshot(
        reservoir=150.0, battery_percent=80
    )
    client.get_latest_uploader_snapshot.return_value = UploaderSnapshot(battery=90)
    client.get_profile_summary.return_value = ProfileSummary()
    client.get_daily_summary.return_value = DailySummaryDay(time_in_range_percent=72.5)
    client.get_sensor_age.return_value = DeviceAgeInfo(days=5)
    client.create_glucose.return_value = SensorGlucose(mgdl=120)
    client.create_meter_glucose.return_value = MagicMock()
    client.create_carb_intake.return_value = MagicMock()
    client.create_bolus.return_value = MagicMock()
    client.create_state_span.return_value = MagicMock()
    return client


@dataclass
class Stubs:
    """The API and SignalR clients each set-up entry was given, keyed by instance URL."""

    clients: dict[str, AsyncMock] = field(default_factory=dict)
    signalr: dict[str, MagicMock] = field(default_factory=dict)


@pytest.fixture
def stubs(reauth_hass: HomeAssistant) -> Stubs:
    """Let the real setup run with a fresh stubbed API and SignalR client per entry."""
    created = Stubs()

    def api_client(base_url: str, **_: object) -> AsyncMock:
        return created.clients.setdefault(base_url, make_api_client())

    def signalr_client(*, instance_url: str, **callbacks: object) -> MagicMock:
        client = MagicMock()
        client.connected = True
        client.callbacks = callbacks
        client.stop = AsyncMock()
        client.acknowledge = AsyncMock()
        created.signalr[instance_url] = client
        return client

    with (
        patch("custom_components.nocturne.NocturneApiClient", side_effect=api_client),
        patch("custom_components.nocturne.NocturneSignalRClient", side_effect=signalr_client),
    ):
        yield created


async def set_up(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    """Add the entries and set the integration up, which loads every one of them."""
    for entry in entries:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    assert [entry.state for entry in entries] == [ConfigEntryState.LOADED] * len(entries)


def make_signalr_client(hass: HomeAssistant, **overrides: object) -> NocturneSignalRClient:
    """A real SignalR client whose callbacks are AsyncMocks unless overridden."""
    arguments: dict[str, object] = {
        "hass": hass,
        "instance_url": INSTANCE_URL,
        "instance_id": "test-client-id",
        "async_get_token": AsyncMock(return_value="test-token"),
        "on_glucose_reading": AsyncMock(),
        "on_alert_dispatch": AsyncMock(),
        "on_alert_resolved": AsyncMock(),
        "on_alert_acknowledged": AsyncMock(),
    }
    return NocturneSignalRClient(**{**arguments, **overrides})


async def deliver(client: NocturneSignalRClient, event: str, payload: object) -> None:
    """Hand a server event to the handler the client registers for it with pysignalr."""
    handlers: dict[str, object] = {}

    class Hub:
        def on(self, name: str, handler: object) -> None:
            handlers[name] = handler

    client._subscribe_events(Hub())
    await handlers[event](payload)


def make_alert_handler(hass: HomeAssistant, *notify_services: str) -> NocturneAlertHandler:
    return NocturneAlertHandler(hass, list(notify_services), lambda: "test-hub")


class Notifications:
    """The persistent notifications open in HA, and every one that was created or dismissed."""

    def __init__(self) -> None:
        self.open: dict[str, persistent_notification.Notification] = {}
        self.created: list[persistent_notification.Notification] = []
        self.dismissed: list[str] = []

    def record(
        self,
        update_type: persistent_notification.UpdateType,
        notifications: dict[str, persistent_notification.Notification],
    ) -> None:
        if update_type is persistent_notification.UpdateType.ADDED:
            self.open.update(notifications)
            self.created.extend(notifications.values())
        elif update_type is persistent_notification.UpdateType.REMOVED:
            for notification_id in notifications:
                self.open.pop(notification_id, None)
                self.dismissed.append(notification_id)


@pytest.fixture
async def notifications(hass: HomeAssistant) -> Notifications:
    assert await async_setup_component(hass, "persistent_notification", {})
    seen = Notifications()
    persistent_notification.async_register_callback(hass, seen.record)
    return seen


@pytest.fixture
def connect_hub():
    """Connect a Nocturne client to a real pysignalr client whose transport is stubbed.

    The server's completion, when given, is fed back through pysignalr's own message dispatch.
    """

    def connect(client, reply=None, send_error=None):
        hub = SignalRClient("https://nocturne.example.com/hubs/home-assistant")

        sent = []

        async def transport_send(message):
            sent.append((message.target, message.arguments))
            if send_error is not None:
                raise send_error
            if reply is not None:
                asyncio.get_running_loop().call_soon(
                    asyncio.ensure_future,
                    hub._on_message(CompletionMessage(message.invocation_id, **reply)),
                )

        hub._transport.send = transport_send
        hub.sent = sent
        client._client = hub
        client._connected = True
        return hub

    return connect
