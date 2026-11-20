"""Real HighwayEnv validation; no SUMO process or LLM service is involved."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from trafficverse.adapters.highway_env.adapter import HighwayTrafficEngineAdapter
from trafficverse.bootstrap import build_core_api
from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.enums import LaneChangeDirection
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import ControlCommand

pytestmark = [pytest.mark.integration, pytest.mark.traffic]
ROOT = Path(__file__).resolve().parents[3]


def test_seed_control_release_lane_change_and_episode_end() -> None:
    experiment_id = uuid4()
    config = HighwayEnvConfig(vehicles_count=0, initial_lane_id=1, duration_ms=2000)
    first = HighwayTrafficEngineAdapter(experiment_id)
    second = HighwayTrafficEngineAdapter(experiment_id)
    try:
        first.load(config)
        second.load(config)
        assert first.network_geojson() == second.network_geojson()
        assert first.step(100) == second.step(100)
        rejected = first.apply_step_controls(
            {
                "missing": ControlCommand(desired_speed_mps=0),
                "ego_0": ControlCommand(
                    desired_acceleration_mps2=-6, lane_change=LaneChangeDirection.LEFT
                ),
            }
        )
        assert rejected == ("missing",)
        slowed = first.step(200).vehicles[0]
        native = second.step(200).vehicles[0]
        assert slowed.speed_mps < native.speed_mps
        assert slowed.target_lane_id is not None
        assert slowed.target_lane_id.endswith(":0")
        assert first.apply_step_controls(
            {"ego_0": ControlCommand(desired_speed_mps=0, safety_checks_override=True)}
        ) == ("ego_0",)
        first.apply_controls({"ego_0": ControlCommand(desired_speed_mps=0)})
        assert first.step(300).vehicles[0].controller_id == "external"
        first.release_speed_controls(["ego_0"])
        assert first.step(400).vehicles[0].controller_id == "highway-idm"
        with pytest.raises(TrafficVerseError):
            first.step(600)
        for time_ms in range(500, 2001, 100):
            final = first.step(time_ms)
        assert final.episode_ended
        with pytest.raises(TrafficVerseError):
            first.step(2100)
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("map_id", ["highway-light", "mixed-automation-cutin"])
def test_api_saved_configuration_external_steps_history_and_replay(
    tmp_path: Path, map_id: str
) -> None:
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
        configuration_root=tmp_path / "configurations",
        simulation_artifact_root=tmp_path / "simulations",
        test_artifact_root=tmp_path / "tests",
    )
    with TestClient(app) as client:
        maps = client.get("/api/v1/maps", params={"include_engine_metadata": True}).json()
        assert any(item["kind"] == "sumo" for item in maps)
        assert any(item["map_id"] == "highway-light" for item in maps)
        workspace = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        saved = client.post(
            "/api/v1/simulation-configurations",
            json={
                "workspace_id": workspace,
                "scenario_id": str(UUID(int=42)),
                "map_id": map_id,
                "engine": "highway",
                "scene_name": "Highway regression",
                "duration_ms": 500,
                "automation_demands": [],
            },
        )
        assert saved.status_code == 201, saved.text
        config_id = saved.json()["configuration_id"]
        source = tmp_path / "configurations" / config_id / "highway.json"
        original = source.read_bytes()
        created = client.post(
            "/api/v1/experiments",
            json={
                "workspace_id": workspace,
                "scenario_id": str(UUID(int=42)),
                "map_id": map_id,
                "configuration_id": config_id,
                "external_stepping": True,
            },
        )
        assert created.status_code == 202, created.text
        root = "/api/v1/experiments/" + created.json()["experiment_id"]
        assert client.post(root + "/prepare").status_code == 200
        assert client.post(root + "/start").status_code == 200
        assert client.get(root).json()["simulation_time_ms"] == 0
        controlled_id = "ego_0"
        policy_step_ms = 100 if map_id == "highway-light" else 50
        for sequence in range(500 // policy_step_ms):
            response = client.post(
                root + "/step",
                json={
                    "expected_sequence": sequence,
                    "actions": []
                    if sequence == 0
                    else [{"vehicle_id": controlled_id, "desired_acceleration_mps2": -2}],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["traffic"]["sequence"] == sequence + 1
            controlled_id = response.json()["traffic"]["vehicles"][0]["vehicle_id"]
        assert client.get(root).json()["status"] == "COMPLETED"
        assert source.read_bytes() == original
    from trafficverse.adapters.persistence.simulation_history import FileSimulationHistoryStore

    store = FileSimulationHistoryStore(tmp_path / "simulations")
    run_id = next((tmp_path / "simulations").iterdir()).name
    assert store.get_network(run_id)["features"]
    replay = store.get_replay(run_id, from_time_ms=0, limit=100)
    assert replay.frames
    assert store.get_run(run_id).run_id == run_id
    assert store.get_run(run_id).simulation_time_ms == 500
