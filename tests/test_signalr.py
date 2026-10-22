"""Tests for the SignalR connection manager."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from websockets.exceptions import ConnectionClosed

from custom_components.nocturne.signalr import NocturneSignalRClient


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    return hass


@pytest.fixture
def signalr_client(mock_hass):
    return NocturneSignalRClient(
        hass=mock_hass,
        instance_url="https://nocturne.example.com",
        access_token="test-token",
        instance_id="test-client-id",
        on_glucose_reading=AsyncMock(),
        on_alert_dispatch=AsyncMock(),
        on_alert_resolved=AsyncMock(),
        on_alert_acknowledged=AsyncMock(),
    )


def test_hub_url(signalr_client):
    assert signalr_client.hub_url == "https://nocturne.example.com/hubs/home-assistant"


@pytest.mark.asyncio
async def test_on_glucose_reading_callback(signalr_client):
    test_data = {"value": 120, "trend": "Flat"}
    await signalr_client._handle_glucose_reading(test_data)
    signalr_client._on_glucose_reading.assert_awaited_once_with(test_data)


@pytest.mark.asyncio
async def test_on_alert_dispatch_callback(signalr_client):
    test_payload = {"excursionId": "test-uuid", "ruleName": "Urgent Low", "severity": "critical"}
    await signalr_client._handle_alert_dispatch(test_payload)
    signalr_client._on_alert_dispatch.assert_awaited_once_with(test_payload)


@pytest.mark.asyncio
async def test_on_alert_resolved_callback(signalr_client):
    test_payload = {"excursionId": "test-uuid"}
    await signalr_client._handle_alert_resolved(test_payload)
    signalr_client._on_alert_resolved.assert_awaited_once_with(test_payload)


@pytest.mark.asyncio
async def test_on_alert_acknowledged_callback(signalr_client):
    test_payload = {"excursionId": "test-uuid", "acknowledgedBy": "homeassistant:test"}
    await signalr_client._handle_alert_acknowledged(test_payload)
    signalr_client._on_alert_acknowledged.assert_awaited_once_with(test_payload)


@pytest.mark.asyncio
async def test_handle_unwraps_list(signalr_client):
    """SignalR sends args as a list — handler should unwrap."""
    test_data = [{"value": 120}]
    await signalr_client._handle_glucose_reading(test_data)
    signalr_client._on_glucose_reading.assert_awaited_once_with({"value": 120})


@pytest.mark.asyncio
async def test_acknowledge_raises_when_not_connected(signalr_client):
    with pytest.raises(HomeAssistantError):
        await signalr_client.acknowledge("exc-id", "homeassistant:test")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["acknowledged", "muted"])
async def test_acknowledge_returns_when_server_completes(signalr_client, connect_hub, outcome):
    hub = connect_hub(signalr_client, reply={"result": outcome})

    await signalr_client.acknowledge("exc-id", "homeassistant:test")

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
async def test_disconnect_callback_called_on_disconnected(mock_hass):
    """on_disconnected callback is invoked when _on_disconnected fires."""
    on_disconnect = AsyncMock()
    client = NocturneSignalRClient(
        hass=mock_hass,
        instance_url="https://nocturne.example.com",
        access_token="test-token",
        instance_id="test-client-id",
        on_glucose_reading=AsyncMock(),
        on_alert_dispatch=AsyncMock(),
        on_alert_resolved=AsyncMock(),
        on_alert_acknowledged=AsyncMock(),
        on_disconnected=on_disconnect,
    )
    client._connected = True

    await client._on_disconnected()

    assert client._connected is False
    on_disconnect.assert_awaited_once()
