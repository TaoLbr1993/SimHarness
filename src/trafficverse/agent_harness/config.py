"""Typed configuration for external polling and agent assignments."""

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelConfig(BaseModel):
    """Connection settings; credentials are resolved from the named environment variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    api_key_env: str | None = Field(default=None, min_length=1)
    timeout_s: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    jev_acceleration_mps2: float = Field(default=1.0, gt=0, allow_inf_nan=False)

    @field_validator("api_key_env")
    @classmethod
    def validate_environment_name(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
            raise ValueError("api_key_env must be an environment variable name, not a key")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, a query or fragment")
        return value.rstrip("/")


class MemoryConfig(BaseModel):
    """Per-vehicle in-process memory configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    max_steps: int = Field(default=10, ge=1, le=1000)


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    wrapper: str = Field(
        min_length=3, description="Python module:class; accepts model if configured"
    )
    vehicle_ids: tuple[str, ...] = ()
    all_vehicles: bool = False
    automation_levels: tuple[Literal["L0", "L1", "L2", "L3", "L4", "L5"], ...] = ()
    model: ModelConfig | None = None
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_selector(self) -> "Binding":
        if not self.all_vehicles and not self.vehicle_ids and not self.automation_levels:
            raise ValueError("a binding must select vehicle IDs, automation levels or all vehicles")
        return self


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: str = "http://127.0.0.1:8000"
    experiment_id: UUID | None = None
    workspace_id: UUID | None = None
    scenario_id: UUID | None = None
    map_id: str | None = Field(default=None, min_length=1)
    configuration_id: str | None = None
    max_steps: int = Field(default=100, ge=1)
    step_delay_s: float = Field(default=0, ge=0)
    request_timeout_s: float = Field(default=30.0, gt=0)
    observation_radius_m: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    skills_directory: Path | None = None
    bindings: tuple[Binding, ...] = ()

    @model_validator(mode="after")
    def unique_names(self) -> "HarnessConfig":
        names = [binding.name for binding in self.bindings]
        if len(names) != len(set(names)):
            raise ValueError("binding names must be unique")
        if self.experiment_id is None and (
            self.workspace_id is None or self.scenario_id is None or self.map_id is None
        ):
            raise ValueError("provide experiment_id or workspace_id, scenario_id and map_id")
        return self


def load_config(path: Path) -> HarnessConfig:
    with path.open(encoding="utf-8") as stream:
        config = HarnessConfig.model_validate(yaml.safe_load(stream))
    if config.skills_directory is not None and not config.skills_directory.is_absolute():
        return config.model_copy(
            update={"skills_directory": (path.resolve().parent / config.skills_directory).resolve()}
        )
    return config
