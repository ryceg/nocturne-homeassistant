"""Nocturne integration for Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
    async_register_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .alert_handler import NocturneAlertHandler
from .api import NocturneApiClient
from .config_flow import NocturneOAuth2Implementation
from .const import (
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_NOTIFY_SERVICES,
    DEFAULT_TITLE,
    DOMAIN,
    missing_scopes,
)
from .coordinator import DeviceCoordinator, GlucoseCoordinator
from .services import async_setup_services
from .signalr import NocturneSignalRClient, TokenProvider

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class NocturneData:
    """Runtime state for one loaded Nocturne entry."""

    client: NocturneApiClient
    glucose_coordinator: GlucoseCoordinator
    device_coordinator: DeviceCoordinator
    signalr_client: NocturneSignalRClient
    alert_handler: NocturneAlertHandler
    instance_name: str


type NocturneConfigEntry = ConfigEntry[NocturneData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Nocturne services once for every entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NocturneConfigEntry) -> bool:
    """Set up Nocturne from a config entry."""
    base_url = entry.data[CONF_INSTANCE_URL]

    # Re-register the OAuth implementation so it survives restarts.
    if CONF_CLIENT_ID in entry.data:
        async_register_implementation(
            hass, DOMAIN, NocturneOAuth2Implementation.from_entry_data(hass, entry.data)
        )

    implementation = await async_get_config_entry_implementation(hass, entry)
    oauth_session = OAuth2Session(hass, entry, implementation)

    client = NocturneApiClient(base_url, oauth_session=oauth_session)

    glucose_coordinator = GlucoseCoordinator(hass, entry, client)
    device_coordinator = DeviceCoordinator(hass, entry, client)

    await glucose_coordinator.async_config_entry_first_refresh()
    await device_coordinator.async_config_entry_first_refresh()

    notify_services: list[str] = entry.options.get(CONF_NOTIFY_SERVICES, [])
    instance_name = entry.data.get(CONF_CLIENT_ID, "unknown")
    alert_handler = NocturneAlertHandler(hass, notify_services, lambda: _display_name(entry))

    async def _on_glucose_reading(data: dict) -> None:
        await glucose_coordinator.async_handle_glucose_push()

    async def _on_disconnected() -> None:
        _LOGGER.info("SignalR disconnected")

    signalr_client = NocturneSignalRClient(
        hass=hass,
        instance_url=base_url,
        instance_id=entry.data.get(CONF_CLIENT_ID, ""),
        async_get_token=_access_token_provider(oauth_session),
        on_glucose_reading=_on_glucose_reading,
        on_alert_dispatch=alert_handler.handle_alert_dispatch,
        on_alert_resolved=alert_handler.handle_alert_resolved,
        on_alert_acknowledged=alert_handler.handle_alert_acknowledged,
        on_disconnected=_on_disconnected,
    )

    entry.runtime_data = NocturneData(
        client=client,
        glucose_coordinator=glucose_coordinator,
        device_coordinator=device_coordinator,
        signalr_client=signalr_client,
        alert_handler=alert_handler,
        instance_name=instance_name,
    )

    signalr_client.start(entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if missing := missing_scopes(entry.data):
        _LOGGER.info(
            "Nocturne now requests scopes %s; starting reauthentication", ", ".join(missing)
        )
        entry.async_start_reauth(hass)

    return True


def _display_name(entry: NocturneConfigEntry) -> str:
    """Name the instance for people: the entry title, or its host while the title is the default."""
    if entry.title != DEFAULT_TITLE:
        return entry.title
    return urlparse(entry.data[CONF_INSTANCE_URL]).hostname or entry.title


def _access_token_provider(oauth_session: OAuth2Session) -> TokenProvider:
    """Return a provider that refreshes the OAuth token when it is due and yields it."""

    async def _get_access_token() -> str:
        await oauth_session.async_ensure_token_valid()
        return oauth_session.token["access_token"]

    return _get_access_token


async def async_unload_entry(hass: HomeAssistant, entry: NocturneConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.signalr_client.stop()
    return unload_ok
