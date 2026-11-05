"""Count edits create faithful input-only drafts without changing published scenarios."""

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import main
import scenario_catalog
import scenario_revision
from fastapi.testclient import TestClient
from PIL import Image
from scenario_demand import build_demand, write_simulation_config
from scenario_models import (
    AutomationMixShare,
    ScenarioCountRevision,
    ScenarioIntent,
    ScenarioResult,
    ScenarioRevision,
    ScenarioSettings,
    resolve_plan,
)
from test_scenario import network_fixture

SOURCE_ID = "20261104_100000_aabbcc"


class ScenarioRevisionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspaces"
        self.catalog = self.root / "catalog"
        self.source_dir = self.workspace / SOURCE_ID / "output"
        self.source_dir.mkdir(parents=True)
        for name, value in (
            ("WORKSPACES_DIR", self.workspace),
            ("SCENARIO_CATALOG_DIR", self.catalog),
            ("SCENARIO", ScenarioSettings()),
        ):
            mocked = patch.object(config, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.source = self._source()

    def _source(self, autonomy=False):
        description = "测试车辆速度，共3辆，L0占70%，L3占30%" if autonomy else "测试车辆速度"
        intent = ScenarioIntent(
            objective="测试车辆速度",
            vehicle_count=3 if autonomy else None,
            vehicle_count_quote="3辆" if autonomy else "",
            automation_mix=[
                AutomationMixShare(level="L0", share_percent=70),
                AutomationMixShare(level="L3", share_percent=30),
            ]
            if autonomy
            else [],
            automation_mix_quote="L0占70%，L3占30%" if autonomy else "",
        )
        intent.validate_sources(description)
        plan = resolve_plan(
            intent, config.SCENARIO, simulation_mode="autonomy" if autonomy else "traffic"
        )
        network = self.source_dir / "network.net.xml"
        ET.ElementTree(network_fixture()).write(network)
        Image.new("RGB", (12, 12), "white").save(self.source_dir / "map_preview.png")
        (self.source_dir / "source_map_metadata.json").write_text(
            json.dumps({"unverified_requirements": ["源地图待核对"]}), encoding="utf-8"
        )
        demand = build_demand(network, self.source_dir, plan, config.SCENARIO)
        write_simulation_config(self.source_dir, plan, config.SCENARIO)
        result = ScenarioResult(
            status="generated",
            description=description,
            task_id=SOURCE_ID,
            map_task_id="20261104_090000_abcdef",
            intent=intent,
            plan=plan,
            demand=demand,
            unverified_requirements=["源地图待核对"],
            notes=["原有说明"],
        )
        (self.source_dir / "scenario.json").write_text(result.model_dump_json(), encoding="utf-8")
        return result

    def _snapshot(self, directory):
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }

    def test_count_override_rebuilds_exact_schedule_and_preserves_original_inputs(self):
        before = self._snapshot(self.source_dir)
        result = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioCountRevision(vehicle_count=137)
        )
        output = self.workspace / result.task_id / "output"
        self.assertNotEqual(result.task_id, SOURCE_ID)
        self.assertEqual(result.status, "generated")
        self.assertIsNone(result.catalog_map_id)
        self.assertIsNone(result.metrics)
        self.assertIsNone(result.simulation_exit_code)
        self.assertEqual(result.parent_task_id, SOURCE_ID)
        self.assertEqual(result.manual_overrides.vehicle_count, 137)
        self.assertEqual(result.intent, self.source.intent)
        self.assertEqual(result.description, self.source.description)
        self.assertEqual(result.map_task_id, self.source.map_task_id)
        self.assertEqual(result.unverified_requirements, self.source.unverified_requirements)
        self.assertEqual(result.demand.planned_vehicles, 137)
        self.assertEqual(result.plan.requested_vehicle_count, 137)
        self.assertIsNone(result.plan.inflow_veh_per_hour_per_lane)
        self.assertNotIn("发车流量由目标密度 × 参考速度推算", result.plan.defaults_used)
        self.assertEqual(result.plan.duration_s, self.source.plan.duration_s)
        self.assertEqual(result.plan.seed, self.source.plan.seed)
        self.assertEqual(
            result.plan.desired_density_veh_per_km_lane,
            self.source.plan.desired_density_veh_per_km_lane,
        )
        vehicles = ET.parse(output / "traffic.rou.xml").getroot().findall("vehicle")
        self.assertEqual(len(vehicles), 137)
        self.assertLess(float(vehicles[-1].attrib["depart"]), result.plan.duration_s)
        self.assertEqual((output / "network.net.xml").read_bytes(), before["network.net.xml"])
        self.assertEqual((output / "map_preview.png").read_bytes(), before["map_preview.png"])
        self.assertEqual(self._snapshot(self.source_dir), before)
        self.assertFalse(self.catalog.exists())
        self.assertEqual(
            ScenarioResult.model_validate_json((output / "scenario.json").read_text()), result
        )
        self.assertEqual(
            result.artifact_urls["output/map_preview.png"],
            f"/artifacts/{result.task_id}/map_preview.png",
        )

    def test_full_settings_update_actual_sumo_inputs_and_cumulative_overrides(self):
        before = self._snapshot(self.source_dir)
        revision = ScenarioRevision(
            demand_mode="count",
            vehicle_count=25,
            duration_s=900,
            warmup_s=90,
            seed=123,
            desired_density_veh_per_km_lane=15,
            reference_speed_kmh=25,
            automation_mix=[
                {"level": "L0", "share_percent": 40},
                {"level": "L4", "share_percent": 60},
            ],
        )
        result = scenario_revision.revise_scenario(SOURCE_ID, revision)
        output = self.workspace / result.task_id / "output"
        configuration = ET.parse(output / "scenario.sumocfg").getroot()
        self.assertEqual(configuration.find("time/end").get("value"), "900")
        self.assertEqual(configuration.find("random_number/seed").get("value"), "123")
        metrics = ET.parse(output / "metrics.add.xml").getroot().find("laneData")
        self.assertEqual((metrics.get("begin"), metrics.get("end")), ("90", "900"))
        vehicles = ET.parse(output / "traffic.rou.xml").getroot().findall("vehicle")
        self.assertEqual(Counter(v.get("type") for v in vehicles), {"L0": 10, "L4": 15})
        self.assertAlmostEqual(float(vehicles[-1].get("depart")), 864)
        self.assertEqual(self._snapshot(self.source_dir), before)
        self.assertEqual(result.intent, self.source.intent)
        repeated = scenario_revision.revise_scenario(result.task_id, ScenarioRevision(seed=124))
        self.assertEqual(repeated.manual_overrides.duration_s, 900)
        self.assertEqual(repeated.manual_overrides.vehicle_count, 25)
        self.assertEqual(repeated.manual_overrides.seed, 124)
        self.assertEqual(repeated.demand.planned_vehicles, 25)

    def test_flow_density_and_total_flow_modes_remain_active_after_time_edit(self):
        for patch_data, expected_flow, expected_count in (
            ({"inflow_veh_per_hour_per_lane": 90}, 90, 60),
            ({"desired_density_veh_per_km_lane": 3, "reference_speed_kmh": 20}, 60, 40),
            ({"total_flow_veh_per_hour": 180}, None, 30),
        ):
            with self.subTest(patch=patch_data):
                first = scenario_revision.revise_scenario(SOURCE_ID, ScenarioRevision(**patch_data))
                self.assertEqual(first.plan.inflow_veh_per_hour_per_lane, expected_flow)
                self.assertEqual(first.demand.planned_vehicles, expected_count)
                second = scenario_revision.revise_scenario(
                    first.task_id, ScenarioRevision(duration_s=1200)
                )
                self.assertEqual(second.demand.planned_vehicles, expected_count * 2)
                self.assertNotIn("默认轻量车流：推算流量 × 10%", second.plan.defaults_used)

    def test_time_only_preserves_existing_lightweight_inflow(self):
        result = scenario_revision.revise_scenario(SOURCE_ID, ScenarioRevision(duration_s=1200))
        self.assertEqual(
            result.plan.inflow_veh_per_hour_per_lane, self.source.plan.inflow_veh_per_hour_per_lane
        )
        self.assertEqual(result.demand.planned_vehicles, self.source.demand.planned_vehicles * 2)

    def test_revision_boundary_rejects_empty_null_conflicting_and_invalid_parameters(self):
        client = TestClient(main.app)
        for payload in (
            {},
            {"seed": None},
            {"seed": True},
            {"duration_s": 900.5},
            {"warmup_s": -1},
            {"desired_density_veh_per_km_lane": True},
            {"reference_speed_kmh": 0},
            {"vehicle_count": 12, "inflow_veh_per_hour_per_lane": 50},
            {"vehicle_count": 12, "desired_density_veh_per_km_lane": 30},
            {"demand_mode": "inflow", "vehicle_count": 12, "inflow_veh_per_hour_per_lane": 50},
            {"demand_mode": "density", "inflow_veh_per_hour_per_lane": 50},
            {"automation_mix": []},
            {"automation_mix": [{"level": "L0", "share_percent": 50}]},
            {
                "automation_mix": [
                    {"level": "L0", "share_percent": 50},
                    {"level": "L0", "share_percent": 50},
                ]
            },
            {"automation_mix": [{"level": "L0", "share_percent": True}]},
        ):
            with self.subTest(payload=payload):
                response = client.post(f"/scenarios/{SOURCE_ID}/revise", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        for payload in (
            {"warmup_s": 600},
            {"total_flow_veh_per_hour": 120006},
            {"total_flow_veh_per_hour": 0.1},
        ):
            with self.subTest(payload=payload):
                response = client.post(f"/scenarios/{SOURCE_ID}/revise", json=payload)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(self.workspace.iterdir()), [self.source_dir.parent])

    def test_total_flow_reports_requested_rate_separately_from_integer_schedule(self):
        first = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioRevision(total_flow_veh_per_hour=181.5)
        )
        self.assertEqual(first.demand.planned_vehicles, 30)
        self.assertEqual(first.demand.requested_inflow_veh_per_hour, 181.5)
        self.assertEqual(first.demand.realized_schedule_veh_per_hour, 180)
        second = scenario_revision.revise_scenario(
            first.task_id, ScenarioRevision(vehicle_count=10)
        )
        self.assertEqual(second.plan.demand_mode, "count")
        self.assertIsNone(second.plan.requested_total_flow_veh_per_hour)
        self.assertNotIn("total_flow_veh_per_hour", second.manual_overrides.model_dump())
        self.assertEqual(second.manual_overrides.vehicle_count, 10)

    def test_reference_speed_only_does_not_silently_change_flow_or_vehicle_speed(self):
        result = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioRevision(reference_speed_kmh=50)
        )
        self.assertEqual(result.plan.reference_speed_kmh, 50)
        self.assertEqual(
            result.plan.inflow_veh_per_hour_per_lane, self.source.plan.inflow_veh_per_hour_per_lane
        )
        self.assertEqual(result.demand.planned_vehicles, self.source.demand.planned_vehicles)
        self.assertTrue(any("不会更改发车数量或车辆限速" in note for note in result.notes))
        self.assertNotIn("发车流量由目标密度 × 参考速度推算", result.plan.defaults_used)

    def test_nonfinite_and_coerced_numbers_are_rejected_by_typed_revision(self):
        for value in (float("nan"), float("inf"), -float("inf"), "30", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScenarioRevision(desired_density_veh_per_km_lane=value)

    def test_autonomy_uses_requested_shares_not_rounded_previous_vehicle_shares(self):
        self.source = self._source(autonomy=True)
        self.assertAlmostEqual(self.source.demand.automation_mix[0].share_percent, 200 / 3)
        result = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioCountRevision(vehicle_count=10)
        )
        mix = {item.level: item.vehicle_count for item in result.demand.automation_mix}
        self.assertEqual(mix["L0"], 7)
        self.assertEqual(mix["L3"], 3)
        xml = ET.parse(self.workspace / result.task_id / "output/traffic.rou.xml").getroot()
        self.assertEqual(
            Counter(v.attrib["id"].split("_")[1] for v in xml.findall("vehicle")),
            {"L0": 7, "L3": 3},
        )
        repeated = scenario_revision.revise_scenario(
            result.task_id, ScenarioCountRevision(vehicle_count=10)
        )
        self.assertEqual(
            (self.workspace / repeated.task_id / "output/traffic.rou.xml").read_bytes(),
            (self.workspace / result.task_id / "output/traffic.rou.xml").read_bytes(),
        )
        self.assertEqual(repeated.parent_task_id, result.task_id)

    def test_published_source_is_immutable_and_new_publication_retains_provenance(self):
        old = scenario_catalog.publish_scenario(self.source, self.source_dir)
        old_directory = self.catalog / old.map_id
        before = self._snapshot(old_directory)
        result = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioCountRevision(vehicle_count=51)
        )
        self.assertEqual(len(scenario_catalog.list_scenarios()), 1)
        new = scenario_catalog.publish_existing(result.task_id)
        self.assertEqual(new.settings.planned_vehicles, 51)
        self.assertEqual(self._snapshot(old_directory), before)
        manifest = json.loads((self.catalog / new.map_id / "scenario.manifest.json").read_text())
        definition = manifest["spec"]["definition"]
        self.assertEqual(definition["parent_task_id"], SOURCE_ID)
        self.assertEqual(
            definition["manual_overrides"], {"vehicle_count": 51, "demand_mode": "count"}
        )
        self.assertEqual(definition["intent"], self.source.intent.model_dump(mode="json"))

    def test_edit_does_not_call_model_map_generation_publication_or_simulation(self):
        with (
            patch("agent.interpret_map_request") as model,
            patch("realmap.generate_real_map") as realmap,
            patch("review_pipeline.generate_checked") as roadgen,
            patch("subprocess.run") as run,
            patch("scenario_catalog.publish_scenario") as publish,
        ):
            scenario_revision.revise_scenario(SOURCE_ID, ScenarioCountRevision(vehicle_count=9))
        for mock in (model, realmap, roadgen, run, publish):
            mock.assert_not_called()

    def test_failed_rebuild_leaves_no_task_or_changed_original(self):
        before = self._snapshot(self.source_dir)
        with (
            patch("scenario_revision.write_simulation_config", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            scenario_revision.revise_scenario(SOURCE_ID, ScenarioCountRevision(vehicle_count=9))
        self.assertEqual(list(self.workspace.iterdir()), [self.source_dir.parent])
        self.assertEqual(self._snapshot(self.source_dir), before)
        self.assertFalse(self.catalog.exists())

    def test_count_is_a_strict_bounded_integer_at_http_boundary(self):
        client = TestClient(main.app)
        for count in (True, False, 0, 20001, -1, 2.5, "100", None):
            with self.subTest(count=count):
                response = client.post(
                    f"/scenarios/{SOURCE_ID}/revise", json={"vehicle_count": count}
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(
            client.post(
                f"/scenarios/{SOURCE_ID}/revise", json={"vehicle_count": 10, "unknown": 7}
            ).status_code,
            422,
        )
        for count in (1, 20000):
            response = client.post(f"/scenarios/{SOURCE_ID}/revise", json={"vehicle_count": count})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["demand"]["planned_vehicles"], count)

    def test_http_preserves_meaningful_failure_and_safe_io_error(self):
        client = TestClient(main.app)
        response = client.post("/scenarios/invalid/revise", json={"vehicle_count": 5})
        self.assertEqual(response.status_code, 400)
        self.assertIn("任务 ID", response.json()["detail"])
        (self.source_dir / "map_preview.png").write_bytes(b"broken PNG")
        response = client.post(f"/scenarios/{SOURCE_ID}/revise", json={"vehicle_count": 5})
        self.assertEqual(response.status_code, 400)
        self.assertIn("地图预览已损坏", response.json()["detail"])
        with patch(
            "scenario_revision.revise_scenario", side_effect=PermissionError("private path")
        ):
            response = client.post(f"/scenarios/{SOURCE_ID}/revise", json={"vehicle_count": 5})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private path", response.json()["detail"])

    def test_unsafe_paths_mismatched_identity_missing_and_trial_sources_are_rejected(self):
        for task_id in ("../outside", "", SOURCE_ID + "/../other"):
            with self.subTest(task_id=task_id), self.assertRaisesRegex(ValueError, "任务 ID"):
                scenario_revision.revise_scenario(task_id, ScenarioCountRevision(vehicle_count=5))
        with self.assertRaisesRegex(ValueError, "缺少 scenario.json"):
            scenario_revision.revise_scenario(
                "20261104_200000_abcdef", ScenarioCountRevision(vehicle_count=5)
            )
        for updates, message in (
            ({"task_id": "20261104_200000_abcdef"}, "不一致"),
            ({"status": "success"}, "尚未试运行"),
        ):
            source = self.source.model_copy(update=updates)
            (self.source_dir / "scenario.json").write_text(
                source.model_dump_json(), encoding="utf-8"
            )
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, message):
                scenario_revision.revise_scenario(SOURCE_ID, ScenarioCountRevision(vehicle_count=5))

    def test_source_step_length_is_preserved_when_global_default_changes(self):
        with patch.object(config, "SCENARIO", ScenarioSettings(step_length_s=0.1)):
            result = scenario_revision.revise_scenario(
                SOURCE_ID, ScenarioCountRevision(vehicle_count=5)
            )
        xml = ET.parse(self.workspace / result.task_id / "output/scenario.sumocfg").getroot()
        self.assertEqual(xml.find("time/step-length").attrib["value"], "0.5")

    def test_corrupt_source_files_fail_without_exposing_a_new_task(self):
        for filename, reason in (
            ("scenario.json", "元数据损坏"),
            ("scenario.sumocfg", "步长配置"),
            ("network.net.xml", "路网 XML 已损坏"),
            ("source_map_metadata.json", "元数据损坏"),
        ):
            path = self.source_dir / filename
            original = path.read_bytes()
            path.write_bytes(b"corrupt")
            try:
                with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, reason):
                    scenario_revision.revise_scenario(
                        SOURCE_ID, ScenarioCountRevision(vehicle_count=10)
                    )
                self.assertEqual(list(self.workspace.iterdir()), [self.source_dir.parent])
            finally:
                path.write_bytes(original)

    def test_symlinked_source_files_cannot_escape_the_task_output(self):
        path = self.source_dir / "network.net.xml"
        outside = self.root / "outside.net.xml"
        path.rename(outside)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("This host cannot create symlinks")
        with self.assertRaisesRegex(ValueError, "不在允许的任务目录"):
            scenario_revision.revise_scenario(SOURCE_ID, ScenarioCountRevision(vehicle_count=10))
        self.assertEqual(list(self.workspace.iterdir()), [self.source_dir.parent])

    def test_observed_output_files_are_never_copied_into_revision(self):
        for filename in (
            "summary.xml",
            "tripinfo.xml",
            "statistics.xml",
            "lane_metrics.xml",
            "simulation.log",
        ):
            (self.source_dir / filename).write_text("old observation", encoding="utf-8")
        result = scenario_revision.revise_scenario(
            SOURCE_ID, ScenarioCountRevision(vehicle_count=5)
        )
        self.assertFalse(
            any("summary.xml" in name or "simulation.log" in name for name in result.artifacts)
        )
        self.assertEqual(
            set(result.artifacts),
            {
                "output/network.net.xml",
                "output/map_preview.png",
                "output/source_map_metadata.json",
                "output/traffic.rou.xml",
                "output/scenario.sumocfg",
                "output/metrics.add.xml",
                "output/scenario.json",
            },
        )


if __name__ == "__main__":
    unittest.main()
