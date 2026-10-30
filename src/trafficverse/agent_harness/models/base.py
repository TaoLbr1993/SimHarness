"""Contract for model wrappers used by the external agent manager."""

from typing import Protocol

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import TrafficSnapshot, VehicleState


class VehicleAgent(Protocol):
    """Select actions for the assigned vehicles in the supplied SUMO snapshot."""

    def decide(
        self, snapshot: TrafficSnapshot, vehicles: tuple[VehicleState, ...]
    ) -> tuple[VehicleControlRequest, ...]: ...
