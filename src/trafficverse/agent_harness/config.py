"""Typed configuration for external polling and agent assignments."""

from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    wrapper: str = Field(
        min_length=3, description="Python module:class; constructed without arguments"
    )
    vehicle_ids: tuple[str, ...] = ()
    automation_levels: tuple[Literal["L0", "L1", "L2", "L3", "L4", "L5"], ...] = ()

    @model_validator(mode="after")
    def require_selector(self) -> "Binding":
        if not self.vehicle_ids and not self.automation_levels:
            raise ValueError("a binding must select vehicle IDs or automation levels")
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
        return HarnessConfig.model_validate(yaml.safe_load(stream))
