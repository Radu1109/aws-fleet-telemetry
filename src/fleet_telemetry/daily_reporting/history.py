"""Bounded pagination adapter for U1's fixed-watermark history contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Protocol, cast

from fleet_telemetry.telemetry_core.models import TelemetryReading, as_utc

from .models import HistoryCompleteness
from .retries import RetryPolicy, retry_call


class U1HistoryPort(Protocol):
    def read_canonical_history(
        self,
        vehicle_id: str,
        start_inclusive: datetime,
        end_exclusive: datetime,
        continuation_token: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CanonicalHistorySnapshot:
    readings: tuple[TelemetryReading, ...]
    last_valid_reading_before_start: TelemetryReading | None
    watermark: datetime
    completeness: HistoryCompleteness
    retention_boundary: datetime


class HistoryContractError(RuntimeError):
    """The U1 response violated the pagination or snapshot contract."""


class HistoryReadFailure(RuntimeError):
    """Canonical history could not be read completely."""


@dataclass(slots=True)
class _PageCollection:
    watermark: datetime | None = None
    retention_boundary: datetime | None = None
    previous: TelemetryReading | None = None
    completeness: HistoryCompleteness = HistoryCompleteness.COMPLETE
    readings: list[TelemetryReading] = field(default_factory=list)
    seen_tokens: set[str] = field(default_factory=set)

    def remember_token(self, token: str | None) -> None:
        if token is not None and token in self.seen_tokens:
            raise HistoryContractError("U1 repeated a continuation token")
        if token is not None:
            self.seen_tokens.add(token)

    def add_page(
        self,
        response: dict[str, Any],
        vehicle_id: str,
        start: datetime,
    ) -> str | None:
        watermark = U1HistoryAdapter._aware_timestamp(response.get("watermark"), "watermark")
        boundary = U1HistoryAdapter._aware_timestamp(
            response.get("retentionBoundary"), "retentionBoundary"
        )
        self._capture_snapshot(response, vehicle_id, start, watermark, boundary)
        self._capture_completeness(response)
        self._append_readings(response)
        return self._next_token(response)

    def _capture_snapshot(
        self,
        response: dict[str, Any],
        vehicle_id: str,
        start: datetime,
        watermark: datetime,
        boundary: datetime,
    ) -> None:
        if self.watermark is not None:
            if watermark != self.watermark or boundary != self.retention_boundary:
                raise HistoryContractError("U1 changed the history snapshot across pages")
            return
        self.watermark, self.retention_boundary = watermark, boundary
        self.previous = response.get("lastValidReadingBeforeStart")
        if self.previous is not None and not isinstance(self.previous, TelemetryReading):
            raise HistoryContractError("U1 predecessor has an unexpected type")
        if self.previous is not None:
            if self.previous.vehicle_id != vehicle_id:
                raise HistoryContractError("U1 predecessor belongs to another vehicle")
            try:
                previous_time = as_utc(self.previous.event_time)
            except ValueError as exc:
                raise HistoryContractError("U1 predecessor must have an aware timestamp") from exc
            if previous_time >= start:
                raise HistoryContractError("U1 predecessor is not before the interval")

    def _capture_completeness(self, response: dict[str, Any]) -> None:
        try:
            page_status = HistoryCompleteness(cast(str, response.get("completeness")))
        except ValueError as exc:
            raise HistoryContractError("U1 returned an unknown completeness value") from exc
        if page_status is HistoryCompleteness.PARTIAL:
            self.completeness = HistoryCompleteness.PARTIAL

    def _append_readings(self, response: dict[str, Any]) -> None:
        page_readings = response.get("readings")
        if not isinstance(page_readings, list):
            raise HistoryContractError("U1 readings must be a list")
        if any(not isinstance(item, TelemetryReading) for item in page_readings):
            raise HistoryContractError("U1 returned a reading with an unexpected type")
        self.readings.extend(page_readings)

    @staticmethod
    def _next_token(response: dict[str, Any]) -> str | None:
        token = response.get("nextContinuationToken")
        if token is not None and (not isinstance(token, str) or not token):
            raise HistoryContractError("U1 returned an invalid continuation token")
        return token

    def finish(self) -> CanonicalHistorySnapshot:
        if self.watermark is None or self.retention_boundary is None:
            raise HistoryContractError("U1 did not establish the history snapshot")
        return CanonicalHistorySnapshot(
            readings=tuple(self.readings),
            last_valid_reading_before_start=self.previous,
            watermark=self.watermark,
            completeness=self.completeness,
            retention_boundary=self.retention_boundary,
        )


class U1HistoryAdapter:
    def __init__(
        self,
        port: U1HistoryPort,
        *,
        max_pages: int,
        retry_policy: RetryPolicy,
        retryable: Callable[[Exception], bool] | None = None,
        sleep: Callable[[float], None] | None = None,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least one")
        self._port = port
        self._max_pages = max_pages
        self._retry_policy = retry_policy
        self._retryable = retryable
        self._sleep = sleep
        self._random_value = random_value

    def read_all(self, vehicle_id: str, utc_day: date) -> CanonicalHistorySnapshot:
        if not vehicle_id:
            raise ValueError("vehicle_id must not be empty")
        if isinstance(utc_day, datetime) or not isinstance(utc_day, date):
            raise ValueError("utc_day must be a calendar date")
        start = datetime.combine(utc_day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        token: str | None = None
        collection = _PageCollection()
        for _page_number in range(self._max_pages):
            collection.remember_token(token)
            response = self._fetch(vehicle_id, start, end, token)
            token = collection.add_page(response, vehicle_id, start)
            if token is None:
                return collection.finish()
        raise HistoryContractError(f"U1 history exceeded the configured {self._max_pages}-page bound")

    def _fetch(
        self,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        token: str | None,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            return self._port.read_canonical_history(vehicle_id, start, end, token)

        options: dict[str, Any] = {}
        if self._retryable is not None:
            options["retryable"] = self._retryable
        if self._sleep is not None:
            options["sleep"] = self._sleep
        if self._random_value is not None:
            options["random_value"] = self._random_value
        response = retry_call(operation, self._retry_policy, **options)
        if not isinstance(response, dict):
            raise HistoryContractError("U1 returned a non-object response")
        return response

    @staticmethod
    def _aware_timestamp(value: object, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise HistoryContractError(f"U1 {field_name} must be a datetime")
        try:
            return as_utc(value)
        except ValueError as exc:
            raise HistoryContractError(f"U1 {field_name} must be timezone-aware") from exc
