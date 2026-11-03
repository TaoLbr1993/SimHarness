import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from uuid import UUID

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from trafficverse.agent_harness.client import HttpSimulationClient
from trafficverse.agent_harness.config import Binding, HarnessConfig, MemoryConfig, ModelConfig
from trafficverse.agent_harness.manager import AgentManager
from trafficverse.agent_harness.models.api import ApiModel
from trafficverse.api.models import VehicleControlRequest
from trafficverse.bootstrap import build_core_api
from trafficverse.domain.models.observation import VehicleObservation

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.integration, pytest.mark.traffic]


class TestClientTransport(HttpSimulationClient):
    __test__ = False

    def __init__(self, client: TestClient) -> None:
        super().__init__("http://testserver", 10)
        self.client = client
        self.submitted: list[int] = []
        self.local_frames: list[dict[str, object]] = []

    def _request(self, path: str, payload: dict[str, object] | None = None) -> object:
        if path.endswith("/local-step") and payload is not None:
            actions = payload["actions"]
            assert isinstance(actions, list)
            self.submitted.append(len(actions))
        response = (
            self.client.get("/api/v1" + path)
            if payload is None
            else self.client.post("/api/v1" + path, json=payload)
        )
        response.raise_for_status()
        result = response.json()
        if path.endswith("/local-step"):
            self.local_frames.append(result)
        return result


