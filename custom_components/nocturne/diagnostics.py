"""Diagnostics for Nocturne."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NocturneConfigEntry
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
)

TO_REDACT = {
    "token",
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_AUTHORIZE_URL,
    CONF_TOKEN_URL,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NocturneConfigEntry
) -> dict[str, Any]:
    """Return diagnostics that describe the entry's health without any glucose or profile data."""
    data = entry.runtime_data

    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
        },
        "signalr_connected": data.signalr_client.connected,
        "coordinators": {
            name: {
                "last_update_success": coordinator.last_update_success,
                "data_keys": sorted(coordinator.data or {}),
            }
            for name, coordinator in (
                ("glucose", data.glucose_coordinator),
                ("device", data.device_coordinator),
            )
        },
    }
