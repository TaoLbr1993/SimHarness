"""HTTP resources for backend-hosted Agent sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Query

from trafficverse.agent_harness.session_models import SessionCheck, SessionConfig, SessionView
from trafficverse.agent_harness.trace_models import DecisionPage, DecisionRecord
from trafficverse.api.dependencies import ApiDependencies
from trafficverse.domain.enums import ErrorCode
from trafficverse.domain.errors import TrafficVerseError

if TYPE_CHECKING:
    from trafficverse.agent_harness.sessions import AgentSessionService


def build_agent_session_router(dependencies: ApiDependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v1/experiments/{experiment_id}/agent-session")

    def service() -> AgentSessionService:
        if dependencies.agent_sessions is None:
            raise TrafficVerseError(ErrorCode.COMPONENT_UNAVAILABLE, "当前后端未启用 Agent 接入")
        return dependencies.agent_sessions

    @router.get("", response_model=SessionView)
    async def view(experiment_id: UUID) -> SessionView:
        return await service().view(experiment_id)

    @router.post("/check", response_model=SessionCheck)
    async def check(experiment_id: UUID, config: SessionConfig) -> SessionCheck:
        return await service().check(experiment_id, config)

    @router.post("/start", response_model=SessionView)
    async def start(experiment_id: UUID, config: SessionConfig) -> SessionView:
        return await service().start(experiment_id, config)

    @router.post("/pause", response_model=SessionView)
    async def pause(experiment_id: UUID) -> SessionView:
        return await service().pause(experiment_id)

    @router.post("/stop", response_model=SessionView)
    async def stop(experiment_id: UUID) -> SessionView:
        return await service().stop(experiment_id)

    @router.get("/decisions", response_model=DecisionPage)
    async def decisions(
        experiment_id: UUID,
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> DecisionPage:
        return await service().decisions(experiment_id, before_id, limit)

    @router.get("/decisions/{record_id}", response_model=DecisionRecord)
    async def decision(experiment_id: UUID, record_id: int, session_id: UUID) -> DecisionRecord:
        return await service().decision(experiment_id, record_id, session_id)

    return router
