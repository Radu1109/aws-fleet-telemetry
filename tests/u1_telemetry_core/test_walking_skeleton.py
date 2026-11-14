from datetime import datetime, timezone
from decimal import Decimal

from fleet_telemetry.telemetry_core.memory import AcceptAndReadPath, MemoryTelemetryStore
from fleet_telemetry.telemetry_core.models import TelemetryReading, canonical_identity


def test_accept_store_read_smoke_path_returns_the_persisted_reading():
    event_time = datetime(2026, 10, 24, 10, tzinfo=timezone.utc)
    reading = TelemetryReading(
        vehicle_id="vehicle-7",
        event_time=event_time,
        latitude=Decimal("48.1"),
        longitude=Decimal("11.5"),
        speed_kmh=Decimal("35"),
        fuel_level_percent=Decimal("72"),
        diagnostic_measurements=(),
        event_id="evt-1",
        receipt_time=event_time,
        canonical_id=canonical_identity("vehicle-7", "evt-1", event_time),
    )
    store = MemoryTelemetryStore()
    path = AcceptAndReadPath(store)

    accepted = path.accept(reading)

    assert store.get_reading(reading.canonical_id) == reading
    assert accepted == reading
