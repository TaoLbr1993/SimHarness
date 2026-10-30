from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from trafficverse.application.clock import SimulationClock
from trafficverse.application.experiment_registry import ExperimentRegistry
from trafficverse.application.simulation_manager import SimulationManager
from trafficverse.config.loader import load_scenario
from trafficverse.config.models import CarlaConfig, SumoConfig, WeatherConfig
from trafficverse.domain.enums import (
    AutomationLevel,
    ComponentStatus,
    ErrorCode,
    EventSeverity,
    ExperimentStatus,
    RequirementMode,
    TrafficLightColor,
    VehicleAction,
)
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import (
    ActorSpawnResult,
    CarlaFrame,
    CarlaTrafficLight,
    ComponentHealth,
    ControlCommand,
    DomainEvent,
    MetricSample,
    SimulationFrame,
    TrafficLightState,
    TrafficLightUpdate,
    TrafficSnapshot,
    Vector3,
    VehicleState,
)
from trafficverse.ports.simulation import ActorTransform, RenderVehicleSpec
from trafficverse.roi.models import RoiApplyPlan, RoiApplyResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_PATH = REPOSITORY_ROOT / "configs/scenarios/core-run-town04.yaml"


class FailureInjector:
    def __init__(self) -> None:
        self.stage: str | None = None

    def check(self, stage: str) -> None:
        if self.stage == stage:
            raise RuntimeError(f"injected failure at {stage}")


class TraceTraffic:
    def __init__(self, experiment_id: UUID, trace: list[str], failures: FailureInjector) -> None:
        self.experiment_id = experiment_id
        self.trace = trace
        self.failures = failures
        self.opened = False
        self.closed = False
        self.sequence = 0
        self.applied_commands: list[dict[str, ControlCommand]] = []
        self.released: list[tuple[str, ...]] = []

    def load(self, config: SumoConfig) -> None:
        del config
        self.trace.append("traffic.load")
        self.failures.check("traffic.load")
        self.opened = True

    def apply_controls(self, commands: Mapping[str, ControlCommand]) -> None:
        self.trace.append(f"traffic.apply:{len(commands)}")
        self.failures.check("traffic.apply")
        self.applied_commands.append(dict(commands))

    def apply_step_controls(self, commands: Mapping[str, ControlCommand]) -> tuple[str, ...]:
        self.apply_controls(commands)
        return ()

    def release_speed_controls(self, vehicle_ids: Sequence[str]) -> None:
        self.released.append(tuple(vehicle_ids))

    def step(self, target_time_ms: int) -> TrafficSnapshot:
        self.trace.append(f"traffic.step:{target_time_ms}")
        self.failures.check("traffic.step")
        self.sequence += 1
        return TrafficSnapshot(
            experiment_id=self.experiment_id,
            simulation_time_ms=target_time_ms,
            sequence=self.sequence,
            vehicles=(
                VehicleState(
                    experiment_id=self.experiment_id,
                    vehicle_id="vehicle-1",
                    simulation_time_ms=target_time_ms,
                    sequence=self.sequence,
                    automation_level=AutomationLevel.HUMAN,
                    position=Vector3(x=float(self.sequence), y=0.0),
                    speed_mps=5.0,
                    acceleration_mps2=0.0,
                    heading_rad=0.0,
                    lane_id="lane-1",
                    controller_id="fixture",
                    action=VehicleAction.KEEP_LANE,
                    risk_score=0.0,
                ),
            ),
            traffic_lights=(
                TrafficLightState(
                    signal_id="signal-1",
                    simulation_time_ms=target_time_ms,
                    phase="RED",
                ),
            ),
        )

    def health(self) -> ComponentHealth:
        self.trace.append("traffic.health")
        self.failures.check("traffic.health")
        return ComponentHealth(
            component="traffic-engine",
            status=ComponentStatus.HEALTHY,
            version="fake",
        )

    def close(self) -> None:
        self.trace.append("traffic.close")
        self.closed = True
        self.failures.check("traffic.close")


