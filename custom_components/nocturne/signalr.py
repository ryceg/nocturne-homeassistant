"""Persistent SignalR connection to the Nocturne HomeAssistantHub."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pysignalr.client import SignalRClient
from pysignalr.messages import CompletionMessage
from websockets.exceptions import ConnectionClosed

from .const import SIGNALR_HUB_PATH

_LOGGER = logging.getLogger(__name__)

NOT_CONNECTED_MESSAGE = "Cannot acknowledge the alert: not connected to Nocturne"
ACKNOWLEDGE_TIMEOUT_SECONDS = 10
ACKNOWLEDGE_SUCCESS_OUTCOMES = frozenset({"acknowledged", "muted"})

AsyncCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class NocturneSignalRClient:
    """Manages a persistent SignalR connection to the Nocturne API."""

    def __init__(
        self,
        hass: HomeAssistant,
        instance_url: str,
        access_token: str,
        instance_id: str,
        on_glucose_reading: AsyncCallback,
        on_alert_dispatch: AsyncCallback,
        on_alert_resolved: AsyncCallback,
        on_alert_acknowledged: AsyncCallback,
        async_get_token: Callable[[], Coroutine[Any, Any, str]] | None = None,
        on_disconnected: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._hass = hass
        self._instance_url = instance_url.rstrip("/")
        self._access_token = access_token
        self._instance_id = instance_id
        self._on_glucose_reading = on_glucose_reading
        self._on_alert_dispatch = on_alert_dispatch
        self._on_alert_resolved = on_alert_resolved
        self._on_alert_acknowledged = on_alert_acknowledged
        self._async_get_token = async_get_token
        self._on_disconnected_cb = on_disconnected
        self._client: SignalRClient | None = None
        self._task: asyncio.Task | None = None
        self._connected = False

    @property
    def hub_url(self) -> str:
        return f"{self._instance_url}{SIGNALR_HUB_PATH}"

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Start the SignalR connection."""
        self._client = SignalRClient(
            url=self.hub_url,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        self._client.on("glucose_reading", self._handle_glucose_reading)
        self._client.on("alert_dispatch", self._handle_alert_dispatch)
        self._client.on("alert_resolved", self._handle_alert_resolved)
        self._client.on("alert_acknowledged", self._handle_alert_acknowledged)
        self._client.on_open(self._on_connected)
        self._client.on_close(self._on_disconnected)
        self._task = asyncio.create_task(self._client.run())
        _LOGGER.info("SignalR connection started to %s", self.hub_url)

    async def stop(self) -> None:
        """Stop the SignalR connection."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        _LOGGER.info("SignalR connection stopped")

    async def acknowledge(self, excursion_id: str, acknowledged_by: str) -> None:
        """Acknowledge an excursion and wait for the server's verdict."""
        if not self._client or not self._connected:
            raise HomeAssistantError(NOT_CONNECTED_MESSAGE)
        client = self._client
        completion: asyncio.Future[CompletionMessage] = asyncio.get_running_loop().create_future()

        async def on_completion(message: CompletionMessage) -> None:
            if not completion.done():
                completion.set_result(message)

        async def ignore_error(message: CompletionMessage) -> None:
            """Errors reach the caller through on_completion; pysignalr raises without a handler."""

        client.on_error(ignore_error)
        try:
            await client.send(
                "Acknowledge", [excursion_id, acknowledged_by], on_invocation=on_completion
            )
            message = await asyncio.wait_for(completion, ACKNOWLEDGE_TIMEOUT_SECONDS)
        except (RuntimeError, ConnectionClosed) as err:
            raise HomeAssistantError(NOT_CONNECTED_MESSAGE) from err
        except TimeoutError as err:
            raise HomeAssistantError("Nocturne did not answer the acknowledgement in time") from err
        finally:
            # pysignalr keeps the handler until a completion arrives and offers no way to drop it
            for invocation_id, handler in list(client._invocation_handlers.items()):
                if handler is on_completion:
                    del client._invocation_handlers[invocation_id]
        if message.error:
            raise HomeAssistantError(
                f"Nocturne refused the acknowledgement: {str(message.error).rstrip('.')}. "
                "If it names a scope, "
                "reauthenticate the Nocturne integration to grant the alerts.readwrite scope."
            )
        if message.result not in ACKNOWLEDGE_SUCCESS_OUTCOMES:
            raise HomeAssistantError(
                f"Nocturne did not acknowledge the alert: it reported {message.result!r}"
            )
        _LOGGER.debug(
            "Excursion %s acknowledged by %s: %s", excursion_id, acknowledged_by, message.result
        )

    async def _on_connected(self) -> None:
        # Refresh token if callback provided
        if self._async_get_token:
            try:
                self._access_token = await self._async_get_token()
            except (KeyError, ClientError, HomeAssistantError):
                _LOGGER.warning("Failed to refresh token before subscribe")
        self._connected = True
        _LOGGER.info("SignalR connected, subscribing as instance %s", self._instance_id)
        if self._client:
            await self._client.send("Subscribe", [self._instance_id])

    async def _on_disconnected(self) -> None:
        self._connected = False
        _LOGGER.warning("SignalR connection lost, will reconnect automatically")
        if self._on_disconnected_cb:
            await self._on_disconnected_cb()

    async def _handle_glucose_reading(self, raw: Any) -> None:
        try:
            data = raw[0] if isinstance(raw, (list, tuple)) else raw
            await self._on_glucose_reading(data)
        except Exception:
            _LOGGER.exception("Error handling glucose_reading event")

    async def _handle_alert_dispatch(self, raw: Any) -> None:
        try:
            data = raw[0] if isinstance(raw, (list, tuple)) else raw
            await self._on_alert_dispatch(data)
        except Exception:
            _LOGGER.exception("Error handling alert_dispatch event")

    async def _handle_alert_resolved(self, raw: Any) -> None:
        try:
            data = raw[0] if isinstance(raw, (list, tuple)) else raw
            await self._on_alert_resolved(data)
        except Exception:
            _LOGGER.exception("Error handling alert_resolved event")

    async def _handle_alert_acknowledged(self, raw: Any) -> None:
        try:
            data = raw[0] if isinstance(raw, (list, tuple)) else raw
            await self._on_alert_acknowledged(data)
        except Exception:
            _LOGGER.exception("Error handling alert_acknowledged event")
