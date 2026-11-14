"""Tests for client vehicle authorization pure logic."""


from fleet_telemetry.fleet_access.authorization import is_vehicle_authorized


def test_authorized_vehicle_returns_true():
    item = {"clientId": "c1", "authorizedVehicleIds": {"V-001", "V-002"}}
    assert is_vehicle_authorized("V-001", item) is True


def test_unauthorized_vehicle_returns_false():
    item = {"clientId": "c1", "authorizedVehicleIds": {"V-001"}}
    assert is_vehicle_authorized("V-999", item) is False


def test_none_item_denies():
    assert is_vehicle_authorized("V-001", None) is False


def test_empty_vehicle_set_denies():
    item = {"clientId": "c1", "authorizedVehicleIds": set()}
    assert is_vehicle_authorized("V-001", item) is False


def test_missing_authorized_vehicles_key_denies():
    item = {"clientId": "c1"}
    assert is_vehicle_authorized("V-001", item) is False


def test_vehicle_in_multi_vehicle_set():
    item = {"authorizedVehicleIds": {"V-001", "V-002", "V-003"}}
    assert is_vehicle_authorized("V-003", item) is True


def test_exact_vehicle_id_match_required():
    item = {"authorizedVehicleIds": {"V-001"}}
    assert is_vehicle_authorized("V-0010", item) is False
