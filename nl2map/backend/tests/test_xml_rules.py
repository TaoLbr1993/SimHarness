"""Regression cases for deterministic acceptance of compiled network data."""

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from review_models import ReviewPlan
from xml_checks import check_network


def crossroad(missing="", malformed=False):
    root = ET.Element("net")
    ET.SubElement(root, "junction", id="center", x="0", y="0", type="traffic_light")
    for side, x, y in (("north", 0, 100), ("east", 100, 0), ("south", 0, -100), ("west", -100, 0)):
        ET.SubElement(root, "junction", id=side, x=str(x), y=str(y), type="dead_end")
        for suffix, start, end in (("in", side, "center"), ("out", "center", side)):
            edge = ET.SubElement(root, "edge", {"id": f"{side}_{suffix}", "from": start, "to": end})
            ET.SubElement(edge, "lane", id=f"{side}_{suffix}_0", index="0")
    signal = ET.SubElement(root, "tlLogic", id="tls", programID="0")
    ET.SubElement(signal, "phase", duration="30", state="G" * 12)
    index = 0
    sides = ["north", "east", "south", "west"]
    for side in sides:
        for movement, other in zip(("r", "s", "l"), [s for s in sides if s != side], strict=True):
            attrs = {
                "from": side + "_in",
                "to": other + "_out",
                "fromLane": "0",
                "toLane": "0",
                "dir": movement,
            }
            if side != missing:
                attrs.update(tl="tls", linkIndex=str(99 if malformed else index))
            ET.SubElement(root, "connection", attrs)
            index += 1
    return root


def plan(*rules):
    return ReviewPlan.model_validate(
        {
            "rules": [{"source_quote": "test", **rule} for rule in rules],
            "visual_requirements": [],
            "unverified_requirements": [],
        }
    )


class XmlRulesTest(unittest.TestCase):
    def check(self, root, requirements):
        with tempfile.TemporaryDirectory() as tmp:
            network = Path(tmp, "network.xml")
            ET.ElementTree(root).write(network)
            return check_network(str(network), requirements)

    def test_partial_signal_is_localized_to_east_without_false_north(self):
        result = self.check(
            crossroad("east"), plan({"kind": "signals", "policy": "all_approaches"})
        )
        failed = [check for check in result.checks if check.status == "fail"]
        self.assertEqual(len(failed), 1)
        self.assertIn("东侧", failed[0].reason)
        self.assertIn("0/3", failed[0].reason)
        self.assertIn("east_in", " ".join(failed[0].evidence))
        self.assertNotIn("北侧", failed[0].reason)
        self.assertEqual(
            {a.direction: a.controlled_connection_count for a in result.approaches},
            {
                "north": 3,
                "east": 0,
                "south": 3,
                "west": 3,
            },
        )

    def test_four_signals_pass_but_invalid_phase_index_does_not(self):
        requirements = plan({"kind": "signals", "policy": "all_approaches"})
        self.assertTrue(self.check(crossroad(), requirements).passed)
        result = self.check(crossroad(malformed=True), requirements)
        self.assertTrue(any(c.property == "signals" and c.status == "fail" for c in result.checks))

    def test_one_uncontrolled_turn_or_unserved_lane_prevents_complete_coverage(self):
        requirements = plan({"kind": "signals", "policy": "all_approaches"})
        root = crossroad()
        root.find("connection").attrib.pop("tl")
        self.assertFalse(self.check(root, requirements).passed)
        root = crossroad()
        ET.SubElement(root.find("edge"), "lane", id="north_in_1", index="1")
        self.assertFalse(self.check(root, requirements).passed)

    def test_lane_count_and_forbidden_turn_report_actual_edges(self):
        result = self.check(
            crossroad(),
            plan(
                {"kind": "lane_count", "incoming": 2, "directions": ["east"]},
                {"kind": "turn", "movement": "left", "allowed": False, "directions": ["north"]},
            ),
        )
        failures = [c for c in result.checks if c.status == "fail"]
        self.assertEqual({c.property for c in failures}, {"lanes", "connections"})
        self.assertTrue(all(c.evidence for c in failures))

    def test_missing_direction_is_not_a_vacuous_pass(self):
        root = crossroad()
        for edge in list(root.findall("edge")):
            if edge.get("id").startswith("east"):
                root.remove(edge)
        result = self.check(
            root, plan({"kind": "signals", "policy": "all_approaches", "directions": ["east"]})
        )
        self.assertFalse(result.passed)

    def test_disconnected_roads_and_ambiguous_direction_are_not_accepted(self):
        root = crossroad()
        ET.SubElement(root, "junction", id="extra1", x="200", y="200")
        ET.SubElement(root, "junction", id="extra2", x="300", y="200")
        edge = ET.SubElement(root, "edge", {"id": "extra", "from": "extra1", "to": "extra2"})
        ET.SubElement(edge, "lane", id="extra_0", index="0")
        result = self.check(root, plan({"kind": "connected"}))
        self.assertTrue(any(c.rule_id == "R1" and c.status == "fail" for c in result.checks))
        root = crossroad()
        root.find("junction[@id='east']").set("y", "100")
        result = self.check(
            root, plan({"kind": "lane_count", "incoming": 1, "directions": ["east"]})
        )
        self.assertTrue(any(c.status == "unverified" for c in result.checks))

    def test_plan_rejects_invented_source_and_unknown_rule(self):
        requirements = plan({"kind": "junction_count", "count": 1})
        with self.assertRaises(ValueError):
            requirements.validate_sources("a different request")
        with self.assertRaises(ValueError):
            plan({"kind": "physical_lamp_position", "count": 4})

    def test_parallel_edges_are_not_silently_added_as_one_approach(self):
        root = crossroad()
        edge = ET.SubElement(root, "edge", {"id": "east_parallel", "from": "east", "to": "center"})
        ET.SubElement(edge, "lane", id="east_parallel_0", index="0")
        result = self.check(
            root, plan({"kind": "lane_count", "incoming": 2, "directions": ["east"]})
        )
        self.assertTrue(any(c.status == "unverified" for c in result.checks))
        self.assertFalse(result.passed)

    def test_outbound_only_arm_is_not_an_unsignalized_incoming_lane(self):
        root = crossroad()
        root.remove(root.find("edge[@id='north_in']"))
        for connection in list(root.findall("connection")):
            if connection.get("from") == "north_in":
                root.remove(connection)
        result = self.check(root, plan({"kind": "signals", "policy": "all_approaches"}))
        self.assertTrue(result.passed)
        result = self.check(
            root, plan({"kind": "signals", "policy": "all_approaches", "directions": ["north"]})
        )
        self.assertFalse(result.passed)

    def test_all_junctions_does_not_silently_skip_one_without_inlets(self):
        root = crossroad()
        ET.SubElement(root, "junction", id="out_only", x="300", y="300")
        for index, (x, y) in enumerate(((400, 300), (300, 400), (200, 300), (300, 200))):
            node = f"extra_{index}"
            ET.SubElement(root, "junction", id=node, x=str(x), y=str(y))
            edge = ET.SubElement(root, "edge", {"id": node, "from": "out_only", "to": node})
            ET.SubElement(edge, "lane", id=node + "_0", index="0")
        result = self.check(
            root, plan({"kind": "signals", "policy": "all_approaches", "scope": "all"})
        )
        self.assertFalse(result.passed)
        unverified = [c for c in result.checks if c.status == "unverified"]
        self.assertEqual(len(unverified), 1)
        self.assertIn("out_only", unverified[0].reason)


if __name__ == "__main__":
    unittest.main()
