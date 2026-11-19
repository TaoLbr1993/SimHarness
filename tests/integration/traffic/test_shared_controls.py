"""Regression checks for script intents translated into HighwayEnv dynamics."""

from pathlib import Path
from uuid import uuid4

import pytest

from trafficverse.adapters.highway_env.adapter import HighwayTrafficEngineAdapter
from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.enums import LaneChangeDirection
from trafficverse.domain.models import ControlCommand
from trafficverse.domain.models.shared_scene import (
    SceneConnection,
    SceneLane,
    SceneVehicle,
    SharedScene,
)

pytestmark = [pytest.mark.integration, pytest.mark.traffic]


def test_override_speed_applies_before_motion_and_acceleration_takes_precedence() -> None:
    scene = SharedScene(
        map_id="speed-command",
        lanes=(SceneLane(lane_id="road_0", edge_id="road", points=((0, 0), (1000, 0))),),
        connections=(),
        vehicles=(
            SceneVehicle(
                vehicle_id="car",
                route_id="route",
                edges=("road",),
                depart_s=0,
                depart_position_m=10,
                depart_speed="28",
            ),
        ),
    )
    adapter = HighwayTrafficEngineAdapter(uuid4())
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=2000))
        before = adapter.step(100).vehicles[0]
        adapter.apply_controls(
            {"car": ControlCommand(desired_speed_mps=8, safety_checks_override=True)}
        )
        slowed = adapter.step(200).vehicles[0]
        assert slowed.speed_mps == pytest.approx(8)
        assert slowed.position.x - before.position.x == pytest.approx(0.8)
        adapter.apply_controls(
            {
                "car": ControlCommand(
                    desired_speed_mps=25, desired_acceleration_mps2=-2, safety_checks_override=True
                )
            }
        )
        assert adapter.step(300).vehicles[0].speed_mps == pytest.approx(7.8)
        adapter.release_speed_controls(["car"])
        previous = adapter.step(400).vehicles[0]
        assert previous.speed_mps > 7.8
        for time_ms in range(500, 2001, 100):
            adapter.apply_controls(
                {"car": ControlCommand(desired_acceleration_mps2=-8, safety_checks_override=True)}
            )
            current = adapter.step(time_ms).vehicles[0]
            assert current.position.x >= previous.position.x
            previous = current
        assert current.speed_mps == 0
    finally:
        adapter.close()


@pytest.mark.parametrize("request_duration_s", [0.1, 1.5])
def test_blocked_safe_lane_change_keeps_braking_and_retries_until_gap_opens(
    request_duration_s: float,
) -> None:
    scene = SharedScene(
        map_id="blocked-lane-change",
        lanes=tuple(
            SceneLane(lane_id=f"road_{i}", edge_id="road", points=((0, i * 3.5), (1000, i * 3.5)))
            for i in range(2)
        ),
        connections=(),
        vehicles=tuple(
            SceneVehicle(
                vehicle_id=name,
                route_id="route",
                edges=("road",),
                depart_s=0,
                depart_lane=str(i),
                depart_position_m=20,
                depart_speed="10",
            )
            for i, name in enumerate(("car", "blocker"))
        ),
    )
    adapter = HighwayTrafficEngineAdapter(uuid4())
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=2000))
        adapter.step(100)
        rejected = adapter.apply_step_controls(
            {
                "car": ControlCommand(
                    desired_speed_mps=8,
                    safety_checks_override=True,
                    lane_change=LaneChangeDirection.LEFT,
                    lane_change_mode=512,
                    lane_change_duration_s=request_duration_s,
                )
            }
        )
        assert rejected == ()
        frame = adapter.step(200)
        car = next(v for v in frame.vehicles if v.vehicle_id == "car")
        assert car.speed_mps == pytest.approx(8)
        assert car.target_lane_id == "road_0"
        adapter.apply_controls(
            {"blocker": ControlCommand(desired_speed_mps=30, safety_checks_override=True)}
        )
        for time_ms in range(300, 1501, 100):
            frame = adapter.step(time_ms)
        car = next(v for v in frame.vehicles if v.vehicle_id == "car")
        assert car.target_lane_id == ("road_1" if request_duration_s > 1 else "road_0")
        assert not frame.collision_vehicle_ids
    finally:
        adapter.close()


