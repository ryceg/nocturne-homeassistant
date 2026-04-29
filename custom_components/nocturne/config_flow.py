"""OAuth2 config flow for Nocturne."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    AbstractOAuth2Implementation,
    async_register_implementation,
)
from homeassistant.helpers.network import get_url
from yarl import URL

from .api import NocturneApiClient
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_CLIENT_ID,
    CONF_INSTANCE_URL,
    CONF_TOKEN_URL,
    DEFAULT_SCOPES,
    DOMAIN,
    SOFTWARE_ID,
)

_LOGGER = logging.getLogger(__name__)


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


class NocturneOAuth2Implementation(AbstractOAuth2Implementation):
    """OAuth2 implementation for a Nocturne instance (public client with PKCE)."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        instance_url: str,
        authorize_url: str,
        token_url: str,
        client_id: str,
    ) -> None:
        self.hass = hass
        self._domain = domain
        self._instance_url = instance_url
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._client_id = client_id
        self._code_verifier: str | None = None

    @property
    def name(self) -> str:
        return "Nocturne"

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def redirect_uri(self) -> str:
        """Return HA's external auth callback URL."""
        return f"{get_url(self.hass, allow_internal=False, allow_ip=False)}/auth/external/callback"

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate the full authorize URL with OAuth2 + PKCE params."""
        from homeassistant.helpers.config_entry_oauth2_flow import _encode_jwt

        self._code_verifier, code_challenge = _generate_pkce()

        state = _encode_jwt(
            self.hass,
            {"flow_id": flow_id, "redirect_uri": self.redirect_uri},
        )

        return str(
            URL(self._authorize_url).with_query(
                {
                    "response_type": "code",
                    "client_id": self._client_id,
                    "redirect_uri": self.redirect_uri,
                    "state": state,
                    "scope": " ".join(DEFAULT_SCOPES),
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Exchange authorization code for tokens."""
        session = async_get_clientsession(self.hass)
        resp = await session.post(
            self._token_url,
            data={
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": self.redirect_uri,
                "client_id": self._client_id,
                "code_verifier": self._code_verifier,
            },
        )
        resp.raise_for_status()
        return await resp.json()

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token."""
        session = async_get_clientsession(self.hass)
        resp = await session.post(
            self._token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": self._client_id,
            },
        )
        resp.raise_for_status()
        return await resp.json()


class NocturneOAuth2FlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 config flow for Nocturne."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._instance_url: str | None = None
        self._discovery: dict[str, Any] | None = None
        self._client_id: str | None = None
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
                        session, registration_endpoint
                    )
                    if dcr_response is None:
                        errors["base"] = "cannot_connect"
                        return self._show_form(errors)

                    self._client_id = dcr_response["client_id"]

                    impl = NocturneOAuth2Implementation(
                        self.hass,
                        DOMAIN,
                        url,
                        self._authorize_url,
                        self._token_url,
                        self._client_id,
                    )
                    async_register_implementation(self.hass, DOMAIN, impl)
                    self.flow_impl = impl

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
        data[CONF_CLIENT_ID] = self._client_id
        data[CONF_AUTHORIZE_URL] = self._authorize_url
        data[CONF_TOKEN_URL] = self._token_url
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
        self, session: ClientSession, registration_url: str
    ) -> dict[str, Any] | None:
        """Register as an OAuth client via Dynamic Client Registration."""
        redirect_uri = f"{get_url(self.hass, allow_internal=False, allow_ip=False)}/auth/external/callback"
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
