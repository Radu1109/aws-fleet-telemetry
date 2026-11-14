"""Typed domain objects and deterministic telemetry identity/order helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any


class EngineState(StrEnum):
    """Reported engine state, kept separate from numeric diagnostics."""

    ON = "ON"
    OFF = "OFF"
    NOT_REPORTED = "NOT_REPORTED"


@dataclass(frozen=True, slots=True)
class DiagnosticMeasurement:
    name: str
    value: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class TelemetryReading:
    vehicle_id: str
    event_time: datetime
    latitude: Decimal
    longitude: Decimal
    speed_kmh: Decimal
    fuel_level_percent: Decimal
    diagnostic_measurements: tuple[DiagnosticMeasurement, ...]
    event_id: str | None = None
    receipt_time: datetime | None = None
    canonical_id: str = ""
    engine_state: EngineState = EngineState.NOT_REPORTED
    engine_state_reason: str | None = None


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime; reject ambiguous naive timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def canonical_identity(vehicle_id: str, event_id: str | None, event_time: datetime) -> str:
    """Hash the identity tuple without conflating ID-based and time-based keys."""
    identity = ("event-id", vehicle_id, event_id) if event_id is not None else (
        "event-time",
        vehicle_id,
        as_utc(event_time).isoformat(timespec="microseconds"),
    )
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def canonical_order(reading: TelemetryReading) -> tuple[datetime, int, bytes]:
    """Canonical ascending order; ID-bearing readings sort before absent IDs at ties."""
    return (
        as_utc(reading.event_time),
        0 if reading.event_id is not None else 1,
        reading.event_id.encode("utf-8") if reading.event_id is not None else b"",
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return as_utc(value).isoformat(timespec="microseconds")
    if isinstance(value, DiagnosticMeasurement):
        return {"name": value.name, "value": str(value.value), "unit": value.unit}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    return value


def payload_digest(payload: dict[str, Any]) -> str:
    """Return stable content digest used to distinguish exact transport retries."""
    content = {key: value for key, value in payload.items() if key not in {"signature", "proof"}}
    encoded = json.dumps(_json_value(content), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
