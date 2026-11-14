from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fleet_telemetry.telemetry_core.alerts import evaluate_alerts
from fleet_telemetry.telemetry_core.models import EngineState, TelemetryReading, canonical_order
from fleet_telemetry.telemetry_core.validation import validate_payload

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def payload(**changes):
    value = {
        "vehicleId": "v-1",
        "eventId": "e-1",
        "eventTime": NOW.isoformat(),
        "latitude": 0,
        "longitude": 0,
        "speedKmh": 10,
        "fuelLevelPercent": 50,
        "diagnosticMeasurements": [{"name": "unknown-x", "value": 2.5, "unit": "opaque"}],
    }
    value.update(changes)
    return value


def make_reading(offset, *, event_id="e", speed=20, fuel=50, latitude=0):
    event_time = NOW + timedelta(minutes=offset)
    return TelemetryReading(
        vehicle_id="v-1",
        event_time=event_time,
        latitude=Decimal(str(latitude)),
        longitude=Decimal("0"),
        speed_kmh=Decimal(str(speed)),
        fuel_level_percent=Decimal(str(fuel)),
        diagnostic_measurements=(),
        event_id=event_id,
        canonical_id=f"{offset}-{event_id}",
    )


def test_validation_accepts_inclusive_coordinate_and_fuel_bounds_and_normalizes_values():
    reading, errors = validate_payload(
        payload(latitude=-90, longitude=180, fuelLevelPercent=100), NOW
    )

    assert errors == ()
    assert reading is not None
    assert reading.latitude == Decimal("-90")
    assert reading.longitude == Decimal("180")
    assert reading.fuel_level_percent == Decimal("100")
    assert reading.event_time.tzinfo == timezone.utc


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("latitude", -90.001, "latitude_out_of_range"),
        ("latitude", 90.001, "latitude_out_of_range"),
        ("longitude", -180.001, "longitude_out_of_range"),
        ("longitude", 180.001, "longitude_out_of_range"),
        ("fuelLevelPercent", -0.01, "fuel_out_of_range"),
        ("fuelLevelPercent", 100.01, "fuel_out_of_range"),
        ("speedKmh", -0.01, "speed_negative"),
    ],
)
def test_validation_rejects_values_outside_defined_numeric_bounds(field, value, reason):
    reading, errors = validate_payload(payload(**{field: value}), NOW)

    assert reading is None
    assert reason in errors


def test_validation_preserves_well_formed_unknown_diagnostics():
    reading, errors = validate_payload(payload(), NOW)

    assert errors == ()
    assert reading is not None
    assert reading.diagnostic_measurements[0].name == "unknown-x"
    assert reading.diagnostic_measurements[0].unit == "opaque"


@pytest.mark.parametrize(
    ("payload_state", "expected_state", "expected_reason"),
    [
        ("absent", EngineState.NOT_REPORTED, None),
        ("ON", EngineState.ON, None),
        ("OFF", EngineState.OFF, None),
        ("NOT_REPORTED", EngineState.NOT_REPORTED, None),
        (None, EngineState.NOT_REPORTED, "engine_state_invalid"),
        ("broken", EngineState.NOT_REPORTED, "engine_state_invalid"),
        (17, EngineState.NOT_REPORTED, "engine_state_invalid"),
    ],
)
def test_engine_state_normalizes_separately_without_quarantining(
    payload_state, expected_state, expected_reason
):
    body = payload()
    if payload_state != "absent":
        body["engineState"] = payload_state

    reading, errors = validate_payload(body, NOW)

    assert errors == ()
    assert reading is not None
    assert reading.engine_state is expected_state
    assert reading.engine_state_reason == expected_reason


def test_timestamp_identity_is_used_only_when_event_id_is_absent():
    without_id, errors = validate_payload(payload(eventId=None), NOW)
    identified, _ = validate_payload(payload(eventId="evt-1"), NOW)

    assert errors == ()
    assert without_id is not None and identified is not None
    assert without_id.canonical_id != identified.canonical_id


def test_validation_enforces_inclusive_event_time_window():
    old_edge = NOW - timedelta(days=90)
    future_edge = NOW + timedelta(minutes=5)

    assert validate_payload(payload(eventTime=old_edge.isoformat()), NOW)[1] == ()
    assert validate_payload(payload(eventTime=future_edge.isoformat()), NOW)[1] == ()
    assert "event_too_old" in validate_payload(
        payload(eventTime=(old_edge - timedelta(microseconds=1)).isoformat()), NOW
    )[1]
    assert "event_in_future" in validate_payload(
        payload(eventTime=(future_edge + timedelta(microseconds=1)).isoformat()), NOW
    )[1]


def test_canonical_tie_order_prefers_event_id_then_byte_order():
    no_id = make_reading(0, event_id=None)
    z_id = make_reading(0, event_id="z")
    a_id = make_reading(0, event_id="a")

    assert sorted([no_id, z_id, a_id], key=canonical_order) == [a_id, z_id, no_id]


def test_speed_alert_requires_two_consecutive_readings_strictly_over_threshold():
    single = evaluate_alerts([make_reading(0, speed=111)])
    exact = evaluate_alerts([make_reading(0, speed=111), make_reading(1, speed=110)])
    qualified = evaluate_alerts([make_reading(0, speed=111), make_reading(1, speed=111)])

    assert not [alert for alert in single if alert["alertType"] == "excessive-speed"]
    assert not [alert for alert in exact if alert["alertType"] == "excessive-speed"]
    assert len([alert for alert in qualified if alert["alertType"] == "excessive-speed"]) == 1


def test_fuel_alert_is_strictly_more_than_ten_points_with_five_minute_window():
    exact = evaluate_alerts([make_reading(0, fuel=60), make_reading(5, fuel=50)])
    above = evaluate_alerts([make_reading(0, fuel=60), make_reading(5, fuel=Decimal("49.99"))])
    too_late = evaluate_alerts([make_reading(0, fuel=60), make_reading(6, fuel=49)])

    assert not [alert for alert in exact if alert["alertType"] == "sudden-fuel-drop"]
    assert len([alert for alert in above if alert["alertType"] == "sudden-fuel-drop"]) == 1
    assert not [alert for alert in too_late if alert["alertType"] == "sudden-fuel-drop"]


def test_alert_cooldown_is_inclusive_and_replay_is_deterministic():
    history = [
        make_reading(0, speed=111),
        make_reading(1, speed=112),
        make_reading(10, speed=113),
        make_reading(11, speed=114),
    ]

    first = evaluate_alerts(history)
    replayed = evaluate_alerts(list(reversed(history)))
    speed = [alert for alert in first if alert["alertType"] == "excessive-speed"]

    assert first == replayed
    assert [item["effectiveState"] for item in speed] == ["effective", "superseded", "effective"]
