"""Real SUMO and a local deterministic HTTP model, without paid model requests."""

import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from trafficverse.bootstrap import build_core_api

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.integration, pytest.mark.traffic]


@pytest.mark.skipif(
    os.getenv("TRAFFICVERSE_SUMO_PACKAGE_INTEGRATION") != "1" or shutil.which("sumo") is None,
    reason="requires real SUMO",
)
def test_desktop_session_uses_http_model_memory_skill_and_real_sumo(tmp_path: Path) -> None:
    requests = []

    class ModelServer(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state = json.loads(payload["messages"][1]["content"])
            requests.append(state)
            content = (
                "invalid response"
                if len(requests) == 2
                else json.dumps(
                    {
                        "actions": [
                            {"vehicle_id": observation["ego"]["vehicle_id"], "desired_speed_mps": 0}
                            for observation in state["observations"]
                        ]
                    }
                )
            )
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelServer)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    app = build_core_api(
        ROOT / "configs/scenarios/core-run-town04.yaml",
        repository_root=ROOT,
        artifact_root=tmp_path / "maps",
        sumo_artifact_root=tmp_path / "sumo",
        configuration_root=tmp_path / "configs",
        simulation_artifact_root=tmp_path / "simulations",
        test_artifact_root=tmp_path / "tests",
    )
    try:
        with TestClient(app) as client:
            workspace = client.get("/api/v1/workspaces").json()[0]["workspace_id"]
            created = client.post(
                "/api/v1/experiments",
                json={
                    "workspace_id": workspace,
                    "scenario_id": str(UUID(int=42)),
                    "map_id": "image2road",
                    "external_stepping": True,
                },
            )
            assert created.status_code == 202, created.text
            root = "/api/v1/experiments/" + created.json()["experiment_id"]
            assert client.post(root + "/prepare").status_code == 200
            assert client.post(root + "/start").status_code == 200
            config = {
                "max_steps": 4,
                "bindings": [
                    {
                        "name": "local-test",
                        "kind": "local",
                        "all_vehicles": True,
                        "model": {
                            "base_url": f"http://127.0.0.1:{model.server_port}/v1",
                            "model_id": "deterministic-test",
                            "timeout_s": 2,
                        },
                        "memory": {"enabled": True, "max_steps": 2},
                        "skills": ["human_normal"],
                    }
                ],
            }
            checked = client.post(root + "/agent-session/check", json=config)
            assert checked.status_code == 200 and checked.json()["ready"]
            assert requests == []
            started = client.post(root + "/agent-session/start", json=config)
            assert started.status_code == 200, started.text

            def wait_paused() -> dict:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    view = client.get(root + "/agent-session").json()
                    if view["status"] not in {"running", "pausing"}:
                        assert view["status"] == "paused", view
                        return view
                    time.sleep(0.02)
                raise AssertionError("session did not pause at its frame budget")

            view = wait_paused()
            assert view["sequence"] == 4 and view["steps_completed"] == 4
            assert len(requests) == 3
            ego = requests[0]["observations"][0]["ego"]
            speeds = [
                next(
                    o["ego"]["speed_mps"]
                    for o in state["observations"]
                    if o["ego"]["vehicle_id"] == ego["vehicle_id"]
                )
                for state in requests
            ]
            # SUMO respects braking limits; a zero-speed request is not an instant stop.
            assert speeds[1] < speeds[0]
            assert speeds[2] > speeds[1]
            assert requests[0]["vehicle_contexts"][0]["skills"][0]["name"] == "human_normal"
            assert requests[1]["vehicle_contexts"][0]["memory"]
            assert requests[2]["vehicle_contexts"][0]["memory"][-1]["requested_action"] is None
            history = client.get(root + "/agent-session/decisions", params={"limit": 2}).json()
            assert history["total"] == 3
            assert [r["record_id"] for r in history["items"]] == [3, 2]
            assert all(r["outcome"] == "submitted" for r in history["items"])
            detail = client.get(
                root + "/agent-session/decisions/2", params={"session_id": history["session_id"]}
            ).json()
            assert "invalid response" in detail["output_text"]
            assert detail["actions_text"] == "[]" and detail["fallback_count"] > 0
            assert "human_normal" in detail["input_text"]
            assert "messages" in detail["request_text"]
            preview = detail["input_preview"]["vehicles"][0]
            assert preview["vehicle_id"] == ego["vehicle_id"]
            assert preview["memory_steps"] == 1
            assert preview["skills"][0]["name"] == "human_normal"
            older = client.get(
                root + "/agent-session/decisions", params={"before_id": history["next_before_id"]}
            ).json()
            assert [r["record_id"] for r in older["items"]] == [1]
            assert (
                client.get(
                    root + "/agent-session/decisions/2", params={"session_id": str(UUID(int=0))}
                ).status_code
                == 404
            )
            assert (
                client.post(
                    root + "/step", json={"expected_sequence": 4, "actions": []}
                ).status_code
                == 409
            )
            assert client.post(root + "/agent-session/stop").status_code == 200
            assert client.get(root).json()["status"] == "RUNNING"
            assert (
                client.post(
                    root + "/step", json={"expected_sequence": 4, "actions": []}
                ).status_code
                == 200
            )
            config["max_steps"] = 2
            assert client.post(root + "/agent-session/start", json=config).status_code == 200
            assert wait_paused()["sequence"] == 7
            assert client.post(root + "/agent-session/stop").status_code == 200
            assert client.post(root + "/stop", json={}).status_code == 200
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=3)
