from uuid import uuid4

import pytest
from pydantic import ValidationError

from trafficverse.adapters.highway_env.adapter import HighwayTrafficEngineAdapter
from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.errors import TrafficVerseError


def test_policy_steps_must_be_exact_integer_milliseconds() -> None:
    with pytest.raises(ValidationError):
        HighwayEnvConfig(policy_frequency=3)
    with pytest.raises(ValidationError):
        HighwayEnvConfig(policy_frequency=10, simulation_frequency=15)


def test_unloaded_adapter_rejects_steps_and_close_is_idempotent() -> None:
    adapter = HighwayTrafficEngineAdapter(uuid4())
    with pytest.raises(TrafficVerseError):
        adapter.step(100)
    adapter.close()
    adapter.close()


def test_missing_optional_dependency_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    def unavailable(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", unavailable)
    adapter = HighwayTrafficEngineAdapter(uuid4())
    with pytest.raises(TrafficVerseError, match="extra highway"):
        adapter.load(HighwayEnvConfig())
    assert adapter.health().status.value == "UNAVAILABLE"
    adapter.close()
