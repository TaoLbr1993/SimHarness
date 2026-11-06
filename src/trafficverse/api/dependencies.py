"""Technology-neutral dependencies injected into the FastAPI edge."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from trafficverse.api.models import ExperimentView, ReadinessComponent
from trafficverse.domain.enums import ErrorCode, ExperimentStatus
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import ControlCommand, SimulationFrame, SimulationRunInput

if TYPE_CHECKING:
    from trafficverse.adapters.messaging.frame_broker import FrameBroker
    from trafficverse.agent_harness.sessions import AgentSessionService
    from trafficverse.api.command_bus import ExperimentCommandBus
    from trafficverse.api.map_catalog import MapCatalog
    from trafficverse.application.simulation_configuration_service import (
        SimulationConfigurationService,
    )
    from trafficverse.application.simulation_history_service import SimulationHistoryService
    from trafficverse.application.workspace_service import WorkspaceService


class SimulationControlPort(Protocol):
    @property
    def external_stepping(self) -> bool: ...
    @property
    def experiment_id(self) -> UUID | None: ...

    @property
    def simulation_time_ms(self) -> int: ...

    @property
    def speed_multiplier(self) -> float: ...

    @property
    def last_frame(self) -> SimulationFrame | None: ...

    async def prepare(self, experiment_id: UUID) -> None: ...

    async def start(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def stop(self, reason: str = "USER_REQUEST") -> None: ...

    async def set_speed(self, multiplier: float) -> None: ...

    async def get_status(self) -> ExperimentStatus: ...

    async def control_vehicle(self, vehicle_id: str, command: ControlCommand) -> None: ...

    def enable_external_stepping(self) -> None: ...

    async def step_external(
        self, expected_sequence: int, commands: dict[str, ControlCommand]
    ) -> SimulationFrame: ...


RuntimeFactory = Callable[
    [UUID, UUID, str | None, SimulationRunInput | None],
    Awaitable[SimulationControlPort],
]
ReadinessCheck = Callable[[], Awaitable[tuple[ReadinessComponent, ...]]]
ShutdownHook = Callable[[], Awaitable[None]]
_UNSCOPED_WORKSPACE_ID = UUID(int=0)


class RuntimeDirectory:
    """Per-process manager directory; construction stays outside handlers."""

    def __init__(self, factory: RuntimeFactory | None = None) -> None:
        self._factory = factory
        self._managers: dict[UUID, SimulationControlPort] = {}
        self._workspace_ids: dict[UUID, UUID] = {}
        self._step_owners: dict[UUID, UUID] = {}
        self._step_locks: dict[UUID, asyncio.Lock] = {}

    def control_owner(self, experiment_id: UUID) -> UUID | None:
        return self._step_owners.get(experiment_id)

    async def claim_control(self, experiment_id: UUID, owner: UUID) -> None:
        async with self._step_locks.setdefault(experiment_id, asyncio.Lock()):
            if self._step_owners.get(experiment_id) not in (None, owner):
                raise TrafficVerseError(ErrorCode.RESOURCE_CONFLICT, "实验已有 Agent 接入会话")
            self._step_owners[experiment_id] = owner

    def release_control(self, experiment_id: UUID, owner: UUID) -> None:
        if self._step_owners.get(experiment_id) == owner:
            del self._step_owners[experiment_id]

    async def step_external(
        self,
        experiment_id: UUID,
        expected_sequence: int,
        commands: dict[str, ControlCommand],
        *,
        owner: UUID | None = None,
    ) -> SimulationFrame:
        async with self._step_locks.setdefault(experiment_id, asyncio.Lock()):
            if self._step_owners.get(experiment_id) != owner:
                raise TrafficVerseError(ErrorCode.RESOURCE_CONFLICT, "实验由另一接入会话控制")
            manager = await self.get(experiment_id)
            return await manager.step_external(expected_sequence, commands)

    async def create(
        self,
        experiment_id: UUID,
        workspace_id: UUID,
        scenario_id: UUID,
        map_id: str | None = None,
        run_input: SimulationRunInput | None = None,
        external_stepping: bool = False,
    ) -> ExperimentView:
        if self._factory is None:
            raise TrafficVerseError(
                ErrorCode.COMPONENT_UNAVAILABLE,
                "experiment runtime factory is not configured",
            )
        manager = await self._factory(experiment_id, scenario_id, map_id, run_input)
        if external_stepping:
            manager.enable_external_stepping()
        self._managers[experiment_id] = manager
        self._workspace_ids[experiment_id] = workspace_id
        return ExperimentView(
            experiment_id=experiment_id,
            workspace_id=workspace_id,
            status=ExperimentStatus.CREATED,
            simulation_time_ms=manager.simulation_time_ms,
            speed_multiplier=manager.speed_multiplier,
            external_stepping=manager.external_stepping,
            latest_sequence=manager.last_frame.traffic.sequence if manager.last_frame else 0,
        )

    def register(
        self,
        experiment_id: UUID,
        manager: SimulationControlPort,
        *,
        workspace_id: UUID = _UNSCOPED_WORKSPACE_ID,
    ) -> None:
        self._managers[experiment_id] = manager
        self._workspace_ids[experiment_id] = workspace_id

    async def get(self, experiment_id: UUID) -> SimulationControlPort:
        manager = self._managers.get(experiment_id)
        if manager is None:
            raise TrafficVerseError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"experiment runtime is not registered: {experiment_id}",
            )
        return manager

    async def view(self, experiment_id: UUID) -> ExperimentView:
        manager = await self.get(experiment_id)
        return ExperimentView(
            experiment_id=experiment_id,
            workspace_id=self._workspace_ids[experiment_id],
            status=await manager.get_status(),
            simulation_time_ms=manager.simulation_time_ms,
            speed_multiplier=manager.speed_multiplier,
            external_stepping=manager.external_stepping,
            latest_sequence=manager.last_frame.traffic.sequence if manager.last_frame else 0,
        )


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    runtimes: RuntimeDirectory
    maps: MapCatalog
    commands: ExperimentCommandBus
    broker: FrameBroker
    readiness: ReadinessCheck
    workspaces: WorkspaceService
    configurations: SimulationConfigurationService | None = None
    histories: SimulationHistoryService | None = None
    shutdown: ShutdownHook | None = None
    agent_sessions: AgentSessionService | None = None
