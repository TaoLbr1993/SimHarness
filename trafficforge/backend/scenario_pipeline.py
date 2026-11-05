"""Compose a map, demand and native SUMO trial without changing Core simulation control."""

import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import config
import realmap
import renderer
import review_pipeline
import scenario_catalog
from realmap_models import DetailLevel, MapIntent
from review_models import ReviewPlan
from scenario_demand import build_demand, write_simulation_config
from scenario_metrics import collect_metrics
from scenario_models import ScenarioIntent, ScenarioResult, resolve_plan
from xml_checks import check_network


def _network_in_task(task_id: str) -> Path:
    if not re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", task_id):
        raise ValueError("map_task_id 不是有效任务 ID")
    root = (Path(config.WORKSPACES_DIR) / task_id / "output").resolve()
    files = list(root.rglob("validated.net.xml")) if root.is_dir() else []
    if len(files) != 1:
        raise ValueError("指定任务没有唯一的已编译路网，请先生成或选择地图")
    path = files[0].resolve()
    if not path.is_relative_to(root):
        raise ValueError("地图文件不在任务输出目录")
    return path


def generate_scenario(
    description: str,
    intent: MapIntent,
    *,
    map_task_id: str | None = None,
    selected_place_id: str | None = None,
    detail_level: DetailLevel | None = None,
    run_simulation: bool = False,
    publish_to_catalog: bool = False,
    simulation_mode: Literal["traffic", "autonomy"] = "traffic",
    ui_language: Literal["en", "zh"] = "en",
) -> ScenarioResult:
    """Build an unpublished preview; publication and SUMO trials require explicit opt-in."""
    task_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    output = Path(config.WORKSPACES_DIR) / task_id / "output"
    output.mkdir(parents=True)
    scene = intent.scenario or ScenarioIntent(objective=description)
    result = ScenarioResult(
        status="scenario_error",
        description=description,
        task_id=task_id,
        intent=scene,
        unverified_requirements=scene.unverified_requirements,
    )
    stage = "parameters"
    try:
        # Recheck at the pipeline boundary, including the scenario=None fallback.
        scene.validate_sources(description)
        result.plan = resolve_plan(scene, config.SCENARIO, simulation_mode=simulation_mode)
        if scene.use_existing_map and map_task_id is None:
            result.status = "needs_clarification"
            result.error = "请通过 map_task_id 指定要沿用的地图；不会猜测或覆盖最近一次任务"
            return _finish(result, output)
        stage = "map"
        if map_task_id is None:
            if intent.source_mode == "real_map":
                map_result = realmap.generate_real_map(
                    description,
                    intent.model_copy(update={"scenario": None}),
                    selected_place_id,
                    detail_level=detail_level,
                    ui_language=ui_language,
                ).model_dump(mode="json")
                candidate_id = map_result.get("task_id")
            else:
                map_description = intent.map_description or config.SCENARIO.default_map_description
                result.map_defaulted = not bool(intent.map_description)
                map_result = review_pipeline.generate_checked(map_description)
                candidate_id = map_result.get("execution", {}).get("task_id")
            realmap._write_json(output / "map_generation.json", map_result)
            pending = list(map_result.get("unverified_requirements", []))
            pending.extend(map_result.get("review_plan", {}).get("unverified_requirements", []))
            if map_result.get("status") == "review_required":
                pending.extend(map_result.get("review", {}).get("issues", []))
            result.unverified_requirements = list(
                dict.fromkeys([*result.unverified_requirements, *pending])
            )
            if map_result.get("status") not in {"success", "review_required"}:
                result.status = (
                    "needs_clarification"
                    if map_result.get("status") == "needs_clarification"
                    else "map_error"
                )
                result.error = str(
                    map_result.get("error") or "地图生成未通过，请查看 map_generation.json"
                )
                return _finish(result, output)
            if not isinstance(candidate_id, str):
                raise ValueError("地图生成没有返回有效任务 ID")
            map_task_id = candidate_id
            if map_result.get("status") == "review_required":
                result.notes.append(
                    "源地图有待用户复核项，已保留 map_generation.json，不代表需求全部满足。"
                )
        source = _network_in_task(map_task_id)
        result.map_task_id = map_task_id
        checks = check_network(
            str(source), ReviewPlan(rules=[], visual_requirements=[], unverified_requirements=[])
        )
        realmap._write_json(output / "network_checks.json", checks.model_dump(mode="json"))
        if not checks.passed:
            raise ValueError("源地图 XML 完整性检查未通过")
        shutil.copyfile(source, output / "network.net.xml")
        for name in ("map_preview.png", "review.png"):
            if (source.parent / name).is_file():
                shutil.copyfile(source.parent / name, output / "map_preview.png")
                break
        for language in ("en", "zh"):
            variant = f"map_preview.{language}.png"
            if (source.parent / variant).is_file():
                shutil.copyfile(source.parent / variant, output / variant)
        selected_preview = output / f"map_preview.{ui_language}.png"
        if selected_preview.is_file():
            shutil.copyfile(selected_preview, output / "map_preview.png")
        metadata = source.parent / "metadata.json"
        if metadata.is_file():
            shutil.copyfile(metadata, output / "source_map_metadata.json")
            source_metadata = json.loads(metadata.read_text(encoding="utf-8"))
            source_pending = source_metadata.get("unverified_requirements", [])
            result.notes.extend(
                f"地图来源说明：{warning}"
                for warning in source_metadata.get("warnings", [])
                if isinstance(warning, str)
            )
            if isinstance(source_metadata.get("attribution"), str):
                result.notes.append("地图来源：" + source_metadata["attribution"])
            result.unverified_requirements = list(
                dict.fromkeys([*result.unverified_requirements, *source_pending])
            )
        stage = "preview"
        if not (output / "map_preview.png").is_file():
            renderer.render_network(
                str(output / "network.net.xml"),
                str(output / "map_preview.png"),
                osm_source=intent.source_mode == "real_map" or metadata.is_file(),
            )
        stage = "demand"
        result.demand = build_demand(
            output / "network.net.xml", output, result.plan, config.SCENARIO
        )
        write_simulation_config(output, result.plan, config.SCENARIO)
        result.notes.extend(
            [
                "车流是按可达路线分配的场景设定，不是实际交通调查或校准的 OD。",
                "目标密度与实际密度分开；未能进入路网的车辆不算作在网车辆。",
                "速度为预热后 SUMO 全网在网车辆时间加权均值；"
                "密度为外部小客车车道上的平均在网量/车道公里。",
                "排队为预热后全网停驶车辆数；行程时间只统计全程已完成车辆，"
                "包含预热阶段，不含入网前等待。",
                "用户关注指标记录在 plan.metrics；当前固定返回全部观测指标。",
                "拥堵结论依据本次公开判据，只说明当前模型试运行的交通状态，是否适合研究由用户判断。",
            ]
        )
        if result.map_defaulted:
            result.notes.append("未指定地图，使用简化的四向信号十字路口。")
        if result.plan.automation_note:
            result.notes.append(result.plan.automation_note)
        if result.plan.traffic_state != "congested":
            result.notes.append(
                "当前只验证拥堵目标是否出现；其他交通状态提供实测数据，不自动判定达成。"
            )
        result.status = "generated"
        if publish_to_catalog:
            stage = "catalog"
            result.catalog_map_id = scenario_catalog.publish_scenario(result, output).map_id
        _finish(result, output)
        if not run_simulation:
            return result
        stage = "simulation"
        if not shutil.which("sumo"):
            raise ValueError("未找到 SUMO，场景文件已生成；可安装 SUMO 后运行 scenario.sumocfg")
        command = ["sumo", "-c", "scenario.sumocfg"]
        realmap._write_json(output / "simulation-command.json", command)
        version = subprocess.run(
            ["sumo", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=realmap._tool_environment(),
        )
        (output / "sumo-version.txt").write_text(version.stdout + version.stderr, encoding="utf-8")
        try:
            completed = subprocess.run(
                command,
                cwd=output,
                capture_output=True,
                text=True,
                timeout=config.SCENARIO.run_timeout_s,
                env=realmap._tool_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            (output / "simulation.log").write_text(
                realmap._timeout_diagnostic(exc), encoding="utf-8"
            )
            raise
        result.simulation_exit_code = completed.returncode
        (output / "simulation.log").write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            raise ValueError("SUMO 试运行失败：" + (completed.stderr or completed.stdout)[-4000:])
        stage = "metrics"
        result.metrics = collect_metrics(output, result.plan, result.demand)
        result.status = (
            "needs_adjustment"
            if result.metrics.target_state in {"not_observed", "no_data", "invalid_run"}
            else "success"
        )
    except Exception as exc:
        result.status = "map_error" if stage == "map" else "scenario_error"
        result.error = f"{stage}：{exc}"
    return _finish(result, output)


def _finish(result: ScenarioResult, output: Path) -> ScenarioResult:
    result.artifacts = sorted(
        {f"output/{path.name}" for path in output.iterdir() if path.is_file()}
        | {"output/scenario.json"}
    )
    result.artifact_urls = {
        path: f"/artifacts/{result.task_id}/{Path(path).name}" for path in result.artifacts
    }
    (output / "scenario.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
