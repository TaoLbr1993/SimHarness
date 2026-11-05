"""Deterministic acceptance checks against compiled SUMO XML, never generated code."""

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

import config
from review_models import (
    ApproachCountRule,
    ApproachEvidence,
    CheckResult,
    ConnectedRule,
    JunctionCountRule,
    LaneCountRule,
    Property,
    ReviewPlan,
    Rule,
    SignalRule,
    TurnRule,
    XmlReview,
)

SIDE_NAMES = {"north": "北侧", "east": "东侧", "south": "南侧", "west": "西侧"}
TURN_CODES = {"left": "l", "right": "r", "straight": "s", "uturn": "t"}


def _required(element: ET.Element, key: str) -> str:
    value = element.get(key)
    if value is None:
        raise ValueError(f"XML {element.tag} 缺少 {key}")
    return value


def _connection_name(connection: ET.Element) -> str:
    return (
        f"{connection.get('from')}[{connection.get('fromLane')}] -> "
        f"{connection.get('to')}[{connection.get('toLane')}]"
    )


@dataclass
class _Network:
    edges: dict[str, ET.Element]
    nodes: dict[str, ET.Element]
    connections: list[ET.Element]
    programs: dict[str, list[ET.Element]]
    neighbors: dict[str, set[str]]

    @classmethod
    def read(cls, path: str) -> "_Network":
        root = ET.parse(path).getroot()
        if root.tag != "net":
            raise ValueError("检查对象必须是编译后的 SUMO net")
        edges = {
            _required(e, "id"): e for e in root.findall("edge") if e.get("function") != "internal"
        }
        nodes = {
            _required(j, "id"): j for j in root.findall("junction") if j.get("type") != "internal"
        }
        connections = [
            c for c in root.findall("connection") if not c.get("from", "").startswith(":")
        ]
        programs: dict[str, list[ET.Element]] = defaultdict(list)
        for program in root.findall("tlLogic"):
            programs[_required(program, "id")].append(program)
        neighbors: dict[str, set[str]] = defaultdict(set)
        for edge in edges.values():
            start, end = _required(edge, "from"), _required(edge, "to")
            neighbors[start].add(end)
            neighbors[end].add(start)
        return cls(edges, nodes, connections, dict(programs), dict(neighbors))

    def controlled(self, connection: ET.Element) -> bool:
        programs = self.programs.get(connection.get("tl", ""), [])
        try:
            index = int(connection.get("linkIndex", "-1"))
            return (
                bool(programs)
                and index >= 0
                and all(
                    program.findall("phase")
                    and all(
                        len(phase.get("state", "")) > index
                        and math.isfinite(float(phase.get("duration", "0")))
                        and float(phase.get("duration", "0")) > 0
                        for phase in program.findall("phase")
                    )
                    for program in programs
                )
            )
        except ValueError:
            return False

    @property
    def intersections(self) -> list[str]:
        return sorted(node for node, adjacent in self.neighbors.items() if len(adjacent) >= 3)

    def side(self, node: str, neighbor: str) -> str:
        origin, end = self.nodes[node], self.nodes[neighbor]
        dx = float(_required(end, "x")) - float(_required(origin, "x"))
        dy = float(_required(end, "y")) - float(_required(origin, "y"))
        major, minor = max(abs(dx), abs(dy)), min(abs(dx), abs(dy))
        if (
            not math.isfinite(major + minor)
            or major == 0
            or minor / major > math.tan(math.radians(config.XML_CARDINAL_MAX_DEVIATION_DEG))
        ):
            return "unknown"
        return (
            ("east" if dx > 0 else "west")
            if abs(dx) > abs(dy)
            else ("north" if dy > 0 else "south")
        )

    def approaches(self, node: str) -> list[ApproachEvidence]:
        result = []
        for neighbor in sorted(self.neighbors[node]):
            incoming = [
                e for e in self.edges.values() if e.get("from") == neighbor and e.get("to") == node
            ]
            outgoing = [
                e for e in self.edges.values() if e.get("to") == neighbor and e.get("from") == node
            ]
            ids = {_required(e, "id") for e in incoming}
            connections = [c for c in self.connections if c.get("from") in ids]
            controlled = [c for c in connections if self.controlled(c)]
            served = {(c.get("from"), c.get("fromLane")) for c in controlled}
            unserved = [
                _required(lane, "id")
                for edge in incoming
                for lane in edge.findall("lane")
                if (edge.get("id"), lane.get("index")) not in served
            ]
            result.append(
                ApproachEvidence(
                    junction=node,
                    direction=self.side(node, neighbor),
                    incoming_edges=sorted(ids),
                    outgoing_edges=sorted(_required(e, "id") for e in outgoing),
                    incoming_lanes=sum(len(e.findall("lane")) for e in incoming),
                    outgoing_lanes=sum(len(e.findall("lane")) for e in outgoing),
                    connection_count=len(connections),
                    controlled_connection_count=len(controlled),
                    uncontrolled_connections=[
                        _connection_name(c) for c in connections if not self.controlled(c)
                    ],
                    unserved_lanes=unserved,
                )
            )
        return result


