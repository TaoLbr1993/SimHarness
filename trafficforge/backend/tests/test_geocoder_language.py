"""Offline checks that place text, not UI language or translated queries, selects a provider."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amap_source
import config
import main
import map_sources
import realmap
from realmap_models import GeocodingResult, MapIntent, SourceRecord


class GeocoderLanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.empty = GeocodingResult([], [])
        for name, value in (
            ("WORKSPACES_DIR", temporary.name),
            ("AMAP_WEB_API_KEY", "fake-key-for-language-tests"),
            ("REAL_MAP", config.REAL_MAP.model_copy(update={"geocoder_provider": "auto"})),
        ):
            change = patch.object(config, name, value)
            change.start()
            self.addCleanup(change.stop)

    def empty_response(self) -> map_sources.SourceResponse:
        return map_sources.SourceResponse(
            json.dumps({"type": "FeatureCollection", "features": []}).encode(),
            SourceRecord(
                url="https://example.invalid/api", query="q", fetched_at="now", cache_hit=False
            ),
        )

    def test_auto_dispatches_by_place_text_even_with_amap_key(self) -> None:
        cases = (
            ("Stanford University, California, USA", "photon"),
            ("Tsinghua University, Beijing", "photon"),
            ("北京航空航天大学", "amap"),
            ("美国斯坦福大学", "amap"),
            ("SOHO望京", "amap"),
            ("München Hauptbahnhof", "photon"),
        )
        for place, expected in cases:
            with (
                self.subTest(place=place),
                patch.object(amap_source, "geocode", return_value=self.empty) as amap,
                patch.object(map_sources, "_photon_geocode", return_value=self.empty) as photon,
            ):
                self.assertIs(map_sources.geocode(place, "", self.root), self.empty)
                self.assertEqual(amap.call_count, int(expected == "amap"))
                self.assertEqual(photon.call_count, int(expected == "photon"))

    def test_health_reports_auto_routing_instead_of_a_global_provider(self) -> None:
        self.assertEqual(main.health()["geocoder_provider"], "auto")

    def test_explicit_provider_configuration_still_overrides_language(self) -> None:
        for provider, place in (("amap", "Stanford University"), ("photon", "清华大学")):
            with (
                self.subTest(provider=provider),
                patch.object(
                    config,
                    "REAL_MAP",
                    config.REAL_MAP.model_copy(update={"geocoder_provider": provider}),
                ),
                patch.object(amap_source, "geocode", return_value=self.empty) as amap,
                patch.object(map_sources, "_photon_geocode", return_value=self.empty) as photon,
            ):
                map_sources.geocode(place, "", self.root, location_quote=place)
                self.assertEqual(map_sources.geocoder_provider(), provider)
                self.assertEqual(amap.call_count, int(provider == "amap"))
                self.assertEqual(photon.call_count, int(provider == "photon"))

    def test_english_original_uses_english_alternate_before_translated_primary(self) -> None:
        english = "Stanford University, California"
        with (
            patch.object(map_sources, "_fetch", return_value=self.empty_response()) as fetch,
            patch.object(amap_source, "geocode") as amap,
        ):
            map_sources.geocode("加州斯坦福大学", english, self.root, location_quote=english)
        self.assertEqual(parse_qs(fetch.call_args_list[0].args[1])["q"], [english])
        amap.assert_not_called()

    def test_english_original_is_used_when_both_model_queries_were_translated(self) -> None:
        english = "Tsinghua University, Beijing"
        with (
            patch.object(map_sources, "_fetch", return_value=self.empty_response()) as fetch,
            patch.object(amap_source, "geocode") as amap,
        ):
            map_sources.geocode("北京清华大学", "清华大学", self.root, location_quote=english)
        self.assertEqual(parse_qs(fetch.call_args_list[0].args[1])["q"], [english])
        amap.assert_not_called()

    def test_chinese_original_stays_on_amap_after_model_translation(self) -> None:
        with (
            patch.object(amap_source, "geocode", return_value=self.empty) as amap,
            patch.object(map_sources, "_fetch") as photon,
        ):
            map_sources.geocode(
                "Beihang University", "BUAA", self.root, location_quote="北航学院路校区"
            )
        self.assertEqual(amap.call_args.args[0], "北航学院路校区")
        photon.assert_not_called()

    def test_whitespace_quote_uses_the_primary_query_language(self) -> None:
        with (
            patch.object(amap_source, "geocode", return_value=self.empty) as amap,
            patch.object(map_sources, "_fetch", return_value=self.empty_response()) as photon,
        ):
            map_sources.geocode("清华大学", "Tsinghua University", self.root, location_quote="  ")
        amap.assert_called_once()
        photon.assert_not_called()

    def test_chinese_without_key_fails_instead_of_silently_using_photon(self) -> None:
        with (
            patch.object(config, "AMAP_WEB_API_KEY", ""),
            patch.object(amap_source, "urlopen") as amap,
            patch.object(map_sources, "_fetch") as photon,
            self.assertRaisesRegex(ValueError, "AMAP_WEB_API_KEY"),
        ):
            map_sources.geocode("清华大学", "Tsinghua University", self.root)
        amap.assert_not_called()
        photon.assert_not_called()

    def test_english_without_any_amap_key_can_still_query_photon(self) -> None:
        with (
            patch.object(config, "AMAP_WEB_API_KEY", ""),
            patch.object(map_sources, "_fetch", return_value=self.empty_response()) as fetch,
            patch.object(amap_source, "geocode") as amap,
        ):
            result = map_sources.geocode("Stanford University", "", self.root)
        self.assertEqual(result.candidates, [])
        fetch.assert_called_once()
        amap.assert_not_called()

    def test_selected_provider_failure_does_not_switch_services(self) -> None:
        for place, provider in (("清华大学", "amap"), ("Stanford University", "photon")):
            with (
                self.subTest(provider=provider),
                patch.object(
                    amap_source, "geocode", side_effect=ValueError("amap failure")
                ) as amap,
                patch.object(
                    map_sources, "_photon_geocode", side_effect=ValueError("photon failure")
                ) as photon,
                self.assertRaisesRegex(ValueError, provider + " failure"),
            ):
                map_sources.geocode(place, "", self.root)
            self.assertEqual(amap.call_count, int(provider == "amap"))
            self.assertEqual(photon.call_count, int(provider == "photon"))

    def test_realmap_uses_english_place_with_chinese_instruction_in_both_ui_languages(self) -> None:
        english = "Stanford University, California"
        intent = MapIntent(
            source_mode="real_map",
            place_query="加州斯坦福大学",
            alternate_query=english,
            location_quote=english,
            reason="test",
        )
        for language in ("en", "zh"):
            with (
                self.subTest(ui_language=language),
                patch.object(map_sources, "_fetch", return_value=self.empty_response()) as fetch,
                patch.object(amap_source, "geocode") as amap,
                patch.object(realmap.agent, "choose_map_place") as choose,
            ):
                result = realmap.generate_real_map(
                    f"请生成 {english} 附近的路网", intent, ui_language=language
                )
            self.assertEqual(result.status, "needs_clarification")
            self.assertEqual(parse_qs(fetch.call_args_list[0].args[1])["q"], [english])
            amap.assert_not_called()
            choose.assert_not_called()

    def test_realmap_uses_chinese_place_with_english_instruction_and_ui(self) -> None:
        intent = MapIntent(
            source_mode="real_map",
            place_query="Beihang University",
            alternate_query="BUAA",
            location_quote="北航学院路校区",
            reason="test",
        )
        with (
            patch.object(amap_source, "geocode", return_value=self.empty) as amap,
            patch.object(map_sources, "_fetch") as photon,
        ):
            result = realmap.generate_real_map(
                "Generate roads around 北航学院路校区", intent, ui_language="en"
            )
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(amap.call_args.args[0], intent.location_quote)
        photon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
