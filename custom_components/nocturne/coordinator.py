"""Data update coordinators for Nocturne."""

from __future__ import annotations

import logging
from datetime import timedelta
from http import HTTPStatus
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from nocturne_py import ApiException

from .api import NocturneApiClient
from .const import DEVICE_UPDATE_INTERVAL_SECONDS, GLUCOSE_UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class NocturneCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls Nocturne and maps API and token refresh errors for both coordinators."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: NocturneApiClient,
        name: str,
        update_interval_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.client = client
        self._refresh_failed = False

    async def _async_fetch(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._async_fetch()
        except ClientResponseError as err:
            if 400 <= err.status < 500 and err.status != HTTPStatus.TOO_MANY_REQUESTS:
                raise ConfigEntryAuthFailed("OAuth token refresh refused") from err
            raise UpdateFailed(f"Token endpoint error: {err.status}") from err
        except ApiException as err:
            if err.status == 401:
                if self._refresh_failed:
                    raise ConfigEntryAuthFailed(
                        "OAuth token expired"
                    ) from err
                self._refresh_failed = True
                raise UpdateFailed("Auth failed, will retry") from err
            raise UpdateFailed(f"API error: {err.status}") from err
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err
        self._refresh_failed = False
        return data


class GlucoseCoordinator(NocturneCoordinator):
    """Coordinator for glucose entries and APS snapshots (60s interval)."""

    def __init__(self, hass: HomeAssistant, client: NocturneApiClient) -> None:
        super().__init__(hass, client, "Nocturne Glucose", GLUCOSE_UPDATE_INTERVAL_SECONDS)

    async def async_handle_glucose_push(self) -> None:
        """Refresh now in response to a SignalR glucose_reading.

        The push carries legacy Entry objects, not this coordinator's data shape,
        and APS state is not pushed at all, so the push only triggers a poll.
        """
        await self.async_request_refresh()

    async def _async_fetch(self) -> dict[str, Any]:
        return {
            "glucose": await self.client.get_latest_glucose(),
            "aps": await self.client.get_latest_aps_snapshot(),
        }


class DeviceCoordinator(NocturneCoordinator):
    """Coordinator for device status, profile, and reports (5m interval)."""

    def __init__(self, hass: HomeAssistant, client: NocturneApiClient) -> None:
        super().__init__(hass, client, "Nocturne Device", DEVICE_UPDATE_INTERVAL_SECONDS)

    async def _async_fetch(self) -> dict[str, Any]:
        return {
            "pump": await self.client.get_latest_pump_snapshot(),
            "uploader": await self.client.get_latest_uploader_snapshot(),
            "profile": await self.client.get_profile_summary(),
            "daily_summary": await self.client.get_daily_summary(dt_util.now().date()),
            "sensor_age": await self.client.get_sensor_age(),
        }
