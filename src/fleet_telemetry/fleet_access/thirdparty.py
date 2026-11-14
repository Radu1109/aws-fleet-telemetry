"""Third-party current-status query business logic."""

from __future__ import annotations

from typing import Any, Protocol

from .authorization import AuthorizationTablePort, is_vehicle_authorized


class U1QueryPort(Protocol):
    def get_current_state(self, vehicle_id: str) -> dict[str, Any]: ...


class AuthorizationDenied(Exception):
    """Raised when a client is not authorized for the requested vehicle."""


def get_vehicle_status(
    vehicle_id: str,
    client_id: str,
    u1_port: U1QueryPort,
    auth_port: AuthorizationTablePort,
) -> dict[str, Any]:
    """Return current vehicle status or raise AuthorizationDenied."""
    auth_item = auth_port.get_client_item(client_id)
    if not is_vehicle_authorized(vehicle_id, auth_item):
        raise AuthorizationDenied()
    response = u1_port.get_current_state(vehicle_id)
    if response.get("outcome") == "noState":
        return {"availability": "noData", "vehicleId": vehicle_id}
    return {
        "availability": "available",
        "vehicleId": vehicle_id,
        "position": response.get("position"),
        "positionEventTime": response.get("positionEventTime"),
        "lastReceivedAt": response.get("lastReceivedAt"),
        "stale": response.get("stale", False),
    }
