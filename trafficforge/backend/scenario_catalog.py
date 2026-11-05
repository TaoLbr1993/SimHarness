"""Publish generated inputs to the existing SimHarness native SUMO catalog."""

import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import config
from scenario_models import ScenarioDraft, ScenarioDraftSettings, ScenarioResult

_TASK_PATTERN = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}")
_INPUTS = ("network.net.xml", "traffic.rou.xml", "metrics.add.xml", "scenario.sumocfg")
_METADATA = "scenario.manifest.json"


def _task_id(value: str) -> str:
    if not _TASK_PATTERN.fullmatch(value):
        raise ValueError("无效场景任务 ID")
    return value


def _draft(directory: Path) -> ScenarioDraft:
    spec = json.loads((directory / _METADATA).read_text(encoding="utf-8"))["spec"]
    # Existing published scenes remain immutable and retain their original metadata key.
    metadata = spec["trafficforge"] if "trafficforge" in spec else spec["nl2map"]
    settings = ScenarioDraftSettings.model_validate(metadata)
    prefix = f"/scenarios/{settings.task_id}/files"
    return ScenarioDraft(
        map_id=directory.name,
        name=spec["name"],
        settings=settings,
        preview_url=f"{prefix}/map_preview.png"
        if (directory / "map_preview.png").is_file()
        else None,
        configuration_url=f"{prefix}/scenario.sumocfg",
    )


def list_scenarios() -> list[ScenarioDraft]:
    """List saved definitions without loading or running SUMO."""
    root = Path(config.SCENARIO_CATALOG_DIR)
    return [_draft(path.parent) for path in sorted(root.glob(f"nl2-*/{_METADATA}"), reverse=True)]


def get_scenario(task_id: str) -> ScenarioDraft:
    """Read one saved definition by its generated task ID."""
    directory = Path(config.SCENARIO_CATALOG_DIR) / f"nl2-{_task_id(task_id)}"
    if not (directory / _METADATA).is_file():
        raise FileNotFoundError("场景未保存到系统")
    return _draft(directory)


def publish_scenario(result: ScenarioResult, output: Path) -> ScenarioDraft:
    """Atomically publish only inputs/settings; existing definitions are immutable."""
    task_id = _task_id(result.task_id)
    if result.plan is None or result.demand is None:
        raise ValueError("场景缺少参数或车辆需求，不能保存")
    plan, demand = result.plan, result.demand
    pending = list(result.unverified_requirements)
    for filename in ("source_map_metadata.json", "map_generation.json"):
        path = output / filename
        if path.is_file():
            source = json.loads(path.read_text(encoding="utf-8"))
            pending.extend(source.get("unverified_requirements", []))
            pending.extend(source.get("review_plan", {}).get("unverified_requirements", []))
            if source.get("status") == "review_required":
                pending.extend(source.get("review", {}).get("issues", []))
    settings = ScenarioDraftSettings(
        task_id=task_id,
        description=result.description,
        traffic_state=plan.traffic_state,
        desired_density_veh_per_km_lane=plan.desired_density_veh_per_km_lane,
        planned_vehicles=demand.planned_vehicles,
        duration_s=plan.duration_s,
        warmup_s=plan.warmup_s,
        seed=plan.seed,
        inflow_veh_per_hour_per_lane=plan.inflow_veh_per_hour_per_lane,
        simulation_mode=plan.simulation_mode,
        automation_mix=demand.automation_mix,
        unverified_requirements=list(dict.fromkeys(pending)),
    )
    root = Path(config.SCENARIO_CATALOG_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"nl2-{task_id}"
    if target.exists():
        return get_scenario(task_id)
    for name in _INPUTS:
        try:
            ET.parse(output / name)
        except ET.ParseError as exc:
            raise ValueError(f"场景输入 {name} 不是有效 XML，请重新生成场景") from exc
    with tempfile.TemporaryDirectory(prefix=".nl2-", dir=root) as temporary:
        staging = Path(temporary) / target.name
        staging.mkdir()
        for name in _INPUTS:
            shutil.copyfile(output / name, staging / name)
        for name in (
            "map_preview.png",
            "map_preview.en.png",
            "map_preview.zh.png",
            "source_map_metadata.json",
        ):
            if (output / name).is_file():
                shutil.copyfile(output / name, staging / name)
        metadata = {
            "schema_version": "2.0",
            "spec": {
                "name": result.description[:80],
                "trafficDemandMode": "scripted",
                "trafficforge": settings.model_dump(mode="json"),
                "definition": {
                    "intent": result.intent.model_dump(mode="json") if result.intent else None,
                    "plan": plan.model_dump(mode="json"),
                    "map_task_id": result.map_task_id,
                    "map_defaulted": result.map_defaulted,
                    "parent_task_id": result.parent_task_id,
                    "manual_overrides": result.manual_overrides.model_dump(mode="json")
                    if result.manual_overrides is not None
                    else None,
                },
            },
        }
        (staging / _METADATA).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            staging.rename(target)
        except FileExistsError:
            return get_scenario(task_id)
    return get_scenario(task_id)


def publish_existing(task_id: str) -> ScenarioDraft:
    """Confirm a draft's original inputs, excluding observations and preserving the draft."""
    output = Path(config.WORKSPACES_DIR) / _task_id(task_id) / "output"
    result = ScenarioResult.model_validate_json(
        (output / "scenario.json").read_text(encoding="utf-8")
    )
    if result.task_id != task_id or result.status not in {
        "generated",
        "success",
        "needs_adjustment",
    }:
        raise ValueError("任务没有完整可运行的场景输入")
    return publish_scenario(result, output)


def scenario_file(task_id: str, filename: str) -> Path:
    """Resolve only known published package files, never arbitrary paths."""
    if filename not in {
        *_INPUTS,
        _METADATA,
        "map_preview.png",
        "map_preview.en.png",
        "map_preview.zh.png",
        "source_map_metadata.json",
    }:
        raise FileNotFoundError("场景文件不存在")
    path = Path(config.SCENARIO_CATALOG_DIR) / f"nl2-{_task_id(task_id)}" / filename
    if not path.is_file():
        raise FileNotFoundError("场景文件不存在")
    return path
