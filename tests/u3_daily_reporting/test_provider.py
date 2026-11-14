import csv
import io
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fleet_telemetry.daily_reporting.models import (
    DailyVehicleSummary,
    MetricCompleteness,
    MetricStatus,
)
from fleet_telemetry.daily_reporting.provider import (
    InternalReportProvider,
    ProviderOutcome,
)
from fleet_telemetry.daily_reporting.repository import InMemorySummaryRepository

UTC = timezone.utc
DAY = date(2026, 10, 10)
WATERMARK = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)


def report(vehicle_id="v-1"):
    return DailyVehicleSummary(
        vehicle_id=vehicle_id,
        utc_day=DAY,
        distance_km=Decimal("0.00"),
        idle_duration=None,
        fuel_used_percentage_points=Decimal("4.5"),
        metric_completeness=MetricCompleteness(
            distance_km=MetricStatus.COMPLETE,
            idle_duration=MetricStatus.UNAVAILABLE,
            fuel_used_percentage_points=MetricStatus.INCOMPLETE,
        ),
        generated_at=WATERMARK,
    )


def test_json_is_canonical_and_preserves_null_zero_and_completeness():
    repository = InMemorySummaryRepository()
    repository.replace(report(), expires_at=WATERMARK + timedelta(days=90))
    provider = InternalReportProvider(
        repository,
        duration_encoder=lambda duration: duration.total_seconds(),
    )

    result = provider.get_summary("v-1", DAY)

    content = json.loads(result.content)
    assert result.outcome is ProviderOutcome.FOUND
    assert content["distanceKm"] == 0
    assert content["idleDuration"] is None
    assert content["fuelUsedPercentagePoints"] == 4.5
    assert content["metricCompleteness"] == {
        "distanceKm": "complete",
        "idleDuration": "unavailable",
        "fuelUsedPercentagePoints": "incomplete",
    }


def test_csv_is_derived_from_the_same_summary_and_keeps_null_cell_blank():
    repository = InMemorySummaryRepository()
    repository.replace(report(), expires_at=WATERMARK + timedelta(days=90))
    provider = InternalReportProvider(repository, duration_encoder=lambda _duration: "duration")

    result = provider.download_csv("v-1", DAY)
    row = next(csv.DictReader(io.StringIO(result.content)))
    canonical = json.loads(provider.get_summary("v-1", DAY).content)

    assert row["distanceKm"] == "0"
    assert row["distanceKmCompleteness"] == "complete"
    assert row["idleDuration"] == ""
    assert row["idleDurationCompleteness"] == "unavailable"
    assert row["fuelUsedPercentagePoints"] == "4.5"
    assert row["fuelUsedPercentagePointsCompleteness"] == "incomplete"
    assert row["vehicleId"] == canonical["vehicleId"]
    assert row["utcDay"] == canonical["utcDay"]
    assert row["generatedAt"] == canonical["generatedAt"]
    assert row["distanceKm"] == str(canonical["distanceKm"])
    assert row["idleDuration"] == ""
    assert row["fuelUsedPercentagePoints"] == str(canonical["fuelUsedPercentagePoints"])


def test_csv_escapes_special_characters_in_vehicle_identity():
    repository = InMemorySummaryRepository()
    vehicle_id = 'vehicle,"one"\nline'
    repository.replace(report(vehicle_id), expires_at=WATERMARK + timedelta(days=90))
    provider = InternalReportProvider(repository, duration_encoder=lambda _duration: 0)

    result = provider.download_csv(vehicle_id, DAY)
    rows = list(csv.DictReader(io.StringIO(result.content)))

    assert len(rows) == 1
    assert rows[0]["vehicleId"] == vehicle_id


def test_provider_distinguishes_missing_summary_from_query_failure():
    provider = InternalReportProvider(
        InMemorySummaryRepository(),
        duration_encoder=lambda _duration: 0,
    )

    assert provider.get_summary("v-1", DAY).outcome is ProviderOutcome.NOT_FOUND

    class FailedRepository:
        def get(self, *_args):
            raise OSError("store read failed")

    failed = InternalReportProvider(
        FailedRepository(),
        duration_encoder=lambda _duration: 0,
    ).get_summary("v-1", DAY)
    assert failed.outcome is ProviderOutcome.FAILED
    assert failed.error_type == "OSError"
    assert failed.content is None


def test_provider_requires_no_user_identity_or_u3_authorization():
    repository = InMemorySummaryRepository()
    repository.replace(report(), expires_at=WATERMARK + timedelta(days=90))
    provider = InternalReportProvider(
        repository,
        duration_encoder=lambda _duration: 0,
    )

    result = provider.get_summary("v-1", DAY)

    assert result.outcome is ProviderOutcome.FOUND
    assert result.summary.vehicle_id == "v-1"


def test_duration_wire_representation_is_injected_for_both_formats():
    repository = InMemorySummaryRepository()
    value = replace(
        report(),
        idle_duration=timedelta(seconds=12),
        metric_completeness=MetricCompleteness(
            MetricStatus.COMPLETE,
            MetricStatus.COMPLETE,
            MetricStatus.INCOMPLETE,
        ),
    )
    repository.replace(value, expires_at=WATERMARK + timedelta(days=90))
    provider = InternalReportProvider(
        repository,
        duration_encoder=lambda duration: f"duration:{duration.total_seconds()}",
    )

    json_result = provider.get_summary("v-1", DAY)
    csv_result = provider.download_csv("v-1", DAY)

    assert json.loads(json_result.content)["idleDuration"] == "duration:12.0"
    row = next(csv.DictReader(io.StringIO(csv_result.content)))
    assert row["idleDuration"] == "duration:12.0"
