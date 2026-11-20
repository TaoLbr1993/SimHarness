"""Exercise the draft edit API against a small, compiled SUMO junction."""

import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main
from fastapi.testclient import TestClient
from PIL import Image
from scenario_demand import build_demand, write_simulation_config
from scenario_models import ScenarioIntent, ScenarioResult, resolve_plan


@unittest.skipUnless(shutil.which("netconvert"), "requires SUMO netconvert")
class EditDraftTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.source_id = "20261115_120000_abcdef"
        self.output = self.workspace / self.source_id / "output"
        self.output.mkdir(parents=True)
        self.client = TestClient(main.app)
        for attr, value in (
            ("WORKSPACES_DIR", self.workspace),
            ("SCENARIO_CATALOG_DIR", self.workspace / "catalog"),
        ):
            p = patch.object(config, attr, value)
            p.start()
            self.addCleanup(p.stop)
        nodes = ET.Element("nodes")
        edges = ET.Element("edges")
        for name, x, y in (
            ("c", 0, 0),
            ("n", 0, 100),
            ("s", 0, -100),
            ("e", 100, 0),
            ("w", -100, 0),
        ):
            ET.SubElement(nodes, "node", id=name, x=str(x), y=str(y))
            if name != "c":
                for a, b in ((name, "c"), ("c", name)):
                    ET.SubElement(
                        edges,
                        "edge",
                        {"id": a + b, "from": a, "to": b, "priority": "3" if name in "ns" else "1"},
                    )
        for filename, tree in (("n.xml", nodes), ("e.xml", edges)):
            ET.ElementTree(tree).write(self.workspace / filename)
        network = self.output / "network.net.xml"
        subprocess.run(
            ["netconvert", "-n", "n.xml", "-e", "e.xml", "-o", str(network)],
            cwd=self.workspace,
            check=True,
            capture_output=True,
        )
        Image.new("RGB", (16, 16)).save(self.output / "map_preview.png")
        intent = ScenarioIntent(objective="test", vehicle_count=12, vehicle_count_quote="12")
        plan = resolve_plan(intent, config.SCENARIO)
        source = ScenarioResult(
            status="generated",
            task_id=self.source_id,
            description="12 vehicles",
            plan=plan,
            demand=build_demand(network, self.output, plan, config.SCENARIO),
        )
        write_simulation_config(self.output, plan, config.SCENARIO)
        (self.output / "scenario.json").write_text(source.model_dump_json(), encoding="utf-8")

    def apply(self, parent, instruction):
        response = self.client.post(
            f"/scenarios/{parent}/map-edits", json={"instruction": instruction}
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["parent_task_id"], parent)
        self.assertEqual(result["demand"]["planned_vehicles"], 12)
        return result, self.workspace / result["task_id"] / "output"

    def test_ordered_edits_display_only_and_publication(self):
        original = (self.output / "network.net.xml").read_bytes()
        added, first = self.apply(self.source_id, "增加东西路")
        ids = set(added["map_edits"][-1]["edge_ids"])
        self.assertTrue(ids)

        def roads(directory):
            return {
                e.get("id")
                for e in ET.parse(directory / "network.net.xml").findall("edge")
                if e.get("from")
            }

        self.assertEqual(roads(first), roads(self.output) | ids)
        view, second = self.apply(added["task_id"], "show only main roads")
        for name in ("network.net.xml", "traffic.rou.xml", "scenario.sumocfg"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        self.assertNotEqual(
            (first / "map_preview.png").read_bytes(), (second / "map_preview.png").read_bytes()
        )
        removed, third = self.apply(view["task_id"], "删除东西路")
        self.assertEqual(roads(third), roads(self.output))
        self.assertEqual(set(removed["map_edits"][-1]["edge_ids"]), ids)
        restored, final = self.apply(removed["task_id"], "显示全部道路")
        self.assertEqual(len(restored["map_edits"]), 4)
        self.assertEqual((self.output / "network.net.xml").read_bytes(), original)
        self.assertFalse((self.workspace / "catalog").exists())
        response = self.client.post(f"/scenarios/{restored['task_id']}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        published = self.workspace / "catalog" / response.json()["map_id"]
        self.assertEqual(
            (published / "network.net.xml").read_bytes(), (final / "network.net.xml").read_bytes()
        )

    def test_rejects_empty_or_ambiguous_without_deriving_a_draft(self):
        url = f"/scenarios/{self.source_id}/map-edits"
        self.assertEqual(self.client.post(url, json={"instruction": "  "}).status_code, 422)
        with patch(
            "agent.interpret_road_edit",
            return_value='{"operation":"clarify","reason":"Which direction?"}',
        ):
            response = self.client.post(url, json={"instruction": "Remove a road"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Which direction?", response.text)
        response = self.client.post(url, json={"instruction": "只显示一级道路"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("No roads of this class", response.text)
        self.assertEqual([p.name for p in self.workspace.iterdir() if p.is_dir()], [self.source_id])

    def test_remove_existing_axis_preserves_other_axis(self):
        from map_edits import interpret

        with patch(
            "agent.interpret_road_edit",
            return_value=('{"type":"json_object","operation":"show_secondary","reason":""}'),
        ):
            self.assertEqual(interpret("Keep just the secondary roads on screen"), "show_secondary")
        result, output = self.apply(self.source_id, "remove the east-west road")
        self.assertEqual(set(result["map_edits"][-1]["edge_ids"]), {"cw", "wc", "ce", "ec"})
        self.assertEqual(
            {
                e.get("id")
                for e in ET.parse(output / "network.net.xml").findall("edge")
                if e.get("from")
            },
            {"cn", "nc", "cs", "sc"},
        )
