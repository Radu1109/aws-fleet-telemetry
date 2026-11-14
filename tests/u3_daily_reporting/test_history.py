from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fleet_telemetry.daily_reporting.history import (
    HistoryContractError,
    U1HistoryAdapter,
)
from fleet_telemetry.daily_reporting.models import HistoryCompleteness
from fleet_telemetry.daily_reporting.retries import RetryPolicy, TransientReportError
from fleet_telemetry.telemetry_core.models import TelemetryReading

UTC = timezone.utc
WATERMARK = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)
BOUNDARY = WATERMARK - timedelta(days=90)


def reading(second: int) -> TelemetryReading:
    event_time = datetime(2026, 10, 10, tzinfo=UTC) + timedelta(seconds=second)
    return TelemetryReading(
        vehicle_id="v-1",
        event_time=event_time,
        latitude=Decimal("0"),
        longitude=Decimal("0"),
        speed_kmh=Decimal("0"),
        fuel_level_percent=Decimal("50"),
        diagnostic_measurements=(),
        event_id=f"e-{second}",
        canonical_id=f"id-{second}",
    )


def page(
    *,
    readings=None,
    token=None,
    watermark=WATERMARK,
    completeness="complete",
    boundary=BOUNDARY,
):
    return {
        "readings": readings or [],
        "lastValidReadingBeforeStart": None,
        "nextContinuationToken": token,
        "completeness": completeness,
        "retentionBoundary": boundary,
        "watermark": watermark,
    }


class FakeHistory:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def read_canonical_history(self, vehicle_id, start, end, token=None):
        self.calls.append((vehicle_id, start, end, token))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def adapter(port, *, max_pages=4, retries=1):
    return U1HistoryAdapter(
        port,
        max_pages=max_pages,
        retry_policy=RetryPolicy(retries, 0, 0),
        sleep=lambda _delay: None,
    )


def test_pagination_preserves_fixed_watermark_and_all_page_readings():
    port = FakeHistory([page(readings=[reading(1)], token="page-2"), page(readings=[reading(2)])])

    result = adapter(port).read_all("v-1", date(2026, 10, 10))

    assert [item.canonical_id for item in result.readings] == ["id-1", "id-2"]
    assert result.watermark == WATERMARK
    assert [call[3] for call in port.calls] == [None, "page-2"]
    assert all(call[1].hour == 0 and call[2] - call[1] == timedelta(days=1) for call in port.calls)


def test_successful_empty_history_is_not_a_query_failure():
    result = adapter(FakeHistory([page()])).read_all("v-1", date(2026, 10, 10))

    assert result.readings == ()
    assert result.completeness is HistoryCompleteness.COMPLETE


def test_retention_boundary_partial_history_is_preserved():
    port = FakeHistory([page(completeness="partial")])

    result = adapter(port).read_all("v-1", date(2026, 10, 10))

    assert result.completeness is HistoryCompleteness.PARTIAL
    assert result.retention_boundary == BOUNDARY


def test_watermark_change_between_pages_fails_closed():
    changed = WATERMARK + timedelta(seconds=1)
    port = FakeHistory([page(token="next"), page(watermark=changed)])

    with pytest.raises(HistoryContractError, match="changed the history snapshot"):
        adapter(port).read_all("v-1", date(2026, 10, 10))


def test_page_bound_and_repeated_token_are_detected():
    with pytest.raises(HistoryContractError, match="page bound"):
        adapter(FakeHistory([page(token="more")]), max_pages=1).read_all(
            "v-1", date(2026, 10, 10)
        )
    with pytest.raises(HistoryContractError, match="repeated a continuation"):
        adapter(
            FakeHistory([page(token="same"), page(token="same"), page()]),
            max_pages=4,
        ).read_all("v-1", date(2026, 10, 10))


def test_transient_page_read_retries_same_token_then_returns_page():
    port = FakeHistory([TransientReportError("temporary"), page(readings=[reading(1)])])
    delays = []
    history = U1HistoryAdapter(
        port,
        max_pages=2,
        retry_policy=RetryPolicy(2, 0.1, 0.1),
        sleep=delays.append,
    )

    result = history.read_all("v-1", date(2026, 10, 10))

    assert len(port.calls) == 2
    assert port.calls[0][3] is port.calls[1][3] is None
    assert delays == [0.1]
    assert len(result.readings) == 1


def test_transient_page_read_exhaustion_surfaces_failure():
    port = FakeHistory(
        [TransientReportError("temporary-1"), TransientReportError("temporary-2")]
    )

    with pytest.raises(TransientReportError, match="temporary-2"):
        adapter(port, retries=2).read_all("v-1", date(2026, 10, 10))

    assert len(port.calls) == 2
