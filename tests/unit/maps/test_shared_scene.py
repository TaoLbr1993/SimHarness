"""Shared inputs reject ambiguous traffic rules before starting an engine."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models.shared_scene import SceneLane, SceneVehicle, SharedScene
from trafficverse.maps.shared_scene import convert_sumo_scene, shared_highway_config
from trafficverse.maps.sumo_package import load_sumo_package

ROOT = Path(__file__).resolve().parents[3]


def test_shared_highway_clock_preserves_or_rejects_source_step() -> None:
    source = ROOT / "configs/maps/mixed-automation-obstacle/mixed-automation-obstacle.sumocfg"
    package = load_sumo_package(source, allowed_root=source.parent)

    assert shared_highway_config(replace(package, step_ms=125), 2000).step_ms == 125
    with pytest.raises(TrafficVerseError, match="400 ms"):
        shared_highway_config(replace(package, step_ms=400), 2000)


def test_empty_demand_is_valid_but_unknown_route_is_rejected() -> None:
    lane = SceneLane(lane_id="road_0", edge_id="road", points=((0, 0), (100, 0)))
    assert SharedScene(map_id="empty", lanes=(lane,), connections=(), vehicles=()).vehicles == ()
    with pytest.raises(ValidationError, match="unknown edge"):
        SharedScene(
            map_id="bad",
            lanes=(lane,),
            connections=(),
            vehicles=(
                SceneVehicle(vehicle_id="car", route_id="route", edges=("missing",), depart_s=0),
            ),
        )


def test_duplicate_vehicle_ids_are_rejected() -> None:
    lane = SceneLane(lane_id="road_0", edge_id="road", points=((0, 0), (100, 0)))
    car = SceneVehicle(vehicle_id="car", route_id="route", edges=("road",), depart_s=0)
    with pytest.raises(ValidationError, match="unique"):
        SharedScene(map_id="bad", lanes=(lane,), connections=(), vehicles=(car, car))


def test_adaptive_signal_is_rejected_before_runtime(tmp_path: Path) -> None:
    source = ROOT / "configs/maps/image2road/image2road.sumocfg"
    package = load_sumo_package(source, allowed_root=source.parent)
    original = package.network_path.read_text(encoding="utf-8")
    assert 'type="static"' in original
    network = tmp_path / "network.xml"
    network.write_text(original.replace('type="static"', 'type="actuated"'), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed-time"):
        convert_sumo_scene(replace(package, network_path=network))


def test_count_and_period_flow_preserves_timing_and_initial_layout_order() -> None:
    source = (
        ROOT
        / "configs/maps/mixed-automation-low-level-merge/mixed-automation-low-level-merge.sumocfg"
    )
    scene = convert_sumo_scene(load_sumo_package(source, allowed_root=source.parent))
    flow = [v for v in scene.vehicles if v.vehicle_id.startswith("merge_main_L0_lane0.")]
    assert len(flow) == 4
    assert [v.depart_s for v in flow] == pytest.approx([0, 4.8, 9.6, 14.4])
    assert scene.vehicles[0].vehicle_id == "merge_opposing_L0_lane0.100"
