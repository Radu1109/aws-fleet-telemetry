"""In-memory adapters for deterministic tests and local examples."""

from __future__ import annotations

import secrets
from bisect import bisect_left, bisect_right, insort
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any

from .models import TelemetryReading, as_utc, canonical_order
from .ports import (
    IntakeRepository,
    ProcessingSnapshot,
    ProcessingVersionConflict,
)


class MemoryTelemetryStore:
    """A test adapter; its lock only provides local atomic compare-and-commit."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._readings: dict[str, TelemetryReading] = {}
        self._readings_by_vehicle: dict[str, list[TelemetryReading]] = {}
        self._reading_orders: dict[str, list[tuple[datetime, int, bytes]]] = {}
        self._acceptance_positions: dict[str, int] = {}
        self._acceptance_watermark = 0
        self._sequences: dict[tuple[str, int], tuple[str, dict[str, Any]]] = {}
        self._high_watermarks: dict[str, int] = {}
        self._quarantined: list[dict[str, Any]] = []
        self._conflicts: list[dict[str, str]] = []
        self._last_received: dict[str, datetime] = {}
        self._projections: dict[str, dict[str, Any]] = {}
        self._alerts: dict[str, list[dict[str, Any]]] = {}
        self._alert_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        self._alert_entries: dict[tuple[str, str], tuple[str, tuple[datetime, int, bytes]]] = {}
        self._alert_orders: dict[
            tuple[str, str], list[tuple[tuple[datetime, int, bytes], str]]
        ] = {}
        self._effective_alert_orders: dict[
            tuple[str, str], list[tuple[tuple[datetime, int, bytes], str]]
        ] = {}
        self._condition_checkpoints: dict[
            str, dict[tuple[datetime, int, bytes], dict[str, Any]]
        ] = {}
        self._condition_checkpoint_orders: dict[
            str, list[tuple[datetime, int, bytes]]
        ] = {}
        self._processed: set[str] = set()
        self._processing_versions: dict[str, int] = {}
        self._replay_jobs: dict[str, dict[str, Any]] = {}
        self._replay_continuations_enqueued: set[tuple[str, int]] = set()
        self._read_cursors: dict[str, dict[str, Any]] = {}
        self._next_read_cursors: dict[str, str] = {}
        self.outbox: list[tuple[str, dict[str, Any]]] = []

    def sequence_record(self, device_id: str, sequence: int) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            return self._sequences.get((device_id, sequence))

    def high_watermark(self, device_id: str) -> int:
        with self._lock:
            return self._high_watermarks.get(device_id, -1)

    def accept(
        self,
        reading: TelemetryReading,
        device_id: str,
        sequence: int,
        digest: str,
        outbox_messages: list[tuple[str, dict[str, Any]]],
    ) -> tuple[bool, bool]:
        """Atomically model sequence, canonical record, and receipt persistence."""
        with self._lock:
            if sequence <= self._high_watermarks.get(device_id, -1):
                raise ValueError("sequence is not fresh")
            is_new = reading.canonical_id not in self._readings
            if is_new:
                self._record_reading(reading)
                self._acceptance_watermark += 1
                self._acceptance_positions[reading.canonical_id] = self._acceptance_watermark
            prior = self._readings[reading.canonical_id]
            conflict = _reading_content(prior) != _reading_content(reading)
            self._high_watermarks[device_id] = sequence
            self._sequences[(device_id, sequence)] = (
                digest,
                {"canonical_id": prior.canonical_id, "accepted": is_new, "conflict": conflict},
            )
            if conflict:
                self._conflicts.append(
                    {"canonical_id": reading.canonical_id, "device_id": device_id}
                )
            self.outbox.extend(
                (kind, dict(message))
                for kind, message in outbox_messages
                if kind != "accepted-reading" or is_new
            )
            return is_new, conflict

    def record_quarantine(self, record: dict[str, Any]) -> None:
        self._quarantined.append(dict(record))

    @property
    def quarantined(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._quarantined)

    def get_reading(self, canonical_id: str) -> TelemetryReading | None:
        return self._readings.get(canonical_id)

    def store_if_absent(self, reading: TelemetryReading) -> TelemetryReading:
        with self._lock:
            if reading.canonical_id not in self._readings:
                self._record_reading(reading)
                self._acceptance_watermark += 1
                self._acceptance_positions[reading.canonical_id] = self._acceptance_watermark
            return self._readings[reading.canonical_id]

    def _record_reading(self, reading: TelemetryReading) -> None:
        self._readings[reading.canonical_id] = reading
        ordered = self._readings_by_vehicle.setdefault(reading.vehicle_id, [])
        orders = self._reading_orders.setdefault(reading.vehicle_id, [])
        insort(orders, canonical_order(reading))
        ordered.insert(bisect_left(orders, canonical_order(reading)), reading)

    def capture_acceptance_watermark(self) -> int:
        with self._lock:
            return self._acceptance_watermark

    def list_readings_page(
        self,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        accepted_through: int,
        after: tuple[datetime, int, bytes] | None,
        limit: int,
    ) -> list[TelemetryReading]:
        if limit < 1:
            raise ValueError("limit must be positive")
        start_utc, end_utc = as_utc(start), as_utc(end)
        with self._lock:
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            lower = (start_utc, 0, b"")
            if after is not None and after > lower:
                lower = after
                start_index = bisect_right(keys, lower)
            else:
                start_index = bisect_left(keys, lower)
            end_index = bisect_left(keys, (end_utc, 0, b""))
            result = []
            for index in range(start_index, end_index):
                item = ordered[index]
                if self._acceptance_positions.get(item.canonical_id, 0) <= accepted_through:
                    result.append(item)
                    if len(result) == limit:
                        break
            return result

    def latest_retained_before(
        self,
        vehicle_id: str,
        before: datetime,
        retained_from: datetime,
        accepted_through: int,
    ) -> TelemetryReading | None:
        before_utc, retained_utc = as_utc(before), as_utc(retained_from)
        with self._lock:
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            index = bisect_left(keys, (before_utc, 0, b""))
            for item_index in range(index - 1, -1, -1):
                item = ordered[item_index]
                if as_utc(item.event_time) < retained_utc:
                    return None
                if self._acceptance_positions.get(item.canonical_id, 0) <= accepted_through:
                    return item
            return None

    def create_read_cursor(self, state: dict[str, Any]) -> str:
        with self._lock:
            token = secrets.token_urlsafe(32)
            self._read_cursors[token] = dict(state)
            return token

    def read_cursor(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._read_cursors.get(token)
            return None if state is None else dict(state)

    def next_read_cursor(self, parent: str, state: dict[str, Any]) -> str:
        with self._lock:
            existing = self._next_read_cursors.get(parent)
            if existing is not None:
                return existing
            token = secrets.token_urlsafe(32)
            self._read_cursors[token] = dict(state)
            self._next_read_cursors[parent] = token
            return token

    def record_receipt(self, vehicle_id: str, receipt_time: datetime) -> None:
        with self._lock:
            current = self._last_received.get(vehicle_id)
            if current is None or receipt_time > current:
                self._last_received[vehicle_id] = receipt_time

    def last_received_at(self, vehicle_id: str) -> datetime | None:
        with self._lock:
            return self._last_received.get(vehicle_id)

    def readings_in_range(
        self, vehicle_id: str, start_inclusive: datetime, end_inclusive: datetime
    ) -> list[TelemetryReading]:
        start, end = as_utc(start_inclusive), as_utc(end_inclusive)
        with self._lock:
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            lower = bisect_left(keys, (start, 0, b""))
            upper = bisect_right(keys, (end, 2, b""))
            return list(ordered[lower:upper])

    def latest_event_before(
        self, vehicle_id: str, before: tuple[datetime, int, bytes]
    ) -> TelemetryReading | None:
        with self._lock:
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            index = bisect_left(keys, before)
            return ordered[index - 1] if index else None

    def store_projection(self, vehicle_id: str, projection: dict[str, Any]) -> None:
        with self._lock:
            self._projections[vehicle_id] = deepcopy(projection)

    def projection(self, vehicle_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._projections.get(vehicle_id)
            return None if value is None else deepcopy(value)

    def processing_snapshot(self, vehicle_id: str) -> ProcessingSnapshot:
        """Read a consistent, copy-isolated optimistic processing frontier."""
        with self._lock:
            projection = self._projections.get(vehicle_id)
            return ProcessingSnapshot(
                version=self._processing_versions.get(vehicle_id, 0),
                projection=None if projection is None else deepcopy(projection),
            )

    def commit_processing(
        self,
        vehicle_id: str,
        expected_version: int,
        canonical_id: str,
        *,
        projection: dict[str, Any],
        alerts: list[dict[str, Any]],
        condition_checkpoints: list[
            tuple[TelemetryReading, dict[str, Any]]
        ],
        supersede_range: tuple[datetime, datetime] | None = None,
        replay_job_id: str | None = None,
    ) -> bool:
        """Atomically publish all effects if this worker still owns the read version."""
        with self._lock:
            current_version = self._processing_versions.get(vehicle_id, 0)
            if current_version != expected_version:
                raise ProcessingVersionConflict(
                    f"vehicle {vehicle_id!r} changed from version "
                    f"{expected_version} to {current_version}"
                )
            if canonical_id in self._processed:
                return False

            if replay_job_id is not None:
                job = self._replay_jobs.get(replay_job_id)
                if (
                    job is None
                    or job["vehicle_id"] != vehicle_id
                    or job["canonical_id"] != canonical_id
                    or job["expected_version"] != expected_version
                    or not job["complete"]
                ):
                    raise ValueError("replay generation is missing or incomplete")
                alerts = list(job["alerts"].values())
                condition_checkpoints = list(job["checkpoints"].values())
                supersede_range = job["supersede_range"]

            rollback_names = (
                "_projections",
                "_alerts",
                "_alert_lookup",
                "_alert_entries",
                "_alert_orders",
                "_effective_alert_orders",
                "_condition_checkpoints",
                "_condition_checkpoint_orders",
                "_processed",
                "_processing_versions",
                "_replay_jobs",
            )
            rollback_values = deepcopy(
                tuple(getattr(self, name) for name in rollback_names)
            )
            try:
                self.store_alerts(vehicle_id, alerts, supersede_range)
                for reading, conditions in condition_checkpoints:
                    self.store_condition_checkpoint(vehicle_id, reading, conditions)
                self.store_projection(vehicle_id, projection)
                self._processed.add(canonical_id)
                self._processing_versions[vehicle_id] = current_version + 1
                if replay_job_id is not None:
                    self._replay_jobs.pop(replay_job_id, None)
            except Exception:
                for name, value in zip(rollback_names, rollback_values):
                    setattr(self, name, value)
                raise
            return True

    def store_alerts(
        self,
        vehicle_id: str,
        alerts: list[dict[str, Any]],
        supersede_range: tuple[datetime, datetime] | None = None,
    ) -> None:
        with self._lock:
            if supersede_range is not None:
                start, end = map(as_utc, supersede_range)
                for alert_type in ("excessive-speed", "sudden-fuel-drop"):
                    index_key = (vehicle_id, alert_type)
                    entries = self._alert_orders.get(index_key, [])
                    lower = bisect_left(entries, ((start, 0, b""), ""))
                    upper = bisect_left(entries, ((end, 2, b""), ""))
                    replayed_ids = {item["alertId"] for item in alerts}
                    for _, alert_id in entries[lower:upper]:
                        replay_prior = self._alert_lookup[(vehicle_id, alert_id)]
                        if alert_id not in replayed_ids:
                            self._set_alert_state(vehicle_id, alert_id, replay_prior, "superseded")
            for item in alerts:
                alert_id = item["alertId"]
                lookup_key = (vehicle_id, alert_id)
                prior: dict[str, Any] | None = self._alert_lookup.get(lookup_key)
                if prior is None:
                    prior = dict(item)
                    self._alert_lookup[lookup_key] = prior
                    self._alerts.setdefault(vehicle_id, []).append(prior)
                    reading = self._readings.get(item.get("triggeringReadingIdentity") or "")
                    if reading is not None:
                        order = canonical_order(reading)
                        alert_type = item["alertType"]
                        index_key = (vehicle_id, alert_type)
                        insort(self._alert_orders.setdefault(index_key, []), (order, alert_id))
                        self._alert_entries[lookup_key] = (alert_type, order)
                else:
                    prior_state = prior.get("effectiveState")
                    prior.update(item)
                    prior["effectiveState"] = prior_state
                self._set_alert_state(
                    vehicle_id, alert_id, prior, item["effectiveState"]
                )

    def _set_alert_state(
        self,
        vehicle_id: str,
        alert_id: str,
        alert: dict[str, Any],
        state: str,
    ) -> None:
        entry = self._alert_entries.get((vehicle_id, alert_id))
        if entry is None:
            alert["effectiveState"] = state
            return
        alert_type, order = entry
        index_key = (vehicle_id, alert_type)
        effective = self._effective_alert_orders.setdefault(index_key, [])
        index_entry = (order, alert_id)
        if alert.get("effectiveState") == "effective":
            position = bisect_left(effective, index_entry)
            if position < len(effective) and effective[position] == index_entry:
                effective.pop(position)
        alert["effectiveState"] = state
        if state == "effective":
            insort(effective, index_entry)

    def alerts(self, vehicle_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._alerts.get(vehicle_id, []))

    def last_effective_alert_before(
        self, vehicle_id: str, alert_type: str, before: tuple[datetime, int, bytes]
    ) -> dict[str, Any] | None:
        with self._lock:
            entries = self._effective_alert_orders.get((vehicle_id, alert_type), [])
            position = bisect_left(entries, (before, "")) - 1
            if position < 0:
                return None
            alert_id = entries[position][1]
            return deepcopy(self._alert_lookup[(vehicle_id, alert_id)])

    def condition_checkpoint_before(
        self, vehicle_id: str, before: tuple[datetime, int, bytes]
    ) -> dict[str, dict[str, Any]] | None:
        with self._lock:
            checkpoints = self._condition_checkpoints.get(vehicle_id, {})
            keys = self._condition_checkpoint_orders.get(vehicle_id, [])
            position = bisect_left(keys, before) - 1
            if position < 0:
                return None
            state = checkpoints[keys[position]]
            return deepcopy(state.get("activeConditions", state))

    def processing_checkpoint_before(
        self, vehicle_id: str, before: tuple[datetime, int, bytes]
    ) -> dict[str, Any] | None:
        with self._lock:
            checkpoints = self._condition_checkpoints.get(vehicle_id, {})
            keys = self._condition_checkpoint_orders.get(vehicle_id, [])
            position = bisect_left(keys, before) - 1
            return None if position < 0 else deepcopy(checkpoints[keys[position]])

    def processing_checkpoint_at(
        self, vehicle_id: str, at: tuple[datetime, int, bytes]
    ) -> dict[str, Any] | None:
        with self._lock:
            checkpoint = self._condition_checkpoints.get(vehicle_id, {}).get(at)
            return None if checkpoint is None else deepcopy(checkpoint)

    def begin_replay(
        self,
        vehicle_id: str,
        canonical_id: str,
        expected_version: int,
        affected_order: tuple[datetime, int, bytes],
        initial_state: dict[str, Any],
    ) -> str:
        """Pin an acceptance cut and create or resume a staged replay generation."""
        from hashlib import sha256

        job_id = sha256(
            f"{vehicle_id}\0{canonical_id}\0{expected_version}".encode("utf-8")
        ).hexdigest()
        with self._lock:
            if self._processing_versions.get(vehicle_id, 0) != expected_version:
                raise ProcessingVersionConflict("vehicle changed before replay began")
            for stale_id, stale_job in tuple(self._replay_jobs.items()):
                if (
                    stale_job["vehicle_id"] == vehicle_id
                    and stale_job["canonical_id"] == canonical_id
                    and stale_job["expected_version"] != expected_version
                ):
                    self._replay_jobs.pop(stale_id, None)
            existing = self._replay_jobs.get(job_id)
            if existing is not None:
                return job_id

            checkpoint = self.processing_checkpoint_before(vehicle_id, affected_order)
            checkpoint_orders = self._condition_checkpoint_orders.get(vehicle_id, [])
            if checkpoint is None:
                next_after = None
                rule_state = deepcopy(initial_state)
            else:
                checkpoint_position = bisect_left(checkpoint_orders, affected_order) - 1
                next_after = checkpoint_orders[checkpoint_position]
                rule_state = checkpoint
            projection = self._projections.get(vehicle_id)
            retained_times = [
                reading.event_time
                for reading in self._readings_by_vehicle.get(vehicle_id, [])
                if self._acceptance_positions.get(reading.canonical_id, 0)
                <= self._acceptance_watermark
            ]
            event_id = None if projection is None else projection.get("positionEventId")
            self._replay_jobs[job_id] = {
                "vehicle_id": vehicle_id,
                "canonical_id": canonical_id,
                "expected_version": expected_version,
                "accepted_through": self._acceptance_watermark,
                "retention_boundary": min(retained_times) if retained_times else None,
                "original_frontier": (
                    (
                        as_utc(projection["positionEventTime"]),
                        0 if event_id is not None else 1,
                        event_id.encode("utf-8") if event_id is not None else b"",
                    )
                    if projection is not None
                    else None
                ),
                "next_after": next_after,
                "rule_state": deepcopy(rule_state),
                "projection": None if projection is None else deepcopy(projection),
                "alerts": {},
                "checkpoints": {},
                "complete": False,
                "converged": False,
                "continuation_version": 0,
                "trigger_seen": False,
                "affected_time": affected_order[0],
                "supersede_range": None,
            }
            return job_id

    def replay_session(self, replay_job_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                return deepcopy(self._replay_jobs[replay_job_id])
            except KeyError as exc:
                raise ValueError("replay generation does not exist") from exc

    def replay_readings_page(self, replay_job_id: str, limit: int) -> list[TelemetryReading]:
        if limit < 1:
            raise ValueError("replay page limit must be positive")
        with self._lock:
            job = self._replay_jobs[replay_job_id]
            vehicle_id = job["vehicle_id"]
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            after = job["next_after"]
            start = 0 if after is None else bisect_right(keys, after)
            rows = []
            for item in ordered[start:]:
                if self._acceptance_positions.get(item.canonical_id, 0) > job["accepted_through"]:
                    continue
                rows.append(item)
                if len(rows) == limit:
                    break
            return rows

    def replay_has_more(
        self, replay_job_id: str, after: tuple[datetime, int, bytes]
    ) -> bool:
        with self._lock:
            job = self._replay_jobs[replay_job_id]
            vehicle_id = job["vehicle_id"]
            ordered = self._readings_by_vehicle.get(vehicle_id, [])
            keys = self._reading_orders.get(vehicle_id, [])
            start = bisect_right(keys, after)
            return any(
                self._acceptance_positions.get(item.canonical_id, 0)
                <= job["accepted_through"]
                for item in ordered[start:]
            )

    def enqueue_replay_continuation(
        self, replay_job_id: str, continuation_version: int
    ) -> None:
        with self._lock:
            job = self._replay_jobs[replay_job_id]
            if job["complete"]:
                return
            current_version = job["continuation_version"]
            if continuation_version != current_version:
                raise ProcessingVersionConflict(
                    "replay continuation changed before handoff"
                )
            key = (replay_job_id, continuation_version)
            if key in self._replay_continuations_enqueued:
                return
            self._replay_continuations_enqueued.add(key)
            self.outbox.append(
                (
                    "replay-continuation",
                    {
                        "replayJobId": replay_job_id,
                        "continuationVersion": continuation_version,
                        "canonicalId": job["canonical_id"],
                    },
                )
            )

    def replay_suffix_unchanged(
        self, replay_job_id: str, after: tuple[datetime, int, bytes]
    ) -> bool:
        """Require the pinned suffix to have a previously published checkpoint."""
        with self._lock:
            job = self._replay_jobs[replay_job_id]
            vehicle_id = job["vehicle_id"]
            checkpoints = self._condition_checkpoints.get(vehicle_id, {})
            for reading in self._readings_by_vehicle.get(vehicle_id, []):
                if canonical_order(reading) <= after:
                    continue
                if self._acceptance_positions.get(reading.canonical_id, 0) > job["accepted_through"]:
                    continue
                if canonical_order(reading) not in checkpoints:
                    return False
            return True

    def stage_replay_chunk(
        self,
        replay_job_id: str,
        *,
        expected_after: tuple[datetime, int, bytes] | None,
        next_after: tuple[datetime, int, bytes] | None,
        alerts: list[dict[str, Any]],
        checkpoints: list[tuple[TelemetryReading, dict[str, Any]]],
        rule_state: dict[str, Any],
        complete: bool,
        converged: bool,
        trigger_seen: bool,
        supersede_end: datetime | None,
    ) -> None:
        """Atomically persist one bounded chunk and its resumable continuation."""
        with self._lock:
            job = self._replay_jobs[replay_job_id]
            if job["next_after"] != expected_after:
                current_after = job["next_after"]
                if current_after is None or (
                    expected_after is not None and current_after < expected_after
                ):
                    raise ProcessingVersionConflict(
                        "replay continuation moved behind this worker's chunk"
                    )
                for reading, state in checkpoints:
                    staged = job["checkpoints"].get(canonical_order(reading))
                    if staged is None or staged[1] != state:
                        raise ProcessingVersionConflict(
                            "concurrent replay staged a different checkpoint"
                        )
                for alert in alerts:
                    if job["alerts"].get(alert["alertId"]) != alert:
                        raise ProcessingVersionConflict(
                            "concurrent replay staged different alert evidence"
                        )
                return
            for item in alerts:
                job["alerts"][item["alertId"]] = deepcopy(item)
            for reading, state in checkpoints:
                job["checkpoints"][canonical_order(reading)] = (
                    reading,
                    deepcopy(state),
                )
            job["next_after"] = next_after
            job["rule_state"] = deepcopy(rule_state)
            job["complete"] = complete
            job["converged"] = converged
            job["trigger_seen"] = trigger_seen
            job["continuation_version"] += 1
            if supersede_end is not None:
                job["supersede_range"] = (
                    job["affected_time"],
                    as_utc(supersede_end),
                )

    def discard_replay(self, replay_job_id: str) -> None:
        with self._lock:
            self._replay_jobs.pop(replay_job_id, None)

    def store_condition_checkpoint(
        self,
        vehicle_id: str,
        reading: TelemetryReading,
        conditions: dict[str, Any],
    ) -> None:
        with self._lock:
            order = canonical_order(reading)
            checkpoints = self._condition_checkpoints.setdefault(vehicle_id, {})
            if order not in checkpoints:
                insort(self._condition_checkpoint_orders.setdefault(vehicle_id, []), order)
            checkpoints[order] = deepcopy(conditions)

    def mark_processed(self, canonical_id: str) -> bool:
        with self._lock:
            if canonical_id in self._processed:
                return False
            self._processed.add(canonical_id)
            return True

    def is_processed(self, canonical_id: str) -> bool:
        with self._lock:
            return canonical_id in self._processed


class MemoryDeviceRegistry:
    def __init__(self, assignments: dict[str, set[str]] | None = None) -> None:
        self.assignments = assignments or {}

    def is_authorized(self, device_id: str, vehicle_id: str) -> bool:
        return vehicle_id in self.assignments.get(device_id, set())


class AcceptAndReadPath:
    """Small injected accept-store-read slice used by the walking skeleton."""

    def __init__(self, store: IntakeRepository) -> None:
        self._store = store

    def accept(self, reading: TelemetryReading) -> TelemetryReading:
        if not reading.canonical_id:
            raise ValueError("canonical identity is required")
        self._store.store_if_absent(reading)
        persisted = self._store.get_reading(reading.canonical_id)
        if persisted is None:
            raise RuntimeError("accepted reading was not available for read-back")
        return persisted


def _reading_content(reading: TelemetryReading) -> tuple[Any, ...]:
    return (
        reading.vehicle_id,
        reading.event_time,
        reading.latitude,
        reading.longitude,
        reading.speed_kmh,
        reading.fuel_level_percent,
        reading.diagnostic_measurements,
        reading.event_id,
        reading.engine_state,
        reading.engine_state_reason,
    )
