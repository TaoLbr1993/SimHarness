from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from trafficverse.agent_harness.agent import DrivingAgent
from trafficverse.agent_harness.config import Binding, HarnessConfig, MemoryConfig, load_config
from trafficverse.agent_harness.context import AgentInput, MemoryRecord
from trafficverse.agent_harness.manager import AgentManager
from trafficverse.agent_harness.memory import InMemoryMemoryStore
from trafficverse.agent_harness.skills.provider import FileSkillProvider
from trafficverse.api.models import ExperimentView, ExternalLocalStepRequest, VehicleControlRequest
from trafficverse.domain.enums import (
    AutomationLevel,
    EventSeverity,
    ExperimentStatus,
    VehicleAction,
)
from trafficverse.domain.models import DomainEvent, Vector3, VehicleState
from trafficverse.domain.models.observation import LocalObservationFrame, VehicleObservation

EXPERIMENT = UUID(int=1)


def _observation(
    vehicle_id: str, sequence: int = 1, experiment: UUID = EXPERIMENT
) -> VehicleObservation:
    return VehicleObservation(
        ego=VehicleState(
            experiment_id=experiment,
            vehicle_id=vehicle_id,
            simulation_time_ms=sequence * 1000,
            sequence=sequence,
            automation_level=AutomationLevel.L0,
            position=Vector3(x=sequence, y=0),
            speed_mps=float(sequence),
            acceleration_mps2=0,
            heading_rad=0,
            lane_id="lane",
            controller_id="sumo",
            action=VehicleAction.KEEP_LANE,
            risk_score=0,
        ),
        radius_m=100,
    )


