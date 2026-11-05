import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import config
import main
import review_pipeline
from review_models import ReviewPlan, VisualIssue, VisualReview
from test_xml_rules import crossroad, plan
from xml_checks import check_network


class CheckedPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.plan = plan({"kind": "signals", "policy": "all_approaches"})

    def rules(self, missing=""):
        path = self.directory / "network.xml"
        ET.ElementTree(crossroad(missing)).write(path)
        return check_network(str(path), self.plan)

    def signal_suspicion(self):
        return VisualReview(
            issues=[
                VisualIssue(
                    property="signals",
                    problem="北侧和东侧缺灯",
                    reason="看不到灯符号",
                    location="北侧和东侧",
                    signal_claim="coverage",
                    directions=["north", "east"],
                )
            ]
        )

    def candidate(self, decision, rules):
        return {
            "generated_code": "# mocked candidate, not a live result",
            "execution": {"task_id": "test", "exit_code": 0, "timeout": False},
            "validation": {"passed": True, "image": "review.png"},
            "rule_review": {"passed": rules.passed, **rules.model_dump()},
            "visual_review": {"issues": []},
            "review": decision,
        }

    def test_wrong_north_visual_claim_is_not_a_confirmed_repair_instruction(self):
        rules = self.rules("east")
        decision = review_pipeline.combine_reviews(self.plan, rules, self.signal_suspicion())
        self.assertEqual(decision["outcome"], "repair")
        self.assertEqual(len(decision["issue_details"]), 1)
        self.assertIn("东侧", decision["issue_details"][0]["reason"])
        self.assertNotIn("北侧", decision["issue_details"][0]["reason"])
        self.assertEqual(decision["issue_details"][0]["certainty"], "confirmed")
        self.assertEqual(decision["adjudications"][0]["authority"], "xml")
        feedback = review_pipeline.repair_feedback(self.candidate(decision, rules))
        self.assertNotIn("北侧和东侧缺灯", feedback["diagnostic"])
        self.assertIn("北侧进口 3/3", feedback["diagnostic"])
        self.assertIn("东侧进口 0/3", feedback["diagnostic"])
        self.assertIn("image_path", feedback)

    def test_image_cannot_override_passing_signal_rules(self):
        decision = review_pipeline.combine_reviews(self.plan, self.rules(), self.signal_suspicion())
        self.assertTrue(decision["passed"])
        self.assertEqual(decision["issues"], [])
        self.assertTrue(decision["adjudications"])

    def test_uncovered_visual_structure_and_unsupported_requirements_need_review(self):
        requirements = plan({"kind": "junction_count", "count": 1})
        path = self.directory / "network.xml"
        ET.ElementTree(crossroad()).write(path)
        rules = check_network(str(path), requirements)
        decision = review_pipeline.combine_reviews(requirements, rules, self.signal_suspicion())
        self.assertEqual(decision["outcome"], "review_required")
        requirements.unverified_requirements = ["四根实体灯杆"]
        decision = review_pipeline.combine_reviews(requirements, rules, VisualReview(issues=[]))
        self.assertFalse(decision["passed"])
        self.assertIn("四根实体灯杆", decision["unverified_requirements"])

    def test_network_signal_presence_cannot_settle_local_coverage(self):
        requirements = plan({"kind": "signals", "policy": "present"})
        path = self.directory / "partial.xml"
        ET.ElementTree(crossroad("east")).write(path)
        rules = check_network(str(path), requirements)
        self.assertTrue(rules.passed)
        decision = review_pipeline.combine_reviews(requirements, rules, self.signal_suspicion())
        self.assertEqual(decision["outcome"], "review_required")
        self.assertEqual(decision["adjudications"], [])

    def test_checking_only_north_cannot_settle_east_coverage(self):
        requirements = plan(
            {"kind": "signals", "policy": "all_approaches", "directions": ["north"]}
        )
        path = self.directory / "partial.xml"
        ET.ElementTree(crossroad("east")).write(path)
        rules = check_network(str(path), requirements)
        decision = review_pipeline.combine_reviews(requirements, rules, self.signal_suspicion())
        self.assertEqual(decision["outcome"], "review_required")

    def test_implicit_all_inlets_cannot_settle_unchecked_outbound_side(self):
        root = crossroad()
        root.remove(root.find("edge[@id='north_in']"))
        for connection in list(root.findall("connection")):
            if connection.get("from") == "north_in":
                root.remove(connection)
        path = self.directory / "outbound_only.xml"
        ET.ElementTree(root).write(path)
        rules = check_network(str(path), self.plan)
        self.assertTrue(rules.passed)
        suspicion = VisualReview(
            issues=[
                VisualIssue(
                    property="signals",
                    problem="北侧缺灯",
                    reason="未见灯图标",
                    location="北侧",
                    signal_claim="coverage",
                    directions=["north"],
                )
            ]
        )
        decision = review_pipeline.combine_reviews(self.plan, rules, suspicion)
        self.assertEqual(decision["outcome"], "review_required")
        self.assertEqual(decision["adjudications"], [])

    def test_image_failure_preserves_available_xml_diagnostics(self):
        task = self.directory / "test"
        task.mkdir()
        ET.ElementTree(crossroad("east")).write(task / "network.xml")
        with (
            patch.object(config, "WORKSPACES_DIR", self.temp.name),
            patch.object(
                review_pipeline.runner, "run_generated_code", return_value={"task_id": "test"}
            ),
            patch.object(
                review_pipeline.runner,
                "validate_network",
                return_value={"passed": True, "network": "network.xml", "image": "review.png"},
            ),
            patch.object(
                review_pipeline.agent,
                "review_geometry",
                side_effect=ValueError("image service failed"),
            ),
        ):
            attempt = review_pipeline.evaluate_candidate("# test", self.plan)
        self.assertIn("review_error", attempt)
        self.assertFalse(attempt["rule_review"]["passed"])
        self.assertIsNone(attempt["review"])

    def test_same_plan_is_used_across_repair_and_default_api_selects_new_flow(self):
        failed_rules, good_rules = self.rules("east"), self.rules()
        failed = self.candidate(
            review_pipeline.combine_reviews(self.plan, failed_rules, VisualReview(issues=[])),
            failed_rules,
        )
        good = self.candidate(
            review_pipeline.combine_reviews(self.plan, good_rules, VisualReview(issues=[])),
            good_rules,
        )
        with (
            patch.object(config, "MODEL_CONFIGURED", True),
            patch.object(main.shutil, "which", return_value="netconvert"),
            patch.object(agent, "plan_review", return_value=self.plan) as planner,
            patch.object(agent, "generate_code", return_value="# mocked generation") as generator,
            patch.object(
                review_pipeline, "evaluate_candidate", side_effect=[failed, good]
            ) as evaluate,
        ):
            result = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic", output_kind="map", description="四向信号灯"
                )
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["review_mode"], "rules_and_image")
        self.assertEqual(len(result["attempts"]), 2)
        planner.assert_called_once()
        self.assertEqual(generator.call_count, 2)
        self.assertTrue(all(call.args[1] is self.plan for call in evaluate.call_args_list))

    def test_persistent_geometry_suspicion_is_bounded_and_never_called_confirmed(self):
        rules = self.rules()
        visual = VisualReview(
            issues=[
                VisualIssue(
                    property="geometry",
                    problem="疑似道路错位",
                    reason="图中端点偏移",
                    location="路口右侧",
                )
            ]
        )
        decision = review_pipeline.combine_reviews(self.plan, rules, visual)
        self.assertEqual(decision["issue_details"][0]["certainty"], "suspected")
        candidate = self.candidate(decision, rules)
        with (
            patch.object(agent, "plan_review", return_value=self.plan),
            patch.object(agent, "generate_code", return_value="# mocked generation"),
            patch.object(review_pipeline, "evaluate_candidate", return_value=candidate),
        ):
            result = review_pipeline.generate_checked("test")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(len(result["attempts"]), config.MAX_GENERATION_ATTEMPTS)

    def test_plan_corrects_source_failure_once_and_retains_diagnostic(self):
        invalid = {
            "rules": [],
            "visual_requirements": ["伪造的原文"],
            "unverified_requirements": [],
        }
        valid = {"rules": [], "visual_requirements": ["十字路口"], "unverified_requirements": []}
        with patch.object(agent, "OpenAI") as client, self.assertLogs("agent", level="WARNING"):
            create = client.return_value.chat.completions.create
            create.side_effect = [
                SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))]
                )
                for value in (invalid, valid)
            ]
            result = agent.plan_review("生成一个十字路口")
        self.assertEqual(result.visual_requirements, ["十字路口"])
        self.assertEqual(create.call_count, 2)
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(messages[1]["content"], "生成一个十字路口")
        self.assertEqual(json.loads(messages[-2]["content"]), invalid)
        self.assertIn("验收项必须引用需求原文", messages[-1]["content"])

    def test_invalid_plan_stops_after_one_correction(self):
        with patch.object(agent, "OpenAI") as client, self.assertLogs("agent", level="WARNING"):
            create = client.return_value.chat.completions.create
            create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )
            with self.assertRaises(ValueError):
                agent.plan_review("生成一个十字路口")
        self.assertEqual(create.call_count, 2)

    def test_plan_source_validation_and_geometry_payload(self):
        raw = {
            "rules": [
                {
                    "kind": "signals",
                    "policy": "all_approaches",
                    "source_quote": "四个方向都必须有信号灯",
                }
            ],
            "visual_requirements": [],
            "unverified_requirements": [],
        }
        image = self.directory / "image.png"
        image.write_bytes(b"test-image")
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))]
            )
            parsed = agent.plan_review("四个方向都必须有信号灯")
            self.assertIsInstance(parsed, ReviewPlan)
            with self.assertRaises(ValueError):
                agent.plan_review("没有引用的另一句话")
            create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"issues":[]}'))]
            )
            agent.review_geometry(["十字形"], str(image))
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(
            json.loads(messages[1]["content"][0]["text"]), {"visual_requirements": ["十字形"]}
        )
        self.assertEqual(messages[1]["content"][1]["type"], "image_url")


if __name__ == "__main__":
    unittest.main()
