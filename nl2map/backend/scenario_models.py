"""Scenario intent, declared defaults and measured results for the local SUMO demo."""

import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

TrafficState = Literal["free_flow", "moderate", "congested"]
Metric = Literal["speed", "density", "queue", "travel_time"]
SimulationMode = Literal["traffic", "autonomy"]
DemandMode = Literal["count", "inflow", "density", "total_flow"]
AutomationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]
_LEVEL_MENTION = re.compile(r"(?<![A-Za-z0-9_])L[0-5](?![0-9])", re.IGNORECASE)
_LEVEL_PERCENTAGE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<level>L[0-5])(?![0-9])\s*"
    r"(?:的\s*)?(?:车辆?\s*)?(?:占比|比例|占)?\s*(?:为|是|:|=)?\s*"
    r"(?P<percentage>[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+))\s*%",
    re.IGNORECASE,
)


def _explicit_automation_percentages(text: str) -> dict[str, float]:
    """Recognize only explicit level-number-percent declarations; never infer missing shares."""
    result: dict[str, float] = {}
    for match in _LEVEL_PERCENTAGE.finditer(unicodedata.normalize("NFKC", text)):
        level, value = match["level"].upper(), float(match["percentage"])
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"原文 {level} 比例须为 0–100% 之间的有限数值")
        if level in result and not math.isclose(result[level], value, rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"原文 {level} 出现不一致的百分比，请明确每个等级的最终比例")
        result[level] = value
    return result


def _validate_unparsed_level_requirements(description: str, pending: list[str]) -> None:
    """Every level mention outside a supported declaration must remain visible for review."""
    normalized = unicodedata.normalize("NFKC", description)
    pending_texts = [unicodedata.normalize("NFKC", text) for text in pending]
    for clause in re.findall(r"[^,，;；。！？!?\n]+", normalized):
        declarations = [match.span() for match in _LEVEL_PERCENTAGE.finditer(clause)]
        unmatched = any(
            not any(
                start <= mention.start() and mention.end() <= end for start, end in declarations
            )
            for mention in _LEVEL_MENTION.finditer(clause)
        )
        if unmatched and not any(clause.strip() in text for text in pending_texts):
            raise ValueError(
                f"等级要求尚无法核验且未保留为待验证：{clause.strip()}；"
                "请使用完整数字百分比（如 L0占70%、L3占30%），或完整保留该原文要求供用户确认"
            )


class AutomationMixShare(BaseModel):
    """Requested percentages, separate from the integer vehicle allocation."""

    model_config = ConfigDict(extra="forbid")
    level: AutomationLevel
    share_percent: float = Field(ge=0, le=100, allow_inf_nan=False)


class GeneratedAutomationShare(AutomationMixShare):
    """Actual authored departure count and its share of the total schedule."""

    vehicle_count: int = Field(ge=0, le=20000)


def validate_automation_mix(mix: Sequence[AutomationMixShare]) -> None:
    if len({item.level for item in mix}) != len(mix):
        raise ValueError("L0–L5 配比中的等级不能重复")
    if mix and not math.isclose(
        sum(item.share_percent for item in mix), 100, rel_tol=0, abs_tol=1e-6
    ):
        raise ValueError("L0–L5 车辆比例之和必须为 100%")


def _default_metrics() -> list[Metric]:
    return ["speed", "density"]


class ScenarioIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1)
    traffic_state: TrafficState = "moderate"
    traffic_state_quote: str = ""
    duration_s: int | None = Field(default=None, ge=60, le=3600)
    duration_quote: str = ""
    warmup_s: int | None = Field(default=None, ge=0, le=3500)
    warmup_quote: str = ""
    density_veh_per_km_lane: float | None = Field(default=None, gt=0, le=120, allow_inf_nan=False)
    density_quote: str = ""
    inflow_veh_per_hour_per_lane: float | None = Field(
        default=None, gt=0, le=10000, allow_inf_nan=False
    )
    inflow_quote: str = ""
    vehicle_count: int | None = Field(default=None, ge=1, le=20000)
    vehicle_count_quote: str = ""
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    seed_quote: str = ""
    automation_mix: list[AutomationMixShare] = Field(default_factory=list, max_length=6)
    automation_mix_quote: str = ""
    metrics: list[Metric] = Field(default_factory=_default_metrics, min_length=1, max_length=4)
    use_existing_map: bool = False
    unverified_requirements: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def consistent_demand(self) -> "ScenarioIntent":
        if self.vehicle_count is not None and self.inflow_veh_per_hour_per_lane is not None:
            raise ValueError("当前场景请选择总发车数或每车道流量之一，不能同时指定")
        validate_automation_mix(self.automation_mix)
        return self

    def validate_sources(self, description: str) -> None:
        quotes = [self.objective, *self.unverified_requirements]
        if self.traffic_state != "moderate" or self.traffic_state_quote:
            quotes.append(self.traffic_state_quote)
        if self.automation_mix or self.automation_mix_quote:
            quotes.append(self.automation_mix_quote)
        for field, quote in (
            (self.duration_s, self.duration_quote),
            (self.warmup_s, self.warmup_quote),
            (self.density_veh_per_km_lane, self.density_quote),
            (self.inflow_veh_per_hour_per_lane, self.inflow_quote),
            (self.vehicle_count, self.vehicle_count_quote),
            (self.seed, self.seed_quote),
        ):
            if field is not None:
                quotes.append(quote)
        invalid = [q for q in quotes if not q.strip() or q not in description]
        if invalid:
            raise ValueError(f"场景参数必须连续引用需求原文：{invalid!r}")
        self._validate_automation_sources(description)

    def _validate_automation_sources(self, description: str) -> None:
        explicit = _explicit_automation_percentages(description)
        if not self.automation_mix:
            _validate_unparsed_level_requirements(description, self.unverified_requirements)
            if not explicit:
                return
            if math.isclose(sum(explicit.values()), 100, rel_tol=0, abs_tol=1e-6):
                raise ValueError("原文已给出完整 L0–L5 百分比，但模型未提取配比，请重试")
            pending: dict[str, float] = {}
            for requirement in self.unverified_requirements:
                pending.update(_explicit_automation_percentages(requirement))
            if any(pending.get(level) != value for level, value in explicit.items()):
                raise ValueError("原文 L0–L5 百分比不完整，须补齐至 100% 或明确保留为待验证要求")
            return

        quoted = _explicit_automation_percentages(self.automation_mix_quote)
        if not quoted:
            raise ValueError(
                "无法从配比引用核验数值，请使用完整数字百分比，如 L0占70%、L3占30%；"
                "各半、其余等表述须保留为待验证要求，不能猜测配比"
            )
        if quoted != explicit:
            raise ValueError("配比引用未包含原文中的全部明确百分比，请连续引用完整 L0–L5 配比")
        proposed = {item.level: item.share_percent for item in self.automation_mix}
        errors = []
        for level, value in quoted.items():
            if level not in proposed:
                errors.append(f"模型遗漏原文的 {level} {value:g}%")
            elif not math.isclose(proposed[level], value, rel_tol=0, abs_tol=1e-6):
                errors.append(f"{level} 原文为 {value:g}%，模型却为 {proposed[level]:g}%")
        for level in proposed.keys() - quoted.keys():
            errors.append(f"原文没有可核验的 {level} 数字百分比")
        if errors:
            raise ValueError("L0–L5 配比与原文不一致：" + "；".join(errors))
        _validate_unparsed_level_requirements(description, self.unverified_requirements)


class ScenarioSettings(BaseModel):
    default_duration_s: int = Field(default=600, ge=60, le=3600)
    default_seed: int = Field(default=42, ge=0)
    max_vehicles: int = Field(default=20000, ge=1, le=20000)
    max_origins: int = Field(default=12, ge=1, le=24)
    destinations_per_origin: int = Field(default=3, ge=1, le=8)
    reference_speed_kmh: float = Field(default=30, gt=0, le=100)
    default_demand_scale: float = Field(default=0.1, gt=0, le=1, allow_inf_nan=False)
    free_flow_density: float = Field(default=8, gt=0, le=120)
    moderate_density: float = Field(default=22, gt=0, le=120)
    congested_density: float = Field(default=65, gt=0, le=120)
    run_timeout_s: int = Field(default=120, ge=10, le=600)
    step_length_s: float = Field(default=0.5, gt=0, le=1)
    congestion_speed_ratio: float = Field(default=0.5, gt=0, le=1)
    congestion_halting_fraction: float = Field(default=0.2, gt=0, le=1)
    congestion_time_fraction: float = Field(default=0.3, gt=0, le=1)
    default_map_description: str = (
        "生成一个四向十字路口，每个方向一条进口车道和一条出口车道，四个方向都必须有信号灯。"
        "每条道路从路口向外延伸约300米。"
    )


class ScenarioPlan(BaseModel):
    objective: str
    traffic_state: TrafficState
    duration_s: int
    warmup_s: int
    seed: int
    desired_density_veh_per_km_lane: float
    reference_speed_kmh: float
    inflow_veh_per_hour_per_lane: float | None
    requested_vehicle_count: int | None
    metrics: list[str]
    defaults_used: list[str]
    simulation_mode: SimulationMode = "traffic"
    automation_mix: list[AutomationMixShare] = Field(default_factory=list, max_length=6)
    automation_note: str | None = None
    density_note: str = "目标密度用于建议发车需求；实际密度由仿真测得，不能保证等于目标。"
    congestion_speed_ratio: float
    congestion_halting_fraction: float
    congestion_time_fraction: float
    demand_mode: DemandMode | None = None
    requested_total_flow_veh_per_hour: float | None = None


