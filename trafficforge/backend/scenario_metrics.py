"""Observed metrics from SUMO outputs, separate from requested demand and density hints."""

import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from scenario_demand import passenger_allowed
from scenario_models import RouteDemand, ScenarioMetrics, ScenarioPlan, ScenarioSample


def _number(node: ET.Element, key: str) -> float:
    value = float(node.attrib[key])
    if not math.isfinite(value):
        raise ValueError(f"SUMO 输出 {key} 不是有限数值")
    return value


def collect_metrics(output: Path, plan: ScenarioPlan, demand: RouteDemand) -> ScenarioMetrics:
    summary = ET.parse(output / "summary.xml").getroot().findall("step")
    if not summary:
        raise ValueError("SUMO 未输出任何仿真时步，不能计算车速")
    times = [_number(step, "time") for step in summary]
    if any(b <= a for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("SUMO 时间序列不递增")
    observed_duration = vehicle_seconds = speed_total = halting_total = congested_seconds = 0.0
    samples = []
    for index, step in enumerate(summary):
        time = times[index]
        stop = times[index + 1] if index + 1 < len(times) else min(time + 1, plan.duration_s)
        duration = max(0, min(stop, plan.duration_s) - max(time, plan.warmup_s))
        if duration == 0:
            continue
        running, halting = int(_number(step, "running")), int(_number(step, "halting"))
        speed_raw, relative_raw = _number(step, "meanSpeed"), _number(step, "meanSpeedRelative")
        speed = speed_raw if running > 0 and speed_raw >= 0 else None
        relative = relative_raw if running > 0 and relative_raw >= 0 else None
        samples.append(
            ScenarioSample(
                time_s=max(time, plan.warmup_s),
                running=running,
                waiting_to_enter=int(_number(step, "waiting")),
                halting=halting,
                speed_kmh=speed * 3.6 if speed is not None else None,
                speed_ratio=relative,
            )
        )
        observed_duration += duration
        halting_total += halting * duration
        if speed is not None:
            vehicle_seconds += running * duration
            speed_total += speed * running * duration
        if (
            running > 0
            and relative is not None
            and relative <= plan.congestion_speed_ratio
            and halting / running >= plan.congestion_halting_fraction
        ):
            congested_seconds += duration
    if observed_duration + 1 < plan.duration_s - plan.warmup_s:
        raise ValueError("SUMO 试运行未覆盖设定的观测时段，不能把部分输出当成完整测试")
    network = ET.parse(output / "network.net.xml").getroot()
    eligible_lanes = {
        lane.attrib["id"]
        for edge in network.findall("edge")
        if edge.get("function") != "internal"
        for lane in edge.findall("lane")
        if passenger_allowed(lane)
    }
    external_vehicle_seconds = density_duration = 0.0
    for interval in ET.parse(output / "lane_metrics.xml").getroot().findall("interval"):
        begin, end = _number(interval, "begin"), _number(interval, "end")
        if begin < plan.warmup_s - 1e-6 or end > plan.duration_s + 1e-6 or end <= begin:
            raise ValueError("密度统计区间不符合预热/结束设置")
        density_duration += end - begin
        for lane in interval.iter("lane"):
            if lane.get("id") in eligible_lanes:
                external_vehicle_seconds += _number(lane, "sampledSeconds")
    statistics = ET.parse(output / "statistics.xml").getroot()
    safety, teleports = statistics.find("safety"), statistics.find("teleports")
    vehicles = statistics.find("vehicles")
    if safety is None or teleports is None or vehicles is None:
        raise ValueError("SUMO 缺少碰撞或传送统计，无法确认试运行有效性")
    collisions = int(_number(safety, "collisions"))
    teleport_count = int(_number(teleports, "total"))
    fraction = (
        congested_seconds / observed_duration
        if vehicle_seconds > 0 and observed_duration > 0
        else None
    )
    congested = fraction >= plan.congestion_time_fraction if fraction is not None else None
    target = "observed" if congested else "not_observed"
    reason = (
        f"预热后低相对速度且排队的时段占 {fraction:.1%}；判据为相对速度≤"
        f"{plan.congestion_speed_ratio:g} 且停驶比例≥{plan.congestion_halting_fraction:.0%}，"
        f"持续比例≥{plan.congestion_time_fraction:.0%}。"
        if fraction is not None
        else "观测窗口没有有效车辆速度数据。"
    )
    if plan.traffic_state != "congested":
        target = "not_requested"
    if fraction is None:
        target = "no_data"
    if collisions or teleport_count:
        target = "invalid_run"
        congested = None
        reason = f"发生 {collisions} 次碰撞、{teleport_count} 次传送，不能视为有效拥堵测试。"
    trip_durations = [
        _number(trip, "duration")
        for trip in ET.parse(output / "tripinfo.xml").getroot().findall("tripinfo")
        if _number(trip, "arrival") >= 0 and _number(trip, "depart") >= 0
    ]
    inserted = int(_number(vehicles, "inserted"))
    result = ScenarioMetrics(
        mean_speed_kmh=speed_total / vehicle_seconds * 3.6 if vehicle_seconds > 0 else None,
        mean_external_density_veh_per_km_lane=(
            external_vehicle_seconds / density_duration / demand.external_passenger_lane_km
            if density_duration > 0
            else None
        ),
        mean_halting_vehicles=halting_total / observed_duration if observed_duration else None,
        max_halting_vehicles=max((sample.halting for sample in samples), default=0),
        inserted_vehicles=inserted,
        arrived_vehicles=len(trip_durations),
        unfinished_vehicles=int(_number(vehicles, "running")),
        not_inserted_vehicles=max(0, demand.planned_vehicles - inserted),
        pending_at_end=int(_number(vehicles, "waiting")),
        collision_events=collisions,
        teleport_events=teleport_count,
        observation_duration_s=observed_duration,
        density_observation_duration_s=density_duration,
        congestion_time_fraction=fraction,
        congestion_observed=congested,
        target_state=target,
        reason=reason,
        mean_completed_travel_time_s=sum(trip_durations) / len(trip_durations)
        if trip_durations
        else None,
        samples=samples,
    )
    with (output / "timeseries.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ScenarioSample.model_fields))
        writer.writeheader()
        writer.writerows(sample.model_dump() for sample in samples)
    (output / "metrics.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
