"""Typed, independently reproducible HighwayEnv scenario packages."""

from pathlib import Path

import yaml
from pydantic import Field

from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.models.common import StrictModel


class HighwayScenario(StrictModel):
    map_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    config: HighwayEnvConfig = Field(default_factory=HighwayEnvConfig)


def load_highway_scenario(path: Path) -> HighwayScenario:
    return HighwayScenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
