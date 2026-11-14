from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Thread
from typing import Any

import pytest

from fleet_telemetry.telemetry_core.memory import MemoryTelemetryStore
from fleet_telemetry.telemetry_core.models import TelemetryReading, canonical_order
from fleet_telemetry.telemetry_core.ports import ProcessingVersionConflict
from fleet_telemetry.telemetry_core.processing import (
    AcceptedReadingProcessor,
    ProcessingRetryExhausted,
    ReceiptProcessor,
    ReplayContinuationPending,
)

BASE = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def reading(minute, *, identity, speed=30, fuel=70):
    return TelemetryReading(
        vehicle_id="v-1",
        event_time=BASE + timedelta(minutes=minute),
        latitude=Decimal("1"),
        longitude=Decimal("2"),
        speed_kmh=Decimal(str(speed)),
        fuel_level_percent=Decimal(str(fuel)),
        diagnostic_measurements=(),
        event_id=identity,
        receipt_time=BASE + timedelta(minutes=minute),
        canonical_id=identity,
    )


def assert_complete_replay_checkpoint(checkpoint, last_reading=None):
    assert set(checkpoint) == {
        "lastEffectiveTriggerTimes",
        "activeConditions",
        "lastProcessedReading",
        "speedPredecessor",
        "trailingFuelReadings",
    }
    assert set(checkpoint["lastEffectiveTriggerTimes"]) == {
        "excessive-speed",
        "sudden-fuel-drop",
    }
    assert set(checkpoint["activeConditions"]) == {
        "excessive-speed",
        "sudden-fuel-drop",
    }
    assert isinstance(checkpoint["trailingFuelReadings"], tuple)
    if last_reading is not None:
        assert checkpoint["lastProcessedReading"] == last_reading
        assert checkpoint["speedPredecessor"] == last_reading


def complete_replay(processor, store, late_reading):
    try:
        result = processor.process(late_reading)
    except ReplayContinuationPending:
        result = True
    continuation_index = 0
    while not store.is_processed(late_reading.canonical_id):
        messages = [
            message
            for kind, message in store.outbox
            if kind == "replay-continuation"
        ]
        assert continuation_index < len(messages)
        message = messages[continuation_index]
        processor.process_replay_continuation(
            message["replayJobId"],
            message["continuationVersion"],
            message["canonicalId"],
        )
        continuation_index += 1
    return result


def test_receipt_freshness_is_independent_and_never_regresses():
    store = MemoryTelemetryStore()
    processor = ReceiptProcessor(store)
    processor.process("v-1", BASE + timedelta(minutes=5))
    processor.process("v-1", BASE + timedelta(minutes=2))

    assert store.last_received_at("v-1") == BASE + timedelta(minutes=5)
    accepted = reading(1, identity="older-event")
    store.store_if_absent(accepted)
    AcceptedReadingProcessor(store).process(accepted)
    assert store.last_received_at("v-1") == BASE + timedelta(minutes=5)


def test_processing_is_idempotent_and_position_never_regresses_on_late_event():
    store = MemoryTelemetryStore()
    earlier, later = reading(1, identity="earlier"), reading(2, identity="later")
    store.store_if_absent(earlier)
    store.store_if_absent(later)
    processor = AcceptedReadingProcessor(store)

    assert processor.process(later)
    assert not processor.process(later)
    assert processor.process(earlier)
    assert store.projection("v-1")["positionReadingIdentity"] == "later"


def test_late_event_replay_recomputes_speed_evidence_in_canonical_order():
    store = MemoryTelemetryStore()
    first, second = reading(0, identity="a", speed=111), reading(2, identity="c", speed=112)
    store.store_if_absent(first)
    store.store_if_absent(second)
    processor = AcceptedReadingProcessor(store)
    processor.process(second)

    late = reading(1, identity="b", speed=50)
    store.store_if_absent(late)
    assert processor.process(late)
    assert store.is_processed(late.canonical_id)
    assert not [
        message
        for kind, message in store.outbox
        if kind == "replay-continuation"
    ]

    speed_alerts = [
        item for item in store.alerts("v-1")
        if item["alertType"] == "excessive-speed"
    ]
    assert len(speed_alerts) == 1
    assert speed_alerts[0]["effectiveState"] == "superseded"


