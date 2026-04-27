"""Nocturne integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NocturneApiClient
from .const import CONF_INSTANCE_URL, DATA_SOURCE_HOME_ASSISTANT, DOMAIN
from .coordinator import DeviceCoordinator, GlucoseCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

type NocturneConfigEntry = ConfigEntry

LOG_CARBS_SCHEMA = vol.Schema(
    {
        vol.Required("carbs"): vol.Coerce(float),
        vol.Optional("notes", default=""): str,
    }
)

LOG_INSULIN_SCHEMA = vol.Schema(
    {
        vol.Required("insulin"): vol.Coerce(float),
        vol.Optional("notes", default=""): str,
    }
)

LOG_GLUCOSE_SCHEMA = vol.Schema(
    {
        vol.Required("value"): vol.Coerce(float),
        vol.Optional("type", default="sgv"): vol.In(["sgv", "mbg"]),
    }
)

LOG_ACTIVITY_SCHEMA = vol.Schema(
    {
        vol.Required("duration"): vol.Coerce(int),
        vol.Optional("activity_type", default=""): str,
        vol.Optional("notes", default=""): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: NocturneConfigEntry) -> bool:
    """Set up Nocturne from a config entry."""
    session = async_get_clientsession(hass)
    token = entry.data["token"]["access_token"]
    base_url = entry.data[CONF_INSTANCE_URL]

    client = NocturneApiClient(session, base_url, token)

    glucose_coordinator = GlucoseCoordinator(hass, client)
    device_coordinator = DeviceCoordinator(hass, client)

    await glucose_coordinator.async_config_entry_first_refresh()
    await device_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "glucose_coordinator": glucose_coordinator,
        "device_coordinator": device_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: NocturneConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _get_client(hass: HomeAssistant) -> NocturneApiClient:
    """Get the API client from the first config entry."""
    entry_id = next(iter(hass.data[DOMAIN]))
    return hass.data[DOMAIN][entry_id]["client"]


def _register_services(hass: HomeAssistant) -> None:
    """Register HA services for writing data to Nocturne."""
    if hass.services.has_service(DOMAIN, "log_carbs"):
        return

    async def handle_log_carbs(call: ServiceCall) -> None:
        client = _get_client(hass)
        await client.post_treatment(
            {
                "eventType": "Carb Correction",
                "carbs": call.data["carbs"],
                "notes": call.data.get("notes", ""),
                "dataSource": DATA_SOURCE_HOME_ASSISTANT,
            }
        )

    async def handle_log_insulin(call: ServiceCall) -> None:
        client = _get_client(hass)
        await client.post_treatment(
            {
                "eventType": "Correction Bolus",
                "insulin": call.data["insulin"],
                "notes": call.data.get("notes", ""),
                "dataSource": DATA_SOURCE_HOME_ASSISTANT,
            }
        )

    async def handle_log_glucose(call: ServiceCall) -> None:
        client = _get_client(hass)
        await client.post_entry(
            {
                "sgv": call.data["value"],
                "type": call.data.get("type", "sgv"),
                "dataSource": DATA_SOURCE_HOME_ASSISTANT,
            }
        )

    async def handle_log_activity(call: ServiceCall) -> None:
        client = _get_client(hass)
        await client.post_treatment(
            {
                "eventType": "Exercise",
                "duration": call.data["duration"],
                "notes": call.data.get("notes", ""),
                "dataSource": DATA_SOURCE_HOME_ASSISTANT,
            }
        )

    hass.services.async_register(
        DOMAIN, "log_carbs", handle_log_carbs, schema=LOG_CARBS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "log_insulin", handle_log_insulin, schema=LOG_INSULIN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "log_glucose", handle_log_glucose, schema=LOG_GLUCOSE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "log_activity", handle_log_activity, schema=LOG_ACTIVITY_SCHEMA
    )
