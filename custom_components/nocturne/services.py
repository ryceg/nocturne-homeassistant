"""Services for writing data to Nocturne and acknowledging alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from nocturne_py import (
    CreateBolusRequest,
    CreateCarbIntakeRequest,
    CreateStateSpanRequest,
    StateSpanCategory,
)

from .const import DATA_SOURCE_HOME_ASSISTANT, DOMAIN

if TYPE_CHECKING:
    from . import NocturneConfigEntry

SERVICE_LOG_CARBS = "log_carbs"
SERVICE_LOG_INSULIN = "log_insulin"
SERVICE_LOG_GLUCOSE = "log_glucose"
SERVICE_LOG_ACTIVITY = "log_activity"
SERVICE_ACKNOWLEDGE_ALERT = "acknowledge_alert"

ENTRY_SCHEMA = {vol.Optional(ATTR_CONFIG_ENTRY_ID): str}

LOG_CARBS_SCHEMA = vol.Schema(
    {
        **ENTRY_SCHEMA,
        vol.Required("carbs"): vol.Coerce(float),
        vol.Optional("notes", default=""): str,
    }
)

LOG_INSULIN_SCHEMA = vol.Schema(
    {
        **ENTRY_SCHEMA,
        vol.Required("insulin"): vol.Coerce(float),
        vol.Optional("notes", default=""): str,
    }
)

LOG_GLUCOSE_SCHEMA = vol.Schema(
    {
        **ENTRY_SCHEMA,
        vol.Required("value"): vol.Coerce(float),
        vol.Optional("type", default="sgv"): vol.In(["sgv", "mbg"]),
    }
)

LOG_ACTIVITY_SCHEMA = vol.Schema(
    {
        **ENTRY_SCHEMA,
        vol.Required("duration"): vol.Coerce(int),
        vol.Optional("activity_type", default=""): str,
        vol.Optional("notes", default=""): str,
    }
)

ACKNOWLEDGE_ALERT_SCHEMA = vol.Schema(
    {
        **ENTRY_SCHEMA,
        vol.Required("excursion_id"): str,
    }
)


def async_get_entry(hass: HomeAssistant, call: ServiceCall) -> NocturneConfigEntry:
    """Return the loaded entry a call targets: the one it names, or the only one loaded."""
    if (entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID)) is None:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_entry"
            )
        if len(loaded) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_required"
            )
        return loaded[0]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Nocturne services."""

    async def handle_log_carbs(call: ServiceCall) -> None:
        client = async_get_entry(hass, call).runtime_data.client
        await client.create_carb_intake(
            CreateCarbIntakeRequest(
                carbs=call.data["carbs"],
                data_source=DATA_SOURCE_HOME_ASSISTANT,
            )
        )

    async def handle_log_insulin(call: ServiceCall) -> None:
        client = async_get_entry(hass, call).runtime_data.client
        await client.create_bolus(
            CreateBolusRequest(
                insulin=call.data["insulin"],
                data_source=DATA_SOURCE_HOME_ASSISTANT,
            )
        )

    async def handle_log_glucose(call: ServiceCall) -> None:
        client = async_get_entry(hass, call).runtime_data.client
        await client.create_glucose(
            mgdl=call.data["value"],
            data_source=DATA_SOURCE_HOME_ASSISTANT,
        )

    async def handle_log_activity(call: ServiceCall) -> None:
        client = async_get_entry(hass, call).runtime_data.client
        now = datetime.now(UTC)
        start_mills = int(now.timestamp() * 1000)
        duration_mins = call.data["duration"]
        end_mills = start_mills + (duration_mins * 60 * 1000)
        await client.create_state_span(
            CreateStateSpanRequest(
                category=StateSpanCategory.EXERCISE,
                start_mills=start_mills,
                end_mills=end_mills,
                source=DATA_SOURCE_HOME_ASSISTANT,
            )
        )

    async def handle_acknowledge_alert(call: ServiceCall) -> None:
        data = async_get_entry(hass, call).runtime_data
        await data.signalr_client.acknowledge(
            call.data["excursion_id"], f"homeassistant:{data.instance_name}"
        )

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_CARBS, handle_log_carbs, schema=LOG_CARBS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_INSULIN, handle_log_insulin, schema=LOG_INSULIN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_GLUCOSE, handle_log_glucose, schema=LOG_GLUCOSE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_ACTIVITY, handle_log_activity, schema=LOG_ACTIVITY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACKNOWLEDGE_ALERT,
        handle_acknowledge_alert,
        schema=ACKNOWLEDGE_ALERT_SCHEMA,
    )
