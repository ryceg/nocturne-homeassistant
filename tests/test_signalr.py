"""Tests for the SignalR connection manager."""
import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pysignalr.exceptions import AuthorizationError, NegotiationFailure
from websockets.exceptions import ConnectionClosed

from custom_components.nocturne import signalr
from custom_components.nocturne.auth import oauth_token_provider

from .conftest import deliver, issue_tokens, make_signalr_client


@pytest.fixture
def signalr_client(hass):
    return make_signalr_client(hass)


def test_hub_url(signalr_client):
    assert signalr_client.hub_url == "https://nocturne.example.com/hubs/home-assistant"


CALLBACKS = {
    "glucose_reading": "_on_glucose_reading",
    "alert_dispatch": "_on_alert_dispatch",
    "alert_resolved": "_on_alert_resolved",
    "alert_acknowledged": "_on_alert_acknowledged",
}


@pytest.mark.parametrize("event", CALLBACKS)
async def test_each_event_reaches_only_its_own_callback(signalr_client, event):
    payload = {"excursionId": "test-uuid"}

    await deliver(signalr_client, event, payload)

    for name, attribute in CALLBACKS.items():
        callback = getattr(signalr_client, attribute)
        if name == event:
            callback.assert_awaited_once_with(payload)
        else:
            callback.assert_not_awaited()


async def test_handle_unwraps_list(signalr_client):
    """SignalR sends args as a list, so the handler unwraps the first."""
    await deliver(signalr_client, "glucose_reading", [{"value": 120}])

    signalr_client._on_glucose_reading.assert_awaited_once_with({"value": 120})


async def test_a_failing_callback_does_not_stop_later_events(signalr_client):
    signalr_client._on_alert_dispatch.side_effect = RuntimeError("boom")

    await deliver(signalr_client, "alert_dispatch", {"excursionId": "a"})
    await deliver(signalr_client, "alert_dispatch", {"excursionId": "b"})

    assert signalr_client._on_alert_dispatch.await_count == 2


@pytest.mark.asyncio
async def test_acknowledge_raises_when_not_connected(signalr_client):
    with pytest.raises(HomeAssistantError):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["acknowledged", "muted"])
async def test_acknowledge_returns_when_server_completes(signalr_client, connect_hub, outcome):
    hub = connect_hub(signalr_client, reply={"result": outcome})

    await signalr_client.acknowledge("exc-id", "homeassistant:test")

    assert hub.sent == [("Acknowledge", ["exc-id", "homeassistant:test"])]
    assert hub._invocation_handlers == {}


@pytest.mark.asyncio
async def test_acknowledge_raises_on_server_error(signalr_client, connect_hub):
    hub = connect_hub(signalr_client, reply={"error": "Insufficient scope"})

    with pytest.raises(HomeAssistantError, match="Insufficient scope"):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")

    assert hub._invocation_handlers == {}


@pytest.mark.asyncio
async def test_acknowledge_refusal_tells_the_user_to_reauthenticate(signalr_client, connect_hub):
    connect_hub(signalr_client, reply={"error": "Acknowledge requires the alerts.readwrite scope."})

    with pytest.raises(HomeAssistantError, match="reauthenticate"):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")


@pytest.mark.asyncio
async def test_acknowledge_raises_when_excursion_already_closed(signalr_client, connect_hub):
    connect_hub(signalr_client, reply={"result": "closed"})

    with pytest.raises(HomeAssistantError, match="closed"):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")


@pytest.mark.asyncio
async def test_acknowledge_raises_on_timeout(signalr_client, connect_hub, monkeypatch):
    monkeypatch.setattr("custom_components.nocturne.signalr.ACKNOWLEDGE_TIMEOUT_SECONDS", 0.01)
    hub = connect_hub(signalr_client)

    with pytest.raises(HomeAssistantError, match="in time"):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")

    assert hub._invocation_handlers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_error", [RuntimeError("Connection is closed"), ConnectionClosed(None, None)]
)
async def test_acknowledge_raises_when_connection_drops_during_send(
    signalr_client, connect_hub, send_error
):
    hub = connect_hub(signalr_client, send_error=send_error)

    with pytest.raises(HomeAssistantError, match="not connected"):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")

    assert hub._invocation_handlers == {}


