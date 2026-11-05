"""Refresh legacy localized previews while preserving all authored map and scenario data."""

import json
import tempfile
from pathlib import Path
from typing import Literal

import config
import renderer
from preview_location import MapOriginMarker
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_RENDERER_VERSION = "english-place-and-road-labels-v1"
_LANGUAGES: dict[str, Literal["en", "zh"]] = {
    "map_preview.en.png": "en",
    "map_preview.zh.png": "zh",
}


class _Place(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(gt=-85, lt=85, allow_inf_nan=False)


class _DisplayMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_mode: Literal["real_map"]
    task_id: str = Field(pattern=r"^\d{8}_\d{6}_[0-9a-f]{6}$")
    place: _Place
    radius_m: float = Field(gt=0, allow_inf_nan=False)
    detail_level: Literal["main_roads", "detailed"] = "main_roads"


class _CacheEntry(BaseModel):
    version: str
    inputs: dict[str, tuple[int, int]]
    preview: tuple[int, int]


def _path(directory: Path, filename: str) -> Path:
    """Reject escaping symlinks even when the referenced target does not exist."""
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise ValueError("Preview source or output is outside its allowed directory")
    return path


def _metadata(output: Path) -> tuple[Path, _DisplayMetadata] | None:
    for filename in ("source_map_metadata.json", "metadata.json"):
        path = _path(output, filename)
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("metadata must be an object")
            if payload.get("source_mode") != "real_map":
                continue
            metadata = _DisplayMetadata.model_validate(payload)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid real-map metadata; cannot refresh the preview") from exc
        return path, metadata
    return None


def _osm_source(output: Path, source_task_id: str) -> Path | None:
    local = _path(output, "source.osm.xml")
    if local.is_file():
        return local
    workspace = Path(config.WORKSPACES_DIR).resolve()
    source_dir = (workspace / source_task_id / "output").resolve()
    if not source_dir.is_relative_to(workspace) or source_dir == workspace:
        raise ValueError("Preview source map is outside the allowed workspace")
    source = _path(source_dir, "source.osm.xml")
    return source if source.is_file() else None


def _signature(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def _cached(stamp: Path, image: Path, inputs: dict[str, tuple[int, int]]) -> bool:
    if not stamp.is_file() or not image.is_file():
        return False
    try:
        cache = _CacheEntry.model_validate_json(stamp.read_text(encoding="utf-8"))
    except (ValidationError, UnicodeError):
        return False
    return (
        cache.version == _RENDERER_VERSION
        and cache.inputs == inputs
        and cache.preview == _signature(image)
    )


def refreshed_preview(output: Path, filename: str) -> Path | None:
    """Repair explicit language variants once per input version, without regenerating a scene.

    The generic preview's original language is not recorded by historical drafts, so it remains
    untouched. Unique staging files and atomic replacement keep concurrent GETs from exposing a
    partial PNG; duplicate renders of the same immutable inputs are safe.
    """
    language = _LANGUAGES.get(filename)
    if language is None:
        return None
    output = output.resolve()
    source = _metadata(output)
    if source is None:
        return None
    metadata_path, metadata = source
    network = _path(output, "network.net.xml")
    if not network.is_file():
        network = _path(output, "validated.net.xml")
    if not network.is_file():
        raise ValueError("The real-map network is missing; cannot refresh the preview")
    osm = _osm_source(output, metadata.task_id)
    inputs = {"metadata": _signature(metadata_path), "network": _signature(network)}
    if osm is not None:
        inputs["osm"] = _signature(osm)
    image = _path(output, filename)
    stamp = _path(output, f".{filename}.labels.json")
    if _cached(stamp, image, inputs):
        return image
    with tempfile.TemporaryDirectory(prefix=".preview-", dir=output) as temporary:
        staging = Path(temporary)
        generated = staging / filename
        renderer.render_map_overview(
            str(network),
            str(generated),
            title=metadata.place.name,
            radius_m=metadata.radius_m,
            detailed=metadata.detail_level == "detailed",
            origin=MapOriginMarker(
                metadata.place.name, metadata.place.longitude, metadata.place.latitude
            ),
            ui_language=language,
            source_osm_path=str(osm) if osm is not None else None,
        )
        cache = _CacheEntry(version=_RENDERER_VERSION, inputs=inputs, preview=_signature(generated))
        cache_path = staging / "labels.json"
        cache_path.write_text(cache.model_dump_json(), encoding="utf-8")
        generated.replace(image)
        cache_path.replace(stamp)
    return image
