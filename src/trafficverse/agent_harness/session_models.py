"""Public configuration and state for a desktop-managed Agent session."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Binding, MemoryConfig, ModelConfig

ModelKind = Literal["api", "local", "jev"]
SessionStatus = Literal["idle", "running", "pausing", "paused", "stopping", "stopped", "failed"]
BUILTIN_SKILLS = ("human_cautious", "human_normal", "human_reckless")


class SessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    kind: ModelKind = "api"
    model: ModelConfig
    vehicle_ids: tuple[str, ...] = Field(default=(), max_length=10000)
    all_vehicles: bool = False
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: tuple[Literal["human_cautious", "human_normal", "human_reckless"], ...] = ()

    @field_validator("vehicle_ids")
    @classmethod
    def clean_vehicle_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("vehicle IDs must be nonempty and at most 200 characters")
        return tuple(dict.fromkeys(value.strip() for value in values))

    @model_validator(mode="after")
    def valid_selection(self) -> "SessionBinding":
        if not self.all_vehicles and not self.vehicle_ids:
            raise ValueError("select vehicle IDs or all vehicles")
        if self.kind != "local" and not self.model.api_key_env:
            raise ValueError("remote models require an API key environment variable name")
        if self.model.timeout_s > 300:
            raise ValueError("desktop model timeout must not exceed 300 seconds")
        return self

    def to_binding(self) -> Binding:
        classes = {"api": "ApiModel", "local": "LocalModel", "jev": "JevModel"}
        return Binding(
            name=self.name,
            wrapper=f"trafficverse.agent_harness.models.{self.kind}:{classes[self.kind]}",
            model=self.model,
            vehicle_ids=self.vehicle_ids,
            all_vehicles=self.all_vehicles,
            memory=self.memory,
            skills=self.skills,
        )


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bindings: tuple[SessionBinding, ...] = Field(min_length=1, max_length=16)
    observation_radius_m: float = Field(default=100, gt=0, allow_inf_nan=False)
    max_steps: int = Field(default=100, ge=1, le=1000000)
    step_delay_s: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def disjoint_bindings(self) -> "SessionConfig":
        names: set[str] = set()
        vehicles: set[str] = set()
        for binding in self.bindings:
            if binding.name in names:
                raise ValueError("control group names must be unique")
            if vehicles.intersection(binding.vehicle_ids):
                raise ValueError("a vehicle cannot belong to multiple control groups")
            if binding.all_vehicles and len(self.bindings) != 1:
                raise ValueError("all-vehicle selection cannot overlap other control groups")
            names.add(binding.name)
            vehicles.update(binding.vehicle_ids)
        return self


class SessionCheck(BaseModel):
    ready: bool
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class SessionView(BaseModel):
    experiment_id: UUID
    session_id: UUID | None = None
    status: SessionStatus = "idle"
    experiment_status: str
    external_stepping: bool
    sequence: int = 0
    simulation_time_ms: int = 0
    steps_completed: int = 0
    vehicle_ids: tuple[str, ...] = ()
    message: str = "尚未接入"
    config: SessionConfig | None = None
