"""Generate a complete vehicle/day summary before atomically replacing its prior value."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from .calculations import calculate_daily_summary
from .history import HistoryReadFailure, U1HistoryAdapter
from .models import DailyVehicleSummary
from .repository import SUMMARY_RETENTION, SummaryRepositoryPort
from .retries import RetryPolicy, retry_call


@dataclass(frozen=True, slots=True)
class GenerationResult:
    outcome: str
    summary: DailyVehicleSummary | None


class DailySummaryGenerator:
    def __init__(
        self,
        history: U1HistoryAdapter,
        repository: SummaryRepositoryPort,
        *,
        write_retry_policy: RetryPolicy,
        retryable_write: Callable[[Exception], bool] | None = None,
        sleep: Callable[[float], None] | None = None,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        self._history = history
        self._repository = repository
        self._write_retry_policy = write_retry_policy
        self._retryable_write = retryable_write
        self._sleep = sleep
        self._random_value = random_value

    def generate(self, vehicle_id: str, utc_day: date) -> GenerationResult:
        try:
            snapshot = self._history.read_all(vehicle_id, utc_day)
        except Exception as exc:
            raise HistoryReadFailure("canonical history read failed") from exc
        if not snapshot.readings:
            return GenerationResult(outcome="no_data", summary=None)
        summary = calculate_daily_summary(
            vehicle_id,
            utc_day,
            snapshot.readings,
            last_valid_reading_before_start=snapshot.last_valid_reading_before_start,
            generated_at=snapshot.watermark,
            history_completeness=snapshot.completeness,
        )
        options: dict[str, Any] = {}
        if self._retryable_write is not None:
            options["retryable"] = self._retryable_write
        if self._sleep is not None:
            options["sleep"] = self._sleep
        if self._random_value is not None:
            options["random_value"] = self._random_value
        retry_call(
            lambda: self._repository.replace(
                summary,
                expires_at=summary.generated_at + SUMMARY_RETENTION,
            ),
            self._write_retry_policy,
            **options,
        )
        return GenerationResult(outcome="generated", summary=summary)
