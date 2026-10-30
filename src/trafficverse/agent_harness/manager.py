"""Coordinate vehicle assignment, agent decisions and synchronous SUMO steps."""

import logging
import sys
from collections.abc import Mapping
from uuid import UUID

from trafficverse.api.models import ExternalStepRequest, VehicleControlRequest
from trafficverse.domain.enums import ExperimentStatus
from trafficverse.domain.models import SimulationFrame, VehicleState

from .client import SimulationClient
from .config import HarnessConfig
from .models.base import VehicleAgent

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

    def actions_for(self, frame: SimulationFrame) -> tuple[VehicleControlRequest, ...]:
        assignments: dict[str, list[VehicleState]] = {
            binding.name: [] for binding in self._config.bindings
        }
        for vehicle in frame.traffic.vehicles:
            matching = [
                binding.name
                for binding in self._config.bindings
                if vehicle.vehicle_id in binding.vehicle_ids
                or vehicle.automation_level.value in binding.automation_levels
            ]
            if len(matching) > 1:
                raise ValueError(
                    f"vehicle {vehicle.vehicle_id} matches multiple agents: {matching}"
                )
            if matching:
                assignments[matching[0]].append(vehicle)

        actions: list[VehicleControlRequest] = []
        for binding in self._config.bindings:
            vehicles = tuple(assignments[binding.name])
            if not vehicles:
                continue
            allowed = {vehicle.vehicle_id for vehicle in vehicles}
            decisions = self._agents[binding.name].decide(frame.traffic, vehicles)
            for decision in decisions:
                action = VehicleControlRequest.model_validate(decision)
                if action.vehicle_id not in allowed:
                    raise ValueError(f"agent {binding.name} returned an unassigned vehicle")
                if action.vehicle_id in {item.vehicle_id for item in actions}:
                    raise ValueError(f"duplicate action for {action.vehicle_id}")
                actions.append(action)
        return ExternalStepRequest(
            expected_sequence=frame.traffic.sequence, actions=tuple(actions)
        ).actions

    def run(self) -> UUID:
        experiment_id = self._client.create(self._config)
        logger.info("Created experiment %s", experiment_id)
        try:
            self._client.prepare(experiment_id)
            self._client.start(experiment_id)
            frame = self._client.step(experiment_id, 0, ())
            logger.info("Step %s: %s vehicles", frame.traffic.sequence, len(frame.traffic.vehicles))
            for _ in range(1, self._config.max_steps):
                status = self._client.status(experiment_id)
                if status == ExperimentStatus.COMPLETED.value:
                    break
                if status != ExperimentStatus.RUNNING.value:
                    raise RuntimeError(f"experiment is not running: {status}")
                actions = self.actions_for(frame)
                frame = self._client.step(experiment_id, frame.traffic.sequence, actions)
                for event in frame.events:
                    if event.event_type == "vehicle.control.rejected":
                        logger.warning("Vehicle action rejected: %s", event.payload)
                logger.info("Step %s: %s actions", frame.traffic.sequence, len(actions))
            return experiment_id
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                self._client.stop(experiment_id)
            except Exception:
                logger.exception("Failed to stop experiment %s", experiment_id)
                if not active_error:
                    raise
