import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import config
import main
import runner

ROAD_CODE = """\
import roadgen
m = roadgen.Map()
lanes = [roadgen.Lane(width=3.5, direction="forward"),
         roadgen.Lane(width=3.5, direction="backward")]
a = m.add_road(start=(0., 0., 0.), end=(100., 0., 0.), lanes=lanes)
b = m.add_road(start=(100., 0., 0.), end=(200., 0., 0.), lanes=lanes)
m.connect(a, b)
m.export_sumo("output/")
"""


@unittest.skipUnless(shutil.which("netconvert"), "requires netconvert")
class RepairLoopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = patch.object(config, "WORKSPACES_DIR", self.temp.name)
        self.workspace.start()
        self.addCleanup(self.workspace.stop)
        self.configured = patch.object(config, "MODEL_CONFIGURED", True)
        self.configured.start()
        self.addCleanup(self.configured.stop)

    def test_successful_exit_without_network_is_rejected(self):
        result = runner.run_generated_code('print("no map")')
        validation = runner.validate_network(result)
        self.assertFalse(validation["passed"])
        self.assertIn("缺少", validation["issues"][0])

    def test_stale_image_is_not_returned_if_network_validation_fails(self):
        code = (
            ROAD_CODE
            + """
with open("output/review.png", "wb") as f:
    f.write(b"not a rendered network")
with open("output/network.con.xml", "w") as f:
    f.write("<broken")
"""
        )
        result = runner.run_generated_code(code)
        validation = runner.validate_network(result)
        self.assertFalse(validation["passed"])
        self.assertNotIn("image", validation)

    def test_failed_execution_is_repaired_and_compiled(self):
        feedback_seen = []

        def generate(description, feedback):
            feedback_seen.append(list(feedback))
            return 'raise ValueError("broken road")' if len(feedback_seen) == 1 else ROAD_CODE

        with (
            patch.object(main.agent, "generate_code", side_effect=generate),
            patch.object(main.agent, "review_network", return_value={"passed": True, "issues": []}),
        ):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="two connected roads",
                    review_mode="image_and_facts",
                )
            )
        self.assertEqual(response["status"], "success")
        self.assertEqual(len(response["attempts"]), 2)
        self.assertIn("broken road", feedback_seen[1][0]["diagnostic"])
        self.assertIn("output/validated.net.xml", response["execution"]["artifacts"])
        self.assertIn("output/review.png", response["execution"]["artifacts"])
        self.assertEqual(response["validation"]["image"], "output/review.png")
        self.assertTrue(
            Path(
                self.temp.name, response["execution"]["task_id"], "output", "validated.net.xml"
            ).is_file()
        )

    def test_requirement_failure_triggers_retry_and_stops_at_limit(self):
        feedback_seen = []

        def generate(description, feedback):
            feedback_seen.append(list(feedback))
            return ROAD_CODE

        with (
            patch.object(main.agent, "generate_code", side_effect=generate),
            patch.object(
                main.agent,
                "review_network",
                return_value={"passed": False, "issues": ["缺少信号灯"]},
            ),
        ):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="road with traffic lights",
                    review_mode="image_and_facts",
                )
            )
        self.assertEqual(response["status"], "validation_error")
        self.assertEqual(len(response["attempts"]), config.MAX_GENERATION_ATTEMPTS)
        self.assertIn("缺少信号灯", feedback_seen[1][0]["diagnostic"])
        self.assertTrue(Path(feedback_seen[1][0]["image_path"]).is_file())

    def test_generated_process_does_not_inherit_api_key(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": "test-secret"}):
            result = runner.run_generated_code(
                'import os; print("present" if "MODEL_API_KEY" in os.environ else "absent")'
            )
        self.assertEqual(result["stdout"].strip(), "absent")

    def test_missing_model_is_error(self):
        with patch.object(config, "MODEL_CONFIGURED", False):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="crossroad",
                    review_mode="image_and_facts",
                )
            )
        self.assertEqual(response["status"], "agent_error")
        self.assertEqual(response["attempts"], [])

    def test_review_failure_preserves_attempt_and_does_not_report_success(self):
        with (
            patch.object(main.agent, "generate_code", return_value=ROAD_CODE),
            patch.object(main.agent, "review_network", side_effect=ValueError("invalid review")),
        ):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="road",
                    review_mode="image_and_facts",
                )
            )
        self.assertEqual(response["status"], "validation_error")
        self.assertEqual(len(response["attempts"]), 1)
        self.assertEqual(response["attempts"][0]["review_error"], "invalid review")

    def test_contradictory_model_review_is_rejected(self):
        image = Path(self.temp.name, "image.png")
        image.write_bytes(b"fake-png")
        message = SimpleNamespace(
            content='{"passed": true, "issues": [{"problem":"signal", "reason":"missing"}]}'
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with patch.object(agent, "OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            with self.assertRaisesRegex(ValueError, "自相矛盾"):
                agent.review_network("road", {}, str(image))

    def test_visual_review_receives_image_and_preserves_reason(self):
        image = Path(self.temp.name, "image.png")
        image.write_bytes(b"fake-png")
        message = SimpleNamespace(
            content='{"passed":false,"issues":[{"problem":"车道数不足","reason":"图中只有一条"}]}'
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.return_value = response
            review = agent.review_network("两条车道", {}, str(image))
        self.assertEqual(review["issue_details"][0]["reason"], "图中只有一条")
        self.assertIn("图中只有一条", review["issues"][0])
        content = create.call_args.kwargs["messages"][1]["content"]
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_only_review_sends_only_request_and_image(self):
        image = Path(self.temp.name, "image.png")
        image.write_bytes(b"fake-png")
        message = SimpleNamespace(
            content='{"passed":false,"issues":[{"problem":"缺灯","reason":"四个进口均无灯图标"}]}'
        )
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.return_value = response
            review = agent.review_network("必须有信号灯", None, str(image))
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 2)
        content = messages[1]["content"]
        self.assertEqual(len(content), 2)
        self.assertEqual(json.loads(content[0]["text"]), {"request": "必须有信号灯"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(review["evidence_mode"], "image_only")
        self.assertEqual(review["issue_details"][0]["reason"], "四个进口均无灯图标")

    def test_image_only_api_omits_facts_on_every_review_and_repairs_with_image(self):
        feedback_seen = []

        def generate(description, feedback):
            feedback_seen.append(list(feedback))
            return ROAD_CODE

        with (
            patch.object(main.agent, "generate_code", side_effect=generate),
            patch.object(
                main.agent,
                "review_network",
                side_effect=[
                    {"passed": False, "issues": ["缺灯：图中进口处没有信号灯图标"]},
                    {"passed": True, "issues": []},
                ],
            ) as review,
        ):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="必须有信号灯",
                    review_mode="image_only",
                )
            )
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["review_mode"], "image_only")
        self.assertEqual(review.call_count, 2)
        for call in review.call_args_list:
            self.assertIsNone(call.args[1])
            self.assertTrue(Path(call.args[2]).is_file())
        self.assertIn("图中进口处", feedback_seen[1][0]["diagnostic"])
        self.assertEqual(set(feedback_seen[1][0]), {"code", "diagnostic", "image_path"})

    def test_repair_model_receives_prior_image_and_reason(self):
        image = Path(self.temp.name, "image.png")
        image.write_bytes(b"fake-png")
        message = SimpleNamespace(content="print('fixed')")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        feedback = [
            {"code": "old code", "diagnostic": "车道不足：图中只有一条", "image_path": str(image)}
        ]
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.return_value = response
            agent.generate_code("两条车道", feedback)
        content = create.call_args.kwargs["messages"][-1]["content"]
        self.assertIn("图中只有一条", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_visual_review_rejects_problem_without_reason(self):
        image = Path(self.temp.name, "image.png")
        image.write_bytes(b"fake-png")
        message = SimpleNamespace(content='{"passed":false,"issues":[{"problem":"车道不足"}]}')
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with patch.object(agent, "OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            with self.assertRaisesRegex(ValueError, "problem 和 reason"):
                agent.review_network("road", {}, str(image))

    def test_missing_netconvert_stops_before_model_call(self):
        with (
            patch.object(main.shutil, "which", return_value=None),
            patch.object(main.agent, "generate_code") as generate,
        ):
            response = main.generate(
                main.GenerateRequest(
                    source_mode="synthetic",
                    output_kind="map",
                    description="road",
                    review_mode="image_and_facts",
                )
            )
        self.assertEqual(response["status"], "validation_error")
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
