from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from trafficverse.adapters.messaging import FrameBroker
from trafficverse.adapters.persistence import InMemoryWorkspaceRepository
from trafficverse.api import ApiDependencies, RuntimeDirectory, create_app
from trafficverse.api.command_bus import ExperimentCommandBus
from trafficverse.api.map_catalog import MapCatalog
from trafficverse.api.models import ReadinessComponent
from trafficverse.application.workspace_service import WorkspaceService
from trafficverse.domain.enums import ErrorCode
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.maps.sumo_package import load_sumo_package

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MAP_DIRECTORY = REPOSITORY_ROOT / "configs/maps/town04"


def test_catalog_publishes_builtin_map_and_geojson(tmp_path: Path) -> None:
    catalog = MapCatalog((MAP_DIRECTORY,), artifact_root=tmp_path)

    maps = catalog.list_maps()

    assert len(maps) == 1
    assert maps[0].validated
    assert catalog.network_geojson(maps[0].map_id)["type"] == "FeatureCollection"


def test_town04_upload_compiles_as_async_job(tmp_path: Path) -> None:
    async def exercise() -> None:
        catalog = MapCatalog((MAP_DIRECTORY,), artifact_root=tmp_path)
        job = await catalog.import_opendrive((MAP_DIRECTORY / "Town04.xodr").read_bytes())

        completed = await catalog.wait_for_job(job.job_id)

        assert completed.status == "SUCCEEDED"
        assert completed.map_id is not None
        assert catalog.manifest(completed.map_id).validated
        await catalog.close()

    asyncio.run(exercise())


def test_invalid_upload_has_structured_failed_job(tmp_path: Path) -> None:
    async def exercise() -> None:
        catalog = MapCatalog((MAP_DIRECTORY,), artifact_root=tmp_path)
        job = await catalog.import_opendrive(b"<not-opendrive />")

        completed = await catalog.wait_for_job(job.job_id)

        assert completed.status == "FAILED"
        assert completed.error_code == "MAP_ASSET_INVALID"
        assert completed.errors
        await catalog.close()

    asyncio.run(exercise())


def _sumo_package(directory: Path, *, missing_network: bool = False) -> None:
    directory.mkdir()
    if not missing_network:
        (directory / "scene.net.xml").write_text(
            """<net>
  <edge id="a"><lane id="a_0" speed="10" shape="0,0 10,0"/></edge>
  <edge id="b"><lane id="b_0" speed="10" shape="11,1 20,1"/></edge>
  <connection from="a" to="b" fromLane="0" toLane="0" tl="j" linkIndex="0"/>
</net>\n""",
            encoding="utf-8",
        )
    (directory / "scene.rou.xml").write_text("<routes/>\n", encoding="utf-8")
    (directory / "scene.sumocfg").write_text(
        """<configuration><input>
  <net-file value="scene.net.xml"/><route-files value="scene.rou.xml"/>
</input><time><step-length value="0.5"/><end value="10"/></time></configuration>\n""",
        encoding="utf-8",
    )


def test_catalog_discovers_native_sumo_package_and_generates_geojson(tmp_path: Path) -> None:
    directory = tmp_path / "direct-sumo"
    _sumo_package(directory)
    catalog = MapCatalog((directory,), artifact_root=tmp_path / "artifacts", package_root=tmp_path)

    summary = catalog.list_maps()[0]
    package = catalog.sumo_package(summary.map_id)
    network = catalog.network_geojson(summary.map_id)

    assert summary.kind == "sumo"
    assert summary.map_id == "direct-sumo"
    assert summary.sumo_step_ms == 500
    assert not summary.manifest_available
    assert package is not None and package.config_path.name == "scene.sumocfg"
    assert network["type"] == "FeatureCollection"
    features = cast("list[dict[str, object]]", network["features"])
    assert any(
        cast("dict[str, object]", feature.get("properties", {})).get("signal_id") == "sumo-tls:j:0"
        for feature in features
    )


