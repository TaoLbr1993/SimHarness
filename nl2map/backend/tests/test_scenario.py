"""Offline scenario tests: numeric intent, route validity, observations and failure semantics."""

import json
import subprocess
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
import scenario_catalog
import scenario_pipeline
from realmap_models import MapIntent
from scenario_demand import TrafficNetwork, build_demand, write_simulation_config
from scenario_metrics import collect_metrics
from scenario_models import ScenarioIntent, ScenarioResult, ScenarioSettings, resolve_plan
from test_xml_rules import crossroad


def network_fixture():
    root = crossroad()
    for lane in root.iter("lane"):
        lane.set("length", "100")
        lane.set("speed", "13.9")
        lane.set("shape", "0,0 100,0")
    return root


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        catalog_patch = patch.object(config, "SCENARIO_CATALOG_DIR", self.output / "catalog")
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        self.network = self.output / "network.net.xml"
        ET.ElementTree(network_fixture()).write(self.network)
        self.settings = ScenarioSettings()
        self.intent = ScenarioIntent(
            objective="测试拥堵车速", traffic_state="congested", traffic_state_quote="拥堵"
        )
        self.plan = resolve_plan(self.intent, self.settings)

    def test_defaults_separate_density_hint_from_inflow(self):
        self.assertEqual(self.plan.desired_density_veh_per_km_lane, 65)
        self.assertEqual(self.plan.inflow_veh_per_hour_per_lane, 195)
        self.assertEqual((self.plan.duration_s, self.plan.warmup_s), (600, 120))
        self.assertTrue(self.plan.defaults_used)

    def test_explicit_parameters_and_zero_warmup_are_preserved(self):
        intent = ScenarioIntent(
            objective="测试",
            duration_s=180,
            duration_quote="3分钟",
            warmup_s=0,
            warmup_quote="不预热",
            vehicle_count=50,
            vehicle_count_quote="50辆",
        )
        intent.validate_sources("测试3分钟，不预热，50辆")
        plan = resolve_plan(intent, self.settings)
        self.assertEqual(
            (plan.duration_s, plan.warmup_s, plan.requested_vehicle_count), (180, 0, 50)
        )
        self.assertIsNone(plan.inflow_veh_per_hour_per_lane)
        intent.warmup_s = 180
        with self.assertRaisesRegex(ValueError, "预热"):
            resolve_plan(intent, self.settings)

    def test_numeric_parameters_need_real_quotes_and_conflicting_controls_fail(self):
        self.intent.vehicle_count = 20
        with self.assertRaises(ValueError):
            self.intent.validate_sources("测试拥堵车速")
        with self.assertRaises(ValueError):
            ScenarioIntent(objective="测试", vehicle_count=50, inflow_veh_per_hour_per_lane=100)

    def test_exact_schedule_is_seeded_and_routes_follow_connections(self):
        demand = build_demand(self.network, self.output, self.plan, self.settings)
        first = (self.output / "traffic.rou.xml").read_bytes()
        build_demand(self.network, self.output, self.plan, self.settings)
        self.assertEqual(first, (self.output / "traffic.rou.xml").read_bytes())
        xml = ET.fromstring(first)
        self.assertEqual(len(xml.findall("vehicle")), 130)
        self.assertEqual(demand.origin_lane_count, 4)
        graph = TrafficNetwork.read(self.network)
        for route in xml.findall("route"):
            edges = route.attrib["edges"].split()
            self.assertGreaterEqual(len(edges), 2)
            for before, after in zip(edges, edges[1:], strict=False):
                self.assertIn(after, graph.next_edges[before])
        departures = [float(v.attrib["depart"]) for v in xml.findall("vehicle")]
        self.assertEqual(departures, sorted(departures))
        self.assertLess(departures[-1], self.plan.duration_s)

    def test_passenger_permissions_exclude_bus_only_origins(self):
        root = network_fixture()
        root.find("edge[@id='north_in']/lane").set("allow", "bus")
        ET.ElementTree(root).write(self.network)
        demand = build_demand(self.network, self.output, self.plan, self.settings)
        self.assertNotIn("north_in", demand.origin_edges)
        self.assertEqual(demand.origin_lane_count, 3)

    def test_no_connection_and_excessive_demand_are_explicit_failures(self):
        with self.assertRaisesRegex(ValueError, "计划发车"):
            build_demand(
                self.network,
                self.output,
                self.plan,
                self.settings.model_copy(update={"max_vehicles": 20}),
            )
        root = network_fixture()
        for connection in root.findall("connection"):
            root.remove(connection)
        ET.ElementTree(root).write(self.network)
        with self.assertRaisesRegex(ValueError, "可达"):
            build_demand(self.network, self.output, self.plan, self.settings)

    def test_configuration_collects_data_without_speed_or_signal_manipulation(self):
        write_simulation_config(self.output, self.plan, self.settings)
        config_xml = ET.parse(self.output / "scenario.sumocfg").getroot()
        self.assertEqual(config_xml.find("processing/time-to-teleport").get("value"), "-1")
        self.assertEqual(config_xml.find("output/summary-output.period").get("value"), "0.5")
        self.assertEqual(config_xml.find("time/end").get("value"), "600")
        self.assertFalse(list(self.output.glob("*.tll.xml")))

    def write_observations(self, *, empty=False, collisions=0):
        self.plan.duration_s = 60
        self.plan.warmup_s = 0
        demand = build_demand(self.network, self.output, self.plan, self.settings)
        summary = ET.Element("summary")
        for t in range(60):
            running = 0 if empty else 1 if t < 30 else 3
            speed = -1 if empty else 10 if t < 30 else 0
            ET.SubElement(
                summary,
                "step",
                time=str(t),
                running=str(running),
                halting=str(running if t >= 30 else 0),
                meanSpeed=str(speed),
                meanSpeedRelative=str(speed / 10 if not empty else -1),
                waiting="2",
            )
        ET.ElementTree(summary).write(self.output / "summary.xml")
        data = ET.Element("meandata")
        interval = ET.SubElement(data, "interval", begin="0", end="60")
        edge = ET.SubElement(interval, "edge", id="north_in")
        ET.SubElement(edge, "lane", id="north_in_0", sampledSeconds="0" if empty else "120")
        ET.ElementTree(data).write(self.output / "lane_metrics.xml")
        stats = ET.Element("statistics")
        ET.SubElement(stats, "safety", collisions=str(collisions))
        ET.SubElement(stats, "teleports", total="0")
        ET.SubElement(stats, "vehicles", inserted="7", running="3", waiting="2")
        ET.ElementTree(stats).write(self.output / "statistics.xml")
        trips = ET.Element("tripinfos")
        ET.SubElement(trips, "tripinfo", depart="0", arrival="10", duration="10")
        ET.SubElement(trips, "tripinfo", depart="0", arrival="30", duration="30")
        ET.SubElement(trips, "tripinfo", depart="30", arrival="-1", duration="30")
        ET.ElementTree(trips).write(self.output / "tripinfo.xml")
        return demand

    def test_measured_speed_is_vehicle_time_weighted_and_density_uses_lane_km(self):
        demand = self.write_observations()
        result = collect_metrics(self.output, self.plan, demand)
        self.assertAlmostEqual(result.mean_speed_kmh, 9.0)
        self.assertAlmostEqual(result.mean_external_density_veh_per_km_lane, 2.5)
        self.assertEqual(result.mean_halting_vehicles, 1.5)
        self.assertEqual(result.congestion_time_fraction, 0.5)
        self.assertEqual(result.target_state, "observed")
        self.assertEqual(result.mean_completed_travel_time_s, 20)
        self.assertEqual(result.arrived_vehicles, 2)
        self.assertEqual(result.inserted_vehicles, 7)
        self.assertEqual(result.pending_at_end, 2)
        self.assertEqual(result.not_inserted_vehicles, demand.planned_vehicles - 7)

    def test_empty_window_is_not_zero_speed_congestion_and_collision_invalidates_trial(self):
        demand = self.write_observations(empty=True)
        result = collect_metrics(self.output, self.plan, demand)
        self.assertIsNone(result.mean_speed_kmh)
        self.assertIsNone(result.congestion_observed)
        self.assertEqual(result.target_state, "no_data")
        demand = self.write_observations(collisions=1)
        result = collect_metrics(self.output, self.plan, demand)
        self.assertEqual(result.target_state, "invalid_run")
        self.assertIsNone(result.congestion_observed)

    def test_incomplete_trial_cannot_be_reported_as_completed(self):
        demand = self.write_observations()
        root = ET.parse(self.output / "summary.xml").getroot()
        for step in root.findall("step")[30:]:
            root.remove(step)
        ET.ElementTree(root).write(self.output / "summary.xml")
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            collect_metrics(self.output, self.plan, demand)

    def source_task(self):
        task = "20260101_000000_aabbcc"
        directory = self.output / task / "output"
        directory.mkdir(parents=True)
        (directory / "validated.net.xml").write_bytes(self.network.read_bytes())
        return task

    def test_generation_only_preserves_source_and_writes_self_contained_package(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with (
            patch.object(config, "WORKSPACES_DIR", str(self.output)),
            patch.object(scenario_pipeline.subprocess, "run") as simulator,
        ):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
        simulator.assert_not_called()
        self.assertEqual(result.status, "generated")
        self.assertFalse(main.GenerateRequest(description="测试拥堵车速").run_simulation)
        self.assertFalse(main.GenerateRequest(description="测试拥堵车速").publish_to_catalog)
        self.assertIsNone(result.catalog_map_id)
        self.assertEqual(scenario_catalog.list_scenarios(), [])
        self.assertEqual(result.intent, self.intent)
        self.assertIsNone(result.metrics)
        target = self.output / result.task_id / "output"
        self.assertTrue((target / "traffic.rou.xml").is_file())
        self.assertTrue((target / "map_preview.png").is_file())
        self.assertEqual(
            result.artifact_urls["output/map_preview.png"],
            f"/artifacts/{result.task_id}/map_preview.png",
        )
        stored = ScenarioResult.model_validate_json((target / "scenario.json").read_text())
        self.assertEqual(stored, result)
        self.assertEqual((target / "network.net.xml").read_bytes(), self.network.read_bytes())
        self.assertNotEqual(result.task_id, task)

    def test_explicit_legacy_auto_publication_remains_opt_in(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario(
                "测试拥堵车速", intent, map_task_id=task, publish_to_catalog=True
            )
        self.assertEqual(result.status, "generated")
        self.assertEqual(result.catalog_map_id, f"nl2-{result.task_id}")
        self.assertEqual(len(scenario_catalog.list_scenarios()), 1)

    def test_preview_failure_cannot_report_a_complete_draft_or_publish(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with (
            patch.object(config, "WORKSPACES_DIR", str(self.output)),
            patch.object(
                scenario_pipeline.renderer, "render_network", side_effect=ValueError("缺少道路几何")
            ),
            patch.object(scenario_pipeline.subprocess, "run") as simulator,
        ):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
            with self.assertRaisesRegex(ValueError, "完整可运行"):
                scenario_catalog.publish_existing(result.task_id)
        simulator.assert_not_called()
        self.assertEqual(result.status, "scenario_error")
        self.assertIn("preview：缺少道路几何", result.error)
        self.assertEqual(scenario_catalog.list_scenarios(), [])

    def test_reuse_requires_explicit_task_and_does_not_guess_latest(self):
        self.intent.use_existing_map = True
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario("用当前地图测试拥堵车速", intent)
        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("map_task_id", result.error)

    def test_scenario_none_fallback_rejects_unrepresented_levels_before_generation(self):
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=None)
        for description in (
            "测试，L0占70%，L3占30%",
            "测试，L0和L3各半",
            "测试，70% L0,30% L3",
        ):
            with (
                self.subTest(description=description),
                patch.object(config, "WORKSPACES_DIR", str(self.output)),
                patch.object(scenario_pipeline.review_pipeline, "generate_checked") as generate_map,
                patch.object(scenario_pipeline, "build_demand") as demand,
                patch.object(scenario_catalog, "publish_scenario") as publish,
                patch.object(scenario_pipeline.subprocess, "run") as simulator,
            ):
                result = scenario_pipeline.generate_scenario(
                    description,
                    intent,
                    publish_to_catalog=True,
                    simulation_mode="autonomy",
                )
                with self.assertRaisesRegex(ValueError, "完整可运行"):
                    scenario_catalog.publish_existing(result.task_id)
            generate_map.assert_not_called()
            demand.assert_not_called()
            publish.assert_not_called()
            simulator.assert_not_called()
            self.assertEqual(result.status, "scenario_error")
            self.assertTrue(result.error.startswith("parameters："))
            self.assertIsNone(result.plan)
            self.assertIsNone(result.demand)
            self.assertIsNone(result.catalog_map_id)
            self.assertEqual(scenario_catalog.list_scenarios(), [])
            output = self.output / result.task_id / "output"
            self.assertEqual({path.name for path in output.iterdir()}, {"scenario.json"})

    def test_pipeline_revalidates_existing_scene_numeric_sources(self):
        description = "测试，L0占50%，L3占50%"
        intent = MapIntent(
            source_mode="synthetic",
            reason="test",
            scenario=ScenarioIntent(
                objective="测试",
                automation_mix=[{"level": "L0", "share_percent": 100}],
                automation_mix_quote="L0占50%，L3占50%",
            ),
        )
        with (
            patch.object(config, "WORKSPACES_DIR", str(self.output)),
            patch.object(scenario_pipeline.review_pipeline, "generate_checked") as generate_map,
            patch.object(scenario_catalog, "publish_scenario") as publish,
        ):
            result = scenario_pipeline.generate_scenario(
                description, intent, publish_to_catalog=True, simulation_mode="autonomy"
            )
        self.assertEqual(result.status, "scenario_error")
        self.assertIn("L0 原文为 50%，模型却为 100%", result.error)
        generate_map.assert_not_called()
        publish.assert_not_called()

    def test_plain_fallback_still_generates_declared_default_autonomy_mix(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=None)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario(
                "生成一个十字路口", intent, map_task_id=task, simulation_mode="autonomy"
            )
        self.assertEqual(result.status, "generated")
        self.assertIsNone(result.catalog_map_id)
        self.assertEqual(result.demand.automation_mix[0].share_percent, 100)
        self.assertTrue(any("默认 L0 100%" in note for note in result.plan.defaults_used))

    def test_published_definition_is_immutable_and_excludes_measured_outputs(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
            output = self.output / result.task_id / "output"
            (output / "metrics.json").write_text('{"mean_speed_kmh":99}', encoding="utf-8")
            original_draft = (output / "scenario.json").read_bytes()
            published = scenario_catalog.publish_existing(result.task_id)
            repeated = scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(published, repeated)
        self.assertEqual((output / "scenario.json").read_bytes(), original_draft)
        saved = Path(config.SCENARIO_CATALOG_DIR) / published.map_id
        self.assertFalse((saved / "metrics.json").exists())
        self.assertFalse((saved / "summary.xml").exists())
        self.assertEqual(
            (saved / "traffic.rou.xml").read_bytes(), (output / "traffic.rou.xml").read_bytes()
        )
        self.assertEqual(repeated.settings.planned_vehicles, 130)
        self.assertEqual(repeated.settings.duration_s, 600)
        self.assertEqual(repeated.settings.simulation_mode, "traffic")
        self.assertEqual(repeated.settings.automation_mix, [])
        self.assertEqual(len(scenario_catalog.list_scenarios()), 1)
        self.assertEqual(scenario_catalog.get_scenario(result.task_id).map_id, published.map_id)
        metadata = json.loads((saved / "scenario.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["spec"]["trafficDemandMode"], "scripted")
        self.assertEqual(
            metadata["spec"]["definition"]["plan"]["defaults_used"], result.plan.defaults_used
        )
        original = (saved / "traffic.rou.xml").read_bytes()
        (output / "traffic.rou.xml").write_text("modified source", encoding="utf-8")
        scenario_catalog.publish_scenario(result, output)
        self.assertEqual((saved / "traffic.rou.xml").read_bytes(), original)
        with self.assertRaises((ValueError, FileNotFoundError)):
            scenario_catalog.scenario_file(result.task_id, "../../.env")

    def test_failed_publication_leaves_no_discoverable_package_or_staging_directory(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
            with (
                patch.object(scenario_catalog.shutil, "copyfile", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(scenario_catalog.list_scenarios(), [])
        self.assertEqual(list(Path(config.SCENARIO_CATALOG_DIR).iterdir()), [])

    def test_invalid_input_xml_is_a_recoverable_publish_error(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
            (self.output / result.task_id / "output/traffic.rou.xml").write_text("invalid XML")
            with self.assertRaisesRegex(ValueError, "traffic.rou.xml"):
                scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(scenario_catalog.list_scenarios(), [])

    def test_real_map_pending_requirements_reach_published_summary(self):
        task = self.source_task()
        metadata = self.output / task / "output/metadata.json"
        metadata.write_text(
            json.dumps({"unverified_requirements": ["保留全部转向限制"]}), encoding="utf-8"
        )
        intent = MapIntent(source_mode="real_map", reason="test", scenario=self.intent)
        map_result = SimpleNamespace(
            model_dump=lambda **kwargs: {
                "status": "success",
                "task_id": task,
                "unverified_requirements": ["增加公交专用道"],
            }
        )
        with (
            patch.object(config, "WORKSPACES_DIR", str(self.output)),
            patch.object(scenario_pipeline.realmap, "generate_real_map", return_value=map_result),
        ):
            result = scenario_pipeline.generate_scenario(
                "北航附近测试拥堵车速，增加公交专用道", intent
            )
            published = scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(result.status, "generated")
        self.assertEqual(result.unverified_requirements, ["增加公交专用道", "保留全部转向限制"])
        self.assertEqual(
            published.settings.unverified_requirements,
            result.unverified_requirements,
        )

    def test_legacy_publish_recovers_pending_map_requirements(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        with patch.object(config, "WORKSPACES_DIR", str(self.output)):
            result = scenario_pipeline.generate_scenario("测试拥堵车速", intent, map_task_id=task)
            output = self.output / result.task_id / "output"
            (output / "source_map_metadata.json").write_text(
                json.dumps({"unverified_requirements": ["增加公交专用道"]}), encoding="utf-8"
            )
            result.unverified_requirements = []
            (output / "scenario.json").write_text(result.model_dump_json(), encoding="utf-8")
            with patch.object(config, "SCENARIO_CATALOG_DIR", self.output / "legacy_catalog"):
                published = scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(published.settings.unverified_requirements, ["增加公交专用道"])

    def test_timeout_retains_partial_simulator_diagnostics(self):
        task = self.source_task()
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        version = SimpleNamespace(stdout="version", stderr="", returncode=0)
        timeout = subprocess.TimeoutExpired(["sumo"], 120, output=b"partial", stderr=b"diagnostic")
        with (
            patch.object(config, "WORKSPACES_DIR", str(self.output)),
            patch.object(scenario_pipeline.shutil, "which", return_value="sumo"),
            patch.object(scenario_pipeline.subprocess, "run", side_effect=[version, timeout]),
        ):
            result = scenario_pipeline.generate_scenario(
                "测试拥堵车速", intent, map_task_id=task, run_simulation=True
            )
        self.assertEqual(result.status, "scenario_error")
        self.assertIsNone(result.metrics)
        self.assertIn(
            "partialdiagnostic",
            (self.output / result.task_id / "output/simulation.log").read_text(),
        )

    def test_auto_entry_routes_scenario_and_map_override_keeps_old_behavior(self):
        intent = MapIntent(source_mode="synthetic", reason="test", scenario=self.intent)
        scene = ScenarioResult(status="generated", description="测试拥堵车速", task_id="test")
        with (
            patch.object(config, "MODEL_CONFIGURED", True),
            patch.object(main.shutil, "which", return_value="tool"),
            patch.object(agent, "interpret_map_request", return_value=intent),
            patch.object(scenario_pipeline, "generate_scenario", return_value=scene) as generate,
            patch.object(
                main.review_pipeline, "generate_checked", return_value={"status": "success"}
            ) as roadgen,
        ):
            result = main.generate(
                main.GenerateRequest(description="测试拥堵车速", run_simulation=False)
            )
            self.assertEqual(result["output_kind"], "scenario")
            self.assertFalse(generate.call_args.kwargs["run_simulation"])
            self.assertFalse(generate.call_args.kwargs["publish_to_catalog"])
            self.assertEqual(generate.call_args.kwargs["simulation_mode"], "traffic")
            roadgen.assert_not_called()
            main.generate(
                main.GenerateRequest(
                    description="测试拥堵车速", publish_to_catalog=True, simulation_mode="autonomy"
                )
            )
            self.assertTrue(generate.call_args.kwargs["publish_to_catalog"])
            self.assertEqual(generate.call_args.kwargs["simulation_mode"], "autonomy")
            main.generate(main.GenerateRequest(description="生成路口", output_kind="map"))
            self.assertEqual(generate.call_count, 2)
            roadgen.assert_called_once()

    def test_agent_parses_numeric_scene_fields_and_leaves_unspecified_defaults_unfilled(self):
        scene = ScenarioIntent(
            objective="测试拥堵车速",
            traffic_state="congested",
            traffic_state_quote="拥堵",
            duration_s=180,
            duration_quote="3分钟",
        )
        raw = MapIntent(source_mode="synthetic", reason="场景", scenario=scene).model_dump()
        with patch.object(agent, "OpenAI") as client:
            client.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))]
            )
            result = agent.interpret_map_request("测试拥堵车速，3分钟")
        self.assertEqual(result.scenario.duration_s, 180)
        self.assertIsNone(result.scenario.density_veh_per_km_lane)


if __name__ == "__main__":
    unittest.main()
