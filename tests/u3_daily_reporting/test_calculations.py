from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fleet_telemetry.daily_reporting.calculations import (
    calculate_daily_summary,
    haversine_distance_meters,
)
from fleet_telemetry.telemetry_core.models import EngineState, TelemetryReading, canonical_identity

UTC = timezone.utc
DAY = date(2026, 10, 10)
MIDNIGHT = datetime(2026, 10, 10, tzinfo=UTC)
WATERMARK = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)


def reading(
    seconds: int,
    *,
    event_id: str | None = None,
    longitude: str | None = "0",
    latitude: str | None = "0",
    speed: str | None = "10",
    fuel: str | None = "50",
    engine: EngineState = EngineState.NOT_REPORTED,
    canonical_id: str | None = None,
) -> TelemetryReading:
    event_time = MIDNIGHT + timedelta(seconds=seconds)
    identity = canonical_identity("vehicle-1", event_id, event_time)
    return TelemetryReading(
        vehicle_id="vehicle-1",
        event_time=event_time,
        latitude=Decimal(latitude) if latitude is not None else None,
        longitude=Decimal(longitude) if longitude is not None else None,
        speed_kmh=Decimal(speed) if speed is not None else None,
        fuel_level_percent=Decimal(fuel) if fuel is not None else None,
        diagnostic_measurements=(),
        event_id=event_id,
        canonical_id=canonical_id or identity,
        engine_state=engine,
    )


def summary_for(
    readings: list[TelemetryReading],
    *,
    previous: TelemetryReading | None = None,
    history_completeness: str = "complete",
):
    return calculate_daily_summary(
        "vehicle-1",
        DAY,
        readings,
        last_valid_reading_before_start=previous,
        generated_at=WATERMARK,
        history_completeness=history_completeness,
    )


def test_readings_use_u1_canonical_order_and_duplicate_identity_once():
    duplicate = reading(0, event_id="same", longitude="0", canonical_id="canonical-1")
    next_reading = reading(10, event_id="next", longitude="0.001")

    summary = summary_for([next_reading, duplicate, duplicate])

    assert summary.distance_km == Decimal("0.11")
    assert summary.metric_completeness.distance_km.value == "complete"


def test_haversine_distance_uses_spherical_earth_radius_and_report_rounding():
    first = reading(0, longitude="0")
    second = reading(10, longitude="0.001")
    summary = summary_for(
        [first, second]
    )

    assert summary.distance_km == Decimal("0.11")
    assert abs(haversine_distance_meters(first, second) - 111.195) < 0.5


@pytest.mark.parametrize(
    ("gap_seconds", "longitude", "expected_km"),
    [
        (300, "0.001", Decimal("0.11")),
        (301, "0.001", Decimal("0.00")),
        (10, "0.01", Decimal("0.00")),
    ],
)
def test_distance_pair_gap_and_implied_speed_boundaries(
    gap_seconds, longitude, expected_km
):
    summary = summary_for(
        [
            reading(0, longitude="0"),
            reading(gap_seconds, longitude=longitude),
        ]
    )

    assert summary.distance_km == expected_km


def test_invalid_position_is_removed_before_pairing_and_marks_partial_result():
    summary = summary_for(
        [
            reading(0, longitude="0"),
            reading(10, latitude="95", longitude="0.001"),
            reading(20, longitude="0.002"),
        ]
    )

    assert summary.distance_km == Decimal("0.22")
    assert summary.metric_completeness.distance_km.value == "incomplete"


def test_idle_counts_on_and_not_reported_but_not_off_and_caps_each_interval():
    records = [
        reading(0, speed="0", engine=EngineState.ON),
        reading(120, speed="0", engine=EngineState.OFF),
        reading(180, speed="0", engine=EngineState.NOT_REPORTED),
        reading(300, speed="1", engine=EngineState.ON),
        reading(360, speed="0", engine=EngineState.ON),
    ]

    summary = summary_for(records)

    assert summary.idle_duration == timedelta(seconds=120)
    assert summary.metric_completeness.idle_duration.value == "complete"


def test_idle_speed_boundary_is_strictly_below_one():
    summary = summary_for(
        [
            reading(0, speed="1", engine=EngineState.ON),
            reading(20, speed="15", engine=EngineState.ON),
        ]
    )

    assert summary.idle_duration == timedelta(0)


def test_fuel_sums_decreases_without_netting_refuels_and_ignores_prior_day():
    previous = reading(-30, fuel="90")
    summary = summary_for(
        [
            reading(0, fuel="80"),
            reading(10, fuel="78"),
            reading(20, fuel="75"),
            reading(30, fuel="95"),
            reading(40, fuel="92"),
            reading(50, fuel="90"),
        ],
        previous=previous,
    )

    assert summary.fuel_used_percentage_points == Decimal("10")
    assert summary.metric_completeness.fuel_used_percentage_points.value == "complete"


def test_invalid_fuel_is_excluded_and_remaining_result_is_incomplete():
    summary = summary_for(
        [
            reading(0, fuel="80"),
            reading(10, fuel="101"),
            reading(20, fuel="75"),
        ]
    )

    assert summary.fuel_used_percentage_points == Decimal("5")
    assert summary.metric_completeness.fuel_used_percentage_points.value == "incomplete"


def test_completeness_is_independent_and_zero_is_not_unavailable():
    summary = summary_for(
        [
            reading(0, longitude="0", speed="20", fuel="50"),
            reading(10, longitude="0", speed="20", fuel="50"),
            reading(20, latitude=None, longitude=None, speed=None, fuel="50"),
            reading(30, longitude="0", speed="20", fuel="50"),
        ]
    )

    assert summary.distance_km == Decimal("0.00")
    assert summary.idle_duration == timedelta(0)
    assert summary.fuel_used_percentage_points == Decimal("0")
    assert summary.metric_completeness.distance_km.value == "incomplete"
    assert summary.metric_completeness.idle_duration.value == "incomplete"
    assert summary.metric_completeness.fuel_used_percentage_points.value == "complete"


def test_no_usable_pairs_are_unavailable_not_numeric_zero():
    summary = summary_for([reading(0, longitude="0", fuel="50", speed="3")])

    assert summary.distance_km is None
    assert summary.idle_duration is None
    assert summary.fuel_used_percentage_points is None
    assert summary.metric_completeness.distance_km.value == "unavailable"
    assert summary.metric_completeness.idle_duration.value == "unavailable"
    assert summary.metric_completeness.fuel_used_percentage_points.value == "unavailable"


def test_cross_midnight_distance_and_idle_belong_entirely_to_later_day():
    previous = TelemetryReading(
        vehicle_id="vehicle-1",
        event_time=MIDNIGHT - timedelta(seconds=20),
        latitude=Decimal("0"),
        longitude=Decimal("0"),
        speed_kmh=Decimal("0"),
        fuel_level_percent=Decimal("80"),
        diagnostic_measurements=(),
        event_id="before-midnight",
        canonical_id="before-midnight",
        engine_state=EngineState.ON,
    )
    after = reading(20, longitude="0.001", speed="0", fuel="70")

    summary = summary_for([after], previous=previous)

    assert summary.distance_km == Decimal("0.11")
    assert summary.idle_duration == timedelta(seconds=40)
    assert summary.fuel_used_percentage_points is None