def test_invalid_sumo_package_does_not_block_other_catalog_entries(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    invalid = tmp_path / "invalid"
    _sumo_package(valid)
    _sumo_package(invalid, missing_network=True)
    catalog = MapCatalog(
        (invalid, valid),
        artifact_root=tmp_path / "artifacts",
        package_root=tmp_path,
    )

    summaries = {item.map_id: item for item in catalog.list_maps()}

    assert summaries["valid"].validated
    assert not summaries["invalid"].validated
    assert summaries["invalid"].validation_errors
    with pytest.raises(TrafficVerseError) as captured:
        catalog.sumo_package("invalid")
    assert captured.value.code is ErrorCode.MAP_ASSET_INVALID


def _generated_metadata() -> dict[str, object]:
    return {
        "task_id": "20261104_000000_aabbcc",
        "description": "希望测试拥堵场景下车辆速度",
        "traffic_state": "congested",
        "desired_density_veh_per_km_lane": 65,
        "planned_vehicles": 1300,
        "duration_s": 600,
        "warmup_s": 120,
        "seed": 42,
        "inflow_veh_per_hour_per_lane": 1950,
        "unverified_requirements": [],
    }


def _write_generated_manifest(
    directory: Path, metadata: dict[str, object], *, metadata_key: str = "trafficforge"
) -> None:
    (directory / "scenario.manifest.json").write_text(
        json.dumps(
            {
                "spec": {
                    "name": "拥堵车速场景",
                    "trafficDemandMode": "scripted",
                    metadata_key: metadata,
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("metadata_key", ["trafficforge", "nl2map"])
def test_maps_api_discovers_new_published_scene_without_restarting(
    tmp_path: Path, metadata_key: str
) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    existing = root / "existing"
    _sumo_package(existing)
    catalog = MapCatalog((existing,), artifact_root=tmp_path / "artifacts", package_root=root)
    runtimes = RuntimeDirectory()

    async def readiness() -> tuple[ReadinessComponent, ...]:
        return ()

    dependencies = ApiDependencies(
        runtimes=runtimes,
        maps=catalog,
        commands=ExperimentCommandBus(runtimes),
        broker=FrameBroker(),
        readiness=readiness,
        workspaces=WorkspaceService(InMemoryWorkspaceRepository(initial=())),
    )
    with TestClient(create_app(dependencies)) as client:
        assert len(client.get("/api/v1/maps").json()) == 1
        published = root / "nl2-20261104_000000_aabbcc"
        _sumo_package(published)
        _write_generated_manifest(published, _generated_metadata(), metadata_key=metadata_key)
        with patch(
            "trafficverse.api.map_catalog.load_sumo_package", wraps=load_sumo_package
        ) as load:
            response = client.get("/api/v1/maps")
            repeated = client.get("/api/v1/maps")
        assert response.status_code == 200
        assert repeated.json() == response.json()
        assert load.call_count == 1
        rows = {row["map_id"]: row for row in response.json()}
        scene = rows[published.name]
        assert scene["validated"]
        assert scene["display_name"] == "拥堵车速场景"
        assert scene["generated_scenario"] == {
            **_generated_metadata(),
            "simulation_mode": "traffic",
            "automation_mix": [],
        }
        assert rows[existing.name]["generated_scenario"] is None
        assert "metrics" not in scene["generated_scenario"]


def test_catalog_scans_only_nonhidden_direct_children_of_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    _sumo_package(tmp_path / "outside")
    _sumo_package(root / ".publishing")
    nested = root / "container"
    nested.mkdir()
    _sumo_package(nested / "nested-package")
    catalog = MapCatalog((), artifact_root=tmp_path / "artifacts", package_root=root)
    assert catalog.list_maps() == ()
    _sumo_package(root / "published")
    assert [row.map_id for row in catalog.list_maps()] == ["published"]


@pytest.mark.parametrize(
    "changes",
    [
        {"desired_density_veh_per_km_lane": float("nan")},
        {"planned_vehicles": 0},
        {"warmup_s": 600},
        {"description": "x" * 8001},
        {"metrics": {"mean_speed_kmh": 7}},
    ],
)
def test_invalid_generated_metadata_is_visible_and_blocks_run(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    directory = tmp_path / "invalid-generated"
    _sumo_package(directory)
    _write_generated_manifest(directory, {**_generated_metadata(), **changes})
    catalog = MapCatalog((), artifact_root=tmp_path / "artifacts", package_root=tmp_path)

    summary = catalog.list_maps()[0]

    assert not summary.validated
    assert summary.generated_scenario is None
    assert "invalid TrafficForge scenario metadata" in summary.validation_errors[0]
    with pytest.raises(TrafficVerseError) as captured:
        catalog.sumo_package(directory.name)
    assert captured.value.code is ErrorCode.MAP_ASSET_INVALID


def test_trafficforge_metadata_takes_precedence_and_cannot_fall_back_when_invalid(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "renamed-scene"
    _sumo_package(directory)
    metadata = _generated_metadata()
    manifest = directory / "scenario.manifest.json"
    manifest.write_text(
        json.dumps(
            {"spec": {"trafficforge": {**metadata, "planned_vehicles": 25}, "nl2map": metadata}}
        ),
        encoding="utf-8",
    )
    summary = MapCatalog._generated_scenario(directory)
    assert summary is not None and summary.planned_vehicles == 25
    manifest.write_text(
        json.dumps({"spec": {"trafficforge": None, "nl2map": metadata}}), encoding="utf-8"
    )
    catalog = MapCatalog((), artifact_root=tmp_path / "artifacts", package_root=tmp_path)
    assert not catalog.list_maps()[0].validated
