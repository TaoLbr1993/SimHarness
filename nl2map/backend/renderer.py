"""Render the compiled SUMO network for visual review."""

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from preview_labels import english_name, osm_english_names
from preview_location import MapOriginMarker, project_origin


def _points(shape: str) -> list[tuple[float, float]]:
    return [(float(point.split(",")[0]), float(point.split(",")[1])) for point in shape.split()]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _signal_lanes(root: ET.Element) -> list[ET.Element]:
    """Resolve incoming lanes controlled by a signal program, without inventing poles."""
    programs = {light.get("id") for light in root.findall("tlLogic") if light.findall("phase")}
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    controlled = {}
    for connection in root.findall("connection"):
        if connection.get("tl") not in programs:
            continue
        edge = edges.get(connection.get("from"))
        if edge is None or edge.get("function") == "internal":
            continue
        for lane in edge.findall("lane"):
            if lane.get("index") == connection.get("fromLane"):
                controlled[lane.get("id")] = lane
    return list(controlled.values())


def _draw_signal(
    painter: ImageDraw.ImageDraw,
    end: tuple[float, float],
    previous: tuple[float, float],
    lane_width_px: float,
) -> None:
    """Draw a schematic head beside the incoming lane, with a leader to its stop line."""
    length = math.dist(end, previous)
    if length == 0:
        return
    ux, uy = (end[0] - previous[0]) / length, (end[1] - previous[1]) / length
    half_width = max(lane_width_px / 2, 3)
    # Move upstream and to the right of travel, keeping the junction connections visible.
    offset = half_width + 16
    x, y = end[0] - ux * 36 - uy * offset, end[1] - uy * 36 + ux * offset
    painter.line(
        [
            (end[0] - uy * half_width, end[1] + ux * half_width),
            (end[0] + uy * half_width, end[1] - ux * half_width),
        ],
        fill="white",
        width=3,
    )
    painter.line([end, (x, y)], fill="#475569", width=2)
    painter.rounded_rectangle(
        (x - 10, y - 25, x + 10, y + 25), radius=5, fill="#18212d", outline="white", width=1
    )
    for dy, color in ((-16, "#ef4444"), (0, "#facc15"), (16, "#22c55e")):
        painter.ellipse((x - 5, y + dy - 5, x + 5, y + dy + 5), fill=color)


