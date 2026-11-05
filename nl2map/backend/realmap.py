"""Real-place import workflow. Road geometry comes from OSM, never generated Python."""

import json
import math
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import agent
import config
import map_sources
import renderer
import runner
from preview_location import MapOriginMarker
from pydantic import BaseModel, Field
from realmap_models import (
    MAIN_ROAD_TYPES,
    DetailLevel,
    MapIntent,
    PlaceCandidate,
    PlaceChoice,
    SourceRecord,
)
from review_models import ReviewPlan, VisualReview, XmlReview
from xml_checks import check_network


class NetworkStatistics(BaseModel):
    directed_edges: int
    lanes: int
    junctions: int
    connections: int
    signal_controllers: int
    weak_components: int
    road_names: list[str]
    converted_xy_bounds: str
    xml_location_xy_bounds: str
    projection: str
    source_highway_ways: int
    source_ways_with_lanes: int
    source_ways_with_maxspeed: int
    source_signal_nodes: int


class ImportValidation(BaseModel):
    passed: bool = False
    network: str = "output/validated.net.xml"
    image: str = "output/map_preview.png"
    overview: str = "output/map_preview.png"
    sumo_config: str = "output/map.sumocfg"
    xml_review: XmlReview | None = None
    visual_review: VisualReview | None = None
    sumo_exit_code: int | None = None
    statistics: NetworkStatistics | None = None
    import_issues: list[str] = Field(default_factory=list)


def _timeout_diagnostic(error: subprocess.TimeoutExpired) -> str:
    def decoded(value: bytes | str | None) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

    return (
        f"TIMEOUT after {error.timeout} seconds\n" + decoded(error.stdout) + decoded(error.stderr)
    )


class RealMapResult(BaseModel):
    status: Literal[
        "success", "needs_clarification", "source_error", "validation_error", "review_required"
    ]
    description: str
    source_mode: Literal["real_map"] = "real_map"
    review_mode: Literal["xml_and_user"] = "xml_and_user"
    detail_level: DetailLevel = "main_roads"
    user_review: Literal["not_ready", "pending"] = "not_ready"
    user_review_note: str | None = None
    unverified_requirements: list[str] = Field(default_factory=list)
    task_id: str
    intent: MapIntent
    place: PlaceCandidate | None = None
    place_choice: PlaceChoice | None = None
    candidates: list[PlaceCandidate] = Field(default_factory=list)
    radius_m: float
    radius_defaulted: bool
    extent_policy: str = "圆形选路并以 36 点近似圆筛选 SUMO 道路段，保留完整段；边缘可能超出半径"
    sources: list[SourceRecord] = Field(default_factory=list)
    validation: ImportValidation | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    artifact_urls: dict[str, str] = Field(default_factory=dict)
    attribution: str = "© OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _statistics(network: Path, source: Path) -> NetworkStatistics:
    root, osm = ET.parse(network).getroot(), ET.parse(source).getroot()
    edges = [e for e in root.findall("edge") if e.get("function") != "internal"]
    if not edges:
        raise ValueError("转换后没有 passenger 可用道路，请核对地图或扩大范围")
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        start, end = edge.attrib["from"], edge.attrib["to"]
        neighbors[start].add(end)
        neighbors[end].add(start)
    pending = set(neighbors)
    components = 0
    while pending:
        components += 1
        stack = [pending.pop()]
        while stack:
            adjacent = neighbors[stack.pop()] & pending
            pending.difference_update(adjacent)
            stack.extend(adjacent)
    ways = [w for w in osm.findall("way") if w.find("tag[@k='highway']") is not None]
    tags = [{t.get("k"): t.get("v") for t in w.findall("tag")} for w in ways]
    location = root.find("location")
    if location is None:
        raise ValueError("转换结果缺少地理投影信息")
    points = [
        tuple(map(float, p.split(",")[:2]))
        for edge in edges
        for lane in edge.findall("lane")
        for p in lane.get("shape", "").split()
    ]
    if not points or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError("转换结果缺少有效车道坐标")
    bounds = (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )
    return NetworkStatistics(
        directed_edges=len(edges),
        lanes=sum(len(e.findall("lane")) for e in edges),
        junctions=sum(j.get("type") != "internal" for j in root.findall("junction")),
        connections=sum(not c.get("from", "").startswith(":") for c in root.findall("connection")),
        signal_controllers=len({t.get("id") for t in root.findall("tlLogic")}),
        weak_components=components,
        road_names=sorted({e.attrib["name"] for e in edges if e.get("name")}),
        converted_xy_bounds=",".join(f"{v:.2f}" for v in bounds),
        xml_location_xy_bounds=location.get("convBoundary", ""),
        projection=location.get("projParameter", ""),
        source_highway_ways=len(ways),
        source_ways_with_lanes=sum("lanes" in t for t in tags),
        source_ways_with_maxspeed=sum("maxspeed" in t for t in tags),
        source_signal_nodes=sum(
            n.find("tag[@k='highway'][@v='traffic_signals']") is not None
            for n in osm.findall("node")
        ),
    )


