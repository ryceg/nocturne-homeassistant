"""OAuth2 config flow for Nocturne."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AUTH_CALLBACK_PATH,
    MY_AUTH_CALLBACK_PATH,
    AbstractOAuth2FlowHandler,
    LocalOAuth2ImplementationWithPkce,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import NocturneApiClient
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_NOTIFY_SERVICES,
    CONF_REDIRECT_URI,
    CONF_REQUESTED_SCOPES,
    CONF_TOKEN_URL,
    DEFAULT_SCOPES,
    DOMAIN,
    SOFTWARE_ID,
)

_LOGGER = logging.getLogger(__name__)


def _external_callback_uri(hass: HomeAssistant) -> str:
    return f"{get_url(hass, allow_internal=False, allow_ip=False)}{AUTH_CALLBACK_PATH}"


def registration_redirect_uri(hass: HomeAssistant) -> str:
    """Return the redirect URI to register a new client with.

    The external URL comes first because its callback is unique to this install, which keeps
    each install on its own client. Raises NoURLAvailableError when HA has neither.
    """
    try:
        return _external_callback_uri(hass)
    except NoURLAvailableError:
        if "my" in hass.config.components:
            return MY_AUTH_CALLBACK_PATH
        raise


class NocturneOAuth2Implementation(LocalOAuth2ImplementationWithPkce):
    """OAuth2 implementation for one registered client of a Nocturne instance."""

    def __init__(
        self,
        hass: HomeAssistant,
        client_id: str,
        authorize_url: str,
        token_url: str,
        redirect_uri: str | None = None,
    ) -> None:
        super().__init__(hass, DOMAIN, client_id, authorize_url, token_url)
        self._redirect_uri = redirect_uri

    @classmethod
    def from_entry_data(
        cls, hass: HomeAssistant, data: Mapping[str, Any]
    ) -> NocturneOAuth2Implementation:
        """Build the implementation from a config entry's stored client and URLs."""
        return cls(
            hass,
            data[CONF_CLIENT_ID],
            data[CONF_AUTHORIZE_URL],
            data[CONF_TOKEN_URL],
            data.get(CONF_REDIRECT_URI),
        )

    @property
    def name(self) -> str:
        return "Nocturne"

    @property
    def redirect_uri(self) -> str:
        """Return the redirect URI the client registered with.

        Entries made before it was stored registered the external URL callback.
        """
        return self._redirect_uri or _external_callback_uri(self.hass)


class NocturneOptionsFlowHandler(OptionsFlow):
    """Handle options flow for Nocturne — lets users pick notify services."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the form for selecting notification services."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Build a dict of available notify.* services
        notify_services: dict[str, str] = {}
        services = self.hass.services.async_services()
        for service_name in sorted(services.get("notify", {})):
            notify_services[service_name] = service_name

        current_selection: list[str] = self.config_entry.options.get(
            CONF_NOTIFY_SERVICES, []
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_NOTIFY_SERVICES,
                        default=current_selection,
                    ): vol.All(
                        cv.multi_select(notify_services),
                    ),
                }
            ),
        )


class NocturneOAuth2FlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 config flow for Nocturne."""

    DOMAIN = DOMAIN

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return NocturneOptionsFlowHandler()

    def __init__(self) -> None:
        super().__init__()
        self._instance_url: str | None = None
        self._discovery: dict[str, Any] | None = None
        self._authorize_url: str | None = None
        self._token_url: str | None = None

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
            try:
                redirect_uri = registration_redirect_uri(self.hass)
            except NoURLAvailableError:
                return self._show_form({"base": "no_redirect_url"})
            url = user_input[CONF_INSTANCE_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            client = NocturneApiClient(url)

            if await client.validate_connection():
                self._instance_url = url
                self._discovery = await self._fetch_discovery(session, url)
                if self._discovery:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()

                    def _rebase_url(endpoint_url: str) -> str:
                        """Rewrite discovery URL to use the tenant instance origin."""
                        from urllib.parse import urlparse, urlunparse

                        ep = urlparse(endpoint_url)
                        inst = urlparse(url)
                        return urlunparse(ep._replace(
                            scheme=inst.scheme, netloc=inst.netloc
                        ))

                    self._authorize_url = _rebase_url(
                        self._discovery.get("authorization_endpoint")
                        or self._discovery["authorizationEndpoint"]
                    )
                    self._token_url = _rebase_url(
                        self._discovery.get("token_endpoint")
                        or self._discovery["tokenEndpoint"]
                    )

                    # Dynamic Client Registration (RFC 7591)
                    raw_registration = (
                        self._discovery.get("registration_endpoint")
                        or self._discovery.get("registrationEndpoint")
                    )
                    registration_endpoint = (
                        _rebase_url(raw_registration)
                        if raw_registration
                        else f"{url}/api/oauth/register"
                    )
                    dcr_response = await self._register_client(
                        session, registration_endpoint, redirect_uri
                    )
                    if dcr_response is None:
                        errors["base"] = "cannot_connect"
                        return self._show_form(errors)

                    self.flow_impl = NocturneOAuth2Implementation(
                        self.hass,
                        dcr_response["client_id"],
                        self._authorize_url,
                        self._token_url,
                        redirect_uri,
                    )

                    return await self.async_step_auth()
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "cannot_connect"

        return self._show_form(errors)

    def _show_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_INSTANCE_URL): str}
            ),
            errors=errors or {},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication, then authorise against the entry's own client."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        entry_data = self._get_reauth_entry().data
        self._instance_url = entry_data[CONF_INSTANCE_URL]
        self.flow_impl = NocturneOAuth2Implementation.from_entry_data(self.hass, entry_data)
        return await self.async_step_auth()

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create the config entry after OAuth completes."""
        data[CONF_REQUESTED_SCOPES] = list(DEFAULT_SCOPES)
        if self.source == SOURCE_REAUTH:
            await self.async_set_unique_id(self._instance_url)
            self._abort_if_unique_id_mismatch(reason="wrong_instance")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        data[CONF_INSTANCE_URL] = self._instance_url
        data[CONF_CLIENT_ID] = self.flow_impl.client_id
        data[CONF_AUTHORIZE_URL] = self._authorize_url
        data[CONF_TOKEN_URL] = self._token_url
        data[CONF_REDIRECT_URI] = self.flow_impl.redirect_uri
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

    async def _register_client(
        self, session: ClientSession, registration_url: str, redirect_uri: str
    ) -> dict[str, Any] | None:
        """Register as an OAuth client via Dynamic Client Registration."""
        try:
            resp = await session.post(
                registration_url,
                json={
                    "software_id": SOFTWARE_ID,
                    "client_name": "Home Assistant",
                    "redirect_uris": [redirect_uri],
                    "scope": " ".join(DEFAULT_SCOPES),
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
                timeout=10,
            )
            if resp.status in (200, 201):
                return await resp.json()
            _LOGGER.error("DCR failed with status %s", resp.status)
        except Exception:
            _LOGGER.exception("Failed to register OAuth client")
        return None
