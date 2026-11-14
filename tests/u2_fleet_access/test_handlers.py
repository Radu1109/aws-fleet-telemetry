"""Tests for Lambda handler dispatch and response shaping."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from fleet_telemetry.fleet_access import staff_handler, thirdparty_handler


def _staff_event(route_key: str, path: dict = {}, qs: dict = {}) -> dict[str, Any]:
    return {"routeKey": route_key, "pathParameters": path, "queryStringParameters": qs}


def _thirdparty_event(vehicle_id: str, client_id: str) -> dict[str, Any]:
    return {
        "routeKey": "GET /v1/vehicles/{vehicleId}/status",
        "pathParameters": {"vehicleId": vehicle_id},
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"client_id": client_id}}}
        },
    }


_U1_AVAILABLE = {
    "vehicleId": "V-001",
    "outcome": "available",
    "position": {"latitude": 48.0, "longitude": 2.0},
    "positionEventTime": "2026-10-31T12:00:00Z",
    "lastReceivedAt": "2026-10-31T12:00:05Z",
    "stale": False,
}

_AUTHORIZED_ITEM = {"authorizedVehicleIds": {"V-001"}}


class _FakeU1:
    def get_current_state(self, vehicle_id: str) -> dict[str, Any]:
        return _U1_AVAILABLE

    def list_effective_alerts(self, vehicle_id: str) -> dict[str, Any]:
        return {"alerts": [{"alertId": "a1", "alertType": "excessive-speed"}]}

    def get_route_history(self, vehicle_id: str, start: Any, end: Any, token: Any = None) -> dict[str, Any]:
        return {"points": [], "nextContinuationToken": None, "completeness": "complete"}


class _FakeU3:
    def get_summary(self, vehicle_id: str, utc_day: str) -> dict[str, Any]:
        return {"summary": {"vehicleId": vehicle_id, "utcDay": utc_day}}

    def download_csv(self, vehicle_id: str, utc_day: str) -> dict[str, Any]:
        return {"csv": f"vehicleId,utcDay\n{vehicle_id},{utc_day}\n"}


class _FakeAuth:
    def get_client_item(self, client_id: str) -> dict[str, Any] | None:
        return _AUTHORIZED_ITEM if client_id == "client-1" else None


# ---------- staff handler ----------

def test_staff_vehicle_state_returns_200():
    resp = staff_handler._dispatch("GET /vehicles/{vehicleId}", {"vehicleId": "V-001"}, {}, _FakeU1(), _FakeU3())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["vehicleId"] == "V-001"
    assert body["availability"] == "available"


def test_staff_alerts_returns_200():
    resp = staff_handler._dispatch("GET /vehicles/{vehicleId}/alerts", {"vehicleId": "V-001"}, {}, _FakeU1(), _FakeU3())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "alerts" in body
    assert body["vehicleId"] == "V-001"


def test_staff_route_valid_interval_returns_200():
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=2)).isoformat()
    end = (now - timedelta(hours=1)).isoformat()
    resp = staff_handler._dispatch(
        "GET /vehicles/{vehicleId}/route",
        {"vehicleId": "V-001"},
        {"startInclusive": start, "endExclusive": end},
        _FakeU1(), _FakeU3(),
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["completeness"] == "complete"


def test_staff_route_invalid_interval_returns_400():
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now - timedelta(hours=2)).isoformat()
    # RouteValidationError is caught by handle(); call via _dispatch then wrap
    with pytest.raises(Exception):
        staff_handler._dispatch(
            "GET /vehicles/{vehicleId}/route",
            {"vehicleId": "V-001"},
            {"startInclusive": start, "endExclusive": end},
            _FakeU1(), _FakeU3(),
        )
    # Verify the top-level handle wraps it to HTTP 400
    from fleet_telemetry.fleet_access import staff_handler as sh
    from fleet_telemetry.fleet_access.staff import RouteValidationError
    original_dispatch = sh._dispatch
    def _patched(*args, **kwargs):
        raise RouteValidationError("invalid_range.end_not_after_start", "end not after start")
    sh._dispatch = _patched
    try:
        with (
            patch("fleet_telemetry.fleet_access.staff_handler.LambdaU1QueryPort", return_value=_FakeU1()),
            patch("fleet_telemetry.fleet_access.staff_handler.LambdaU3ReportPort", return_value=_FakeU3()),
        ):
            event = _staff_event(
                "GET /vehicles/{vehicleId}/route",
                {"vehicleId": "V-001"},
                {"startInclusive": start, "endExclusive": end},
            )
            resp = sh.handle(event, None)
        assert resp["statusCode"] == 400
        assert "invalid_range" in json.loads(resp["body"])["error"]
    finally:
        sh._dispatch = original_dispatch


def test_staff_report_returns_200():
    resp = staff_handler._dispatch(
        "GET /vehicles/{vehicleId}/reports/{utcDay}",
        {"vehicleId": "V-001", "utcDay": "2026-10-10"},
        {},
        _FakeU1(), _FakeU3(),
    )
    assert resp["statusCode"] == 200


def test_staff_report_not_found_returns_404():
    class _U3NotFound:
        def get_summary(self, vid: str, day: str) -> dict[str, Any]:
            return {"outcome": "not_found"}
        def download_csv(self, vid: str, day: str) -> dict[str, Any]:
            return {"outcome": "not_found"}

    resp = staff_handler._dispatch(
        "GET /vehicles/{vehicleId}/reports/{utcDay}",
        {"vehicleId": "V-001", "utcDay": "2026-10-10"},
        {},
        _FakeU1(), _U3NotFound(),
    )
    assert resp["statusCode"] == 404


def test_staff_unknown_route_returns_404():
    resp = staff_handler._dispatch("GET /unknown", {}, {}, _FakeU1(), _FakeU3())
    assert resp["statusCode"] == 404


def test_staff_csv_download_returns_csv():
    resp = staff_handler._dispatch(
        "GET /vehicles/{vehicleId}/reports/{utcDay}/download",
        {"vehicleId": "V-001", "utcDay": "2026-10-10"},
        {},
        _FakeU1(), _FakeU3(),
    )
    assert resp["statusCode"] == 200
    assert "text/csv" in resp["headers"]["Content-Type"]
    assert "V-001" in resp["body"]


def test_staff_csv_download_not_found_returns_404():
    class _U3NotFound:
        def get_summary(self, vid: str, day: str) -> dict[str, Any]:
            return {"outcome": "not_found"}
        def download_csv(self, vid: str, day: str) -> dict[str, Any]:
            return {"outcome": "not_found"}

    resp = staff_handler._dispatch(
        "GET /vehicles/{vehicleId}/reports/{utcDay}/download",
        {"vehicleId": "V-001", "utcDay": "2026-10-10"},
        {},
        _FakeU1(), _U3NotFound(),
    )
    assert resp["statusCode"] == 404


def test_handle_fleet_with_vehicles():
    vehicles = staff_handler._handle_fleet(_FakeU1(), ["V-001", "V-002"])
    body = json.loads(vehicles["body"])
    assert len(body["vehicles"]) == 2
    assert body["vehicles"][0]["availability"] == "available"


def test_handle_fleet_partial_failure():
    class _FailingU1:
        def get_current_state(self, vid: str) -> dict[str, Any]:
            raise RuntimeError("U1 down")
        def list_effective_alerts(self, vid: str) -> dict[str, Any]:
            return {}
        def get_route_history(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {}

    result = staff_handler._handle_fleet(_FailingU1(), ["V-001"])
    body = json.loads(result["body"])
    assert body["partial"] is True
    assert "V-001" in body["failedVehicles"]


# ---------- thirdparty handler ----------

def test_thirdparty_authorized_returns_200():
    event = _thirdparty_event("V-001", "client-1")
    with (
        patch("fleet_telemetry.fleet_access.thirdparty_handler.LambdaU1QueryPort", return_value=_FakeU1()),
        patch("fleet_telemetry.fleet_access.thirdparty_handler.DynamoDBAuthorizationPort", return_value=_FakeAuth()),
    ):
        resp = thirdparty_handler.handle(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["availability"] == "available"


def test_thirdparty_unauthorized_returns_404():
    event = _thirdparty_event("V-001", "unknown-client")
    with (
        patch("fleet_telemetry.fleet_access.thirdparty_handler.LambdaU1QueryPort", return_value=_FakeU1()),
        patch("fleet_telemetry.fleet_access.thirdparty_handler.DynamoDBAuthorizationPort", return_value=_FakeAuth()),
    ):
        resp = thirdparty_handler.handle(event, None)
    assert resp["statusCode"] == 404


def test_thirdparty_missing_client_identity_returns_401():
    event = {
        "pathParameters": {"vehicleId": "V-001"},
        "requestContext": {"authorizer": {"jwt": {"claims": {}}}},
    }
    resp = thirdparty_handler.handle(event, None)
    assert resp["statusCode"] == 401


def test_thirdparty_u1_failure_returns_503():
    class _ErrorU1:
        def get_current_state(self, vid: str) -> dict[str, Any]:
            raise RuntimeError("Lambda invoke failed")

    event = _thirdparty_event("V-001", "client-1")
    with (
        patch("fleet_telemetry.fleet_access.thirdparty_handler.LambdaU1QueryPort", return_value=_ErrorU1()),
        patch("fleet_telemetry.fleet_access.thirdparty_handler.DynamoDBAuthorizationPort", return_value=_FakeAuth()),
    ):
        resp = thirdparty_handler.handle(event, None)
    assert resp["statusCode"] == 503
