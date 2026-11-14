"""Typed values shared by U3's calculation, job, and provider layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum


class MetricStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class HistoryCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class MetricCompleteness:
    distance_km: MetricStatus
    idle_duration: MetricStatus
    fuel_used_percentage_points: MetricStatus

    def __post_init__(self) -> None:
        if any(
            not isinstance(status, MetricStatus)
            for status in (
                self.distance_km,
                self.idle_duration,
                self.fuel_used_percentage_points,
            )
        ):
            raise ValueError("metric completeness values must be MetricStatus members")


@dataclass(frozen=True, slots=True)
class DailyVehicleSummary:
    vehicle_id: str
    utc_day: date
    distance_km: Decimal | None
    idle_duration: timedelta | None
    fuel_used_percentage_points: Decimal | None
    metric_completeness: MetricCompleteness
    generated_at: datetime

    def __post_init__(self) -> None:
        if not self.vehicle_id:
            raise ValueError("vehicle_id must not be empty")
        if isinstance(self.utc_day, datetime) or not isinstance(self.utc_day, date):
            raise ValueError("utc_day must be a calendar date")
        if not isinstance(self.metric_completeness, MetricCompleteness):
            raise ValueError("metric_completeness must be a MetricCompleteness value")
        if not isinstance(self.generated_at, datetime):
            raise ValueError("generated_at must be a datetime")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(timezone.utc))
        for value, status, field_name in (
            (self.distance_km, self.metric_completeness.distance_km, "distance_km"),
            (self.idle_duration, self.metric_completeness.idle_duration, "idle_duration"),
            (
                self.fuel_used_percentage_points,
                self.metric_completeness.fuel_used_percentage_points,
                "fuel_used_percentage_points",
            ),
        ):
            if (value is None) != (status is MetricStatus.UNAVAILABLE):
                raise ValueError(f"{field_name} nullability must match unavailable status")
            if field_name == "idle_duration" and value is not None and not isinstance(
                value, timedelta
            ):
                raise ValueError("idle_duration must be a timedelta or None")
            if field_name != "idle_duration" and value is not None and not isinstance(
                value, Decimal
            ):
                raise ValueError(f"{field_name} must be a Decimal or None")
            if isinstance(value, Decimal) and (not value.is_finite() or value < 0):
                raise ValueError(f"{field_name} must be a finite non-negative value")
            if isinstance(value, timedelta) and value < timedelta(0):
                raise ValueError(f"{field_name} must not be negative")
