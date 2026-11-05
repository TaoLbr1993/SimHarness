"""Offline tests for source fidelity, failures and auto routing; no public-service calls."""

import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import config
import main
import map_sources
import realmap
from fastapi import HTTPException
from map_sources import GeocodingResult
from realmap_models import MapIntent, PlaceCandidate, PlaceChoice
from review_models import VisualReview
from test_xml_rules import crossroad


class RealMapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(config, "WORKSPACES_DIR", self.temp.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        provider = patch.object(
            config, "REAL_MAP", config.REAL_MAP.model_copy(update={"geocoder_provider": "photon"})
        )
        provider.start()
        self.addCleanup(provider.stop)
        self.intent = MapIntent(
            source_mode="real_map",
            place_query="北航学院路校区",
            location_quote="北航学院路校区",
            radius_m=1000,
            radius_quote="1 公里",
            reason="指定真实地点",
        )
        self.place = PlaceCandidate(
            id="R123", name="北航学院路校区", longitude=116.34, latitude=39.98
        )
        self.geo = GeocodingResult([self.place], [])
        self.choice = PlaceChoice(
            selected_id=self.place.id, needs_clarification=False, reason="校区匹配"
        )

    def fake_response(self, content):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))]
        )

    def test_intent_uses_quote_and_cannot_invent_coordinates(self):
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.return_value = self.fake_response(self.intent.model_dump())
            self.assertEqual(agent.interpret_map_request("北航学院路校区，1 公里").radius_m, 1000)
            with self.assertRaises(ValueError):
                agent.interpret_map_request("另一个地方")
            create.return_value = self.fake_response({**self.intent.model_dump(), "latitude": 40})
            with self.assertRaises(ValueError):
                agent.interpret_map_request("北航学院路校区，1 公里")

    def test_choice_rejects_invented_id(self):
        with patch.object(agent, "OpenAI") as client:
            client.return_value.chat.completions.create.return_value = self.fake_response(
                {"selected_id": "W999", "needs_clarification": False, "reason": "invented"}
            )
            with self.assertRaisesRegex(ValueError, "不存在"):
                agent.choose_map_place("北航", [self.place])

    def test_main_roads_is_default_and_language_detail_requires_user_quote(self):
        self.assertEqual(self.intent.detail_level, "main_roads")
        self.intent.detail_level = "detailed"
        with self.assertRaises(ValueError):
            self.intent.validate_sources("北航学院路校区，1 公里")
        self.intent.detail_quote = "包含支路"
        self.intent.validate_sources("北航学院路校区，1 公里，包含支路")

    def test_intent_retries_one_bad_quote_without_silently_accepting_it(self):
        bad = self.intent.model_dump()
        bad.update(detail_level="detailed", detail_quote="把支路也都加上")
        good = {**bad, "detail_quote": "包含支路"}
        with patch.object(agent, "OpenAI") as client:
            create = client.return_value.chat.completions.create
            create.side_effect = [self.fake_response(bad), self.fake_response(good)]
            result = agent.interpret_map_request("北航学院路校区，1 公里，包含支路")
        self.assertEqual(result.detail_level, "detailed")
        self.assertEqual(create.call_count, 2)

    def test_ambiguous_place_does_not_download_or_generate_fake_roads(self):
        choice = PlaceChoice(
            selected_id=None, needs_clarification=True, reason="请指定学院路或沙河校区"
        )
        with (
            patch.object(map_sources, "geocode", return_value=self.geo),
            patch.object(agent, "choose_map_place", return_value=choice),
            patch.object(map_sources, "fetch_roads") as download,
            patch.object(agent, "generate_code") as generate,
        ):
            result = realmap.generate_real_map("北航", self.intent)
        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("沙河", result.error)
        download.assert_not_called()
        generate.assert_not_called()
        self.assertTrue((self.root / result.task_id / "output/metadata.json").is_file())

    def test_radius_is_rejected_not_clamped(self):
        self.intent.radius_m = 10000
        with patch.object(map_sources, "geocode") as geocode:
            result = realmap.generate_real_map("北航 10 公里", self.intent)
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(result.radius_m, 10000)
        geocode.assert_not_called()

    def test_provider_failure_preserves_reason_without_synthetic_fallback(self):
        with (
            patch.object(map_sources, "geocode", return_value=self.geo),
            patch.object(agent, "choose_map_place", return_value=self.choice),
            patch.object(map_sources, "fetch_roads", side_effect=ValueError("HTTP 429")),
            patch.object(agent, "generate_code") as generate,
        ):
            result = realmap.generate_real_map("北航学院路校区", self.intent)
        self.assertEqual(result.status, "source_error")
        self.assertIn("429", result.error)
        generate.assert_not_called()

    def test_explicit_place_must_be_current_candidate(self):
        with (
            patch.object(map_sources, "geocode", return_value=self.geo),
            patch.object(map_sources, "fetch_roads") as download,
        ):
            result = realmap.generate_real_map("北航", self.intent, "W999")
        self.assertEqual(result.status, "needs_clarification")
        download.assert_not_called()

    def test_coordinate_note_is_retained_once_even_if_geocoder_already_reported_it(self):
        note = "GCJ-02 坐标已近似转换至 WGS84。"
        self.place.coordinate_note = note
        for upstream_warnings in ([], [note]):
            with (
                self.subTest(upstream_warnings=upstream_warnings),
                patch.object(
                    map_sources,
                    "geocode",
                    return_value=GeocodingResult([self.place], [], upstream_warnings),
                ),
                patch.object(agent, "choose_map_place", return_value=self.choice),
                patch.object(map_sources, "fetch_roads", side_effect=ValueError("HTTP 429")),
            ):
                result = realmap.generate_real_map("北航学院路校区", self.intent)
                self.assertEqual(result.place, self.place)
                self.assertEqual(result.warnings.count(note), 1)

    def test_overpass_query_preserves_restrictions_and_coordinate_order(self):
        body = b'<osm><way id="1"><tag k="highway" v="residential"/></way></osm>'
        record = map_sources.SourceRecord(
            url="provider", query="q", fetched_at="now", cache_hit=False
        )
        with patch.object(
            map_sources, "_fetch", return_value=map_sources.SourceResponse(body, record)
        ) as fetch:
            map_sources.fetch_roads(self.place, 1000, self.root)
        query = fetch.call_args.args[1]
        self.assertIn("1000,39.9800000,116.3400000", query)
        self.assertIn('rel(bw.roads)["type"="restriction"]', query)
        self.assertEqual((self.root / "source.osm.xml").read_bytes(), body)

    def test_cache_reuses_response_and_rate_limit_is_not_retried(self):
        body = b'{"type":"FeatureCollection","features":[]}'
        with (
            patch.object(map_sources, "urlopen") as remote,
            patch.object(map_sources.time, "sleep"),
        ):
            remote.return_value.__enter__.return_value.read.return_value = body
            first = map_sources._fetch("https://provider.test/api", "q=test", post=False)
            second = map_sources._fetch("https://provider.test/api", "q=test", post=False)
            self.assertFalse(first.record.cache_hit)
            self.assertTrue(second.record.cache_hit)
            self.assertEqual(remote.call_count, 1)
            remote.side_effect = HTTPError("https://provider.test/api", 429, "limited", {}, None)
            with self.assertRaisesRegex(ValueError, "429"):
                map_sources._fetch("https://provider.test/api", "q=other", post=False)
            self.assertEqual(remote.call_count, 2)

    def geocoder_response(self, names):
        features = [
            {
                "geometry": {"type": "Point", "coordinates": [116.34, 39.98]},
                "properties": {"osm_type": kind, "osm_id": number, "name": name},
            }
            for kind, number, name in names
        ]
        return map_sources.SourceResponse(
            json.dumps({"type": "FeatureCollection", "features": features}).encode(),
            map_sources.SourceRecord(url="provider", query="q", fetched_at="now", cache_hit=False),
        )

    def test_unrelated_nonempty_hits_do_not_block_alternate_campus_lookup(self):
        primary = self.geocoder_response([("W", 1, "北航柏彦大厦")])
        alternate = self.geocoder_response(
            [
                ("W", 1, "Boyan Building"),
                ("R", 2, "北京航空航天大学(学院路校区)"),
            ]
        )
        with patch.object(map_sources, "_fetch", side_effect=[primary, alternate]) as fetch:
            result = map_sources.geocode("北航", "Beihang University", self.root)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([place.id for place in result.candidates], ["W1", "R2"])
        self.assertEqual(result.candidates[0].name, "北航柏彦大厦")
        self.assertEqual(len(result.records), 2)
        self.assertTrue((self.root / "geocoder-2.json").is_file())

    def test_identical_queries_are_not_repeated_and_candidate_count_is_bounded(self):
        response = self.geocoder_response([("W", index, str(index)) for index in range(8)])
        with patch.object(map_sources, "_fetch", return_value=response) as fetch:
            result = map_sources.geocode(" 北航 ", "北航", self.root)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(result.candidates), 5)

    def test_alternate_failure_keeps_primary_evidence_and_reports_reason(self):
        primary = self.geocoder_response([("R", 2, "北京航空航天大学(学院路校区)")])
        with patch.object(
            map_sources, "_fetch", side_effect=[primary, ValueError("HTTP 429")]
        ) as fetch:
            result = map_sources.geocode("北京航空航天大学", "Beihang University", self.root)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([place.id for place in result.candidates], ["R2"])
        self.assertEqual(len(result.records), 1)
        self.assertIn("429", result.warnings[0])
        self.assertIn("429", (self.root / "geocoder-2-error.json").read_text(encoding="utf-8"))

    def test_primary_rate_limit_does_not_trigger_second_provider_query(self):
        with (
            patch.object(map_sources, "_fetch", side_effect=ValueError("HTTP 429")) as fetch,
            self.assertRaisesRegex(ValueError, "429"),
        ):
            map_sources.geocode("北航", "Beihang University", self.root)
        self.assertEqual(fetch.call_count, 1)

    def test_large_network_is_not_truncated_and_invalid_late_reference_fails(self):
        root = crossroad()
        for lane in root.iter("lane"):
            lane.set("shape", "0,0 100,100")
        ET.SubElement(root, "location", convBoundary="0,0,100,100", projParameter="test")
        for index in range(100):
            edge = ET.SubElement(
                root, "edge", {"id": f"extra{index}", "from": "north", "to": "center"}
            )
            ET.SubElement(edge, "lane", index="0", id=f"extra{index}_0", shape="0,0 100,100")
        ET.SubElement(
            root,
            "connection",
            {"from": "extra99", "to": "east_out", "fromLane": "9", "toLane": "0"},
        )
        ET.ElementTree(root).write(self.root / "validated.net.xml")
        (self.root / "source.osm.xml").write_text("<osm/>")
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", return_value=completed),
        ):
            checked = realmap.compile_network(self.root, self.place, 1000)
        self.assertEqual(checked.statistics.directed_edges, 108)
        self.assertFalse(checked.passed)
        self.assertIn("extra99", str(checked.xml_review.model_dump()))
        self.assertIsNone(checked.sumo_exit_code)
        self.assertEqual(checked.statistics.converted_xy_bounds, "0.00,0.00,100.00,100.00")

    def test_selection_polygon_has_expected_radius_and_coordinate_order(self):
        import math

        values = list(map(float, realmap._selection_polygon(self.place, 1000).split(",")))
        self.assertEqual(len(values), 72)
        for lon, lat in zip(values[0::2], values[1::2], strict=True):
            dy = math.radians(lat - self.place.latitude) * 6371008.8
            dx = (
                math.radians(lon - self.place.longitude)
                * 6371008.8
                * math.cos(math.radians(self.place.latitude))
            )
            self.assertAlmostEqual(math.hypot(dx, dy), 1000, delta=0.2)

    def test_second_conversion_failure_keeps_both_pass_logs(self):
        good = SimpleNamespace(returncode=0, stdout="Success", stderr="")
        bad = SimpleNamespace(returncode=1, stdout="", stderr="trim failed")
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", side_effect=[good, good, bad]),
            self.assertRaisesRegex(ValueError, "trim failed"),
        ):
            realmap.compile_network(self.root, self.place, 1000)
        log = (self.root / "netconvert.log").read_text()
        self.assertIn("PASS 1", log)
        self.assertIn("PASS 2", log)
        commands = json.loads((self.root / "conversion-command.json").read_text())
        self.assertIn("--geometry.split", commands[0])
        self.assertIn("--keep-edges.by-type", commands[0])
        self.assertIn("--sumo-net-file", commands[1])

    def test_detailed_import_does_not_filter_road_classes(self):
        good = SimpleNamespace(returncode=0, stdout="Success", stderr="")
        bad = SimpleNamespace(returncode=1, stdout="", stderr="stop after command capture")
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", side_effect=[good, bad]),
            self.assertRaises(ValueError),
        ):
            realmap.compile_network(self.root, self.place, 1000, "detailed")
        commands = json.loads((self.root / "conversion-command.json").read_text())
        self.assertNotIn("--keep-edges.by-type", commands[0])

    def test_timeout_retains_partial_output_and_previous_conversion_pass(self):
        good = SimpleNamespace(returncode=0, stdout="Success", stderr="previous warning")
        timeout = subprocess.TimeoutExpired(
            ["netconvert"], 90, output=b"partial stdout", stderr=b"partial stderr"
        )
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", side_effect=[good, good, timeout]),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            realmap.compile_network(self.root, self.place, 1000)
        log = (self.root / "netconvert.log").read_text()
        for expected in (
            "PASS 1",
            "previous warning",
            "PASS 2",
            "TIMEOUT",
            "partial stdout",
            "partial stderr",
        ):
            self.assertIn(expected, log)

    def test_import_issue_reason_does_not_claim_unrelated_previous_warning(self):
        network = crossroad()
        ET.SubElement(network, "location", convBoundary="0,0,100,100", projParameter="test")
        for lane in network.iter("lane"):
            lane.set("shape", "0,0 100,100")
        ET.ElementTree(network).write(self.root / "validated.net.xml")
        (self.root / "source.osm.xml").write_text("<osm/>")
        good = SimpleNamespace(returncode=0, stdout="Success", stderr="")
        warning = SimpleNamespace(
            returncode=0,
            stdout="Success",
            stderr=(
                "Warning: direction of restriction relation could not be determined\n"
                "Warning: Ignoring restriction relation '1'.\n"
                "Warning: unrelated speed warning\n"
                "Warning: The traffic light '2' does not control any links; it will not be build.\n"
            ),
        )
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", side_effect=[good, warning, good, good]),
        ):
            result = realmap.compile_network(self.root, self.place, 1000)
        self.assertTrue(result.passed)
        self.assertIn("direction of restriction", result.import_issues[0])
        self.assertNotIn("unrelated speed", result.import_issues[1])

    def test_import_warnings_are_retained_for_user_judgment_without_visual_gate(self):
        self.place.provider = "amap"
        self.place.source_crs = "GCJ-02"
        self.place.source_longitude = 116.346
        self.place.source_latitude = 39.982
        record = map_sources.SourceRecord(
            url="provider", query="q", fetched_at="now", cache_hit=False
        )

        def compile_fake(output, place, radius, detail_level):
            (output / "netconvert.log").write_text("Success")
            return realmap.ImportValidation(
                passed=True, sumo_exit_code=0, import_issues=["Ignoring restriction relation 1"]
            )

        with (
            patch.object(map_sources, "geocode", return_value=self.geo),
            patch.object(agent, "choose_map_place", return_value=self.choice),
            patch.object(map_sources, "fetch_roads", return_value=record),
            patch.object(realmap, "compile_network", side_effect=compile_fake),
            patch.object(realmap.renderer, "render_network"),
            patch.object(
                realmap.renderer,
                "render_map_overview",
                side_effect=lambda network, target, **kwargs: Path(target).write_bytes(
                    kwargs["ui_language"].encode()
                ),
            ) as overview,
            patch.object(
                agent,
                "review_geometry",
                side_effect=AssertionError("真实地图由用户判断，不调用视觉审核"),
            ) as visual,
        ):
            first = realmap.generate_real_map("北航", self.intent)
        self.assertEqual(first.status, "success")
        self.assertEqual(first.user_review, "pending")
        self.assertEqual(first.review_mode, "xml_and_user")
        self.assertTrue(first.validation.import_issues)
        self.assertIsNone(first.validation.visual_review)
        visual.assert_not_called()
        self.assertEqual(first.detail_level, "main_roads")
        origin = overview.call_args.kwargs["origin"]
        self.assertEqual(origin.name, self.place.name)
        self.assertEqual((origin.longitude, origin.latitude), (116.34, 39.98))
        self.assertEqual(
            {call.kwargs["ui_language"] for call in overview.call_args_list}, {"en", "zh"}
        )
        output = self.root / first.task_id / "output"
        self.assertEqual((output / "map_preview.png").read_bytes(), b"en")
        for language in ("en", "zh"):
            self.assertIn(f"output/map_preview.{language}.png", first.artifact_urls)

    def test_artifact_download_can_serve_generated_network(self):
        output = self.root / "20260101_000000_aabbcc" / "output"
        output.mkdir(parents=True)
        (output / "validated.net.xml").write_text("<net/>")
        response = main.artifact("20260101_000000_aabbcc", "validated.net.xml")
        self.assertEqual(Path(response.path), output / "validated.net.xml")

    def test_extra_requirement_retains_import_but_is_not_claimed_passed(self):
        self.intent.extra_requirements = ["四个方向都有信号灯"]
        record = map_sources.SourceRecord(
            url="provider", query="q", fetched_at="now", cache_hit=False
        )

        def compile_fake(output, place, radius, detail_level):
            (output / "netconvert.log").write_text("Success")
            return realmap.ImportValidation(passed=True, sumo_exit_code=0)

        with (
            patch.object(map_sources, "geocode", return_value=self.geo),
            patch.object(agent, "choose_map_place", return_value=self.choice),
            patch.object(map_sources, "fetch_roads", return_value=record),
            patch.object(realmap, "compile_network", side_effect=compile_fake),
            patch.object(realmap.renderer, "render_network"),
            patch.object(
                realmap.renderer,
                "render_map_overview",
                side_effect=lambda network, target, **kwargs: Path(target).write_bytes(
                    kwargs["ui_language"].encode()
                ),
            ),
            patch.object(agent, "review_geometry", return_value=VisualReview(issues=[])),
        ):
            result = realmap.generate_real_map("北航四个方向都有信号灯", self.intent)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.user_review, "pending")
        self.assertEqual(result.unverified_requirements, self.intent.extra_requirements)
        self.assertTrue(result.validation.passed)
        self.assertIn("四个方向都有信号灯", " ".join(result.warnings))

    def test_default_auto_dispatch_and_explicit_synthetic_bypass(self):
        imported = realmap.RealMapResult(
            status="success",
            description="北航",
            task_id="test",
            intent=self.intent,
            radius_m=1000,
            radius_defaulted=False,
        )
        with (
            patch.object(config, "MODEL_CONFIGURED", True),
            patch.object(main.shutil, "which", return_value="netconvert"),
            patch.object(agent, "interpret_map_request", return_value=self.intent) as router,
            patch.object(realmap, "generate_real_map", return_value=imported) as real,
            patch.object(
                main.review_pipeline, "generate_checked", return_value={"status": "success"}
            ) as synthetic,
        ):
            self.assertEqual(
                main.generate(main.GenerateRequest(description="北航"))["source_mode"], "real_map"
            )
            real.assert_called_once()
            synthetic.assert_not_called()
            main.generate(
                main.GenerateRequest(
                    description="十字路口", source_mode="synthetic", output_kind="map"
                )
            )
            self.assertEqual(router.call_count, 1)
            synthetic.assert_called_once()

    def test_download_rejects_parent_paths_and_missing_files(self):
        for task, filename in [
            ("..", "config.py"),
            ("20260101_000000_aabbcc", ".."),
            ("20260101_000000_aabbcc", "missing.xml"),
        ]:
            with self.assertRaises(HTTPException):
                main.artifact(task, filename)


if __name__ == "__main__":
    unittest.main()