class TraceCarla:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures
        self.closed = False
        self.frame = 0

    def connect(self, config: CarlaConfig) -> None:
        del config
        self.trace.append("carla.connect")
        self.failures.check("carla.connect")

    def load_world(self, map_name: str, weather: WeatherConfig) -> None:
        del map_name, weather
        self.trace.append("carla.load_world")
        self.failures.check("carla.load_world")

    def spawn_vehicle(self, spec: RenderVehicleSpec) -> int:
        del spec
        return 1

    def spawn_vehicles(self, specs: Sequence[RenderVehicleSpec]) -> tuple[ActorSpawnResult, ...]:
        self.trace.append(f"carla.spawn:{len(specs)}")
        self.failures.check("carla.spawn")
        return tuple(
            ActorSpawnResult(vehicle_id=spec.vehicle_id, success=True, actor_id=index + 1)
            for index, spec in enumerate(specs)
        )

    def update_actors(self, updates: Sequence[ActorTransform]) -> None:
        self.trace.append(f"carla.update:{len(updates)}")
        self.failures.check("carla.update")

    def destroy_actors(self, actor_ids: Sequence[int]) -> None:
        self.trace.append(f"carla.destroy:{len(actor_ids)}")
        self.failures.check("carla.destroy")

    def existing_actor_ids(self, actor_ids: Sequence[int]) -> frozenset[int]:
        return frozenset(actor_ids)

    def traffic_lights(self) -> tuple[CarlaTrafficLight, ...]:
        return ()

    def update_traffic_lights(self, updates: Sequence[TrafficLightUpdate]) -> None:
        self.trace.append(f"carla.signals:{len(updates)}")
        self.failures.check("carla.signals")

    def tick(self, target_time_ms: int) -> CarlaFrame:
        self.trace.append(f"carla.tick:{target_time_ms}")
        self.failures.check("carla.tick")
        self.frame += 1
        return CarlaFrame(
            simulation_time_ms=target_time_ms,
            carla_frame=self.frame,
            actor_count=0,
        )

    def health(self) -> ComponentHealth:
        self.trace.append("carla.health")
        self.failures.check("carla.health")
        return ComponentHealth(component="carla", status=ComponentStatus.HEALTHY, version="fake")

    def close(self) -> None:
        self.trace.append("carla.close")
        self.closed = True
        self.failures.check("carla.close")


class TraceRepository:
    def __init__(self, experiment_id: UUID, trace: list[str]) -> None:
        self.statuses = {experiment_id: ExperimentStatus.CREATED}
        self.transitions: list[ExperimentStatus] = []
        self.events: list[DomainEvent] = []
        self.metrics: list[MetricSample] = []
        self.trace = trace

    async def get_status(self, experiment_id: UUID) -> ExperimentStatus:
        return self.statuses[experiment_id]

    async def set_status(
        self,
        experiment_id: UUID,
        status: ExperimentStatus,
        *,
        reason: str | None = None,
    ) -> None:
        del reason
        self.trace.append(f"status:{status.value}")
        self.statuses[experiment_id] = status
        self.transitions.append(status)

    async def append_event(self, event: DomainEvent) -> None:
        self.trace.append("repository.event")
        self.events.append(event)

    async def append_metric(self, metric: MetricSample) -> None:
        self.metrics.append(metric)


class TraceController:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures
        self.previous_sequences: list[int | None] = []

    def step(self, previous: TrafficSnapshot | None, dt_s: float) -> Mapping[str, ControlCommand]:
        assert dt_s == 0.05
        sequence = previous.sequence if previous is not None else None
        self.previous_sequences.append(sequence)
        self.trace.append(f"controller:{sequence}")
        self.failures.check("controller")
        return {"vehicle-1": ControlCommand(desired_speed_mps=5.0)}


class TraceRoiPlanner:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures

    def plan(self, snapshot: TrafficSnapshot) -> RoiApplyPlan:
        self.trace.append(f"roi:{snapshot.sequence}")
        self.failures.check("roi")
        return RoiApplyPlan()

    def actor_ids(self) -> frozenset[int]:
        return frozenset()

    def report_missing_actor_ids(self, actor_ids: frozenset[int]) -> None:
        del actor_ids

    def commit(self, result: RoiApplyResult) -> None:
        del result


