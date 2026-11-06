"""Public, credential-free decision history resources."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: int
    sequence: int
    simulation_time_ms: int
    group_name: str
    model_id: str
    started_at: datetime
    state: Literal["running", "completed", "failed", "skipped"] = "running"
    outcome: Literal["pending", "submitted", "discarded", "failed"] = "pending"
    elapsed_ms: int = 0
    vehicle_count: int = 0
    action_count: int = 0
    fallback_count: int = 0
    message: str = "正在推理"
    truncated: bool = False


class DecisionRecord(DecisionSummary):
    input_text: str = ""
    request_text: str = ""
    output_text: str = ""
    actions_text: str = "[]"
    input_preview: "DecisionInputPreview | None" = None


class NearbyInputPreview(BaseModel):
    vehicle_id: str
    distance_m: float
    speed_mps: float


class MemoryInputPreview(BaseModel):
    sequence: int
    action_text: str
    next_speed_mps: float | None = None


class SkillInputPreview(BaseModel):
    name: str
    content: str


class VehicleInputPreview(BaseModel):
    vehicle_id: str
    speed_mps: float
    lane_id: str
    radius_m: float
    nearby_count: int
    nearby: tuple[NearbyInputPreview, ...] = ()
    memory_steps: int | None = None
    recent_memory: tuple[MemoryInputPreview, ...] = ()
    skills: tuple[SkillInputPreview, ...] = ()


class DecisionInputPreview(BaseModel):
    vehicles: tuple[VehicleInputPreview, ...] = ()
    truncated: bool = False


class DecisionPage(BaseModel):
    session_id: UUID | None = None
    total: int = 0
    items: tuple[DecisionSummary, ...] = ()
    next_before_id: int | None = None
    storage_warning: bool = False
