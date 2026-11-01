"""Derive range-based local observations from an immutable simulation frame."""

from math import hypot

from trafficverse.domain.enums import AutomationLevel
from trafficverse.domain.models.observation import (
    LocalObservationFrame,
    NearbyVehicle,
    VehicleObservation,
)
from trafficverse.domain.models.simulation import SimulationFrame


def local_observations(
    frame: SimulationFrame,
    *,
    radius_m: float,
    vehicle_ids: tuple[str, ...] = (),
    automation_levels: tuple[AutomationLevel, ...] = (),
    all_vehicles: bool = False,
) -> LocalObservationFrame:
    """Select egos by ID/level, with neighbors inside an inclusive XY radius in meters."""
    ids, levels = set(vehicle_ids), set(automation_levels)
    vehicles = frame.traffic.vehicles
    observations = []
    for ego in vehicles:
        if not (all_vehicles or ego.vehicle_id in ids or ego.automation_level in levels):
            continue
        neighbors = []
        for other in vehicles:
            if other.vehicle_id == ego.vehicle_id:
                continue
            distance = hypot(other.position.x - ego.position.x, other.position.y - ego.position.y)
            if distance <= radius_m:
                neighbors.append(NearbyVehicle(vehicle=other, distance_m=distance))
        neighbors.sort(key=lambda item: (item.distance_m, item.vehicle.vehicle_id))
        observations.append(
            VehicleObservation(ego=ego, radius_m=radius_m, nearby_vehicles=tuple(neighbors))
        )
    return LocalObservationFrame(
        experiment_id=frame.traffic.experiment_id,
        simulation_time_ms=frame.traffic.simulation_time_ms,
        sequence=frame.traffic.sequence,
        radius_m=radius_m,
        observations=tuple(observations),
        events=frame.events,
    )
