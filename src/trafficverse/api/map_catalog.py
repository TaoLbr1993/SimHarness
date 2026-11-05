"""Validated built-in and imported map publication service."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from pydantic import ValidationError

from trafficverse.api.models import GeneratedScenarioSummary, MapImportJob, MapSummary
from trafficverse.config.loader import load_map_manifest, validate_map_manifest
from trafficverse.config.models import MapManifest
from trafficverse.domain.enums import ErrorCode
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.maps.compiler import OpenDriveMapCompiler
from trafficverse.maps.errors import MapCompileError, SumoPackageError
from trafficverse.maps.sumo_display import sumo_display_geojson
from trafficverse.maps.sumo_package import SumoScenarioPackage, load_sumo_package

_SUMO_DISPLAY_SCHEMA = "sumo-net/display-1.0"


@dataclass(frozen=True, slots=True)
class _MapEntry:
    directory: Path
    manifest: MapManifest | None = None
    package: SumoScenarioPackage | None = None
    errors: tuple[str, ...] = ()
    config_path: Path | None = None
    generated_scenario: GeneratedScenarioSummary | None = None


def _validate_manifest(path: Path) -> MapManifest:
    manifest = load_map_manifest(path)
    return validate_map_manifest(
        path,
        expected_carla_version=manifest.carla_version,
        expected_network_schema_version=manifest.network_schema_version,
    )


class MapCatalog:
    def __init__(
        self,
        built_in_directories: tuple[Path, ...],
        *,
        artifact_root: Path,
        package_root: Path | None = None,
        maximum_upload_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        if maximum_upload_bytes <= 0:
            raise ValueError("maximum map upload size must be positive")
        self._artifact_root = artifact_root
        self._maximum_upload_bytes = maximum_upload_bytes
        self._package_root = package_root.resolve() if package_root is not None else None
        self._registered_directories: set[Path] = set()
        self._entries: dict[str, _MapEntry] = {}
        self._jobs: dict[UUID, MapImportJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        for directory in built_in_directories:
            self._register_directory(directory, package_root=package_root or directory)
            self._registered_directories.add(directory.resolve())

    @property
    def maximum_upload_bytes(self) -> int:
        return self._maximum_upload_bytes

    def list_maps(self) -> tuple[MapSummary, ...]:
        self._discover_new_packages()
        summaries = []
        for map_id, entry in sorted(self._entries.items()):
            if entry.manifest is not None:
                manifest = _validate_manifest(entry.directory / "manifest.yaml")
                summaries.append(
                    MapSummary(
                        map_id=map_id,
                        kind="core_run",
                        display_name=manifest.carla_map,
                        carla_map=manifest.carla_map,
                        carla_version=manifest.carla_version,
                        validated=manifest.validated,
                        network_schema_version=manifest.network_schema_version,
                        files=tuple(sorted(manifest.files)),
                    )
                )
                continue
            package = entry.package
            summaries.append(
                MapSummary(
                    map_id=map_id,
                    kind="sumo",
                    display_name=package.display_name if package is not None else map_id,
                    validated=package is not None and not entry.errors,
                    network_schema_version=_SUMO_DISPLAY_SCHEMA,
                    manifest_available=False,
                    sumo_config_file=(package.config_path.name if package is not None else None),
                    sumo_step_ms=package.step_ms if package is not None else None,
                    sumo_begin_time_ms=package.begin_time_ms if package is not None else 0,
                    sumo_end_time_ms=package.end_time_ms if package is not None else None,
                    files=(
                        package.files
                        if package is not None
                        else ((entry.config_path.name,) if entry.config_path is not None else ())
                    ),
                    validation_errors=entry.errors,
                    generated_scenario=entry.generated_scenario,
                )
            )
        return tuple(summaries)

    def _discover_new_packages(self) -> None:
        """Discover published native packages without rebuilding existing catalog entries."""
        root = self._package_root
        if root is None or not root.is_dir():
            return
        for directory in sorted(root.iterdir()):
            if directory.name.startswith(".") or not directory.is_dir():
                continue
            resolved = directory.resolve()
            if not resolved.is_relative_to(root) or resolved in self._registered_directories:
                continue
            # Core manifest assets retain their startup validation path. Empty/staging
            # directories are not registered, so later completed publication is discoverable.
            if (directory / "manifest.yaml").is_file() or not any(directory.glob("*.sumocfg")):
                continue
            self._register_directory(directory, package_root=root)
            self._registered_directories.add(resolved)

    def manifest(self, map_id: str) -> MapManifest:
        entry = self._require_entry(map_id)
        if entry.manifest is None:
            raise TrafficVerseError(
                ErrorCode.MAP_ASSET_INVALID,
                "native SUMO packages do not use a Town04 map manifest",
            )
        return _validate_manifest(entry.directory / "manifest.yaml")

    def directory(self, map_id: str) -> Path:
        return self._require_entry(map_id).directory

    def sumo_package(self, map_id: str) -> SumoScenarioPackage | None:
        entry = self._require_entry(map_id)
        if entry.errors:
            raise TrafficVerseError(
                ErrorCode.MAP_ASSET_INVALID,
                "SUMO scenario package is not runnable",
                details={"configuration": "; ".join(entry.errors)},
            )
        return entry.package

    def network_geojson(self, map_id: str) -> dict[str, object]:
        entry = self._require_entry(map_id)
        if entry.errors:
            raise TrafficVerseError(
                ErrorCode.MAP_ASSET_INVALID,
                "SUMO scenario package is not renderable",
                details={"configuration": "; ".join(entry.errors)},
            )
        if entry.package is not None:
            return sumo_display_geojson(entry.package.network_path)
        payload = json.loads((entry.directory / "network.geojson").read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TrafficVerseError(ErrorCode.MAP_ASSET_INVALID, "network GeoJSON is invalid")
        return payload

    async def import_opendrive(self, payload: bytes) -> MapImportJob:
        if not payload:
            raise TrafficVerseError(ErrorCode.MAP_ASSET_INVALID, "OpenDRIVE upload is empty")
        if len(payload) > self._maximum_upload_bytes:
            raise TrafficVerseError(
                ErrorCode.MAP_ASSET_INVALID,
                "OpenDRIVE upload exceeds the configured size limit",
            )
        job_id = uuid4()
        job = MapImportJob(job_id=job_id, status="PENDING")
        self._jobs[job_id] = job
        task = asyncio.create_task(self._compile(job_id, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def import_job(self, job_id: UUID) -> MapImportJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise TrafficVerseError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"map import job does not exist: {job_id}",
            )
        return job

    async def wait_for_job(self, job_id: UUID) -> MapImportJob:
        while self.import_job(job_id).status in {"PENDING", "RUNNING"}:
            await asyncio.sleep(0)
        return self.import_job(job_id)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _compile(self, job_id: UUID, payload: bytes) -> None:
        self._jobs[job_id] = self._jobs[job_id].model_copy(update={"status": "RUNNING"})
        map_id = f"imported-{job_id}"
        job_root = self._artifact_root / str(job_id)
        source = job_root / "staging" / "source.xodr"
        output = job_root / "published"
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(payload)
            compiler = OpenDriveMapCompiler()
            await asyncio.to_thread(
                compiler.compile,
                source,
                output,
                map_id=map_id,
            )
            _validate_manifest(output / "manifest.yaml")
            manifest = _validate_manifest(output / "manifest.yaml")
            self._entries[map_id] = _MapEntry(directory=output, manifest=manifest)
            self._jobs[job_id] = self._jobs[job_id].model_copy(
                update={"status": "SUCCEEDED", "map_id": map_id}
            )
        except (MapCompileError, OSError, ValueError, yaml.YAMLError) as error:
            self._jobs[job_id] = self._jobs[job_id].model_copy(
                update={
                    "status": "FAILED",
                    "error_code": ErrorCode.MAP_ASSET_INVALID.value,
                    "errors": (str(error),),
                }
            )

    def _register_directory(self, directory: Path, *, package_root: Path) -> None:
        manifest_path = directory / "manifest.yaml"
        if manifest_path.is_file():
            manifest = _validate_manifest(manifest_path)
            self._register_entry(manifest.map_id, _MapEntry(directory=directory, manifest=manifest))
            return
        config_paths = tuple(sorted(directory.glob("*.sumocfg")))
        multiple = len(config_paths) > 1
        for config_path in config_paths:
            map_id = (
                f"{directory.name}-{config_path.name.removesuffix('.sumocfg')}"
                if multiple
                else directory.name
            )
            try:
                package = load_sumo_package(
                    config_path,
                    allowed_root=package_root,
                    package_id=map_id,
                )
                generated_scenario = self._generated_scenario(directory)
                entry = _MapEntry(
                    directory=directory,
                    package=package,
                    config_path=config_path,
                    generated_scenario=generated_scenario,
                )
            except (SumoPackageError, OSError, ValueError) as error:
                entry = _MapEntry(
                    directory=directory,
                    errors=(str(error),),
                    config_path=config_path,
                )
            self._register_entry(map_id, entry)

    @staticmethod
    def _generated_scenario(directory: Path) -> GeneratedScenarioSummary | None:
        """Read optional draft parameters from the package's selected manifest only."""
        manifest_path = next(iter(sorted(directory.glob("*.manifest.json"))), None)
        if manifest_path is None:
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = payload.get("spec") if isinstance(payload, dict) else None
        if not isinstance(spec, dict) or "nl2map" not in spec:
            return None
        try:
            return GeneratedScenarioSummary.model_validate(spec["nl2map"])
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            raise SumoPackageError(f"invalid NL2MAP scenario metadata: {details}") from error

    def _register_entry(self, map_id: str, entry: _MapEntry) -> None:
        if map_id in self._entries:
            raise ValueError(f"duplicate map or SUMO package id: {map_id}")
        self._entries[map_id] = entry

    def _require_entry(self, map_id: str) -> _MapEntry:
        entry = self._entries.get(map_id)
        if entry is None:
            raise TrafficVerseError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"map does not exist: {map_id}",
            )
        return entry
