"""Idempotent receipt freshness, position projection, alerts, and bounded replay."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .alerts import ALERT_COOLDOWN, FUEL_LOOKBACK, SPEED_LIMIT, evaluate_alerts
from .models import TelemetryReading, as_utc, canonical_order
from .ports import (
    OrderKey,
    ProcessingRepository,
    ProcessingVersionConflict,
)

REPLAY_PAGE_SIZE = 10
MAX_CONFLICT_RETRIES = 3
INITIAL_CONFLICT_BACKOFF_SECONDS = 0.1
MAX_CONFLICT_BACKOFF_SECONDS = 1.0


class ProcessingRetryExhausted(RuntimeError):
    """The delivery must remain retryable after repeated optimistic conflicts."""


class ReplayContinuationPending(RuntimeError):
    """Replay yielded after handing its durable continuation to the queue."""


@dataclass(frozen=True, slots=True)
class _PreparedProcessing:
    projection: dict[str, Any]
    alerts: list[dict[str, Any]]
    condition_checkpoints: list[tuple[TelemetryReading, dict[str, Any]]]
    supersede_range: tuple[datetime, datetime] | None = None
    replay_job_id: str | None = None
    replay_pending: bool = False
    continuation_version: int | None = None


class ReceiptProcessor:
    """Maintains maximum trusted receipt time independently of event time."""

    def __init__(self, repository: ProcessingRepository) -> None:
        self._repository = repository

    def process(self, vehicle_id: str, receipt_time: datetime) -> None:
        self._repository.record_receipt(vehicle_id, as_utc(receipt_time))


class AcceptedReadingProcessor:
    """Optimistically compute and atomically publish per-vehicle reading effects."""

    def __init__(
        self,
        repository: ProcessingRepository,
        cooldown: timedelta = ALERT_COOLDOWN,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        replay_page_size: int = REPLAY_PAGE_SIZE,
    ) -> None:
        self._repository = repository
        if cooldown < timedelta(0):
            raise ValueError("cooldown cannot be negative")
        self._cooldown = cooldown
        self._sleeper = sleeper
        self._jitter = jitter
        if replay_page_size < 1:
            raise ValueError("replay page size must be positive")
        self._replay_page_size = replay_page_size

    def process(self, reading: TelemetryReading) -> bool:
        if not reading.canonical_id:
            raise ValueError("canonical identity is required")
        for retry in range(MAX_CONFLICT_RETRIES + 1):
            snapshot = self._repository.processing_snapshot(reading.vehicle_id)
            if self._repository.is_processed(reading.canonical_id):
                return False
            prepared: _PreparedProcessing | None = None
            try:
                position_order = _projection_order(snapshot.projection)
                if position_order is not None and canonical_order(reading) < position_order:
                    prepared = self._replay(reading, snapshot.version, snapshot.projection)
                else:
                    prepared = self._process_in_order(reading, snapshot.projection)
                if prepared.replay_pending:
                    if (
                        prepared.replay_job_id is None
                        or prepared.continuation_version is None
                    ):
                        raise RuntimeError("replay continuation state is incomplete")
                    self._repository.enqueue_replay_continuation(
                        prepared.replay_job_id, prepared.continuation_version
                    )
                    raise ReplayContinuationPending(
                        "replay continuation was queued; final publication is pending"
                    )
                return self._repository.commit_processing(
                    reading.vehicle_id,
                    snapshot.version,
                    reading.canonical_id,
                    projection=prepared.projection,
                    alerts=prepared.alerts,
                    condition_checkpoints=prepared.condition_checkpoints,
                    supersede_range=prepared.supersede_range,
                    replay_job_id=prepared.replay_job_id,
                )
            except ValueError:
                if self._repository.is_processed(reading.canonical_id):
                    return False
                raise
            except ProcessingVersionConflict as conflict:
                if prepared is not None and prepared.replay_job_id is not None:
                    self._repository.discard_replay(prepared.replay_job_id)
                if retry == MAX_CONFLICT_RETRIES:
                    raise ProcessingRetryExhausted(
                        f"processing for vehicle {reading.vehicle_id!r} exceeded "
                        f"{MAX_CONFLICT_RETRIES} conflict retries"
                    ) from conflict
                backoff_ceiling = min(
                    INITIAL_CONFLICT_BACKOFF_SECONDS * (2**retry),
                    MAX_CONFLICT_BACKOFF_SECONDS,
                )
                self._sleeper(self._jitter(0.0, backoff_ceiling))
        raise AssertionError("conflict retry loop exited without a result")

    def process_replay_continuation(
        self,
        replay_job_id: str,
        continuation_version: int,
        canonical_id: str,
    ) -> bool:
        """Process at most one persisted replay chunk for a queue continuation."""
        if self._repository.is_processed(canonical_id):
            return False
        session = self._repository.replay_session(replay_job_id)
        if session["canonical_id"] != canonical_id:
            raise ValueError("replay continuation identity does not match its job")
        current_version = session["continuation_version"]
        if continuation_version > current_version:
            raise ValueError("replay continuation version is ahead of persisted state")
        if session["complete"]:
            return self._publish_replay(replay_job_id, session)
        if continuation_version < current_version:
            self._repository.enqueue_replay_continuation(
                replay_job_id, current_version
            )
            return False

        reading = self._repository.get_reading(canonical_id)
        if reading is None:
            raise RuntimeError("replay reading is unavailable")
        try:
            self._run_replay_chunk(replay_job_id, reading, session)
            session = self._repository.replay_session(replay_job_id)
            if session["complete"]:
                return self._publish_replay(replay_job_id, session)
            self._repository.enqueue_replay_continuation(
                replay_job_id, session["continuation_version"]
            )
            return True
        except ProcessingVersionConflict:
            self._repository.discard_replay(replay_job_id)
            try:
                return self.process(reading)
            except ReplayContinuationPending:
                return True

    def _process_in_order(
        self, reading: TelemetryReading, projection: dict[str, Any] | None
    ) -> _PreparedProcessing:
        order = canonical_order(reading)
        prior = self._repository.latest_event_before(reading.vehicle_id, order)
        state = self._repository.processing_checkpoint_before(reading.vehicle_id, order)
        if state is None:
            state = _initial_rule_state()
            if prior is not None:
                state["speedPredecessor"] = prior
                state["lastProcessedReading"] = prior
                state["trailingFuelReadings"] = self._repository.readings_in_range(
                    reading.vehicle_id,
                    as_utc(reading.event_time) - FUEL_LOOKBACK,
                    as_utc(reading.event_time),
                )
                state["trailingFuelReadings"] = [
                    item
                    for item in state["trailingFuelReadings"]
                    if canonical_order(item) < order
                ]
        candidates, next_state = _advance_rule_state(state, reading, self._cooldown)
        return _PreparedProcessing(
            projection=_projection(reading, next_state["activeConditions"]),
            alerts=candidates,
            condition_checkpoints=[(reading, next_state)],
        )

    def _replay(
        self,
        late_reading: TelemetryReading,
        expected_version: int,
        projection: dict[str, Any] | None,
    ) -> _PreparedProcessing:
        if projection is None:
            raise RuntimeError("late replay requires an existing published projection")
        affected_order = canonical_order(late_reading)
        replay_job_id = self._repository.begin_replay(
            late_reading.vehicle_id,
            late_reading.canonical_id,
            expected_version,
            affected_order,
            _initial_rule_state(),
        )
        try:
            session = self._repository.replay_session(replay_job_id)
            if not session["complete"] and session["continuation_version"] == 0:
                self._run_replay_chunk(replay_job_id, late_reading, session)
            session = self._repository.replay_session(replay_job_id)
            if not session["trigger_seen"]:
                self._repository.discard_replay(replay_job_id)
                raise RuntimeError("late reading is absent from the pinned replay snapshot")
            if not session["complete"]:
                return _PreparedProcessing(
                    projection={},
                    alerts=[],
                    condition_checkpoints=[],
                    replay_job_id=replay_job_id,
                    replay_pending=True,
                    continuation_version=session["continuation_version"],
                )
            return self._replay_prepared(replay_job_id, session)
        except ProcessingVersionConflict:
            self._repository.discard_replay(replay_job_id)
            raise

    def _replay_prepared(
        self, replay_job_id: str, session: dict[str, Any]
    ) -> _PreparedProcessing:
        replay_projection = dict(session["projection"])
        if not session["converged"]:
            replay_projection["activeConditions"] = session["rule_state"]["activeConditions"]
        return _PreparedProcessing(
            projection=replay_projection,
            alerts=[],
            condition_checkpoints=[],
            replay_job_id=replay_job_id,
        )

    def _publish_replay(self, replay_job_id: str, session: dict[str, Any]) -> bool:
        if not session["trigger_seen"]:
            self._repository.discard_replay(replay_job_id)
            raise RuntimeError("late reading is absent from the pinned replay snapshot")
        prepared = self._replay_prepared(replay_job_id, session)
        return self._repository.commit_processing(
            session["vehicle_id"],
            session["expected_version"],
            session["canonical_id"],
            projection=prepared.projection,
            alerts=prepared.alerts,
            condition_checkpoints=prepared.condition_checkpoints,
            replay_job_id=replay_job_id,
        )

    def _run_replay_chunk(
        self,
        replay_job_id: str,
        late_reading: TelemetryReading,
        session: dict[str, Any],
    ) -> None:
        if session["complete"]:
            return
        page = self._repository.replay_readings_page(
            replay_job_id, self._replay_page_size
        )
        self._replay_chunk(replay_job_id, late_reading, session, page)

    def _replay_chunk(
        self,
        replay_job_id: str,
        late_reading: TelemetryReading,
        session: dict[str, Any],
        page: list[TelemetryReading],
    ) -> None:
        if not page:
            self._stage_replay_chunk(
                replay_job_id,
                session,
                next_after=session["next_after"],
                alerts=[],
                checkpoints=[],
                rule_state=session["rule_state"],
                complete=True,
                converged=False,
                trigger_seen=session["trigger_seen"],
                supersede_end=session["supersede_range"][1]
                if session["supersede_range"]
                else late_reading.event_time,
            )
            return

        state = session["rule_state"]
        trigger_seen = session["trigger_seen"]
        alerts: list[dict[str, Any]] = []
        checkpoints: list[tuple[TelemetryReading, dict[str, Any]]] = []
        next_after = session["next_after"]
        end_time = late_reading.event_time
        converged = False
        for item in page:
            candidates, state = _advance_rule_state(state, item, self._cooldown)
            alerts.extend(candidates)
            checkpoints.append((item, state))
            next_after, end_time = canonical_order(item), item.event_time
            trigger_seen |= item.canonical_id == late_reading.canonical_id
            if trigger_seen and self._state_converged(replay_job_id, item, state):
                converged = True
                break
        complete = converged or not self._repository.replay_has_more(
            replay_job_id, next_after
        )
        self._stage_replay_chunk(
            replay_job_id,
            session,
            next_after=next_after,
            alerts=alerts,
            checkpoints=checkpoints,
            rule_state=state,
            complete=complete,
            converged=converged,
            trigger_seen=trigger_seen,
            supersede_end=end_time,
        )

    def _state_converged(
        self,
        replay_job_id: str,
        reading: TelemetryReading,
        state: dict[str, Any],
    ) -> bool:
        order = canonical_order(reading)
        stored = self._repository.processing_checkpoint_at(reading.vehicle_id, order)
        return (
            stored is not None
            and state == stored
            and self._repository.replay_suffix_unchanged(replay_job_id, order)
        )

    def _stage_replay_chunk(
        self,
        replay_job_id: str,
        session: dict[str, Any],
        **chunk: Any,
    ) -> None:
        try:
            self._repository.stage_replay_chunk(
                replay_job_id,
                expected_after=session["next_after"],
                **chunk,
            )
        except ProcessingVersionConflict:
            self._repository.discard_replay(replay_job_id)
            raise


def _projection_order(projection: dict[str, Any] | None) -> OrderKey | None:
    if projection is None or "positionEventTime" not in projection:
        return None
    event_id = projection.get("positionEventId")
    return (
        as_utc(projection["positionEventTime"]),
        0 if event_id is not None else 1,
        event_id.encode("utf-8") if event_id is not None else b"",
    )


def _initial_rule_state() -> dict[str, Any]:
    return {
        "lastEffectiveTriggerTimes": {
            "excessive-speed": None,
            "sudden-fuel-drop": None,
        },
        "activeConditions": _initial_conditions(),
        "lastProcessedReading": None,
        "speedPredecessor": None,
        "trailingFuelReadings": (),
    }


def _advance_rule_state(
    prior_state: dict[str, Any],
    reading: TelemetryReading,
    cooldown: timedelta,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate one canonical reading from a complete replay continuation state."""
    previous = prior_state.get("speedPredecessor")
    context = list(prior_state.get("trailingFuelReadings", ()))
    if previous is not None and all(
        item.canonical_id != previous.canonical_id for item in context
    ):
        context.append(previous)
    if all(item.canonical_id != reading.canonical_id for item in context):
        context.append(reading)
    context.sort(key=canonical_order)
    candidates = [
        item
        for item in evaluate_alerts(context, cooldown)
        if item["triggeringReadingIdentity"] == reading.canonical_id
    ]

    last_effective = dict(prior_state["lastEffectiveTriggerTimes"])
    for item in candidates:
        alert_type = item["alertType"]
        previous_time = last_effective[alert_type]
        raised_at = as_utc(item["raisedAt"])
        if previous_time is None or raised_at - as_utc(previous_time) >= cooldown:
            item["effectiveState"] = "effective"
            last_effective[alert_type] = raised_at
        else:
            item["effectiveState"] = "superseded"

    conditions = _advance_conditions(
        prior_state["activeConditions"], previous, reading, candidates
    )
    reading_time = as_utc(reading.event_time)
    trailing_fuel = [
        item
        for item in prior_state.get("trailingFuelReadings", ())
        if reading_time - as_utc(item.event_time) <= FUEL_LOOKBACK
        and canonical_order(item) < canonical_order(reading)
    ]
    trailing_fuel.append(reading)
    state = {
        "lastEffectiveTriggerTimes": last_effective,
        "activeConditions": conditions,
        "lastProcessedReading": reading,
        "speedPredecessor": reading,
        "trailingFuelReadings": tuple(trailing_fuel),
    }
    return candidates, state


