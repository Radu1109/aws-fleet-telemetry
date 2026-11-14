"""Tests for staff query pure business logic."""

from datetime import datetime, timedelta, timezone

import pytest

from fleet_telemetry.fleet_access.staff import (
    RouteValidationError,
    format_route_page,
    format_vehicle_state,
    validate_route_interval,
)

NOW = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
RETENTION_BOUNDARY = NOW - timedelta(days=90)


# ---------- validate_route_interval ----------

def test_valid_interval_passes():
    start = NOW - timedelta(hours=2)
    end = NOW - timedelta(hours=1)
    validate_route_interval(start, end, NOW)  # must not raise


def test_end_not_after_start_raises():
    start = NOW - timedelta(hours=1)
    end = NOW - timedelta(hours=2)
    with pytest.raises(RouteValidationError) as exc_info:
        validate_route_interval(start, end, NOW)
    assert exc_info.value.code == "invalid_range.end_not_after_start"


def test_equal_start_end_raises():
    t = NOW - timedelta(hours=1)
    with pytest.raises(RouteValidationError) as exc_info:
        validate_route_interval(t, t, NOW)
    assert exc_info.value.code == "invalid_range.end_not_after_start"


def test_interval_over_24h_raises():
    start = NOW - timedelta(hours=25)
    end = NOW
    with pytest.raises(RouteValidationError) as exc_info:
        validate_route_interval(start, end, NOW)
    assert exc_info.value.code == "invalid_range.interval_over_24_hours"


def test_interval_exactly_24h_passes():
    start = NOW - timedelta(hours=24)
    end = NOW
    validate_route_interval(start, end, NOW)  # must not raise


def test_start_before_retention_raises():
    start = RETENTION_BOUNDARY - timedelta(seconds=1)
    end = start + timedelta(hours=1)
    with pytest.raises(RouteValidationError) as exc_info:
        validate_route_interval(start, end, NOW)
    assert exc_info.value.code == "invalid_range.before_retention"


def test_start_at_retention_boundary_passes():
    start = RETENTION_BOUNDARY
    end = start + timedelta(hours=1)
    validate_route_interval(start, end, NOW)  # must not raise


# ---------- format_vehicle_state ----------

def test_available_vehicle_maps_all_fields():
    u1 = {
        "vehicleId": "V-001",
        "outcome": "available",
        "position": {"latitude": 48.0, "longitude": 2.0},
        "positionEventTime": "2026-10-31T12:00:00Z",
        "lastReceivedAt": "2026-10-31T12:00:05Z",
        "stale": False,
    }
    result = format_vehicle_state(u1)
    assert result["availability"] == "available"
    assert result["vehicleId"] == "V-001"
    assert result["stale"] is False


def test_no_state_maps_to_no_data():
    u1 = {"vehicleId": "V-001", "outcome": "noState"}
    result = format_vehicle_state(u1)
    assert result == {"vehicleId": "V-001", "availability": "noData"}
    assert "position" not in result


def test_stale_defaults_to_false_when_absent():
    u1 = {"vehicleId": "V-001", "outcome": "available"}
    assert format_vehicle_state(u1)["stale"] is False


# ---------- format_route_page ----------

def test_route_page_maps_points_and_token():
    u1 = {
        "points": [{"eventTime": "2026-10-31T10:00:00Z"}],
        "nextContinuationToken": "tok-abc",
        "completeness": "complete",
    }
    result = format_route_page(u1, "V-001")
    assert result["vehicleId"] == "V-001"
    assert result["points"] == u1["points"]
    assert result["nextContinuationToken"] == "tok-abc"
    assert result["completeness"] == "complete"


def test_route_page_empty_points():
    u1 = {"points": [], "nextContinuationToken": None}
    result = format_route_page(u1, "V-002")
    assert result["points"] == []
    assert result["nextContinuationToken"] is None
