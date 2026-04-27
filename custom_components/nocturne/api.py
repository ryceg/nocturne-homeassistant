"""Thin API client wrapping aiohttp with nocturne_sdk models."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)


class NocturneApiClient:
    """Nocturne v4 API client using HA's aiohttp session."""

    def __init__(
        self, session: ClientSession, base_url: str, access_token: str
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def update_token(self, access_token: str) -> None:
        """Update the OAuth access token after refresh."""
        self._token = access_token

    async def validate_connection(self) -> bool:
        """Validate the instance URL by hitting the OAuth discovery endpoint."""
        try:
            resp = await self._session.get(
                f"{self._base_url}/.well-known/openid-configuration",
                timeout=10,
            )
            return resp.status == 200
        except Exception:
            return False

    async def get_latest_entry(self) -> dict[str, Any] | None:
        """Fetch the most recent glucose entry."""
        data = await self._get(
            "/api/v4/entries", params={"limit": "1", "sort": "desc"}
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return None

    async def get_latest_device_status(self) -> dict[str, Any] | None:
        """Fetch the most recent device status."""
        data = await self._get(
            "/api/v4/devicestatus", params={"limit": "1", "sort": "desc"}
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return None

    async def get_active_profile(self) -> dict[str, Any] | None:
        """Fetch the active profile."""
        return await self._get("/api/v4/profiles/active")

    async def get_report_summary(self) -> dict[str, Any] | None:
        """Fetch the report summary (time in range, etc.)."""
        return await self._get("/api/v4/reports/summary")

    async def post_entry(self, entry: dict[str, Any]) -> bool:
        """Post a glucose entry."""
        return await self._post("/api/v4/entries", entry)

    async def post_treatment(self, treatment: dict[str, Any]) -> bool:
        """Post a treatment (carbs, insulin, etc.)."""
        return await self._post("/api/v4/treatments", treatment)

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> Any | None:
        try:
            resp = await self._session.get(
                f"{self._base_url}{path}",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            return await resp.json()
        except ClientResponseError:
            raise
        except Exception:
            _LOGGER.exception("Unexpected error fetching %s", path)
            return None

    async def _post(self, path: str, data: dict[str, Any]) -> bool:
        try:
            resp = await self._session.post(
                f"{self._base_url}{path}",
                headers=self._headers,
                json=data,
            )
            resp.raise_for_status()
            return True
        except Exception:
            _LOGGER.exception("Unexpected error posting to %s", path)
            return False
