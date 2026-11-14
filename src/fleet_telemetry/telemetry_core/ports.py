"""Small injected boundaries for persistence, messaging, and device trust."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .models import TelemetryReading

OrderKey = tuple[datetime, int, bytes]


@dataclass(frozen=True, slots=True)
class ProcessingSnapshot:
    """Versioned view used as the optimistic boundary for one vehicle."""

    version: int
    projection: dict[str, Any] | None


class ProcessingVersionConflict(RuntimeError):
    """The vehicle changed after a processor read its versioned snapshot."""


class ReplayContextMissing(RuntimeError):
    """Required retained-boundary state is unavailable; replay cannot assume empty state."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class DeviceRegistry(Protocol):
    def is_authorized(self, device_id: str, vehicle_id: str) -> bool: ...


class SignatureVerifier(Protocol):
    def verify(self, device_id: str, payload: dict[str, Any], signature: str | None) -> bool: ...


class IntakeRepository(Protocol):
    """Acceptance transaction including canonical/sequence state and outbox rows."""

    def sequence_record(self, device_id: str, sequence: int) -> tuple[str, dict[str, Any]] | None: ...
    def high_watermark(self, device_id: str) -> int: ...
    def accept(
        self,
        reading: TelemetryReading,
        device_id: str,
        sequence: int,
        digest: str,
        outbox_messages: list[tuple[str, dict[str, Any]]],
    ) -> tuple[bool, bool]: ...
    def record_quarantine(self, record: dict[str, Any]) -> None: ...
    def store_if_absent(self, reading: TelemetryReading) -> TelemetryReading: ...
    def get_reading(self, canonical_id: str) -> TelemetryReading | None: ...
    def capture_acceptance_watermark(self) -> int:
        """Return a durable commit-generation cut, not an Intake receipt-time cutoff."""
        ...

    def list_readings_page(
        self,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        accepted_through: int,
        after: OrderKey | None,
        limit: int,
    ) -> list[TelemetryReading]:
        """Return one canonical-order page accepted at or before the snapshot cut."""
        ...

    def latest_retained_before(
        self,
        vehicle_id: str,
        before: datetime,
        retained_from: datetime,
        accepted_through: int,
    ) -> TelemetryReading | None:
        """Return the latest in-retention event before the interval at the same cut."""
        ...

    def create_read_cursor(self, state: dict[str, Any]) -> str:
        """Persist query state behind an opaque, unguessable continuation handle."""
        ...

    def read_cursor(self, token: str) -> dict[str, Any] | None:
        """Resolve a continuation handle without trusting client-supplied state."""
        ...

    def next_read_cursor(self, parent: str, state: dict[str, Any]) -> str:
        """Idempotently return the same next handle for retries of one page."""
        ...


class ProcessingRepository(Protocol):
    """Versioned derived-state boundary shared by all processing workers.

    Implementations must make ``commit_processing`` an atomic conditional
    commit across workers; a process-local mutex is not a production substitute.
    Receipt freshness remains a separate atomic-maximum operation.
    """

    def processing_snapshot(self, vehicle_id: str) -> ProcessingSnapshot: ...
    def get_reading(self, canonical_id: str) -> TelemetryReading | None: ...

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
    ) -> bool: ...

    def record_receipt(self, vehicle_id: str, receipt_time: datetime) -> None: ...
    def last_received_at(self, vehicle_id: str) -> datetime | None: ...
    def readings_in_range(
        self, vehicle_id: str, start_inclusive: datetime, end_inclusive: datetime
    ) -> list[TelemetryReading]:
        """Read only the indexed event-time window, in canonical order."""
        ...
    def latest_event_before(
        self, vehicle_id: str, before: OrderKey
    ) -> TelemetryReading | None: ...
    def projection(self, vehicle_id: str) -> dict[str, Any] | None: ...
    def alerts(self, vehicle_id: str) -> list[dict[str, Any]]: ...
    def last_effective_alert_before(
        self, vehicle_id: str, alert_type: str, before: OrderKey
    ) -> dict[str, Any] | None: ...
    def condition_checkpoint_before(
        self, vehicle_id: str, before: OrderKey
    ) -> dict[str, dict[str, Any]] | None: ...
    def processing_checkpoint_before(
        self, vehicle_id: str, before: OrderKey
    ) -> dict[str, Any] | None: ...
    def processing_checkpoint_at(
        self, vehicle_id: str, at: OrderKey
    ) -> dict[str, Any] | None: ...
    def begin_replay(
        self,
        vehicle_id: str,
        canonical_id: str,
        expected_version: int,
        affected_order: OrderKey,
        initial_state: dict[str, Any],
    ) -> str:
        """Pin input/retention state and restore a valid prior checkpoint.

        Implementations may use ``initial_state`` only at the established
        retained-history start with all required boundary context. Otherwise
        they raise ``ReplayContextMissing`` rather than inventing empty state.
        """
        ...
    def replay_session(self, replay_job_id: str) -> dict[str, Any]: ...
    def replay_readings_page(self, replay_job_id: str, limit: int) -> list[TelemetryReading]: ...
    def replay_has_more(
        self, replay_job_id: str, after: OrderKey
    ) -> bool: ...
    def enqueue_replay_continuation(
        self, replay_job_id: str, continuation_version: int
    ) -> None: ...
    def replay_suffix_unchanged(
        self, replay_job_id: str, after: OrderKey
    ) -> bool: ...
    def stage_replay_chunk(
        self,
        replay_job_id: str,
        *,
        expected_after: OrderKey | None,
        next_after: OrderKey | None,
        alerts: list[dict[str, Any]],
        checkpoints: list[tuple[TelemetryReading, dict[str, Any]]],
        rule_state: dict[str, Any],
        complete: bool,
        converged: bool,
        trigger_seen: bool,
        supersede_end: datetime | None,
    ) -> None: ...
    def discard_replay(self, replay_job_id: str) -> None: ...
    def is_processed(self, canonical_id: str) -> bool: ...