def test_alert_evidence_and_cooldown_are_stored_with_idempotent_replay():
    store = MemoryTelemetryStore()
    points = [
        reading(0, identity="a", speed=111),
        reading(1, identity="b", speed=112),
        reading(10, identity="c", speed=113),
        reading(11, identity="d", speed=114),
    ]
    for item in points:
        store.store_if_absent(item)
    processor = AcceptedReadingProcessor(store)
    for item in points:
        processor.process(item)

    alerts = store.alerts("v-1")
    assert [item["effectiveState"] for item in alerts] == [
        "effective",
        "superseded",
        "effective",
    ]
    assert processor.process(points[-1]) is False
    assert len(store.alerts("v-1")) == 3


def test_speed_condition_clears_at_or_below_threshold():
    store = MemoryTelemetryStore()
    points = [
        reading(0, identity="a", speed=111),
        reading(1, identity="b", speed=112),
        reading(2, identity="c", speed=110),
    ]
    for item in points:
        store.store_if_absent(item)
    processor = AcceptedReadingProcessor(store)
    for item in points:
        processor.process(item)

    condition = store.projection("v-1")["activeConditions"]["excessive-speed"]
    assert condition == {"state": "inactive", "clearedAt": BASE + timedelta(minutes=2)}


def test_effective_speed_evidence_sets_active_condition():
    store = MemoryTelemetryStore()
    points = [
        reading(0, identity="a", speed=111),
        reading(1, identity="b", speed=112),
    ]
    for item in points:
        store.store_if_absent(item)
    AcceptedReadingProcessor(store).process(points[-1])

    condition = store.projection("v-1")["activeConditions"]["excessive-speed"]
    assert condition["state"] == "active"
    assert condition["raisedAt"] == BASE + timedelta(minutes=1)
    checkpoint = store.processing_checkpoint_before(
        "v-1", (BASE + timedelta(days=1), 0, b"")
    )
    assert checkpoint["lastEffectiveTriggerTimes"]["excessive-speed"] == (
        BASE + timedelta(minutes=1)
    )
    assert checkpoint["lastProcessedReading"].canonical_id == "b"
    assert checkpoint["speedPredecessor"].canonical_id == "b"
    assert checkpoint["trailingFuelReadings"][-1].canonical_id == "b"


def test_speed_condition_without_prior_activation_has_no_clear_timestamp():
    store = MemoryTelemetryStore()
    low = reading(0, identity="a", speed=110)
    store.store_if_absent(low)

    AcceptedReadingProcessor(store).process(low)

    condition = store.projection("v-1")["activeConditions"]["excessive-speed"]
    assert condition == {"state": "inactive", "clearedAt": None}


def test_in_order_processing_uses_bounded_context_instead_of_full_history():
    class BoundedStore(MemoryTelemetryStore):
        def readings(self, vehicle_id):
            raise AssertionError("in-order processing must not load full history")

    store = BoundedStore()
    item = reading(1, identity="current")
    store.store_if_absent(item)

    assert AcceptedReadingProcessor(store).process(item)