def _frame(sequence: int, *, events: tuple[DomainEvent, ...] = ()) -> LocalObservationFrame:
    return LocalObservationFrame(
        experiment_id=EXPERIMENT,
        simulation_time_ms=sequence * 1000,
        sequence=sequence,
        radius_m=100,
        observations=(_observation("a", sequence), _observation("b", sequence)),
        events=events,
    )


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentInput, ...]] = []

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        raise AssertionError("context-aware interface should be used")

    def decide_with_context(
        self, inputs: tuple[AgentInput, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        self.calls.append(inputs)
        return (
            VehicleControlRequest(
                vehicle_id=inputs[0].observation.ego.vehicle_id, desired_speed_mps=4
            ),
        )


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[ExternalLocalStepRequest] = []

    def create(self, config: HarnessConfig) -> UUID:
        raise AssertionError("desktop experiment is used")

    def prepare(self, experiment_id: UUID) -> None:
        raise AssertionError("desktop experiment is already prepared")

    def start(self, experiment_id: UUID) -> None:
        raise AssertionError("desktop experiment is already started")

    def stop(self, experiment_id: UUID) -> None:
        raise AssertionError("desktop experiment is not owned by the agent")

    def experiment(self, experiment_id: UUID) -> ExperimentView:
        return ExperimentView(
            experiment_id=experiment_id,
            workspace_id=UUID(int=2),
            status=ExperimentStatus.RUNNING,
            simulation_time_ms=0,
            speed_multiplier=1,
            external_stepping=True,
        )

    def status(self, experiment_id: UUID) -> str:
        return ExperimentStatus.RUNNING.value

    def step(self, experiment_id: UUID, request: ExternalLocalStepRequest) -> LocalObservationFrame:
        self.requests.append(request)
        sequence = request.expected_sequence + 1
        event = DomainEvent(
            event_id=UUID(int=3),
            experiment_id=EXPERIMENT,
            event_type="vehicle.control.rejected",
            severity=EventSeverity.WARNING,
            simulation_time_ms=2000,
            payload={"vehicle_id": "a", "reason": "TEST"},
        )
        return _frame(sequence, events=(event,) if sequence == 2 else ())


def test_manager_adds_per_vehicle_memory_after_real_next_frame(tmp_path: Path) -> None:
    (tmp_path / "style.md").write_text("Keep a cautious distance.", encoding="utf-8")
    config = HarnessConfig(
        experiment_id=EXPERIMENT,
        max_steps=3,
        skills_directory=tmp_path,
        bindings=(
            Binding(
                name="driver",
                wrapper="example:Agent",
                vehicle_ids=("a", "b"),
                memory=MemoryConfig(enabled=True, max_steps=2),
                skills=("style",),
            ),
        ),
    )
    model = RecordingModel()
    client = FakeClient()
    assert AgentManager(config, client, {"driver": model}).run() == EXPERIMENT
    assert len(model.calls) == 2
    assert [item.memory for item in model.calls[0]] == [(), ()]
    assert [item.skills[0].content for item in model.calls[0]] == [
        "Keep a cautious distance.",
        "Keep a cautious distance.",
    ]
    a, b = model.calls[1]
    assert a.memory is not None and b.memory is not None
    assert len(a.memory) == len(b.memory) == 1
    assert a.memory[0].observation.ego.vehicle_id == "a"
    assert a.memory[0].requested_action is not None
    assert a.memory[0].next_state is not None and a.memory[0].next_state.speed_mps == 2
    assert a.memory[0].events[0].event_type == "vehicle.control.rejected"
    assert b.memory[0].observation.ego.vehicle_id == "b"
    assert b.memory[0].requested_action is None and b.memory[0].events == ()
    assert [len(request.actions) for request in client.requests] == [0, 1, 1]


def test_memory_is_bounded_isolated_and_discards_departed_vehicle() -> None:
    store = InMemoryMemoryStore(max_steps=2)
    for sequence in (1, 2, 3):
        observation = _observation("a", sequence)
        store.append(
            EXPERIMENT, "a", MemoryRecord(observation=observation, next_sequence=sequence + 1)
        )
    other = UUID(int=2)
    store.append(
        other, "a", MemoryRecord(observation=_observation("a", experiment=other), next_sequence=2)
    )
    assert [r.observation.ego.sequence for r in store.read(EXPERIMENT, "a")] == [2, 3]
    assert len(store.read(other, "a")) == 1
    assert store.read(EXPERIMENT, "b") == ()
    store.retain(EXPERIMENT, frozenset())
    assert store.read(EXPERIMENT, "a") == ()
    assert len(store.read(other, "a")) == 1
    with pytest.raises(ValueError):
        store.append(
            EXPERIMENT, "wrong", MemoryRecord(observation=_observation("a"), next_sequence=2)
        )


def test_skill_and_memory_failures_do_not_block_decisions() -> None:
    class BrokenMemory:
        def read(self, experiment_id: UUID, vehicle_id: str) -> tuple[MemoryRecord, ...]:
            raise RuntimeError("offline")

        def append(self, experiment_id: UUID, vehicle_id: str, record: MemoryRecord) -> None:
            raise RuntimeError("offline")

        def retain(self, experiment_id: UUID, vehicle_ids: frozenset[str]) -> None:
            raise RuntimeError("offline")

    binding = Binding(
        name="driver",
        wrapper="example:Agent",
        vehicle_ids=("a",),
        memory=MemoryConfig(enabled=True),
        skills=("missing", "human_cautious"),
    )
    model = RecordingModel()
    agent = DrivingAgent(binding, model, FileSkillProvider(), BrokenMemory())
    result = agent.decide((_observation("a"),))
    assert len(result) == 1
    assert model.calls[0][0].memory is None
    assert [skill.name for skill in model.calls[0][0].skills] == ["human_cautious"]
    agent.record_transition((_observation("a"),), result, _frame(2))


def test_skill_paths_and_relative_configuration(tmp_path: Path) -> None:
    directory = tmp_path / "styles"
    directory.mkdir()
    (directory / "mine.md").write_text("My driving style", encoding="utf-8")
    config_file = tmp_path / "harness.yaml"
    config_file.write_text(
        'experiment_id: "00000000-0000-0000-0000-000000000001"\nskills_directory: styles\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.skills_directory == directory.resolve()
    provider = FileSkillProvider(config.skills_directory)
    assert provider.load("mine").content == "My driving style"
    with pytest.raises(ValueError):
        provider.load("../mine")
