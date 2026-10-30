"""Placeholder for a remote API vehicle decision model."""

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import TrafficSnapshot, VehicleState


class ApiModel:
    """Remote API adapter; inference is not implemented."""

    def decide(
        self, snapshot: TrafficSnapshot, vehicles: tuple[VehicleState, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        raise NotImplementedError("ApiModel inference is not implemented")
