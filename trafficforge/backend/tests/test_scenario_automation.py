"""Keep preview allocations faithful to the authored SUMO departure schedule."""

import collections
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenario_demand import build_demand
from scenario_models import AutomationMixShare, ScenarioIntent, ScenarioSettings, resolve_plan
from test_xml_rules import crossroad


class ScenarioAutomationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.network = self.output / "network.net.xml"
        root = crossroad()
        for lane in root.iter("lane"):
            lane.set("length", "100")
            lane.set("speed", "13.9")
            lane.set("shape", "0,0 100,0")
        ET.ElementTree(root).write(self.network)
        self.settings = ScenarioSettings()

    def demand(self, *, count=7, mix=None, mode="autonomy", seed=42):
        intent = ScenarioIntent(
            objective="测试",
            vehicle_count=count,
            seed=seed,
            automation_mix=mix or [],
        )
        plan = resolve_plan(intent, self.settings, simulation_mode=mode)
        demand = build_demand(self.network, self.output, plan, self.settings)
        xml = ET.parse(self.output / "traffic.rou.xml").getroot()
        return plan, demand, xml

    def test_percentages_must_be_unique_finite_bounded_and_sum_to_hundred(self):
        invalid = (
            [{"level": "L6", "share_percent": 100}],
            [{"level": "L0", "share_percent": float("nan")}],
            [{"level": "L0", "share_percent": -1}],
            [{"level": "L0", "share_percent": 101}],
            [{"level": "L0", "share_percent": 60}],
            [{"level": "L0", "share_percent": 50}] * 2,
        )
        for mix in invalid:
            with self.subTest(mix=mix), self.assertRaises(ValueError):
                ScenarioIntent(objective="测试", automation_mix=mix)

    def test_mix_requires_continuous_original_quote(self):
        intent = ScenarioIntent(
            objective="测试",
            automation_mix=[{"level": "L0", "share_percent": 100}],
        )
        with self.assertRaises(ValueError):
            intent.validate_sources("测试，L0占100%")
        intent.automation_mix_quote = "L0占100%"
        intent.validate_sources("测试，L0占100%")
        with self.assertRaises(ValueError):
            intent.validate_sources("测试，L5占100%")

    def test_quoted_percentages_cannot_be_replaced_by_plausible_model_values(self):
        quote = "L0占50%，L3占50%"
        intent = ScenarioIntent(
            objective="测试",
            automation_mix=[{"level": "L0", "share_percent": 100}],
            automation_mix_quote=quote,
        )
        with self.assertRaisesRegex(ValueError, "L0 原文为 50%，模型却为 100%.*遗漏.*L3"):
            intent.validate_sources("测试，" + quote)

    def test_common_explicit_forms_are_verified_against_exact_numbers(self):
        quotes = (
            "L0占70%、L3占30%",
            "L0 70%/L3 30%",
            "L0占比为70％，L3占比：30％",
            "l0比例为70%，l3为30%",
            "L0：70%，L3=30%",
            "Ｌ０的车辆占７０％，Ｌ３的车辆占３０％",
            "L0占比70.5%，L3占比29.5%",
        )
        for quote in quotes:
            with self.subTest(quote=quote):
                first = 70.5 if "70.5" in quote else 70
                intent = ScenarioIntent(
                    objective="测试",
                    automation_mix=[
                        {"level": "L0", "share_percent": first},
                        {"level": "L3", "share_percent": 100 - first},
                    ],
                    automation_mix_quote=quote,
                )
                intent.validate_sources("测试，" + quote)

    def test_subset_quote_and_added_level_are_rejected(self):
        intent = ScenarioIntent(
            objective="测试",
            automation_mix=[
                {"level": "L0", "share_percent": 70},
                {"level": "L3", "share_percent": 30},
            ],
            automation_mix_quote="L0占70%",
        )
        with self.assertRaisesRegex(ValueError, "未包含.*全部"):
            intent.validate_sources("测试，L0占70%，L3占30%")
        intent.automation_mix_quote = "L0占70%，L5占30%"
        with self.assertRaisesRegex(ValueError, "原文没有可核验的 L3"):
            intent.validate_sources("测试，L0占70%，L5占30%")

    def test_complete_percentages_cannot_be_silently_dropped(self):
        intent = ScenarioIntent(objective="测试")
        with self.assertRaisesRegex(ValueError, "完整.*模型未提取"):
            intent.validate_sources("测试，L0占70%，L3占30%")

    def test_incomplete_percentages_must_remain_pending_not_inferred(self):
        quote = "L0占70%，其余L3"
        inferred = ScenarioIntent(
            objective="测试",
            automation_mix=[
                {"level": "L0", "share_percent": 70},
                {"level": "L3", "share_percent": 30},
            ],
            automation_mix_quote=quote,
        )
        with self.assertRaisesRegex(ValueError, "原文没有可核验的 L3"):
            inferred.validate_sources("测试，" + quote)
        pending = ScenarioIntent(objective="测试")
        with self.assertRaisesRegex(ValueError, "待验证"):
            pending.validate_sources("测试，" + quote)
        pending.unverified_requirements = [quote]
        pending.validate_sources("测试，" + quote)

    def test_natural_fraction_is_pending_instead_of_unverifiable_applied_mix(self):
        quote = "L0和L3各半"
        inferred = ScenarioIntent(
            objective="测试",
            automation_mix=[
                {"level": "L0", "share_percent": 50},
                {"level": "L3", "share_percent": 50},
            ],
            automation_mix_quote=quote,
        )
        with self.assertRaisesRegex(ValueError, "无法.*核验.*完整数字百分比"):
            inferred.validate_sources("测试，" + quote)
        pending = ScenarioIntent(objective="测试", unverified_requirements=[quote])
        pending.validate_sources("测试，" + quote)

    def test_unsupported_level_expressions_cannot_disappear_into_empty_mix(self):
        for quote in ("L0和L3各半", "70% L0,30% L3", "仅使用L3车辆", "L0占70%，30% L3"):
            with self.subTest(quote=quote):
                intent = ScenarioIntent(objective="测试")
                with self.assertRaisesRegex(ValueError, "未保留为待验证"):
                    intent.validate_sources("测试，" + quote)
                intent.unverified_requirements = [quote]
                intent.validate_sources("测试，" + quote)

    def test_pending_level_names_alone_do_not_preserve_the_requested_ratio(self):
        intent = ScenarioIntent(objective="测试", unverified_requirements=["L0", "L3"])
        with self.assertRaisesRegex(ValueError, "未保留为待验证"):
            intent.validate_sources("测试，70% L0,30% L3")

    def test_extra_unparsed_request_still_requires_pending_with_complete_applied_mix(self):
        description = "测试，L0占100%，L0和L3各半"
        intent = ScenarioIntent(
            objective="测试",
            automation_mix=[{"level": "L0", "share_percent": 100}],
            automation_mix_quote="L0占100%",
        )
        with self.assertRaisesRegex(ValueError, "未保留为待验证"):
            intent.validate_sources(description)
        intent.unverified_requirements = ["L0和L3各半"]
        intent.validate_sources(description)

    def test_conflicting_or_invalid_source_percentages_are_explained(self):
        for quote in ("L0占70%，L0占100%", "L0占-70%，L3占170%"):
            intent = ScenarioIntent(
                objective="测试",
                automation_mix=[{"level": "L0", "share_percent": 100}],
                automation_mix_quote=quote,
            )
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, "原文 L0"):
                intent.validate_sources("测试，" + quote)

    def test_default_autonomy_is_disclosed_l0_and_traffic_preserves_legacy(self):
        plan, demand, xml = self.demand()
        self.assertTrue(any("默认 L0 100%" in note for note in plan.defaults_used))
        self.assertIn("不代表", plan.automation_note)
        self.assertEqual(len(demand.automation_mix), 6)
        self.assertEqual(demand.automation_mix[0].vehicle_count, 7)
        self.assertTrue(all(v.get("type") == "L0" for v in xml.findall("vehicle")))
        self.assertEqual(xml.find("vType").get("id"), "L0")
        plan, demand, xml = self.demand(mode="traffic")
        self.assertEqual(demand.automation_mix, [])
        self.assertIsNone(plan.automation_note)
        self.assertTrue(all(v.get("type") == "passenger" for v in xml.findall("vehicle")))
        self.assertEqual(xml.find("vType").get("speedDev"), "0")

    def test_exact_counts_and_percentages_match_xml_and_core_level_ids(self):
        mix = [
            AutomationMixShare(level="L0", share_percent=50),
            AutomationMixShare(level="L2", share_percent=30),
            AutomationMixShare(level="L5", share_percent=20),
        ]
        _, demand, xml = self.demand(count=7, mix=mix)
        counts = collections.Counter(v.get("type") for v in xml.findall("vehicle"))
        self.assertEqual(counts, {"L0": 4, "L2": 2, "L5": 1})
        self.assertEqual(sum(item.vehicle_count for item in demand.automation_mix), 7)
        for item in demand.automation_mix:
            self.assertEqual(item.vehicle_count, counts[item.level])
            self.assertAlmostEqual(item.share_percent, 100 * counts[item.level] / 7)
        core_pattern = re.compile(r"(?:^|_)L([0-5])(?:[._]|$)")
        for vehicle in xml.findall("vehicle"):
            match = core_pattern.search(vehicle.get("id"))
            self.assertIsNotNone(match)
            self.assertEqual("L" + match.group(1), vehicle.get("type"))
        self.assertEqual(xml.find("vType[@id='L5']").get("tau"), "0.6")
        self.assertEqual(xml.find("vType[@id='L0']").get("tau"), "1")

    def test_tiny_counts_keep_zero_shares_and_ties_use_level_order(self):
        _, demand, xml = self.demand(
            count=1,
            mix=[
                {"level": "L5", "share_percent": 50},
                {"level": "L0", "share_percent": 50},
            ],
        )
        self.assertEqual(len(xml.findall("vehicle")), 1)
        self.assertEqual(demand.automation_mix[0].vehicle_count, 1)
        self.assertEqual(demand.automation_mix[-1].vehicle_count, 0)
        self.assertEqual(sum(item.share_percent for item in demand.automation_mix), 100)

    def test_mix_shuffle_is_seeded_and_preserves_routes_and_departures(self):
        mix = [
            {"level": "L0", "share_percent": 50},
            {"level": "L5", "share_percent": 50},
        ]
        self.demand(count=50, mix=mix)
        first = (self.output / "traffic.rou.xml").read_bytes()
        _, _, xml = self.demand(count=50, mix=mix)
        self.assertEqual(first, (self.output / "traffic.rou.xml").read_bytes())
        _, _, ordinary = self.demand(count=50, mode="traffic")
        for automated, passenger in zip(
            xml.findall("vehicle"), ordinary.findall("vehicle"), strict=True
        ):
            self.assertEqual(automated.get("route"), passenger.get("route"))
            self.assertEqual(automated.get("depart"), passenger.get("depart"))
        self.demand(count=50, mix=mix, seed=43)
        self.assertNotEqual(first, (self.output / "traffic.rou.xml").read_bytes())

    def test_explicit_level_request_in_traffic_mode_is_not_discarded(self):
        plan, demand, xml = self.demand(mix=[{"level": "L4", "share_percent": 100}], mode="traffic")
        self.assertEqual(plan.simulation_mode, "traffic")
        self.assertIn("保留", plan.automation_note)
        self.assertEqual(demand.automation_mix[4].vehicle_count, 7)
        self.assertTrue(all(v.get("type") == "L4" for v in xml.findall("vehicle")))


if __name__ == "__main__":
    unittest.main()
