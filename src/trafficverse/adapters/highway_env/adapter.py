"""Normalize one seeded HighwayEnv episode into the traffic engine contract."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from trafficverse.config.models import HighwayEnvConfig, TrafficEngineConfig
from trafficverse.domain.enums import (
    AutomationLevel,
    ComponentStatus,
    ErrorCode,
    LaneChangeDirection,
    TrafficLightColor,
    VehicleAction,
)
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import (
    ComponentHealth,
    ControlCommand,
    TrafficLightState,
    TrafficSnapshot,
    Vector3,
    VehicleState,
)


class HighwayTrafficEngineAdapter:
    """Own one environment; advance only when SimulationManager requests a step."""

    def __init__(self, experiment_id: UUID) -> None:
        self._experiment_id = experiment_id
        # Third-party objects are confined to this SDK boundary, never returned to callers.
        self._env: Any = None
        self._vehicles: dict[str, Any] = {}
        self._config: HighwayEnvConfig | None = None
        self._sequence = 0
        self._time_ms = 0
        self._ended = False
        self._failed = False
        self._rejected: tuple[str, ...] = ()
        self._collisions: set[str] = set()

    def load(self, config: TrafficEngineConfig) -> None:
        if not isinstance(config, HighwayEnvConfig):
            raise ValueError("HighwayEnv adapter requires HighwayEnv configuration")
        if self._env is not None:
            return
        try:
            if config.shared_scene is not None:
                runtime = importlib.import_module(
                    "trafficverse.adapters.highway_env.shared_runtime"
                ).SharedHighwayRuntime
                self._env = runtime(config)
                self._vehicles = self._env.vehicles
                self._config = config
                self._sequence = self._time_ms = 0
                self._ended = self._failed = False
                self._collisions.clear()
                return
            gym = importlib.import_module("gymnasium")
            importlib.import_module("highway_env")
            vehicle_class = importlib.import_module(
                "trafficverse.adapters.highway_env.vehicle"
            ).ExternalIDMVehicle
            self._env = gym.make(
                config.environment_id,
                config={
                    "lanes_count": config.lanes_count,
                    "vehicles_count": config.vehicles_count,
                    "controlled_vehicles": config.controlled_vehicles,
                    "initial_lane_id": config.initial_lane_id,
                    "duration": config.duration_ms / 1000,
                    "policy_frequency": config.policy_frequency,
                    "simulation_frequency": config.simulation_frequency,
                    "other_vehicles_type": (
                        "trafficverse.adapters.highway_env.vehicle.ExternalIDMVehicle"
                    ),
                },
                disable_env_checker=True,
            )
            self._env.reset(seed=config.seed)
            env = self._env.unwrapped
            # Replace only the native ego vehicles at reset, through the SDK's supported
            # create_from constructor. Background vehicles already use our IDM subclass.
            for index, old in enumerate(tuple(env.controlled_vehicles)):
                new = vehicle_class.create_from(old)
                env.road.vehicles[env.road.vehicles.index(old)] = new
                env.controlled_vehicles[index] = new
            controlled = {id(v): f"ego_{i}" for i, v in enumerate(env.controlled_vehicles)}
            self._vehicles = {
                controlled.get(id(v), f"vehicle_{i:04d}"): v
                for i, v in enumerate(env.road.vehicles)
            }
            self._config = config
            self._sequence = self._time_ms = 0
            self._ended = self._failed = False
            self._collisions.clear()
        except ImportError as error:
            self.close()
            raise TrafficVerseError(
                ErrorCode.COMPONENT_UNAVAILABLE,
                "HighwayEnv is unavailable; install with uv sync --extra highway",
            ) from error
        except Exception as error:
            self.close()
            raise TrafficVerseError(
                ErrorCode.COMPONENT_UNAVAILABLE, "HighwayEnv initialization failed"
            ) from error

    def _require_loaded(self) -> HighwayEnvConfig:
        if self._env is None or self._config is None or self._failed:
            raise TrafficVerseError(ErrorCode.COMPONENT_UNAVAILABLE, "HighwayEnv is not ready")
        return self._config

    def release_speed_controls(self, vehicle_ids: Sequence[str]) -> None:
        self._require_loaded()
        for identifier in vehicle_ids:
            if identifier in self._vehicles:
                self._vehicles[identifier].external_speed = None

    def apply_controls(self, commands: Mapping[str, ControlCommand]) -> None:
        self._require_loaded()
        rejected = []
        for identifier, command in sorted(commands.items()):
            vehicle = self._vehicles.get(identifier)
            if vehicle is None or not self._valid_command(vehicle, command):
                rejected.append(identifier)
                continue
            if self._config is not None and self._config.shared_scene is not None:
                vehicle.unsafe_control = command.safety_checks_override
                if command.lane_change_mode is not None:
                    vehicle.enable_lane_change = False
                    vehicle.external_lane_mode = command.lane_change_mode
            if command.stop_requested:
                vehicle.external_speed = 0.0
            elif command.desired_speed_mps is not None:
                vehicle.external_speed = command.desired_speed_mps
            vehicle.external_acceleration = command.desired_acceleration_mps2
            if command.lane_change is not LaneChangeDirection.NONE:
                direction = -1 if command.lane_change is LaneChangeDirection.LEFT else 1
                a, b, lane = vehicle.lane_index
                vehicle.external_lane_until = vehicle.timer + command.lane_change_duration_s
                if vehicle.external_lane_mode == 512:
                    # An occupied target lane delays the lateral request, not braking.
                    vehicle.pending_lane_index = (a, b, lane + direction)
                else:
                    vehicle.pending_lane_index = None
                    vehicle.target_lane_index = (a, b, lane + direction)
        self._rejected = tuple(rejected)

    def _valid_command(self, vehicle: Any, command: ControlCommand) -> bool:
        if (
            vehicle.crashed
            or (
                (command.safety_checks_override or command.lane_change_mode is not None)
                and (self._config is None or self._config.shared_scene is None)
            )
            or (
                command.takeover_requested
                and (self._config is None or self._config.shared_scene is None)
            )
        ):
            return False
        if command.lane_change_mode not in (None, 0, 512):
            return False
        numbers = (command.desired_speed_mps, command.desired_acceleration_mps2)
        if any(value is not None and not math.isfinite(value) for value in numbers):
            return False
        acceleration = command.desired_acceleration_mps2
        if acceleration is not None and abs(acceleration) > vehicle.ACC_MAX:
            return False
        if command.lane_change is not LaneChangeDirection.NONE:
            a, b, lane = vehicle.lane_index
            target = lane + (-1 if command.lane_change is LaneChangeDirection.LEFT else 1)
            lanes = self._env.unwrapped.road.network.graph[a][b]
            if target < 0 or target >= len(lanes):
                return False
            if not lanes[target].is_reachable_from(vehicle.position):
                return False
        return True

    def apply_step_controls(self, commands: Mapping[str, ControlCommand]) -> tuple[str, ...]:
        self.apply_controls(commands)
        return self._rejected

    def step(self, target_time_ms: int) -> TrafficSnapshot:
        config = self._require_loaded()
        if self._ended or target_time_ms != self._time_ms + config.step_ms:
            raise TrafficVerseError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "HighwayEnv requires one policy step and cannot step an ended episode",
            )
        try:
            # None deliberately delegates act() to each native IDM vehicle. External
            # intents are applied by the vehicle subclass on every physics substep.
            _, _, terminated, truncated, _ = self._env.step(None)
            actual = round(float(self._env.unwrapped.time) * 1000)
            if actual != target_time_ms:
                raise ValueError("HighwayEnv clock disagrees with requested time")
            self._time_ms = actual
            self._sequence += 1
            self._ended = bool(terminated or truncated or actual >= config.duration_ms)
            vehicles = tuple(self._vehicle_state(k, v) for k, v in self._vehicles.items())
            self._collisions.update(k for k, v in self._vehicles.items() if v.crashed)
            lights: tuple[TrafficLightState, ...] = ()
            if config.shared_scene is not None:
                self._collisions.update(self._env.collision_ids)
                lights = tuple(
                    TrafficLightState(
                        signal_id=f"sumo-tls:{signal}:{index}",
                        simulation_time_ms=actual,
                        phase=TrafficLightColor.from_sumo_link_state(phase).value,
                        remaining_ms=round(remaining * 1000),
                    )
                    for signal, (state, remaining) in self._env.signal_states().items()
                    for index, phase in enumerate(state)
                )
            for vehicle in self._vehicles.values():
                vehicle.external_acceleration = None
            return TrafficSnapshot(
                experiment_id=self._experiment_id,
                simulation_time_ms=actual,
                sequence=self._sequence,
                vehicles=vehicles,
                collision_vehicle_ids=tuple(sorted(self._collisions)),
                episode_ended=self._ended,
                traffic_lights=lights,
            )
        except Exception as error:
            self._failed = True
            raise TrafficVerseError(
                ErrorCode.COMPONENT_UNAVAILABLE,
                "HighwayEnv step failed; the episode cannot continue",
            ) from error

    def _vehicle_state(self, identifier: str, vehicle: Any) -> VehicleState:
        acceleration = float(vehicle.action.get("acceleration", 0))
        action = VehicleAction.KEEP_LANE
        if vehicle.target_lane_index[2] < vehicle.lane_index[2]:
            action = VehicleAction.LANE_CHANGE_LEFT
        elif vehicle.target_lane_index[2] > vehicle.lane_index[2]:
            action = VehicleAction.LANE_CHANGE_RIGHT
        elif acceleration > 0.1:
            action = VehicleAction.ACCELERATE
        elif acceleration < -0.1:
            action = VehicleAction.BRAKE
        external = vehicle.external_speed is not None or vehicle.external_acceleration is not None
        level = AutomationLevel.HUMAN
        route_id = None
        if self._config is not None and self._config.shared_scene is not None:
            match = re.search(r"(?:^|_)L([0-5])(?:[._]|$)", identifier)
            if match:
                level = AutomationLevel(f"L{match.group(1)}")
            route_id = self._env.specs[identifier].route_id
        return VehicleState(
            experiment_id=self._experiment_id,
            vehicle_id=identifier,
            simulation_time_ms=self._time_ms,
            sequence=self._sequence,
            automation_level=level,
            route_id=route_id,
            position=Vector3(x=float(vehicle.position[0]), y=-float(vehicle.position[1]), z=0),
            speed_mps=max(0.0, float(vehicle.speed)),
            acceleration_mps2=acceleration,
            heading_rad=-float(vehicle.heading),
            lane_id=self._lane_id(vehicle.lane_index),
            target_lane_id=self._lane_id(vehicle.target_lane_index),
            controller_id="external" if external else "highway-idm",
            action=action,
            risk_score=1.0 if vehicle.crashed else 0.0,
        )

    def _lane_id(self, index: Any) -> str:
        if self._config is not None and self._config.shared_scene is not None:
            return str(self._env.lane_ids[index])
        return f"highway:{index[0]}:{index[1]}:{index[2]}"

    def network_geojson(self) -> dict[str, object]:
        self._require_loaded()
        features = []
        for start, destinations in self._env.unwrapped.road.network.graph.items():
            for end, lanes in destinations.items():
                for index, lane in enumerate(lanes):
                    count = max(2, min(2000, math.ceil(lane.length / 5) + 1))
                    points = [lane.position(lane.length * i / (count - 1), 0) for i in range(count)]
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "lane_id": self._lane_id((start, end, index)),
                                "width_m": float(lane.width_at(0)),
                                "speed_limit_mps": float(lane.speed_limit or 30),
                            },
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[float(p[0]), -float(p[1]), 0] for p in points],
                            },
                        }
                    )
        return {"type": "FeatureCollection", "features": features}

    def health(self) -> ComponentHealth:
        return ComponentHealth(
            component="highway",
            status=(
                ComponentStatus.HEALTHY
                if self._env is not None and not self._failed
                else ComponentStatus.UNAVAILABLE
            ),
        )

    def close(self) -> None:
        env, self._env = self._env, None
        self._config = None
        self._vehicles.clear()
        if env is not None:
            env.close()
