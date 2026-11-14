"""Client vehicle authorization against the client_authorization DynamoDB table."""

from __future__ import annotations

from typing import Any, Protocol


class AuthorizationTablePort(Protocol):
    def get_client_item(self, client_id: str) -> dict[str, Any] | None: ...


def is_vehicle_authorized(vehicle_id: str, auth_item: dict[str, Any] | None) -> bool:
    """Return True when auth_item grants access to vehicle_id."""
    if auth_item is None:
        return False
    authorized = auth_item.get("authorizedVehicleIds") or set()
    return vehicle_id in authorized
