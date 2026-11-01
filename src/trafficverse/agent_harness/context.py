"""Typed vehicle memory and model context for the external agent harness."""

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import DomainEvent, StrictModel, VehicleState
from trafficverse.domain.models.observation import VehicleObservation


class MemoryRecord(StrictModel):
    """A decision and its observed next state; missing next state means unknown."""

    observation: VehicleObservation
    requested_action: VehicleControlRequest | None = None
    next_state: VehicleState | None = None
    next_sequence: int
    events: tuple[DomainEvent, ...] = ()


class SkillContent(StrictModel):
    name: str
    content: str


class AgentInput(StrictModel):
    observation: VehicleObservation
    memory: tuple[MemoryRecord, ...] | None = None
    skills: tuple[SkillContent, ...] = ()


def model_state(inputs: tuple[AgentInput, ...]) -> dict[str, object]:
    """Build a common vehicle-scoped payload for chat and decision models."""
    state: dict[str, object] = {
        "observations": [item.observation.model_dump(mode="json") for item in inputs]
    }
    if any(item.memory is not None or item.skills for item in inputs):
        state["vehicle_contexts"] = [
            {
                "vehicle_id": item.observation.ego.vehicle_id,
                "memory": (
                    [record.model_dump(mode="json") for record in item.memory]
                    if item.memory is not None
                    else None
                ),
                "skills": [skill.model_dump(mode="json") for skill in item.skills],
            }
            for item in inputs
        ]
    return state
