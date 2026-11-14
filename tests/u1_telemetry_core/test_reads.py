from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fleet_telemetry.telemetry_core.memory import MemoryTelemetryStore
from fleet_telemetry.telemetry_core.models import TelemetryReading
from fleet_telemetry.telemetry_core.reads import InvalidRange, QueryFailed, ReadService

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


def point(index, event_time=None, receipt_time=None):
    event_time = event_time or NOW - timedelta(minutes=index)
    receipt_time = receipt_time or NOW - timedelta(seconds=index)
    return TelemetryReading(
        vehicle_id="v-1",
        event_time=event_time,
        latitude=Decimal(index),
        longitude=Decimal("2"),
        speed_kmh=Decimal("3"),
        fuel_level_percent=Decimal("4"),
        diagnostic_measurements=(),
        event_id=f"e-{index}",
        receipt_time=receipt_time,
        canonical_id=f"id-{index}",
    )


def service(store, clock=None, page_size=100):
    return ReadService(store, store, clock or FixedClock(NOW), page_size)


def test_current_state_distinguishes_no_state_and_stale_boundary():
    store = MemoryTelemetryStore()
    query = service(store)
    assert query.get_current_state("v-1") == {"vehicleId": "v-1", "outcome": "noState"}
    store.store_projection(
        "v-1",
        {
            "position": {"latitude": 1, "longitude": 2},
            "positionEventTime": NOW - timedelta(minutes=1),
        },
    )
    store.record_receipt("v-1", NOW - timedelta(seconds=60))

    result = query.get_current_state("v-1")

    assert result["outcome"] == "available"
    assert result["stale"] is True


def test_current_state_reports_missing_freshness_as_failure_not_no_data():
    store = MemoryTelemetryStore()
    store.store_projection("v-1", {"position": {}, "positionEventTime": NOW})

    with pytest.raises(QueryFailed):
        service(store).get_current_state("v-1")


def test_alert_read_returns_effective_evidence_only():
    store = MemoryTelemetryStore()
    store.store_alerts(
        "v-1",
        [
            {"alertId": "effective", "effectiveState": "effective"},
            {"alertId": "old", "effectiveState": "superseded"},
        ],
    )

    result = service(store).list_effective_alerts("v-1")

    assert [item["alertId"] for item in result["alerts"]] == ["effective"]


def test_route_history_empty_is_success_and_query_failure_is_distinct():
    store = MemoryTelemetryStore()
    query = service(store)
    empty = query.get_route_history("v-1", NOW - timedelta(hours=1), NOW)
    assert empty["statusCode"] == 200 and empty["points"] == []

    class FailedStore(MemoryTelemetryStore):
        def list_readings_page(self, vehicle_id, start, end, accepted_through, after, limit):
            raise OSError("storage unavailable")

    failed = service(FailedStore())
    with pytest.raises(QueryFailed):
        failed.get_route_history("v-1", NOW - timedelta(hours=1), NOW)


def test_route_history_enforces_range_and_inclusive_retention_boundary():
    query = service(MemoryTelemetryStore())
    boundary = NOW - timedelta(days=90)

    assert query.get_route_history("v-1", boundary, boundary + timedelta(hours=24))["points"] == []
    with pytest.raises(InvalidRange) as long:
        query.get_route_history("v-1", NOW - timedelta(hours=25), NOW)
    with pytest.raises(InvalidRange) as expired:
        query.get_route_history(
            "v-1",
            boundary - timedelta(microseconds=1),
            boundary + timedelta(minutes=1),
        )
    assert "interval_over_24_hours" in long.value.reasons
    assert "before_retention" in expired.value.reasons