@pytest.mark.asyncio
async def test_disconnect_callback_called_on_disconnected(hass):
    """on_disconnected callback is invoked when _on_disconnected fires."""
    on_disconnect = AsyncMock()
    client = make_signalr_client(hass, on_disconnected=on_disconnect)
    client._connected = True

    await client._on_disconnected()

    assert client._connected is False
    on_disconnect.assert_awaited_once()


class FakeSignalRClient:
    """Stands in for pysignalr; each instance plays the next scripted attempt."""

    def __init__(self, script, url, headers, retry_count, ssl):
        self.headers = headers
        self.ssl = ssl
        self.sent: list = []
        self._script = script
        self._open_cb = None
        self._close_cb = None

    def on(self, event, callback):
        pass

    def on_open(self, callback):
        self._open_cb = callback

    def on_close(self, callback):
        self._close_cb = callback

    async def send(self, method, args):
        self.sent.append((method, args))

    async def run(self):
        step = self._script.pop(0) if self._script else "stay"
        if isinstance(step, NegotiationFailure):
            await self._close_cb()
            raise step
        if isinstance(step, BaseException):
            raise step
        await self._open_cb()
        if isinstance(step, tuple):
            raise step[1]
        if step == "drop":
            await self._close_cb()
        await asyncio.Event().wait()


@pytest.fixture
def fake_pysignalr(monkeypatch):
    """Patch pysignalr with scripted clients and remove the reconnect delay."""
    script: list = []
    instances: list[FakeSignalRClient] = []

    def factory(**kwargs):
        client = FakeSignalRClient(script, **kwargs)
        instances.append(client)
        return client

    monkeypatch.setattr(signalr, "SignalRClient", factory)
    monkeypatch.setattr(signalr, "RECONNECT_INITIAL_DELAY", 0)
    monkeypatch.setattr(signalr, "RECONNECT_MAX_DELAY", 0)
    return script, instances


def _token_sequence():
    count = 0

    async def _next_token() -> str:
        nonlocal count
        count += 1
        return f"token-{count}"

    return AsyncMock(side_effect=_next_token)


def _client(hass, async_get_token):
    return make_signalr_client(hass, async_get_token=async_get_token)


async def _until(predicate) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def test_reconnect_after_unauthorised_attempt_uses_fresh_token(
    hass, entry, fake_pysignalr
):
    script, instances = fake_pysignalr
    script.append(AuthorizationError())
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: client.connected)

    assert [c.headers["Authorization"] for c in instances] == [
        "Bearer token-1",
        "Bearer token-2",
    ]
    assert instances[-1].sent == [("Subscribe", ["test-client-id"])]
    await client.stop()


async def test_dropped_connection_reconnects_with_fresh_token(
    hass, entry, fake_pysignalr
):
    script, instances = fake_pysignalr
    script.append("drop")
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: len(instances) == 2 and client.connected)

    assert instances[0].headers["Authorization"] == "Bearer token-1"
    assert instances[1].headers["Authorization"] == "Bearer token-2"
    assert instances[1].sent == [("Subscribe", ["test-client-id"])]
    await client.stop()


async def test_keeps_retrying_past_old_retry_budget(hass, entry, fake_pysignalr):
    script, instances = fake_pysignalr
    script.extend(NegotiationFailure() for _ in range(25))
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: client.connected)

    assert len(instances) == 26
    assert instances[-1].headers["Authorization"] == "Bearer token-26"
    await client.stop()


async def test_refused_token_refresh_keeps_retrying(hass, entry, fake_pysignalr):
    _, instances = fake_pysignalr
    get_token = AsyncMock(side_effect=[HomeAssistantError("refused"), "token-ok"])
    client = _client(hass, get_token)

    client.start(entry)
    await _until(lambda: client.connected)

    assert [c.headers["Authorization"] for c in instances] == ["Bearer token-ok"]
    await client.stop()


async def test_stop_ends_the_loop(hass, entry, fake_pysignalr):
    _, instances = fake_pysignalr
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: client.connected)
    await client.stop()
    for _ in range(10):
        await asyncio.sleep(0)

    assert client._task.done()
    assert not client.connected
    assert len(instances) == 1


