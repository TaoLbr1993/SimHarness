"""Bounded road operations on a compiled draft, with SUMO rebuilding junction connections."""

import json
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Literal

import agent
import config
import realmap
from pydantic import BaseModel, ConfigDict, Field
from road_display import CLASS_LABELS, MapOperation
from scenario_demand import TrafficNetwork, passenger_allowed
from scenario_models import AppliedMapEdit


class MapEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    instruction: str = Field(min_length=1, max_length=2000)
    ui_language: Literal["en", "zh"] = "en"


class EditIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: MapOperation | Literal["clarify"]
    reason: str = Field(default="", max_length=500)


def interpret(instruction: str) -> str:
    phrase = instruction.lower().strip(" 。.!！")
    for axis, pattern in (
        ("ew", "东西|东[-–]西|east[- –]west"),
        ("ns", "南北|南[-–]北|north[- –]south"),
    ):
        for action, verb in (
            ("add", "增加|新增|添加|加|add"),
            ("remove", "删除|移除|去掉|删掉|remove|delete"),
        ):
            if re.fullmatch(
                rf"(?:请)?(?:{verb})\s*(?:一条|an? |the )?(?:{pattern})"
                r"(?:方向|向)?\s*(?:道路|路|road)",
                phrase,
            ):
                return f"{action}_{axis}"
    if phrase in {
        "只显示主干道",
        "只看主干道",
        "仅显示主干道",
        "show main roads",
        "show only main roads",
    }:
        return "show_main"
    if phrase in {"显示全部道路", "显示所有道路", "显示所有路网等级", "show all roads"}:
        return "show_all"
    for key, (zh, en) in CLASS_LABELS.items():
        if re.fullmatch(rf"(?:只|仅)?显示{zh}|show (?:only )?{en.lower()}", phrase):
            return "show_" + key
    payload = json.loads(agent.interpret_road_edit(instruction))
    if isinstance(payload, dict) and payload.get("type") == "json_object":
        del payload["type"]
    intent = EditIntent.model_validate(payload)
    if intent.operation == "clarify":
        raise ValueError(
            intent.reason or "请指定道路方向或显示等级。 Specify a direction or display level."
        )
    return intent.operation


