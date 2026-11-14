"""Internal report-provider contract for U2; authorization remains outside U3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Callable

from .models import DailyVehicleSummary
from .repository import SummaryRepositoryPort
from .serialization import DurationEncoder, serialize_summary_csv, serialize_summary_json


class ProviderOutcome(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReportProviderResult:
    outcome: ProviderOutcome
    summary: DailyVehicleSummary | None = None
    content: str | None = None
    error_type: str | None = None


class InternalReportProvider:
    """Read summaries for U2 without adding an endpoint or an authorization layer."""

    def __init__(
        self,
        repository: SummaryRepositoryPort,
        *,
        duration_encoder: DurationEncoder,
    ) -> None:
        self._repository = repository
        self._duration_encoder = duration_encoder

    def get_summary(self, vehicle_id: str, utc_day: date) -> ReportProviderResult:
        return self._provide(vehicle_id, utc_day, serialize_summary_json)

    def download_csv(self, vehicle_id: str, utc_day: date) -> ReportProviderResult:
        return self._provide(vehicle_id, utc_day, serialize_summary_csv)

    def _provide(
        self,
        vehicle_id: str,
        utc_day: date,
        serializer: Callable[..., str],
    ) -> ReportProviderResult:
        try:
            summary = self._repository.get(vehicle_id, utc_day)
            if summary is None:
                return ReportProviderResult(ProviderOutcome.NOT_FOUND)
            content = serializer(summary, duration_encoder=self._duration_encoder)
            return ReportProviderResult(ProviderOutcome.FOUND, summary, content)
        except Exception as exc:
            return ReportProviderResult(ProviderOutcome.FAILED, error_type=type(exc).__name__)
