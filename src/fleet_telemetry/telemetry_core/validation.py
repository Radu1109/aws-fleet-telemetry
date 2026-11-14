"""Pure input validation and normalization for device telemetry messages."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    DiagnosticMeasurement,
    EngineState,
    TelemetryReading,
    as_utc,
    canonical_identity,
)


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _instant(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            instant = value
        elif isinstance(value, str):
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        return as_utc(instant)
    except (ValueError, OverflowError):
        return None


def validate_payload(
    payload: dict[str, Any],
    receipt_time: datetime,
) -> tuple[TelemetryReading | None, tuple[str, ...]]:
    """Normalize a contract-shaped payload; return all safe validation reasons."""
    reasons: list[str] = []
    receipt = as_utc(receipt_time)
    vehicle_id = payload.get("vehicleId")
    if not isinstance(vehicle_id, str) or not vehicle_id.strip():
        reasons.append("vehicle_id_required")
    event_time = _instant(payload.get("eventTime"))
    if event_time is None:
        reasons.append("event_time_invalid")
    event_id = payload.get("eventId")
    if event_id is not None and (not isinstance(event_id, str) or not event_id):
        reasons.append("event_id_invalid")
    raw_engine_state = payload.get("engineState")
    engine_state_reason = None
    if "engineState" not in payload:
        engine_state = EngineState.NOT_REPORTED
    elif isinstance(raw_engine_state, str) and raw_engine_state in {
        state.value for state in EngineState
    }:
        engine_state = EngineState(raw_engine_state)
    else:
        engine_state = EngineState.NOT_REPORTED
        engine_state_reason = "engine_state_invalid"

    numeric_fields = {
        "latitude": "latitude_invalid",
        "longitude": "longitude_invalid",
        "speedKmh": "speed_invalid",
        "fuelLevelPercent": "fuel_invalid",
    }
    values: dict[str, Decimal | None] = {}
    for field, reason in numeric_fields.items():
        values[field] = _number(payload.get(field))
        if values[field] is None:
            reasons.append(reason)
    latitude = values["latitude"]
    longitude = values["longitude"]
    speed = values["speedKmh"]
    fuel = values["fuelLevelPercent"]
    if latitude is not None and not Decimal("-90") <= latitude <= Decimal("90"):
        reasons.append("latitude_out_of_range")
    if longitude is not None and not Decimal("-180") <= longitude <= Decimal("180"):
        reasons.append("longitude_out_of_range")
    if speed is not None and speed < 0:
        reasons.append("speed_negative")
    if fuel is not None and not Decimal("0") <= fuel <= Decimal("100"):
        reasons.append("fuel_out_of_range")

    diagnostics: list[DiagnosticMeasurement] = []
    raw_diagnostics = payload.get("diagnosticMeasurements")
    if not isinstance(raw_diagnostics, list):
        reasons.append("diagnostics_invalid")
    else:
        for index, item in enumerate(raw_diagnostics):
            if not isinstance(item, dict):
                reasons.append(f"diagnostic_{index}_invalid")
                continue
            name, unit = item.get("name"), item.get("unit")
            value = _number(item.get("value"))
            if not isinstance(name, str) or not name.strip():
                reasons.append(f"diagnostic_{index}_name_invalid")
            if not isinstance(unit, str) or not unit.strip():
                reasons.append(f"diagnostic_{index}_unit_invalid")
            if value is None:
                reasons.append(f"diagnostic_{index}_value_invalid")
            if (
                isinstance(name, str) and name.strip()
                and isinstance(unit, str) and unit.strip()
                and value is not None
            ):
                diagnostics.append(DiagnosticMeasurement(name, value, unit))

    if event_time is not None:
        if event_time < receipt - timedelta(days=90):
            reasons.append("event_too_old")
        if event_time > receipt + timedelta(minutes=5):
            reasons.append("event_in_future")

    if reasons:
        return None, tuple(dict.fromkeys(reasons))
    if (
        event_time is None
        or not isinstance(vehicle_id, str)
        or latitude is None
        or longitude is None
        or speed is None
        or fuel is None
    ):
        raise RuntimeError("validation accepted telemetry with missing required values")
    identity = canonical_identity(vehicle_id, event_id, event_time)
    return (
        TelemetryReading(
            vehicle_id=vehicle_id,
            event_time=event_time,
            latitude=latitude,
            longitude=longitude,
            speed_kmh=speed,
            fuel_level_percent=fuel,
            diagnostic_measurements=tuple(diagnostics),
            event_id=event_id,
            receipt_time=receipt,
            canonical_id=identity,
            engine_state=engine_state,
            engine_state_reason=engine_state_reason,
        ),
        (),
    )