def resolve_plan(
    intent: ScenarioIntent,
    settings: ScenarioSettings,
    *,
    simulation_mode: SimulationMode = "traffic",
) -> ScenarioPlan:
    duration = intent.duration_s if intent.duration_s is not None else settings.default_duration_s
    warmup = intent.warmup_s if intent.warmup_s is not None else min(120, duration // 5)
    if warmup >= duration:
        raise ValueError("预热时间必须小于仿真时长")
    presets = {
        "free_flow": settings.free_flow_density,
        "moderate": settings.moderate_density,
        "congested": settings.congested_density,
    }
    density = intent.density_veh_per_km_lane or presets[intent.traffic_state]
    flow = intent.inflow_veh_per_hour_per_lane or density * settings.reference_speed_kmh
    automatic_demand = (
        intent.vehicle_count is None
        and intent.inflow_veh_per_hour_per_lane is None
        and intent.density_veh_per_km_lane is None
    )
    default_minimum_applied = False
    if automatic_demand:
        flow *= settings.default_demand_scale
        minimum_flow = math.ceil(3600 / duration)
        if flow < minimum_flow:
            flow = minimum_flow
            default_minimum_applied = True
    defaults = []
    for value, label in (
        (intent.duration_s, "仿真时长"),
        (intent.warmup_s, "预热时间"),
        (intent.seed, "随机种子"),
        (intent.density_veh_per_km_lane, "目标密度参考值"),
    ):
        if value is None:
            defaults.append(label)
    if intent.vehicle_count is None and intent.inflow_veh_per_hour_per_lane is None:
        defaults.append("发车流量由目标密度 × 参考速度推算")
        if automatic_demand and settings.default_demand_scale != 1:
            defaults.append(f"默认轻量车流：推算流量 × {settings.default_demand_scale:.0%}")
        if default_minimum_applied:
            defaults.append("默认轻量车流：短时场景每入口车道至少安排 1 辆")
    mix = intent.automation_mix
    if simulation_mode == "autonomy" and not mix:
        mix = [AutomationMixShare(level="L0", share_percent=100)]
        defaults.append("未指定自动驾驶等级配比，默认 L0 100%")
    automation_note = None
    if mix:
        automation_note = (
            "L0–L5 沿用系统 SUMO 跟驰行为参数，不代表已接入或验证真实自动驾驶算法；"
            "预览比例按实际整数发车数计算，取整可能使其与请求比例略有差异。"
        )
        if simulation_mode == "traffic":
            automation_note += "本次普通交通场景仍保留需求中明确指定的等级配比。"
    return ScenarioPlan(
        objective=intent.objective,
        traffic_state=intent.traffic_state,
        duration_s=duration,
        warmup_s=warmup,
        seed=intent.seed if intent.seed is not None else settings.default_seed,
        desired_density_veh_per_km_lane=density,
        reference_speed_kmh=settings.reference_speed_kmh,
        inflow_veh_per_hour_per_lane=None if intent.vehicle_count is not None else flow,
        requested_vehicle_count=intent.vehicle_count,
        metrics=intent.metrics,
        defaults_used=defaults,
        simulation_mode=simulation_mode,
        automation_mix=mix,
        automation_note=automation_note,
        congestion_speed_ratio=settings.congestion_speed_ratio,
        congestion_halting_fraction=settings.congestion_halting_fraction,
        congestion_time_fraction=settings.congestion_time_fraction,
        demand_mode="count" if intent.vehicle_count is not None else "inflow",
    )


class RouteDemand(BaseModel):
    planned_vehicles: int
    route_count: int
    origin_edges: list[str]
    origin_lane_count: int
    external_passenger_lane_km: float
    routed_edges: int
    eligible_edges: int
    requested_inflow_veh_per_hour: float
    realized_schedule_veh_per_hour: float
    source_policy: str
    automation_mix: list[GeneratedAutomationShare] = Field(default_factory=list, max_length=6)


class ScenarioSample(BaseModel):
    time_s: float
    running: int
    waiting_to_enter: int
    halting: int
    speed_kmh: float | None
    speed_ratio: float | None


class ScenarioMetrics(BaseModel):
    mean_speed_kmh: float | None
    mean_external_density_veh_per_km_lane: float | None
    mean_halting_vehicles: float | None
    max_halting_vehicles: int
    inserted_vehicles: int
    arrived_vehicles: int
    unfinished_vehicles: int
    not_inserted_vehicles: int
    pending_at_end: int
    collision_events: int
    teleport_events: int
    observation_duration_s: float
    density_observation_duration_s: float
    congestion_time_fraction: float | None
    congestion_observed: bool | None
    target_state: Literal["observed", "not_observed", "not_requested", "no_data", "invalid_run"]
    reason: str
    mean_completed_travel_time_s: float | None
    samples: list[ScenarioSample]


class RevisionAutomationShare(AutomationMixShare):
    """Reject coercion in manually entered percentages at the request boundary."""

    share_percent: float = Field(strict=True, ge=0, le=100, allow_inf_nan=False)


class ScenarioRevision(BaseModel):
    """A partial human-authored patch, separate from the original quoted language intent."""

    model_config = ConfigDict(extra="forbid", strict=True)
    vehicle_count: int | None = Field(default=None, ge=1, le=20000)
    desired_density_veh_per_km_lane: float | None = Field(
        default=None, gt=0, le=120, allow_inf_nan=False
    )
    duration_s: int | None = Field(default=None, ge=60, le=3600)
    warmup_s: int | None = Field(default=None, ge=0, le=3500)
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    inflow_veh_per_hour_per_lane: float | None = Field(
        default=None, gt=0, le=10000, allow_inf_nan=False
    )
    total_flow_veh_per_hour: float | None = Field(
        default=None, gt=0, le=1200000, allow_inf_nan=False
    )
    reference_speed_kmh: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    automation_mix: list[RevisionAutomationShare] | None = Field(
        default=None, min_length=1, max_length=6
    )
    demand_mode: DemandMode | None = None

    @model_validator(mode="before")
    @classmethod
    def require_patch(cls, value: object) -> object:
        if isinstance(value, dict) and (not value or any(v is None for v in value.values())):
            raise ValueError("至少修改一项场景设定；未修改的字段请省略，不能填写 null")
        return value

    @model_validator(mode="after")
    def consistent_patch(self) -> "ScenarioRevision":
        drivers = (
            self.vehicle_count,
            self.inflow_veh_per_hour_per_lane,
            self.total_flow_veh_per_hour,
            self.desired_density_veh_per_km_lane,
        )
        if self.demand_mode is None and sum(v is not None for v in drivers) > 1:
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
            validate_automation_mix(self.automation_mix)
        return self

    @model_serializer(mode="wrap")
    def serialize_patch(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # Omitted fields remain omitted when nested in saved scenario metadata.
        return {key: value for key, value in handler(self).items() if value is not None}


# Keep the original Python import and count-only HTTP payload compatible.
ScenarioCountRevision = ScenarioRevision


class ScenarioResult(BaseModel):
    status: Literal[
        "success",
        "generated",
        "needs_adjustment",
        "needs_clarification",
        "map_error",
        "scenario_error",
    ]
    output_kind: Literal["scenario"] = "scenario"
    description: str
    task_id: str
    catalog_map_id: str | None = None
    parent_task_id: str | None = Field(default=None, pattern=r"^\d{8}_\d{6}_[0-9a-f]{6}$")
    manual_overrides: ScenarioRevision | None = None
    intent: ScenarioIntent | None = None
    map_task_id: str | None = None
    map_defaulted: bool = False
    plan: ScenarioPlan | None = None
    demand: RouteDemand | None = None
    metrics: ScenarioMetrics | None = None
    simulation_exit_code: int | None = None
    user_review: Literal["pending"] = "pending"
    unverified_requirements: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    artifact_urls: dict[str, str] = Field(default_factory=dict)


class ScenarioDraftSettings(BaseModel):
    """Settings published to TrafficVerse; none of these are observations."""

    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^\d{8}_\d{6}_[0-9a-f]{6}$")
    description: str = Field(min_length=1, max_length=8000)
    traffic_state: TrafficState
    desired_density_veh_per_km_lane: float = Field(gt=0, le=120, allow_inf_nan=False)
    planned_vehicles: int = Field(ge=1, le=20000)
    duration_s: int = Field(ge=60, le=3600)
    warmup_s: int = Field(ge=0, le=3500)
    seed: int = Field(ge=0, le=2147483647)
    inflow_veh_per_hour_per_lane: float | None = Field(gt=0, le=10000, allow_inf_nan=False)
    unverified_requirements: list[str] = Field(default_factory=list, max_length=32)
    simulation_mode: SimulationMode = "traffic"
    automation_mix: list[GeneratedAutomationShare] = Field(default_factory=list, max_length=6)


class ScenarioDraft(BaseModel):
    """A saved runnable definition, independent of any execution history."""

    map_id: str
    name: str
    settings: ScenarioDraftSettings
    preview_url: str | None = None
    configuration_url: str
