"""Local mirrors of Agent session resources; no backend imports."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(SessionModel):
    base_url: str
    model_id: str
    api_key_env: str | None = None
    timeout_s: float = 30
    jev_acceleration_mps2: float = 1


class MemorySettings(SessionModel):
    enabled: bool = False
    max_steps: int = 10


class ControlGroup(SessionModel):
    name: str
    kind: Literal["api", "local", "jev"] = "api"
    model: ModelSettings
    vehicle_ids: tuple[str, ...] = ()
    all_vehicles: bool = False
    memory: MemorySettings = Field(default_factory=MemorySettings)
    skills: tuple[str, ...] = ()


class AgentSessionConfig(SessionModel):
    bindings: tuple[ControlGroup, ...]
    observation_radius_m: float = 100
    max_steps: int = 100
    step_delay_s: float = 0


class AgentSessionView(SessionModel):
    experiment_id: UUID
    session_id: UUID | None = None
    status: str = "idle"
    experiment_status: str
    external_stepping: bool
    sequence: int = 0
    simulation_time_ms: int = 0
    steps_completed: int = 0
    vehicle_ids: tuple[str, ...] = ()
    message: str = "尚未接入"
    config: AgentSessionConfig | None = None


class AgentSessionCheck(SessionModel):
    ready: bool
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