def _check(
    rule_id: str,
    prop: Property,
    passed: bool | None,
    reason: str,
    evidence: list[str],
    quote: str = "",
    problem: str = "结构要求不满足",
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        property=prop,
        status="unverified" if passed is None else "pass" if passed else "fail",
        source_quote=quote,
        problem=None if passed else problem,
        reason=reason,
        evidence=evidence,
    )


def _integrity(net: _Network) -> list[CheckResult]:
    references, signal_errors = [], []
    for edge_id, edge in net.edges.items():
        if edge.get("from") not in net.nodes or edge.get("to") not in net.nodes:
            references.append(f"edge {edge_id}: from/to 节点不存在")
    for connection in net.connections:
        name = _connection_name(connection)
        before, after = (
            net.edges.get(connection.get("from", "")),
            net.edges.get(connection.get("to", "")),
        )
        if before is None or after is None:
            references.append(name + ": 道路不存在")
            continue
        for edge, attr in ((before, "fromLane"), (after, "toLane")):
            if connection.get(attr) not in {lane.get("index") for lane in edge.findall("lane")}:
                references.append(name + f": {attr} 引用不存在的车道")
        if before.get("to") != after.get("from"):
            references.append(name + ": 两条道路不在同一节点相接")
        if connection.get("tl") and not net.controlled(connection):
            signal_errors.append(
                name + f": tl={connection.get('tl')}, linkIndex={connection.get('linkIndex')}"
                " 无有效相位绑定"
            )
    for controller, programs in net.programs.items():
        for program in programs:
            phases = program.findall("phase")
            try:
                valid = (
                    bool(phases)
                    and len({len(p.get("state", "")) for p in phases}) == 1
                    and all(
                        bool(p.get("state"))
                        and math.isfinite(float(p.get("duration", "0")))
                        and float(p.get("duration", "0")) > 0
                        for p in phases
                    )
                )
            except ValueError:
                valid = False
            if not valid:
                signal_errors.append(
                    f"tlLogic {controller}/{program.get('programID')}: "
                    "相位为空、长度不一致或时长无效"
                )
    return [
        _check(
            "integrity.connections",
            "connections",
            not references,
            "道路/车道引用与连接端点有效" if not references else "；".join(references),
            references,
        ),
        _check(
            "integrity.signals",
            "signals",
            not signal_errors,
            "信号程序和连接相位索引有效（未验证冲突安全）"
            if not signal_errors
            else "；".join(signal_errors),
            signal_errors,
        ),
    ]