def test_canonical_history_pages_are_stable_at_watermark_and_retryable():
    store = MemoryTelemetryStore()
    originals = [point(index) for index in range(3)]
    for item in originals:
        store.store_if_absent(item)
    clock = FixedClock(NOW)
    query = service(store, clock, page_size=2)
    first = query.read_canonical_history("v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    token = first["nextContinuationToken"]
    assert token is not None and len(first["readings"]) == 2

    clock.value = NOW + timedelta(seconds=1)
    late = point(4, event_time=NOW - timedelta(minutes=4), receipt_time=clock.value)
    store.store_if_absent(late)
    next_page = query.read_canonical_history(
        "v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1), token
    )
    retry = query.read_canonical_history(
        "v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1), token
    )

    assert len(next_page["readings"]) == 1
    assert next_page == retry
    assert late not in next_page["readings"]


def test_route_history_continuation_keeps_its_watermark_and_order():
    store = MemoryTelemetryStore()
    originals = [point(index) for index in range(3)]
    for item in originals:
        store.store_if_absent(item)
    clock = FixedClock(NOW)
    query = service(store, clock, page_size=1)
    first = query.get_route_history("v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    token = first["nextContinuationToken"]

    assert token is not None
    clock.value += timedelta(seconds=1)
    later = point(4, event_time=NOW - timedelta(minutes=4), receipt_time=clock.value)
    store.store_if_absent(later)
    second = query.get_route_history(
        "v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1), token
    )

    assert len(first["points"]) == len(second["points"]) == 1
    assert first["points"][0].event_time < second["points"][0].event_time
    assert later not in second["points"]


def test_canonical_history_reports_partial_retention_and_rejects_bad_cursor():
    query = service(MemoryTelemetryStore())
    boundary = NOW - timedelta(days=90)
    partial = query.read_canonical_history(
        "v-1",
        boundary - timedelta(days=1),
        NOW,
    )
    assert partial["completeness"] == "partial"
    assert partial["retentionBoundary"] == boundary

    with pytest.raises(InvalidRange):
        query.read_canonical_history(
            "v-1",
            NOW - timedelta(hours=1),
            NOW,
            "not-a-valid-cursor",
        )


def test_history_queries_use_bounded_ordered_repository_pages():
    class BoundedStore(MemoryTelemetryStore):
        def __init__(self):
            super().__init__()
            self.page_queries = []

        def list_readings(self, vehicle_id):
            raise AssertionError("history reads must not load the full vehicle history")

        def list_readings_page(
            self, vehicle_id, start, end, accepted_through, after, limit
        ):
            self.page_queries.append((start, end, accepted_through, after, limit))
            return super().list_readings_page(
                vehicle_id, start, end, accepted_through, after, limit
            )

    store = BoundedStore()
    in_range = point(1)
    unrelated_old = point(2, event_time=NOW - timedelta(days=2))
    unrelated_future = point(3, event_time=NOW + timedelta(days=2))
    for item in (in_range, unrelated_old, unrelated_future):
        store.store_if_absent(item)
    query = service(store, page_size=5)
    start, end = NOW - timedelta(hours=1), NOW

    route = query.get_route_history("v-1", start, end)
    canonical = query.read_canonical_history("v-1", start, end)

    assert route["points"] == [in_range]
    assert canonical["readings"] == [in_range]
    assert store.page_queries
    assert all(entry[0:2] == (start, end) for entry in store.page_queries)


def test_canonical_snapshot_excludes_delayed_commit_and_pins_retention_on_retry():
    store = MemoryTelemetryStore()
    boundary = NOW - timedelta(days=90)
    originals = [
        point(1, event_time=boundary + timedelta(minutes=1)),
        point(2, event_time=boundary + timedelta(minutes=2)),
        point(3, event_time=boundary + timedelta(minutes=3)),
    ]
    for item in originals:
        store.store_if_absent(item)
    clock = FixedClock(NOW)
    query = service(store, clock, page_size=1)
    start, end = boundary + timedelta(minutes=1), NOW

    first = query.read_canonical_history("v-1", start, end)
    token = first["nextContinuationToken"]
    delayed = point(
        4,
        event_time=boundary + timedelta(minutes=1, seconds=30),
        receipt_time=NOW - timedelta(seconds=1),
    )
    store.store_if_absent(delayed)
    clock.value = NOW + timedelta(days=2)

    next_page = query.read_canonical_history("v-1", start, end, token)
    retry = query.read_canonical_history("v-1", start, end, token)

    assert next_page == retry
    assert next_page["readings"] == [originals[1]]
    assert delayed not in next_page["readings"]
    assert next_page["retentionBoundary"] == boundary
    assert next_page["watermark"] == NOW


def test_altered_opaque_continuation_is_rejected():
    store = MemoryTelemetryStore()
    for index in range(3):
        store.store_if_absent(point(index))
    query = service(store, page_size=1)
    first = query.read_canonical_history(
        "v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    )
    token = first["nextContinuationToken"]
    altered = token[:-1] + ("a" if token[-1] != "a" else "b")

    with pytest.raises(InvalidRange):
        query.read_canonical_history(
            "v-1", NOW - timedelta(hours=1), NOW + timedelta(hours=1), altered
        )


def test_partial_canonical_history_excludes_expired_repository_rows():
    boundary = NOW - timedelta(days=90)
    expired = point(1, event_time=boundary - timedelta(seconds=1))
    retained = point(2, event_time=boundary)
    store = MemoryTelemetryStore()
    store.store_if_absent(expired)
    store.store_if_absent(retained)

    result = service(store).read_canonical_history(
        "v-1", boundary - timedelta(days=1), NOW
    )

    assert result["completeness"] == "partial"
    assert result["readings"] == [retained]
    assert result["lastValidReadingBeforeStart"] is None