def render_network(network_path: str, output_path: str, *, osm_source: bool = False) -> None:
    """Draw geometry and schematic signal-controlled lane markers from compiled SUMO."""
    root = ET.parse(network_path).getroot()
    signal_lanes = _signal_lanes(root)
    lanes = []
    for edge in root.findall("edge"):
        internal = edge.get("function") == "internal"
        for lane in edge.findall("lane"):
            shape = lane.get("shape", "")
            points = _points(shape)
            if len(points) >= 2:
                lanes.append((points, float(lane.get("width", "3.2")), internal))
    road_points = [point for points, _, internal in lanes if not internal for point in points]
    if not road_points:
        raise ValueError("SUMO 网络没有可绘制的道路几何")
    junctions = [j for j in root.findall("junction") if j.get("type") != "internal"]
    junction = max(junctions, key=lambda j: len(j.get("incLanes", "").split()), default=None)
    xs, ys = zip(*road_points, strict=True)
    bounds = (min(xs), min(ys), max(xs), max(ys))
    width = max(bounds[2] - bounds[0], 1.0)
    height = max(bounds[3] - bounds[1], 1.0)
    center_x = (
        float(junction.get("x", "0")) if junction is not None else (bounds[0] + bounds[2]) / 2
    )
    center_y = (
        float(junction.get("y", "0")) if junction is not None else (bounds[1] + bounds[3]) / 2
    )
    radius = max(20.0, min(max(width, height) * 0.18, 100.0))
    detail = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)

    image = Image.new("RGB", (1600, 920), "white")
    draw = ImageDraw.Draw(image)
    title = "SUMO network geometry - compiled output"
    if osm_source:
        title = "OSM-derived SUMO network | (c) OpenStreetMap contributors (ODbL)"
    draw.text((25, 18), title, fill="#172334", font=_font(25))
    for left, title, box in (
        (30, "Full network", bounds),
        (820, "Busiest junction detail", detail),
    ):
        draw.text((left, 61), title, fill="#172334", font=_font(19))
        side = 740
        top = 98
        draw.rectangle((left, top, left + side, top + side), fill="#f6f8fa", outline="#cbd5e1")
        span_x = max(box[2] - box[0], 1.0)
        span_y = max(box[3] - box[1], 1.0)
        scale = (side - 100) / max(span_x, span_y)
        mid_x, mid_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

        # Draw into a panel-local image so the enlarged view clips at its border.
        panel = Image.new("RGB", (side, side), "#f6f8fa")
        painter = ImageDraw.Draw(panel)

        def local(
            point: tuple[float, float],
            side: int = side,
            mid_x: float = mid_x,
            mid_y: float = mid_y,
            scale: float = scale,
        ) -> tuple[float, float]:
            return (side / 2 + (point[0] - mid_x) * scale, side / 2 - (point[1] - mid_y) * scale)

        for j in junctions:
            polygon = _points(j.get("shape", ""))
            if len(polygon) >= 3:
                painter.polygon([local(p) for p in polygon], fill="#d6dbe1")
        for points, lane_width, internal in lanes:
            projected = [local(p) for p in points]
            if not internal:
                painter.line(
                    projected,
                    fill="#aeb9c5",
                    width=max(2, round(lane_width * scale)),
                    joint="curve",
                )
                painter.line(projected, fill="#2864bd", width=2, joint="curve")
                lengths = [math.dist(a, b) for a, b in zip(projected, projected[1:], strict=False)]
                target = sum(lengths) / 2
                for index, length in enumerate(lengths):
                    if length > 0 and target <= length:
                        a, b = projected[index], projected[index + 1]
                        x = a[0] + (b[0] - a[0]) * target / length
                        y = a[1] + (b[1] - a[1]) * target / length
                        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                        painter.polygon(
                            [
                                (x + ux * 6, y + uy * 6),
                                (x - ux * 5 - uy * 4, y - uy * 5 + ux * 4),
                                (x - ux * 5 + uy * 4, y - uy * 5 - ux * 4),
                            ],
                            fill="#1554a0",
                        )
                        break
                    target -= length
        for points, _, internal in lanes:
            if internal:
                painter.line([local(p) for p in points], fill="#b85db8", width=2, joint="curve")
        for lane in signal_lanes:
            points = _points(lane.get("shape", ""))
            if len(points) < 2:
                continue
            end = points[-1]
            previous = next((p for p in reversed(points[:-1]) if p != end), None)
            if previous is not None:
                _draw_signal(
                    painter, local(end), local(previous), float(lane.get("width", "3.2")) * scale
                )
        image.paste(panel, (left, top))
        draw.rectangle((left, top, left + side, top + side), outline="#cbd5e1")
    draw.text(
        (30, 850),
        "Blue: lane direction  |  Purple: SUMO internal lanes  |  Geometry from .net.xml",
        fill="#334155",
        font=_font(17),
    )
    draw.text(
        (30, 882),
        "Traffic-light icons: signal-controlled incoming lanes "
        "(schematic placement; not live colors)",
        fill="#334155",
        font=_font(17),
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")


def _map_font(size: int) -> ImageFont.ImageFont:
    for name in (
        "NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/mnt/c/Windows/Fonts/msyh.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return _font(size)


def _origin_annotation(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    name: str,
    ui_language: Literal["en", "zh"],
) -> tuple[tuple[float, float, float, float], str]:
    """Place a bounded callout beside the pin, reserving its space before road labels."""
    name = " ".join(name.split()) or ("Requested place" if ui_language == "en" else "请求地点")
    font = _map_font(19)
    truncated = name
    while len(truncated) > 1 and draw.textlength(truncated, font=font) > 410:
        truncated = truncated[:-1]
    if truncated != name:
        truncated = truncated.rstrip() + "…"
    heading = "Original location" if ui_language == "en" else "原始位置"
    width = max(
        draw.textlength(heading, font=_map_font(21)) + 32,
        draw.textlength(truncated, font=font) + 32,
    )
    x, y = point
    left = x + 35 if x + 35 + width <= 1450 else x - 35 - width
    left = max(25.0, min(left, 1450 - width))
    top = max(114.0, min(y - 77, 905.0))
    return (left, top, left + width, top + 70), truncated


def _draw_origin(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    box: tuple[float, float, float, float],
    name: str,
    ui_language: Literal["en", "zh"],
) -> None:
    """Use the pin tip as the exact projected location; the label may move independently."""
    x, y = point
    left, top, right, bottom = box
    draw.ellipse((x - 27, y - 16, x + 27, y + 16), fill="#ffe4e6")
    draw.ellipse((x - 16, y - 9, x + 16, y + 9), outline="#fb7185", width=2)
    label_anchor = (left if left > x else right, (top + bottom) / 2)
    draw.line([(x, y - 29), label_anchor], fill="#e85b70", width=2)
    draw.rounded_rectangle((left + 2, top + 3, right + 2, bottom + 3), radius=12, fill="#e2e8f0")
    draw.rounded_rectangle(box, radius=12, fill="white", outline="#fda4af", width=2)
    draw.text(
        (left + 15, top + 8),
        "Original location" if ui_language == "en" else "原始位置",
        font=_map_font(21),
        fill="#be123c",
    )
    draw.text((left + 15, top + 39), name, font=_map_font(19), fill="#243647")
    # A white rim keeps the marker distinct from road lines and red signal nodes.
    draw.polygon([(x - 18, y - 28), (x, y + 1), (x + 18, y - 28)], fill="white")
    draw.ellipse((x - 21, y - 59, x + 21, y - 17), fill="white")
    draw.polygon([(x - 15, y - 28), (x, y), (x + 15, y - 28)], fill="#e11d48")
    draw.ellipse((x - 18, y - 56, x + 18, y - 20), fill="#e11d48")
    draw.ellipse((x - 7, y - 45, x + 7, y - 31), fill="white")


def render_map_overview(
    network_path: str,
    output_path: str,
    *,
    title: str,
    radius_m: float,
    detailed: bool = False,
    origin: MapOriginMarker | None = None,
    ui_language: Literal["en", "zh"] = "en",
    source_osm_path: str | None = None,
) -> None:
    """Render localized display names and scale from compiled SUMO XY, with OSM attribution."""
    root = ET.parse(network_path).getroot()
    edges = [e for e in root.findall("edge") if e.get("function") != "internal"]
    geometries = [
        (e, _points(lane.get("shape", ""))) for e in edges if (lane := e.find("lane")) is not None
    ]
    points = [p for _, shape in geometries for p in shape]
    if not points:
        raise ValueError("没有可渲染的道路几何")
    origin_xy = project_origin(root, origin) if origin is not None else None
    if origin_xy is not None:
        # Main-road filtering can leave the requested building outside the visible road bounds.
        points.append(origin_xy)
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(1320 / max(xmax - xmin, 1), 840 / max(ymax - ymin, 1))
    center_x, center_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    image = Image.new("RGB", (1480, 1140), "#f8fafc")
    draw = ImageDraw.Draw(image)

    def xy(point: tuple[float, float]) -> tuple[float, float]:
        return 740 + (point[0] - center_x) * scale, 550 - (point[1] - center_y) * scale

    if ui_language not in {"en", "zh"}:
        raise ValueError("Unsupported preview language")
    source_names = osm_english_names(source_osm_path) if ui_language == "en" else {}

    def display_name(name: str) -> str:
        return english_name(name, source_names) if ui_language == "en" else name

    label = (
        ("Detailed road network" if detailed else "Main-road network")
        if ui_language == "en"
        else ("详细路网" if detailed else "主干道路网")
    )
    heading = display_name(title) + " · " + label
    heading_font = _map_font(30)
    # Romanized names can be considerably longer than the source Chinese name.
    while len(heading) > 1 and draw.textlength(heading, font=heading_font) > 1390:
        heading = heading[:-2].rstrip("… ") + "…"
    draw.text((42, 20), heading, fill="#172334", font=heading_font)
    draw.text(
        (42, 65),
        f"Approx. {radius_m / 1000:g} km radius · North up"
        if ui_language == "en"
        else f"周边约 {radius_m / 1000:g} 公里 · 北向上",
        fill="#475569",
        font=_map_font(20),
    )
    named: dict[str, tuple[float, list[tuple[float, float]]]] = {}
    for edge, shape in geometries:
        if len(shape) < 2:
            continue
        projected = [xy(p) for p in shape]
        arterial = any(
            t in edge.get("type", "") for t in ("primary", "secondary", "trunk", "motorway")
        )
        color = ("#cc8538" if arterial else "#3c7295") if detailed else "#3977a8"
        width = (
            max(3, round(len(edge.findall("lane")) * 3.2 * scale))
            if detailed
            else (7 if arterial else 4)
        )
        draw.line(projected, fill="#ffffff", width=width + 3, joint="curve")
        draw.line(projected, fill=color, width=width, joint="curve")
        name = edge.get("name", "")
        length = sum(math.dist(a, b) for a, b in zip(shape, shape[1:], strict=False))
        if name and length > named.get(name, (0, []))[0]:
            named[name] = (length, projected)
    edge_lookup = {e.get("id"): e for e in edges}
    controlled = {
        edge_lookup[c.get("from")].get("to")
        for c in root.findall("connection")
        if c.get("tl") and c.get("from") in edge_lookup
    }
    for node in root.findall("junction"):
        if detailed and node.get("id") in controlled:
            x, y = xy((float(node.get("x", "0")), float(node.get("y", "0"))))
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill="#dc4a56", outline="white", width=1)
    occupied: list[tuple[float, float, float, float]] = []
    annotation = None
    if origin_xy is not None and origin is not None:
        point = xy(origin_xy)
        annotation = _origin_annotation(draw, point, display_name(origin.name), ui_language)
        occupied.extend(
            [annotation[0], (point[0] - 29, point[1] - 62, point[0] + 29, point[1] + 19)]
        )
    for name, (_, shape) in sorted(named.items(), key=lambda item: -item[1][0]):
        name = display_name(name)
        x, y = shape[len(shape) // 2]
        font = _map_font(18)
        box = draw.textbbox((x, y), name, font=font, anchor="mm")
        if box[0] < 20 or box[2] > 1460 or box[1] < 110 or box[3] > 990:
            continue
        if any(
            not (box[2] + 8 < b[0] or box[0] - 8 > b[2] or box[3] + 8 < b[1] or box[1] - 8 > b[3])
            for b in occupied
        ):
            continue
        draw.rounded_rectangle(
            (box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), radius=3, fill="#f8fafc"
        )
        draw.text((x, y), name, fill="#243647", font=font, anchor="mm")
        occupied.append(box)
    if annotation is not None and origin_xy is not None:
        _draw_origin(draw, xy(origin_xy), *annotation, ui_language)
    meters = 100 if 100 * scale >= 50 else 500
    draw.line((55, 990, 55 + meters * scale, 990), fill="#334155", width=3)
    draw.text((55, 1000), f"{meters} m", fill="#334155", font=_font(17))
    draw.polygon([(1410, 132), (1402, 153), (1418, 153)], fill="#334155")
    draw.text((1402, 104), "N", fill="#334155", font=_font(20))
    legend = (
        (
            "Orange: main roads   Blue: other roads   Red dots: SUMO signal controls"
            if detailed
            else "Main roads and their connections"
        )
        if ui_language == "en"
        else (
            "橙：主干道路   蓝：其他机动车道路   红点：SUMO 信控节点"
            if detailed
            else "主要道路与连接关系"
        )
    )
    note = (
        (
            "Roads: OpenStreetMap. Lane attributes, connections and signals "
            "may be inferred by SUMO."
            if detailed
            else "Request more detail to include minor roads, lanes and signal information."
        )
        if ui_language == "en"
        else (
            "道路来自 OpenStreetMap；车道属性、连接及信号配时可能由 SUMO 推断。"
            if detailed
            else "需要更详细时，可要求包含支路、车道和信号信息。"
        )
    )
    draw.text(
        (42, 1040),
        legend,
        fill="#475569",
        font=_map_font(20),
    )
    draw.text(
        (42, 1072),
        note,
        fill="#475569",
        font=_map_font(19),
    )
    draw.text(
        (42, 1104),
        "© OpenStreetMap contributors · ODbL · openstreetmap.org/copyright",
        fill="#64748b",
        font=_font(17),
    )
    image.save(output_path, format="PNG")