class TestAgent:
    __test__ = False

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        return tuple(
            VehicleControlRequest(vehicle_id=v.ego.vehicle_id, desired_speed_mps=0)
            for v in observations
        )


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_agent_attaches_to_desktop_experiment(tmp_path: Path) -> None:
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
        configuration_root=tmp_path / "configs",
        simulation_artifact_root=tmp_path / "simulations",
        test_artifact_root=tmp_path / "tests",
    )
    with TestClient(app) as client:
        workspace = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
        saved = client.post(
            "/api/v1/simulation-configurations",
            json={
                "workspace_id": workspace,
                "scenario_id": str(UUID(int=42)),
                "scene_name": "Agent integration",
                "description": "",
                "map_id": "image2road",
                "duration_ms": 60000,
                "automation_demands": [],
            },
        )
        assert saved.status_code == 201, saved.text
        created = client.post(
            "/api/v1/experiments",
            json={
                "workspace_id": workspace,
                "scenario_id": str(UUID(int=42)),
                "map_id": "image2road",
                "configuration_id": saved.json()["configuration_id"],
                "external_stepping": True,
            },
        )
        assert created.status_code == 202
        experiment_id = UUID(created.json()["experiment_id"])
        root = f"/api/v1/experiments/{experiment_id}"
        assert client.post(root + "/prepare").status_code == 200
        assert client.post(root + "/start").status_code == 200
        config = HarnessConfig(experiment_id=experiment_id, max_steps=3)
        transport = TestClientTransport(client)
        with client.websocket_connect(f"/api/v1/ws?experiment_id={experiment_id}") as websocket:
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json(
                {
                    "schema_version": "1.0",
                    "type": "subscribe",
                    "message_id": "subscribe",
                    "experiment_id": str(experiment_id),
                    "simulation_time_ms": 0,
                    "sequence": 0,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "payload": {"topics": ["vehicles"], "max_hz": 20},
                }
            )
            assert websocket.receive_json()["type"] == "command.accepted"
            assert AgentManager(config, transport, {}).run() == experiment_id
            message = websocket.receive_json()
            assert message["type"] == "vehicle.delta"
            assert message["payload"]["vehicles"]
        view = client.get(root).json()
        assert view["status"] == "RUNNING"
        assert view["latest_sequence"] == 3
        invalid = client.post(root + "/local-step", json={"expected_sequence": 3, "radius_m": 0})
        assert invalid.status_code == 422
        stale = client.post(root + "/local-step", json={"expected_sequence": 2, "radius_m": 100})
        assert stale.status_code == 409
        assert client.get(root).json()["latest_sequence"] == 3
        with pytest.raises(ValueError, match="fresh"):
            AgentManager(config, transport, {}).run()
        assert client.post(root + "/stop", json={}).status_code == 200


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_manager_drives_real_sumo_with_test_agent(tmp_path: Path) -> None:
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
    )
    with TestClient(app) as client:
        workspace = UUID(client.get("/api/v1/workspaces").json()[0]["workspace_id"])
        binding = Binding(
            name="test",
            wrapper="test:Agent",
            automation_levels=("L0", "L1", "L2", "L3", "L4", "L5"),
        )
        config = HarnessConfig(
            workspace_id=workspace,
            scenario_id=UUID(int=42),
            map_id="image2road",
            max_steps=3,
            bindings=(binding,),
        )
        transport = TestClientTransport(client)
        experiment_id = AgentManager(config, transport, {"test": TestAgent()}).run()
        assert transport.submitted[0] == 0
        assert transport.submitted[1] > 0
        assert len(transport.submitted) == 3
        first_frame = transport.local_frames[0]
        assert first_frame["sequence"] == 1
        assert "traffic" not in first_frame
        observations = first_frame["observations"]
        assert isinstance(observations, list) and observations
        assert all(
            neighbor["distance_m"] <= config.observation_radius_m
            for observation in observations
            for neighbor in observation["nearby_vehicles"]
        )
        assert client.get(f"/api/v1/experiments/{experiment_id}").json()["status"] == "COMPLETED"


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_invalid_model_output_falls_back_to_real_sumo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    call_count = 0
    seen_contexts: list[list[dict[str, Any]]] = []

    def chat_response(_config: ModelConfig, _path: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        state = json.loads(payload["messages"][1]["content"])
        observations = state["observations"]
        seen_contexts.append(state["vehicle_contexts"])
        vehicle_id = observations[0]["ego"]["vehicle_id"]
        output = (
            json.dumps({"actions": [{"vehicle_id": vehicle_id, "desired_speed_mps": 0}]})
            if call_count == 1
            else "invalid model output"
        )
        return {"choices": [{"message": {"content": output}}]}

    monkeypatch.setattr("trafficverse.agent_harness.models.http_chat.post_json", chat_response)
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
    )
    with TestClient(app) as client:
        workspace = UUID(client.get("/api/v1/workspaces").json()[0]["workspace_id"])
        binding = Binding(
            name="test",
            wrapper="test:Model",
            automation_levels=("L0", "L1", "L2", "L3", "L4", "L5"),
            memory=MemoryConfig(enabled=True, max_steps=2),
            skills=("human_cautious",),
        )
        config = HarnessConfig(
            workspace_id=workspace,
            scenario_id=UUID(int=42),
            map_id="image2road",
            max_steps=4,
            bindings=(binding,),
        )
        transport = TestClientTransport(client)
        model = ApiModel(ModelConfig(base_url="http://localhost:8001/v1", model_id="mock"))
        experiment_id = AgentManager(config, transport, {"test": model}).run()
        assert transport.submitted == [0, 1, 0, 0]
        first = seen_contexts[0][0]
        second = next(
            item for item in seen_contexts[1] if item["vehicle_id"] == first["vehicle_id"]
        )
        assert first["memory"] == []
        assert first["skills"][0]["name"] == "human_cautious"
        assert len(second["memory"]) == 1
        assert second["memory"][0]["requested_action"]["desired_speed_mps"] == 0
        assert second["memory"][0]["next_state"]["vehicle_id"] == first["vehicle_id"]
        assert client.get(f"/api/v1/experiments/{experiment_id}").json()["status"] == "COMPLETED"


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_cli_runs_against_http_server_and_real_sumo(tmp_path: Path) -> None:
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.05)
        import json

        with urlopen(f"http://127.0.0.1:{port}/api/v1/workspaces", timeout=5) as response:
            workspace = json.load(response)[0]["workspace_id"]
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump(
                {
                    "api_url": f"http://127.0.0.1:{port}",
                    "workspace_id": workspace,
                    "scenario_id": str(UUID(int=42)),
                    "map_id": "image2road",
                    "max_steps": 3,
                    "bindings": [],
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-m", "trafficverse.agent_harness", "--config", str(config_file)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Step 3" in result.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_headless_run_records_agent_actions_and_sumo_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from trafficverse.agent_harness import headless

    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "map_id": "image2road",
                "max_steps": 3,
                "bindings": [
                    {
                        "name": "test",
                        "wrapper": "test:Agent",
                        "automation_levels": ["L0", "L1", "L2", "L3", "L4", "L5"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(headless, "load_agent", lambda binding: TestAgent())
    assert headless.run_headless(config_path, ROOT, output_root=tmp_path / "output") == 0
    summary_file = next((tmp_path / "output").glob("*/summary.json"))
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in summary_file.with_name("steps.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["status"] == "COMPLETED"
    assert summary["steps"] == len(records) == 3
    assert records[0]["response"]["observations"]
    assert records[1]["request"]["actions"]
    assert records[1]["response"]["observations"]
    assert records[1]["response"]["sequence"] == 2


def test_headless_rejects_unknown_sumo_scenario(tmp_path: Path) -> None:
    from trafficverse.agent_harness.headless import run_headless

    config = tmp_path / "run.yaml"
    config.write_text("map_id: no-such-scenario\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SUMO scenario is missing or invalid"):
        run_headless(config, ROOT, output_root=tmp_path / "output")