def test_late_replay_uses_bounded_chunks_without_a_total_time_cap():
    class ChunkedStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.replay_page_sizes = []
            self.replay_page_times = []
            self.replay_chunks = []
            self.terminal_chunks = []

        def readings(self, vehicle_id):
            raise AssertionError("replay must not load full history")

        def replay_readings_page(self, replay_job_id, limit):
            page = super().replay_readings_page(replay_job_id, limit)
            self.replay_page_sizes.append(len(page))
            if page:
                self.replay_page_times.append(
                    (page[0].event_time, page[-1].event_time)
                )
            return page

        def stage_replay_chunk(self, replay_job_id, **kwargs):
            checkpoint = kwargs["rule_state"]
            assert_complete_replay_checkpoint(checkpoint)
            staged_checkpoints = kwargs["checkpoints"]
            assert len(staged_checkpoints) <= 4
            if staged_checkpoints:
                assert checkpoint["lastProcessedReading"] == staged_checkpoints[-1][0]
                assert kwargs["next_after"] == canonical_order(staged_checkpoints[-1][0])
            chunk = {
                "checkpoint_count": len(staged_checkpoints),
                "checkpoint": checkpoint,
                "expected_after": kwargs["expected_after"],
                "next_after": kwargs["next_after"],
                "complete": kwargs["complete"],
                "converged": kwargs["converged"],
            }
            self.replay_chunks.append(chunk)
            if kwargs["complete"]:
                self.terminal_chunks.append(chunk)
            super().stage_replay_chunk(replay_job_id, **kwargs)

    store = ChunkedStore()
    points = [
        reading(hour * 60, identity=f"e-{hour}", speed=111 if hour < 2 else 30)
        for hour in range(46)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=4)
    processor.process(points[-1])

    assert complete_replay(processor, store, points[2])
    assert len(store.replay_page_sizes) > 1
    assert max(store.replay_page_sizes) <= 4
    assert len(store.replay_chunks) > 1
    assert all(chunk["checkpoint_count"] <= 4 for chunk in store.replay_chunks)
    assert store.replay_chunks[0]["expected_after"] is None
    assert all(
        current["expected_after"] == previous["next_after"]
        for previous, current in zip(
            store.replay_chunks, store.replay_chunks[1:]
        )
    )
    assert store.replay_page_times[-1][1] - store.replay_page_times[0][0] > timedelta(
        hours=24
    )
    assert len(store.terminal_chunks) == 1
    terminal = store.terminal_chunks[0]
    assert terminal["complete"]
    assert not terminal["converged"]
    assert terminal["next_after"] == canonical_order(points[-1])
    assert terminal["checkpoint"]["lastProcessedReading"] == points[-1]
    final_conditions = store.condition_checkpoint_before(
        "v-1", (BASE + timedelta(days=3), 0, b"")
    )
    assert final_conditions["excessive-speed"] == {
        "state": "inactive",
        "clearedAt": BASE + timedelta(hours=2),
    }


def test_replay_redelivery_recovers_after_timeout_after_chunk_commit():
    class InterruptOnceStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.interrupted = False
            self.begin_sessions = []

        def begin_replay(self, *args, **kwargs):
            replay_job_id = super().begin_replay(*args, **kwargs)
            self.begin_sessions.append(self.replay_session(replay_job_id))
            return replay_job_id

        def stage_replay_chunk(self, replay_job_id, **kwargs):
            super().stage_replay_chunk(replay_job_id, **kwargs)
            if not self.interrupted and kwargs["checkpoints"]:
                self.interrupted = True
                raise TimeoutError("simulated timeout after durable chunk")

    store = InterruptOnceStore()
    points = [
        reading(minute, identity=f"r-{minute}", speed=111 if minute < 2 else 30)
        for minute in range(12)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=3)
    processor.process(points[-1])

    with pytest.raises(TimeoutError, match="simulated timeout"):
        processor.process(points[2])
    assert not store.is_processed(points[2].canonical_id)
    assert store._replay_jobs

    replay_job_id = next(iter(store._replay_jobs))
    continuation = store.replay_session(replay_job_id)
    assert continuation["next_after"] == canonical_order(points[2])
    assert len(continuation["checkpoints"]) == 3
    assert_complete_replay_checkpoint(
        continuation["rule_state"], last_reading=points[2]
    )
    assert continuation["rule_state"]["activeConditions"]["excessive-speed"] == {
        "state": "inactive",
        "clearedAt": points[2].event_time,
    }
    assert continuation["rule_state"]["lastEffectiveTriggerTimes"][
        "excessive-speed"
    ] == points[1].event_time

    with pytest.raises(ReplayContinuationPending):
        processor.process(points[2])
    assert len(store.begin_sessions) == 2
    resumed_session = store.begin_sessions[1]
    assert resumed_session["next_after"] == continuation["next_after"]
    assert resumed_session["rule_state"] == continuation["rule_state"]
    assert resumed_session["checkpoints"] == continuation["checkpoints"]
    complete_replay(processor, store, points[2])
    assert store.is_processed(points[2].canonical_id)
    assert store._replay_jobs == {}


