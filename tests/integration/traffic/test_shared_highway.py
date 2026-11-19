"""Small real-engine checks for the shared scenario bridge."""

from pathlib import Path
from uuid import uuid4

import pytest

from trafficverse.adapters.highway_env.adapter import HighwayTrafficEngineAdapter
from trafficverse.config.models import HighwayEnvConfig
from trafficverse.controllers import controller_for_sumo_package
from trafficverse.domain.enums import LaneChangeDirection
from trafficverse.domain.models.shared_scene import (
    SceneConnection,
    SceneLane,
    SceneSignal,
    SceneStop,
    SceneVehicle,
    SharedScene,
)
from trafficverse.maps.shared_scene import convert_sumo_scene
from trafficverse.maps.sumo_package import load_sumo_package

pytestmark = [pytest.mark.integration, pytest.mark.traffic]
ROOT = Path(__file__).resolve().parents[3]
PACKAGES = sorted(
    p for p in (ROOT / "configs/maps").glob("*/*.sumocfg") if p.parent.name != "town04"
)


@pytest.mark.parametrize("path", PACKAGES, ids=lambda p: p.parent.name)
def test_existing_scene_keeps_ids_and_accepts_event_controls(path: Path) -> None:
    package = load_sumo_package(path, allowed_root=path.parent, package_id=path.parent.name)
    scene = convert_sumo_scene(package)
    adapter = HighwayTrafficEngineAdapter(uuid4())
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=1000))
        first = adapter.step(100)
        assert first.vehicles
        specs = {v.vehicle_id: v for v in scene.vehicles}
        for vehicle in first.vehicles:
            assert vehicle.vehicle_id in specs
            assert vehicle.route_id == specs[vehicle.vehicle_id].route_id
            assert vehicle.lane_id in {lane.lane_id for lane in scene.lanes}
        controller = controller_for_sumo_package(package.package_id)
        commands = controller.step(first, 0.1) if controller is not None else {}
        assert adapter.apply_step_controls(commands) == ()
        second = adapter.step(200)
        assert second.simulation_time_ms == 200
        assert len(second.traffic_lights) == sum(len(s.phases[0][1]) for s in scene.signals)
        if package.package_id == "mixed-automation-cutin":
            assert controller is not None
            # Exercise the event phase without an expensive multi-second traffic run.
            commands = controller.step(second.model_copy(update={"simulation_time_ms": 5000}), 0.1)
            assert any(command.takeover_requested for command in commands.values())
            assert any(
                command.lane_change is LaneChangeDirection.LEFT for command in commands.values()
            )
            assert adapter.apply_step_controls(commands) == ()
            changed = adapter.step(300)
            intruder = next(v for v in changed.vehicles if v.vehicle_id == "cutin_actor_L0_000")
            assert intruder.target_lane_id == "road_fwd_1"
    finally:
        adapter.close()
        adapter.close()


def test_signals_stops_late_departure_and_route_completion() -> None:
    scene = SharedScene(
        map_id="tiny-shared",
        lanes=(
            SceneLane(lane_id="a_0", edge_id="a", points=((0, 0), (30, 0))),
            SceneLane(lane_id="b_0", edge_id="b", points=((31, 0), (50, 0))),
        ),
        connections=(
            SceneConnection(
                source_lane="a_0", target_lane="b_0", signal_id="light", signal_index=0
            ),
        ),
        signals=(SceneSignal(signal_id="light", phases=((3, "r"), (100, "G"))),),
        vehicles=(
            SceneVehicle(
                vehicle_id="lead_L5_0",
                route_id="route",
                edges=("a", "b"),
                depart_s=0,
                depart_position_m=29,
                depart_speed="0",
                max_speed_mps=10,
                stops=(SceneStop(lane_id="b_0", position_m=10, duration_s=1),),
            ),
            SceneVehicle(
                vehicle_id="late",
                route_id="route",
                edges=("a", "b"),
                depart_s=8,
                depart_position_m=5,
            ),
        ),
    )
    adapter = HighwayTrafficEngineAdapter(uuid4())
    try:
        adapter.load(HighwayEnvConfig(shared_scene=scene, duration_ms=20000))
        stopped_frames = []
        for time_ms in range(100, 20001, 100):
            frame = adapter.step(time_ms)
            vehicles = {v.vehicle_id: v for v in frame.vehicles}
            if time_ms < 3000:
                assert frame.traffic_lights[0].phase == "r"
                assert vehicles["lead_L5_0"].position.x <= 27.6
            if time_ms < 8000:
                assert "late" not in vehicles
            if time_ms == 8100:
                assert "late" in vehicles
            lead = vehicles.get("lead_L5_0")
            if lead and lead.lane_id == "b_0" and lead.speed_mps < 0.2:
                stopped_frames.append(time_ms)
        assert len(stopped_frames) >= 8
        assert "lead_L5_0" not in vehicles
        assert frame.episode_ended
    finally:
        adapter.close()
