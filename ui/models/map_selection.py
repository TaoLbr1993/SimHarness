"""Display-side contract for a geographic area explicitly selected by the user."""

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MapSelectionBounds(BaseModel):
    """WGS84 rectangle; longitude wrapping is intentionally not supported."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False)
    south: float = Field(gt=-85, lt=85, allow_inf_nan=False)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False)
    north: float = Field(gt=-85, lt=85, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> "MapSelectionBounds":
        if self.west >= self.east or self.south >= self.north or self.east - self.west >= 180:
            raise ValueError("Select a non-empty rectangle without crossing the date line.")
        return self

    @property
    def width_m(self) -> float:
        return self._distance(self.west, self.latitude, self.east, self.latitude)

    @property
    def height_m(self) -> float:
        return self._distance(self.longitude, self.south, self.longitude, self.north)

    @property
    def diagonal_m(self) -> float:
        return self._distance(self.west, self.south, self.east, self.north)

    @property
    def longitude(self) -> float:
        return (self.west + self.east) / 2

    @property
    def latitude(self) -> float:
        return (self.south + self.north) / 2

    @staticmethod
    def _distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        latitude1, latitude2 = math.radians(lat1), math.radians(lat2)
        a = math.sin((latitude2 - latitude1) / 2) ** 2 + (
            math.cos(latitude1) * math.cos(latitude2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
        )
        return 2 * 6371008.8 * math.asin(math.sqrt(min(1.0, a)))


class SelectionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_diagonal_m: float = Field(gt=0, allow_inf_nan=False)


class MapSearchPlace(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(gt=-85, lt=85, allow_inf_nan=False)
    city: str = ""
    district: str = ""
    country: str = ""

    @property
    def label(self) -> str:
        return " · ".join(dict.fromkeys(v for v in (self.name, self.city, self.country) if v))


class MapSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidates: list[MapSearchPlace] = Field(default_factory=list, max_length=50)
