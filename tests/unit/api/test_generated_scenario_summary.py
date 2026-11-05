"""Validate generated demand metadata before it enters the runnable catalog."""

import pytest
from pydantic import ValidationError

from trafficverse.api.models import GeneratedScenarioSummary


def _payload() -> dict[str, object]:
    return {
        "task_id": "20261104_100000_aabbcc",
        "description": "测试",
        "traffic_state": "moderate",
        "desired_density_veh_per_km_lane": 22,
        "planned_vehicles": 3,
        "duration_s": 600,
        "warmup_s": 120,
        "seed": 42,
    }


def test_legacy_metadata_does_not_invent_automation_allocation() -> None:
    result = GeneratedScenarioSummary.model_validate(_payload())
    assert result.simulation_mode == "traffic"
    assert result.automation_mix == []


def test_generated_integer_allocation_survives_api_and_ui_mirror() -> None:
    # UI consumes the serialized protocol only, never the Core object.
    from ui.models.protocol import GeneratedScenarioSummary as UiSummary

    payload = _payload()
    payload.update(
        simulation_mode="autonomy",
        automation_mix=[
            {"level": "L0", "vehicle_count": 2, "share_percent": 200 / 3},
            {"level": "L5", "vehicle_count": 1, "share_percent": 100 / 3},
        ],
    )
    core = GeneratedScenarioSummary.model_validate(payload)
    ui = UiSummary.model_validate_json(core.model_dump_json())
    assert ui.simulation_mode == "autonomy"
    assert sum(item.vehicle_count for item in ui.automation_mix) == 3
    assert ui.automation_mix[1].share_percent == pytest.approx(100 / 3)


@pytest.mark.parametrize(
    "mix",
    [
        [{"level": "L0", "vehicle_count": 2, "share_percent": 100}],
        [{"level": "L0", "vehicle_count": 3, "share_percent": 90}],
        [
            {"level": "L0", "vehicle_count": 2, "share_percent": 200 / 3},
            {"level": "L0", "vehicle_count": 1, "share_percent": 100 / 3},
        ],
        [{"level": "L6", "vehicle_count": 3, "share_percent": 100}],
    ],
)
def test_mismatched_or_duplicate_allocation_cannot_enter_catalog(mix: object) -> None:
    with pytest.raises(ValidationError):
        GeneratedScenarioSummary.model_validate({**_payload(), "automation_mix": mix})