def test_replay_continuation_redelivery_resumes_from_persisted_position():
    store = MemoryTelemetryStore()
    points = [
        reading(minute, identity=f"q-{minute}")
        for minute in range(8)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=2)
    for point in points:
        processor.process(point)
    late = reading(1, identity="q-late")
    store.store_if_absent(late)

    with pytest.raises(ReplayContinuationPending):
        processor.process(late)

    first_message = next(
        message for kind, message in store.outbox if kind == "replay-continuation"
    )
    first_session = store.replay_session(first_message["replayJobId"])
    assert first_session["next_after"] == canonical_order(points[2])
    assert first_session["continuation_version"] == first_message["continuationVersion"]

    assert processor.process_replay_continuation(
        first_message["replayJobId"],
        first_message["continuationVersion"],
        first_message["canonicalId"],
    )
    resumed = store.replay_session(first_message["replayJobId"])
    assert resumed["next_after"] == canonical_order(points[4])
    assert resumed["continuation_version"] == first_message["continuationVersion"] + 1
    assert len(
        [kind for kind, _ in store.outbox if kind == "replay-continuation"]
    ) == 2


def test_duplicate_replay_continuation_message_is_idempotent():
    store = MemoryTelemetryStore()
    points = [
        reading(minute, identity=f"d-{minute}")
        for minute in range(8)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=2)
    for point in points:
        processor.process(point)
    late = reading(1, identity="d-late")
    store.store_if_absent(late)

    with pytest.raises(ReplayContinuationPending):
        processor.process(late)
    message = next(
        message for kind, message in store.outbox if kind == "replay-continuation"
    )
    arguments = (
        message["replayJobId"],
        message["continuationVersion"],
        message["canonicalId"],
    )

    assert processor.process_replay_continuation(*arguments)
    session_after_first = store.replay_session(message["replayJobId"])
    queue_count_after_first = len(
        [kind for kind, _ in store.outbox if kind == "replay-continuation"]
    )
    assert not processor.process_replay_continuation(*arguments)

    session_after_duplicate = store.replay_session(message["replayJobId"])
    queue_count_after_duplicate = len(
        [kind for kind, _ in store.outbox if kind == "replay-continuation"]
    )
    assert session_after_duplicate["continuation_version"] == session_after_first[
        "continuation_version"
    ]
    assert session_after_duplicate["next_after"] == session_after_first["next_after"]
    assert queue_count_after_duplicate == queue_count_after_first


def test_replay_does_not_converge_before_an_unprocessed_pinned_suffix():
    store = MemoryTelemetryStore()
    points = [
        reading(minute, identity=f"p-{minute}", speed=30, fuel=70)
        for minute in range(11)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=2)
    processor.process(points[0])
    for point in points[1:10]:
        processor.process(point)

    unprocessed_suffix = reading(10, identity="accepted-after-frontier")
    store.store_if_absent(unprocessed_suffix)
    late = reading(2, identity="late-no-rule-change", speed=30)
    store.store_if_absent(late)

    assert complete_replay(processor, store, late)
    final_state = store.processing_checkpoint_before(
        "v-1", (BASE + timedelta(minutes=11), 0, b"")
    )
    assert final_state["activeConditions"] == {
        "excessive-speed": {"state": "inactive", "clearedAt": None},
        "sudden-fuel-drop": {"state": "inactive", "clearedAt": None},
    }
    assert final_state["lastProcessedReading"].canonical_id == points[10].canonical_id


