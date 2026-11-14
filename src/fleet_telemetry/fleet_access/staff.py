"""Staff query business logic — pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

RETENTION_DAYS = 90
MAX_INTERVAL_HOURS = 24


class RouteValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def validate_route_interval(start: datetime, end: datetime, now: datetime) -> None:
    """Raise RouteValidationError if the route interval violates any contract rule."""
    retention_boundary = now - timedelta(days=RETENTION_DAYS)
    if end <= start:
        raise RouteValidationError(
            "invalid_range.end_not_after_start",
            "endExclusive must be after startInclusive",
        )
    if (end - start) > timedelta(hours=MAX_INTERVAL_HOURS):
        raise RouteValidationError(
            "invalid_range.interval_over_24_hours",
            "Interval must not exceed 24 hours",
        )
    if start < retention_boundary:
        raise RouteValidationError(
            "invalid_range.before_retention",
            "startInclusive is before the 90-day retention boundary",
        )


def format_vehicle_state(u1_response: dict[str, Any]) -> dict[str, Any]:
    """Map a U1 getCurrentState response to the U2 staff vehicle-state shape."""
    vehicle_id = u1_response["vehicleId"]
    if u1_response.get("outcome") == "noState":
        return {"vehicleId": vehicle_id, "availability": "noData"}
    return {
        "vehicleId": vehicle_id,
        "availability": "available",
        "position": u1_response.get("position"),
        "positionEventTime": u1_response.get("positionEventTime"),
        "lastReceivedAt": u1_response.get("lastReceivedAt"),
        "stale": u1_response.get("stale", False),
    }


def format_route_page(u1_response: dict[str, Any], vehicle_id: str) -> dict[str, Any]:
    """Map a U1 getRouteHistory response to the U2 staff route shape."""
    return {
        "vehicleId": vehicle_id,
        "points": u1_response.get("points", []),
        "nextContinuationToken": u1_response.get("nextContinuationToken"),
        "completeness": u1_response.get("completeness", "complete"),
    }
