"""Alert payloads shaped exactly as the Nocturne server serialises them over SignalR."""
from __future__ import annotations

from typing import Any

TENANT_ID = "6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
EXCURSION_ID = "0b9f4e52-1c7a-4d3e-a6f8-2e5d7c9b1a34"
OTHER_EXCURSION_ID = "c3a1d8f0-7b2e-4f59-9d46-5a8e0b6c2d17"
INSTANCE_ID = "a41e7c90-3d5b-4b28-8f6a-9c2d1e0b7f53"
OTHER_INSTANCE_ID = "e92b6d14-8a3c-4e75-b1f0-4d7a5c3e9b86"


def alert_dispatch(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "alertType": "threshold",
        "ruleName": "Urgent Low",
        "glucoseValue": 55,
        "trend": None,
        "trendRate": -2.0,
        "readingTimestamp": "2026-05-14T12:00:00Z",
        "excursionId": EXCURSION_ID,
        "instanceId": INSTANCE_ID,
        "tenantId": TENANT_ID,
        "subjectName": "Test User",
        "activeExcursionCount": 1,
        "severity": "critical",
    }
    payload.update(overrides)
    return payload


def alert_dispatch_without_reading(**overrides: Any) -> dict[str, Any]:
    reading_less: dict[str, Any] = {
        "alertType": "staleness",
        "ruleName": "Connection Lost",
        "glucoseValue": None,
        "trendRate": None,
        "severity": "warning",
    }
    reading_less.update(overrides)
    return alert_dispatch(**reading_less)


def alert_resolved(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "excursionId": EXCURSION_ID,
        "tenantId": TENANT_ID,
        "resolvedAt": "2026-05-14T12:30:00Z",
        "reason": "hysteresis",
    }
    payload.update(overrides)
    return payload


def alert_acknowledged_for_excursion(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenantId": TENANT_ID,
        "excursionId": EXCURSION_ID,
        "acknowledgedBy": "homeassistant:test-hub",
        "acknowledgedAt": "2026-05-14T12:15:00Z",
    }
    payload.update(overrides)
    return payload


def alert_acknowledged_tenant_wide(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenantId": TENANT_ID,
        "acknowledgedBy": "homeassistant:test-hub",
        "acknowledgedAt": "2026-05-14T12:15:00Z",
        "excursionCount": 2,
    }
    payload.update(overrides)
    return payload
