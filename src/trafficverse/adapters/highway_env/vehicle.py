"""SDK-local vehicle extension: native IDM/MOBIL with temporary external intent."""

from __future__ import annotations

from typing import Any

# HighwayEnv has no typed package boundary. SDK types stay in this module.
from highway_env.vehicle.behavior import IDMVehicle  # type: ignore[import-untyped]


class ExternalIDMVehicle(IDMVehicle):  # type: ignore[misc]
    target_speed: float
    enable_lane_change: bool
    unsafe_control: bool = False
    respect_speed_limit: bool = False
    external_acceleration: float | None = None
    external_speed: float | None = None
    external_lane_until: float = 0.0
    external_lane_mode: int = 0
    pending_lane_index: tuple[str, str, int] | None = None
    physics_step_s: float | None = None
    default_target_speed: float | None = None

    def act(self, action: Any = None) -> None:
        direct_speed = (
            not self.crashed
            and self.unsafe_control
            and self.external_speed is not None
            and self.external_acceleration is None
        )
        if direct_speed:
            assert self.external_speed is not None
            # Shared scripts use TraCI speedMode=0 for immediate prescribed speeds.
            # A proportional controller adds a braking delay the script did not request.
            self.speed = min(float(self.external_speed), self.MAX_SPEED)
        self._try_pending_lane_change()
        if self.default_target_speed is None:
            self.default_target_speed = float(self.target_speed)
        self.target_speed = (
            self.external_speed if self.external_speed is not None else self.default_target_speed
        )
        if self.respect_speed_limit and self.external_speed is None:
            self.target_speed = min(self.target_speed, self.lane.speed_limit)
        native_lane_change = self.enable_lane_change
        if self.timer < self.external_lane_until:
            self.enable_lane_change = False
        super().act(action)
        self.enable_lane_change = native_lane_change
        if direct_speed:
            self.action["acceleration"] = 0.0
        if not self.crashed and self.external_acceleration is not None:
            self.action["acceleration"] = self.external_acceleration
        if not self.crashed and self.physics_step_s is not None:
            # HighwayEnv allows reverse motion; SUMO-origin traffic scripts do not.
            self.action["acceleration"] = max(
                self.action["acceleration"], -self.speed / self.physics_step_s
            )

    def _try_pending_lane_change(self) -> None:
        target = self.pending_lane_index
        if target is None:
            return
        if self.timer >= self.external_lane_until or target[:2] != self.lane_index[:2]:
            self.pending_lane_index = None
            return
        lane = self.road.network.get_lane(target)
        if not lane.is_reachable_from(self.position):
            return
        front, rear = self.road.neighbour_vehicles(self, target)
        for other in (front, rear):
            if other is None:
                continue
            gap = self.lane_distance_to(other, lane)
            closing_speed = self.speed - other.speed if gap >= 0 else other.speed - self.speed
            required = (
                (self.LENGTH + other.LENGTH) / 2
                + self.DISTANCE_WANTED
                + max(0, closing_speed) * self.TIME_WANTED
            )
            if abs(gap) < required:
                return
        self.target_lane_index = target
        self.pending_lane_index = None