def _selection_polygon(place: PlaceCandidate, radius_m: float) -> str:
    latitude, longitude = math.radians(place.latitude), math.radians(place.longitude)
    distance = radius_m / 6371008.8
    coordinates: list[str] = []
    for degrees in range(0, 360, 10):
        bearing = math.radians(degrees)
        lat = math.asin(
            math.sin(latitude) * math.cos(distance)
            + math.cos(latitude) * math.sin(distance) * math.cos(bearing)
        )
        lon = longitude + math.atan2(
            math.sin(bearing) * math.sin(distance) * math.cos(latitude),
            math.cos(distance) - math.sin(latitude) * math.sin(lat),
        )
        coordinates.extend((f"{math.degrees(lon):.7f}", f"{math.degrees(lat):.7f}"))
    return ",".join(coordinates)


def _tool_environment() -> dict[str, str]:
    environment = runner._child_environment()
    if not environment.get("SUMO_HOME"):
        binary = shutil.which("sumo")
        if binary:
            prefix = Path(binary).resolve().parent.parent
            for home in (prefix / "share/sumo", prefix):
                if (home / "data/xsd/net_file.xsd").is_file():
                    environment["SUMO_HOME"] = str(home)
                    break
    return environment


def compile_network(
    output: Path, place: PlaceCandidate, radius_m: float, detail_level: DetailLevel = "main_roads"
) -> ImportValidation:
    """Deterministic conversion and full-network checks, with logs retained on failure."""
    for executable in ("netconvert", "sumo"):
        if not shutil.which(executable):
            raise ValueError(f"未找到 {executable}，请安装 SUMO 并加入 PATH")
    command = [
        "netconvert",
        "--osm-files",
        "source.osm.xml",
        "--output-file",
        "imported.net.xml",
        "--keep-edges.by-vclass",
        "passenger",
        "--osm.turn-lanes",
        "true",
        "--output.street-names",
        "true",
        "--geometry.remove",
        "true",
        "--tls.guess",
        "false",
        "--junctions.join",
        "false",
        "--geometry.split",
        "true",
        "--keep-edges.postload",
        "true",
    ]
    command.extend(["--keep-edges.in-geo-boundary", _selection_polygon(place, radius_m)])
    if detail_level == "main_roads":
        command.extend(["--keep-edges.by-type", ",".join(MAIN_ROAD_TYPES)])
    trim_command = [
        "netconvert",
        "--sumo-net-file",
        "imported.net.xml",
        "--output-file",
        "validated.net.xml",
        "--keep-edges.in-geo-boundary",
        _selection_polygon(place, radius_m),
        "--geometry.remove",
        "true",
        "--output.street-names",
        "true",
    ]
    environment = _tool_environment()
    _write_json(output / "conversion-command.json", [command, trim_command])
    try:
        version = subprocess.run(
            ["netconvert", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        (output / "netconvert-version.txt").write_text(_timeout_diagnostic(exc), encoding="utf-8")
        raise
    (output / "netconvert-version.txt").write_text(
        version.stdout + version.stderr, encoding="utf-8"
    )
    conversion_log = ""
    for number, arguments in enumerate([command, trim_command], 1):
        try:
            converted = subprocess.run(
                arguments,
                cwd=output,
                capture_output=True,
                text=True,
                timeout=config.REAL_MAP.tool_timeout_sec,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            conversion_log += f"\nPASS {number}\n" + _timeout_diagnostic(exc)
            (output / "netconvert.log").write_text(conversion_log, encoding="utf-8")
            raise
        conversion_log += f"\nPASS {number}\n" + converted.stdout + converted.stderr
        (output / "netconvert.log").write_text(conversion_log, encoding="utf-8")
        if converted.returncode:
            raise ValueError(
                "netconvert 转换失败：" + (converted.stderr or converted.stdout)[-4000:]
            )
    network = output / "validated.net.xml"
    stats = _statistics(network, output / "source.osm.xml")
    checks = check_network(
        str(network), ReviewPlan(rules=[], visual_requirements=[], unverified_requirements=[])
    )
    validation = ImportValidation(xml_review=checks, statistics=stats)
    diagnostic_lines = conversion_log.splitlines()
    for index, line in enumerate(diagnostic_lines):
        if any(
            token in line.lower()
            for token in (
                "ignoring restriction",
                "could not build program",
                "does not control any links",
            )
        ):
            context = diagnostic_lines[index - 1] if index else ""
            # Adjacent warnings can be unrelated; only attach a known restriction cause.
            if (
                "ignoring restriction" not in line.lower()
                or "direction of restriction" not in context.lower()
            ):
                context = ""
            validation.import_issues.append(
                "源地图约束未完整导入：" + line + (" 原因：" + context if context else "")
            )
    if not checks.passed:
        return validation
    configuration = ET.Element("configuration")
    inputs = ET.SubElement(configuration, "input")
    ET.SubElement(inputs, "net-file", value="validated.net.xml")
    timing = ET.SubElement(configuration, "time")
    ET.SubElement(timing, "begin", value="0")
    ET.SubElement(timing, "end", value="1")
    ET.ElementTree(configuration).write(
        output / "map.sumocfg", encoding="utf-8", xml_declaration=True
    )
    try:
        checked = subprocess.run(
            ["sumo", "-c", "map.sumocfg", "--no-step-log", "true"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=config.REAL_MAP.tool_timeout_sec,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        (output / "sumo-load.log").write_text(_timeout_diagnostic(exc), encoding="utf-8")
        raise
    (output / "sumo-load.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
    validation.sumo_exit_code = checked.returncode
    validation.passed = checked.returncode == 0
    return validation


def generate_real_map(
    description: str,
    intent: MapIntent,
    selected_place_id: str | None = None,
    *,
    detail_level: DetailLevel | None = None,
    ui_language: Literal["en", "zh"] = "en",
) -> RealMapResult:
    """Resolve a real place, preserve its OSM evidence, compile and inspect the imported network."""
    task_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    output = Path(config.WORKSPACES_DIR) / task_id / "output"
    output.mkdir(parents=True)
    radius = intent.radius_m if intent.radius_m is not None else config.REAL_MAP.default_radius_m
    result = RealMapResult(
        status="source_error",
        task_id=task_id,
        description=description,
        intent=intent,
        radius_m=radius,
        radius_defaulted=intent.radius_m is None,
        detail_level=detail_level or intent.detail_level,
        unverified_requirements=intent.extra_requirements,
    )
    stage = "source"
    try:
        if not 100 <= radius <= config.REAL_MAP.max_radius_m:
            result.status = "needs_clarification"
            result.error = f"半径须为 100–{config.REAL_MAP.max_radius_m:g} 米，未自动缩放"
            return _finish(result, output)
        geocoding = map_sources.geocode(intent.place_query, intent.alternate_query, output)
        result.sources.extend(geocoding.records)
        result.warnings.extend(geocoding.warnings)
        result.candidates = geocoding.candidates
        if not geocoding.candidates:
            result.status = "needs_clarification"
            result.error = "没有找到匹配地点，请补充城市、校区或更完整的地名"
            return _finish(result, output)
        if selected_place_id:
            if selected_place_id not in {c.id for c in geocoding.candidates}:
                result.status = "needs_clarification"
                result.error = "所选地点不在本次候选中，请从 candidates 重新选择"
                return _finish(result, output)
            choice = PlaceChoice(
                selected_id=selected_place_id,
                needs_clarification=False,
                reason="调用方显式选择地图候选",
            )
        else:
            choice = agent.choose_map_place(description, geocoding.candidates)
        result.place_choice = choice
        if choice.needs_clarification:
            result.status = "needs_clarification"
            result.error = "；".join([choice.reason, *geocoding.warnings])
            return _finish(result, output)
        place = next(c for c in geocoding.candidates if c.id == choice.selected_id)
        result.place = place
        if place.coordinate_note and place.coordinate_note not in result.warnings:
            result.warnings.append(place.coordinate_note)
        if place.provider == "amap":
            result.attribution = (
                "地点检索：高德地图；道路：© OpenStreetMap contributors, ODbL: "
                "https://www.openstreetmap.org/copyright"
            )
        result.sources.append(map_sources.fetch_roads(place, radius, output))
        stage = "validation"
        result.validation = compile_network(output, place, radius, result.detail_level)
        if not result.validation.passed:
            result.status = "validation_error"
            result.error = "完整 XML 检查或 SUMO 加载失败，详见 validation 与日志"
            return _finish(result, output)
        if result.detail_level == "detailed":
            renderer.render_network(
                str(output / "validated.net.xml"), str(output / "review.png"), osm_source=True
            )
            result.validation.image = "output/review.png"
        for language in ("en", "zh"):
            renderer.render_map_overview(
                str(output / "validated.net.xml"),
                str(output / f"map_preview.{language}.png"),
                title=place.name,
                radius_m=radius,
                detailed=result.detail_level == "detailed",
                origin=MapOriginMarker(
                    name=place.name, longitude=place.longitude, latitude=place.latitude
                ),
                ui_language=language,
                source_osm_path=str(output / "source.osm.xml"),
            )
        shutil.copyfile(output / f"map_preview.{ui_language}.png", output / "map_preview.png")
        result.warnings.extend(
            [
                "OSM 道路派生地图，不保证地图最新、道路完整或实际通行权限准确。",
                result.extent_policy,
                "缺失的车道数、限速、优先权及车道连接可由 netconvert 推断；"
                "信号配时为生成值，并非实测。",
                "信号图标表示 SUMO 控制绑定，不表示真实灯杆位置；无标注不代表实地无信号灯。",
                "map.sumocfg 仅用于空车流加载测试，尚未生成交通需求或验证所有 OD 可达性。",
            ]
        )
        if result.radius_defaulted:
            result.warnings.append(f"用户未指定半径，本次使用 {radius:g} 米。")
        stats = result.validation.statistics
        if stats and stats.weak_components > 1:
            result.warnings.append(
                f"路网有 {stats.weak_components} 个弱连通分量，未擅自删除孤立道路；跨分量无法通行。"
            )
        log = (output / "netconvert.log").read_text("utf-8")
        if "Warning:" in log:
            result.warnings.append("netconvert 有转换警告，完整内容保留于 netconvert.log。")
        result.status = "success"
        result.user_review = "pending"
        result.user_review_note = (
            "路网已生成，请查看预览判断是否符合测试需求；需要时可要求包含支路或更多细节。"
        )
        if intent.extra_requirements:
            result.warnings.append(
                "以下额外要求尚未验证，未改写真实道路：" + "；".join(intent.extra_requirements)
            )
    except (ValueError, OSError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        result.status = "source_error" if stage == "source" else "validation_error"
        result.error = str(exc)
    except Exception as exc:
        result.status = "source_error" if stage == "source" else "validation_error"
        result.error = f"{stage} 阶段失败：{exc}"
    return _finish(result, output)


def _finish(result: RealMapResult, output: Path) -> RealMapResult:
    result.artifacts = sorted(
        [f"output/{p.name}" for p in output.iterdir() if p.is_file()] + ["output/metadata.json"]
    )
    result.artifact_urls = {
        p: f"/artifacts/{result.task_id}/{Path(p).name}" for p in result.artifacts
    }
    _write_json(output / "metadata.json", result.model_dump(mode="json"))
    return result
