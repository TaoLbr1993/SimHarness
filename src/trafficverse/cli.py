"""TrafficVerse command-line entry point."""

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from uuid import UUID

from trafficverse.adapters.carla import CarlaAdapter, CarlaDiagnostics
from trafficverse.adapters.sumo import SumoTrafficEngineAdapter
from trafficverse.config.compatibility import (
    default_baseline_path,
    inspect_runtime,
    select_runtime_profile,
)
from trafficverse.config.loader import (
    configuration_hash,
    load_map_manifest,
    load_runtime_baseline,
    load_scenario,
    validate_map_manifest,
    validate_scenario_environment,
)
from trafficverse.config.models import ScenarioConfig
from trafficverse.domain.enums import (
    ErrorCode,
    RequirementMode,
    TrafficLightColor,
)
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import (
    ActorSpawnResult,
    TrafficLightUpdate,
    Vector3,
)
from trafficverse.maps import (
    NETWORK_SCHEMA_VERSION,
    OpenDriveMapCompiler,
    load_network,
    validate_compiled_bundle,
)

SOFTWARE_WEBGL_FLAGS = (
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",
    "--disable-gpu-compositing",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trafficverse")
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="inspect local runtime compatibility")
    doctor.add_argument(
        "--profile",
        default=os.getenv("TRAFFICVERSE_RUNTIME_PROFILE", "auto"),
        help="runtime profile name or auto",
    )
    doctor.add_argument(
        "--baseline",
        type=Path,
        default=default_baseline_path(_repository_root()),
    )

    scenario = subcommands.add_parser("scenario", help="scenario operations")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    validate = scenario_commands.add_parser("validate", help="validate a scenario YAML")
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--environment",
        action="store_true",
        help="also require referenced native map assets",
    )

    map_parser = subcommands.add_parser("map", help="map asset operations")
    map_commands = map_parser.add_subparsers(dest="map_command", required=True)
    map_validate = map_commands.add_parser("validate", help="validate a map manifest")
    map_validate.add_argument("path", type=Path)
    map_validate.add_argument(
        "--profile",
        default=os.getenv("TRAFFICVERSE_RUNTIME_PROFILE", "auto"),
        help="runtime profile whose component versions must match",
    )
    map_validate.add_argument(
        "--baseline",
        type=Path,
        default=default_baseline_path(_repository_root()),
    )
    map_compile = map_commands.add_parser("compile", help="compile OpenDRIVE native map assets")
    map_compile.add_argument("source", type=Path)
    map_compile.add_argument("output", type=Path)
    map_compile.add_argument("--map-id", default="town04-carla-0.9.16-native-1.0")
    map_compile.add_argument("--carla-map", default="Town04")
    map_compile.add_argument("--carla-version", default="0.9.16")

    traffic = subcommands.add_parser("traffic", help="SUMO traffic adapter operations")
    traffic_commands = traffic.add_subparsers(dest="traffic_command", required=True)
    smoke = traffic_commands.add_parser("smoke", help="run the external SUMO smoke test")
    smoke.add_argument(
        "--scenario",
        type=Path,
        default=_repository_root() / "configs" / "scenarios" / "core-run-town04.yaml",
    )
    smoke.add_argument("--ticks", type=int, default=2400)

    carla = subcommands.add_parser("carla", help="local CARLA operations")
    carla_commands = carla.add_subparsers(dest="carla_command", required=True)
    carla_doctor = carla_commands.add_parser(
        "doctor", help="verify the local CARLA version handshake"
    )
    carla_doctor.add_argument(
        "--scenario",
        type=Path,
        default=_repository_root() / "configs" / "scenarios" / "core-run-town04.yaml",
    )
    carla_smoke = carla_commands.add_parser(
        "smoke", help="spawn, move, signal, and clean up local actors"
    )
    carla_smoke.add_argument(
        "--scenario",
        type=Path,
        default=_repository_root() / "configs" / "scenarios" / "core-run-town04.yaml",
    )
    carla_smoke.add_argument("--vehicles", type=int, default=10)
    carla_smoke.add_argument("--ticks", type=int, default=240)

    for command, help_text in (
        ("ui", "open the mixed-automation desktop UI"),
        ("traffic-ui", "open the general-traffic desktop UI"),
    ):
        ui = subcommands.add_parser(command, help=help_text)
        ui.add_argument(
            "--api-url", default=os.getenv("TRAFFICVERSE_API_URL", "http://127.0.0.1:8000")
        )
        ui.add_argument(
            "--scenario-id", type=UUID, default=UUID("00000000-0000-0000-0000-000000000042")
        )
        ui.add_argument(
            "--allow-software-webgl",
            action="store_true",
            help="allow Chromium WebGL on a blocklisted software renderer such as llvmpipe",
        )
    serve = subcommands.add_parser("serve", help="serve the Core Run REST/WebSocket API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--scenario",
        type=Path,
        default=_repository_root() / "configs/scenarios/core-run-town04.yaml",
    )
    serve.add_argument(
        "--carla-mode",
        choices=[mode.value for mode in RequirementMode],
        default=None,
        help="override the scenario CARLA mode; use disabled for a local 2D demo",
    )
    serve.add_argument(
        "--artifact-root",
        type=Path,
        default=_repository_root() / "artifacts/maps",
    )
    headless = subcommands.add_parser("run", help="run an agent simulation without a desktop")
    headless.add_argument("--config", type=Path, required=True, help="headless YAML configuration")
    headless.add_argument("--scenario", default=None, help="override the configured SUMO map ID")
    return parser


