"""Handles alert events from Nocturne and delivers them via HA services."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
)

_LOGGER = logging.getLogger(__name__)

SEVERITY_PUSH_MAP = {
    "critical": {"push": {"interruption-level": "critical"}},
    "warning": {"push": {"interruption-level": "time-sensitive"}},
    "info": {"push": {"interruption-level": "active"}},
}


class NocturneAlertHandler:
    def __init__(self, hass: HomeAssistant, notify_services: list[str], instance_name: str) -> None:
        self._hass = hass
        self._notify_services = notify_services
        self._instance_name = instance_name

    async def handle_alert_dispatch(self, payload: dict[str, Any]) -> None:
        """Handle alert: fire event, persistent notification, mobile push."""
        excursion_id = payload.get("excursionId", "")
        rule_name = payload.get("ruleName", "Nocturne Alert")
        severity = payload.get("severity", "info")
        glucose_value = payload.get("glucoseValue")
        trend = payload.get("trend")
        message = self._build_message(payload)

        # 1. Fire HA event
        self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT, {
            "excursion_id": excursion_id,
            "rule_name": rule_name,
            "severity": severity,
            "message": message,
            "glucose_value": glucose_value,
            "glucose_unit": "mg/dL",
            "trend": trend,
            "ack_able": payload.get("ackAble", False),
            "channel_id": payload.get("channelId", ""),
            "fired_at": payload.get("readingTimestamp", ""),
        })

        # 2. Persistent notification
        await self._hass.services.async_call(
            "persistent_notification", "create",
            {"title": f"Nocturne: {rule_name}", "message": message, "notification_id": f"nocturne_alert_{excursion_id}"},
        )

        # 3. Mobile push
        push_data = self._map_push_severity(severity)
        for service in self._notify_services:
            service_name = service.removeprefix("notify.")
            await self._hass.services.async_call(
                "notify", service_name,
                {"title": f"Nocturne: {rule_name}", "message": message, "data": push_data},
            )

    async def handle_alert_resolved(self, payload: dict[str, Any]) -> None:
        excursion_id = payload.get("excursionId", "")
        self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT_RESOLVED, {
            "excursion_id": excursion_id,
            "rule_name": payload.get("ruleName", ""),
            "resolved_at": payload.get("resolvedAt", ""),
        })
        await self._hass.services.async_call(
            "persistent_notification", "dismiss",
            {"notification_id": f"nocturne_alert_{excursion_id}"},
        )

    async def handle_alert_acknowledged(self, payload: dict[str, Any]) -> None:
        excursion_id = payload.get("excursionId", "")
        rule_name = payload.get("ruleName", "")
        acked_by = payload.get("acknowledgedBy", "unknown")
        self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT_ACKNOWLEDGED, {
            "excursion_id": excursion_id,
            "rule_name": rule_name,
            "acknowledged_by": acked_by,
            "acknowledged_at": payload.get("acknowledgedAt", ""),
        })
        await self._hass.services.async_call(
            "persistent_notification", "create",
            {"title": f"Nocturne: {rule_name} (Acknowledged)", "message": f"Acknowledged by {acked_by}", "notification_id": f"nocturne_alert_{excursion_id}"},
        )

    def _build_message(self, payload: dict[str, Any]) -> str:
        glucose = payload.get("glucoseValue")
        trend = payload.get("trend", "")
        rule_name = payload.get("ruleName", "Alert")
        if glucose is not None:
            return f"{rule_name}: Glucose is {glucose} mg/dL {trend}"
        return rule_name

    def _map_push_severity(self, severity: str) -> dict[str, Any]:
        return SEVERITY_PUSH_MAP.get(severity, SEVERITY_PUSH_MAP["info"])
