"""Data update coordinators for Nocturne."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import NocturneApiClient
from .const import DEVICE_UPDATE_INTERVAL_SECONDS, GLUCOSE_UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class GlucoseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for glucose entries and APS snapshots (60s interval)."""

    def __init__(self, hass: HomeAssistant, client: NocturneApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Nocturne Glucose",
            update_interval=timedelta(seconds=GLUCOSE_UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self._refresh_failed = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            entry = await self.client.get_latest_entry()
            device_status = await self.client.get_latest_device_status()
            self._refresh_failed = False
            return {
                "entry": entry,
                "device_status": device_status,
            }
        except ClientResponseError as err:
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


class DeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for device status, profile, and reports (5m interval)."""

    def __init__(self, hass: HomeAssistant, client: NocturneApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Nocturne Device",
            update_interval=timedelta(seconds=DEVICE_UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self._refresh_failed = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            device_status = await self.client.get_latest_device_status()
            profile = await self.client.get_active_profile()
            report = await self.client.get_report_summary()
            self._refresh_failed = False
            return {
                "device_status": device_status,
                "profile": profile,
                "report": report,
            }
        except ClientResponseError as err:
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