async def test_the_loop_is_an_entry_background_task(hass, entry, fake_pysignalr):
    client = _client(hass, _token_sequence())
    client.start(entry)
    await _until(lambda: client.connected)

    await entry._async_process_on_unload(hass)

    assert client._task.cancelled()


async def test_every_reconnect_refreshes_an_expired_oauth_token(
    hass, entry, oauth_session, aioclient_mock, fake_pysignalr
):
    script, instances = fake_pysignalr
    script.append("drop")
    issue_tokens(aioclient_mock, "oauth-1", "oauth-2", expires_in=0)
    client = _client(hass, oauth_token_provider(oauth_session))

    client.start(entry)
    await _until(lambda: len(instances) == 2 and client.connected)

    assert [c.headers["Authorization"] for c in instances] == ["Bearer oauth-1", "Bearer oauth-2"]
    await client.stop()


async def test_https_hub_uses_home_assistant_ssl_context(hass, entry, fake_pysignalr):
    _, instances = fake_pysignalr
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: client.connected)

    assert instances[0].ssl is signalr.get_default_context()
    await client.stop()


@pytest.mark.parametrize(
    "error", [RuntimeError("Connection is closed"), AttributeError("unexpected")]
)
async def test_error_after_open_does_not_end_the_loop(
    hass, entry, fake_pysignalr, error
):
    script, instances = fake_pysignalr
    script.append(("open", error))
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: len(instances) == 2 and client.connected)

    assert instances[1].headers["Authorization"] == "Bearer token-2"
    await client.stop()


@pytest.fixture
def recorded_delays(monkeypatch):
    """Record each reconnect delay and return without waiting."""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args):
        if delay:
            delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(signalr.asyncio, "sleep", _sleep)
    return delays


async def _delays_for(hass, entry, fake_pysignalr, monkeypatch, steps, delays):
    script, instances = fake_pysignalr
    monkeypatch.setattr(signalr, "RECONNECT_INITIAL_DELAY", 1)
    monkeypatch.setattr(signalr, "RECONNECT_MAX_DELAY", 8)
    script.extend(steps)
    client = _client(hass, _token_sequence())

    client.start(entry)
    await _until(lambda: len(instances) == len(steps) + 1 and client.connected)
    await client.stop()
    return delays


async def test_backoff_doubles_up_to_the_cap(
    hass, entry, fake_pysignalr, monkeypatch, recorded_delays
):
    steps = [NegotiationFailure() for _ in range(6)]

    delays = await _delays_for(
        hass, entry, fake_pysignalr, monkeypatch, steps, recorded_delays
    )

    assert delays == [1, 2, 4, 8, 8, 8]


async def test_backoff_resets_after_a_stable_connection(
    hass, entry, fake_pysignalr, monkeypatch, recorded_delays
):
    monkeypatch.setattr(signalr, "STABLE_CONNECTION_SECONDS", 0)
    steps = [NegotiationFailure(), NegotiationFailure(), NegotiationFailure(), "drop"]
    steps.append(NegotiationFailure())

    delays = await _delays_for(
        hass, entry, fake_pysignalr, monkeypatch, steps, recorded_delays
    )

    assert delays == [1, 2, 4, 1, 2]


async def test_backoff_keeps_growing_after_a_short_connection(
    hass, entry, fake_pysignalr, monkeypatch, recorded_delays
):
    steps = [NegotiationFailure(), NegotiationFailure(), NegotiationFailure(), "drop"]
    steps.append(NegotiationFailure())

    delays = await _delays_for(
        hass, entry, fake_pysignalr, monkeypatch, steps, recorded_delays
    )

    assert delays == [1, 2, 4, 8, 8]


async def test_outage_warns_once_and_reconnect_is_logged(
    hass, entry, fake_pysignalr, caplog
):
    script, instances = fake_pysignalr
    script.extend(NegotiationFailure() for _ in range(5))
    client = _client(hass, _token_sequence())
    caplog.set_level(logging.DEBUG, logger=signalr.__name__)

    client.start(entry)
    await _until(lambda: client.connected)
    await client.stop()

    failures = [r for r in caplog.records if "connection failed" in r.getMessage()]
    assert [r.levelno for r in failures] == [logging.WARNING] + [logging.DEBUG] * 4
    assert any(
        r.levelno == logging.INFO and "SignalR reconnected" in r.getMessage()
        for r in caplog.records
    )