def _selected(
    net: _Network,
    rule: ApproachCountRule | SignalRule | LaneCountRule | TurnRule,
    rule_id: str,
    prop: Property,
) -> tuple[list[ApproachEvidence], list[CheckResult]]:
    nodes = net.intersections
    if not nodes:
        return [], [
            _check(
                rule_id, prop, False, "未找到至少三个相邻道路端点的交叉口", [], rule.source_quote
            )
        ]
    if rule.scope == "single" and len(nodes) != 1:
        return [], [
            _check(
                rule_id,
                prop,
                None,
                f"有 {len(nodes)} 个交叉口，无法唯一确定检查对象",
                nodes,
                rule.source_quote,
                "检查对象不明确",
            )
        ]
    approaches = [a for node in nodes for a in net.approaches(node)]
    if not isinstance(rule, ApproachCountRule) and any(
        len(a.incoming_edges) > 1 or len(a.outgoing_edges) > 1 for a in approaches
    ):
        return [], [
            _check(
                rule_id,
                prop,
                None,
                "同一相邻节点之间有多条道路，进口定义不唯一",
                [edge for a in approaches for edge in a.incoming_edges + a.outgoing_edges],
                rule.source_quote,
                "进口无法唯一匹配",
            )
        ]
    directions = getattr(rule, "directions", [])
    if isinstance(rule, SignalRule) and not directions:
        approaches = [a for a in approaches if a.incoming_lanes > 0]
        missing_inlets = set(nodes) - {a.junction for a in approaches}
        return approaches, [
            _check(
                rule_id,
                prop,
                None,
                f"{node} 未找到可检查信控的进口车道",
                [node],
                rule.source_quote,
                "没有可检查的进口",
            )
            for node in sorted(missing_inlets)
        ]
    if not directions:
        return approaches, []
    for node in nodes:
        sides = [a.direction for a in approaches if a.junction == node]
        if "unknown" in sides or len(sides) != len(set(sides)):
            return [], [
                _check(
                    rule_id,
                    prop,
                    None,
                    f"{node} 含斜向或同侧多进口，不能唯一对应四个方位",
                    sides,
                    rule.source_quote,
                    "方位无法可靠匹配",
                )
            ]
    checks = []
    for node in nodes:
        for side in directions:
            if not any(a.junction == node and a.direction == side for a in approaches):
                checks.append(
                    _check(
                        rule_id,
                        prop,
                        False,
                        f"{node} 缺少{SIDE_NAMES[side]}进口/出口",
                        [node, side],
                        rule.source_quote,
                    )
                )
    return [a for a in approaches if a.direction in directions], checks


def _connected(net: _Network) -> list[list[str]]:
    unseen, groups = set(net.neighbors), []
    while unseen:
        queue, group = [min(unseen)], set()
        while queue:
            node = queue.pop()
            if node in group:
                continue
            group.add(node)
            queue.extend(net.neighbors[node] - group)
        groups.append(sorted(group))
        unseen -= group
    return groups