class TraceSignalPlanner:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures

    def initialize(self, traffic_lights: tuple[CarlaTrafficLight, ...]) -> None:
        del traffic_lights

    def plan(self, traffic_lights: tuple[TrafficLightState, ...]) -> tuple[TrafficLightUpdate, ...]:
        self.trace.append(f"signal:{len(traffic_lights)}")
        self.failures.check("signal")
        return (
            TrafficLightUpdate(
                carla_actor_id=99,
                color=TrafficLightColor.RED,
            ),
        )


class TraceLogger:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures
        self.frames: list[SimulationFrame] = []
        self.events: list[DomainEvent] = []

    async def record_frame(self, frame: SimulationFrame) -> None:
        self.trace.append("logger.frame")
        self.failures.check("logger.frame")
        self.frames.append(frame)

    async def record_event(self, event: DomainEvent) -> None:
        self.trace.append("logger.event")
        self.failures.check("logger.event")
        self.events.append(event)

    async def flush(self) -> None:
        self.trace.append("logger.flush")
        self.failures.check("logger.flush")


class TracePublisher:
    def __init__(self, trace: list[str], failures: FailureInjector) -> None:
        self.trace = trace
        self.failures = failures
        self.frames: list[SimulationFrame] = []

    async def publish_frame(self, frame: SimulationFrame) -> None:
        self.trace.append("publisher.frame")
        self.failures.check("publisher.frame")
        self.frames.append(frame)


class Harness:
    def __init__(
        self,
        *,
        carla_mode: RequirementMode = RequirementMode.REQUIRED,
        registry: ExperimentRegistry | None = None,
        start_time_ms: int = 0,
        duration_ms: int = 1000,
    ) -> None:
        self.experiment_id = uuid4()
        self.trace: list[str] = []
        self.failures = FailureInjector()
        self.traffic = TraceTraffic(self.experiment_id, self.trace, self.failures)
        self.carla = TraceCarla(self.trace, self.failures)
        self.repository = TraceRepository(self.experiment_id, self.trace)
        self.controller = TraceController(self.trace, self.failures)
        self.logger = TraceLogger(self.trace, self.failures)
        self.publisher = TracePublisher(self.trace, self.failures)
        scenario = load_scenario(SCENARIO_PATH, apply_environment=False)
        scenario = scenario.model_copy(
            update={
                "carla": scenario.carla.model_copy(update={"mode": carla_mode}),
                "simulation": scenario.simulation.model_copy(
                    update={"start_time_ms": start_time_ms, "duration_ms": duration_ms}
                ),
                "sumo": scenario.sumo.model_copy(update={"begin_time_ms": start_time_ms}),
            }
        )
        self.manager = SimulationManager(
            scenario=scenario,
            carla_map_name="Town04",
            traffic=self.traffic,
            carla=self.carla,
            experiments=self.repository,
            data_logger=self.logger,
            controller=self.controller,
            roi_planner=TraceRoiPlanner(self.trace, self.failures),
            signal_planner=TraceSignalPlanner(self.trace, self.failures),
            frame_publisher=self.publisher,
            registry=registry,
            clock=SimulationClock(50, initial_time_ms=start_time_ms),
        )

    async def ready_and_started(self) -> None:
        await self.manager.prepare(self.experiment_id)
        await self.manager.start()


