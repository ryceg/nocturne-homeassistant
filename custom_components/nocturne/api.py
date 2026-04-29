"""API client wrapping the nocturne-py SDK for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from nocturne_py import (
    ApiClient,
    ApsSnapshot,
    Configuration,
    DeviceAgeInfo,
    PumpSnapshot,
    SensorGlucose,
    StatusApi,
    Treatment,
    UploaderSnapshot,
    UpsertSensorGlucoseRequest,
    V4APSSnapshotsApi,
    V4BatteryApi,
    V4DataOverviewApi,
    V4DeviceAgeApi,
    V4ProfileApi,
    V4PumpSnapshotsApi,
    V4SensorGlucoseApi,
    V4TreatmentsApi,
    V4UploaderSnapshotsApi,
)
from nocturne_py.models import DailySummaryDay, ProfileSummary

_LOGGER = logging.getLogger(__name__)


class NocturneApiClient:
    """Nocturne v4 API client using the nocturne-py SDK."""

    def __init__(
        self,
        base_url: str,
        oauth_session: OAuth2Session | None = None,
        access_token: str = "",
    ) -> None:
        self._config = Configuration(host=base_url.rstrip("/"))
        self._oauth_session = oauth_session
        self._static_token = access_token

    async def _ensure_token(self) -> None:
        """Refresh the OAuth token and update the SDK configuration."""
        if self._oauth_session is not None:
            await self._oauth_session.async_ensure_token_valid()
            self._config.access_token = self._oauth_session.token["access_token"]
        else:
            self._config.access_token = self._static_token

    def _client(self) -> ApiClient:
        return ApiClient(self._config)

    async def validate_connection(self) -> bool:
        """Validate the instance URL by hitting the status endpoint."""
        try:
            await self._ensure_token()

            def _call() -> bool:
                with self._client() as client:
                    api = StatusApi(client)
                    resp = api.status_get_status()
                    return resp.status is not None

            return await asyncio.to_thread(_call)
        except Exception:
            return False

    async def get_latest_glucose(self) -> SensorGlucose | None:
        """Fetch the most recent sensor glucose reading."""
        await self._ensure_token()

        def _call() -> SensorGlucose | None:
            with self._client() as client:
                api = V4SensorGlucoseApi(client)
                resp = api.sensor_glucose_get_all(limit=1, sort="desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_aps_snapshot(self) -> ApsSnapshot | None:
        """Fetch the most recent APS snapshot."""
        await self._ensure_token()

        def _call() -> ApsSnapshot | None:
            with self._client() as client:
                api = V4APSSnapshotsApi(client)
                resp = api.aps_snapshot_get_all(limit=1, sort="desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_pump_snapshot(self) -> PumpSnapshot | None:
        """Fetch the most recent pump snapshot."""
        await self._ensure_token()

        def _call() -> PumpSnapshot | None:
            with self._client() as client:
                api = V4PumpSnapshotsApi(client)
                resp = api.pump_snapshot_get_all(limit=1, sort="desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_uploader_snapshot(self) -> UploaderSnapshot | None:
        """Fetch the most recent uploader (CGM phone) snapshot."""
        await self._ensure_token()

        def _call() -> UploaderSnapshot | None:
            with self._client() as client:
                api = V4UploaderSnapshotsApi(client)
                resp = api.uploader_snapshot_get_all(limit=1, sort="desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_profile_summary(self) -> ProfileSummary | None:
        """Fetch the profile summary."""
        await self._ensure_token()

        def _call() -> ProfileSummary | None:
            with self._client() as client:
                api = V4ProfileApi(client)
                return api.profile_get_profile_summary()

        return await asyncio.to_thread(_call)

    async def get_daily_summary(self) -> DailySummaryDay | None:
        """Fetch today's daily summary (time in range, etc.)."""
        await self._ensure_token()
        year = datetime.now().year

        def _call() -> DailySummaryDay | None:
            with self._client() as client:
                api = V4DataOverviewApi(client)
                resp = api.data_overview_get_daily_summary(year=year)
                if resp.days:
                    return resp.days[-1]
                return None

        return await asyncio.to_thread(_call)

    async def get_sensor_age(self) -> DeviceAgeInfo | None:
        """Fetch sensor age information."""
        await self._ensure_token()

        def _call() -> DeviceAgeInfo | None:
            with self._client() as client:
                api = V4DeviceAgeApi(client)
                resp = api.device_age_get_sensor_age()
                return resp.sensor_start

        return await asyncio.to_thread(_call)

    async def create_glucose(
        self, mgdl: float, data_source: str = ""
    ) -> SensorGlucose | None:
        """Post a sensor glucose reading."""
        await self._ensure_token()

        def _call() -> SensorGlucose | None:
            with self._client() as client:
                api = V4SensorGlucoseApi(client)
                req = UpsertSensorGlucoseRequest(mgdl=mgdl, dataSource=data_source)
                return api.sensor_glucose_create(req)

        return await asyncio.to_thread(_call)

    async def create_treatment(self, treatment: Treatment) -> Treatment | None:
        """Post a treatment (carbs, insulin, etc.)."""
        await self._ensure_token()

        def _call() -> Treatment | None:
            with self._client() as client:
                api = V4TreatmentsApi(client)
                return api.treatments_create_treatment(treatment)

        return await asyncio.to_thread(_call)