def change_roads(
    source: Path, target: Path, operation: str, task_id: str, history: list[AppliedMapEdit]
) -> list[str]:
    root = ET.parse(source).getroot()
    nodes = {
        j.attrib["id"]: (float(j.attrib["x"]), float(j.attrib["y"]))
        for j in root.findall("junction")
        if j.get("type") != "internal"
    }
    neighbors: dict[str, set[str]] = defaultdict(set)
    roads = {}
    lane_index = {}
    for edge in root.findall("edge"):
        a, b = edge.get("from"), edge.get("to")
        if (
            a in nodes
            and b in nodes
            and a != b
            and any(passenger_allowed(lane) for lane in edge.findall("lane"))
        ):
            roads[edge.attrib["id"]] = (a, b)
            lane_index[edge.attrib["id"]] = next(
                lane.attrib["index"] for lane in edge.findall("lane") if passenger_allowed(lane)
            )
            neighbors[a].add(b)
            neighbors[b].add(a)
    if not roads:
        raise ValueError(
            "No connected passenger roads in the current map. 当前地图没有相连的机动车道路。"
        )
    xs, ys = zip(*(nodes[node] for node in neighbors), strict=True)
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    axis = operation.split("_")[1]
    index = 0 if axis == "ew" else 1

    # An arm within 30 degrees of the requested axis counts as occupying that side.
    def side(a: str, b: str) -> int:
        along = nodes[b][index] - nodes[a][index]
        across = nodes[b][1 - index] - nodes[a][1 - index]
        if abs(along) > 0 and abs(across) <= abs(along) / math.sqrt(3):
            return 1 if along > 0 else -1
        return 0

    ordered = sorted(neighbors, key=lambda node: (math.dist(nodes[node], center), node))
    node_patch, edge_patch = ET.Element("nodes"), ET.Element("edges")
    connections = ET.Element("connections")
    prefix = f"tf_{axis}_{task_id}_"
    if operation.startswith("add"):
        available = {
            node: {-1, 1}.difference(side(node, n) for n in neighbors[node]) for node in ordered
        }
        anchor = max(ordered, key=lambda node: len(available[node]))
        if not available[anchor]:
            raise ValueError(
                "No junction has space for this crossing. 当前路网没有适合新增该方向道路的路口。"
            )
        # Keep new arms local to the junction, proportional to its neighbouring blocks.
        span = min(math.dist(nodes[anchor], nodes[n]) for n in neighbors[anchor]) * 0.65
        if span < 10:
            raise ValueError("Insufficient space at the junction. 路口可用距离太短。")
        affected = []
        for suffix, sign in (("a", -1), ("b", 1)):
            if sign not in available[anchor]:
                continue
            point = list(nodes[anchor])
            point[index] += sign * span
            end = prefix + suffix
            ET.SubElement(node_patch, "node", id=end, x=str(point[0]), y=str(point[1]))
            for direction, (a, b) in enumerate(((anchor, end), (end, anchor))):
                edge_id = f"{end}_{direction}"
                ET.SubElement(
                    edge_patch,
                    "edge",
                    id=edge_id,
                    attrib={"from": a, "to": b},
                    numLanes="1",
                    speed="13.89",
                    priority="1",
                )
                affected.append(edge_id)
        # Imported .net.xml carries explicit turns. Add turns incident to each new arm
        # without discarding existing turn restrictions anywhere in the source map.
        incoming = {eid: a for eid, (a, b) in roads.items() if b == anchor}
        outgoing = {eid: b for eid, (a, b) in roads.items() if a == anchor}
        for edge in edge_patch:
            a, b, eid = (edge.attrib[key] for key in ("from", "to", "id"))
            if b == anchor:
                incoming[eid] = a
            else:
                outgoing[eid] = b
        for start, origin in incoming.items():
            for end, destination in outgoing.items():
                if origin != destination and (start in affected or end in affected):
                    ET.SubElement(
                        connections,
                        "connection",
                        {
                            "from": start,
                            "to": end,
                            "fromLane": lane_index.get(start, "0"),
                            "toLane": lane_index.get(end, "0"),
                        },
                    )
    else:
        latest = next(
            (
                edit
                for edit in reversed(history)
                if edit.operation == f"add_{axis}"
                and set(edit.edge_ids).issubset(roads)
                and edit.edge_ids
            ),
            None,
        )
        if latest:
            affected = latest.edge_ids
        else:
            anchor = next(
                (node for node in ordered if any(side(node, n) for n in neighbors[node])), None
            )
            if anchor is None:
                raise ValueError("No road in the requested direction. 当前地图中没有该方向的道路。")
            affected = [eid for eid, (a, b) in roads.items() if anchor in (a, b) and side(a, b)]
        if len(affected) == len(roads):
            raise ValueError("This would remove every road. 此修改会删掉全部可行驶道路。")
        for eid in affected:
            ET.SubElement(edge_patch, "delete", id=eid)

    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        directory = Path(temporary)
        for name, tree in (
            ("patch.nod.xml", node_patch),
            ("patch.edg.xml", edge_patch),
            ("patch.con.xml", connections),
        ):
            ET.ElementTree(tree).write(directory / name, encoding="utf-8", xml_declaration=True)
        command = [
            "netconvert",
            "--sumo-net-file",
            str(source),
            "--node-files",
            "patch.nod.xml",
            "--edge-files",
            "patch.edg.xml",
            "--connection-files",
            "patch.con.xml",
            "--offset.disable-normalization",
            "true",
            "--output-file",
            str(target),
        ]
        process = subprocess.run(
            command,
            cwd=directory,
            env=realmap._tool_environment(),
            capture_output=True,
            text=True,
            timeout=config.REAL_MAP.tool_timeout_sec,
        )
        if process.returncode:
            raise ValueError(
                "SUMO could not rebuild the edited junction: " + process.stderr[-1200:]
            )
    net = TrafficNetwork.read(target)
    if operation.startswith("add"):
        if (
            not all(
                e in net.roads and (net.next_edges.get(e) or net.previous_edges.get(e))
                for e in affected
            )
            or not any(net.next_edges.get(e, set()).intersection(roads) for e in affected)
            or not any(net.previous_edges.get(e, set()).intersection(roads) for e in affected)
        ):
            raise ValueError("The added road has no valid connections. 新道路未能接入路网。")
    elif set(affected).intersection(net.roads):
        raise ValueError("SUMO did not remove the requested road.")
    return sorted(affected)