def test_external_steps_wait_for_actions_release_speed_and_reject_stale_frames() -> None:
    async def exercise() -> None:
        harness = Harness(carla_mode=RequirementMode.DISABLED)
        harness.manager.enable_external_stepping()
        await harness.ready_and_started()
        assert harness.manager.simulation_time_ms == 0
        first = await harness.manager.step_external(0, {})
        assert first.traffic.sequence == 1
        second = await harness.manager.step_external(
            1, {"vehicle-1": ControlCommand(desired_speed_mps=8.0)}
        )
        assert second.traffic.sequence == 2
        with pytest.raises(TrafficVerseError):
            await harness.manager.step_external(1, {})
        assert harness.manager.simulation_time_ms == 100
        third = await harness.manager.step_external(2, {})
        assert third.traffic.sequence == 3
        assert harness.traffic.released[-1] == ("vehicle-1",)
        assert harness.traffic.applied_commands == [
            {},
            {"vehicle-1": ControlCommand(desired_speed_mps=8.0, lane_change_duration_s=0.05)},
            {},
        ]
        assert not any(item.startswith("controller:") for item in harness.trace)
        await harness.manager.stop()

    asyncio.run(exercise())


def test_complete_lifecycle_tick_order_pause_and_speed() -> None:
    async def exercise() -> None:
        harness = Harness()
        await harness.ready_and_started()
        harness.trace.clear()

        first = await harness.manager.run_tick()
        assert harness.trace == [
            "controller:None",
            "traffic.apply:1",
            "traffic.step:50",
            "roi:1",
            "signal:1",
            "carla.update:0",
            "carla.signals:1",
            "carla.tick:50",
            "logger.frame",
            "publisher.frame",
        ]
        assert first.carla is not None

        await harness.manager.pause()
        paused = await harness.manager.run_tick()
        assert paused is first
        assert harness.manager.simulation_time_ms == 50
        await harness.manager.set_speed(2.0)
        assert harness.manager.speed_multiplier == 2.0
        await harness.manager.resume()
        second = await harness.manager.run_tick()
        assert second.traffic.simulation_time_ms == 100
        assert harness.controller.previous_sequences == [None, 1]
        await harness.manager.stop("TEST_COMPLETE")

        assert harness.repository.transitions == [
            ExperimentStatus.PREPARING,
            ExperimentStatus.READY,
            ExperimentStatus.RUNNING,
            ExperimentStatus.PAUSED,
            ExperimentStatus.RUNNING,
            ExperimentStatus.STOPPING,
            ExperimentStatus.COMPLETED,
        ]
        assert harness.traffic.closed
        assert harness.carla.closed

    asyncio.run(exercise())


def test_nonzero_sumo_begin_time_keeps_duration_relative_to_start() -> None:
    async def exercise() -> None:
        harness = Harness(
            carla_mode=RequirementMode.DISABLED,
            start_time_ms=500,
            duration_ms=100,
        )
        await harness.ready_and_started()

        first = await harness.manager.run_tick()
        second = await harness.manager.run_tick()

        assert first.traffic.simulation_time_ms == 550
        assert second.traffic.simulation_time_ms == 600
        assert await harness.manager.get_status() is ExperimentStatus.COMPLETED

    asyncio.run(exercise())


def test_api_vehicle_control_is_validated_and_applied_on_next_tick() -> None:
    async def exercise() -> None:
        harness = Harness()
        await harness.ready_and_started()
        await harness.manager.run_tick()

        with pytest.raises(TrafficVerseError) as missing:
            await harness.manager.control_vehicle(
                "missing-vehicle", ControlCommand(desired_speed_mps=2.0)
            )
        assert missing.value.code is ErrorCode.RESOURCE_NOT_FOUND

        await harness.manager.control_vehicle("vehicle-1", ControlCommand(desired_speed_mps=8.0))
        await harness.manager.run_tick()

        assert harness.traffic.applied_commands[-1]["vehicle-1"].desired_speed_mps == 8.0

    asyncio.run(exercise())


