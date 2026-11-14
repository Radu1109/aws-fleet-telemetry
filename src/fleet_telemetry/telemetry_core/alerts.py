"""Deterministic alert evidence calculation over canonical event-time history."""

from __future__ import annotations

import heapq
import json
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .models import TelemetryReading, canonical_order

SPEED_LIMIT = 110
FUEL_DROP_LIMIT = 10
FUEL_LOOKBACK = timedelta(minutes=5)
ALERT_COOLDOWN = timedelta(minutes=10)


def _alert(
    reading: TelemetryReading,
    alert_type: str,
    contributors: list[TelemetryReading],
    measured_value: Decimal,
    threshold: str,
) -> dict[str, Any]:
    identity = json.dumps(
        (reading.vehicle_id, alert_type, reading.canonical_id),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "alertId": sha256(identity).hexdigest(),
        "vehicleId": reading.vehicle_id,
        "alertType": alert_type,
        "triggeringReadingIdentity": reading.canonical_id,
        "triggeringReadingIdentities": [item.canonical_id for item in contributors],
        "triggeringEventTimes": [item.event_time for item in contributors],
        "thresholdApplied": threshold,
        "measuredValue": measured_value,
        "raisedAt": reading.event_time,
        "effectiveState": "superseded",
    }


def evaluate_alerts(
    readings: list[TelemetryReading],
    cooldown: timedelta = ALERT_COOLDOWN,
) -> list[dict[str, Any]]:
    """Recompute candidate evidence and cooldown state deterministically."""
    if cooldown < timedelta(0):
        raise ValueError("cooldown cannot be negative")
    ordered = sorted(readings, key=canonical_order)
    candidates: list[dict[str, Any]] = []
    fuel_heap: list[tuple[Decimal, int]] = []
    left = 0
    for index, reading in enumerate(ordered):
        while (
            left < index
            and reading.event_time - ordered[left].event_time > FUEL_LOOKBACK
        ):
            left += 1
        while fuel_heap and fuel_heap[0][1] < left:
            heapq.heappop(fuel_heap)

        if index and reading.speed_kmh > SPEED_LIMIT:
            previous = ordered[index - 1]
            if previous.speed_kmh > SPEED_LIMIT:
                candidates.append(
                    _alert(
                        reading,
                        "excessive-speed",
                        [previous, reading],
                        reading.speed_kmh,
                        "two consecutive readings > 110 km/h",
                    )
                )

        if fuel_heap:
            _, contributor_index = fuel_heap[0]
            contributor = ordered[contributor_index]
            drop = contributor.fuel_level_percent - reading.fuel_level_percent
            if drop > FUEL_DROP_LIMIT:
                candidates.append(
                    _alert(
                        reading,
                        "sudden-fuel-drop",
                        [contributor, reading],
                        drop,
                        "fuel decrease > 10 percentage points within 5 minutes",
                    )
                )
        heapq.heappush(fuel_heap, (-reading.fuel_level_percent, index))

    last_effective: dict[tuple[str, str], Any] = {}
    for item in candidates:
        key = (item["vehicleId"], item["alertType"])
        prev_raised_at = last_effective.get(key)
        if prev_raised_at is None or item["raisedAt"] - prev_raised_at >= cooldown:
            item["effectiveState"] = "effective"
            last_effective[key] = item["raisedAt"]
    return candidates


def active_speed_condition(
    readings: list[TelemetryReading],
    alerts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the current effective speed condition, if one remains active."""
    return speed_condition_state(readings, alerts)[0]


def speed_condition_state(
    readings: list[TelemetryReading],
    alerts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, datetime | None]:
    """Return active evidence and the event time of the latest active-to-clear transition."""
    ordered = sorted(readings, key=canonical_order)
    effective_by_trigger = {
        item["triggeringReadingIdentity"]: item
        for item in (alerts if alerts is not None else evaluate_alerts(ordered))
        if item["alertType"] == "excessive-speed"
        and item["effectiveState"] == "effective"
    }
    active: dict[str, Any] | None = None
    cleared_at = None
    for index, reading in enumerate(ordered):
        if reading.speed_kmh <= SPEED_LIMIT:
            if active is not None:
                cleared_at = reading.event_time
            active = None
        elif index and ordered[index - 1].speed_kmh > SPEED_LIMIT:
            matching = effective_by_trigger.get(reading.canonical_id)
            if matching is not None:
                active = matching
                cleared_at = None
    return active, cleared_at
