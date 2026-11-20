"""Parse validated SUMO input assets without starting or importing SUMO."""

import math
import xml.etree.ElementTree as ET

from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.enums import ErrorCode
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models.shared_scene import (
    SceneConnection,
    SceneLane,
    SceneSignal,
    SceneStop,
    SceneVehicle,
    SharedScene,
)
from trafficverse.maps.sumo_package import SumoScenarioPackage


def shared_highway_config(package: SumoScenarioPackage, duration_ms: int) -> HighwayEnvConfig:
    """Use the source step exactly when constructing the shared HighwayEnv clock."""
    if 1000 % package.step_ms:
        raise TrafficVerseError(
            ErrorCode.SCENARIO_VALIDATION_FAILED,
            f"HighwayEnv requires a step that divides 1000 ms exactly, got {package.step_ms} ms",
        )
    frequency = 1000 // package.step_ms
    scene = convert_sumo_scene(package)
    return HighwayEnvConfig(
        duration_ms=duration_ms,
        shared_scene=scene,
        seed=scene.seed,
        policy_frequency=frequency,
        simulation_frequency=frequency * max(1, 20 // frequency),
    )


def convert_sumo_scene(package: SumoScenarioPackage) -> SharedScene:
    network = ET.parse(package.network_path).getroot()
    seed_element = ET.parse(package.config_path).getroot().find("random_number/seed")
    seed = int(seed_element.get("value", "42")) if seed_element is not None else 42
    lanes = []
    for edge in network.findall("edge"):
        for lane in edge.findall("lane"):
            points = tuple(
                tuple(float(v) for v in p.split(",")[:2]) for p in lane.attrib["shape"].split()
            )
            lanes.append(
                SceneLane(
                    lane_id=lane.attrib["id"],
                    edge_id=edge.attrib["id"],
                    points=points,
                    width_m=float(lane.get("width", "3.2")),
                    speed_mps=float(lane.get("speed", "30")),
                )
            )
    connections = tuple(
        SceneConnection(
            source_lane=f"{c.attrib['from']}_{c.attrib['fromLane']}",
            target_lane=f"{c.attrib['to']}_{c.attrib['toLane']}",
            via_lane=c.get("via"),
            signal_id=c.get("tl"),
            signal_index=int(c.get("linkIndex", "0")),
        )
        for c in network.findall("connection")
        if not c.attrib["from"].startswith(":")
    )
    roots = [ET.parse(p).getroot() for p in (*package.route_paths, *package.additional_paths)]
    signals = {}
    for root in [network, *roots]:
        for logic in root.findall("tlLogic"):
            if logic.get("type", "static") != "static":
                raise ValueError("HighwayEnv conversion requires fixed-time signal programs")
            signals[logic.attrib["id"]] = SceneSignal(
                signal_id=logic.attrib["id"],
                offset_s=float(logic.get("offset", "0")),
                phases=tuple(
                    (float(p.attrib["duration"]), p.attrib["state"]) for p in logic.findall("phase")
                ),
            )
    routes = {
        r.attrib["id"]: tuple(r.attrib["edges"].split())
        for root in roots
        for r in root.findall("route")
    }
    types = {v.attrib["id"]: v.attrib for root in roots for v in root.findall("vType")}
    vehicles = []
    for root in roots:
        if any(
            root.findall(tag)
            for tag in (
                "trip",
                "person",
                "personFlow",
                "routeDistribution",
                "vTypeDistribution",
                "rerouter",
                "calibrator",
            )
        ):
            raise ValueError("HighwayEnv conversion requires explicit vehicle routes")
        for element in root:
            if element.tag not in {"vehicle", "flow"}:
                continue
            if element.find("routeDistribution") is not None or "probability" in element.attrib:
                raise ValueError("HighwayEnv conversion requires deterministic explicit demand")
            inline = element.find("route")
            route_id = element.get("route", element.attrib["id"])
            edges = (
                tuple(inline.attrib["edges"].split()) if inline is not None else routes[route_id]
            )
            departures = [float(element.get("depart", "0"))]
            if element.tag == "flow":
                begin, end = float(element.get("begin", "0")), float(element.get("end", "3600"))
                if "vehsPerHour" in element.attrib:
                    period = 3600 / float(element.attrib["vehsPerHour"])
                elif "period" in element.attrib:
                    period = float(element.attrib["period"])
                elif "number" in element.attrib:
                    period = (end - begin) / max(1, int(element.attrib["number"]))
                else:
                    raise ValueError("HighwayEnv flow requires number, period or vehsPerHour")
                if not math.isfinite(period) or period <= 0:
                    raise ValueError("HighwayEnv flow requires a positive finite period")
                count = (
                    int(element.attrib["number"])
                    if "number" in element.attrib
                    else math.ceil((end - begin) / period)
                )
                if count < 0 or count > 100000:
                    raise ValueError("HighwayEnv departure plan is too large")
                departures = [
                    begin + i * period
                    for i in range(count)
                    if "end" not in element.attrib or begin + i * period < end
                ]
            vehicle_type = types.get(element.get("type", ""), {})
            stops = tuple(
                SceneStop(
                    lane_id=s.attrib["lane"],
                    position_m=float(s.get("endPos", s.get("pos", "0"))),
                    duration_s=float(s.get("duration", "0")),
                    until_s=float(s.attrib["until"]) if "until" in s.attrib else None,
                )
                for s in element.findall("stop")
            )
            for index, depart in enumerate(departures):
                identifier = element.attrib["id"]
                if element.tag == "flow":
                    identifier += f".{index}"
                vehicles.append(
                    SceneVehicle(
                        vehicle_id=identifier,
                        route_id=route_id,
                        edges=edges,
                        depart_s=depart,
                        depart_lane=element.get("departLane", "best"),
                        depart_position_m=(
                            float(vehicle_type.get("length", "5"))
                            + float(vehicle_type.get("minGap", "2.5"))
                            if element.get("departPos", "base") == "base"
                            else float(element.attrib["departPos"])
                        ),
                        depart_speed=element.get("departSpeed", "0"),
                        type_id=element.get("type", "DEFAULT_VEHTYPE"),
                        length_m=float(vehicle_type.get("length", "5")),
                        width_m=float(vehicle_type.get("width", "1.8")),
                        max_speed_mps=float(vehicle_type.get("maxSpeed", "30")),
                        acceleration_mps2=float(vehicle_type.get("accel", "3")),
                        deceleration_mps2=float(vehicle_type.get("decel", "4.5")),
                        min_gap_m=float(vehicle_type.get("minGap", "2.5")),
                        headway_s=float(vehicle_type.get("tau", "1.5")),
                        stops=stops,
                    )
                )
    return SharedScene(
        map_id=package.package_id,
        seed=seed,
        begin_time_s=package.begin_time_ms / 1000,
        lanes=tuple(lanes),
        connections=connections,
        signals=tuple(signals.values()),
        # Stable sorting preserves source order for simultaneous departures. Sorting
        # by ID can insert a replenishment flow before the scripted initial layout.
        vehicles=tuple(sorted(vehicles, key=lambda v: v.depart_s)),
    )
