"""Pure daily metric calculations over U1 canonical readings."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from fleet_telemetry.telemetry_core.models import (
    EngineState,
    TelemetryReading,
    as_utc,
    canonical_identity,
    canonical_order,
)

from .models import (
    DailyVehicleSummary,
    HistoryCompleteness,
    MetricCompleteness,
    MetricStatus,
)

EARTH_RADIUS_METERS = 6_371_000.0
MAX_DISTANCE_GAP_SECONDS = 300
MAX_DISTANCE_SPEED_KMH = 200.0
MAX_IDLE_INTERVAL = timedelta(seconds=60)


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _fuel_level(value: object) -> Decimal | None:
    try:
        level = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    return level if level.is_finite() and Decimal(0) <= level <= Decimal(100) else None


def haversine_distance_meters(first: TelemetryReading, second: TelemetryReading) -> float:
    """Return great-circle distance using the U1-approved spherical Earth radius."""
    lat1, lon1 = _finite_number(first.latitude), _finite_number(first.longitude)
    lat2, lon2 = _finite_number(second.latitude), _finite_number(second.longitude)
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        raise ValueError("both readings must contain numeric coordinates")
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        raise ValueError("latitude must be between -90 and 90 degrees")
    if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        raise ValueError("longitude must be between -180 and 180 degrees")
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    central_angle = 2 * math.atan2(math.sqrt(haversine), math.sqrt(max(0.0, 1 - haversine)))
    return EARTH_RADIUS_METERS * central_angle


def _valid_position(reading: TelemetryReading) -> bool:
    latitude = _finite_number(reading.latitude)
    longitude = _finite_number(reading.longitude)
    return (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _unique_ordered(readings: Iterable[TelemetryReading]) -> list[TelemetryReading]:
    ordered = sorted(readings, key=canonical_order)
    seen: set[str] = set()
    unique: list[TelemetryReading] = []
    for reading in ordered:
        identity = reading.canonical_id or canonical_identity(
            reading.vehicle_id, reading.event_id, reading.event_time
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(reading)
    return unique


def _status(value: object | None, incomplete: bool, history_partial: bool) -> MetricStatus:
    if value is None:
        return MetricStatus.UNAVAILABLE
    if incomplete or history_partial:
        return MetricStatus.INCOMPLETE
    return MetricStatus.COMPLETE


def _distance(
    ordered: list[TelemetryReading],
    day_start: datetime,
    day_end: datetime,
    history_partial: bool,
) -> tuple[Decimal | None, MetricStatus]:
    invalid_input = any(
        day_start <= as_utc(item.event_time) < day_end and not _valid_position(item)
        for item in ordered
    )
    positions = [item for item in ordered if _valid_position(item)]
    if len(positions) < 2:
        return None, MetricStatus.UNAVAILABLE
    distance_meters = 0.0
    for earlier, later in zip(positions, positions[1:]):
        later_time = as_utc(later.event_time)
        if not day_start <= later_time < day_end:
            continue
        gap_seconds = (later_time - as_utc(earlier.event_time)).total_seconds()
        if gap_seconds <= 0 or gap_seconds > MAX_DISTANCE_GAP_SECONDS:
            continue
        pair_distance = haversine_distance_meters(earlier, later)
        if pair_distance / gap_seconds * 3.6 > MAX_DISTANCE_SPEED_KMH:
            continue
        distance_meters += pair_distance
    value = (Decimal(str(distance_meters / 1000))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return value, _status(value, invalid_input, history_partial)


def _idle(
    ordered: list[TelemetryReading],
    day_start: datetime,
    day_end: datetime,
    history_partial: bool,
) -> tuple[timedelta | None, MetricStatus]:
    total = timedelta(0)
    usable_intervals = 0
    invalid_input = False
    for earlier, later in zip(ordered, ordered[1:]):
        later_time = as_utc(later.event_time)
        if not day_start <= later_time < day_end:
            continue
        speed = _finite_number(earlier.speed_kmh)
        if speed is None or speed < 0:
            invalid_input = True
            continue
        usable_intervals += 1
        engine = earlier.engine_state
        if engine is EngineState.OFF or engine == EngineState.OFF.value:
            continue
        if speed < 1:
            elapsed = later_time - as_utc(earlier.event_time)
            if elapsed > timedelta(0):
                total += min(elapsed, MAX_IDLE_INTERVAL)
    if usable_intervals == 0:
        return None, MetricStatus.UNAVAILABLE
    return total, _status(total, invalid_input, history_partial)


def _fuel(
    readings: list[TelemetryReading],
    day_start: datetime,
    day_end: datetime,
    history_partial: bool,
) -> tuple[Decimal | None, MetricStatus]:
    in_day = [item for item in readings if day_start <= as_utc(item.event_time) < day_end]
    valid: list[Decimal] = []
    invalid_input = False
    for item in in_day:
        level = _fuel_level(item.fuel_level_percent)
        if level is None:
            invalid_input = True
        else:
            valid.append(level)
    if len(valid) < 2:
        return None, MetricStatus.UNAVAILABLE
    value = sum(
        (max(Decimal(0), earlier - later) for earlier, later in zip(valid, valid[1:])),
        start=Decimal(0),
    ).normalize()
    return value, _status(value, invalid_input, history_partial)


def _prepare_calculation(
    vehicle_id: str,
    utc_day: date,
    generated_at: datetime,
    history_completeness: HistoryCompleteness | str,
) -> tuple[datetime, datetime, datetime, HistoryCompleteness]:
    if not vehicle_id:
        raise ValueError("vehicle_id must not be empty")
    if isinstance(utc_day, datetime) or not isinstance(utc_day, date):
        raise ValueError("utc_day must be a calendar date")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    try:
        completeness = HistoryCompleteness(history_completeness)
    except ValueError as exc:
        raise ValueError("history_completeness must be complete or partial") from exc
    day_start = datetime.combine(utc_day, time.min, tzinfo=timezone.utc)
    return day_start, day_start + timedelta(days=1), as_utc(generated_at), completeness


def _readings_in_day(
    vehicle_id: str,
    readings: Iterable[TelemetryReading],
    start: datetime,
    end: datetime,
) -> list[TelemetryReading]:
    return [
        item
        for item in readings
        if item.vehicle_id == vehicle_id and start <= as_utc(item.event_time) < end
    ]


def calculate_daily_summary(
    vehicle_id: str,
    utc_day: date,
    readings: Iterable[TelemetryReading],
    *,
    last_valid_reading_before_start: TelemetryReading | None,
    generated_at: datetime,
    history_completeness: HistoryCompleteness | str = HistoryCompleteness.COMPLETE,
) -> DailyVehicleSummary:
    """Calculate distance, idle duration, and fuel with independent completeness."""
    day_start, day_end, watermark, completeness = _prepare_calculation(
        vehicle_id, utc_day, generated_at, history_completeness
    )
    in_day = _readings_in_day(vehicle_id, readings, day_start, day_end)
    ordered = _unique_ordered(
        ([last_valid_reading_before_start] if last_valid_reading_before_start else []) + in_day
    )
    history_partial = completeness is HistoryCompleteness.PARTIAL
    distance, distance_status = _distance(ordered, day_start, day_end, history_partial)
    idle, idle_status = _idle(ordered, day_start, day_end, history_partial)
    fuel, fuel_status = _fuel(in_day, day_start, day_end, history_partial)
    return DailyVehicleSummary(
        vehicle_id=vehicle_id,
        utc_day=utc_day,
        distance_km=distance,
        idle_duration=idle,
        fuel_used_percentage_points=fuel,
        metric_completeness=MetricCompleteness(
            distance_km=distance_status,
            idle_duration=idle_status,
            fuel_used_percentage_points=fuel_status,
        ),
        generated_at=watermark,
    )
