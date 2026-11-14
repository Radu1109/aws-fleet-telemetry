"""Fail-closed device intake, canonical deduplication, and transactional outbox."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .models import payload_digest
from .ports import Clock, DeviceRegistry, IntakeRepository, SignatureVerifier
from .validation import validate_payload


@dataclass(frozen=True, slots=True)
class IntakeOutcome:
    status: str
    canonical_id: str | None = None
    reasons: tuple[str, ...] = ()
    accepted_new: bool = False
    conflict: bool = False


class RetryableIntakeError(RuntimeError):
    """Durable acceptance failed; the same device publication may be retried."""


class IntakeService:
    """Validates, authorizes, and durably accepts device publications."""

    def __init__(
        self,
        repository: IntakeRepository,
        registry: DeviceRegistry,
        verifier: SignatureVerifier,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._verifier = verifier
        self._clock = clock

    def handle(
        self,
        device_id: str,
        sequence: int,
        payload: dict[str, Any],
        signature: str | None,
    ) -> IntakeOutcome:
        """Process one delivery; durable commit failures surface as retryable errors."""
        receipt_time = self._clock.now()
        if not isinstance(device_id, str) or not device_id.strip():
            return self._reject(receipt_time, "device_identity_invalid", payload=payload)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            return self._reject(receipt_time, "sequence_invalid", payload=payload)
        if not isinstance(payload, dict):
            return self._reject(receipt_time, "payload_invalid")

        if not self._verifier.verify(device_id, payload, signature):
            return self._reject(receipt_time, "signature_invalid", payload=payload)

        digest = payload_digest(payload)
        prior = self._repository.sequence_record(device_id, sequence)
        if prior is not None:
            prior_digest, prior_result = prior
            if prior_digest == digest:
                return IntakeOutcome(
                    "retry",
                    canonical_id=prior_result["canonical_id"],
                    accepted_new=prior_result["accepted"],
                    conflict=prior_result["conflict"],
                )
            return self._reject(receipt_time, "sequence_content_conflict", payload=payload)
        if sequence <= self._repository.high_watermark(device_id):
            return self._reject(receipt_time, "sequence_stale", payload=payload)

        reading, validation_reasons = validate_payload(payload, receipt_time)
        if validation_reasons:
            return self._reject(receipt_time, *validation_reasons, payload=payload)
        if reading is None:
            raise RuntimeError("validation returned neither a reading nor rejection reasons")
        if not self._registry.is_authorized(device_id, reading.vehicle_id):
            return self._reject(
                receipt_time,
                "device_vehicle_unauthorized",
                payload=payload,
            )

        receipt = {
            "vehicleId": reading.vehicle_id,
            "receiptTime": receipt_time,
        }
        telemetry = {
            "position": {
                "latitude": reading.latitude,
                "longitude": reading.longitude,
            },
            "speedKmh": reading.speed_kmh,
            "fuelLevelPercent": reading.fuel_level_percent,
            "engineState": reading.engine_state.value,
            "diagnosticMeasurements": [
                {"name": item.name, "value": item.value, "unit": item.unit}
                for item in reading.diagnostic_measurements
            ],
        }
        if reading.engine_state_reason is not None:
            telemetry["engineStateReason"] = reading.engine_state_reason
        accepted = {
            "canonicalIdentity": reading.canonical_id,
            "vehicleId": reading.vehicle_id,
            "eventId": reading.event_id,
            "eventTime": reading.event_time,
            "telemetry": telemetry,
        }
        try:
            is_new, conflict = self._repository.accept(
                reading,
                device_id,
                sequence,
                digest,
                [("valid-receipt", receipt), ("accepted-reading", accepted)],
            )
        except Exception as exc:
            raise RetryableIntakeError("durable telemetry acceptance failed") from exc
        # Acceptance outcome from the repository is authoritative; the pre-check
        # above is not relied on for concurrent writers or duplicate identities.
        return IntakeOutcome(
            "accepted" if is_new else "duplicate",
            canonical_id=reading.canonical_id,
            accepted_new=is_new,
            conflict=conflict,
        )

    def _reject(
        self,
        at: datetime,
        *reasons: str,
        payload: dict[str, Any] | None = None,
    ) -> IntakeOutcome:
        unique_reasons = tuple(dict.fromkeys(reasons))
        record: dict[str, Any] = {
            "receiptTime": at,
            "rejectionReasons": unique_reasons,
        }
        if payload is not None:
            allowed = {
                "vehicleId",
                "eventId",
                "eventTime",
                "latitude",
                "longitude",
                "speedKmh",
                "fuelLevelPercent",
                "engineState",
                "diagnosticMeasurements",
            }
            record["receivedPayload"] = deepcopy(
                {key: value for key, value in payload.items() if key in allowed}
            )
        self._repository.record_quarantine(record)
        return IntakeOutcome("rejected", reasons=unique_reasons)


# ---------------------------------------------------------------------------
# Lambda handler — called by API Gateway POST /telemetry
# ---------------------------------------------------------------------------

def _decimal(v: Any) -> Decimal:
    return Decimal(str(v))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Minimal intake handler: validates payload and writes vehicle state to DynamoDB."""
    import boto3

    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return {"statusCode": 400, "body": json.dumps({"error": "invalid_json"})}

    now = datetime.now(timezone.utc)

    receipt_time = now
    reading, reasons = validate_payload(body, receipt_time)
    if reading is None:
        return {"statusCode": 422, "body": json.dumps({"error": "invalid_payload", "reasons": list(reasons)})}

    dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    outbox = dynamo.Table(os.environ["OUTBOX_PROJECTIONS_TABLE"])
    history = dynamo.Table(os.environ["CANONICAL_HISTORY_TABLE"])

    vehicle_id = reading.vehicle_id
    event_time_iso = reading.event_time.isoformat()

    # Write/update current vehicle state (latest position wins by event_time)
    existing = outbox.get_item(Key={"pk": f"VEHICLE#{vehicle_id}", "sk": "STATE"}).get("Item")
    if existing is None or existing.get("positionEventTime", "") <= event_time_iso:
        outbox.put_item(Item={
            "pk": f"VEHICLE#{vehicle_id}",
            "sk": "STATE",
            "vehicleId": vehicle_id,
            "latitude": reading.latitude,
            "longitude": reading.longitude,
            "speedKmh": reading.speed_kmh,
            "fuelLevelPercent": reading.fuel_level_percent,
            "positionEventTime": event_time_iso,
            "lastReceivedAt": receipt_time.isoformat(),
        })

    # Append to canonical history for route replay
    sort_key = f"{event_time_iso}#{reading.canonical_id}"
    history.put_item(Item={
        "vehicleId": vehicle_id,
        "eventSortKey": sort_key,
        "canonicalId": reading.canonical_id,
        "eventTime": event_time_iso,
        "latitude": reading.latitude,
        "longitude": reading.longitude,
        "speedKmh": reading.speed_kmh,
        "fuelLevelPercent": reading.fuel_level_percent,
        "engineState": reading.engine_state.value,
        "receiptTime": receipt_time.isoformat(),
    })

    return {"statusCode": 200, "body": json.dumps({"canonicalId": reading.canonical_id, "status": "accepted"})}
