from uuid import UUID

import pytest
from pydantic import ValidationError

from trafficverse.api.models import ExternalLocalStepRequest
from trafficverse.application.vehicle_observation import local_observations
from trafficverse.domain.enums import AutomationLevel, VehicleAction
from trafficverse.domain.models import SimulationFrame, TrafficSnapshot, Vector3, VehicleState


def _vehicle(vehicle_id: str, x: float, y: float, z: float = 0) -> VehicleState:
    return VehicleState(
        experiment_id=UUID(int=1),
        vehicle_id=vehicle_id,
        simulation_time_ms=1000,
        sequence=4,
        automation_level=AutomationLevel.L0,
        position=Vector3(x=x, y=y, z=z),
        speed_mps=1,
        acceleration_mps2=0,
        heading_rad=0,
        lane_id="lane",
        controller_id="sumo",
        action=VehicleAction.KEEP_LANE,
        risk_score=0,
    )


def test_range_observations_are_local_inclusive_and_deterministic() -> None:
    vehicles = (
        _vehicle("ego", 0, 0),
        _vehicle("boundary", 3, 4, 100),
        _vehicle("outside", 5.01, 0),
        _vehicle("near", 1, 0),
    )
    frame = SimulationFrame(
        traffic=TrafficSnapshot(
            experiment_id=UUID(int=1),
            simulation_time_ms=1000,
            sequence=4,
            vehicles=vehicles,
        )
    )
    result = local_observations(frame, radius_m=5, vehicle_ids=("ego", "missing"))
    assert (result.experiment_id, result.sequence, result.simulation_time_ms) == (
        UUID(int=1),
        4,
        1000,
    )
    assert len(result.observations) == 1
    assert result.observations[0].ego.vehicle_id == "ego"
    assert result.observations[0].radius_m == 5
    assert [
        (n.vehicle.vehicle_id, n.distance_m) for n in result.observations[0].nearby_vehicles
    ] == [
        ("near", 1),
        ("boundary", 5),
    ]
    assert local_observations(frame, radius_m=5).observations == ()


@pytest.mark.parametrize("radius", [0, -1, float("inf"), float("nan")])
def test_invalid_radius_is_rejected_before_stepping(radius: float) -> None:
    with pytest.raises(ValidationError):
        ExternalLocalStepRequest(expected_sequence=0, radius_m=radius)
