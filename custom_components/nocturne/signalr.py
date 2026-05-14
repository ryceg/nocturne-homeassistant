"""Persistent SignalR connection to the Nocturne HomeAssistantHub."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from pysignalr.client import SignalRClient
from homeassistant.core import HomeAssistant

from .const import SIGNALR_HUB_PATH

_LOGGER = logging.getLogger(__name__)

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
    ) -> None:
        self._hass = hass
        self._instance_url = instance_url.rstrip("/")
        self._access_token = access_token
        self._instance_id = instance_id
        self._on_glucose_reading = on_glucose_reading
        self._on_alert_dispatch = on_alert_dispatch
        self._on_alert_resolved = on_alert_resolved
        self._on_alert_acknowledged = on_alert_acknowledged
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
        """Send an acknowledgement for an excursion."""
        if not self._client or not self._connected:
            raise ConnectionError("SignalR client is not connected")
        await self._client.send("Acknowledge", [excursion_id, acknowledged_by])
        _LOGGER.debug("Sent ack for excursion %s by %s", excursion_id, acknowledged_by)

    async def _on_connected(self) -> None:
        self._connected = True
        _LOGGER.info("SignalR connected, subscribing as instance %s", self._instance_id)
        if self._client:
            await self._client.send("Subscribe", [self._instance_id])

    async def _on_disconnected(self) -> None:
        self._connected = False
        _LOGGER.warning("SignalR connection lost, will reconnect automatically")

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
