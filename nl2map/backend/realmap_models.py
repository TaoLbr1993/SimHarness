"""Typed intent, provider evidence and local-import settings for the NL2MAP demo."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from scenario_models import ScenarioIntent

DetailLevel = Literal["main_roads", "detailed"]
MAIN_ROAD_TYPES = tuple(
    f"highway.{road}{suffix}"
    for road in ("motorway", "trunk", "primary", "secondary", "tertiary")
    for suffix in ("", "_link")
)


class MapIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_mode: Literal["synthetic", "real_map"]
    place_query: str = Field(default="", max_length=240)
    alternate_query: str = Field(default="", max_length=240)
    location_quote: str = ""
    radius_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    radius_quote: str = ""
    detail_level: DetailLevel = "main_roads"
    detail_quote: str = ""
    extra_requirements: list[str] = Field(default_factory=list, max_length=32)
    reason: str = Field(min_length=1)
    scenario: ScenarioIntent | None = None
    map_description: str = ""

    def validate_sources(self, description: str) -> None:
        if self.scenario is not None:
            self.scenario.validate_sources(description)
        if self.source_mode == "real_map":
            if not self.place_query.strip() or not self.location_quote.strip():
                raise ValueError("真实地图请求缺少地点，请说明城市、地点或校区")
            quotes = [self.location_quote, *self.extra_requirements]
            if self.radius_m is not None:
                quotes.append(self.radius_quote)
            if self.detail_level == "detailed":
                quotes.append(self.detail_quote)
            invalid = [q for q in quotes if not q.strip() or q not in description]
            if invalid:
                raise ValueError(f"地点、范围和额外要求必须连续引用原文；无效引用：{invalid!r}")


class PlaceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(gt=-85, lt=85, allow_inf_nan=False)
    city: str = ""
    district: str = ""
    country: str = ""
    street: str = ""
    osm_key: str = ""
    osm_value: str = ""
    provider: Literal["photon", "amap"] = "photon"
    source_crs: Literal["WGS84", "GCJ-02"] = "WGS84"
    source_longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    source_latitude: float | None = Field(default=None, ge=-85, le=85, allow_inf_nan=False)
    formatted_address: str = ""
    match_level: str = ""
    coordinate_note: str = ""


class SourceRecord(BaseModel):
    url: str
    query: str
    fetched_at: str
    cache_hit: bool


@dataclass
class GeocodingResult:
    candidates: list[PlaceCandidate]
    records: list[SourceRecord]
    warnings: list[str] = field(default_factory=list)


class PlaceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_id: str | None
    needs_clarification: bool
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_choice(self) -> "PlaceChoice":
        if self.needs_clarification == (self.selected_id is not None):
            raise ValueError("歧义时不可选择地点，确定地点时必须提供候选 ID")
        return self


class RealMapSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geocoder_provider: Literal["auto", "amap", "photon"] = "auto"
    amap_city: str = Field(default="", max_length=80)
    photon_url: str = "https://photon.komoot.io/api/"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    user_agent: str = "SimHarness-NL2MAP/0.3 (local research demo)"
    default_radius_m: float = Field(default=1000, ge=100, le=3000)
    max_radius_m: float = Field(default=3000, ge=100, le=3000)
    request_timeout_sec: int = Field(default=75, ge=10, le=180)
    cache_ttl_sec: int = Field(default=86400, ge=0)
    max_response_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    tool_timeout_sec: int = Field(default=90, ge=10, le=300)

    @model_validator(mode="after")
    def valid_defaults(self) -> "RealMapSettings":
        if self.default_radius_m > self.max_radius_m:
            raise ValueError("真实地图默认半径不能超过最大半径")
        return self
