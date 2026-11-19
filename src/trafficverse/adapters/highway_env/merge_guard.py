"""Reserve shared destination lanes while safe vehicles traverse merging connectors."""

import math
from collections.abc import Callable, Sequence
from typing import Any


class SharedMergeGuard:
    """Bridge SUMO junction yielding missing from HighwayEnv's plain Road dynamics.

    SDK objects stay inside this adapter. Unsafe script commands bypass yielding;
    safe vehicles reserve one merge passage until their rear and following gap clear.
    """

    def __init__(self, network: Any) -> None:
        self._network = network
        incoming: dict[Any, set[str]] = {}
        for connector, target in network.connector_targets.items():
            incoming.setdefault(target, set()).add(connector[0])
        self._targets = {target for target, sources in incoming.items() if len(sources) > 1}
        self._owners: dict[Any, Any] = {}

    def apply(
        self, vehicles: Sequence[Any], limit_at: Callable[[Any, float, float], None], dt: float
    ) -> None:
        groups: dict[Any, list[tuple[Any, float, float]]] = {}
        for vehicle in vehicles:
            approach = self._approach(vehicle)
            if approach is None or vehicle.unsafe_control:
                continue
            target, distance, stop_position = approach
            braking_distance = vehicle.speed**2 / (2 * abs(vehicle.COMFORT_ACC_MIN))
            lookahead = braking_distance + vehicle.speed * vehicle.TIME_WANTED + vehicle.LENGTH
            lane = self._network.get_lane(vehicle.lane_index)
            to_stop_line = lane.length - lane.local_coordinates(vehicle.position)[0]
            # Keep yielding while stopped at the entrance, even as the dynamic
            # braking horizon shrinks below the length of the connector itself.
            if to_stop_line <= lookahead:
                groups.setdefault(target, []).append((vehicle, stop_position, distance))
        for target, candidates in groups.items():
            owner = self._owners.get(target)
            lane = self._network.get_lane(target)
            if owner is not None:
                cleared = (
                    owner.lane_index == target
                    and lane.local_coordinates(owner.position)[0]
                    > owner.LENGTH / 2 + owner.DISTANCE_WANTED + owner.speed * owner.TIME_WANTED
                )
                still_approaching = any(vehicle is owner for vehicle, _, _ in candidates)
                on_connector = self._network.connector_targets.get(owner.lane_index) == target
                left_approach = (
                    not still_approaching and not on_connector and owner.lane_index != target
                )
                if owner not in vehicles or cleared or left_approach:
                    owner = None
            if owner is None:
                # An already occupied exit has priority over new approach requests.
                occupied = [
                    v
                    for v in vehicles
                    if v.lane_index == target
                    and lane.local_coordinates(v.position)[0]
                    < v.LENGTH / 2 + v.DISTANCE_WANTED + v.speed * v.TIME_WANTED
                ]
                owner = (
                    occupied[0]
                    if occupied
                    else min(candidates, key=lambda item: item[2] / max(item[0].speed, 0.1))[0]
                )
                self._owners[target] = owner
            for vehicle, stop_position, _ in candidates:
                if vehicle is not owner:
                    limit_at(vehicle, stop_position, dt)
        self._protect_following(vehicles, dt)

    @staticmethod
    def _protect_following(vehicles: Sequence[Any], dt: float) -> None:
        for vehicle in vehicles:
            if vehicle.unsafe_control or vehicle.crashed:
                continue
            for index in {vehicle.lane_index, vehicle.target_lane_index}:
                front, _ = vehicle.road.neighbour_vehicles(vehicle, index)
                if front is None:
                    continue
                lane = vehicle.road.network.get_lane(index)
                gap = vehicle.lane_distance_to(front, lane) - (vehicle.LENGTH + front.LENGTH) / 2
                # Bound this Euler displacement even if a leader is stopped by a
                # signal or merge reservation after IDM has already acted.
                clearance = max(0, gap - max(0, vehicle.DISTANCE_WANTED - vehicle.LENGTH))
                safe_speed = min(
                    clearance / dt,
                    math.sqrt(front.speed**2 + 2 * abs(vehicle.COMFORT_ACC_MIN) * clearance),
                )
                vehicle.speed = min(vehicle.speed, safe_speed)
                vehicle.action["acceleration"] = min(
                    vehicle.action["acceleration"], (safe_speed - vehicle.speed) / dt
                )

    def _approach(self, vehicle: Any) -> tuple[Any, float, float] | None:
        current = vehicle.lane_index
        lane = self._network.get_lane(current)
        remaining = lane.length - lane.local_coordinates(vehicle.position)[0]
        target = self._network.connector_targets.get(current)
        if target in self._targets:
            return target, remaining, lane.length
        route = vehicle.route or []
        for next_lane in route[:2]:
            pair = next_lane[:2]
            choices = self._network.transitions.get((current, pair), [])
            for index in choices:
                connector = (*pair, index)
                target = self._network.connector_targets.get(connector)
                if target in self._targets:
                    distance = remaining + self._network.get_lane(connector).length
                    return target, distance, lane.length
        return None