def test_replay_stops_after_full_state_converges_on_an_unchanged_suffix():
    class RecordingStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.convergence_records = []

        def stage_replay_chunk(self, replay_job_id, **kwargs):
            if kwargs["converged"]:
                boundary, recomputed_state = kwargs["checkpoints"][-1]
                session = self.replay_session(replay_job_id)
                boundary_order = canonical_order(boundary)
                suffix_unchanged = self.replay_suffix_unchanged(
                    replay_job_id, boundary_order
                )
                pinned_suffix = [
                    item
                    for item in self._readings_by_vehicle[session["vehicle_id"]]
                    if canonical_order(item) > boundary_order
                    and self._acceptance_positions[item.canonical_id]
                    <= session["accepted_through"]
                ]
                stored_state = self.processing_checkpoint_at(
                    session["vehicle_id"], boundary_order
                )
                suffix_checkpoints = [
                    self.processing_checkpoint_at(
                        session["vehicle_id"], canonical_order(item)
                    )
                    for item in pinned_suffix
                ]
                self.convergence_records.append(
                    {
                        "boundary": boundary,
                        "recomputed_state": recomputed_state,
                        "stored_state": stored_state,
                        "suffix_unchanged": suffix_unchanged,
                        "pinned_suffix": pinned_suffix,
                        "suffix_checkpoints": suffix_checkpoints,
                    }
                )
            super().stage_replay_chunk(replay_job_id, **kwargs)

    store = RecordingStore()
    points = [
        reading(minute, identity=f"p-{minute}", speed=30, fuel=70)
        for minute in range(21)
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=3)
    for point in points:
        processor.process(point)
    late = reading(2, identity="a-late-reading", speed=30, fuel=70)
    store.store_if_absent(late)

    assert complete_replay(processor, store, late)
    assert len(store.convergence_records) == 1
    convergence = store.convergence_records[0]
    assert convergence["recomputed_state"] == convergence["stored_state"]
    assert_complete_replay_checkpoint(
        convergence["recomputed_state"], last_reading=convergence["boundary"]
    )
    assert convergence["suffix_unchanged"]
    assert convergence["pinned_suffix"]
    assert all(convergence["suffix_checkpoints"])
    assert convergence["boundary"].event_time < points[-1].event_time
    final_state = store.processing_checkpoint_before(
        "v-1", (BASE + timedelta(days=1), 0, b"")
    )
    assert final_state["lastProcessedReading"].canonical_id == points[-1].canonical_id


def test_replay_does_not_converge_when_state_differs_even_if_suffix_is_unchanged():
    class ObservedStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.mismatches_with_unchanged_suffix = []
            self.terminal_chunk = None

        def stage_replay_chunk(self, replay_job_id, **kwargs):
            for boundary, recomputed_state in kwargs["checkpoints"]:
                order = canonical_order(boundary)
                stored_state = self.processing_checkpoint_at(
                    boundary.vehicle_id, order
                )
                if (
                    stored_state is not None
                    and recomputed_state != stored_state
                    and self.replay_suffix_unchanged(replay_job_id, order)
                ):
                    self.mismatches_with_unchanged_suffix.append(
                        (boundary, recomputed_state, stored_state)
                    )
            if kwargs["complete"]:
                self.terminal_chunk = {
                    "next_after": kwargs["next_after"],
                    "converged": kwargs["converged"],
                    "rule_state": kwargs["rule_state"],
                }
            super().stage_replay_chunk(replay_job_id, **kwargs)

    store = ObservedStore()
    points = [
        reading(0, identity="m-0", speed=30),
        reading(1, identity="m-1", speed=111),
        reading(2, identity="m-2", speed=112),
        reading(3, identity="m-3", speed=30),
        reading(4, identity="m-4", speed=30),
        reading(5, identity="m-5", speed=30),
    ]
    for point in points:
        store.store_if_absent(point)
    processor = AcceptedReadingProcessor(store, replay_page_size=2)
    for point in points:
        processor.process(point)
    original_final_state = store.processing_checkpoint_at(
        "v-1", canonical_order(points[-1])
    )

    late = reading(0.5, identity="m-late", speed=111)
    store.store_if_absent(late)

    assert complete_replay(processor, store, late)
    assert store.mismatches_with_unchanged_suffix
    assert store.terminal_chunk is not None
    assert store.terminal_chunk["next_after"] == canonical_order(points[-1])
    assert not store.terminal_chunk["converged"]
    assert store.terminal_chunk["rule_state"] != original_final_state
    final_state = store.processing_checkpoint_at("v-1", canonical_order(points[-1]))
    assert final_state == store.terminal_chunk["rule_state"]


