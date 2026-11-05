"""Derive immutable, unpublished drafts from explicit human scene settings."""

import json
import math
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import config
from PIL import Image
from pydantic import ValidationError
from scenario_demand import build_demand, write_simulation_config
from scenario_models import (
    AutomationMixShare,
    DemandMode,
    ScenarioPlan,
    ScenarioResult,
    ScenarioRevision,
    ScenarioSettings,
)

_TASK_PATTERN = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}")
_MAP_FILES = ("network.net.xml", "map_preview.png")
_OPTIONAL_MAP_FILES = ("source_map_metadata.json", "map_generation.json", "network_checks.json")
_MANUAL_NOTE_PREFIX = "手动修改计划发车总数："
_SETTINGS_NOTE_PREFIX = "手动修改场景设定："
_INFERRED_FLOW_DEFAULT = "发车流量由目标密度 × 参考速度推算"
_DRIVER_FIELDS: dict[DemandMode, str] = {
    "count": "vehicle_count",
    "inflow": "inflow_veh_per_hour_per_lane",
    "density": "desired_density_veh_per_km_lane",
    "total_flow": "total_flow_veh_per_hour",
}


def _source_file(directory: Path, filename: str) -> Path:
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise ValueError(f"源草稿 {filename} 不在允许的任务目录内")
    if not path.is_file():
        raise ValueError(f"源草稿缺少 {filename}，请重新生成场景")
    return path


def _read_source(task_id: str) -> tuple[Path, ScenarioResult]:
    if not _TASK_PATTERN.fullmatch(task_id):
        raise ValueError("无效场景任务 ID")
    root = Path(config.WORKSPACES_DIR).resolve()
    directory = (root / task_id / "output").resolve()
    if not directory.is_relative_to(root) or directory == root:
        raise ValueError("源草稿不在允许的任务目录内")
    path = _source_file(directory, "scenario.json")
    try:
        source = ScenarioResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("源草稿元数据损坏，请重新生成场景") from exc
    if source.task_id != task_id:
        raise ValueError("源草稿任务 ID 与请求不一致")
    if source.status != "generated" or source.metrics is not None:
        raise ValueError("只能修改尚未试运行的生成草稿，请先重新生成场景")
    if source.plan is None or source.demand is None:
        raise ValueError("源草稿缺少完整参数和车流计划，请重新生成场景")
    for filename in _MAP_FILES:
        _source_file(directory, filename)
    try:
        with Image.open(directory / "map_preview.png") as preview:
            if preview.format != "PNG":
                raise ValueError("源草稿预览必须是 PNG 图片")
            preview.verify()
    except (OSError, ValueError, SyntaxError) as exc:
        raise ValueError("源草稿地图预览已损坏，请重新生成场景") from exc
    return directory, source


def _source_settings(directory: Path) -> ScenarioSettings:
    try:
        root = ET.parse(_source_file(directory, "scenario.sumocfg")).getroot()
        step = root.find("time/step-length")
        if root.tag != "configuration" or step is None:
            raise ValueError("missing source step length")
        settings = config.SCENARIO.model_dump()
        settings["step_length_s"] = float(step.attrib["value"])
        return ScenarioSettings.model_validate(settings)
    except (ET.ParseError, ValueError, KeyError) as exc:
        raise ValueError("源草稿缺少有效仿真步长配置，请重新生成场景") from exc


def _copy_source_metadata(source_dir: Path, output: Path, filename: str) -> None:
    path = _source_file(source_dir, filename)
    if filename in _OPTIONAL_MAP_FILES:
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"源草稿 {filename} 元数据损坏，请重新生成场景") from exc
    shutil.copyfile(path, output / filename)


