"""Bounded, independent vehicle/day job coordination and operational indicators."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic
from typing import Callable, Protocol

from .generation import GenerationResult
from .history import HistoryReadFailure
from .models import DailyVehicleSummary


class SummaryGenerationPort(Protocol):
    def generate(self, vehicle_id: str, utc_day: date) -> GenerationResult: ...


@dataclass(frozen=True, slots=True)
class VehicleJobResult:
    vehicle_id: str
    outcome: str
    summary: DailyVehicleSummary | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class JobTelemetry:
    outcome: str
    duration_seconds: float
    report_count: int
    history_read_failures: int
    last_success_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobRunResult:
    items: tuple[VehicleJobResult, ...]
    telemetry: JobTelemetry


class DailyReportCoordinator:
    def __init__(
        self,
        generator: SummaryGenerationPort,
        *,
        max_workers: int,
        monotonic_clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        self._generator = generator
        self._max_workers = max_workers
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._last_success_at: datetime | None = None

    def run(self, utc_day: date, vehicle_ids: list[str]) -> JobRunResult:
        if isinstance(utc_day, datetime) or not isinstance(utc_day, date):
            raise ValueError("utc_day must be a calendar date")
        if any(not isinstance(vehicle_id, str) or not vehicle_id for vehicle_id in vehicle_ids):
            raise ValueError("vehicle IDs must be non-empty strings")
        vehicles = list(dict.fromkeys(vehicle_ids))
        started = self._monotonic_clock()
        items, failures, history_failures = self._run_workers(vehicles, utc_day)
        finished = self._monotonic_clock()
        return self._build_result(items, failures, history_failures, finished - started)

    def _run_workers(
        self, vehicles: list[str], utc_day: date
    ) -> tuple[tuple[VehicleJobResult, ...], int, int]:
        results: dict[str, VehicleJobResult] = {}
        failures = 0
        history_failures = 0
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._generate_one, vehicle_id, utc_day): vehicle_id
                for vehicle_id in vehicles
            }
            for future in as_completed(futures):
                vehicle_id = futures[future]
                try:
                    results[vehicle_id] = future.result()
                except Exception as exc:
                    failures += 1
                    history_failures += int(isinstance(exc, HistoryReadFailure))
                    results[vehicle_id] = VehicleJobResult(
                        vehicle_id=vehicle_id,
                        outcome="failed",
                        summary=None,
                        error=type(exc).__name__,
                    )
        return tuple(results[vehicle_id] for vehicle_id in vehicles), failures, history_failures

    def _build_result(
        self,
        items: tuple[VehicleJobResult, ...],
        failures: int,
        history_failures: int,
        duration_seconds: float,
    ) -> JobRunResult:
        if failures == 0:
            completed_at = self._wall_clock()
            if completed_at.tzinfo is None or completed_at.utcoffset() is None:
                raise ValueError("wall_clock must return a timezone-aware datetime")
            self._last_success_at = completed_at.astimezone(timezone.utc)
        outcome = "succeeded" if failures == 0 else ("partial" if failures < len(items) else "failed")
        telemetry = JobTelemetry(
            outcome=outcome,
            duration_seconds=max(0.0, duration_seconds),
            report_count=sum(1 for item in items if item.outcome == "generated"),
            history_read_failures=history_failures,
            last_success_at=self._last_success_at,
        )
        return JobRunResult(items=items, telemetry=telemetry)

    def _generate_one(self, vehicle_id: str, utc_day: date) -> VehicleJobResult:
        result = self._generator.generate(vehicle_id, utc_day)
        return VehicleJobResult(vehicle_id, result.outcome, result.summary)
