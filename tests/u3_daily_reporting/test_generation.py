from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fleet_telemetry.daily_reporting.generation import DailySummaryGenerator
from fleet_telemetry.daily_reporting.history import HistoryReadFailure, U1HistoryAdapter
from fleet_telemetry.daily_reporting.repository import InMemorySummaryRepository
from fleet_telemetry.daily_reporting.retries import RetryPolicy, TransientReportError
from fleet_telemetry.telemetry_core.models import TelemetryReading

UTC = timezone.utc
DAY = date(2026, 10, 10)
WATERMARK = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)
BOUNDARY = WATERMARK - timedelta(days=90)


def sample_reading(second: int, longitude: str, fuel: str) -> TelemetryReading:
    return TelemetryReading(
        vehicle_id="v-1",
        event_time=datetime(2026, 10, 10, tzinfo=UTC) + timedelta(seconds=second),
        latitude=Decimal("0"),
        longitude=Decimal(longitude),
        speed_kmh=Decimal("0"),
        fuel_level_percent=Decimal(fuel),
        diagnostic_measurements=(),
        event_id=f"e-{second}",
        canonical_id=f"id-{second}",
    )


def response(readings):
    return {
        "readings": readings,
        "lastValidReadingBeforeStart": None,
        "nextContinuationToken": None,
        "completeness": "complete",
        "retentionBoundary": BOUNDARY,
        "watermark": WATERMARK,
    }


class OnePageHistory:
    def __init__(self, result):
        self.result = result

    def read_canonical_history(self, *_args):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FailingWriteRepository(InMemorySummaryRepository):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure
        self.attempts = 0

    def replace(self, summary, *, expires_at):
        self.attempts += 1
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        super().replace(summary, expires_at=expires_at)


def generator(history_result, repository, *, write_attempts=1):
    history = U1HistoryAdapter(
        OnePageHistory(history_result),
        max_pages=2,
        retry_policy=RetryPolicy(1, 0, 0),
    )
    return DailySummaryGenerator(
        history,
        repository,
        write_retry_policy=RetryPolicy(write_attempts, 0, 0),
        sleep=lambda _delay: None,
    )


def test_generation_persists_summary_with_history_watermark_and_90_day_retention():
    repository = InMemorySummaryRepository()
    result = generator(
        response([sample_reading(0, "0", "50"), sample_reading(10, "0.001", "45")]),
        repository,
    ).generate("v-1", DAY)

    assert result.outcome == "generated"
    assert repository.get("v-1", DAY) == result.summary
    assert result.summary.generated_at == WATERMARK
    assert repository.expires_at("v-1", DAY) == WATERMARK + timedelta(days=90)


def test_empty_successful_history_does_not_create_summary():
    repository = InMemorySummaryRepository()

    result = generator(response([]), repository).generate("v-1", DAY)

    assert result.outcome == "no_data"
    assert result.summary is None
    assert repository.get("v-1", DAY) is None


def test_failed_history_read_keeps_previous_successful_summary():
    repository = InMemorySummaryRepository()
    old = generator(
        response([sample_reading(0, "0", "50"), sample_reading(10, "0", "45")]),
        repository,
    ).generate("v-1", DAY).summary

    with pytest.raises(HistoryReadFailure) as failure:
        generator(TransientReportError("history failed"), repository).generate("v-1", DAY)

    assert isinstance(failure.value.__cause__, TransientReportError)
    assert repository.get("v-1", DAY) == old


def test_failed_replacement_does_not_mutate_stored_summary():
    repository = InMemorySummaryRepository()
    old = generator(
        response([sample_reading(0, "0", "50"), sample_reading(10, "0", "45")]),
        repository,
    ).generate("v-1", DAY).summary
    replacement_repo = FailingWriteRepository(None)
    replacement_repo.replace(old, expires_at=WATERMARK + timedelta(days=90))
    replacement_repo.failure = TransientReportError("write failed")
    replacement = generator(
        response([sample_reading(0, "0", "50"), sample_reading(10, "0.002", "40")]),
        replacement_repo,
    )

    with pytest.raises(TransientReportError, match="write failed"):
        replacement.generate("v-1", DAY)

    assert replacement_repo.get("v-1", DAY) == old


def test_transient_summary_write_retries_with_configured_budget():
    repository = FailingWriteRepository(TransientReportError("temporary write"))
    result = generator(
        response([sample_reading(0, "0", "50"), sample_reading(10, "0", "45")]),
        repository,
        write_attempts=2,
    ).generate("v-1", DAY)

    assert result.outcome == "generated"
    assert repository.attempts == 2
    assert repository.get("v-1", DAY) == result.summary


def test_failed_write_exhaustion_is_reported_without_publishing():
    class AlwaysFailRepository(InMemorySummaryRepository):
        def replace(self, _summary, *, expires_at):
            raise TransientReportError("write exhausted")

    repository = AlwaysFailRepository()
    with pytest.raises(TransientReportError, match="write exhausted"):
        generator(
            response([sample_reading(0, "0", "50"), sample_reading(10, "0", "45")]),
            repository,
            write_attempts=2,
        ).generate("v-1", DAY)
    assert repository.get("v-1", DAY) is None
