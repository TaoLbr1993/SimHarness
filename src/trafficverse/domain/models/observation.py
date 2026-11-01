"""Local vehicle observations derived from one authoritative traffic frame."""

from uuid import UUID

from pydantic import Field

from trafficverse.domain.models.common import StrictModel
from trafficverse.domain.models.simulation import DomainEvent
from trafficverse.domain.models.vehicle import VehicleState


class NearbyVehicle(StrictModel):
    vehicle: VehicleState
    distance_m: float = Field(ge=0, allow_inf_nan=False)


class VehicleObservation(StrictModel):
    ego: VehicleState
    radius_m: float = Field(gt=0, allow_inf_nan=False)
    nearby_vehicles: tuple[NearbyVehicle, ...] = ()


class LocalObservationFrame(StrictModel):
    experiment_id: UUID
    simulation_time_ms: int = Field(ge=0)
    sequence: int = Field(ge=0)
    radius_m: float = Field(gt=0, allow_inf_nan=False)
    observations: tuple[VehicleObservation, ...] = ()
    events: tuple[DomainEvent, ...] = ()
