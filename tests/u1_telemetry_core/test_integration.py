from datetime import datetime, timedelta, timezone

import pytest

from fleet_telemetry.telemetry_core.intake import IntakeService
from fleet_telemetry.telemetry_core.memory import MemoryDeviceRegistry, MemoryTelemetryStore
from fleet_telemetry.telemetry_core.models import EngineState
from fleet_telemetry.telemetry_core.processing import (
    AcceptedReadingProcessor,
    ReceiptProcessor,
    ReplayContinuationPending,
)
from fleet_telemetry.telemetry_core.reads import ReadService

BASE = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


class TrustedVerifier:
    def verify(self, device_id, payload, signature):
        return signature == "ok"


def setup():
    store = MemoryTelemetryStore()
    clock = MutableClock(BASE)
    intake = IntakeService(
        store,
        MemoryDeviceRegistry({"d-1": {"v-1"}}),
        TrustedVerifier(),
        clock,
    )
    receipt_processor = ReceiptProcessor(store)
    reading_processor = AcceptedReadingProcessor(store)
    reads = ReadService(store, store, clock, page_size=1)
    return store, clock, intake, receipt_processor, reading_processor, reads


def payload(event_id, event_time, *, speed=30, fuel=70):
    return {
        "vehicleId": "v-1",
        "eventId": event_id,
        "eventTime": event_time.isoformat(),
        "latitude": 48.1,
        "longitude": 11.5,
        "speedKmh": speed,
        "fuelLevelPercent": fuel,
        "diagnosticMeasurements": [],
    }


def process_outbox(store, receipt_processor, reading_processor):
    for kind, body in store.outbox:
        if kind == "valid-receipt":
            receipt_processor.process(body["vehicleId"], body["receiptTime"])
        elif kind == "accepted-reading":
            try:
                reading_processor.process(store.get_reading(body["canonicalIdentity"]))
            except ReplayContinuationPending:
                pass
        elif kind == "replay-continuation":
            reading_processor.process_replay_continuation(
                body["replayJobId"],
                body["continuationVersion"],
                body["canonicalId"],
            )


def test_integration_intake_outbox_processing_and_current_state_read():
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    outcome = intake.handle("d-1", 1, payload("e-1", BASE), "ok")
    process_outbox(store, receipt_processor, reading_processor)

    state = reads.get_current_state("v-1")

    assert outcome.status == "accepted"
    assert state["outcome"] == "available"
    assert state["positionEventTime"] == BASE
    assert state["lastReceivedAt"] == BASE


@pytest.mark.parametrize("engine_state", [EngineState.ON, EngineState.OFF])
def test_integration_engine_state_survives_intake_message_and_processing(engine_state):
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    body = payload("engine-state", BASE)
    body["engineState"] = engine_state.value

    outcome = intake.handle("d-1", 1, body, "ok")
    accepted_message = next(
        message
        for kind, message in store.outbox
        if kind == "accepted-reading"
    )
    process_outbox(store, receipt_processor, reading_processor)

    canonical_reading = store.get_reading(outcome.canonical_id)
    canonical_history = reads.read_canonical_history(
        "v-1", BASE, BASE + timedelta(minutes=1)
    )
    history_reading = canonical_history["readings"][0]
    assert outcome.status == "accepted"
    assert accepted_message["canonicalIdentity"] == outcome.canonical_id
    assert canonical_reading.engine_state is engine_state
    assert history_reading.engine_state is engine_state
    assert accepted_message["telemetry"]["engineState"] == engine_state.value
    assert history_reading.engine_state_reason is None
    assert "engineStateReason" not in accepted_message["telemetry"]
    assert store.is_processed(outcome.canonical_id)
    assert reads.get_current_state("v-1")["outcome"] == "available"
    assert store.quarantined == ()


