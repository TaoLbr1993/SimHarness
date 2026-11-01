"""Compose one configured model with optional vehicle memory and driving skills."""

import logging

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models import DomainEvent
from trafficverse.domain.models.observation import LocalObservationFrame, VehicleObservation

from .config import Binding
from .context import AgentInput, MemoryRecord, SkillContent
from .memory import InMemoryMemoryStore, MemoryStore
from .models.base import ContextVehicleAgent, VehicleAgent
from .skills.provider import SkillProvider

logger = logging.getLogger(__name__)


class DrivingAgent:
    """Manage context for one binding while preserving the legacy model interface."""

    def __init__(
        self,
        binding: Binding,
        model: VehicleAgent,
        skill_provider: SkillProvider,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._binding = binding
        self._model = model
        self._skill_provider = skill_provider
        self._memory = (
            (
                memory_store
                if memory_store is not None
                else InMemoryMemoryStore(binding.memory.max_steps)
            )
            if binding.memory.enabled
            else None
        )

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        if self._memory is None and not self._binding.skills:
            return self._model.decide(observations)
        if not isinstance(self._model, ContextVehicleAgent):
            logger.warning("agent.context_unsupported agent=%s", self._binding.name)
            return self._model.decide(observations)
        skills: list[SkillContent] = []
        for name in self._binding.skills:
            try:
                skills.append(self._skill_provider.load(name))
            except Exception:
                logger.warning(
                    "agent.skill_load_failed agent=%s skill=%s", self._binding.name, name
                )
        inputs = []
        for observation in observations:
            memory = None
            if self._memory is not None:
                try:
                    memory = self._memory.read(
                        observation.ego.experiment_id, observation.ego.vehicle_id
                    )
                except Exception:
                    logger.warning("agent.memory_read_failed agent=%s", self._binding.name)
            inputs.append(AgentInput(observation=observation, memory=memory, skills=tuple(skills)))
        return self._model.decide_with_context(tuple(inputs))

    def record_transition(
        self,
        observations: tuple[VehicleObservation, ...],
        actions: tuple[VehicleControlRequest, ...],
        next_frame: LocalObservationFrame,
    ) -> None:
        if self._memory is None:
            return
        next_vehicles = {item.ego.vehicle_id: item.ego for item in next_frame.observations}
        actions_by_vehicle = {action.vehicle_id: action for action in actions}
        for observation in observations:
            vehicle_id = observation.ego.vehicle_id
            events = tuple(
                event for event in next_frame.events if _is_vehicle_event(event, vehicle_id)
            )
            record = MemoryRecord(
                observation=observation,
                requested_action=actions_by_vehicle.get(vehicle_id),
                next_state=next_vehicles.get(vehicle_id),
                next_sequence=next_frame.sequence,
                events=events,
            )
            try:
                self._memory.append(observation.ego.experiment_id, vehicle_id, record)
            except Exception:
                logger.warning("agent.memory_write_failed agent=%s", self._binding.name)
        try:
            self._memory.retain(next_frame.experiment_id, frozenset(next_vehicles))
        except Exception:
            logger.warning("agent.memory_cleanup_failed agent=%s", self._binding.name)


def _is_vehicle_event(event: DomainEvent, vehicle_id: str) -> bool:
    return isinstance(event.payload, dict) and event.payload.get("vehicle_id") == vehicle_id
