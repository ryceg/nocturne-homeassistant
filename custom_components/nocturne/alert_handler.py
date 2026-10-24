"""Handles alert events from Nocturne and delivers them via HA services."""
from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    EVENT_NOCTURNE_ALERT,
    EVENT_NOCTURNE_ALERT_ACKNOWLEDGED,
    EVENT_NOCTURNE_ALERT_RESOLVED,
)

_LOGGER = logging.getLogger(__name__)

DISPATCH_DEDUPE_WINDOW_SECONDS = 60
DISPATCH_DEDUPE_MAX_ENTRIES = 128

SEVERITY_PUSH_MAP = {
    "critical": {
        "push": {"interruption-level": "critical"},
        "priority": "high",
        "channel": "nocturne_critical",
    },
    "warning": {
        "push": {"interruption-level": "time-sensitive"},
        "priority": "high",
        "channel": "nocturne_warning",
    },
    "info": {
        "push": {"interruption-level": "active"},
        "priority": "default",
        "channel": "nocturne_info",
    },
}


def _notification_id(excursion_id: str) -> str:
    return f"nocturne_alert_{excursion_id}"


def _acknowledged_excursions(payload: dict[str, Any]) -> list[str]:
    """Servers before nocturne#2639 send a single excursionId, or none for acknowledge-all."""
    if "excursionIds" in payload:
        return payload["excursionIds"]
    legacy = payload.get("excursionId")
    return [legacy] if legacy else []


class NocturneAlertHandler:
    def __init__(self, hass: HomeAssistant, notify_services: list[str], display_name: Callable[[], str]) -> None:
        self._hass = hass
        self._notify_services = notify_services
        self._display_name = display_name
        self._recent_dispatches: dict[tuple[str, str], float] = {}
        self._open_rule_names: dict[str, str] = {}

    def _title_prefix(self) -> str:
        return f"Nocturne ({self._display_name()})"

    async def handle_alert_dispatch(self, payload: dict[str, Any]) -> None:
        """Handle alert: fire event, persistent notification, mobile push."""
        excursion_id = payload["excursionId"]
        if self._is_repeat_dispatch(excursion_id, payload["instanceId"]):
            _LOGGER.debug("Ignoring repeated alert dispatch for excursion %s", excursion_id)
            return

        rule_name = payload["ruleName"]
        severity = payload["severity"]
        message = self._build_message(payload)
        self._open_rule_names[excursion_id] = rule_name

        self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT, {
            "excursion_id": excursion_id,
            "rule_name": rule_name,
            "severity": severity,
            "message": message,
            "glucose_value": payload["glucoseValue"],
            "trend": payload["trend"],
            "fired_at": payload["readingTimestamp"],
        })

        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"{self._title_prefix()}: {rule_name}", "message": message, "notification_id": _notification_id(excursion_id)},
            )
        except Exception:
            _LOGGER.exception("Failed to create persistent notification for %s", excursion_id)

        push_data = self._map_push_severity(severity)
        for service in self._notify_services:
            service_name = service.removeprefix("notify.")
            try:
                await self._hass.services.async_call(
                    "notify", service_name,
                    {"title": f"{self._title_prefix()}: {rule_name}", "message": message, "data": push_data},
                )
            except Exception:
                _LOGGER.exception("Failed to send push to notify.%s", service_name)

    async def handle_alert_resolved(self, payload: dict[str, Any]) -> None:
        excursion_id = payload["excursionId"]
        self._open_rule_names.pop(excursion_id, None)
        self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT_RESOLVED, {
            "excursion_id": excursion_id,
            "alert_rule_id": payload.get("alertRuleId"),
            "rule_name": payload.get("ruleName"),
            "reason": payload.get("reason"),
            "resolved_at": payload["resolvedAt"],
        })
        await self._hass.services.async_call(
            "persistent_notification", "dismiss",
            {"notification_id": _notification_id(excursion_id)},
        )

    async def handle_alert_acknowledged(self, payload: dict[str, Any]) -> None:
        acked_by = payload["acknowledgedBy"]
        for excursion_id in _acknowledged_excursions(payload):
            self._hass.bus.async_fire(EVENT_NOCTURNE_ALERT_ACKNOWLEDGED, {
                "excursion_id": excursion_id,
                "acknowledged_by": acked_by,
                "acknowledged_at": payload["acknowledgedAt"],
            })

            rule_name = self._open_rule_names.get(excursion_id)
            if rule_name is None:
                continue
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": f"{self._title_prefix()}: {rule_name} (Acknowledged)", "message": f"Acknowledged by {acked_by}", "notification_id": _notification_id(excursion_id)},
            )

    def _is_repeat_dispatch(self, excursion_id: str, instance_id: str) -> bool:
        now = monotonic()
        self._recent_dispatches = {
            key: seen_at
            for key, seen_at in self._recent_dispatches.items()
            if now - seen_at < DISPATCH_DEDUPE_WINDOW_SECONDS
        }
        key = (excursion_id, instance_id)
        if key in self._recent_dispatches:
            return True
        self._recent_dispatches[key] = now
        while len(self._recent_dispatches) > DISPATCH_DEDUPE_MAX_ENTRIES:
            del self._recent_dispatches[next(iter(self._recent_dispatches))]
        return False

    def _build_message(self, payload: dict[str, Any]) -> str:
        glucose = payload["glucoseValue"]
        trend = payload["trend"]
        rule_name = payload["ruleName"]
        if glucose is None:
            return rule_name
        message = f"{rule_name}: Glucose is {glucose} mg/dL"
        return f"{message} {trend}" if trend else message

    def _map_push_severity(self, severity: str) -> dict[str, Any]:
        return SEVERITY_PUSH_MAP.get(severity, SEVERITY_PUSH_MAP["info"])
