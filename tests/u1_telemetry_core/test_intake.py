from datetime import datetime, timezone

import pytest

from fleet_telemetry.telemetry_core.intake import IntakeService, RetryableIntakeError
from fleet_telemetry.telemetry_core.memory import MemoryDeviceRegistry, MemoryTelemetryStore


class FixedClock:
    def __init__(self, now):
        self.value = now

    def now(self):
        return self.value


class FakeVerifier:
    def verify(self, device_id, payload, signature):
        return signature == "verified"


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def message(**changes):
    result = {
        "vehicleId": "v-1",
        "eventId": "evt-1",
        "eventTime": NOW.isoformat(),
        "latitude": 10,
        "longitude": 20,
        "speedKmh": 30,
        "fuelLevelPercent": 80,
        "diagnosticMeasurements": [],
    }
    result.update(changes)
    return result


def setup_intake():
    store = MemoryTelemetryStore()
    service = IntakeService(
        store,
        MemoryDeviceRegistry({"device-1": {"v-1"}}),
        FakeVerifier(),
        FixedClock(NOW),
    )
    return service, store


def test_valid_delivery_commits_distinct_receipt_and_new_reading_messages():
    service, store = setup_intake()

    outcome = service.handle("device-1", 1, message(), "verified")

    assert outcome.status == "accepted"
    assert [kind for kind, _ in store.outbox] == ["valid-receipt", "accepted-reading"]
    assert store.outbox[0][1]["receiptTime"] == NOW
    assert store.get_reading(outcome.canonical_id) is not None
    accepted = store.outbox[1][1]
    assert accepted["telemetry"]["engineState"] == "NOT_REPORTED"
    assert "engineStateReason" not in accepted["telemetry"]


def test_accepted_message_preserves_normalized_engine_state_and_reason():
    service, store = setup_intake()

    outcome = service.handle(
        "device-1", 1, message(engineState="bad-value"), "verified"
    )

    accepted = next(body for kind, body in store.outbox if kind == "accepted-reading")
    assert outcome.status == "accepted"
    assert accepted["telemetry"]["engineState"] == "NOT_REPORTED"
    assert accepted["telemetry"]["engineStateReason"] == "engine_state_invalid"


def test_invalid_message_is_quarantined_without_receipt_or_canonical_write():
    service, store = setup_intake()

    outcome = service.handle("device-1", 1, message(speedKmh=-1), "verified")

    assert outcome.status == "rejected"
    assert "speed_negative" in outcome.reasons
    assert store.outbox == []
    assert store.capture_acceptance_watermark() == 0
    assert len(store.quarantined) == 1


def test_signature_and_device_assignment_fail_closed():
    service, store = setup_intake()
    bad_signature = service.handle("device-1", 1, message(), "wrong")
    unknown_assignment = service.handle(
        "device-2", 1, message(), "verified"
    )

    assert bad_signature.reasons == ("signature_invalid",)
    assert unknown_assignment.reasons == ("device_vehicle_unauthorized",)
    assert store.outbox == []


def test_exact_transport_retry_reuses_outcome_without_new_receipt():
    service, store = setup_intake()
    first = service.handle("device-1", 4, message(), "verified")

    retry = service.handle("device-1", 4, message(), "verified")

    assert first.status == "accepted"
    assert retry.status == "retry"
    assert len([item for item in store.outbox if item[0] == "valid-receipt"]) == 1


def test_new_sequence_duplicate_emits_receipt_but_not_second_reading_event():
    service, store = setup_intake()
    service.handle("device-1", 4, message(), "verified")

    duplicate = service.handle("device-1", 5, message(), "verified")

    assert duplicate.status == "duplicate"
    assert len([item for item in store.outbox if item[0] == "valid-receipt"]) == 2
    assert len([item for item in store.outbox if item[0] == "accepted-reading"]) == 1


def test_sequence_reuse_and_stale_sequence_are_rejected():
    service, store = setup_intake()
    service.handle("device-1", 4, message(), "verified")

    reused = service.handle("device-1", 4, message(speedKmh=31), "verified")
    stale = service.handle("device-1", 3, message(), "verified")

    assert reused.reasons == ("sequence_content_conflict",)
    assert stale.reasons == ("sequence_stale",)
    assert len(store.outbox) == 2


def test_conflicting_canonical_duplicate_is_first_wins_and_reported():
    service, store = setup_intake()
    service.handle("device-1", 1, message(), "verified")

    conflict = service.handle("device-1", 2, message(speedKmh=99), "verified")

    assert conflict.status == "duplicate"
    assert conflict.conflict
    assert store.get_reading(conflict.canonical_id).speed_kmh == 30
    assert len([item for item in store.outbox if item[0] == "accepted-reading"]) == 1


def test_durable_commit_failure_is_explicitly_retryable():
    service, store = setup_intake()

    def fail_commit(*args, **kwargs):
        raise OSError("storage unavailable")

    store.accept = fail_commit

    with pytest.raises(RetryableIntakeError):
        service.handle("device-1", 1, message(), "verified")
