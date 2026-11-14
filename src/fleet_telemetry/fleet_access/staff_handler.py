"""Lambda entry point for all staff query routes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .lambda_invoke import LambdaU1QueryPort, LambdaU3ReportPort
from .staff import (
    RouteValidationError,
    format_route_page,
    format_vehicle_state,
    validate_route_interval,
)


def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    path = event.get("pathParameters") or {}
    qs = event.get("queryStringParameters") or {}
    u1 = LambdaU1QueryPort()
    u3 = LambdaU3ReportPort()
    try:
        return _dispatch(route_key, path, qs, u1, u3)
    except RouteValidationError as exc:
        return _json(400, {"error": exc.code, "message": exc.message})
    except Exception:
        return _json(503, {"error": "service_unavailable"})


def _dispatch(
    route_key: str,
    path: dict[str, str],
    qs: dict[str, str],
    u1: LambdaU1QueryPort,
    u3: LambdaU3ReportPort,
) -> dict[str, Any]:
    vid = path.get("vehicleId", "")
    if route_key == "GET /vehicles/{vehicleId}":
        return _json(200, format_vehicle_state(u1.get_current_state(vid)))
    if route_key == "GET /vehicles/{vehicleId}/alerts":
        resp = u1.list_effective_alerts(vid)
        return _json(200, {"vehicleId": vid, "alerts": resp.get("alerts", [])})
    if route_key == "GET /vehicles/{vehicleId}/route":
        start = datetime.fromisoformat(qs["startInclusive"])
        end = datetime.fromisoformat(qs["endExclusive"])
        validate_route_interval(start, end, datetime.now(timezone.utc))
        resp = u1.get_route_history(vid, start, end, qs.get("continuationToken"))
        return _json(200, format_route_page(resp, vid))
    if route_key == "GET /vehicles/{vehicleId}/reports/{utcDay}":
        day = path.get("utcDay", "")
        resp = u3.get_summary(vid, day)
        if resp.get("outcome") == "not_found":
            return _json(404, {"error": "not_found"})
        if resp.get("outcome") == "failed":
            return _json(502, {"error": "upstream_failure"})
        return _json(200, resp.get("summary", resp))
    if route_key == "GET /vehicles/{vehicleId}/reports/{utcDay}/download":
        day = path.get("utcDay", "")
        resp = u3.download_csv(vid, day)
        if resp.get("outcome") == "not_found":
            return _json(404, {"error": "not_found"})
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/csv",
                "Content-Disposition": f'attachment; filename="report-{vid}-{day}.csv"',
            },
            "body": resp.get("csv", ""),
        }
    return _json(404, {"error": "route_not_found"})


def _handle_fleet(u1: LambdaU1QueryPort, vehicle_ids: list[str]) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    vehicles = []
    failed: list[str] = []
    for vehicle_id in vehicle_ids:
        try:
            vehicles.append(format_vehicle_state(u1.get_current_state(vehicle_id)))
        except Exception:
            failed.append(vehicle_id)
    result: dict[str, Any] = {"vehicles": vehicles, "fetchedAt": fetched_at}
    if failed:
        result["partial"] = True
        result["failedVehicles"] = failed
    return _json(200, result)


def _json(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
