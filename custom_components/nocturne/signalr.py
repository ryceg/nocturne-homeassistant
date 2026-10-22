"""Persistent SignalR connection to the Nocturne HomeAssistantHub."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.ssl import get_default_context
from pysignalr.client import SignalRClient
from pysignalr.exceptions import HubError
from pysignalr.messages import CompletionMessage
from websockets.exceptions import ConnectionClosed, WebSocketException

from .const import SIGNALR_HUB_PATH

_LOGGER = logging.getLogger(__name__)

NOT_CONNECTED_MESSAGE = "Cannot acknowledge the alert: not connected to Nocturne"
ACKNOWLEDGE_TIMEOUT_SECONDS = 10
ACKNOWLEDGE_SUCCESS_OUTCOMES = frozenset({"acknowledged", "muted"})

AsyncCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
TokenProvider = Callable[[], Awaitable[str]]

RECONNECT_INITIAL_DELAY = 1.0
RECONNECT_MAX_DELAY = 300.0
STABLE_CONNECTION_SECONDS = 60.0

_CONNECTION_ERRORS = (
    HubError,
    WebSocketException,
    ClientError,
    OSError,
    TimeoutError,
    RuntimeError,
    ValueError,
    KeyError,
    HomeAssistantError,
)


class NocturneSignalRClient:
    """Manages a persistent SignalR connection to the Nocturne API."""

    def __init__(
        self,
        hass: HomeAssistant,
        instance_url: str,
        instance_id: str,
        async_get_token: TokenProvider,
        on_glucose_reading: AsyncCallback,
        on_alert_dispatch: AsyncCallback,
        on_alert_resolved: AsyncCallback,
        on_alert_acknowledged: AsyncCallback,
        on_disconnected: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._hass = hass
        self._instance_url = instance_url.rstrip("/")
        self._instance_id = instance_id
        self._async_get_token = async_get_token
        self._on_glucose_reading = on_glucose_reading
        self._on_alert_dispatch = on_alert_dispatch
        self._on_alert_resolved = on_alert_resolved
        self._on_alert_acknowledged = on_alert_acknowledged
        self._on_disconnected_cb = on_disconnected
        self._client: SignalRClient | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._connected_at: float | None = None
        self._failing = False
        self._closed = asyncio.Event()

    @property
    def hub_url(self) -> str:
        return f"{self._instance_url}{SIGNALR_HUB_PATH}"

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self, entry: ConfigEntry) -> None:
        """Keep the connection up for as long as the entry is loaded."""
        self._task = entry.async_create_background_task(
            self._hass, self._run(), name=f"nocturne_signalr_{entry.entry_id}"
        )
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

    async def _run(self) -> None:
        delay = RECONNECT_INITIAL_DELAY
        while True:
            self._connected_at = None
            try:
                await self._connect_once()
            except _CONNECTION_ERRORS as err:
                log = _LOGGER.debug if self._failing else _LOGGER.warning
                log("SignalR connection failed: %r", err)
                self._failing = True
            except Exception:
                _LOGGER.exception("Unexpected SignalR error")
                self._failing = True
            if (
                self._connected_at is not None
                and time.monotonic() - self._connected_at >= STABLE_CONNECTION_SECONDS
            ):
                delay = RECONNECT_INITIAL_DELAY
            _LOGGER.debug("Reconnecting to SignalR in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_once(self) -> None:
        token = await self._async_get_token()
        # pysignalr reconnects internally with the token and connection ID it started with,
        # so each attempt gets a new client and ends when that connection closes.
        client = SignalRClient(
            url=self.hub_url,
            headers={"Authorization": f"Bearer {token}"},
            retry_count=1,
            ssl=get_default_context() if self.hub_url.startswith("https://") else None,
        )
        client.on("glucose_reading", self._handle_glucose_reading)
        client.on("alert_dispatch", self._handle_alert_dispatch)
        client.on("alert_resolved", self._handle_alert_resolved)
        client.on("alert_acknowledged", self._handle_alert_acknowledged)
        client.on_open(self._on_connected)
        client.on_close(self._on_disconnected)
        self._client = client
        self._closed.clear()

        run = asyncio.create_task(client.run())
        closed = asyncio.create_task(self._closed.wait())
        try:
            await asyncio.wait((run, closed), return_when=asyncio.FIRST_COMPLETED)
        finally:
            run.cancel()
            closed.cancel()
            await asyncio.gather(run, closed, return_exceptions=True)
            self._connected = False
        if run.done() and not run.cancelled() and (err := run.exception()) is not None:
            raise err

    async def _on_connected(self) -> None:
        self._connected = True
        self._connected_at = time.monotonic()
        _LOGGER.info(
            "SignalR %s, subscribing as instance %s",
            "reconnected" if self._failing else "connected",
            self._instance_id,
        )
        self._failing = False
        if self._client:
            await self._client.send("Subscribe", [self._instance_id])

    async def _on_disconnected(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._closed.set()
        if not was_connected:
            return
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
