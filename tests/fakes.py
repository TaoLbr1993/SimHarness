"""Reusable in-memory Port implementations for unit and contract tests."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID

from trafficverse.config.models import CarlaConfig, SumoConfig, WeatherConfig
from trafficverse.domain.enums import ComponentStatus, ExperimentStatus
from trafficverse.domain.models import (
    ActorSpawnResult,
    CarlaFrame,
    CarlaTrafficLight,
    ComponentHealth,
    ControlCommand,
    DomainEvent,
    MetricSample,
    SimulationFrame,
    TrafficLightUpdate,
    TrafficSnapshot,
    WebSocketEnvelope,
)
from trafficverse.ports.simulation import ActorTransform, RenderVehicleSpec


class FakeTrafficEnginePort:
    def __init__(self, experiment_id: UUID) -> None:
        self.experiment_id = experiment_id
        self.started = False
        self.closed = False
        self.sequence = 0
        self.last_time_ms = -1
        self.controls: dict[str, ControlCommand] = {}

    def load(self, config: SumoConfig) -> None:
        del config
        self.started = True
        self.closed = False

    def apply_controls(self, commands: Mapping[str, ControlCommand]) -> None:
        self.controls = dict(commands)

    def apply_step_controls(self, commands: Mapping[str, ControlCommand]) -> tuple[str, ...]:
        self.apply_controls(commands)
        return ()

    def release_speed_controls(self, vehicle_ids: Sequence[str]) -> None:
        for vehicle_id in vehicle_ids:
            self.controls.pop(vehicle_id, None)

    def step(self, target_time_ms: int) -> TrafficSnapshot:
        if not self.started or self.closed:
            raise RuntimeError("Fake traffic engine is not running")
        if target_time_ms <= self.last_time_ms:
            raise ValueError("target simulation time must increase")
        self.last_time_ms = target_time_ms
        self.sequence += 1
        return TrafficSnapshot(
            experiment_id=self.experiment_id,
            simulation_time_ms=target_time_ms,
            sequence=self.sequence,
        )

    def health(self) -> ComponentHealth:
        status = (
            ComponentStatus.HEALTHY
            if self.started and not self.closed
            else ComponentStatus.UNAVAILABLE
        )
        return ComponentHealth(component="traffic-engine", status=status, version="fake")

    def close(self) -> None:
        self.closed = True


class FakeCarlaPort:
    def __init__(self, traffic_lights: tuple[CarlaTrafficLight, ...] = ()) -> None:
        self.connected = False
        self.closed = False
        self.frame = 0
        self.actor_ids: set[int] = set()
        self._next_actor_id = 1
        self._traffic_lights = traffic_lights
        self.traffic_light_updates: list[TrafficLightUpdate] = []

    def connect(self, config: CarlaConfig) -> None:
        del config
        self.connected = True
        self.closed = False

    def load_world(self, map_name: str, weather: WeatherConfig) -> None:
        del map_name, weather
        if not self.connected:
            raise RuntimeError("Fake CARLA is not connected")

    def spawn_vehicle(self, spec: RenderVehicleSpec) -> int:
        del spec
        actor_id = self._next_actor_id
        self._next_actor_id += 1
        self.actor_ids.add(actor_id)
        return actor_id

    def spawn_vehicles(self, specs: Sequence[RenderVehicleSpec]) -> tuple[ActorSpawnResult, ...]:
        return tuple(
            ActorSpawnResult(
                vehicle_id=spec.vehicle_id,
                success=True,
                actor_id=self.spawn_vehicle(spec),
            )
            for spec in specs
        )

    def update_actors(self, updates: Sequence[ActorTransform]) -> None:
        del updates

    def destroy_actors(self, actor_ids: Sequence[int]) -> None:
        self.actor_ids.difference_update(actor_ids)

    def existing_actor_ids(self, actor_ids: Sequence[int]) -> frozenset[int]:
        return frozenset(set(actor_ids) & self.actor_ids)

    def traffic_lights(self) -> tuple[CarlaTrafficLight, ...]:
        return self._traffic_lights

    def update_traffic_lights(self, updates: Sequence[TrafficLightUpdate]) -> None:
        self.traffic_light_updates = list(updates)

    def tick(self, target_time_ms: int) -> CarlaFrame:
        if not self.connected or self.closed:
            raise RuntimeError("Fake CARLA is not connected")
        self.frame += 1
        return CarlaFrame(
            simulation_time_ms=target_time_ms,
            carla_frame=self.frame,
            actor_count=len(self.actor_ids),
        )

    def health(self) -> ComponentHealth:
        status = (
            ComponentStatus.HEALTHY
            if self.connected and not self.closed
            else ComponentStatus.UNAVAILABLE
        )
        return ComponentHealth(component="carla", status=status, version="fake")

    def close(self) -> None:
        self.actor_ids.clear()
        self.closed = True


class FakeExperimentRepository:
    def __init__(self) -> None:
        self.statuses: dict[UUID, ExperimentStatus] = {}
        self.events: list[DomainEvent] = []
        self.metrics: list[MetricSample] = []

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
        self.statuses[experiment_id] = status

    async def append_event(self, event: DomainEvent) -> None:
        self.events.append(event)

    async def append_metric(self, metric: MetricSample) -> None:
        self.metrics.append(metric)


class FakeEventPublisher:
    def __init__(self) -> None:
        self.messages: list[WebSocketEnvelope] = []

    async def publish(self, message: WebSocketEnvelope) -> None:
        self.messages.append(message)


class FakeDataLogger:
    def __init__(self) -> None:
        self.frames: list[SimulationFrame] = []
        self.events: list[DomainEvent] = []
        self.flushed = False

    async def record_frame(self, frame: SimulationFrame) -> None:
        self.frames.append(frame)

    async def record_event(self, event: DomainEvent) -> None:
        self.events.append(event)

    async def flush(self) -> None:
        self.flushed = True


class FakeArtifactWriter:
    def __init__(self) -> None:
        self.payloads: dict[tuple[UUID, Path], bytes] = {}

    async def write_bytes(
        self,
        experiment_id: UUID,
        relative_path: Path,
        payload: bytes,
    ) -> str:
        self.payloads[(experiment_id, relative_path)] = payload
        return f"memory://{experiment_id}/{relative_path.as_posix()}"
