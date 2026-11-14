"""Lambda entry point for the third-party current-status endpoint."""

from __future__ import annotations

import json
from typing import Any

from .lambda_invoke import DynamoDBAuthorizationPort, LambdaU1QueryPort
from .thirdparty import AuthorizationDenied, get_vehicle_status


def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    path = event.get("pathParameters") or {}
    vehicle_id = path.get("vehicleId", "")
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    client_id = claims.get("client_id") or claims.get("sub", "")
    if not client_id:
        return _json(401, {"error": "missing_client_identity"})
    u1 = LambdaU1QueryPort()
    auth = DynamoDBAuthorizationPort()
    try:
        status = get_vehicle_status(vehicle_id, client_id, u1, auth)
        return _json(200, status)
    except AuthorizationDenied:
        return _json(404, {"error": "not_found"})
    except Exception:
        return _json(503, {"error": "service_unavailable"})


def _json(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
