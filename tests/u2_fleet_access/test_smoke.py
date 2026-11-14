"""Walking skeleton smoke test for U2 Fleet Access."""

from fleet_telemetry.fleet_access.authorization import is_vehicle_authorized
from fleet_telemetry.fleet_access.staff import format_vehicle_state
from fleet_telemetry.fleet_access.thirdparty import get_vehicle_status


def test_imports_succeed():
    assert callable(is_vehicle_authorized)
    assert callable(format_vehicle_state)
    assert callable(get_vehicle_status)


def test_authorized_vehicle_smoke():
    item = {"authorizedVehicleIds": {"V-001"}}
    assert is_vehicle_authorized("V-001", item) is True


def test_unauthorized_vehicle_smoke():
    assert is_vehicle_authorized("V-999", None) is False