def test_derived_commit_rejects_stale_version_without_partial_effects():
    store = MemoryTelemetryStore()
    first = reading(0, identity="first")
    newer = reading(2, identity="newer")
    stale = reading(1, identity="stale")
    for item in (first, newer, stale):
        store.store_if_absent(item)
    processor = AcceptedReadingProcessor(store)
    processor.process(first)
    stale_snapshot = store.processing_snapshot("v-1")

    processor.process(newer)
    committed_projection = store.projection("v-1")
    committed_alerts = store.alerts("v-1")
    committed_checkpoint = store.condition_checkpoint_before(
        "v-1", (BASE + timedelta(days=1), 0, b"")
    )

    with pytest.raises(ProcessingVersionConflict):
        store.commit_processing(
            "v-1",
            stale_snapshot.version,
            stale.canonical_id,
            projection={
                "vehicleId": "v-1",
                "positionReadingIdentity": stale.canonical_id,
                "positionEventTime": stale.event_time,
            },
            alerts=[
                {
                    "alertId": "stale-alert",
                    "alertType": "excessive-speed",
                    "triggeringReadingIdentity": stale.canonical_id,
                    "triggeringReadingIdentities": [stale.canonical_id],
                    "triggeringEventTimes": [stale.event_time],
                    "measuredValue": Decimal("111"),
                    "thresholdApplied": ">110 km/h",
                    "raisedAt": stale.event_time,
                    "effectiveState": "effective",
                }
            ],
            condition_checkpoints=[(stale, _inactive_conditions())],
            supersede_range=(stale.event_time, stale.event_time),
        )

    assert store.projection("v-1") == committed_projection
    assert store.alerts("v-1") == committed_alerts
    assert store.condition_checkpoint_before(
        "v-1", (BASE + timedelta(days=1), 0, b"")
    ) == committed_checkpoint
    assert not store.is_processed(stale.canonical_id)


