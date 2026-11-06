"""Lock the desktop/backend session protocol and restricted model selection."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError
from ui.models.agent_decision import DecisionPage as DesktopDecisionPage
from ui.models.agent_decision import DecisionRecord as DesktopDecisionRecord
from ui.models.agent_session import (
    AgentSessionConfig,
    AgentSessionView,
    ControlGroup,
    MemorySettings,
    ModelSettings,
)

from trafficverse.agent_harness.session_models import SessionBinding, SessionConfig, SessionView
from trafficverse.agent_harness.trace_models import (
    DecisionInputPreview,
    DecisionPage,
    DecisionRecord,
    DecisionSummary,
    VehicleInputPreview,
)


@pytest.mark.parametrize("kind", ["api", "local", "jev"])
def test_desktop_configuration_and_backend_state_roundtrip(kind: str) -> None:
    desktop = AgentSessionConfig(
        bindings=(
            ControlGroup.model_validate(
                {
                    "name": "driver",
                    "kind": kind,
                    "all_vehicles": True,
                    "model": ModelSettings(
                        base_url="http://localhost:8002/v1",
                        model_id="test",
                        api_key_env="SIMHARNESS_TEST_KEY",
                    ),
                    "memory": MemorySettings(enabled=True, max_steps=20),
                    "skills": ("human_normal",),
                }
            ),
        )
    )
    backend = SessionConfig.model_validate_json(desktop.model_dump_json())
    state = SessionView(
        experiment_id=UUID(int=1),
        experiment_status="RUNNING",
        external_stepping=True,
        status="paused",
        sequence=12,
        config=backend,
    )
    mirrored = AgentSessionView.model_validate_json(state.model_dump_json())
    assert mirrored.config == desktop
    assert mirrored.sequence == 12 and mirrored.status == "paused"


def test_http_binding_defaults_and_disallowed_wrapper_or_skill() -> None:
    payload = {
        "name": "local-driver",
        "kind": "local",
        "all_vehicles": True,
        "model": {"base_url": "http://localhost:8002/v1", "model_id": "test"},
    }
    binding = SessionBinding.model_validate(payload)
    assert not binding.memory.enabled and not binding.skills
    for extra in ({"wrapper": "arbitrary.module:Class"}, {"skills": ["../private"]}):
        with pytest.raises(ValidationError):
            SessionBinding.model_validate({**payload, **extra})


def test_decision_history_resources_match_desktop_mirrors() -> None:
    record = DecisionRecord(
        record_id=1,
        sequence=3,
        simulation_time_ms=150,
        group_name="driver",
        model_id="local",
        started_at=datetime.now(timezone.utc),
        state="completed",
        outcome="discarded",
        input_text='{"observations": []}',
        output_text="invalid JSON",
        actions_text="[]",
        input_preview=DecisionInputPreview(
            vehicles=(
                VehicleInputPreview(
                    vehicle_id="car",
                    speed_mps=8,
                    lane_id="lane",
                    radius_m=100,
                    nearby_count=2,
                    memory_steps=0,
                ),
            )
        ),
    )
    summary = DecisionSummary.model_validate(record.model_dump())
    page = DecisionPage(session_id=UUID(int=1), total=1, items=(summary,))
    assert (
        DesktopDecisionPage.model_validate_json(page.model_dump_json()).items[0].outcome
        == "discarded"
    )
    assert (
        DesktopDecisionRecord.model_validate_json(record.model_dump_json()).output_text
        == "invalid JSON"
    )
    mirrored = DesktopDecisionRecord.model_validate_json(record.model_dump_json())
    assert mirrored.input_preview is not None
    assert mirrored.input_preview.vehicles[0].memory_steps == 0
    assert mirrored.input_preview.vehicles[0].speed_mps == 8
    assert "input_preview" not in page.items[0].model_dump()
