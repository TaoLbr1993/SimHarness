"""Placeholder for a Jev-type vehicle decision model."""

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import TrafficSnapshot, VehicleState


class JevModel:
    """Jev-type model adapter; inference is not implemented."""

    def decide(
        self, snapshot: TrafficSnapshot, vehicles: tuple[VehicleState, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        raise NotImplementedError("JevModel inference is not implemented")