@pytest.mark.parametrize(
    ("include_engine_state", "engine_state", "expected_reason"),
    [
        pytest.param(False, None, None, id="missing"),
        pytest.param(True, "not-a-state", "engine_state_invalid", id="malformed"),
    ],
)
def test_integration_missing_or_malformed_engine_state_is_non_quarantining(
    include_engine_state, engine_state, expected_reason
):
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    body = payload("engine-state-fallback", BASE)
    if include_engine_state:
        body["engineState"] = engine_state

    outcome = intake.handle("d-1", 1, body, "ok")
    accepted_message = next(
        message
        for kind, message in store.outbox
        if kind == "accepted-reading"
    )
    process_outbox(store, receipt_processor, reading_processor)

    canonical_reading = store.get_reading(outcome.canonical_id)
    canonical_history = reads.read_canonical_history(
        "v-1", BASE, BASE + timedelta(minutes=1)
    )
    history_reading = canonical_history["readings"][0]
    assert outcome.status == "accepted"
    assert accepted_message["canonicalIdentity"] == outcome.canonical_id
    assert canonical_reading.engine_state is EngineState.NOT_REPORTED
    assert canonical_reading.engine_state_reason == expected_reason
    assert history_reading.engine_state is EngineState.NOT_REPORTED
    assert history_reading.engine_state_reason == expected_reason
    assert accepted_message["telemetry"]["engineState"] == "NOT_REPORTED"
    assert accepted_message["telemetry"].get("engineStateReason") == expected_reason
    assert store.is_processed(outcome.canonical_id)
    assert reads.get_current_state("v-1")["outcome"] == "available"
    assert store.quarantined == ()


def test_integration_exact_retry_does_not_advance_freshness():
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    body = payload("e-1", BASE)
    intake.handle("d-1", 1, body, "ok")
    process_outbox(store, receipt_processor, reading_processor)
    clock.value = BASE + timedelta(seconds=30)

    retry = intake.handle("d-1", 1, body, "ok")
    process_outbox(store, receipt_processor, reading_processor)

    assert retry.status == "retry"
    assert reads.get_current_state("v-1")["lastReceivedAt"] == BASE
    assert len([entry for entry in store.outbox if entry[0] == "valid-receipt"]) == 1


def test_integration_fresh_sequence_duplicate_advances_freshness_once_only():
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    body = payload("e-1", BASE)
    intake.handle("d-1", 1, body, "ok")
    process_outbox(store, receipt_processor, reading_processor)
    clock.value = BASE + timedelta(seconds=20)

    outcome = intake.handle("d-1", 2, body, "ok")
    process_outbox(store, receipt_processor, reading_processor)

    assert outcome.status == "duplicate"
    assert reads.get_current_state("v-1")["lastReceivedAt"] == clock.value
    assert store.capture_acceptance_watermark() == 1
    assert len([entry for entry in store.outbox if entry[0] == "accepted-reading"]) == 1


def test_integration_late_event_replay_updates_effective_alert_state():
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    first = payload("a", BASE, speed=111)
    later = payload("c", BASE + timedelta(minutes=2), speed=112)
    intake.handle("d-1", 1, first, "ok")
    intake.handle("d-1", 2, later, "ok")
    process_outbox(store, receipt_processor, reading_processor)
    assert reads.list_effective_alerts("v-1")["alerts"]
    clock.value += timedelta(minutes=3)

    late = payload("b", BASE + timedelta(minutes=1), speed=50)
    intake.handle("d-1", 3, late, "ok")
    process_outbox(store, receipt_processor, reading_processor)

    evidence = store.alerts("v-1")
    assert len(evidence) == 1
    assert evidence[0]["effectiveState"] == "superseded"
    assert reads.list_effective_alerts("v-1")["alerts"] == []


def test_integration_canonical_history_pagination_has_stable_watermark():
    store, clock, intake, receipt_processor, reading_processor, reads = setup()
    for sequence in range(3):
        event_time = BASE + timedelta(minutes=sequence)
        intake.handle(
            "d-1",
            sequence + 1,
            payload(f"e-{sequence}", event_time),
            "ok",
        )
    page_one = reads.read_canonical_history(
        "v-1", BASE, BASE + timedelta(hours=1)
    )
    token = page_one["nextContinuationToken"]

    assert token is not None
    page_two = reads.read_canonical_history(
        "v-1", BASE, BASE + timedelta(hours=1), token
    )
    assert page_two["watermark"] == page_one["watermark"]
    assert page_one["readings"][0].event_time < page_two["readings"][0].event_time