def _fail(message: str, *, exit_code: int = 2) -> NoReturn:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def _run_doctor(args: argparse.Namespace) -> int:
    baseline = load_runtime_baseline(args.baseline)
    report = inspect_runtime(baseline, requested_profile=args.profile)
    print(report.model_dump_json(indent=2))
    return 0 if report.ready else 1


def _run_scenario_validate(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.path)
    if args.environment:
        validate_scenario_environment(scenario, repository_root=_repository_root())
    result = {
        "ok": True,
        "scenario": scenario.scenario.name,
        "map_id": scenario.scenario.map_id,
        "configuration_hash": configuration_hash(scenario),
        "environment_checked": bool(args.environment),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_map_validate(args: argparse.Namespace) -> int:
    baseline = load_runtime_baseline(args.baseline)
    profile_name, profile = select_runtime_profile(
        baseline,
        requested_profile=args.profile,
    )
    if profile.carla.version is None:
        raise ValueError("map validation requires a runtime profile with a CARLA version")
    manifest = validate_map_manifest(
        args.path,
        expected_carla_version=profile.carla.version,
        expected_network_schema_version=NETWORK_SCHEMA_VERSION,
        expected_sumo_version=profile.sumo.version,
    )
    network = validate_compiled_bundle(args.path.parent)
    result = {
        "ok": True,
        "profile": profile_name,
        "map_id": manifest.map_id,
        "validated_files": len(manifest.files),
        "lanes": len(network.lanes),
        "links": len(network.links),
        "signals": len(network.signals),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_map_compile(args: argparse.Namespace) -> int:
    result = OpenDriveMapCompiler().compile(
        args.source,
        args.output,
        map_id=args.map_id,
        carla_map=args.carla_map,
        carla_version=args.carla_version,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "network_schema_version": NETWORK_SCHEMA_VERSION,
                "network": str(result.network_path),
                "geojson": str(result.geojson_path),
                "manifest": str(result.manifest_path),
                "lanes": result.lane_count,
                "links": result.link_count,
                "signals": result.signal_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _resolved_sumo_scenario(scenario_path: Path) -> ScenarioConfig:
    scenario = load_scenario(scenario_path)
    config_file = Path(scenario.sumo.config_file)
    if not config_file.is_absolute():
        config_file = _repository_root() / config_file
    return scenario.model_copy(
        update={"sumo": scenario.sumo.model_copy(update={"config_file": str(config_file)})}
    )


def _run_traffic_smoke(args: argparse.Namespace) -> int:
    if args.ticks <= 0:
        raise ValueError("--ticks must be greater than zero")
    scenario = _resolved_sumo_scenario(args.scenario)
    engine = SumoTrafficEngineAdapter(UUID(int=scenario.scenario.seed))
    seen_vehicle_ids: set[str] = set()
    seen_signal_ids: set[str] = set()
    maximum_active_vehicles = 0
    try:
        engine.load(scenario.sumo)
        for sequence in range(1, args.ticks + 1):
            snapshot = engine.step(sequence * scenario.simulation.step_ms)
            seen_vehicle_ids.update(vehicle.vehicle_id for vehicle in snapshot.vehicles)
            seen_signal_ids.update(signal.signal_id for signal in snapshot.traffic_lights)
            maximum_active_vehicles = max(maximum_active_vehicles, len(snapshot.vehicles))
    finally:
        engine.close()

    diagnostics = engine.diagnostics()
    result = {
        "ok": True,
        "ticks": diagnostics.sequence,
        "simulation_time_ms": diagnostics.simulation_time_ms,
        "seen_vehicles": len(seen_vehicle_ids),
        "traffic_lights": len(seen_signal_ids),
        "maximum_active_vehicles": maximum_active_vehicles,
        "departed_vehicle_ids": diagnostics.departed_vehicle_ids,
        "arrived_vehicle_ids": diagnostics.arrived_vehicle_ids,
        "closed": engine.health().status.value == "UNAVAILABLE",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


@dataclass(frozen=True, slots=True)
class _SmokeVehicle:
    vehicle_id: str
    blueprint_id: str
    position: Vector3
    heading_rad: float


@dataclass(frozen=True, slots=True)
class _SmokeTransform:
    actor_id: int
    position: Vector3
    heading_rad: float


def _run_carla_doctor(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    adapter = CarlaAdapter()
    try:
        adapter.connect(scenario.carla)
        diagnostics = adapter.diagnostics()
    finally:
        adapter.close()
    print(
        json.dumps(
            {
                "ok": True,
                "endpoint": f"{scenario.carla.host}:{scenario.carla.port}",
                "endpoint_mode": scenario.carla.endpoint_mode,
                "client_version": diagnostics.client_version,
                "server_version": diagnostics.server_version,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _configure_software_webgl() -> None:
    current_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for flag in SOFTWARE_WEBGL_FLAGS:
        if flag not in current_flags:
            current_flags.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(current_flags)


def _run_ui(args: argparse.Namespace) -> int:
    if args.allow_software_webgl:
        _configure_software_webgl()
    try:
        from ui.app.main import run
    except ModuleNotFoundError as error:
        if error.name == "PySide6" or (
            error.name is not None and error.name.startswith("PySide6.")
        ):
            _fail("UI dependencies are unavailable; run 'uv sync --extra ui'")
        raise
    return run(args.api_url, args.scenario_id, traffic_mode=args.command == "traffic-ui")


def _run_serve(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(
            "Core Run server only supports loopback binding; use a secured deployment "
            "gateway for remote UI access"
        )
    if not 1 <= args.port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    import uvicorn

    from trafficverse.bootstrap import build_core_api

    mode = RequirementMode(args.carla_mode) if args.carla_mode is not None else None
    app = build_core_api(
        args.scenario,
        repository_root=_repository_root(),
        carla_mode=mode,
        artifact_root=args.artifact_root,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _run_carla_smoke(args: argparse.Namespace) -> int:
    if args.vehicles < 10:
        raise ValueError("--vehicles must be at least 10")
    if args.ticks <= 0:
        raise ValueError("--ticks must be greater than zero")
    scenario = load_scenario(args.scenario)
    network_path = Path(scenario.traffic.network)
    if not network_path.is_absolute():
        network_path = _repository_root() / network_path
    network = load_network(network_path)
    manifest_path = Path(scenario.map_registration.manifest)
    if not manifest_path.is_absolute():
        manifest_path = _repository_root() / manifest_path
    manifest = load_map_manifest(manifest_path)
    candidates = [lane for lane in network.lanes if len(lane.centerline) >= 2]
    if len(candidates) < args.vehicles:
        raise ValueError("compiled map does not contain enough spawn lanes")
    specs: list[_SmokeVehicle] = []
    for index, lane in enumerate(candidates[: args.vehicles]):
        start, following = lane.centerline[0], lane.centerline[1]
        specs.append(
            _SmokeVehicle(
                vehicle_id=f"carla-smoke-{index:03d}",
                blueprint_id=scenario.carla.fallback_blueprints[
                    index % len(scenario.carla.fallback_blueprints)
                ],
                position=Vector3(x=start.x, y=start.y, z=start.z + 0.5),
                heading_rad=math.atan2(following.y - start.y, following.x - start.x),
            )
        )

    adapter = CarlaAdapter()
    baseline_actor_count = 0
    spawn_results: tuple[ActorSpawnResult, ...] = ()
    diagnostics: CarlaDiagnostics
    frozen_lights = False
    signal_colors: list[str] = []
    try:
        adapter.connect(scenario.carla)
        adapter.load_world(manifest.carla_map, scenario.weather)
        baseline_actor_count = adapter.actor_count()
        spawn_results = adapter.spawn_vehicles(specs)
        if any(not result.success for result in spawn_results):
            failures = [result.error for result in spawn_results if not result.success]
            raise TrafficVerseError(
                ErrorCode.CARLA_OPERATION_FAILED,
                f"CARLA smoke spawn failures: {failures}",
            )
        actor_ids = [result.actor_id for result in spawn_results if result.actor_id is not None]
        lights = adapter.traffic_lights()
        frozen_lights = bool(lights) and all(light.frozen for light in lights)
        expected_signal_ids = {signal.opendrive_id for signal in network.signals}
        mapped_light = next(
            (light for light in lights if light.opendrive_id in expected_signal_ids),
            None,
        )
        if mapped_light is None:
            raise TrafficVerseError(
                ErrorCode.CARLA_OPERATION_FAILED,
                "Town04 contains no runtime traffic light referenced by network bindings",
            )
        if mapped_light:
            for color in (
                TrafficLightColor.RED,
                TrafficLightColor.YELLOW,
                TrafficLightColor.GREEN,
            ):
                adapter.update_traffic_lights(
                    (TrafficLightUpdate(carla_actor_id=mapped_light.actor_id, color=color),)
                )
                signal_colors.append(color.value)
                adapter.tick((len(signal_colors)) * scenario.simulation.step_ms)
        time_offset = len(signal_colors)
        for sequence in range(1, args.ticks + 1):
            adapter.update_actors(
                tuple(
                    _SmokeTransform(
                        actor_id=actor_id,
                        position=Vector3(
                            x=spec.position.x + sequence * 0.05,
                            y=spec.position.y,
                            z=spec.position.z,
                        ),
                        heading_rad=spec.heading_rad,
                    )
                    for actor_id, spec in zip(actor_ids, specs, strict=True)
                )
            )
            adapter.tick((time_offset + sequence) * scenario.simulation.step_ms)
        diagnostics = adapter.diagnostics()
        adapter.destroy_actors(actor_ids)
    finally:
        adapter.close()

    probe = CarlaAdapter()
    try:
        probe.connect(scenario.carla)
        restored_actor_count = probe.actor_count()
    finally:
        probe.close()
    result = {
        "ok": frozen_lights and restored_actor_count == baseline_actor_count,
        "endpoint": f"{scenario.carla.host}:{scenario.carla.port}",
        "spawned_vehicles": sum(result.success for result in spawn_results),
        "ticks": args.ticks,
        "latest_carla_frame": diagnostics.last_carla_frame,
        "traffic_lights_frozen": frozen_lights,
        "mapped_signal_id": mapped_light.opendrive_id,
        "signal_colors_tested": signal_colors,
        "baseline_actor_count": baseline_actor_count,
        "restored_actor_count": restored_actor_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            from trafficverse.agent_harness.headless import run_headless

            return run_headless(args.config, _repository_root(), args.scenario)
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "scenario" and args.scenario_command == "validate":
            return _run_scenario_validate(args)
        if args.command == "map" and args.map_command == "validate":
            return _run_map_validate(args)
        if args.command == "map" and args.map_command == "compile":
            return _run_map_compile(args)
        if args.command == "traffic" and args.traffic_command == "smoke":
            return _run_traffic_smoke(args)
        if args.command == "carla" and args.carla_command == "doctor":
            return _run_carla_doctor(args)
        if args.command == "carla" and args.carla_command == "smoke":
            return _run_carla_smoke(args)
        if args.command in {"ui", "traffic-ui"}:
            return _run_ui(args)
        if args.command == "serve":
            return _run_serve(args)
    except (TrafficVerseError, ValueError) as error:
        _fail(str(error))
    parser.error("unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
