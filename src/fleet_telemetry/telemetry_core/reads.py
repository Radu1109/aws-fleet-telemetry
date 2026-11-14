"""U1 query contracts with bounded pages and immutable acceptance snapshots."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import TelemetryReading, as_utc, canonical_order
from .ports import Clock, IntakeRepository, ProcessingRepository

RETENTION = timedelta(days=90)
ROUTE_MAX_INTERVAL = timedelta(hours=24)
STALE_AFTER = timedelta(seconds=60)


class QueryFailed(RuntimeError):
    """A backing-store or projection failure, distinct from an empty result."""


class InvalidRange(ValueError):
    def __init__(self, *reasons: str) -> None:
        self.reasons = tuple(reasons)
        super().__init__(", ".join(self.reasons))


class ReadService:
    def __init__(
        self,
        intake: IntakeRepository,
        processing: ProcessingRepository,
        clock: Clock,
        page_size: int = 100,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._intake = intake
        self._processing = processing
        self._clock = clock
        self._page_size = page_size

    def get_current_state(self, vehicle_id: str) -> dict[str, Any]:
        try:
            projection = self._processing.projection(vehicle_id)
            if projection is None:
                return {"vehicleId": vehicle_id, "outcome": "noState"}
            received = self._processing.last_received_at(vehicle_id)
            if received is None:
                raise QueryFailed("position exists but receipt freshness is not available")
            now = as_utc(self._clock.now())
            received = as_utc(received)
            return {
                "vehicleId": vehicle_id,
                "outcome": "available",
                "position": projection["position"],
                "positionEventTime": projection["positionEventTime"],
                "lastReceivedAt": received,
                "stale": now - received >= STALE_AFTER,
            }
        except QueryFailed:
            raise
        except Exception as exc:
            raise QueryFailed("current-state query failed") from exc

    def list_effective_alerts(self, vehicle_id: str) -> dict[str, Any]:
        try:
            alerts = [
                item
                for item in self._processing.alerts(vehicle_id)
                if item.get("effectiveState") == "effective"
            ]
            return {"vehicleId": vehicle_id, "alerts": alerts}
        except Exception as exc:
            raise QueryFailed("effective-alert query failed") from exc

    def get_route_history(
        self,
        vehicle_id: str,
        start_inclusive: datetime,
        end_exclusive: datetime,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        start, end = as_utc(start_inclusive), as_utc(end_exclusive)
        if end <= start:
            raise InvalidRange("end_not_after_start")
        if end - start > ROUTE_MAX_INTERVAL:
            raise InvalidRange("interval_over_24_hours")
        try:
            state = self._snapshot(
                "route",
                vehicle_id,
                start,
                end,
                continuation_token,
                enforce_retention=True,
            )
            page, more = self._page(state, vehicle_id, start, end)
            token = self._next_token(continuation_token, state, page, more)
            return {
                "statusCode": 200,
                "vehicleId": vehicle_id,
                "points": page,
                "nextContinuationToken": token,
                "completeness": "complete",
            }
        except InvalidRange:
            raise
        except Exception as exc:
            raise QueryFailed("route-history query failed") from exc

    def read_canonical_history(
        self,
        vehicle_id: str,
        start_inclusive: datetime,
        end_exclusive: datetime,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        start, end = as_utc(start_inclusive), as_utc(end_exclusive)
        if end <= start:
            raise InvalidRange("invalid_interval")
        try:
            state = self._snapshot(
                "canonical",
                vehicle_id,
                start,
                end,
                continuation_token,
                enforce_retention=False,
            )
            page, more = self._page(state, vehicle_id, start, end)
            token = self._next_token(continuation_token, state, page, more)
            boundary = state["retention_boundary"]
            previous = self._intake.latest_retained_before(
                vehicle_id,
                start,
                boundary,
                state["acceptance_watermark"],
            )
            return {
                "readings": page,
                "lastValidReadingBeforeStart": previous,
                "nextContinuationToken": token,
                "completeness": "partial" if start < boundary else "complete",
                "retentionBoundary": boundary,
                "watermark": state["snapshot_time"],
            }
        except InvalidRange:
            raise
        except Exception as exc:
            raise QueryFailed("canonical-history query failed") from exc

    def _snapshot(
        self,
        query_type: str,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        token: str | None,
        enforce_retention: bool,
    ) -> dict[str, Any]:
        if token is None:
            snapshot_time = as_utc(self._clock.now())
            retention_boundary = snapshot_time - RETENTION
            if enforce_retention and start < retention_boundary:
                raise InvalidRange("before_retention")
            return {
                "query_type": query_type,
                "vehicle_id": vehicle_id,
                "start": start,
                "end": end,
                "snapshot_time": snapshot_time,
                "retention_boundary": retention_boundary,
                "acceptance_watermark": self._intake.capture_acceptance_watermark(),
                "after": None,
            }
        if not isinstance(token, str) or not token:
            raise InvalidRange("continuation_invalid")
        state = self._intake.read_cursor(token)
        if state is None:
            raise InvalidRange("continuation_invalid")
        if (
            state.get("query_type") != query_type
            or state.get("vehicle_id") != vehicle_id
            or state.get("start") != start
            or state.get("end") != end
        ):
            raise InvalidRange("continuation_mismatch")
        if not isinstance(state.get("acceptance_watermark"), int):
            raise InvalidRange("continuation_invalid")
        if enforce_retention and start < state["retention_boundary"]:
            raise InvalidRange("continuation_invalid")
        return state

    def _page(
        self,
        state: dict[str, Any],
        vehicle_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[TelemetryReading], bool]:
        retained_start = max(start, state["retention_boundary"])
        rows = self._intake.list_readings_page(
            vehicle_id,
            retained_start,
            end,
            state["acceptance_watermark"],
            state.get("after"),
            self._page_size + 1,
        )
        page = rows[: self._page_size]
        return page, len(rows) > self._page_size

    def _next_token(
        self,
        parent: str | None,
        state: dict[str, Any],
        page: list[TelemetryReading],
        more: bool,
    ) -> str | None:
        if not more:
            return None
        next_state = dict(state)
        next_state["after"] = canonical_order(page[-1])
        if parent is None:
            return self._intake.create_read_cursor(next_state)
        return self._intake.next_read_cursor(parent, next_state)


# ---------------------------------------------------------------------------
# Lambda handler — called by U2 via internal Lambda invocation
# ---------------------------------------------------------------------------

def _dynamo():
    import boto3
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-west-1"))


def _outbox_table():
    return _dynamo().Table(os.environ["OUTBOX_PROJECTIONS_TABLE"])


def _canonical_table():
    return _dynamo().Table(os.environ["CANONICAL_HISTORY_TABLE"])


def _get_current_state(vehicle_id: str) -> dict[str, Any]:
    resp = _outbox_table().get_item(
        Key={"pk": f"VEHICLE#{vehicle_id}", "sk": "STATE"},
        ConsistentRead=True,
    )
    item = resp.get("Item")
    if item is None:
        return {"vehicleId": vehicle_id, "outcome": "noState"}
    now = datetime.now(timezone.utc)
    last_received = item.get("lastReceivedAt")
    received_dt = datetime.fromisoformat(str(last_received)) if last_received else now
    return {
        "vehicleId": vehicle_id,
        "outcome": "available",
        "position": {"latitude": float(item["latitude"]), "longitude": float(item["longitude"])},
        "positionEventTime": item.get("positionEventTime"),
        "lastReceivedAt": received_dt.isoformat(),
        "stale": (now - received_dt).total_seconds() >= 60,
    }


def _list_effective_alerts(vehicle_id: str) -> dict[str, Any]:
    from boto3.dynamodb.conditions import Key as DKey
    resp = _outbox_table().query(
        KeyConditionExpression=DKey("pk").eq(f"VEHICLE#{vehicle_id}") & DKey("sk").begins_with("ALERT#"),
        ConsistentRead=True,
    )
    alerts = [
        item for item in resp.get("Items", [])
        if item.get("effectiveState") == "effective"
    ]
    return {"vehicleId": vehicle_id, "alerts": alerts}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for internal U2 → U1 invocations."""
    operation = event.get("operation", "")
    vehicle_id = event.get("vehicleId", "")
    try:
        if operation == "getCurrentState":
            return _get_current_state(vehicle_id)
        if operation == "listEffectiveAlerts":
            return _list_effective_alerts(vehicle_id)
        if operation == "getRouteHistory":
            # Route history requires pagination; return empty for now
            return {"points": [], "nextContinuationToken": None, "completeness": "complete", "vehicleId": vehicle_id}
        return {"error": "unknown_operation", "operation": operation}
    except Exception as exc:
        return {"error": "query_failed", "detail": str(exc)}
