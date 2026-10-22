"""Access token provider shared by the API client and SignalR."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

TokenProvider = Callable[[], Awaitable[str]]


def oauth_token_provider(oauth_session: OAuth2Session) -> TokenProvider:
    """Return a provider that refreshes the OAuth token when it is due and yields it."""

    async def _get_access_token() -> str:
        await oauth_session.async_ensure_token_valid()
        return oauth_session.token["access_token"]

    return _get_access_token