def _rule_checks(net: _Network, rule: Rule, rule_id: str) -> list[CheckResult]:
    if isinstance(rule, JunctionCountRule):
        return [
            _check(
                rule_id,
                "topology",
                len(net.intersections) == rule.count,
                f"交叉口数量 {len(net.intersections)}，要求 {rule.count}",
                net.intersections,
                rule.source_quote,
            )
        ]
    if isinstance(rule, ConnectedRule):
        groups = _connected(net)
        return [
            _check(
                rule_id,
                "connections",
                len(groups) == 1,
                f"道路图有 {len(groups)} 个弱连通分量（不等于所有转向可达）",
                [", ".join(group) for group in groups],
                rule.source_quote,
            )
        ]
    if isinstance(rule, SignalRule) and rule.policy != "all_approaches":
        active = [c for c in net.connections if net.controlled(c)]
        passed = bool(active) if rule.policy == "present" else not active
        return [
            _check(
                rule_id,
                "signals",
                passed,
                f"全网 {len(active)} 个有效信控连接，要求 {rule.policy}",
                [_connection_name(c) for c in active],
                rule.source_quote,
            )
        ]
    prop: Property = (
        "topology"
        if isinstance(rule, ApproachCountRule)
        else (
            "signals"
            if isinstance(rule, SignalRule)
            else "lanes"
            if isinstance(rule, LaneCountRule)
            else "connections"
        )
    )
    approaches, checks = _selected(net, rule, rule_id, prop)
    if isinstance(rule, ApproachCountRule):
        for node in sorted({a.junction for a in approaches}):
            arms = [a for a in approaches if a.junction == node]
            ambiguous = any(len(a.incoming_edges) > 1 or len(a.outgoing_edges) > 1 for a in arms)
            checks.append(
                _check(
                    rule_id,
                    prop,
                    None if ambiguous else len(arms) == rule.count,
                    f"{node} 有 {len(arms)} 个相邻道路端点，要求 {rule.count}"
                    + ("；存在平行边，进口定义不唯一" if ambiguous else ""),
                    [edge for a in arms for edge in a.incoming_edges + a.outgoing_edges],
                    rule.source_quote,
                )
            )
        return checks
    for approach in approaches:
        label = f"{approach.junction} {SIDE_NAMES.get(approach.direction, '未定向侧')}"
        evidence = approach.incoming_edges + approach.outgoing_edges
        if isinstance(rule, SignalRule):
            # Every movement and incoming lane must be covered, not merely one icon.
            passed = (
                approach.incoming_lanes > 0
                and approach.connection_count > 0
                and approach.controlled_connection_count == approach.connection_count
                and not approach.unserved_lanes
            )
            reason = (
                f"{label}进口 {approach.controlled_connection_count}/{approach.connection_count}"
                " 个连接受控；"
                f"{len(approach.unserved_lanes)} 条进口车道没有有效信控连接"
            )
            checks.append(
                _check(
                    rule_id,
                    prop,
                    passed,
                    reason,
                    evidence + approach.uncontrolled_connections + approach.unserved_lanes,
                    rule.source_quote,
                    "进口信号控制不完整",
                )
            )
        elif isinstance(rule, LaneCountRule):
            pairs = [
                ("进口", approach.incoming_lanes, rule.incoming),
                ("出口", approach.outgoing_lanes, rule.outgoing),
            ]
            checks.append(
                _check(
                    rule_id,
                    prop,
                    all(expected is None or actual == expected for _, actual, expected in pairs),
                    label
                    + "："
                    + "；".join(
                        f"{name} {actual} 条，要求 {expected}"
                        for name, actual, expected in pairs
                        if expected is not None
                    ),
                    evidence,
                    rule.source_quote,
                    "车道数量不符合要求",
                )
            )
        elif isinstance(rule, TurnRule):
            turns = [
                c
                for c in net.connections
                if c.get("from") in approach.incoming_edges
                and c.get("dir", "").lower() == TURN_CODES[rule.movement]
            ]
            passed = bool(turns) if rule.allowed else not turns
            checks.append(
                _check(
                    rule_id,
                    prop,
                    passed,
                    f"{label} 有 {len(turns)} 个 {rule.movement} 连接，"
                    f"要求 {'存在' if rule.allowed else '禁止'}",
                    evidence + [_connection_name(c) for c in turns],
                    rule.source_quote,
                    "转向连接不符合要求",
                )
            )
    return checks


def check_network(network_path: str, plan: ReviewPlan) -> XmlReview:
    """Check actual XML against the fixed acceptance plan and return auditable evidence."""
    net = _Network.read(network_path)
    checks = _integrity(net)
    if any(c.status == "fail" and c.property == "connections" for c in checks):
        return XmlReview(checks=checks, approaches=[])
    for index, rule in enumerate(plan.rules, start=1):
        checks.extend(_rule_checks(net, rule, f"R{index}"))
    return XmlReview(
        checks=checks, approaches=[a for node in net.intersections for a in net.approaches(node)]
    )
