"""Explicit WGS84 selection bounds, independent of geocoding and language inference."""

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SelectedBounds(BaseModel):
    """A non-wrapping rectangle. Geographic coordinates use longitude, latitude order."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    south: float = Field(gt=-85, lt=85, allow_inf_nan=False, strict=True)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    north: float = Field(gt=-85, lt=85, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def ordered_bounds(self) -> "SelectedBounds":
        if self.west >= self.east or self.south >= self.north or self.east - self.west >= 180:
            raise ValueError("Select a nonempty rectangle that does not cross the date line.")
        return self

    @property
    def diagonal_m(self) -> float:
        """Return the great-circle distance between opposite corners in metres."""
        south, north = math.radians(self.south), math.radians(self.north)
        delta = math.radians(self.east - self.west)
        haversine = (
            math.sin((north - south) / 2) ** 2
            + math.cos(south) * math.cos(north) * math.sin(delta / 2) ** 2
        )
        return 2 * 6371008.8 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))

    def validate_extent(self, maximum_m: float) -> None:
        """Reject oversized input rather than silently clamping its extent."""
        if self.diagonal_m > maximum_m:
            raise ValueError(
                f"Selected area is too large. Reduce its diagonal to at most {maximum_m:g} m."
            )

    def geo_boundary(self) -> str:
        """Return netconvert's geographic rectangular boundary syntax."""
        return f"{self.west:.7f},{self.south:.7f},{self.east:.7f},{self.north:.7f}"
