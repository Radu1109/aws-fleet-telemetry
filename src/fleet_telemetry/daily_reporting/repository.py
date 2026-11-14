"""Injected summary persistence boundary and a deterministic in-memory adapter."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from threading import RLock
from typing import Protocol

from .models import DailyVehicleSummary

SUMMARY_RETENTION = timedelta(days=90)


class SummaryRepositoryPort(Protocol):
    def get(self, vehicle_id: str, utc_day: date) -> DailyVehicleSummary | None: ...

    def replace(
        self,
        summary: DailyVehicleSummary,
        *,
        expires_at: datetime,
    ) -> None: ...


class InMemorySummaryRepository:
    """Thread-safe test adapter; production persistence remains downstream-owned."""

    def __init__(self) -> None:
        self._summaries: dict[tuple[str, date], tuple[DailyVehicleSummary, datetime]] = {}
        self._lock = RLock()

    def get(self, vehicle_id: str, utc_day: date) -> DailyVehicleSummary | None:
        with self._lock:
            stored = self._summaries.get((vehicle_id, utc_day))
            return stored[0] if stored is not None else None

    def replace(
        self,
        summary: DailyVehicleSummary,
        *,
        expires_at: datetime,
    ) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        key = (summary.vehicle_id, summary.utc_day)
        with self._lock:
            self._summaries[key] = (summary, expires_at)

    def expires_at(self, vehicle_id: str, utc_day: date) -> datetime | None:
        with self._lock:
            stored = self._summaries.get((vehicle_id, utc_day))
            return stored[1] if stored is not None else None
