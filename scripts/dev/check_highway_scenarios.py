"""Run original scene controllers against HighwayEnv and summarize actual outcomes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from uuid import uuid4

from trafficverse.adapters.highway_env.adapter import HighwayTrafficEngineAdapter
from trafficverse.config.models import HighwayEnvConfig
from trafficverse.controllers import controller_for_sumo_package
from trafficverse.maps.shared_scene import convert_sumo_scene
from trafficverse.maps.sumo_package import load_sumo_package


def check_scene(root: Path, scenario_id: str, duration_s: int) -> dict[str, object]:
    """Advance a real adapter, including events, without the external-agent bypass."""
    source = root / "configs/maps" / scenario_id / f"{scenario_id}.sumocfg"
    package = load_sumo_package(source, allowed_root=source.parent, package_id=scenario_id)
    scene = convert_sumo_scene(package)
    frequency = 1000 // package.step_ms
    adapter = HighwayTrafficEngineAdapter(uuid4())
    controller = controller_for_sumo_package(scenario_id)
    previous = None
    first_collisions: dict[str, int] = {}
    rejected_active: dict[str, int] = {}
    visited: set[str] = set()
    merged_ramp_ids: set[str] = set()
    ambulance_x_m: float | None = None
    try:
        adapter.load(
            HighwayEnvConfig(
                shared_scene=scene,
                seed=scene.seed,
                policy_frequency=frequency,
                simulation_frequency=max(20, frequency),
                duration_ms=duration_s * 1000,
            )
        )
        for time_ms in range(package.step_ms, duration_s * 1000 + 1, package.step_ms):
            commands = controller.step(previous, package.step_ms / 1000) if controller else {}
            rejected = adapter.apply_step_controls(commands)
            for identifier in rejected:
                if previous and identifier not in previous.collision_vehicle_ids:
                    rejected_active.setdefault(identifier, time_ms)
            current = adapter.step(time_ms)
            for identifier in current.collision_vehicle_ids:
                first_collisions.setdefault(identifier, time_ms)
            for vehicle in current.vehicles:
                visited.add(vehicle.vehicle_id)
                if vehicle.vehicle_id.startswith("merge_ramp_") and vehicle.lane_id.startswith(
                    "main_after_"
                ):
                    merged_ramp_ids.add(vehicle.vehicle_id)
                if not all(
                    math.isfinite(v)
                    for v in (
                        vehicle.position.x,
                        vehicle.position.y,
                        vehicle.speed_mps,
                        vehicle.heading_rad,
                    )
                ):
                    raise AssertionError(f"nonfinite vehicle state: {vehicle.vehicle_id}")
                if vehicle.vehicle_id == "ambulance_L5_0":
                    ambulance_x_m = vehicle.position.x
            previous = current
        assert previous is not None and previous.episode_ended
        return {
            "scenario": scenario_id,
            "duration_s": duration_s,
            "steps": previous.sequence,
            "vehicle_count_seen": len(visited),
            "vehicle_count_arrived": len(visited - {v.vehicle_id for v in previous.vehicles}),
            "merged_ramp_count": len(merged_ramp_ids),
            "collisions": first_collisions,
            "rejected_active": rejected_active,
            "ambulance_x_m": ambulance_x_m,
        }
    finally:
        adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("--duration-s", type=int, default=60)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = check_scene(root, args.scenario, args.duration_s)
    output = root / "artifacts/highway-scenario-check" / f"{args.scenario}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(result, ensure_ascii=False))
    if args.scenario in {
        "mixed-automation-emergency-yield",
        "mixed-automation-l5-merge",
        "mixed-automation-low-level-merge",
    }:
        assert not result["collisions"], "unexpected collision in a yielding scene"
        assert not result["rejected_active"], "a live vehicle command was rejected"
        if args.scenario.endswith("merge") and args.duration_s >= 12:
            assert result["merged_ramp_count"], "no ramp vehicle passed the merge"


if __name__ == "__main__":
    main()
