"""OAuth2 config flow for Nocturne."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
)

from .api import NocturneApiClient
from .const import CONF_INSTANCE_URL, DEFAULT_SCOPES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class NocturneOAuth2FlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 config flow for Nocturne."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._instance_url: str | None = None
        self._discovery: dict[str, Any] | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        return {"scope": " ".join(DEFAULT_SCOPES)}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: collect Nocturne instance URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_INSTANCE_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            client = NocturneApiClient(session, url, "")

            if await client.validate_connection():
                self._instance_url = url
                self._discovery = await self._fetch_discovery(session, url)
                if self._discovery:
                    return await self.async_step_auth()
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_INSTANCE_URL): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._instance_url = entry_data.get(CONF_INSTANCE_URL)
        return await self.async_step_auth()

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create the config entry after OAuth completes."""
        data[CONF_INSTANCE_URL] = self._instance_url
        return self.async_create_entry(title="Nocturne", data=data)

    async def _fetch_discovery(
        self, session: ClientSession, url: str
    ) -> dict[str, Any] | None:
        """Fetch OAuth discovery document."""
        try:
            resp = await session.get(
                f"{url}/.well-known/openid-configuration", timeout=10
            )
            if resp.status == 200:
                return await resp.json()
        except Exception:
            _LOGGER.exception("Failed to fetch discovery document")
        return None
