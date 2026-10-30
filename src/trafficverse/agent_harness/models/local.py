"""Placeholder for a locally deployed vehicle decision model."""

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import TrafficSnapshot, VehicleState


class LocalModel:
    """Local deployment adapter; inference is not implemented."""

    def decide(
        self, snapshot: TrafficSnapshot, vehicles: tuple[VehicleState, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        raise NotImplementedError("LocalModel inference is not implemented")