def test_terminal_lane_does_not_choose_an_unrequested_outgoing_edge() -> None:
    from trafficverse.adapters.highway_env.shared_runtime import SharedHighwayRuntime

    scene = SharedScene(
        map_id="terminal-route",
        lanes=(
            SceneLane(lane_id="a_0", edge_id="a", points=((0, 0), (30, 0))),
            SceneLane(lane_id="b_0", edge_id="b", points=((31, 0), (100, 0))),
        ),
        connections=(SceneConnection(source_lane="a_0", target_lane="b_0"),),
        vehicles=(),
    )
    runtime = SharedHighwayRuntime(HighwayEnvConfig(shared_scene=scene))
    try:
        key = runtime.lane_indices["a_0"]
        route = [key]
        assert runtime.road.network.next_lane(key, route=route, position=[29, 0]) == key
        assert route == [key]
    finally:
        runtime.close()


def test_emergency_yield_full_minute_has_no_collisions() -> None:
    from trafficverse.controllers import controller_for_sumo_package
    from trafficverse.maps.shared_scene import convert_sumo_scene
    from trafficverse.maps.sumo_package import load_sumo_package

    root = Path(__file__).resolve().parents[3]
    name = "mixed-automation-emergency-yield"
    path = root / "configs/maps" / name / f"{name}.sumocfg"
    scene = convert_sumo_scene(load_sumo_package(path, allowed_root=path.parent, package_id=name))
    controller = controller_for_sumo_package(name)
    assert controller is not None
    adapter = HighwayTrafficEngineAdapter(uuid4())
    previous = None
    ambulance_positions: list[float] = []
    try:
        adapter.load(
            HighwayEnvConfig(
                shared_scene=scene,
                seed=scene.seed,
                duration_ms=60000,
                policy_frequency=20,
                simulation_frequency=20,
            )
        )
        for time_ms in range(50, 60001, 50):
            assert adapter.apply_step_controls(controller.step(previous, 0.05)) == ()
            frame = adapter.step(time_ms)
            assert not frame.collision_vehicle_ids, (time_ms, frame.collision_vehicle_ids)
            ambulance_positions.extend(
                v.position.x for v in frame.vehicles if v.vehicle_id == "ambulance_L5_0"
            )
            previous = frame
        assert frame.episode_ended
        assert max(ambulance_positions) - min(ambulance_positions) > 1000
    finally:
        adapter.close()


def test_safe_follower_stops_when_script_stops_its_leader() -> None:
    scene = SharedScene(
        map_id="leader-stop",
        lanes=(SceneLane(lane_id="road_0", edge_id="road", points=((0, 0), (1000, 0))),),
        connections=(),
        vehicles=tuple(
            SceneVehicle(
                vehicle_id=name,
                route_id="route",
                edges=("road",),
                depart_s=0,
                depart_position_m=position,
                depart_speed="28",
            )
            for name, position in (("front", 30), ("rear", 20))
        ),
    )
    adapter = HighwayTrafficEngineAdapter(uuid4())
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=2000))
        assert len(adapter.step(100).vehicles) == 2
        adapter.apply_controls(
            {"front": ControlCommand(stop_requested=True, safety_checks_override=True)}
        )
        for time_ms in range(200, 2001, 100):
            frame = adapter.step(time_ms)
            assert not frame.collision_vehicle_ids
        assert next(v for v in frame.vehicles if v.vehicle_id == "rear").speed_mps < 0.1
    finally:
        adapter.close()


def test_two_merge_approaches_yield_and_both_make_progress() -> None:
    scene = SharedScene(
        map_id="merge-reservation",
        lanes=(
            SceneLane(lane_id="a_0", edge_id="a", points=((0, -4), (30, -4))),
            SceneLane(lane_id="b_0", edge_id="b", points=((0, 4), (30, 4))),
            SceneLane(lane_id="c_0", edge_id="c", points=((40, 0), (150, 0))),
        ),
        connections=tuple(
            SceneConnection(source_lane=f"{edge}_0", target_lane="c_0") for edge in ("a", "b")
        ),
        vehicles=tuple(
            SceneVehicle(
                vehicle_id=edge,
                route_id=edge,
                edges=(edge, "c"),
                depart_s=0,
                depart_position_m=20,
                depart_speed="10",
                max_speed_mps=10,
            )
            for edge in ("a", "b")
        ),
    )
    adapter = HighwayTrafficEngineAdapter(uuid4())
    merged: set[str] = set()
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=20000))
        for time_ms in range(100, 20001, 100):
            frame = adapter.step(time_ms)
            assert not frame.collision_vehicle_ids, (time_ms, frame.collision_vehicle_ids)
            merged.update(v.vehicle_id for v in frame.vehicles if v.lane_id == "c_0")
        assert merged == {"a", "b"}
        assert not frame.vehicles
    finally:
        adapter.close()