def _revise_plan(
    source: ScenarioResult, revision: ScenarioRevision, settings: ScenarioSettings
) -> tuple[ScenarioPlan, ScenarioRevision]:
    assert source.plan is not None and source.demand is not None
    plan = source.plan.model_copy(deep=True)
    patch = revision.model_dump()
    source_mode = plan.demand_mode or (
        "count" if plan.requested_vehicle_count is not None else "inflow"
    )
    inferred: DemandMode | None = None
    for mode, field in _DRIVER_FIELDS.items():
        if field in patch:
            inferred = mode
            break
    mode = revision.demand_mode or inferred or source_mode
    for field in (
        "duration_s",
        "warmup_s",
        "seed",
        "desired_density_veh_per_km_lane",
        "reference_speed_kmh",
    ):
        if field in patch:
            setattr(plan, field, patch[field])
    if plan.warmup_s >= plan.duration_s:
        raise ValueError("预热时间必须小于仿真时长，请同时调整预热时间或延长仿真")
    steps = plan.duration_s / settings.step_length_s
    if not math.isclose(steps, round(steps), abs_tol=1e-8, rel_tol=0):
        raise ValueError("仿真时长必须是源场景仿真步长的整数倍")
    if revision.automation_mix is not None:
        plan.automation_mix = [
            AutomationMixShare.model_validate(item.model_dump()) for item in revision.automation_mix
        ]
        plan.automation_note = (
            "L0–L5 为系统 SUMO 跟驰行为配置，不代表已接入真实自动驾驶算法；"
            "按照手动百分比重新分配整数车辆，取整后的实际比例可能略有差异。"
        )
    plan.demand_mode = mode
    plan.requested_total_flow_veh_per_hour = None
    if mode == "count":
        plan.requested_vehicle_count = (
            revision.vehicle_count
            if revision.vehicle_count is not None
            else source.demand.planned_vehicles
        )
        plan.inflow_veh_per_hour_per_lane = None
    elif mode == "total_flow":
        flow = revision.total_flow_veh_per_hour or source.plan.requested_total_flow_veh_per_hour
        if flow is None:
            raise ValueError("按总发车流量编排时，请填写总发车流量")
        plan.requested_total_flow_veh_per_hour = flow
        plan.requested_vehicle_count = math.floor(flow * plan.duration_s / 3600)
        plan.inflow_veh_per_hour_per_lane = None
    else:
        plan.requested_vehicle_count = None
        if mode == "density":
            flow = plan.desired_density_veh_per_km_lane * plan.reference_speed_kmh
        else:
            flow = revision.inflow_veh_per_hour_per_lane or source.plan.inflow_veh_per_hour_per_lane
        if flow is None:
            raise ValueError("按入口流量编排时，请填写每入口车道流量")
        if not 0 < flow <= 10000:
            raise ValueError("推算的每入口车道流量须为 0–10000 辆/小时，请降低密度或参考速度")
        plan.inflow_veh_per_hour_per_lane = flow
    count = plan.requested_vehicle_count
    if count is not None and not 1 <= count <= settings.max_vehicles:
        raise ValueError(f"本次需求会计划发车 {count} 辆，允许 1–{settings.max_vehicles} 辆")
    labels = {
        "duration_s": "仿真时长",
        "warmup_s": "预热时间",
        "seed": "随机种子",
        "desired_density_veh_per_km_lane": "目标密度参考值",
        "automation_mix": "未指定自动驾驶等级配比，默认 L0 100%",
    }
    removed = {label for field, label in labels.items() if field in patch}
    changed_driver = revision.demand_mode is not None or inferred is not None
    plan.defaults_used = [
        item
        for item in plan.defaults_used
        if item not in removed
        and not (
            (changed_driver or revision.reference_speed_kmh is not None)
            and (item == _INFERRED_FLOW_DEFAULT or item.startswith("默认轻量车流："))
        )
    ]
    cumulative = source.manual_overrides.model_dump() if source.manual_overrides else {}
    cumulative.update(patch)
    cumulative["demand_mode"] = mode
    # Only the active driving input remains authoritative across successive edits.
    for field in ("vehicle_count", "inflow_veh_per_hour_per_lane", "total_flow_veh_per_hour"):
        if field != _DRIVER_FIELDS[mode]:
            cumulative.pop(field, None)
    if mode == "count" and changed_driver:
        cumulative["vehicle_count"] = plan.requested_vehicle_count
    elif mode == "total_flow" and changed_driver:
        cumulative["total_flow_veh_per_hour"] = plan.requested_total_flow_veh_per_hour
    elif mode == "inflow" and changed_driver:
        cumulative["inflow_veh_per_hour_per_lane"] = plan.inflow_veh_per_hour_per_lane
    return plan, ScenarioRevision.model_validate(cumulative)


def revise_scenario(task_id: str, revision: ScenarioRevision) -> ScenarioResult:
    """Rebuild demand from the unchanged network, without LLM calls or traffic execution."""
    # Validate even direct Python callers; Pydantic's model_construct skips field validation.
    try:
        revision = ScenarioRevision.model_validate(revision.model_dump())
    except ValidationError as exc:
        raise ValueError("场景设定无效，请检查数值范围、预热时间与等级配比") from exc
    source_dir, source = _read_source(task_id)
    settings = _source_settings(source_dir)
    assert source.plan is not None
    new_task_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    root = Path(config.WORKSPACES_DIR).resolve()
    destination = root / new_task_id
    plan, cumulative = _revise_plan(source, revision, settings)
    notes = [
        item
        for item in source.notes
        if not item.startswith((_MANUAL_NOTE_PREFIX, _SETTINGS_NOTE_PREFIX))
    ]
    notes.append(
        f"{_SETTINGS_NOTE_PREFIX}已应用手动参数并重新编排车流；原始需求保留，"
        "有冲突时以当前手动设定为准。目标密度仍为参考目标，不代表实测密度。"
    )
    if revision.reference_speed_kmh is not None and plan.demand_mode != "density":
        notes.append(
            f"{_SETTINGS_NOTE_PREFIX}参考速度仅用于按密度推算车流；"
            "当前按数量或流量编排，因此该参考值不会更改发车数量或车辆限速。"
        )
    result = ScenarioResult(
        status="generated",
        description=source.description,
        task_id=new_task_id,
        parent_task_id=task_id,
        manual_overrides=cumulative,
        intent=source.intent,
        map_task_id=source.map_task_id,
        map_defaulted=source.map_defaulted,
        plan=plan,
        unverified_requirements=source.unverified_requirements,
        notes=notes,
    )
    # Only the final rename exposes a task ID. A failed rebuild leaves no publishable draft.
    with tempfile.TemporaryDirectory(prefix=".revision-", dir=root) as temporary:
        staging = Path(temporary) / new_task_id
        output = staging / "output"
        output.mkdir(parents=True)
        for filename in (
            *_MAP_FILES,
            *_OPTIONAL_MAP_FILES,
            "map_preview.en.png",
            "map_preview.zh.png",
        ):
            if filename in _MAP_FILES or (source_dir / filename).exists():
                _copy_source_metadata(source_dir, output, filename)
        try:
            result.demand = build_demand(output / "network.net.xml", output, plan, settings)
        except ET.ParseError as exc:
            raise ValueError("源草稿路网 XML 已损坏，请重新生成场景") from exc
        write_simulation_config(output, plan, settings)
        result.artifacts = sorted(
            {f"output/{path.name}" for path in output.iterdir() if path.is_file()}
            | {"output/scenario.json"}
        )
        result.artifact_urls = {
            name: f"/artifacts/{new_task_id}/{Path(name).name}" for name in result.artifacts
        }
        (output / "scenario.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        staging.rename(destination)
    return result
