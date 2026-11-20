"""Area selection contracts and source fidelity; no network requests or traffic runs."""

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
import map_sources
import realmap
import scenario_catalog
import scenario_pipeline
from fastapi import HTTPException
from fastapi.testclient import TestClient
from map_area import SelectedBounds
from PIL import Image
from realmap_models import GeocodingResult, MapIntent, PlaceCandidate, SourceRecord
from scenario_models import ScenarioIntent
from test_scenario import network_fixture

BOUNDS = {"west": 116.34, "south": 39.98, "east": 116.35, "north": 39.99}


class MapAreaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(config, "WORKSPACES_DIR", self.output).start()
        self.bounds = SelectedBounds(**BOUNDS)
        self.intent = MapIntent(source_mode="real_map", reason="Selected rectangle")
        self.place = PlaceCandidate(id="area", name="Area", longitude=116.345, latitude=39.985)
        self.record = SourceRecord(url="source", query="query", fetched_at="now", cache_hit=False)

    def test_invalid_coordinates_and_conflicting_sources_are_rejected(self):
        for change in (
            {"west": 181},
            {"west": float("nan")},
            {"south": -85},
            {"north": float("inf")},
            {"west": 116.35},
            {"north": 39.97},
            {"west": True},
            {"west": "116.34"},
            {"west": -179, "east": 179},
            {"extra": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                main.GenerateRequest(selected_bounds={**BOUNDS, **change})
        for change in (
            {"source_mode": "synthetic"},
            {"selected_place_id": "N123"},
            {"map_task_id": "20261106_010101_aabbcc"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                main.GenerateRequest(selected_bounds=BOUNDS, **change)
        with self.assertRaises(ValueError):
            main.GenerateRequest(description=" ")

    def test_extent_limit_is_configured_and_not_silently_clamped(self):
        limit = self.bounds.diagonal_m
        with patch.object(
            config, "REAL_MAP", config.REAL_MAP.model_copy(update={"max_radius_m": limit / 2})
        ):
            self.assertEqual(main.map_selection_config().max_diagonal_m, limit)
            self.assertEqual(
                main.GenerateRequest(selected_bounds=BOUNDS).selected_bounds, self.bounds
            )
            with self.assertRaisesRegex(ValueError, "too large"):
                main.GenerateRequest(selected_bounds={**BOUNDS, "east": 116.351})

    def test_public_api_validates_area_before_model_or_provider_access(self):
        with TestClient(main.app) as client, patch.object(agent, "interpret_map_request") as parse:
            response = client.post("/generate", json={"selected_bounds": {**BOUNDS, "east": 140}})
            self.assertEqual(response.status_code, 422)
            response = client.get("/map/places", params={"q": " "})
            self.assertEqual(response.status_code, 422)
            parse.assert_not_called()

    def test_blank_area_uses_defaults_without_model_and_does_not_run_or_publish(self):
        response = SimpleNamespace(model_dump=lambda **_: {"status": "generated"})
        with (
            patch.object(config, "MODEL_CONFIGURED", False),
            patch.object(main.shutil, "which", return_value="netconvert"),
            patch.object(agent, "interpret_map_request") as parse,
            patch.object(scenario_pipeline, "generate_scenario", return_value=response) as generate,
        ):
            result = main.generate(main.GenerateRequest(selected_bounds=BOUNDS))
        self.assertEqual(result["status"], "generated")
        parse.assert_not_called()
        self.assertEqual(generate.call_args.kwargs["selected_bounds"], self.bounds)
        self.assertFalse(generate.call_args.kwargs["run_simulation"])
        self.assertFalse(generate.call_args.kwargs["publish_to_catalog"])
        self.assertEqual(generate.call_args.args[1].source_mode, "real_map")

    def test_text_without_model_explains_area_only_option(self):
        with patch.object(config, "MODEL_CONFIGURED", False), TestClient(main.app) as client:
            for language, expected in (("en", "leave the description blank"), ("zh", "留空描述")):
                with self.subTest(language=language):
                    result = client.post(
                        "/generate",
                        json={
                            "selected_bounds": BOUNDS,
                            "description": "100 vehicles",
                            "ui_language": language,
                        },
                    ).json()
                    self.assertEqual(result["status"], "agent_error")
                    self.assertIn(expected, result["error"])

    def test_text_area_keeps_scene_parameters_without_requiring_a_place(self):
        self.intent.scenario = ScenarioIntent(
            objective="50 vehicles", vehicle_count=50, vehicle_count_quote="50 vehicles"
        )
        response = SimpleNamespace(model_dump=lambda **_: {"status": "generated"})
        with (
            patch.object(config, "MODEL_CONFIGURED", True),
            patch.object(main.shutil, "which", return_value="netconvert"),
            patch.object(agent, "interpret_map_request", return_value=self.intent) as parse,
            patch.object(scenario_pipeline, "generate_scenario", return_value=response) as generate,
        ):
            main.generate(main.GenerateRequest(description="50 vehicles", selected_bounds=BOUNDS))
        parse.assert_called_once_with("50 vehicles", area_selected=True)
        self.assertEqual(generate.call_args.args[1].scenario.vehicle_count, 50)
        self.assertEqual(generate.call_args.kwargs["selected_bounds"], self.bounds)

    def test_area_intent_checks_quotes_but_not_missing_geocoder_fields(self):
        self.intent.validate_sources("selected", area_selected=True)
        self.intent.detail_level = "detailed"
        self.intent.detail_quote = "missing"
        with self.assertRaises(ValueError):
            self.intent.validate_sources("selected", area_selected=True)
        self.intent.detail_quote = "selected"
        self.intent.validate_sources("selected", area_selected=True)

    def test_overpass_uses_south_west_north_east_and_retains_restrictions(self):
        body = b'<osm><way id="1"><tag k="highway" v="primary"/></way></osm>'
        with patch.object(
            map_sources, "_fetch", return_value=map_sources.SourceResponse(body, self.record)
        ) as fetch:
            map_sources.fetch_roads(self.place, None, self.output, selected_bounds=self.bounds)
        query = fetch.call_args.args[1]
        self.assertIn("way(39.9800000,116.3400000,39.9900000,116.3500000)", query)
        self.assertNotIn("around:", query)
        self.assertIn('rel(bw.roads)["type"="restriction"]', query)
        self.assertEqual((self.output / "source.osm.xml").read_bytes(), body)

    def test_empty_osm_and_oversized_direct_fetch_fail_without_fallback(self):
        with patch.object(
            map_sources, "_fetch", return_value=map_sources.SourceResponse(b"<osm/>", self.record)
        ) as fetch:
            with self.assertRaisesRegex(ValueError, "OSM"):
                map_sources.fetch_roads(self.place, None, self.output, selected_bounds=self.bounds)
            with self.assertRaisesRegex(ValueError, "too large"):
                map_sources.fetch_roads(
                    self.place,
                    None,
                    self.output,
                    selected_bounds=SelectedBounds(**{**BOUNDS, "east": 120}),
                )
            self.assertEqual(fetch.call_count, 1)

    def test_both_netconvert_passes_use_exact_rectangle_not_circle(self):
        good = SimpleNamespace(returncode=0, stdout="", stderr="")
        stop = SimpleNamespace(returncode=1, stdout="", stderr="stop")
        with (
            patch.object(realmap.shutil, "which", return_value="tool"),
            patch.object(realmap.subprocess, "run", side_effect=[good, stop]),
            patch.object(realmap, "_selection_polygon") as circle,
            self.assertRaisesRegex(ValueError, "stop"),
        ):
            realmap.compile_network(self.output, self.place, None, selected_bounds=self.bounds)
        commands = json.loads((self.output / "conversion-command.json").read_text())
        for command in commands:
            self.assertEqual(
                command[command.index("--keep-edges.in-geo-boundary") + 1],
                self.bounds.geo_boundary(),
            )
        circle.assert_not_called()

    def test_area_generation_bypasses_geocoder_and_preserves_center_and_extent(self):
        def draw(network, output, **kwargs):
            Image.new("RGB", (10, 10), "white").save(output)

        def compile_map(output, *args, **kwargs):
            (output / "netconvert.log").write_text("")
            return realmap.ImportValidation(passed=True)

        with (
            patch.object(map_sources, "geocode") as geocode,
            patch.object(agent, "choose_map_place") as choose,
            patch.object(map_sources, "fetch_roads", return_value=self.record) as fetch,
            patch.object(realmap, "compile_network", side_effect=compile_map) as compile_mock,
            patch.object(realmap.renderer, "render_map_overview", side_effect=draw) as render,
        ):
            result = realmap.generate_real_map("Selected", self.intent, selected_bounds=self.bounds)
        self.assertEqual(result.status, "success")
        geocode.assert_not_called()
        choose.assert_not_called()
        self.assertIsNone(result.radius_m)
        self.assertFalse(result.radius_defaulted)
        self.assertEqual(result.selected_bounds, self.bounds)
        self.assertEqual(result.place.provider, "selection")
        self.assertAlmostEqual(result.place.longitude, 116.345)
        self.assertAlmostEqual(result.place.latitude, 39.985)
        self.assertEqual(fetch.call_args.kwargs["selected_bounds"], self.bounds)
        self.assertEqual(compile_mock.call_args.kwargs["selected_bounds"], self.bounds)
        self.assertTrue(
            all(c.kwargs["selected_bounds"] == self.bounds for c in render.call_args_list)
        )
        saved = json.loads((self.output / result.task_id / "output/metadata.json").read_text())
        self.assertEqual(saved["selected_bounds"], BOUNDS)
        self.assertIn("rectangle", saved["extent_policy"])

    def test_map_search_has_no_model_side_effect_and_cleans_temporary_files(self):
        outputs = []

        def geocode(query, alternate, output, **kwargs):
            outputs.append(output)
            self.assertEqual(query, "北航")
            (output / "evidence.json").write_text("{}")
            return GeocodingResult([self.place], [], ["warning"])

        with (
            patch.object(map_sources, "geocode", side_effect=geocode),
            patch.object(agent, "choose_map_place") as choose,
        ):
            result = main.map_places(" 北航 ")
        self.assertEqual(result.candidates, [self.place])
        self.assertEqual(result.warnings, ["warning"])
        self.assertFalse(outputs[0].exists())
        choose.assert_not_called()

    def test_map_search_failure_is_actionable_and_cleans_up(self):
        outputs = []

        def failure(query, alternate, output, **kwargs):
            outputs.append(output)
            raise ValueError("HTTP 429; retry later")

        with (
            patch.object(map_sources, "geocode", side_effect=failure),
            self.assertRaises(HTTPException) as failure_context,
        ):
            main.map_places("London")
        self.assertEqual(failure_context.exception.status_code, 503)
        self.assertIn("429", failure_context.exception.detail)
        self.assertFalse(outputs[0].exists())

    def test_scenario_pipeline_carries_bounds_and_builds_only_unpublished_demand(self):
        map_id = "20261106_010101_aabbcc"
        source = self.output / map_id / "output"
        source.mkdir(parents=True)
        ET.ElementTree(network_fixture()).write(source / "validated.net.xml")
        Image.new("RGB", (10, 10), "white").save(source / "map_preview.png")
        map_result = realmap.RealMapResult(
            status="success",
            description="Selected",
            task_id=map_id,
            intent=self.intent,
            radius_m=None,
            radius_defaulted=False,
            selected_bounds=self.bounds,
        )
        with (
            patch.object(realmap, "generate_real_map", return_value=map_result) as generate,
            patch.object(scenario_pipeline.subprocess, "run") as simulate,
            patch.object(scenario_catalog, "publish_scenario") as publish,
        ):
            result = scenario_pipeline.generate_scenario(
                "Selected", self.intent, selected_bounds=self.bounds
            )
        self.assertEqual(result.status, "generated", result.error)
        self.assertEqual(result.selected_bounds, self.bounds)
        self.assertGreater(result.demand.planned_vehicles, 0)
        self.assertEqual(generate.call_args.kwargs["selected_bounds"], self.bounds)
        simulate.assert_not_called()
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