def test_repeated_lifecycle_commands_are_idempotent() -> None:
    async def exercise() -> None:
        harness = Harness()
        await harness.manager.prepare(harness.experiment_id)
        await harness.manager.prepare(harness.experiment_id)
        await harness.manager.start()
        await harness.manager.start()
        await harness.manager.pause()
        await harness.manager.pause()
        with pytest.raises(TrafficVerseError) as wrong_command:
            await harness.manager.start()
        assert wrong_command.value.code is ErrorCode.INVALID_STATE_TRANSITION
        await harness.manager.resume()
        await harness.manager.resume()
        await harness.manager.stop()
        await harness.manager.stop()

        assert harness.trace.count("traffic.load") == 1
        assert harness.trace.count("carla.connect") == 1
        assert harness.trace.count("carla.close") == 1
        assert harness.trace.count("traffic.close") == 1
        assert harness.trace.count("logger.flush") == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "stage",
    [
        "traffic.load",
        "traffic.health",
        "carla.connect",
        "carla.load_world",
        "carla.health",
    ],
)
def test_initialization_failure_enters_failed_and_cleans_reverse_order(
    stage: str,
) -> None:
    async def exercise() -> None:
        harness = Harness()
        harness.failures.stage = stage

        with pytest.raises(RuntimeError, match="injected failure"):
            await harness.manager.prepare(harness.experiment_id)

        assert harness.repository.statuses[harness.experiment_id] is ExperimentStatus.FAILED
        assert harness.traffic.closed
        if stage.startswith("carla"):
            assert harness.carla.closed
            assert harness.trace.index("carla.close") < harness.trace.index("traffic.close")

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "stage",
    [
        "controller",
        "traffic.apply",
        "traffic.step",
        "roi",
        "signal",
        "carla.update",
        "carla.signals",
        "carla.tick",
        "logger.frame",
        "publisher.frame",
    ],
)
def test_tick_failure_enters_failed_and_cleans_all_components(stage: str) -> None:
    async def exercise() -> None:
        harness = Harness()
        await harness.ready_and_started()
        harness.failures.stage = stage

        with pytest.raises(RuntimeError, match="injected failure"):
            await harness.manager.run_tick()

        assert harness.repository.statuses[harness.experiment_id] is ExperimentStatus.FAILED
        assert harness.traffic.closed
        assert harness.carla.closed
        assert harness.trace.index("carla.close") < harness.trace.index("traffic.close")

    asyncio.run(exercise())


def test_optional_carla_failure_degrades_to_two_dimensional_run() -> None:
    async def exercise() -> None:
        harness = Harness(carla_mode=RequirementMode.OPTIONAL)
        await harness.ready_and_started()
        harness.failures.stage = "carla.tick"

        frame = await harness.manager.run_tick()

        assert frame.carla is None
        assert harness.manager.carla_degraded
        assert harness.repository.statuses[harness.experiment_id] is ExperimentStatus.RUNNING
        assert harness.repository.events[0].event_type == "carla.degraded"
        assert harness.repository.events[0].severity is EventSeverity.WARNING
        await harness.manager.stop()

    asyncio.run(exercise())


def test_registry_enforces_explicit_single_running_limit() -> None:
    async def exercise() -> None:
        registry = ExperimentRegistry(maximum_running=1)
        first = Harness(registry=registry)
        second = Harness(registry=registry)
        await first.manager.prepare(first.experiment_id)
        await second.manager.prepare(second.experiment_id)
        await first.manager.start()

        with pytest.raises(TrafficVerseError) as conflict:
            await second.manager.start()
        assert conflict.value.code is ErrorCode.RESOURCE_CONFLICT

        await first.manager.pause()
        await second.manager.start()
        await first.manager.stop()
        await second.manager.stop()
        with pytest.raises(TrafficVerseError) as missing:
            await registry.get(first.experiment_id)
        assert missing.value.code is ErrorCode.RESOURCE_NOT_FOUND

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["logger.flush", "carla.close"])
def test_cleanup_failure_continues_reverse_cleanup_and_can_retry(stage: str) -> None:
    async def exercise() -> None:
        harness = Harness()
        await harness.ready_and_started()
        harness.failures.stage = stage

        with pytest.raises(TrafficVerseError) as failed:
            await harness.manager.stop()
        assert failed.value.code is ErrorCode.COMPONENT_UNAVAILABLE
        assert harness.repository.statuses[harness.experiment_id] is ExperimentStatus.FAILED
        assert "traffic.close" in harness.trace

        harness.failures.stage = None
        await harness.manager.stop()
        assert harness.traffic.closed
        assert harness.carla.closed

    asyncio.run(exercise())
