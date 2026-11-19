"""Engine-neutral road, demand and fixed-signal inputs converted from a scenario."""

import math
from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from trafficverse.domain.models.common import StrictModel


class SceneLane(StrictModel):
    lane_id: str
    edge_id: str
    points: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(min_length=2)
    width_m: FiniteFloat = Field(default=3.2, gt=0)
    speed_mps: FiniteFloat = 30


class SceneConnection(StrictModel):
    source_lane: str
    target_lane: str
    via_lane: str | None = None
    signal_id: str | None = None
    signal_index: int = Field(default=0, ge=0)


class SceneSignal(StrictModel):
    signal_id: str
    offset_s: FiniteFloat = 0
    phases: tuple[tuple[FiniteFloat, str], ...]


class SceneStop(StrictModel):
    lane_id: str
    position_m: FiniteFloat
    duration_s: FiniteFloat = 0
    until_s: FiniteFloat | None = None


class SceneVehicle(StrictModel):
    vehicle_id: str
    route_id: str
    edges: tuple[str, ...] = Field(min_length=1)
    depart_s: FiniteFloat
    depart_lane: str = "best"
    depart_position_m: FiniteFloat = 0
    depart_speed: str = "0"
    type_id: str = "DEFAULT_VEHTYPE"
    length_m: FiniteFloat = Field(default=5, gt=0)
    width_m: FiniteFloat = Field(default=1.8, gt=0)
    max_speed_mps: FiniteFloat = 30
    acceleration_mps2: FiniteFloat = Field(default=3, gt=0)
    deceleration_mps2: FiniteFloat = Field(default=4.5, gt=0)
    min_gap_m: FiniteFloat = 2.5
    headway_s: FiniteFloat = Field(default=1.5, gt=0)
    stops: tuple[SceneStop, ...] = ()


class SharedScene(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    seed: int = Field(default=42, ge=0)
    map_id: str
    begin_time_s: FiniteFloat = 0
    lanes: tuple[SceneLane, ...]
    connections: tuple[SceneConnection, ...]
    signals: tuple[SceneSignal, ...] = ()
    vehicles: tuple[SceneVehicle, ...] = Field(max_length=100000)

    @model_validator(mode="after")
    def validate_references(self) -> "SharedScene":
        lanes = {lane.lane_id for lane in self.lanes}
        edges = {lane.edge_id for lane in self.lanes}
        signals = {signal.signal_id: signal for signal in self.signals}
        connections = set()
        by_lane = {lane.lane_id: lane.edge_id for lane in self.lanes}
        if len(lanes) != len(self.lanes) or len({v.vehicle_id for v in self.vehicles}) != len(
            self.vehicles
        ):
            raise ValueError("shared scene requires unique lane and vehicle IDs")
        for signal in self.signals:
            if not signal.phases or any(duration <= 0 for duration, _ in signal.phases):
                raise ValueError("signal phases require positive durations")
        for connection in self.connections:
            if connection.source_lane not in lanes or connection.target_lane not in lanes:
                raise ValueError("connection references an unknown lane")
            connections.add((by_lane[connection.source_lane], by_lane[connection.target_lane]))
            if connection.signal_id is not None:
                linked_signal = signals.get(connection.signal_id)
                if linked_signal is None or any(
                    connection.signal_index >= len(state) for _, state in linked_signal.phases
                ):
                    raise ValueError("connection references an unknown signal link")
        for vehicle in self.vehicles:
            if any(edge not in edges for edge in vehicle.edges):
                raise ValueError("vehicle route references an unknown edge")
            if any(
                pair not in connections
                for pair in zip(vehicle.edges, vehicle.edges[1:], strict=False)
            ):
                raise ValueError("vehicle route has disconnected edges")
            if (
                vehicle.depart_lane not in {"best", "free", "first", "allowed"}
                and not vehicle.depart_lane.isdigit()
            ):
                raise ValueError("unsupported departure lane")
            if vehicle.depart_speed != "max" and (
                not math.isfinite(float(vehicle.depart_speed)) or float(vehicle.depart_speed) < 0
            ):
                raise ValueError("departure speed must be nonnegative")
            if any(stop.lane_id not in lanes for stop in vehicle.stops):
                raise ValueError("stop references an unknown lane")
        return self
