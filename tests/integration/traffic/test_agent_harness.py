import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from uuid import UUID

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from trafficverse.agent_harness.client import HttpSimulationClient
from trafficverse.agent_harness.config import Binding, HarnessConfig
from trafficverse.agent_harness.manager import AgentManager
from trafficverse.api.models import VehicleControlRequest
from trafficverse.bootstrap import build_core_api
from trafficverse.domain.models import TrafficSnapshot, VehicleState

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.integration, pytest.mark.traffic]


class TestClientTransport(HttpSimulationClient):
    __test__ = False

    def __init__(self, client: TestClient) -> None:
        super().__init__("http://testserver", 10)
        self.client = client
        self.submitted: list[int] = []

    def _request(self, path: str, payload: dict[str, object] | None = None) -> object:
        if path.endswith("/step") and payload is not None:
            actions = payload["actions"]
            assert isinstance(actions, list)
            self.submitted.append(len(actions))
        response = (
            self.client.get("/api/v1" + path)
            if payload is None
            else self.client.post("/api/v1" + path, json=payload)
        )
        response.raise_for_status()
        return response.json()


class TestAgent:
    __test__ = False

    def decide(
        self, snapshot: TrafficSnapshot, vehicles: tuple[VehicleState, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        del snapshot
        return tuple(
            VehicleControlRequest(vehicle_id=v.vehicle_id, desired_speed_mps=0) for v in vehicles
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