def test_same_expected_version_workers_commit_duplicate_delivery_once():
    class SynchronizedStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.snapshot_barrier = Barrier(2)
            self.snapshot_count = 0

        def processing_snapshot(self, vehicle_id):
            snapshot = super().processing_snapshot(vehicle_id)
            self.snapshot_count += 1
            if self.snapshot_count <= 2:
                self.snapshot_barrier.wait(timeout=2)
            return snapshot

    store = SynchronizedStore()
    duplicate = reading(1, identity="same-reading", speed=111)
    prior = reading(0, identity="prior", speed=112)
    store.store_if_absent(prior)
    store.store_if_absent(duplicate)
    processor = AcceptedReadingProcessor(store, sleeper=lambda _: None)
    results: list[bool] = []
    failures: list[BaseException] = []

    def deliver():
        try:
            results.append(processor.process(duplicate))
        except Exception as error:  # surfaced in the joining test thread
            failures.append(error)

    workers = [Thread(target=deliver), Thread(target=deliver)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    assert sorted(results) == [False, True]
    assert store.is_processed(duplicate.canonical_id)
    assert store.processing_snapshot("v-1").version == 1
    assert len(store.alerts("v-1")) == 1


def test_conflicting_workers_recompute_to_serial_equivalent_state():
    class SynchronizedStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.snapshot_barrier = Barrier(2)
            self.synchronize = False
            self.synchronized_snapshots = 0

        def processing_snapshot(self, vehicle_id):
            snapshot = super().processing_snapshot(vehicle_id)
            if self.synchronize and self.synchronized_snapshots < 2:
                self.synchronized_snapshots += 1
                self.snapshot_barrier.wait(timeout=2)
            return snapshot

    points = [
        reading(0, identity="a", speed=111),
        reading(1, identity="b", speed=50),
        reading(2, identity="c", speed=112),
        reading(3, identity="d", speed=111),
    ]
    concurrent = SynchronizedStore()
    for item in points:
        concurrent.store_if_absent(item)
    processor = AcceptedReadingProcessor(
        concurrent, sleeper=lambda _: None, jitter=lambda _low, high: high
    )
    processor.process(points[0])
    processor.process(points[2])
    concurrent.synchronize = True

    results: list[bool] = []
    failures: list[BaseException] = []

    def deliver(item):
        try:
            results.append(complete_replay(processor, concurrent, item))
        except Exception as error:  # surfaced in the joining test thread
            failures.append(error)

    workers = [Thread(target=deliver, args=(points[1],)), Thread(target=deliver, args=(points[3],))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)

    serial = MemoryTelemetryStore()
    for item in points:
        serial.store_if_absent(item)
    serial_processor = AcceptedReadingProcessor(serial)
    serial_processor.process(points[0])
    serial_processor.process(points[2])
    complete_replay(serial_processor, serial, points[1])
    serial_processor.process(points[3])

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    assert sorted(results) == [True, True]
    assert concurrent.projection("v-1") == serial.projection("v-1")
    assert (
        concurrent.projection("v-1")["positionReadingIdentity"]
        == points[3].canonical_id
    )
    assert concurrent.alerts("v-1") == serial.alerts("v-1")
    assert concurrent.is_processed(points[1].canonical_id)
    assert concurrent.is_processed(points[3].canonical_id)
    assert concurrent.processing_snapshot("v-1").version == 4


def test_conflict_retries_are_bounded_and_use_full_jitter_backoff():
    class AlwaysConflictingStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.commit_attempts = 0

        def commit_processing(self, *args, **kwargs):
            self.commit_attempts += 1
            raise ProcessingVersionConflict("injected stale version")

    store = AlwaysConflictingStore()
    item = reading(1, identity="retry-me")
    store.store_if_absent(item)
    sampled_ranges: list[tuple[float, float]] = []
    sleeps: list[float] = []

    def sample_jitter(low, high):
        sampled_ranges.append((low, high))
        return high

    processor = AcceptedReadingProcessor(
        store, sleeper=sleeps.append, jitter=sample_jitter
    )

    with pytest.raises(ProcessingRetryExhausted):
        processor.process(item)

    assert store.commit_attempts == 4
    assert sampled_ranges == [(0.0, 0.1), (0.0, 0.2), (0.0, 0.4)]
    assert sleeps == [0.1, 0.2, 0.4]
    assert not store.is_processed(item.canonical_id)
    assert store.projection("v-1") is None


def test_memory_atomic_commit_rolls_back_an_adapter_failure():
    class FailingCheckpointStore(MemoryTelemetryStore):
        fail_checkpoint_write = True

        def store_condition_checkpoint(self, vehicle_id, item, conditions):
            if self.fail_checkpoint_write:
                raise OSError("injected checkpoint write failure")
            super().store_condition_checkpoint(vehicle_id, item, conditions)

    store = FailingCheckpointStore()
    item = reading(1, identity="atomic-failure", speed=111)
    store.store_if_absent(item)
    processor = AcceptedReadingProcessor(store, sleeper=lambda _: None)

    with pytest.raises(OSError, match="checkpoint write failure"):
        processor.process(item)

    assert store.projection("v-1") is None
    assert store.alerts("v-1") == []
    assert store.condition_checkpoint_before(
        "v-1", (BASE + timedelta(days=1), 0, b"")
    ) is None
    assert not store.is_processed(item.canonical_id)

    store.fail_checkpoint_write = False
    assert processor.process(item)
    assert store.is_processed(item.canonical_id)


def _inactive_conditions() -> dict[str, dict[str, Any]]:
    return {
        "excessive-speed": {"state": "inactive", "clearedAt": None},
        "sudden-fuel-drop": {"state": "inactive", "clearedAt": None},
    }
