"""Typed acceptance checks and evidence for the V1 roadgen workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Direction = Literal["north", "east", "south", "west"]
Property = Literal["signals", "lanes", "connections", "topology", "geometry", "other"]
Count = Annotated[int, Field(ge=0, le=64, strict=True)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcedRule(StrictModel):
    source_quote: str = Field(min_length=1)


class JunctionCountRule(SourcedRule):
    kind: Literal["junction_count"]
    count: Count


class ScopedRule(SourcedRule):
    scope: Literal["single", "all"] = "single"


class ApproachCountRule(ScopedRule):
    kind: Literal["approach_count"]
    count: Count


class DirectionalRule(ScopedRule):
    directions: list[Direction] = Field(default_factory=list)


class SignalRule(DirectionalRule):
    kind: Literal["signals"]
    policy: Literal["present", "none", "all_approaches"]

    @model_validator(mode="after")
    def check_network_policies(self) -> "SignalRule":
        if self.policy != "all_approaches" and self.directions:
            raise ValueError("present/none are network-wide; use all_approaches for specific sides")
        return self


class LaneCountRule(DirectionalRule):
    kind: Literal["lane_count"]
    incoming: Count | None = None
    outgoing: Count | None = None

    @model_validator(mode="after")
    def require_count(self) -> "LaneCountRule":
        if self.incoming is None and self.outgoing is None:
            raise ValueError("lane_count requires incoming or outgoing")
        return self


class TurnRule(DirectionalRule):
    kind: Literal["turn"]
    movement: Literal["left", "right", "straight", "uturn"]
    allowed: bool = Field(strict=True)


class ConnectedRule(SourcedRule):
    kind: Literal["connected"]


Rule = Annotated[
    JunctionCountRule | ApproachCountRule | SignalRule | LaneCountRule | TurnRule | ConnectedRule,
    Field(discriminator="kind"),
]


class ReviewPlan(StrictModel):
    rules: list[Rule] = Field(max_length=32)
    visual_requirements: list[str] = Field(max_length=32)
    unverified_requirements: list[str] = Field(max_length=32)

    def validate_sources(self, description: str) -> None:
        """Reject invented quotes and an empty plan; semantic interpretation remains visible."""
        quotes = [rule.source_quote for rule in self.rules]
        quotes += self.visual_requirements + self.unverified_requirements
        if not quotes or any(not quote.strip() or quote not in description for quote in quotes):
            raise ValueError("验收项必须引用需求原文，且检查清单不得为空")


class CheckResult(StrictModel):
    rule_id: str
    property: Property
    status: Literal["pass", "fail", "unverified"]
    source_quote: str = ""
    problem: str | None = None
    reason: str
    evidence: list[str] = Field(default_factory=list)


class ApproachEvidence(StrictModel):
    junction: str
    direction: str
    incoming_edges: list[str]
    outgoing_edges: list[str]
    incoming_lanes: int
    outgoing_lanes: int
    connection_count: int
    controlled_connection_count: int
    uncontrolled_connections: list[str]
    unserved_lanes: list[str]


class XmlReview(StrictModel):
    checks: list[CheckResult]
    approaches: list[ApproachEvidence]

    @property
    def passed(self) -> bool:
        return all(check.status == "pass" for check in self.checks)


class VisualIssue(StrictModel):
    property: Property
    problem: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    location: str = Field(min_length=1)
    signal_claim: Literal["coverage", "presence", "absence"] | None = None
    directions: list[Direction] = Field(default_factory=list)


class VisualReview(StrictModel):
    issues: list[VisualIssue] = Field(max_length=32)
