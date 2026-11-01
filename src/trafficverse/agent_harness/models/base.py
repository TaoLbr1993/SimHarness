"""Contract for model wrappers used by the external agent manager."""

from typing import Protocol, runtime_checkable

from trafficverse.agent_harness.context import AgentInput
from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models.observation import VehicleObservation


class VehicleAgent(Protocol):
    """Select actions for the assigned vehicles in the supplied SUMO snapshot."""

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]: ...


@runtime_checkable
class ContextVehicleAgent(Protocol):
    """Optional interface for models that consume vehicle memories and skills."""

    def decide_with_context(
        self, inputs: tuple[AgentInput, ...]
    ) -> tuple[VehicleControlRequest, ...]: ...
