"""Canonical JSON and CSV serializers derived from one typed summary."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from .models import DailyVehicleSummary

DurationEncoder = Callable[[timedelta], str | int | float]

CSV_FIELDS = (
    "vehicleId",
    "utcDay",
    "distanceKm",
    "distanceKmCompleteness",
    "idleDuration",
    "idleDurationCompleteness",
    "fuelUsedPercentagePoints",
    "fuelUsedPercentagePointsCompleteness",
    "generatedAt",
)


def _wire_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def summary_to_mapping(
    summary: DailyVehicleSummary,
    *,
    duration_encoder: DurationEncoder,
) -> dict[str, Any]:
    """Build the canonical field mapping; duration wire encoding is injected."""
    idle_value: str | int | float | None = None
    if summary.idle_duration is not None:
        idle_value = duration_encoder(summary.idle_duration)
        if isinstance(idle_value, bool) or not isinstance(idle_value, (str, int, float)):
            raise TypeError("duration_encoder must return a JSON scalar")
    return {
        "vehicleId": summary.vehicle_id,
        "utcDay": summary.utc_day.isoformat(),
        "distanceKm": _json_number(summary.distance_km),
        "idleDuration": idle_value,
        "fuelUsedPercentagePoints": _json_number(summary.fuel_used_percentage_points),
        "metricCompleteness": {
            "distanceKm": summary.metric_completeness.distance_km.value,
            "idleDuration": summary.metric_completeness.idle_duration.value,
            "fuelUsedPercentagePoints": (
                summary.metric_completeness.fuel_used_percentage_points.value
            ),
        },
        "generatedAt": _wire_timestamp(summary.generated_at),
    }


def serialize_summary_json(
    summary: DailyVehicleSummary,
    *,
    duration_encoder: DurationEncoder,
) -> str:
    return json.dumps(
        summary_to_mapping(summary, duration_encoder=duration_encoder),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def serialize_summary_csv(
    summary: DailyVehicleSummary,
    *,
    duration_encoder: DurationEncoder,
) -> str:
    mapping = summary_to_mapping(summary, duration_encoder=duration_encoder)
    row = {
        "vehicleId": mapping["vehicleId"],
        "utcDay": mapping["utcDay"],
        "distanceKm": mapping["distanceKm"],
        "distanceKmCompleteness": mapping["metricCompleteness"]["distanceKm"],
        "idleDuration": mapping["idleDuration"],
        "idleDurationCompleteness": mapping["metricCompleteness"]["idleDuration"],
        "fuelUsedPercentagePoints": mapping["fuelUsedPercentagePoints"],
        "fuelUsedPercentagePointsCompleteness": (
            mapping["metricCompleteness"]["fuelUsedPercentagePoints"]
        ),
        "generatedAt": mapping["generatedAt"],
    }
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue()
