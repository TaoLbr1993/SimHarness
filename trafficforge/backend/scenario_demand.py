"""Deterministic passenger demand from actual compiled SUMO lane connections."""

import heapq
import math
import random
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scenario_models import (
    AutomationLevel,
    GeneratedAutomationShare,
    RouteDemand,
    ScenarioPlan,
    ScenarioSettings,
    validate_automation_mix,
)

# Match the existing Core SUMO level presets; these are behavior proxies, not autonomy stacks.
_LEVEL_PARAMETERS: dict[AutomationLevel, dict[str, str]] = {
    "L0": {"color": "0,114,189", "minGap": "2.5", "sigma": "0.5", "tau": "1"},
    "L1": {"color": "217,83,25", "minGap": "2", "sigma": "0.4", "tau": "0.95"},
    "L2": {"color": "237,177,32", "minGap": "1.5", "sigma": "0.3", "tau": "0.9"},
    "L3": {"color": "46,139,87", "minGap": "1.25", "sigma": "0.2", "tau": "0.8"},
    "L4": {"color": "126,87,194", "minGap": "0.75", "sigma": "0", "tau": "0.7"},
    "L5": {"color": "190,63,63", "minGap": "0.5", "sigma": "0", "tau": "0.6"},
}


def _automation_allocation(plan: ScenarioPlan, count: int) -> list[GeneratedAutomationShare]:
    """Use largest remainders, with level order breaking ties, to preserve the exact total."""
    if not plan.automation_mix:
        return []
    validate_automation_mix(plan.automation_mix)
    weights = {item.level: item.share_percent for item in plan.automation_mix}
    raw = {level: count * weights.get(level, 0) / 100 for level in _LEVEL_PARAMETERS}
    counts = {level: math.floor(value) for level, value in raw.items()}
    remaining = count - sum(counts.values())
    order = sorted(raw, key=lambda level: (-(raw[level] - counts[level]), level))
    for level in order[:remaining]:
        counts[level] += 1
    return [
        GeneratedAutomationShare(
            level=level, vehicle_count=amount, share_percent=100 * amount / count
        )
        for level, amount in counts.items()
    ]


def _write_vehicle_types(root: ET.Element, mix: list[GeneratedAutomationShare]) -> None:
    if mix:
        for item in mix:
            if item.vehicle_count:
                ET.SubElement(
                    root,
                    "vType",
                    {
                        "id": item.level,
                        "vClass": "passenger",
                        "length": "5",
                        "carFollowModel": "Krauss",
                        "guiShape": "passenger",
                        "accel": "2.6",
                        "decel": "4.5",
                        "maxSpeed": "22.22",
                        **_LEVEL_PARAMETERS[item.level],
                    },
                )
    else:
        ET.SubElement(
            root,
            "vType",
            id="passenger",
            vClass="passenger",
            length="5",
            minGap="2.5",
            accel="2.6",
            decel="4.5",
            tau="1",
            sigma="0.5",
            speedFactor="1",
            speedDev="0",
        )


def passenger_allowed(lane: ET.Element) -> bool:
    allowed, denied = lane.get("allow", "").split(), lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or "passenger" in allowed) and not {
        "all",
        "passenger",
    }.intersection(denied)


@dataclass
class Road:
    id: str
    lanes: set[str]
    length_m: float
    lane_length_m: float


