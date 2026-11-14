"""Tests for third-party vehicle status query logic."""

from typing import Any

import pytest

from fleet_telemetry.fleet_access.thirdparty import AuthorizationDenied, get_vehicle_status


class _FakeU1:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def get_current_state(self, vehicle_id: str) -> dict[str, Any]:
        return self._response


class _FakeAuth:
    def __init__(self, item: dict[str, Any] | None) -> None:
        self._item = item

    def get_client_item(self, client_id: str) -> dict[str, Any] | None:
        return self._item


_AUTHORIZED = {"authorizedVehicleIds": {"V-001", "V-002"}}
_U1_AVAILABLE = {
    "vehicleId": "V-001",
    "outcome": "available",
    "position": {"latitude": 48.0, "longitude": 2.0},
    "positionEventTime": "2026-10-31T12:00:00Z",
    "lastReceivedAt": "2026-10-31T12:00:05Z",
    "stale": False,
}


def test_authorized_vehicle_returns_status():
    result = get_vehicle_status("V-001", "c1", _FakeU1(_U1_AVAILABLE), _FakeAuth(_AUTHORIZED))
    assert result["availability"] == "available"
    assert result["vehicleId"] == "V-001"


def test_authorized_vehicle_no_state_returns_no_data():
    u1 = _FakeU1({"vehicleId": "V-001", "outcome": "noState"})
    result = get_vehicle_status("V-001", "c1", u1, _FakeAuth(_AUTHORIZED))
    assert result == {"availability": "noData", "vehicleId": "V-001"}


def test_unauthorized_vehicle_raises():
    with pytest.raises(AuthorizationDenied):
        get_vehicle_status("V-999", "c1", _FakeU1(_U1_AVAILABLE), _FakeAuth(_AUTHORIZED))


def test_unknown_client_raises():
    with pytest.raises(AuthorizationDenied):
        get_vehicle_status("V-001", "unknown", _FakeU1(_U1_AVAILABLE), _FakeAuth(None))


def test_stale_vehicle_passes_through():
    u1_resp = {**_U1_AVAILABLE, "stale": True}
    result = get_vehicle_status("V-001", "c1", _FakeU1(u1_resp), _FakeAuth(_AUTHORIZED))
    assert result["stale"] is True


def test_empty_authorized_set_raises():
    with pytest.raises(AuthorizationDenied):
        get_vehicle_status("V-001", "c1", _FakeU1(_U1_AVAILABLE), _FakeAuth({"authorizedVehicleIds": set()}))


def test_position_included_in_available_response():
    result = get_vehicle_status("V-001", "c1", _FakeU1(_U1_AVAILABLE), _FakeAuth(_AUTHORIZED))
    assert "position" in result
    assert result["position"] == {"latitude": 48.0, "longitude": 2.0}
