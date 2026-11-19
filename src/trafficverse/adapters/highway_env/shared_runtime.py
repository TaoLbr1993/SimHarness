"""HighwayEnv road dynamics for shared SUMO-origin scenarios (no TraCI runtime)."""

from __future__ import annotations

import importlib
import math
from typing import Any

from trafficverse.config.models import HighwayEnvConfig
from trafficverse.domain.models.shared_scene import SceneVehicle

from .merge_guard import SharedMergeGuard

# Untyped third-party SDK boundary; none of these objects leave the adapter.
road_sdk: Any = importlib.import_module("highway_env.road.road")
lane_sdk: Any = importlib.import_module("highway_env.road.lane")
np: Any = importlib.import_module("numpy")


def _make_lane(points: Any, width_m: float, speed_mps: float) -> Any:
    coordinates = np.asarray([(x, -y) for x, y in points])
    direction = coordinates[-1] - coordinates[0]
    length = float(np.linalg.norm(direction))
    if length <= 0:
        raise ValueError("shared lane must have positive length")
    relative = coordinates - coordinates[0]
    longitudinal = relative @ (direction / length)
    lateral = relative @ np.asarray([-direction[1], direction[0]]) / length
    if np.all(np.abs(lateral) < 1e-6) and np.all(np.diff(longitudinal) >= 0):
        lane = lane_sdk.StraightLane(
            coordinates[0], coordinates[-1], width=width_m, speed_limit=speed_mps
        )
    else:
        lane = lane_sdk.PolyLaneFixedWidth(coordinates, width=width_m, speed_limit=speed_mps)
    lane.source_points = coordinates
    return lane


class SharedRoadNetwork(road_sdk.RoadNetwork):  # type: ignore[misc]
    """Honor source lane-to-lane connections when entering a connector or next edge."""

    def get_closest_lane_index(self, position: Any, heading: Any = None) -> Any:
        if not hasattr(self, "bounds"):
            entries = []
            for a, destinations in self.graph.items():
                for b, lanes in destinations.items():
                    for i, lane in enumerate(lanes):
                        points = lane.source_points
                        entries.append(
                            (
                                (a, b, i),
                                lane,
                                min(p[0] for p in points),
                                max(p[0] for p in points),
                                min(p[1] for p in points),
                                max(p[1] for p in points),
                            )
                        )
            self.bounds = entries
        candidates = sorted(
            (
                (
                    math.hypot(
                        max(x0 - position[0], 0, position[0] - x1),
                        max(y0 - position[1], 0, position[1] - y1),
                    ),
                    key,
                    lane,
                )
                for key, lane, x0, x1, y0, y1 in self.bounds
            ),
            key=lambda item: item[0],
        )
        best_distance, best = math.inf, candidates[0][1]
        for lower, key, lane in candidates:
            if lower > best_distance:
                break
            distance = lane.distance_with_heading(position, heading)
            if distance < best_distance:
                best_distance, best = distance, key
        return best

    def next_lane(self, current_index: Any, route: Any = None, **kwargs: Any) -> Any:
        if route and len(route) == 1 and route[0][:2] == current_index[:2]:
            # HighwayEnv otherwise picks an arbitrary outgoing edge after popping
            # the final route item. SUMO removes a vehicle at its route destination.
            return current_index
        if route and len(route) > 1 and route[0][:2] == current_index[:2]:
            nxt = route[1]
            candidates = self.transitions.get((current_index, nxt[:2]), [])
            if candidates:
                route[1] = (nxt[0], nxt[1], candidates[0])
        return super().next_lane(current_index, route=route, **kwargs)

    def next_lane_given_next_road(
        self, _from: Any, _to: Any, _id: Any, next_to: Any, next_id: Any, position: Any
    ) -> Any:
        if next_id is not None:
            lane = self.get_lane((_to, next_to, next_id))
            return next_id, lane.distance(position)
        return super().next_lane_given_next_road(_from, _to, _id, next_to, next_id, position)


