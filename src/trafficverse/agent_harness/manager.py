"""Coordinate vehicle assignment, agent decisions and synchronous SUMO steps."""

import logging
import sys
import time
from collections.abc import Callable, Mapping
from uuid import UUID

from trafficverse.api.models import (
    ExternalLocalStepRequest,
    ExternalStepRequest,
    VehicleControlRequest,
)
from trafficverse.domain.enums import AutomationLevel, ExperimentStatus
from trafficverse.domain.models.observation import LocalObservationFrame, VehicleObservation

from .agent import DrivingAgent
from .client import SimulationClient
from .config import HarnessConfig
from .models.base import VehicleAgent
from .skills.provider import FileSkillProvider
from .tracing import DecisionHistory, capture_decision

logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(
        self, config: HarnessConfig, client: SimulationClient, agents: Mapping[str, VehicleAgent]
    ) -> None:
        self._config = config
        self._client = client
        self._agents = agents
        if set(agents) != {binding.name for binding in config.bindings}:
            raise ValueError("agent instances must match the configured binding names")
        provider = FileSkillProvider(config.skills_directory)
        self._driving_agents = {
            binding.name: DrivingAgent(binding, agents[binding.name], provider)
            for binding in config.bindings
        }

    def _assign(self, frame: LocalObservationFrame) -> dict[str, list[VehicleObservation]]:
        assignments: dict[str, list[VehicleObservation]] = {
            binding.name: [] for binding in self._config.bindings
        }
        for observation in frame.observations:
            vehicle = observation.ego
            matching = [
                binding.name
                for binding in self._config.bindings
                if binding.all_vehicles
                or vehicle.vehicle_id in binding.vehicle_ids
                or vehicle.automation_level.value in binding.automation_levels
            ]
            if len(matching) > 1:
                raise ValueError(
                    f"vehicle {vehicle.vehicle_id} matches multiple agents: {matching}"
                )
            if matching:
                assignments[matching[0]].append(observation)
        return assignments

    def actions_for(
        self,
        frame: LocalObservationFrame,
        *,
        should_continue: Callable[[], bool] | None = None,
        history: DecisionHistory | None = None,
    ) -> tuple[VehicleControlRequest, ...]:
        assignments = self._assign(frame)
        actions: list[VehicleControlRequest] = []
        for binding in self._config.bindings:
            if should_continue is not None and not should_continue():
                return ()
            observations = tuple(assignments[binding.name])
            with capture_decision(history, binding, frame, observations) as capture:
                allowed = {item.ego.vehicle_id for item in observations}
                decisions = (
                    self._driving_agents[binding.name].decide(observations) if observations else ()
                )
                group_actions = []
                for decision in decisions:
                    action = VehicleControlRequest.model_validate(decision)
                    if action.vehicle_id not in allowed:
                        raise ValueError(f"agent {binding.name} returned an unassigned vehicle")
                    group_actions.append(action)
                validated = ExternalStepRequest(
                    expected_sequence=frame.sequence, actions=tuple(group_actions)
                ).actions
                actions.extend(validated)
                if capture:
                    capture.finish(validated)
        return ExternalStepRequest(expected_sequence=frame.sequence, actions=tuple(actions)).actions

    def _step_request(
        self, sequence: int, actions: tuple[VehicleControlRequest, ...]
    ) -> ExternalLocalStepRequest:
        return ExternalLocalStepRequest(
            expected_sequence=sequence,
            actions=actions,
            radius_m=self._config.observation_radius_m,
            all_vehicles=any(binding.all_vehicles for binding in self._config.bindings),
            vehicle_ids=tuple(
                sorted({v for binding in self._config.bindings for v in binding.vehicle_ids})
            ),
            automation_levels=tuple(
                AutomationLevel(level)
                for level in sorted(
                    {v for binding in self._config.bindings for v in binding.automation_levels}
                )
            ),
        )

    def record_transition(
        self,
        previous: LocalObservationFrame,
        actions: tuple[VehicleControlRequest, ...],
        frame: LocalObservationFrame,
    ) -> None:
        assignments = self._assign(previous)
        for binding in self._config.bindings:
            self._driving_agents[binding.name].record_transition(
                tuple(assignments[binding.name]), actions, frame
            )

    def run(self) -> UUID:
        owned = self._config.experiment_id is None
        experiment_id = self._client.create(self._config) if owned else self._config.experiment_id
        assert experiment_id is not None
        logger.info("Using experiment %s", experiment_id)
        try:
            if owned:
                self._client.prepare(experiment_id)
                self._client.start(experiment_id)
            else:
                experiment = self._client.experiment(experiment_id)
                if not experiment.external_stepping:
                    raise ValueError("experiment is not configured for external stepping")
                if experiment.status is not ExperimentStatus.RUNNING:
                    raise ValueError("desktop experiment must be running before the agent connects")
                if experiment.latest_sequence != 0:
                    raise ValueError("only a fresh, unstepped experiment can be attached")
            frame = self._client.step(experiment_id, self._step_request(0, ()))
            logger.info("Step %s: %s observations", frame.sequence, len(frame.observations))
            for _ in range(1, self._config.max_steps):
                status = self._client.status(experiment_id)
                if status == ExperimentStatus.COMPLETED.value:
                    break
                if status != ExperimentStatus.RUNNING.value:
                    raise RuntimeError(f"experiment is not running: {status}")
                if self._config.step_delay_s:
                    time.sleep(self._config.step_delay_s)
                actions = self.actions_for(frame)
                previous = frame
                frame = self._client.step(
                    experiment_id, self._step_request(frame.sequence, actions)
                )
                self.record_transition(previous, actions, frame)
                for event in frame.events:
                    if event.event_type == "vehicle.control.rejected":
                        logger.warning("Vehicle action rejected: %s", event.payload)
                logger.info("Step %s: %s actions", frame.sequence, len(actions))
            return experiment_id
        finally:
            active_error = sys.exc_info()[0] is not None
            if owned:
                try:
                    self._client.stop(experiment_id)
                except Exception:
                    logger.exception("Failed to stop experiment %s", experiment_id)
                    if not active_error:
                        raise