@dataclass
class TrafficNetwork:
    roads: dict[str, Road]
    next_edges: dict[str, set[str]]
    previous_edges: dict[str, set[str]]

    @classmethod
    def read(cls, path: Path) -> "TrafficNetwork":
        root = ET.parse(path).getroot()
        if root.tag != "net":
            raise ValueError("场景输入必须为编译后的 SUMO 网络")
        roads = {}
        for edge in root.findall("edge"):
            if edge.get("function") == "internal":
                continue
            lanes = [lane for lane in edge.findall("lane") if passenger_allowed(lane)]
            if not lanes:
                continue
            lengths = [float(lane.attrib["length"]) for lane in lanes]
            if any(not math.isfinite(length) or length <= 0 for length in lengths):
                raise ValueError("网络包含非正或无效车道长度")
            edge_id = edge.attrib["id"]
            roads[edge_id] = Road(
                edge_id,
                {lane.attrib["index"] for lane in lanes},
                sum(lengths) / len(lanes),
                sum(lengths),
            )
        following: dict[str, set[str]] = defaultdict(set)
        previous: dict[str, set[str]] = defaultdict(set)
        for connection in root.findall("connection"):
            start, end = connection.get("from", ""), connection.get("to", "")
            if start not in roads or end not in roads or connection.get("dir", "").lower() == "t":
                continue
            if (
                connection.get("fromLane") in roads[start].lanes
                and connection.get("toLane") in roads[end].lanes
            ):
                following[start].add(end)
                previous[end].add(start)
        return cls(roads, dict(following), dict(previous))

    def paths_from(self, origin: str) -> dict[str, tuple[float, list[str]]]:
        distances = {origin: self.roads[origin].length_m}
        parent: dict[str, str] = {}
        queue = [(distances[origin], origin)]
        while queue:
            distance, current = heapq.heappop(queue)
            if distance > distances[current]:
                continue
            for neighbor in sorted(self.next_edges.get(current, set())):
                proposed = distance + self.roads[neighbor].length_m
                if proposed < distances.get(neighbor, math.inf):
                    distances[neighbor] = proposed
                    parent[neighbor] = current
                    heapq.heappush(queue, (proposed, neighbor))
        paths = {}
        for destination, distance in distances.items():
            if destination == origin:
                continue
            path = [destination]
            while path[-1] != origin:
                path.append(parent[path[-1]])
            paths[destination] = (distance, list(reversed(path)))
        return paths


