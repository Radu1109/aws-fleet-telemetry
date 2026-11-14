from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Lock
from time import sleep

import pytest

from fleet_telemetry.daily_reporting.calculations import calculate_daily_summary
from fleet_telemetry.daily_reporting.generation import GenerationResult
from fleet_telemetry.daily_reporting.history import HistoryReadFailure
from fleet_telemetry.daily_reporting.job import DailyReportCoordinator
from fleet_telemetry.telemetry_core.models import TelemetryReading

UTC = timezone.utc
DAY = date(2026, 10, 10)
WATERMARK = datetime(2026, 10, 11, tzinfo=UTC)


def summary():
    events = [
        TelemetryReading(
            vehicle_id="v-1",
            event_time=datetime(2026, 10, 10, second=second, tzinfo=UTC),
            latitude=Decimal("0"),
            longitude=Decimal("0"),
            speed_kmh=Decimal("0"),
            fuel_level_percent=Decimal("50"),
            diagnostic_measurements=(),
            event_id=f"e-{second}",
            canonical_id=f"id-{second}",
        )
        for second in (0, 10)
    ]
    return calculate_daily_summary(
        "v-1",
        DAY,
        events,
        last_valid_reading_before_start=None,
        generated_at=WATERMARK,
    )


class FakeGenerator:
    def __init__(self, behaviors):
        self.behaviors = behaviors

    def generate(self, vehicle_id, _day):
        behavior = self.behaviors[vehicle_id]
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def test_vehicle_day_failures_are_independent_and_job_indicators_are_exposed():
    successes = {
        "v-1": GenerationResult("generated", summary()),
        "v-2": HistoryReadFailure("history unavailable"),
        "v-3": RuntimeError("write unavailable"),
    }
    coordinator = DailyReportCoordinator(
        FakeGenerator(successes),
        max_workers=2,
        monotonic_clock=iter((10.0, 12.5)).__next__,
        wall_clock=lambda: WATERMARK,
    )

    result = coordinator.run(DAY, ["v-1", "v-2", "v-3"])

    assert [item.outcome for item in result.items] == ["generated", "failed", "failed"]
    assert result.telemetry.outcome == "partial"
    assert result.telemetry.duration_seconds == 2.5
    assert result.telemetry.report_count == 1
    assert result.telemetry.history_read_failures == 1
    assert result.telemetry.last_success_at is None


def test_successful_job_updates_last_success_time_and_deduplicates_vehicle_ids():
    coordinator = DailyReportCoordinator(
        FakeGenerator({"v-1": GenerationResult("generated", summary())}),
        max_workers=1,
        wall_clock=lambda: WATERMARK,
    )

    result = coordinator.run(DAY, ["v-1", "v-1"])

    assert len(result.items) == 1
    assert result.telemetry.outcome == "succeeded"
    assert result.telemetry.last_success_at == WATERMARK


def test_successful_empty_vehicle_result_is_not_counted_as_a_report():
    coordinator = DailyReportCoordinator(
        FakeGenerator({"v-1": GenerationResult("no_data", None)}),
        max_workers=1,
        wall_clock=lambda: WATERMARK,
    )

    result = coordinator.run(DAY, ["v-1"])

    assert result.items[0].outcome == "no_data"
    assert result.telemetry.outcome == "succeeded"
    assert result.telemetry.report_count == 0


def test_worker_bound_is_validated_and_concurrency_never_exceeds_configuration():
    with pytest.raises(ValueError, match="max_workers"):
        DailyReportCoordinator(FakeGenerator({}), max_workers=0)

    lock = Lock()
    state = {"active": 0, "peak": 0}

    class SlowGenerator:
        def generate(self, vehicle_id, _day):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            sleep(0.02)
            with lock:
                state["active"] -= 1
            return GenerationResult("no_data", None)

    coordinator = DailyReportCoordinator(SlowGenerator(), max_workers=2)
    result = coordinator.run(DAY, [f"v-{index}" for index in range(6)])

    assert result.telemetry.outcome == "succeeded"
    assert 1 < state["peak"] <= 2


def test_partial_failure_does_not_replace_most_recent_successful_job_time():
    call = {"count": 0}

    class ChangesByRun:
        def generate(self, vehicle_id, _day):
            call["count"] += 1
            if call["count"] == 2:
                raise RuntimeError("one vehicle failed")
            return GenerationResult("no_data", None)

    times = iter((WATERMARK, WATERMARK.replace(hour=1)))
    coordinator = DailyReportCoordinator(
        ChangesByRun(), max_workers=1, wall_clock=lambda: next(times)
    )
    first = coordinator.run(DAY, ["v-1"])
    second = coordinator.run(DAY, ["v-1", "v-2"])

    assert first.telemetry.last_success_at == WATERMARK
    assert second.telemetry.outcome == "partial"
    assert second.telemetry.last_success_at == WATERMARK


def test_invalid_vehicle_identifiers_are_rejected_before_workers_start():
    coordinator = DailyReportCoordinator(FakeGenerator({}), max_workers=1)

    with pytest.raises(ValueError, match="vehicle IDs"):
        coordinator.run(DAY, [""])
