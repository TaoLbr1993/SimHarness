"""Single-clock experiment lifecycle and tick orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

from trafficverse.application.clock import SimulationClock
from trafficverse.application.experiment_registry import ExperimentRegistry
from trafficverse.config.models import ScenarioConfig
from trafficverse.domain.enums import (
    ComponentStatus,
    ErrorCode,
    EventSeverity,
    ExperimentStatus,
    RequirementMode,
)
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import (
    ActorSpawnResult,
    CarlaTrafficLight,
    ControlCommand,
    DomainEvent,
    SimulationFrame,
    TrafficLightState,
    TrafficLightUpdate,
    TrafficSnapshot,
)
from trafficverse.domain.state_machine import require_transition
from trafficverse.ports import (
    CarlaPort,
    DataLoggerPort,
    ExperimentRepositoryPort,
    TrafficEnginePort,
)
from trafficverse.roi.models import RoiApplyPlan, RoiApplyResult


class ControllerStepPort(Protocol):
    def step(
        self, previous: TrafficSnapshot | None, dt_s: float
    ) -> Mapping[str, ControlCommand]: ...


class RoiPlannerPort(Protocol):
    def actor_ids(self) -> frozenset[int]: ...

    def report_missing_actor_ids(self, actor_ids: frozenset[int]) -> None: ...

    def plan(self, snapshot: TrafficSnapshot) -> RoiApplyPlan: ...

    def commit(self, result: RoiApplyResult) -> None: ...


class SignalPlannerPort(Protocol):
    def initialize(self, traffic_lights: tuple[CarlaTrafficLight, ...]) -> None: ...

    def plan(
        self, traffic_lights: tuple[TrafficLightState, ...]
    ) -> tuple[TrafficLightUpdate, ...]: ...


class SimulationFramePublisherPort(Protocol):
    async def publish_frame(self, frame: SimulationFrame) -> None: ...


class NoOpController:
    def step(self, previous: TrafficSnapshot | None, dt_s: float) -> Mapping[str, ControlCommand]:
        del previous, dt_s
        return {}


class NoOpRoiPlanner:
    def actor_ids(self) -> frozenset[int]:
        return frozenset()

    def report_missing_actor_ids(self, actor_ids: frozenset[int]) -> None:
        del actor_ids

    def plan(self, snapshot: TrafficSnapshot) -> RoiApplyPlan:
        del snapshot
        return RoiApplyPlan()

    def commit(self, result: RoiApplyResult) -> None:
        del result


class NoOpSignalPlanner:
    def initialize(self, traffic_lights: tuple[CarlaTrafficLight, ...]) -> None:
        del traffic_lights

    def plan(self, traffic_lights: tuple[TrafficLightState, ...]) -> tuple[TrafficLightUpdate, ...]:
        del traffic_lights
        return ()


class NoOpFramePublisher:
    async def publish_frame(self, frame: SimulationFrame) -> None:
        del frame


class SimulationManager:
    """Owns all Traffic Engine steps and CARLA world ticks for one experiment."""

    def __init__(
        self,
        *,
        scenario: ScenarioConfig,
        carla_map_name: str,
        traffic: TrafficEnginePort,
        carla: CarlaPort,
        experiments: ExperimentRepositoryPort,
        data_logger: DataLoggerPort,
        controller: ControllerStepPort | None = None,
        roi_planner: RoiPlannerPort | None = None,
        signal_planner: SignalPlannerPort | None = None,
        frame_publisher: SimulationFramePublisherPort | None = None,
        registry: ExperimentRegistry | None = None,
        clock: SimulationClock | None = None,
    ) -> None:
        self._scenario = scenario
        self._carla_map_name = carla_map_name
        self._traffic = traffic
        self._carla = carla
        self._experiments = experiments
        self._data_logger = data_logger
        self._controller = controller or NoOpController()
        self._roi_planner = roi_planner or NoOpRoiPlanner()
        self._signal_planner = signal_planner or NoOpSignalPlanner()
        self._frame_publisher = frame_publisher or NoOpFramePublisher()
        self._registry = registry
        self._clock = clock or SimulationClock(
            scenario.simulation.step_ms,
            initial_time_ms=scenario.simulation.start_time_ms,
            speed_multiplier=scenario.simulation.speed_multiplier,
        )
        if self._clock.step_ms != scenario.simulation.step_ms:
            raise ValueError("clock step must match scenario step_ms")
        self._experiment_id: UUID | None = None
        self._previous_snapshot: TrafficSnapshot | None = None
        self._last_frame: SimulationFrame | None = None
        self._traffic_opened = False
        self._carla_opened = False
        self._carla_degraded = scenario.carla.mode is RequirementMode.DISABLED
        self._cleanup_done = False
        self._logger_flushed = False
        self._deferred_roi_vehicle_ids: frozenset[str] = frozenset()
        self._pending_api_controls: dict[str, ControlCommand] = {}
        self._external_stepping = False
        self._external_speed_vehicle_ids: set[str] = set()
        self._command_lock = asyncio.Lock()

    @property
    def experiment_id(self) -> UUID | None:
        return self._experiment_id

    @property
    def simulation_time_ms(self) -> int:
        return self._clock.current_time_ms

    @property
    def step_ms(self) -> int:
        return self._clock.step_ms

    @property
    def speed_multiplier(self) -> float:
        return self._clock.speed_multiplier

    @property
    def carla_degraded(self) -> bool:
        return self._carla_degraded

    @property
    def last_frame(self) -> SimulationFrame | None:
        return self._last_frame

    @property
    def external_stepping(self) -> bool:
        return self._external_stepping

    def enable_external_stepping(self) -> None:
        """Select request-driven stepping before preparing the experiment."""
        if self._experiment_id is not None:
            raise ValueError("external stepping must be configured before prepare")
        if self._scenario.sumo.freeze_collisions:
            self._scenario = self._scenario.model_copy(
                update={"sumo": self._scenario.sumo.model_copy(update={"freeze_collisions": False})}
            )
        self._external_stepping = True

    async def prepare(self, experiment_id: UUID) -> None:
        async with self._command_lock:
            if self._experiment_id is not None:
                if self._experiment_id == experiment_id:
                    status = await self._status()
                    if status in {
                        ExperimentStatus.PREPARING,
                        ExperimentStatus.READY,
                        ExperimentStatus.RUNNING,
                        ExperimentStatus.PAUSED,
                    }:
                        return
                raise TrafficVerseError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "simulation manager is already assigned to an experiment",
                )
            self._experiment_id = experiment_id
            if self._registry is not None:
                await self._registry.register(experiment_id, self)
            try:
                await self._transition(ExperimentStatus.PREPARING)
                self._traffic_opened = True
                self._traffic.load(self._scenario.sumo)
                if self._traffic.health().status is not ComponentStatus.HEALTHY:
                    raise TrafficVerseError(
                        ErrorCode.COMPONENT_UNAVAILABLE,
                        "SUMO is not healthy after initialization",
                    )
                if self._scenario.carla.mode is not RequirementMode.DISABLED:
                    await self._prepare_carla()
                await self._transition(ExperimentStatus.READY)
            except Exception as error:
                await self._fail(error)
                raise

    async def _prepare_carla(self) -> None:
        self._carla_opened = True
        try:
            self._carla.connect(self._scenario.carla)
            self._carla.load_world(self._carla_map_name, self._scenario.weather)
            self._signal_planner.initialize(self._carla.traffic_lights())
            if self._carla.health().status is not ComponentStatus.HEALTHY:
                raise TrafficVerseError(
                    ErrorCode.COMPONENT_UNAVAILABLE,
                    "CARLA is not healthy after initialization",
                )
        except Exception as error:
            if self._scenario.carla.mode is RequirementMode.REQUIRED:
                raise
            await self._degrade_carla(error)

    async def start(self) -> None:
        async with self._command_lock:
            status = await self._status()
            if status is ExperimentStatus.RUNNING:
                return
            if status is not ExperimentStatus.READY:
                raise TrafficVerseError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"start requires READY status, found {status.value}",
                )
            experiment_id = self._require_experiment_id()
            if self._registry is not None:
                await self._registry.acquire_running(experiment_id)
            try:
                await self._transition(ExperimentStatus.RUNNING)
            except Exception:
                if self._registry is not None:
                    await self._registry.release_running(experiment_id)
                raise

    async def pause(self) -> None:
        async with self._command_lock:
            status = await self._status()
            if status is ExperimentStatus.PAUSED:
                return
            await self._transition(ExperimentStatus.PAUSED)
            if self._registry is not None:
                await self._registry.release_running(self._require_experiment_id())

    async def resume(self) -> None:
        async with self._command_lock:
            status = await self._status()
            if status is ExperimentStatus.RUNNING:
                return
            if status is not ExperimentStatus.PAUSED:
                raise TrafficVerseError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"resume requires PAUSED status, found {status.value}",
                )
            experiment_id = self._require_experiment_id()
            if self._registry is not None:
                await self._registry.acquire_running(experiment_id)
            try:
                await self._transition(ExperimentStatus.RUNNING)
            except Exception:
                if self._registry is not None:
                    await self._registry.release_running(experiment_id)
                raise

    async def stop(self, reason: str = "USER_REQUEST") -> None:
        async with self._command_lock:
            status = await self._status()
            if status is ExperimentStatus.COMPLETED:
                return
            if status is ExperimentStatus.FAILED:
                errors = await self._cleanup()
                if errors:
                    raise TrafficVerseError(
                        ErrorCode.COMPONENT_UNAVAILABLE,
                        "component cleanup is still failing",
                        details={"errors": "; ".join(str(item) for item in errors)},
                    )
                return
            if status is not ExperimentStatus.STOPPING:
                await self._transition(ExperimentStatus.STOPPING, reason=reason)
            errors = await self._cleanup()
            if errors:
                error = TrafficVerseError(
                    ErrorCode.COMPONENT_UNAVAILABLE,
                    "one or more components failed during cleanup",
                    details={"errors": "; ".join(str(item) for item in errors)},
                )
                await self._transition(ExperimentStatus.FAILED, reason=str(error))
                raise error
            await self._transition(ExperimentStatus.COMPLETED, reason=reason)

    async def set_speed(self, multiplier: float) -> None:
        async with self._command_lock:
            self._clock.set_speed(multiplier)

    async def get_status(self) -> ExperimentStatus:
        async with self._command_lock:
            if self._experiment_id is None:
                return ExperimentStatus.CREATED
            return await self._status()

    async def control_vehicle(self, vehicle_id: str, command: ControlCommand) -> None:
        async with self._command_lock:
            if self._external_stepping:
                raise TrafficVerseError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "use the external step API in externally stepped experiments",
                )
            status = await self._status()
            if status not in {ExperimentStatus.RUNNING, ExperimentStatus.PAUSED}:
                raise TrafficVerseError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"vehicle control requires RUNNING or PAUSED status, found {status.value}",
                )
            if self._previous_snapshot is None or vehicle_id not in {
                vehicle.vehicle_id for vehicle in self._previous_snapshot.vehicles
            }:
                raise TrafficVerseError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    f"vehicle is not active: {vehicle_id}",
                )
            self._pending_api_controls[vehicle_id] = command

    async def run_tick(self) -> SimulationFrame:
        async with self._command_lock:
            if self._external_stepping:
                raise TrafficVerseError(ErrorCode.RESOURCE_CONFLICT, "external step required")
            return await self._run_tick_locked()

    async def step_external(
        self, expected_sequence: int, commands: Mapping[str, ControlCommand]
    ) -> SimulationFrame:
        async with self._command_lock:
            if not self._external_stepping:
                raise TrafficVerseError(
                    ErrorCode.RESOURCE_CONFLICT, "experiment uses automatic stepping"
                )
            if await self._status() is not ExperimentStatus.RUNNING:
                raise TrafficVerseError(
                    ErrorCode.INVALID_STATE_TRANSITION, "external step requires RUNNING"
                )
            current_sequence = self._previous_snapshot.sequence if self._previous_snapshot else 0
            if expected_sequence != current_sequence:
                raise TrafficVerseError(
                    ErrorCode.RESOURCE_CONFLICT, "observation sequence is stale"
                )
            active = (
                {v.vehicle_id for v in self._previous_snapshot.vehicles}
                if self._previous_snapshot
                else set()
            )
            unknown = set(commands) - active
            step_commands = {
                vehicle_id: command.model_copy(
                    update={"lane_change_duration_s": self._clock.step_ms / 1000.0}
                )
                for vehicle_id, command in commands.items()
                if vehicle_id in active
            }
            return await self._run_tick_locked(
                external_controls=step_commands, unknown_vehicle_ids=tuple(sorted(unknown))
            )

    async def _run_tick_locked(
        self,
        external_controls: Mapping[str, ControlCommand] | None = None,
        unknown_vehicle_ids: tuple[str, ...] = (),
    ) -> SimulationFrame:
        status = await self._status()
        if status is ExperimentStatus.PAUSED and self._last_frame is not None:
            return self._last_frame
        if status is not ExperimentStatus.RUNNING:
            raise TrafficVerseError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"cannot tick experiment while {status.value}",
            )
        target_time_ms = self._clock.next_time_ms
        try:
            rejected_vehicle_ids: tuple[str, ...] = ()
            if external_controls is not None:
                active = (
                    {v.vehicle_id for v in self._previous_snapshot.vehicles}
                    if self._previous_snapshot
                    else set()
                )
                speed_ids = {
                    vehicle_id
                    for vehicle_id, command in external_controls.items()
                    if command.desired_speed_mps is not None or command.stop_requested
                }
                release = sorted((self._external_speed_vehicle_ids - speed_ids) & active)
                self._traffic.release_speed_controls(release)
                rejected_vehicle_ids = self._traffic.apply_step_controls(external_controls)
                rejected_speed = sorted(speed_ids.intersection(rejected_vehicle_ids))
                if rejected_speed:
                    self._traffic.release_speed_controls(rejected_speed)
                self._external_speed_vehicle_ids = speed_ids.difference(rejected_vehicle_ids)
            else:
                controls = dict(
                    self._controller.step(self._previous_snapshot, self._clock.step_ms / 1000.0)
                )
                controls.update(self._pending_api_controls)
                self._traffic.apply_controls(controls)
                self._pending_api_controls.clear()
            traffic_snapshot = self._traffic.step(target_time_ms)
            self._previous_snapshot = traffic_snapshot
            self._clock.commit(target_time_ms)

            carla_frame = None
            roi_events: tuple[DomainEvent, ...] = ()
            if not self._carla_degraded:
                try:
                    known_actor_ids = self._roi_planner.actor_ids()
                    if known_actor_ids:
                        existing_actor_ids = self._carla.existing_actor_ids(
                            tuple(sorted(known_actor_ids))
                        )
                        self._roi_planner.report_missing_actor_ids(
                            known_actor_ids - existing_actor_ids
                        )
                    roi_plan = self._roi_planner.plan(traffic_snapshot)
                    roi_events = await self._record_roi_degradation(roi_plan)
                    signal_updates = self._signal_planner.plan(traffic_snapshot.traffic_lights)
                    spawn_results = self._apply_carla_plan(roi_plan, signal_updates)
                    self._roi_planner.commit(
                        RoiApplyResult(plan=roi_plan, spawn_results=spawn_results)
                    )
                    carla_frame = self._carla.tick(target_time_ms)
                except Exception as error:
                    if self._scenario.carla.mode is RequirementMode.REQUIRED:
                        raise
                    await self._degrade_carla(error)

            control_events: list[DomainEvent] = []
            for vehicle_id, reason in (
                *((vehicle_id, "VEHICLE_NOT_ACTIVE") for vehicle_id in unknown_vehicle_ids),
                *((vehicle_id, "SUMO_REJECTED") for vehicle_id in rejected_vehicle_ids),
            ):
                event = DomainEvent(
                    event_id=uuid4(),
                    experiment_id=self._require_experiment_id(),
                    event_type="vehicle.control.rejected",
                    severity=EventSeverity.WARNING,
                    simulation_time_ms=target_time_ms,
                    payload={"vehicle_id": vehicle_id, "reason": reason},
                )
                await self._experiments.append_event(event)
                await self._data_logger.record_event(event)
                control_events.append(event)

            frame = SimulationFrame(
                traffic=traffic_snapshot,
                carla=carla_frame,
                events=(*roi_events, *control_events),
            )
            await self._data_logger.record_frame(frame)
            await self._frame_publisher.publish_frame(frame)
            self._last_frame = frame
            end_time_ms = (
                self._scenario.simulation.start_time_ms + self._scenario.simulation.duration_ms
            )
            if target_time_ms >= end_time_ms:
                await self._stop_from_tick("DURATION_REACHED")
            return frame
        except Exception as error:
            await self._fail(error)
            raise

    def _apply_carla_plan(
        self,
        roi_plan: RoiApplyPlan,
        signal_updates: tuple[TrafficLightUpdate, ...],
    ) -> tuple[ActorSpawnResult, ...]:
        spawn_results: tuple[ActorSpawnResult, ...] = ()
        if roi_plan.destroy_actor_ids:
            self._carla.destroy_actors(roi_plan.destroy_actor_ids)
        if roi_plan.spawns:
            spawn_results = self._carla.spawn_vehicles(roi_plan.spawns)
        self._carla.update_actors(roi_plan.actor_updates)
        self._carla.update_traffic_lights(signal_updates)
        return spawn_results

    async def _record_roi_degradation(self, roi_plan: RoiApplyPlan) -> tuple[DomainEvent, ...]:
        current = frozenset(roi_plan.degraded_vehicle_ids)
        newly_deferred = sorted(current - self._deferred_roi_vehicle_ids)
        self._deferred_roi_vehicle_ids = current
        if not newly_deferred:
            return ()
        event = DomainEvent(
            event_id=uuid4(),
            experiment_id=self._require_experiment_id(),
            event_type="roi.actor_limit_reached",
            severity=EventSeverity.WARNING,
            simulation_time_ms=roi_plan.simulation_time_ms,
            payload={"deferred_vehicle_ids": newly_deferred},
        )
        await self._experiments.append_event(event)
        await self._data_logger.record_event(event)
        return (event,)

    async def _stop_from_tick(self, reason: str) -> None:
        await self._transition(ExperimentStatus.STOPPING, reason=reason)
        errors = await self._cleanup()
        if errors:
            await self._transition(
                ExperimentStatus.FAILED,
                reason="; ".join(str(item) for item in errors),
            )
            return
        await self._transition(ExperimentStatus.COMPLETED, reason=reason)

    async def _degrade_carla(self, error: Exception) -> None:
        self._carla_degraded = True
        if self._carla_opened:
            try:
                self._carla.close()
            finally:
                self._carla_opened = False
        event = DomainEvent(
            event_id=uuid4(),
            experiment_id=self._require_experiment_id(),
            event_type="carla.degraded",
            severity=EventSeverity.WARNING,
            simulation_time_ms=self._clock.current_time_ms,
            payload={"reason": str(error)},
        )
        await self._experiments.append_event(event)
        await self._data_logger.record_event(event)

    async def _fail(self, error: Exception) -> None:
        experiment_id = self._require_experiment_id()
        try:
            status = await self._status()
            if status is not ExperimentStatus.FAILED:
                await self._transition(ExperimentStatus.FAILED, reason=str(error))
        finally:
            await self._cleanup()
            if self._registry is not None:
                await self._registry.release_running(experiment_id)

    async def _cleanup(self) -> tuple[Exception, ...]:
        if self._cleanup_done:
            return ()
        errors: list[Exception] = []
        if not self._logger_flushed:
            try:
                await self._data_logger.flush()
                self._logger_flushed = True
            except Exception as error:
                errors.append(error)
        if self._carla_opened:
            try:
                self._carla.close()
                self._carla_opened = False
            except Exception as error:
                errors.append(error)
        if self._traffic_opened:
            try:
                self._traffic.close()
                self._traffic_opened = False
            except Exception as error:
                errors.append(error)
        if self._registry is not None and self._experiment_id is not None:
            await self._registry.release_running(self._experiment_id)
        self._cleanup_done = not errors
        if self._cleanup_done and self._registry is not None and self._experiment_id is not None:
            await self._registry.unregister(self._experiment_id)
        return tuple(errors)

    async def _transition(self, status: ExperimentStatus, *, reason: str | None = None) -> None:
        experiment_id = self._require_experiment_id()
        current = await self._experiments.get_status(experiment_id)
        if current is status:
            return
        require_transition(current, status)
        await self._experiments.set_status(experiment_id, status, reason=reason)

    async def _status(self) -> ExperimentStatus:
        return await self._experiments.get_status(self._require_experiment_id())

    def _require_experiment_id(self) -> UUID:
        if self._experiment_id is None:
            raise TrafficVerseError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "simulation manager has not been prepared",
            )
        return self._experiment_id
