import importlib.util
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from trafficverse.agent_harness import headless
from trafficverse.agent_harness.client import HttpSimulationClient
from trafficverse.agent_harness.headless import RecordingClient, load_headless_config
from trafficverse.api.models import ExternalLocalStepRequest
from trafficverse.domain.models.observation import LocalObservationFrame


def test_headless_config_validates_and_resolves_skills(tmp_path: Path) -> None:
    config_file = tmp_path / "headless.yaml"
    config_file.write_text(
        "map_id: image2road\nskills_directory: skills\nmax_steps: 3\nbindings: []\n",
        encoding="utf-8",
    )
    config = load_headless_config(config_file, "another-scenario")
    assert config.map_id == "another-scenario"
    assert config.skills_directory == tmp_path / "skills"
    assert config.max_steps == 3
    config_file.write_text("max_steps: 3\nbindings: []\n", encoding="utf-8")
    assert load_headless_config(config_file, "image2road").map_id == "image2road"
    config_file.write_text("map_id: image2road\nmax_steps: 0\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_headless_config(config_file)


def test_recording_client_saves_only_successful_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_id = UUID(int=123)
    frame = LocalObservationFrame(
        experiment_id=experiment_id, simulation_time_ms=1000, sequence=1, radius_m=100
    )
    request = ExternalLocalStepRequest(expected_sequence=0, radius_m=100)
    path = tmp_path / "steps.jsonl"
    client = RecordingClient("http://localhost", 1, path)

    def successful_step(
        self: HttpSimulationClient, experiment_id: UUID, request: ExternalLocalStepRequest
    ) -> LocalObservationFrame:
        return frame

    monkeypatch.setattr(HttpSimulationClient, "step", successful_step)
    assert client.step(experiment_id, request) == frame
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["request"]["expected_sequence"] == 0
    assert record["response"]["sequence"] == 1

    def failed_step(
        self: HttpSimulationClient, experiment_id: UUID, request: ExternalLocalStepRequest
    ) -> LocalObservationFrame:
        raise RuntimeError("step failed")

    monkeypatch.setattr(HttpSimulationClient, "step", failed_step)
    with pytest.raises(RuntimeError, match="step failed"):
        client.step(experiment_id, request)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_bundled_sumo_is_added_temporarily_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "original-path")
    binary_dir = tmp_path / "sumo" / "bin"
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(submodule_search_locations=[str(binary_dir.parent)]),
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, path=None: str(binary_dir / "sumo") if path == str(binary_dir) else None,
    )
    with pytest.raises(RuntimeError, match="interrupted"), headless._sumo_path():
        assert os.environ["PATH"].split(os.pathsep)[0] == str(binary_dir)
        raise RuntimeError("interrupted")
    assert os.environ["PATH"] == "original-path"