class SharedHighwayRuntime:
    """Deterministic fixed-step demand, signals and stops around native road dynamics."""

    def __init__(self, config: HighwayEnvConfig) -> None:
        assert config.shared_scene is not None
        self.config = config
        self.scene = config.shared_scene
        self.time = 0.0
        self.ticks = 0
        self.vehicles: dict[str, Any] = {}
        self.specs = {v.vehicle_id: v for v in self.scene.vehicles}
        self.pending = list(self.scene.vehicles)
        self.stop_started: dict[tuple[str, int], float] = {}
        self.finished_stops: set[tuple[str, int]] = set()
        self.lane_ids: dict[Any, str] = {}
        self.lane_indices: dict[str, Any] = {}
        self.collision_ids: set[str] = set()
        self.signal_connections: list[Any] = []
        network = SharedRoadNetwork()
        network.transitions = {}
        network.connector_targets = {}
        self.road: Any = road_sdk.Road(
            network=network, np_random=np.random.default_rng(config.seed)
        )
        lanes = {lane_spec.lane_id: lane_spec for lane_spec in self.scene.lanes}
        edges = sorted(
            {
                lane_spec.edge_id
                for lane_spec in self.scene.lanes
                if not lane_spec.edge_id.startswith(":")
            }
        )
        for edge in edges:
            # HighwayEnv indexes left-to-right, SUMO indexes right-to-left.
            selected = sorted(
                (lane_spec for lane_spec in lanes.values() if lane_spec.edge_id == edge),
                key=lambda lane_spec: int(lane_spec.lane_id.rsplit("_", 1)[1]),
                reverse=True,
            )
            for number, lane in enumerate(selected):
                key = (edge + ":in", edge + ":out", number)
                network.add_lane(
                    key[0], key[1], _make_lane(lane.points, lane.width_m, lane.speed_mps)
                )
                self.lane_indices[lane.lane_id] = key
                self.lane_ids[key] = lane.lane_id
        for connection in self.scene.connections:
            source = self.lane_indices.get(connection.source_lane)
            target = self.lane_indices.get(connection.target_lane)
            if source is None or target is None:
                continue
            pair = (source[1], target[0])
            number = len(network.graph.get(pair[0], {}).get(pair[1], []))
            key = (*pair, number)
            points = [lanes[connection.source_lane].points[-1]]
            if connection.via_lane in lanes:
                points.extend(lanes[connection.via_lane].points)
            points.append(lanes[connection.target_lane].points[0])
            distinct = [points[0]]
            for point in points[1:]:
                if math.dist(point, distinct[-1]) > 0.01:
                    distinct.append(point)
            if len(distinct) < 2:
                x, y = distinct[0]
                distinct.append((x + 0.02, y))
            network.add_lane(
                *pair,
                _make_lane(
                    distinct,
                    lanes[connection.source_lane].width_m,
                    min(
                        lanes[connection.source_lane].speed_mps,
                        lanes[connection.target_lane].speed_mps,
                    ),
                ),
            )
            self.lane_ids[key] = connection.via_lane or f"connector:{number}:{pair}"
            network.transitions.setdefault((source, pair), []).append(number)
            network.transitions[(key, target[:2])] = [target[2]]
            network.connector_targets[key] = target
            if connection.signal_id is not None:
                self.signal_connections.append(
                    (source, pair, connection.signal_id, connection.signal_index)
                )
        self._merge_guard = SharedMergeGuard(network)
        self._spawn()

    @property
    def unwrapped(self) -> SharedHighwayRuntime:
        return self

    def signal_states(self) -> dict[str, tuple[str, float]]:
        result = {}
        for signal in self.scene.signals:
            cycle = sum(duration for duration, _ in signal.phases)
            if cycle <= 0:
                raise ValueError("signal cycle must be positive")
            phase_time = (self.scene.begin_time_s + self.time + signal.offset_s) % cycle
            for duration, state in signal.phases:
                if phase_time < duration:
                    result[signal.signal_id] = (state, duration - phase_time)
                    break
                phase_time -= duration
        return result

    def _spawn(self) -> None:
        vehicle_class = importlib.import_module(
            "trafficverse.adapters.highway_env.vehicle"
        ).ExternalIDMVehicle
        remaining = []
        for spec in self.pending:
            if spec.depart_s > self.scene.begin_time_s + self.time + 1e-9:
                remaining.append(spec)
                continue
            edge = spec.edges[0]
            pair = (edge + ":in", edge + ":out")
            options = list(range(len(self.road.network.graph[pair[0]][pair[1]])))
            if spec.depart_lane.isdigit():
                options = [self.lane_indices[f"{edge}_{spec.depart_lane}"][2]]
            chosen = None
            for index in options:
                key = (*pair, index)
                lane = self.road.network.get_lane(key)
                if self._insertion_clear(spec, key, lane):
                    chosen = key
                    break
            if chosen is None:
                remaining.append(spec)
                continue
            lane = self.road.network.get_lane(chosen)
            speed = (
                min(spec.max_speed_mps, lane.speed_limit)
                if spec.depart_speed == "max"
                else float(spec.depart_speed)
            )
            position = max(0.0, spec.depart_position_m - spec.length_m / 2)
            vehicle = vehicle_class(
                self.road,
                lane.position(position, 0),
                heading=lane.heading_at(position),
                speed=speed,
                target_speed=min(spec.max_speed_mps, lane.speed_limit),
            )
            vehicle.LENGTH, vehicle.WIDTH = spec.length_m, spec.width_m
            vehicle.MAX_SPEED = spec.max_speed_mps
            vehicle.respect_speed_limit = True
            vehicle.physics_step_s = 1 / self.config.simulation_frequency
            vehicle.COMFORT_ACC_MAX = spec.acceleration_mps2
            vehicle.COMFORT_ACC_MIN = -spec.deceleration_mps2
            vehicle.ACC_MAX = max(8.0, spec.acceleration_mps2, spec.deceleration_mps2)
            vehicle.DISTANCE_WANTED = spec.min_gap_m + spec.length_m
            vehicle.TIME_WANTED = spec.headway_s
            vehicle.route = self._route(spec)
            vehicle.route[0] = chosen
            vehicle.enable_lane_change = False  # Route and explicit scene controls choose lanes.
            self.road.vehicles.append(vehicle)
            self.vehicles[spec.vehicle_id] = vehicle
        self.pending = remaining

    def _insertion_clear(self, spec: SceneVehicle, key: Any, lane: Any) -> bool:
        position = max(0.0, spec.depart_position_m - spec.length_m / 2)
        speed = (
            min(spec.max_speed_mps, lane.speed_limit)
            if spec.depart_speed == "max"
            else float(spec.depart_speed)
        )
        for vehicle in self.road.vehicles:
            if vehicle.lane_index != key:
                continue
            gap = lane.local_coordinates(vehicle.position)[0] - position
            closing = max(0, speed - vehicle.speed if gap >= 0 else vehicle.speed - speed)
            deceleration = spec.deceleration_mps2 if gap >= 0 else abs(vehicle.COMFORT_ACC_MIN)
            required = (
                (spec.length_m + vehicle.LENGTH) / 2
                + spec.min_gap_m
                + closing / self.config.simulation_frequency
                + closing * closing / (2 * deceleration)
            )
            if abs(gap) < required:
                return False
        return True

    @staticmethod
    def _route(spec: SceneVehicle) -> list[Any]:
        route: list[Any] = []
        for index, edge in enumerate(spec.edges):
            route.append((edge + ":in", edge + ":out", None))
            if index + 1 < len(spec.edges):
                route.append((edge + ":out", spec.edges[index + 1] + ":in", None))
        return route

    def _align_routes(self) -> None:
        for vehicle in self.vehicles.values():
            route = vehicle.route or []
            if len(route) < 2 or route[0][:2] != vehicle.lane_index[:2]:
                continue
            pair = route[1][:2]
            if (vehicle.lane_index, pair) in self.road.network.transitions:
                continue
            a, b, current = vehicle.lane_index
            available = [
                i
                for i in range(len(self.road.network.graph[a][b]))
                if ((a, b, i), pair) in self.road.network.transitions
            ]
            if available:
                target = min(available, key=lambda i: abs(i - current))
                vehicle.target_lane_index = (a, b, target)

    def _limit_at(self, vehicle: Any, position_m: float, dt: float) -> None:
        lane = self.road.network.get_lane(vehicle.lane_index)
        current = lane.local_coordinates(vehicle.position)[0]
        gap = max(0, position_m - current - vehicle.LENGTH / 2)
        # HighwayEnv integrates position with the *current* speed (Euler), so
        # bounding acceleration alone would still overshoot a red light or stop.
        vehicle.speed = min(vehicle.speed, gap / dt)
        safe_speed = min(gap / dt, math.sqrt(2 * abs(vehicle.COMFORT_ACC_MIN) * gap))
        vehicle.action["acceleration"] = min(
            vehicle.action["acceleration"], (safe_speed - vehicle.speed) / dt
        )

    def step(self, action: object = None) -> tuple[None, float, bool, bool, dict[str, object]]:
        del action
        dt = 1 / self.config.simulation_frequency
        for _ in range(self.config.simulation_frequency // self.config.policy_frequency):
            self._spawn()
            states = self.signal_states()
            self._align_routes()
            self.road.act()
            for identifier, vehicle in tuple(self.vehicles.items()):
                lane_id = self.lane_ids.get(vehicle.lane_index)
                spec = self.specs[identifier]
                for source, pair, signal, index in self.signal_connections:
                    if source != vehicle.lane_index or signal not in states:
                        continue
                    route = vehicle.route or []
                    if len(route) > 1 and route[1][:2] != pair:
                        continue
                    state = states[signal][0]
                    if index < len(state) and state[index] in "rRyY":
                        self._limit_at(vehicle, self.road.network.get_lane(source).length, dt)
                for index, stop in enumerate(spec.stops):
                    key = (identifier, index)
                    if key in self.finished_stops or stop.lane_id != lane_id:
                        continue
                    lane = self.road.network.get_lane(vehicle.lane_index)
                    gap = (
                        stop.position_m
                        - lane.local_coordinates(vehicle.position)[0]
                        - vehicle.LENGTH / 2
                    )
                    self._limit_at(vehicle, stop.position_m, dt)
                    if gap < 0.5 and vehicle.speed < 0.2:
                        started = self.stop_started.setdefault(key, self.time)
                        until = (stop.until_s or 0) - self.scene.begin_time_s
                        if self.time >= max(started + stop.duration_s, until):
                            self.finished_stops.add(key)
                if vehicle.crashed:
                    vehicle.speed = 0.0
                    vehicle.action = {"steering": 0.0, "acceleration": 0.0}
            self._merge_guard.apply(self.road.vehicles, self._limit_at, dt)
            self.road.step(dt)
            self.ticks += 1
            self.time = self.ticks / self.config.simulation_frequency
            for identifier, vehicle in tuple(self.vehicles.items()):
                if vehicle.crashed:
                    self.collision_ids.add(identifier)
                lane = self.road.network.get_lane(vehicle.lane_index)
                if (
                    len(vehicle.route or []) <= 1
                    and vehicle.lane_index[:2]
                    == (
                        self.specs[identifier].edges[-1] + ":in",
                        self.specs[identifier].edges[-1] + ":out",
                    )
                    and lane.local_coordinates(vehicle.position)[0] >= lane.length
                ):
                    self.road.vehicles.remove(vehicle)
                    del self.vehicles[identifier]
        return None, 0.0, False, self.time * 1000 >= self.config.duration_ms, {}

    def close(self) -> None:
        self.road.vehicles.clear()
        self.vehicles.clear()
