"""API client wrapping the nocturne-py SDK for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

from nocturne_py import (
    ApiClient,
    ApiException,
    ApsSnapshot,
    ApsSnapshotApi,
    Bolus,
    BolusApi,
    CarbIntake,
    Configuration,
    CreateBolusRequest,
    CreateCarbIntakeRequest,
    CreateStateSpanRequest,
    DataOverviewApi,
    DeviceAgeApi,
    DeviceAgeInfo,
    MeterGlucose,
    MeterGlucoseApi,
    NutritionApi,
    ProfileApi,
    PumpSnapshot,
    PumpSnapshotApi,
    SensorGlucose,
    SensorGlucoseApi,
    StateSpan,
    StateSpansApi,
    StatusApi,
    UploaderSnapshot,
    UploaderSnapshotApi,
    UpsertMeterGlucoseRequest,
    UpsertSensorGlucoseRequest,
)
from nocturne_py.exceptions import OpenApiException
from nocturne_py.models import DailySummaryDay, ProfileSummary
from urllib3.exceptions import HTTPError

from .auth import TokenProvider

_LOGGER = logging.getLogger(__name__)

DAILY_SUMMARY_CACHE_SECONDS = 30 * 60


class NocturneApiClient:
    """Nocturne v4 API client using the nocturne-py SDK."""

    def __init__(
        self,
        base_url: str,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._config = Configuration(host=base_url.rstrip("/"))
        self._token_provider = token_provider
        self._warned_missing_reports_scope = False
        self._daily_summary_cache: tuple[date, float, DailySummaryDay | None] | None = None

    async def _ensure_token(self) -> None:
        """Set the SDK token from the provider before each request."""
        if self._token_provider is not None:
            self._config.access_token = await self._token_provider()

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
        except (OpenApiException, HTTPError, OSError):
            return False

    async def get_latest_glucose(self) -> SensorGlucose | None:
        """Fetch the most recent sensor glucose reading."""
        await self._ensure_token()

        def _call() -> SensorGlucose | None:
            with self._client() as client:
                api = SensorGlucoseApi(client)
                resp = api.sensor_glucose_get_all(limit=1, sort="timestamp_desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_aps_snapshot(self) -> ApsSnapshot | None:
        """Fetch the most recent APS snapshot."""
        await self._ensure_token()

        def _call() -> ApsSnapshot | None:
            with self._client() as client:
                api = ApsSnapshotApi(client)
                resp = api.aps_snapshot_get_all(limit=1, sort="timestamp_desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_pump_snapshot(self) -> PumpSnapshot | None:
        """Fetch the most recent pump snapshot."""
        await self._ensure_token()

        def _call() -> PumpSnapshot | None:
            with self._client() as client:
                api = PumpSnapshotApi(client)
                resp = api.pump_snapshot_get_all(limit=1, sort="timestamp_desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_latest_uploader_snapshot(self) -> UploaderSnapshot | None:
        """Fetch the most recent uploader (CGM phone) snapshot."""
        await self._ensure_token()

        def _call() -> UploaderSnapshot | None:
            with self._client() as client:
                api = UploaderSnapshotApi(client)
                resp = api.uploader_snapshot_get_all(limit=1, sort="timestamp_desc")
                if resp.data:
                    return resp.data[0]
                return None

        return await asyncio.to_thread(_call)

    async def get_profile_summary(self) -> ProfileSummary | None:
        """Fetch the profile summary."""
        await self._ensure_token()

        def _call() -> ProfileSummary | None:
            with self._client() as client:
                api = ProfileApi(client)
                return api.profile_get_profile_summary()

        return await asyncio.to_thread(_call)

    async def get_daily_summary(self, today: date) -> DailySummaryDay | None:
        """Fetch the daily summary (time in range, etc.) for ``today``.

        Returns None when the grant lacks ``reports.read`` (entries authorised
        before that scope was requested keep their other sensors until reauth)
        and when the server has no data for ``today``. The answer, found or not,
        is reused for 30 minutes.
        """
        cached = self._daily_summary_cache
        if (
            cached is not None
            and cached[0] == today
            and time.monotonic() - cached[1] < DAILY_SUMMARY_CACHE_SECONDS
        ):
            return cached[2]

        await self._ensure_token()

        def _call() -> DailySummaryDay | None:
            with self._client() as client:
                api = DataOverviewApi(client)
                try:
                    resp = api.data_overview_get_daily_summary(year=today.year)
                except ApiException as err:
                    if err.status == 403:
                        if not self._warned_missing_reports_scope:
                            self._warned_missing_reports_scope = True
                            _LOGGER.warning(
                                "Time in range needs the reports.read scope; "
                                "reauthenticate the Nocturne integration to enable it"
                            )
                        return None
                    raise
                today_iso = today.isoformat()
                for day in resp.days or []:
                    if day.var_date == today_iso:
                        return day
                return None

        day = await asyncio.to_thread(_call)
        self._daily_summary_cache = (today, time.monotonic(), day)
        return day

    async def get_sensor_age(self) -> DeviceAgeInfo | None:
        """Fetch sensor age information."""
        await self._ensure_token()

        def _call() -> DeviceAgeInfo | None:
            with self._client() as client:
                api = DeviceAgeApi(client)
                resp = api.device_age_get_sensor_age()
                return resp.sensor_start

        return await asyncio.to_thread(_call)

    async def create_glucose(
        self, mgdl: float, timestamp: datetime, data_source: str = ""
    ) -> SensorGlucose | None:
        """Post a sensor glucose reading."""
        await self._ensure_token()

        def _call() -> SensorGlucose | None:
            with self._client() as client:
                api = SensorGlucoseApi(client)
                req = UpsertSensorGlucoseRequest(
                    mgdl=mgdl, timestamp=timestamp, data_source=data_source
                )
                return api.sensor_glucose_create(req)

        return await asyncio.to_thread(_call)

    async def create_meter_glucose(
        self, mgdl: float, timestamp: datetime, data_source: str = ""
    ) -> MeterGlucose | None:
        """Post a fingerstick meter glucose reading."""
        await self._ensure_token()

        def _call() -> MeterGlucose | None:
            with self._client() as client:
                api = MeterGlucoseApi(client)
                req = UpsertMeterGlucoseRequest(
                    mgdl=mgdl, timestamp=timestamp, data_source=data_source
                )
                return api.meter_glucose_create(req)

        return await asyncio.to_thread(_call)

    async def create_carb_intake(
        self, request: CreateCarbIntakeRequest
    ) -> CarbIntake | None:
        """Post a carbohydrate intake record."""
        await self._ensure_token()

        def _call() -> CarbIntake | None:
            with self._client() as client:
                api = NutritionApi(client)
                return api.nutrition_create_carb_intake(request)

        return await asyncio.to_thread(_call)

    async def create_bolus(self, request: CreateBolusRequest) -> Bolus | None:
        """Post a bolus (insulin delivery) record."""
        await self._ensure_token()

        def _call() -> Bolus | None:
            with self._client() as client:
                api = BolusApi(client)
                return api.bolus_create(request)

        return await asyncio.to_thread(_call)

    async def create_state_span(
        self, request: CreateStateSpanRequest
    ) -> StateSpan | None:
        """Post a state span (activity, exercise, etc.) record."""
        await self._ensure_token()

        def _call() -> StateSpan | None:
            with self._client() as client:
                api = StateSpansApi(client)
                return api.state_spans_create_state_span(request)

        return await asyncio.to_thread(_call)
