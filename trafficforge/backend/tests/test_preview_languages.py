"""Verify preview display names follow language without modifying source map inputs."""

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from PIL import Image, ImageDraw
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import main
import renderer
import scenario_catalog
import scenario_pipeline
import scenario_revision
from preview_location import MapOriginMarker
from realmap_models import MapIntent
from scenario_models import ScenarioIntent, ScenarioRevision
from test_scenario import network_fixture


class PreviewLanguageTest(unittest.TestCase):
    def test_api_defaults_to_english_and_rejects_unknown_language(self):
        self.assertEqual(main.GenerateRequest(description="test").ui_language, "en")
        self.assertEqual(
            main.GenerateRequest(description="test", ui_language="zh").ui_language, "zh"
        )
        with self.assertRaises(ValidationError):
            main.GenerateRequest(description="test", ui_language="xx")

    def test_rendered_names_and_annotations_follow_language_without_changing_map(self):
        root = network_fixture()
        root.find("edge").set("name", "文化北路")
        root.find("edge/lane").set("shape", "0,0 50,0 100,0")
        ET.SubElement(
            root,
            "location",
            netOffset="-500000,0",
            projParameter="+proj=utm +zone=50 +datum=WGS84 +units=m +no_defs",
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            network = directory / "network.net.xml"
            ET.ElementTree(root).write(network, encoding="utf-8")
            before = network.read_bytes()
            language: Literal["en", "zh"]
            for language in ("en", "zh"):
                with patch.object(
                    ImageDraw.ImageDraw,
                    "text",
                    autospec=True,
                    side_effect=ImageDraw.ImageDraw.text,
                ) as draw_text:
                    renderer.render_map_overview(
                        str(network),
                        str(directory / f"preview.{language}.png"),
                        title="茂业天地(莱芜店)",
                        radius_m=1000,
                        origin=MapOriginMarker("茂业天地(莱芜店)", 117, 0),
                        ui_language=language,
                    )
                labels = [call.args[2] for call in draw_text.call_args_list]
                self.assertIn("Original location" if language == "en" else "原始位置", labels)
                self.assertIn(
                    "Mao Ye Tian Di (Lai Wu Store) · Main-road network"
                    if language == "en"
                    else "茂业天地(莱芜店) · 主干道路网",
                    labels,
                )
                if language == "en":
                    self.assertTrue(any("1 km radius" in label for label in labels))
                    self.assertIn("Wen Hua Bei Road", labels)
                    self.assertFalse(
                        any("\u4e00" <= char <= "\u9fff" for label in labels for char in label)
                    )
                else:
                    self.assertIn("茂业天地(莱芜店)", labels)
                    self.assertIn("文化北路", labels)
            self.assertEqual(network.read_bytes(), before)
            self.assertNotEqual(
                (directory / "preview.en.png").read_bytes(),
                (directory / "preview.zh.png").read_bytes(),
            )

    def test_source_english_names_are_used_for_place_and_street(self):
        root = network_fixture()
        root.find("edge").set("name", "学院路")
        root.find("edge/lane").set("shape", "0,0 50,0 100,0")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            network = directory / "network.net.xml"
            source = directory / "source.osm.xml"
            ET.ElementTree(root).write(network, encoding="utf-8")
            source.write_text(
                '<osm><way id="1"><tag k="name" v="学院路"/>'
                '<tag k="name:en" v="Xueyuan Road"/></way>'
                '<node id="2"><tag k="name" v="北京航空航天大学"/>'
                '<tag k="name:en" v="Beihang University"/></node></osm>',
                encoding="utf-8",
            )
            before = source.read_bytes(), network.read_bytes()
            with patch.object(
                ImageDraw.ImageDraw,
                "text",
                autospec=True,
                side_effect=ImageDraw.ImageDraw.text,
            ) as draw_text:
                renderer.render_map_overview(
                    str(network),
                    str(directory / "preview.en.png"),
                    title="北京航空航天大学",
                    radius_m=1000,
                    source_osm_path=str(source),
                )
            labels = [call.args[2] for call in draw_text.call_args_list]
            self.assertIn("Beihang University · Main-road network", labels)
            self.assertIn("Xueyuan Road", labels)
            self.assertEqual((source.read_bytes(), network.read_bytes()), before)

    def test_long_english_heading_fits_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            network = directory / "network.net.xml"
            ET.ElementTree(network_fixture()).write(network, encoding="utf-8")
            with patch.object(
                ImageDraw.ImageDraw,
                "text",
                autospec=True,
                side_effect=ImageDraw.ImageDraw.text,
            ) as draw_text:
                renderer.render_map_overview(
                    str(network),
                    str(directory / "preview.png"),
                    title="交通大学" * 40,
                    radius_m=1000,
                )
            heading = draw_text.call_args_list[0]
            width = heading.args[0].textlength(heading.args[2], font=heading.kwargs["font"])
            self.assertLessEqual(width, 1390)
            self.assertTrue(heading.args[2].endswith("…"))

    def test_existing_variants_survive_generation_revision_and_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source_task = "20260101_000000_aabbcc"
            source = workspace / source_task / "output"
            source.mkdir(parents=True)
            ET.ElementTree(network_fixture()).write(source / "validated.net.xml")
            for language, color in (("en", "white"), ("zh", "red")):
                Image.new("RGB", (10, 10), color).save(source / f"map_preview.{language}.png")
            expected = {
                language: (source / f"map_preview.{language}.png").read_bytes()
                for language in ("en", "zh")
            }
            intent = MapIntent(
                source_mode="synthetic", reason="test", scenario=ScenarioIntent(objective="test")
            )
            with (
                patch.object(config, "WORKSPACES_DIR", workspace),
                patch.object(config, "SCENARIO_CATALOG_DIR", workspace / "catalog"),
                patch.object(scenario_pipeline.subprocess, "run") as simulator,
            ):
                draft = scenario_pipeline.generate_scenario(
                    "test", intent, map_task_id=source_task, ui_language="zh"
                )
                self.assertEqual(draft.status, "generated", draft.error)
                output = workspace / draft.task_id / "output"
                self.assertEqual((output / "map_preview.png").read_bytes(), expected["zh"])
                revised = scenario_revision.revise_scenario(
                    draft.task_id, ScenarioRevision(vehicle_count=25)
                )
                revised_output = workspace / revised.task_id / "output"
                scenario_catalog.publish_scenario(revised, revised_output)
                for language in ("en", "zh"):
                    filename = f"map_preview.{language}.png"
                    self.assertEqual((revised_output / filename).read_bytes(), expected[language])
                    self.assertIn(f"output/{filename}", revised.artifact_urls)
                    self.assertEqual(
                        scenario_catalog.scenario_file(revised.task_id, filename).read_bytes(),
                        expected[language],
                    )
                simulator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
