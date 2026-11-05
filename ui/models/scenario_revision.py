"""Client-side contract for explicit edits to an unpublished generated scene."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DemandMode = Literal["count", "inflow", "density", "total_flow"]


class RequestedAutomationShare(BaseModel):
    """Requested percentages before rounding to integer departures."""

    model_config = ConfigDict(extra="forbid")
    level: Literal["L0", "L1", "L2", "L3", "L4", "L5"]
    share_percent: float = Field(strict=True, ge=0, le=100, allow_inf_nan=False)


class ScenarioRevisionRequest(BaseModel):
    """A partial edit; omitted fields retain their existing authored values."""

    model_config = ConfigDict(extra="forbid")
    vehicle_count: int | None = Field(default=None, strict=True, ge=1, le=20000)
    desired_density_veh_per_km_lane: float | None = Field(
        default=None, strict=True, gt=0, le=120, allow_inf_nan=False
    )
    duration_s: int | None = Field(default=None, strict=True, ge=60, le=3600)
    warmup_s: int | None = Field(default=None, strict=True, ge=0, le=3500)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2147483647)
    inflow_veh_per_hour_per_lane: float | None = Field(
        default=None, strict=True, gt=0, le=10000, allow_inf_nan=False
    )
    total_flow_veh_per_hour: float | None = Field(
        default=None, strict=True, gt=0, le=1200000, allow_inf_nan=False
    )
    reference_speed_kmh: float | None = Field(
        default=None, strict=True, gt=0, le=100, allow_inf_nan=False
    )
    automation_mix: list[RequestedAutomationShare] | None = Field(
        default=None, min_length=1, max_length=6
    )
    demand_mode: DemandMode | None = None

    @model_validator(mode="after")
    def validate_edit(self) -> ScenarioRevisionRequest:
        if not self.model_fields_set:
            raise ValueError("请至少修改一项场景设定")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("场景设定不能留空")
        drivers = (
            self.vehicle_count,
            self.inflow_veh_per_hour_per_lane,
            self.total_flow_veh_per_hour,
            self.desired_density_veh_per_km_lane,
        )
        if self.demand_mode is None and sum(value is not None for value in drivers) > 1:
            raise ValueError("数量、流量和密度同时修改时，请指定按哪一项编排车流")
        if self.demand_mode is not None:
            for mode, value in (
                ("count", self.vehicle_count),
                ("inflow", self.inflow_veh_per_hour_per_lane),
                ("total_flow", self.total_flow_veh_per_hour),
            ):
                if value is not None and mode != self.demand_mode:
                    raise ValueError("只填写当前编排方式对应的数量或流量，其他数值由系统重新计算")
        if (
            self.duration_s is not None
            and self.warmup_s is not None
            and self.warmup_s >= self.duration_s
        ):
            raise ValueError("预热时间必须小于仿真时长")
        if self.automation_mix is not None:
            if len({item.level for item in self.automation_mix}) != len(self.automation_mix):
                raise ValueError("L0–L5 等级不能重复")
            if not math.isclose(
                sum(item.share_percent for item in self.automation_mix),
                100,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError("L0–L5 车辆比例之和必须为 100%")
        return self
