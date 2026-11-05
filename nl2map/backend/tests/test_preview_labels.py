"""Source English is preferred; offline fallback never writes through to map data."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from preview_labels import english_name, osm_english_names


class PreviewLabelTest(unittest.TestCase):
    def test_mixed_names_empty_names_and_existing_english(self):
        for source, expected in (
            ("", ""),
            ("  Main Street  ", "Main Street"),
            ("SOHO望京（南门）", "SOHO Wang Jing (Nan Men)"),
            ("学院路校区", "Xue Yuan Road Campus"),
            ("人民大道", "Ren Min Avenue"),
            ("文化大街", "Wen Hua Street"),
        ):
            with self.subTest(source=source):
                self.assertEqual(english_name(source), expected)

    def test_source_names_preferred_but_not_when_they_contain_chinese(self):
        self.assertEqual(english_name("学院路", {"学院路": "Xueyuan Road"}), "Xueyuan Road")
        self.assertEqual(english_name("学院路", {"学院路": "学院Road"}), "Xue Yuan Road")
        self.assertEqual(
            english_name(
                "北京航空航天大学（学院路校区）",
                {"北京航空航天大学": "Beihang University", "学院路": "Xueyuan Road"},
            ),
            "Beihang University (Xueyuan Road Campus)",
        )

    def test_rare_unmapped_ideograph_is_not_rendered_as_chinese(self):
        result = english_name("\U000323af路")
        self.assertNotIn("\U000323af", result)
        self.assertTrue(result.endswith(" Road"))

    def test_all_osm_element_types_and_zh_aliases_are_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.osm.xml"
            source.write_text(
                '<osm><node id="1"><tag k="name" v="学校"/>'
                '<tag k="name:en" v="School"/></node>'
                '<way id="2"><tag k="name" v="Main Road"/><tag k="name:zh" v="主路"/>'
                '<tag k="name:en" v="Main Road"/></way>'
                '<relation id="3"><tag k="name" v="大学"/>'
                '<tag k="name:en" v="University"/></relation></osm>',
                encoding="utf-8",
            )
            self.assertEqual(
                osm_english_names(source),
                {
                    "学校": "School",
                    "Main Road": "Main Road",
                    "主路": "Main Road",
                    "大学": "University",
                },
            )
        self.assertEqual(osm_english_names(None), {})

    def test_conflicting_source_names_and_invalid_english_fall_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.osm.xml"
            source.write_text(
                '<osm><way id="1"><tag k="name" v="人民路"/>'
                '<tag k="name:en" v="People Road"/></way>'
                '<way id="2"><tag k="name" v="人民路"/>'
                '<tag k="name:en" v="Renmin Road"/></way>'
                '<node id="3"><tag k="name" v="大学"/>'
                '<tag k="name:en" v="中文University"/></node></osm>',
                encoding="utf-8",
            )
            self.assertEqual(osm_english_names(source), {})


if __name__ == "__main__":
    unittest.main()
