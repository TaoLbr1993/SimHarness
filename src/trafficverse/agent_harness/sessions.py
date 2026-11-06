"""Bounded, frame-aligned Agent sessions hosted by the simulation backend."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from trafficverse.api.models import VehicleControlRequest
from trafficverse.application.vehicle_observation import local_observations
from trafficverse.domain.enums import ErrorCode, ExperimentStatus
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import ControlCommand, SimulationFrame
from trafficverse.domain.models.observation import LocalObservationFrame

from .client import HttpSimulationClient
from .config import Binding, HarnessConfig
from .loader import load_agent
from .manager import AgentManager
from .models.base import VehicleAgent
from .session_models import SessionCheck, SessionConfig, SessionStatus, SessionView
from .skills.provider import FileSkillProvider
from .trace_models import DecisionPage, DecisionRecord
from .tracing import DecisionHistory

if TYPE_CHECKING:
    from trafficverse.api.dependencies import RuntimeDirectory

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    experiment_id: UUID
    config: SessionConfig
    manager: AgentManager
    session_id: UUID = field(default_factory=uuid4)
    status: SessionStatus = "running"
    steps: int = 0
    message: str = "准备读取局部观测"
    task: asyncio.Task[None] | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    decision_cancelled: threading.Event = field(default_factory=threading.Event)
    history: DecisionHistory | None = None

    def is_running(self) -> bool:
        return self.status == "running"


class AgentSessionService:
    """Own the step lease; model work runs off the API event loop."""

    def __init__(
        self,
        runtimes: RuntimeDirectory,
        *,
        model_loader: Callable[[Binding], VehicleAgent] = load_agent,
        history_root: Path | None = None,
    ) -> None:
        self._runtimes = runtimes
        self._load_model = model_loader
        self._sessions: dict[UUID, _Session] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._history_root = history_root

    async def decisions(
        self, experiment_id: UUID, before_id: int | None = None, limit: int = 20
    ) -> DecisionPage:
        await self._runtimes.get(experiment_id)
        session = self._sessions.get(experiment_id)
        if session is None or session.history is None:
            return DecisionPage()
        return await asyncio.to_thread(session.history.page, before_id, limit)

    async def decision(
        self, experiment_id: UUID, record_id: int, session_id: UUID
    ) -> DecisionRecord:
        await self._runtimes.get(experiment_id)
        session = self._sessions.get(experiment_id)
        if session is None or session.session_id != session_id or session.history is None:
            raise TrafficVerseError(ErrorCode.RESOURCE_NOT_FOUND, "接入会话已改变，请刷新记录")
        try:
            return await asyncio.to_thread(session.history.get, record_id)
        except KeyError as error:
            raise TrafficVerseError(ErrorCode.RESOURCE_NOT_FOUND, "该轮记录不存在") from error

    async def view(self, experiment_id: UUID) -> SessionView:
        runtime = await self._runtimes.get(experiment_id)
        experiment = await self._runtimes.view(experiment_id)
        session = self._sessions.get(experiment_id)
        frame = runtime.last_frame
        if (
            session
            and session.status == "paused"
            and experiment.status in {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED}
        ):
            session.status = "stopped"
            session.message = "实验已结束"
            self._runtimes.release_control(experiment_id, session.session_id)
        return SessionView(
            experiment_id=experiment_id,
            session_id=session.session_id if session else None,
            status=session.status if session else "idle",
            experiment_status=experiment.status.value,
            external_stepping=experiment.external_stepping,
            sequence=experiment.latest_sequence,
            simulation_time_ms=experiment.simulation_time_ms,
            vehicle_ids=tuple(v.vehicle_id for v in frame.traffic.vehicles) if frame else (),
            steps_completed=session.steps if session else 0,
            message=session.message if session else "尚未接入",
            config=session.config if session else None,
        )

    async def check(self, experiment_id: UUID, config: SessionConfig) -> SessionCheck:
        experiment = await self._runtimes.view(experiment_id)
        issues: list[str] = []
        warnings: list[str] = []
        if self._closed:
            issues.append("Agent 运行器正在关闭")
        if not experiment.external_stepping:
            issues.append("请创建勾选“等待外部 Agent 控制”的仿真")
        if experiment.status is not ExperimentStatus.RUNNING:
            issues.append("请先开始或继续仿真，再接入 Agent")
        session = self._sessions.get(experiment_id)
        owner = self._runtimes.control_owner(experiment_id)
        if owner is not None and (session is None or owner != session.session_id):
            issues.append("实验已有其他 Agent 会话")
        for binding in config.bindings:
            env = binding.model.api_key_env
            if env and not os.environ.get(env):
                issues.append(f"{binding.name}：后端运行器未读取到环境变量 {env}")
            for skill in binding.skills:
                try:
                    FileSkillProvider().load(skill)
                except (ValueError, OSError, UnicodeError):
                    warnings.append(f"{binding.name}：Skill {skill} 不可用，运行时将忽略")
        return SessionCheck(ready=not issues, issues=tuple(issues), warnings=tuple(warnings))

    def _manager(self, experiment_id: UUID, config: SessionConfig) -> AgentManager:
        bindings = tuple(binding.to_binding() for binding in config.bindings)
        settings = HarnessConfig(
            experiment_id=experiment_id,
            bindings=bindings,
            observation_radius_m=config.observation_radius_m,
            max_steps=config.max_steps,
            step_delay_s=config.step_delay_s,
        )
        # Only the decision and memory methods are used; sessions own runtime stepping.
        return AgentManager(
            settings,
            HttpSimulationClient(settings.api_url, settings.request_timeout_s),
            {binding.name: self._load_model(binding) for binding in bindings},
        )

    async def start(self, experiment_id: UUID, config: SessionConfig) -> SessionView:
        async with self._lock:
            previous = self._sessions.get(experiment_id)
            if previous and previous.status in {"running", "pausing", "stopping"}:
                raise TrafficVerseError(ErrorCode.RESOURCE_CONFLICT, "请等待当前接入会话暂停或结束")
            check = await self.check(experiment_id, config)
            if not check.ready:
                raise TrafficVerseError(
                    ErrorCode.SCENARIO_VALIDATION_FAILED, "；".join(check.issues)
                )
            resume = previous is not None and previous.status == "paused"
            if resume and previous is not None:
                session = previous
                # Unchanged configuration preserves per-vehicle memory across pauses.
                if previous.config != config:
                    session.manager = self._manager(experiment_id, config)
                session.config = config
            else:
                session = _Session(experiment_id, config, self._manager(experiment_id, config))
                session.history = DecisionHistory(
                    session.session_id,
                    self._history_root / str(experiment_id) / str(session.session_id)
                    if self._history_root
                    else None,
                )
            await self._runtimes.claim_control(experiment_id, session.session_id)
            session.status = "running"
            session.steps = 0
            session.message = "已接入，等待模型决策"
            session.wake.clear()
            session.decision_cancelled.clear()
            self._sessions[experiment_id] = session
            session.task = asyncio.create_task(self._run(session))
        return await self.view(experiment_id)

    async def pause(self, experiment_id: UUID) -> SessionView:
        async with self._lock:
            session = self._sessions.get(experiment_id)
            if session and session.status == "running":
                session.status = "pausing"
                session.decision_cancelled.set()
                session.message = "正在等待本轮推理返回；不会提交尚未执行的动作"
                session.wake.set()
        return await self.view(experiment_id)

    async def stop(self, experiment_id: UUID) -> SessionView:
        async with self._lock:
            session = self._sessions.get(experiment_id)
            if session and session.status in {"running", "pausing", "stopping"}:
                session.status = "stopping"
                session.decision_cancelled.set()
                session.message = "正在结束接入，等待当前调用返回"
                session.wake.set()
            elif session:
                session.status = "stopped"
                session.message = "已结束接入；实验等待外部动作"
                self._runtimes.release_control(experiment_id, session.session_id)
        return await self.view(experiment_id)

    @staticmethod
    def _observe(frame: SimulationFrame, config: SessionConfig) -> LocalObservationFrame:
        return local_observations(
            frame,
            radius_m=config.observation_radius_m,
            vehicle_ids=tuple(v for binding in config.bindings for v in binding.vehicle_ids),
            all_vehicles=any(binding.all_vehicles for binding in config.bindings),
        )

    async def _run(self, session: _Session) -> None:
        decision_sequence: int | None = None
        try:
            runtime = await self._runtimes.get(session.experiment_id)
            while session.is_running():
                status = await runtime.get_status()
                if status is not ExperimentStatus.RUNNING:
                    session.status = "paused" if status is ExperimentStatus.PAUSED else "stopped"
                    session.message = (
                        "实验已暂停，请继续仿真后再接入"
                        if status is ExperimentStatus.PAUSED
                        else "实验已结束"
                    )
                    break
                frame = runtime.last_frame
                commands: dict[str, ControlCommand] = {}
                actions: tuple[VehicleControlRequest, ...] = ()
                previous = self._observe(frame, session.config) if frame else None
                if previous is not None:
                    decision_sequence = previous.sequence
                    session.message = f"正在推理：第 {previous.sequence} 帧，仿真等待模型"
                    try:
                        actions = await asyncio.to_thread(
                            session.manager.actions_for,
                            previous,
                            should_continue=lambda: not session.decision_cancelled.is_set(),
                            history=session.history,
                        )
                    except Exception:
                        # Do not log raw model exceptions: they may contain provider credentials.
                        logger.warning(
                            "agent.session_decision_failed experiment=%s", session.experiment_id
                        )
                        actions = ()
                        if session.history:
                            await asyncio.to_thread(
                                session.history.settle, previous.sequence, "failed"
                            )
                    commands = {
                        action.vehicle_id: ControlCommand(
                            desired_speed_mps=action.desired_speed_mps,
                            desired_acceleration_mps2=action.desired_acceleration_mps2,
                            lane_change=action.lane_change,
                            stop_requested=action.stop_requested,
                        )
                        for action in actions
                    }
                if not session.is_running():
                    break
                if await runtime.get_status() is not ExperimentStatus.RUNNING:
                    if session.history and decision_sequence is not None:
                        await asyncio.to_thread(
                            session.history.settle, decision_sequence, "discarded"
                        )
                    continue
                # The lease and sequence check protect against stale or competing submissions.
                frame = await self._runtimes.step_external(
                    session.experiment_id,
                    frame.traffic.sequence if frame else 0,
                    commands,
                    owner=session.session_id,
                )
                session.steps += 1
                if session.history and decision_sequence is not None:
                    await asyncio.to_thread(session.history.settle, decision_sequence, "submitted")
                decision_sequence = None
                if previous is not None:
                    session.manager.record_transition(
                        previous, actions, self._observe(frame, session.config)
                    )
                if not session.is_running():
                    break
                session.message = (
                    f"第 {frame.traffic.sequence} 帧：提交 {len(actions)} 个动作，"
                    "其余车辆由 SUMO 控制"
                )
                if session.steps >= session.config.max_steps:
                    session.status = "paused"
                    session.message = "已达到本次帧数上限；可继续接入"
                elif session.config.step_delay_s:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(session.wake.wait(), session.config.step_delay_s)
        except asyncio.CancelledError:
            session.status = "stopped"
            session.message = "后端正在关闭，接入已结束"
            raise
        except Exception:
            logger.warning("agent.session_failed experiment=%s", session.experiment_id)
            session.status = "failed"
            session.message = "接入失败，请检查实验状态和后端日志"
            if session.history and decision_sequence is not None:
                await asyncio.to_thread(session.history.settle, decision_sequence, "failed")
        finally:
            if session.history and decision_sequence is not None:
                await asyncio.to_thread(session.history.settle, decision_sequence, "discarded")
            if session.status == "pausing":
                session.status = "paused"
                session.message = "已暂停接入；仿真停在帧边界"
            elif session.status == "stopping":
                session.status = "stopped"
                session.message = "已结束接入；实验等待外部动作"
            if session.status != "paused":
                self._runtimes.release_control(session.experiment_id, session.session_id)

    async def close(self) -> None:
        self._closed = True
        tasks = []
        for session in self._sessions.values():
            session.decision_cancelled.set()
            if session.task and not session.task.done():
                session.task.cancel()
                tasks.append(session.task)
            self._runtimes.release_control(session.experiment_id, session.session_id)
        await asyncio.gather(*tasks, return_exceptions=True)
