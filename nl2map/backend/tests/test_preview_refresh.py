"""Check legacy preview repair without touching authored scenario or network inputs."""

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import preview_refresh
import renderer


class PreviewRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "20261105_075344_106fec" / "output"
        self.output.mkdir(parents=True)
        self.map_id = "20261105_075344_95696c"
        self.source = self.root / self.map_id / "output"
        self.source.mkdir(parents=True)
        self.metadata = {
            "source_mode": "real_map",
            "task_id": self.map_id,
            "place": {"name": "文化大学", "longitude": 117, "latitude": 0},
            "radius_m": 1000,
            "detail_level": "main_roads",
        }
        self.write_metadata()
        (self.output / "scenario.json").write_text('{"unchanged": true}', encoding="utf-8")
        net = ET.Element("net")
        ET.SubElement(
            net,
            "location",
            netOffset="-500000,0",
            projParameter="+proj=utm +zone=50 +datum=WGS84 +units=m +no_defs",
        )
        edge = ET.SubElement(net, "edge", id="42", name="文化路")
        ET.SubElement(edge, "lane", shape="-500,-200 500,-200", width="3.2")
        ET.ElementTree(net).write(self.output / "network.net.xml", encoding="utf-8")
        (self.source / "source.osm.xml").write_text(
            '<osm><way id="42"><tag k="name" v="文化路"/>'
            '<tag k="name:en" v="Culture Road"/></way></osm>',
            encoding="utf-8",
        )
        Image.new("RGB", (10, 10), "red").save(self.output / "map_preview.en.png")
        self.addCleanup(patch.stopall)
        patch.object(config, "WORKSPACES_DIR", self.root).start()

    def write_metadata(self):
        (self.output / "source_map_metadata.json").write_text(
            json.dumps(self.metadata, ensure_ascii=False), encoding="utf-8"
        )

    def test_legacy_english_image_is_repaired_without_changing_authored_files(self):
        immutable = {
            name: (self.output / name).read_bytes()
            for name in ("network.net.xml", "scenario.json", "source_map_metadata.json")
        }
        with patch.object(
            ImageDraw.ImageDraw, "text", autospec=True, side_effect=ImageDraw.ImageDraw.text
        ) as draw:
            path = preview_refresh.refreshed_preview(self.output, "map_preview.en.png")
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertTrue(labels)
        self.assertIn("Culture Road", labels)
        self.assertTrue(all(not any("\u3400" <= c <= "\u9fff" for c in label) for label in labels))
        self.assertEqual(path, self.output / "map_preview.en.png")
        with Image.open(path) as image:
            self.assertEqual(image.size, (1480, 1140))
        for name, original in immutable.items():
            self.assertEqual((self.output / name).read_bytes(), original)

    def test_cache_avoids_redraw_and_invalidates_when_source_names_change(self):
        with patch.object(
            renderer, "render_map_overview", wraps=renderer.render_map_overview
        ) as draw:
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")
            self.assertEqual(draw.call_count, 1)
            source = self.source / "source.osm.xml"
            source.write_text(source.read_text("utf-8").replace("Culture Road", "Culture Avenue"))
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")
            self.assertEqual(draw.call_count, 2)
        with (
            patch.object(preview_refresh, "_RENDERER_VERSION", "new-version"),
            patch.object(
                renderer, "render_map_overview", wraps=renderer.render_map_overview
            ) as draw,
        ):
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")
            self.assertEqual(draw.call_count, 1)

    def test_chinese_variant_preserves_names_and_generic_preview_is_untouched(self):
        generic = self.output / "map_preview.png"
        generic.write_bytes(b"existing default")
        with patch.object(
            ImageDraw.ImageDraw, "text", autospec=True, side_effect=ImageDraw.ImageDraw.text
        ) as draw:
            preview_refresh.refreshed_preview(self.output, "map_preview.zh.png")
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertIn("文化路", labels)
        self.assertIn("文化大学", labels)
        self.assertIsNone(preview_refresh.refreshed_preview(self.output, "map_preview.png"))
        self.assertEqual(generic.read_bytes(), b"existing default")

    def test_nonrealmap_and_unrelated_files_do_not_render(self):
        self.metadata["source_mode"] = "synthetic"
        self.write_metadata()
        with patch.object(renderer, "render_map_overview") as draw:
            self.assertIsNone(preview_refresh.refreshed_preview(self.output, "map_preview.en.png"))
            self.assertIsNone(preview_refresh.refreshed_preview(self.output, "network.net.xml"))
            self.assertIsNone(
                preview_refresh.refreshed_preview(self.output, "../map_preview.en.png")
            )
            draw.assert_not_called()

    def test_malformed_metadata_does_not_replace_original_preview(self):
        image = self.output / "map_preview.en.png"
        before = image.read_bytes()
        (self.output / "source_map_metadata.json").write_text("{bad json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata"):
            preview_refresh.refreshed_preview(self.output, image.name)
        self.assertEqual(image.read_bytes(), before)

    def test_invalid_source_task_id_is_rejected(self):
        self.metadata["task_id"] = "../../outside"
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "metadata"):
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")

    def test_escaping_source_symlink_is_rejected(self):
        source = self.source / "source.osm.xml"
        source.unlink()
        outside = self.root / "private.xml"
        outside.write_text("private data", encoding="utf-8")
        source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside"):
            preview_refresh.refreshed_preview(self.output, "map_preview.en.png")

    def test_render_failure_preserves_old_preview_and_leaves_no_temp_files(self):
        image = self.output / "map_preview.en.png"
        before = image.read_bytes()
        with (
            patch.object(renderer, "render_map_overview", side_effect=ValueError("render failed")),
            self.assertRaisesRegex(ValueError, "render failed"),
        ):
            preview_refresh.refreshed_preview(self.output, image.name)
        self.assertEqual(image.read_bytes(), before)
        self.assertEqual(list(self.output.glob(".preview-*")), [])


if __name__ == "__main__":
    unittest.main()
