"""Dispatch saved configuration and isolated run inputs by scenario engine."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from trafficverse.domain.enums import ErrorCode, SimulationRunKind
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import (
    SimulationConfigurationDraft,
    SimulationConfigurationSnapshot,
    SimulationRunInput,
)
from trafficverse.maps.highway import HighwayScenario, load_highway_scenario
from trafficverse.maps.shared_scene import shared_highway_config
from trafficverse.maps.simulation_configuration import SumoSimulationConfigurationStore
from trafficverse.maps.sumo_display import sumo_display_geojson
from trafficverse.maps.sumo_package import SumoScenarioPackage, load_sumo_package


class EngineConfigurationStore(SumoSimulationConfigurationStore):
    """Preserve shared scenario inputs and resolve the selected traffic engine."""

    def __init__(
        self,
        *,
        highway_resolver: Callable[[str], HighwayScenario | None],
        highway_network: Callable[[HighwayScenario], dict[str, object]],
        package_resolver: Callable[[str], SumoScenarioPackage | None],
        configuration_root: Path,
        simulation_artifact_root: Path,
        test_artifact_root: Path,
    ) -> None:
        super().__init__(
            package_resolver=package_resolver,
            configuration_root=configuration_root,
            simulation_artifact_root=simulation_artifact_root,
            test_artifact_root=test_artifact_root,
        )
        self._highway_resolver = highway_resolver
        self._highway_network = highway_network

    def save(self, draft: SimulationConfigurationDraft) -> SimulationConfigurationSnapshot:
        package = self._highway_resolver(draft.map_id)
        if package is None and draft.engine == "highway":
            # Materialize exactly the same route/demand edits as SUMO, then convert
            # that immutable snapshot. Both engines consume the same source scenario.
            saved = super().save(draft)
            directory = self._configuration_directory(saved.configuration_id)
            try:
                metadata = self._read_metadata(directory)
                config_path = directory / self._metadata_text(metadata, "sumo", "config_file")
                source = load_sumo_package(
                    config_path, allowed_root=directory, package_id=draft.map_id
                )
                package = HighwayScenario(
                    map_id=draft.map_id,
                    display_name=saved.map_name,
                    config=shared_highway_config(source, draft.duration_ms),
                )
                (directory / "highway.json").write_text(
                    package.model_dump_json(indent=2), encoding="utf-8"
                )
                (directory / "network.geojson").write_text(
                    json.dumps(sumo_display_geojson(source.network_path)), encoding="utf-8"
                )
                metadata["engine"] = "highway"
                metadata["highway"] = {"config_file": "highway.json"}
                (directory / "configuration.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                return saved
            except Exception as error:
                shutil.rmtree(directory)
                if isinstance(error, TrafficVerseError):
                    raise
                raise TrafficVerseError(
                    ErrorCode.SCENARIO_VALIDATION_FAILED,
                    "Scenario conversion failed; use SUMO or check explicit routes, "
                    "departures and fixed-time signals",
                ) from error
        if package is None:
            return super().save(draft)
        if draft.automation_demands:
            raise TrafficVerseError(
                ErrorCode.SCENARIO_VALIDATION_FAILED,
                "HighwayEnv uses native IDM vehicles; L0-L5 demand overrides are unsupported",
            )
        try:
            config = package.config.model_validate(
                {**package.config.model_dump(), "duration_ms": draft.duration_ms}
            )
        except ValidationError as error:
            raise TrafficVerseError(
                ErrorCode.SCENARIO_VALIDATION_FAILED,
                "Invalid duration for this HighwayEnv preset",
                details={"duration_ms": str(error.errors()[0]["msg"])},
            ) from error
        package = package.model_copy(update={"config": config})
        # Validate optional SDK availability before creating a saved snapshot.
        network = self._highway_network(package)
        saved_at, destination = self._available_timestamp_directory(self._configuration_root)
        destination.mkdir(parents=True)
        try:
            (destination / "highway.json").write_text(
                package.model_dump_json(indent=2), encoding="utf-8"
            )
            (destination / "network.geojson").write_text(json.dumps(network), encoding="utf-8")
            metadata = {
                "schema_version": "1.0",
                "engine": "highway",
                "configuration_id": destination.name,
                "saved_at": saved_at.isoformat(),
                "workspace_id": str(draft.workspace_id),
                "scenario_id": str(draft.scenario_id),
                "scene": {"name": draft.scene_name, "description": draft.description},
                "map": {"id": package.map_id, "name": package.display_name},
                "simulation": {"duration_ms": draft.duration_ms},
                "traffic_demand": [],
                "highway": {"config_file": "highway.json"},
            }
            (destination / "configuration.json").write_text(json.dumps(metadata), encoding="utf-8")
        except Exception:
            shutil.rmtree(destination)
            raise
        return SimulationConfigurationSnapshot(
            configuration_id=destination.name,
            map_id=package.map_id,
            map_name=package.display_name,
            relative_directory=f"configs/configs/{destination.name}",
        )

    def prepare_run(
        self,
        configuration_id: str,
        run_kind: SimulationRunKind,
        workspace_id: UUID,
        scenario_id: UUID,
        map_id: str | None,
    ) -> SimulationRunInput:
        source = self._configuration_directory(configuration_id)
        metadata = self._read_metadata(source)
        if metadata.get("engine") != "highway":
            return super().prepare_run(
                configuration_id, run_kind, workspace_id, scenario_id, map_id
            )
        saved_map_id = self._metadata_text(metadata, "map", "id")
        if (
            metadata.get("workspace_id") != str(workspace_id)
            or metadata.get("scenario_id") != str(scenario_id)
            or (map_id is not None and map_id != saved_map_id)
        ):
            raise TrafficVerseError(
                ErrorCode.RESOURCE_CONFLICT,
                "saved HighwayEnv configuration does not match the requested context",
            )
        source_config = (source / "highway.json").resolve()
        self._require_within(source_config, source, "saved HighwayEnv configuration")
        package = load_highway_scenario(source_config)
        if package.map_id != saved_map_id:
            raise TrafficVerseError(
                ErrorCode.SCENARIO_VALIDATION_FAILED,
                "saved HighwayEnv scenario ID disagrees with metadata",
            )
        created_at, destination = self._available_timestamp_directory(
            self._artifact_roots[run_kind]
        )
        try:
            shutil.copytree(source, destination)
            self._write_run_metadata(
                destination,
                created_at=created_at,
                configuration_id=configuration_id,
                run_kind=run_kind,
            )
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            raise
        return SimulationRunInput(
            configuration_id=configuration_id,
            run_id=destination.name,
            run_kind=run_kind,
            workspace_id=workspace_id,
            scenario_id=scenario_id,
            map_id=saved_map_id,
            directory=destination,
            highway_config_path=destination / "highway.json",
        )
