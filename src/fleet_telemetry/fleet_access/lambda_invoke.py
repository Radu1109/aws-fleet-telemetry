"""Concrete Lambda and DynamoDB adapters for production use."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


def _invoke_lambda(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    import boto3  # imported lazily so tests can load the module without AWS SDK
    client = boto3.client("lambda")
    result = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload, default=str),
    )
    return json.loads(result["Payload"].read())


class LambdaU1QueryPort:
    def __init__(self) -> None:
        self._fn = os.environ["U1_QUERY_FUNCTION_NAME"]

    def get_current_state(self, vehicle_id: str) -> dict[str, Any]:
        return _invoke_lambda(self._fn, {"operation": "getCurrentState", "vehicleId": vehicle_id})

    def list_effective_alerts(self, vehicle_id: str) -> dict[str, Any]:
        return _invoke_lambda(self._fn, {"operation": "listEffectiveAlerts", "vehicleId": vehicle_id})

    def get_route_history(
        self,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        return _invoke_lambda(self._fn, {
            "operation": "getRouteHistory",
            "vehicleId": vehicle_id,
            "startInclusive": start.isoformat(),
            "endExclusive": end.isoformat(),
            "continuationToken": continuation_token,
        })


class LambdaU3ReportPort:
    def __init__(self) -> None:
        self._fn = os.environ["U3_REPORT_WORKER_FUNCTION_NAME"]

    def get_summary(self, vehicle_id: str, utc_day: str) -> dict[str, Any]:
        return _invoke_lambda(self._fn, {
            "operation": "getSummary",
            "vehicleId": vehicle_id,
            "utcDay": utc_day,
        })

    def download_csv(self, vehicle_id: str, utc_day: str) -> dict[str, Any]:
        return _invoke_lambda(self._fn, {
            "operation": "downloadCsv",
            "vehicleId": vehicle_id,
            "utcDay": utc_day,
        })


class DynamoDBAuthorizationPort:
    def __init__(self) -> None:
        import boto3
        table_name = os.environ["CLIENT_AUTHORIZATION_TABLE"]
        self._table = boto3.resource("dynamodb").Table(table_name)

    def get_client_item(self, client_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(
            Key={"clientId": client_id},
            ConsistentRead=True,
        )
        return response.get("Item")
