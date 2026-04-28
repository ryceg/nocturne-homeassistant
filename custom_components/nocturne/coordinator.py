"""Data update coordinators for Nocturne."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from nocturne_sdk import ApiException

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
            glucose = await self.client.get_latest_glucose()
            aps = await self.client.get_latest_aps_snapshot()
            self._refresh_failed = False
            return {
                "glucose": glucose,
                "aps": aps,
            }
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
            pump = await self.client.get_latest_pump_snapshot()
            uploader = await self.client.get_latest_uploader_snapshot()
            profile = await self.client.get_profile_summary()
            daily_summary = await self.client.get_daily_summary()
            sensor_age = await self.client.get_sensor_age()
            self._refresh_failed = False
            return {
                "pump": pump,
                "uploader": uploader,
                "profile": profile,
                "daily_summary": daily_summary,
                "sensor_age": sensor_age,
            }
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