def _projection(
    reading: TelemetryReading, conditions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "vehicleId": reading.vehicle_id,
        "positionReadingIdentity": reading.canonical_id,
        "positionEventTime": reading.event_time,
        "positionEventId": reading.event_id,
        "position": {
            "latitude": reading.latitude,
            "longitude": reading.longitude,
        },
        "speedKmh": reading.speed_kmh,
        "fuelLevelPercent": reading.fuel_level_percent,
        "activeConditions": conditions,
    }


def _initial_conditions() -> dict[str, dict[str, Any]]:
    return {
        "excessive-speed": {"state": "inactive", "clearedAt": None},
        "sudden-fuel-drop": {"state": "inactive", "clearedAt": None},
    }


def _advance_conditions(
    prior_conditions: dict[str, Any],
    prior_reading: TelemetryReading | None,
    reading: TelemetryReading,
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    conditions = {name: dict(value) for name, value in prior_conditions.items()}
    speed = conditions["excessive-speed"]
    if reading.speed_kmh <= SPEED_LIMIT:
        conditions["excessive-speed"] = {
            "state": "inactive",
            "clearedAt": reading.event_time if speed["state"] == "active" else speed.get("clearedAt"),
        }
    else:
        speed_alert = next(
            (
                item
                for item in alerts
                if item["alertType"] == "excessive-speed"
                and item["effectiveState"] == "effective"
            ),
            None,
        )
        if prior_reading is not None and prior_reading.speed_kmh > SPEED_LIMIT and speed_alert:
            conditions["excessive-speed"] = {
                "state": "active",
                "alertId": speed_alert["alertId"],
                "raisedAt": speed_alert["raisedAt"],
            }
    fuel_alert = next(
        (
            item
            for item in alerts
            if item["alertType"] == "sudden-fuel-drop"
            and item["effectiveState"] == "effective"
        ),
        None,
    )
    if fuel_alert is not None:
        conditions["sudden-fuel-drop"] = {
            "state": "active",
            "alertId": fuel_alert["alertId"],
            "raisedAt": fuel_alert["raisedAt"],
        }
    return conditions