def build_demand(
    network_path: Path, output: Path, plan: ScenarioPlan, settings: ScenarioSettings
) -> RouteDemand:
    """Write valid route chains and an exact, bounded departure schedule; do not change the map."""
    network = TrafficNetwork.read(network_path)
    if not network.roads:
        raise ValueError("没有可供小客车行驶的道路")
    rng = random.Random(plan.seed)
    sources = sorted(edge for edge in network.roads if not network.previous_edges.get(edge))
    source_policy = "boundary_inlets"
    if not sources:
        sources = sorted(network.roads)
        source_policy = "reachable_internal_origins (no boundary inlets)"
    rng.shuffle(sources)
    sinks = {edge for edge in network.roads if not network.next_edges.get(edge)}
    routes: dict[str, list[str]] = {}
    by_origin: dict[str, list[str]] = {}
    internal_destinations = False
    for origin in sources:
        paths = network.paths_from(origin)
        candidates = [edge for edge in paths if edge in sinks]
        if not candidates:
            candidates = list(paths)
            internal_destinations = internal_destinations or bool(candidates)
        candidates = [edge for edge in candidates if paths[edge][0] >= 50]
        if not candidates:
            continue
        # Longest reachable destinations avoid injecting trips that immediately disappear.
        chosen = sorted(candidates, key=lambda edge: (-paths[edge][0], edge))[
            : settings.destinations_per_origin
        ]
        by_origin[origin] = []
        for destination in chosen:
            route_id = f"route_{len(routes)}"
            routes[route_id] = paths[destination][1]
            by_origin[origin].append(route_id)
        if len(by_origin) >= settings.max_origins:
            break
    if not routes:
        raise ValueError("没有至少两段、长度不小于 50 米的可达小客车路线，请检查路网连接")
    slots = [origin for origin in by_origin for _ in network.roads[origin].lanes]
    rng.shuffle(slots)
    total_flow = (
        plan.requested_total_flow_veh_per_hour
        if plan.demand_mode == "total_flow" and plan.requested_total_flow_veh_per_hour is not None
        else (
            (plan.inflow_veh_per_hour_per_lane or 0) * len(slots)
            if plan.requested_vehicle_count is None
            else plan.requested_vehicle_count * 3600 / plan.duration_s
        )
    )
    count = (
        plan.requested_vehicle_count
        if plan.requested_vehicle_count is not None
        else math.floor(total_flow * plan.duration_s / 3600)
    )
    if not 1 <= count <= settings.max_vehicles:
        raise ValueError(
            f"本次需求会计划发车 {count} 辆，允许 1–{settings.max_vehicles} 辆；请降低流量或时长"
        )
    root = ET.Element("routes")
    automation_mix = _automation_allocation(plan, count)
    _write_vehicle_types(root, automation_mix)
    vehicle_types = [item.level for item in automation_mix for _ in range(item.vehicle_count)]
    # A separate RNG leaves the route/demand schedule invariant to a mix-only change.
    random.Random(plan.seed).shuffle(vehicle_types)
    for route_id, edges in routes.items():
        ET.SubElement(root, "route", id=route_id, edges=" ".join(edges))
    for index in range(count):
        origin = slots[index % len(slots)]
        vehicle_type = vehicle_types[index] if vehicle_types else "passenger"
        ET.SubElement(
            root,
            "vehicle",
            id=(f"veh_{vehicle_type}_{index:06d}" if automation_mix else f"veh_{index:06d}"),
            type=vehicle_type,
            route=rng.choice(by_origin[origin]),
            depart=f"{index * plan.duration_s / count:.6f}",
            departLane="best",
            departSpeed="0",
        )
    ET.indent(root)
    ET.ElementTree(root).write(output / "traffic.rou.xml", encoding="utf-8", xml_declaration=True)
    return RouteDemand(
        planned_vehicles=count,
        route_count=len(routes),
        origin_edges=list(by_origin),
        origin_lane_count=len(slots),
        external_passenger_lane_km=sum(road.lane_length_m for road in network.roads.values())
        / 1000,
        routed_edges=len({edge for route in routes.values() for edge in route}),
        eligible_edges=len(network.roads),
        requested_inflow_veh_per_hour=total_flow,
        realized_schedule_veh_per_hour=count * 3600 / plan.duration_s,
        source_policy=source_policy
        + ("; some reachable internal destinations" if internal_destinations else ""),
        automation_mix=automation_mix,
    )


def write_simulation_config(output: Path, plan: ScenarioPlan, settings: ScenarioSettings) -> None:
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "laneData",
        id="scenario_metrics",
        file="lane_metrics.xml",
        period="30",
        begin=str(plan.warmup_s),
        end=str(plan.duration_s),
        excludeEmpty="false",
        withInternal="false",
    )
    ET.ElementTree(additional).write(
        output / "metrics.add.xml", encoding="utf-8", xml_declaration=True
    )
    root = ET.Element("configuration")
    groups = {
        "input": {
            "net-file": "network.net.xml",
            "route-files": "traffic.rou.xml",
            "additional-files": "metrics.add.xml",
        },
        "time": {
            "begin": "0",
            "end": str(plan.duration_s),
            "step-length": str(settings.step_length_s),
        },
        "processing": {"time-to-teleport": "-1", "collision.action": "warn"},
        "random_number": {"seed": str(plan.seed)},
        "output": {
            "summary-output": "summary.xml",
            "summary-output.period": str(settings.step_length_s),
            "tripinfo-output": "tripinfo.xml",
            "tripinfo-output.write-unfinished": "true",
            "tripinfo-output.write-undeparted": "true",
            "statistic-output": "statistics.xml",
        },
        "report": {"no-step-log": "true", "duration-log.statistics": "true"},
    }
    for group, values in groups.items():
        container = ET.SubElement(root, group)
        for key, value in values.items():
            ET.SubElement(container, key, value=value)
    ET.indent(root)
    ET.ElementTree(root).write(output / "scenario.sumocfg", encoding="utf-8", xml_declaration=True)
