"""Run an agent-controlled SUMO experiment without a desktop or existing server."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import uvicorn
import yaml
from pydantic import BaseModel, ConfigDict, Field

from trafficverse.agent_harness.__main__ import load_agent
from trafficverse.agent_harness.client import HttpSimulationClient
from trafficverse.agent_harness.config import Binding, HarnessConfig
from trafficverse.agent_harness.manager import AgentManager
from trafficverse.api.models import ExternalLocalStepRequest
from trafficverse.bootstrap import build_core_api
from trafficverse.domain.enums import RequirementMode
from trafficverse.domain.models.observation import LocalObservationFrame

logger = logging.getLogger(__name__)
_SCENARIO_ID = UUID("00000000-0000-0000-0000-000000000042")


class HeadlessConfig(BaseModel):
    """User-facing settings for a self-contained local run."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    map_id: str = Field(min_length=1)
    max_steps: int = Field(default=100, ge=1)
    step_delay_s: float = Field(default=0, ge=0, allow_inf_nan=False)
    request_timeout_s: float = Field(default=30, gt=0, allow_inf_nan=False)
    observation_radius_m: float = Field(default=100, gt=0, allow_inf_nan=False)
    skills_directory: Path | None = None
    bindings: tuple[Binding, ...] = ()


def load_headless_config(path: Path, map_override: str | None = None) -> HeadlessConfig:
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if map_override is not None and isinstance(raw, dict):
        raw = {**raw, "map_id": map_override}
    config = HeadlessConfig.model_validate(raw)
    if config.skills_directory is not None and not config.skills_directory.is_absolute():
        config = config.model_copy(
            update={"skills_directory": (path.resolve().parent / config.skills_directory).resolve()}
        )
    return config


@contextmanager
def _sumo_path() -> Iterator[None]:
    """Use a SUMO executable on PATH or in the optional Python SUMO package."""
    original = os.environ.get("PATH")
    if shutil.which("sumo") is None:
        spec = importlib.util.find_spec("sumo")
        locations = spec.submodule_search_locations if spec is not None else None
        binary_dir = next(
            (
                Path(location) / "bin"
                for location in locations or ()
                if shutil.which("sumo", path=str(Path(location) / "bin")) is not None
            ),
            None,
        )
        if binary_dir is None:
            raise ValueError("SUMO executable not found; install SUMO and add its bin to PATH")
        os.environ["PATH"] = str(binary_dir) + os.pathsep + (original or "")
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original


@contextmanager
def _local_api(repository_root: Path) -> Iterator[str]:
    app = build_core_api(
        repository_root / "configs/scenarios/core-run-town04.yaml",
        repository_root=repository_root,
        carla_mode=RequirementMode.DISABLED,
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("local simulation API failed to start")
                threading.Event().wait(0.05)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("local simulation API failed to stop")


class RecordingClient(HttpSimulationClient):
    """Record successful step requests and responses as JSON Lines."""

    def __init__(self, base_url: str, timeout_s: float, path: Path) -> None:
        super().__init__(base_url, timeout_s)
        self._path = path

    def step(self, experiment_id: UUID, request: ExternalLocalStepRequest) -> LocalObservationFrame:
        frame = super().step(experiment_id, request)
        record = {
            "request": request.model_dump(mode="json"),
            "response": frame.model_dump(mode="json"),
        }
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        return frame


def run_headless(
    config_path: Path,
    repository_root: Path,
    map_override: str | None = None,
    *,
    output_root: Path | None = None,
) -> int:
    config = load_headless_config(config_path, map_override)
    agents = {binding.name: load_agent(binding) for binding in config.bindings}
    run_dir = (output_root or repository_root / "artifacts/headless") / str(uuid4())
    run_dir.mkdir(parents=True)
    steps_path = run_dir / "steps.jsonl"
    steps_path.touch()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger.info("Headless run output: %s", run_dir)
    with _sumo_path(), _local_api(repository_root) as api_url:
        client = RecordingClient(api_url, config.request_timeout_s, steps_path)
        maps = client._request("/maps")
        if not isinstance(maps, list) or not any(
            isinstance(item, dict)
            and item.get("map_id") == config.map_id
            and item.get("kind") == "sumo"
            and item.get("validated") is True
            for item in maps
        ):
            raise ValueError(f"SUMO scenario is missing or invalid: {config.map_id}")
        workspaces = client._request("/workspaces")
        if not isinstance(workspaces, list) or not workspaces:
            raise RuntimeError("local simulation API returned no workspace")
        first = workspaces[0]
        if not isinstance(first, dict) or not isinstance(first.get("workspace_id"), str):
            raise RuntimeError("local simulation API returned an invalid workspace")
        harness = HarnessConfig(
            api_url=api_url,
            workspace_id=UUID(first["workspace_id"]),
            scenario_id=_SCENARIO_ID,
            map_id=config.map_id,
            max_steps=config.max_steps,
            step_delay_s=config.step_delay_s,
            request_timeout_s=config.request_timeout_s,
            observation_radius_m=config.observation_radius_m,
            skills_directory=config.skills_directory,
            bindings=config.bindings,
        )
        experiment_id = AgentManager(harness, client, agents).run()
        experiment = client.experiment(experiment_id)
        summary = {
            "experiment_id": str(experiment_id),
            "map_id": config.map_id,
            "status": experiment.status.value,
            "steps": experiment.latest_sequence,
            "simulation_time_ms": experiment.simulation_time_ms,
            "steps_file": str(steps_path.resolve()),
            "sumo_artifacts": str(
                (repository_root / "artifacts/sumo" / str(experiment_id)).resolve()
            ),
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
