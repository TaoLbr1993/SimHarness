from uuid import UUID

import pytest

from trafficverse.agent_harness.config import Binding, HarnessConfig
from trafficverse.agent_harness.manager import AgentManager
from trafficverse.api.models import VehicleControlRequest
from trafficverse.application.vehicle_observation import local_observations
from trafficverse.domain.enums import AutomationLevel, VehicleAction
from trafficverse.domain.models import SimulationFrame, TrafficSnapshot, Vector3, VehicleState
from trafficverse.domain.models.observation import VehicleObservation


def _frame() -> SimulationFrame:
    experiment_id = UUID(int=1)
    vehicles = tuple(
        VehicleState(
            experiment_id=experiment_id,
            vehicle_id=vehicle_id,
            simulation_time_ms=1000,
            sequence=1,
            automation_level=level,
            position=Vector3(x=0, y=0),
            speed_mps=5,
            acceleration_mps2=0,
            heading_rad=0,
            lane_id="lane",
            controller_id="sumo",
            action=VehicleAction.KEEP_LANE,
            risk_score=0,
        )
        for vehicle_id, level in (("a", AutomationLevel.L5), ("b", AutomationLevel.L0))
    )
    return SimulationFrame(
        traffic=TrafficSnapshot(
            experiment_id=experiment_id, simulation_time_ms=1000, sequence=1, vehicles=vehicles
        )
    )


class Agent:
    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        return tuple(
            VehicleControlRequest(vehicle_id=v.ego.vehicle_id, desired_speed_mps=2)
            for v in observations
        )


def _config(bindings: tuple[Binding, ...]) -> HarnessConfig:
    return HarnessConfig(
        workspace_id=UUID(int=1), scenario_id=UUID(int=2), map_id="test", bindings=bindings
    )


def test_multiple_agents_receive_only_their_assigned_vehicles() -> None:
    config = _config(
        (
            Binding(name="first", wrapper="x:Y", automation_levels=("L5",)),
            Binding(name="second", wrapper="x:Y", vehicle_ids=("b",)),
        )
    )
    # actions_for does not access the client.
    manager = AgentManager(config, None, {"first": Agent(), "second": Agent()})  # type: ignore[arg-type]
    frame = local_observations(_frame(), radius_m=10, all_vehicles=True)
    assert [action.vehicle_id for action in manager.actions_for(frame)] == ["a", "b"]


def test_conflicting_assignments_are_rejected() -> None:
    config = _config(
        (
            Binding(name="first", wrapper="x:Y", automation_levels=("L5",)),
            Binding(name="second", wrapper="x:Y", vehicle_ids=("a",)),
        )
    )
    manager = AgentManager(config, None, {"first": Agent(), "second": Agent()})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="multiple agents"):
        manager.actions_for(local_observations(_frame(), radius_m=10, all_vehicles=True))


def test_agent_receives_only_assigned_ego_observations() -> None:
    class RecordingAgent(Agent):
        received: tuple[VehicleObservation, ...] = ()

        def decide(
            self, observations: tuple[VehicleObservation, ...]
        ) -> tuple[VehicleControlRequest, ...]:
            self.received = observations
            return super().decide(observations)

    agent = RecordingAgent()
    config = _config((Binding(name="first", wrapper="x:Y", vehicle_ids=("a",)),))
    manager = AgentManager(config, None, {"first": agent})  # type: ignore[arg-type]
    frame = local_observations(_frame(), radius_m=10, all_vehicles=True)
    manager.actions_for(frame)
    assert [item.ego.vehicle_id for item in agent.received] == ["a"]
    assert [item.vehicle.vehicle_id for item in agent.received[0].nearby_vehicles] == ["b"]
