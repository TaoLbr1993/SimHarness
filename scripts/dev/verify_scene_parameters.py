"""Verify real Qt parameter edits and resulting SUMO inputs without publishing or running."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.api_client.scenario_generator import GenerationResponse, ScenarioGeneratorClient
from ui.models import WorkspaceSummary
from ui.viewmodels import RunViewModel
from ui.views import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-task", required=True)
    parser.add_argument("--mode", choices=("traffic", "autonomy"), default="traffic")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = root / "artifacts/amap-scene-parameters" / args.mode
    output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        f"http://127.0.0.1:8001/artifacts/{args.source_task}/scenario.json", timeout=10
    ) as response:
        source = GenerationResponse.model_validate(json.load(response))
    source_dir = root / "trafficforge/backend/workspaces" / source.task_id / "output"
    original = {
        name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
        for name in ("network.net.xml", "traffic.rou.xml", "scenario.json")
    }
    app = QApplication(sys.argv[:1])
    rest = RestApiClient("http://127.0.0.1:8000")
    generator = ScenarioGeneratorClient()
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42), generator=generator)
    window = MainWindow(vm, load_web_map=False, traffic_mode=args.mode == "traffic")
    window.resize(1480, 1040)
    page = window.generation_page
    steps: list[dict[str, object]] = [
        {
            "duration_s": 900,
            "warmup_s": 60,
            "seed": 7,
            "desired_density_veh_per_km_lane": 25,
            "reference_speed_kmh": 10,
            "vehicle_count": 300,
        },
        {"inflow_veh_per_hour_per_lane": 100},
        {"total_flow_veh_per_hour": 800},
        {"desired_density_veh_per_km_lane": 10, "reference_speed_kmh": 12},
    ]
    if args.mode == "autonomy":
        steps = [
            {
                "duration_s": 660,
                "warmup_s": 30,
                "seed": 11,
                "vehicle_count": 101,
                "automation_mix": {"L0": 60, "L3": 40},
            }
        ]
    index = -1
    opened = False
    finished = False
    latest: dict = {}
    evidence: list[dict] = []

    def finish(error: str | None = None) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        (output / "report.json").write_text(
            json.dumps(
                {
                    "error": error,
                    "steps": evidence,
                    "source_unchanged": all(
                        hashlib.sha256((source_dir / name).read_bytes()).hexdigest() == digest
                        for name, digest in original.items()
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        window.close()
        app.exit(1 if error is not None else 0)

    def ready() -> None:
        nonlocal opened
        if opened or vm.active_workspace is None or not window.scene_page.map_combo.count():
            return
        opened = True
        window._show_page("generate")
        assert page.results.isHidden()
        window.grab().save(str(output / "entry.png"))
        # Expose numeric inputs before async preview loading lets Qt settle the layout.
        page.settings_editor.advanced_button.setChecked(True)
        page.description.setText(source.description)
        generator._draft = source
        generator.draft_ready.emit(source.model_dump(mode="json"))
        generator.load_preview(source.task_id)

    def workspaces(items: tuple[WorkspaceSummary, ...]) -> None:
        if vm.active_workspace is None and items:
            vm.select_workspace(str(items[0].workspace_id))
            vm.enter_selected_workspace()
        QTimer.singleShot(0, ready)

    def drafted(payload: dict) -> None:
        nonlocal latest
        latest = payload

    def advance() -> None:
        nonlocal index
        if finished:
            return
        try:
            assert page.publish_button.isEnabled()
            assert vm.experiment_id is None
            if index >= 0:
                task_id = latest["task_id"]
                plan, demand = latest["plan"], latest["demand"]
                directory = root / "trafficforge/backend/workspaces" / task_id / "output"
                cfg = ET.parse(directory / "scenario.sumocfg").getroot()
                vehicles = ET.parse(directory / "traffic.rou.xml").getroot().findall("vehicle")
                assert len(vehicles) == demand["planned_vehicles"]
                assert int(cfg.find("time/end").attrib["value"]) == plan["duration_s"]
                assert int(cfg.find("random_number/seed").attrib["value"]) == plan["seed"]
                assert all(float(v.attrib["depart"]) < plan["duration_s"] for v in vehicles)
                assert (
                    hashlib.sha256((directory / "network.net.xml").read_bytes()).hexdigest()
                    == original["network.net.xml"]
                )
                assert not (root / "configs/maps" / f"nl2-{task_id}").exists()
                assert latest.get("metrics") is None
                if plan["demand_mode"] == "count":
                    assert len(vehicles) == plan["requested_vehicle_count"]
                elif plan["demand_mode"] == "total_flow":
                    assert len(vehicles) == math.floor(
                        plan["requested_total_flow_veh_per_hour"] * plan["duration_s"] / 3600
                    )
                else:
                    assert len(vehicles) == math.floor(
                        plan["inflow_veh_per_hour_per_lane"]
                        * demand["origin_lane_count"]
                        * plan["duration_s"]
                        / 3600
                    )
                if args.mode == "autonomy":
                    assert {
                        item["level"]: item["vehicle_count"] for item in demand["automation_mix"]
                    } == {"L0": 61, "L1": 0, "L2": 0, "L3": 40, "L4": 0, "L5": 0}
                evidence.append(
                    {
                        "task_id": task_id,
                        "mode": plan["demand_mode"],
                        "count": len(vehicles),
                        "duration_s": plan["duration_s"],
                        "warmup_s": plan["warmup_s"],
                        "seed": plan["seed"],
                        "xml_verified": True,
                    }
                )
                (output / f"step-{index}.json").write_text(
                    json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                window.grab().save(str(output / f"step-{index}.png"))
            index += 1
            if index == len(steps):
                finish()
                return
            controls = {
                "duration_s": page.duration_input,
                "warmup_s": page.warmup_input,
                "seed": page.seed_input,
                "vehicle_count": page.vehicle_count_input,
                "desired_density_veh_per_km_lane": page.density_input,
                "reference_speed_kmh": page.reference_speed_input,
                "inflow_veh_per_hour_per_lane": page.inflow_input,
                "total_flow_veh_per_hour": page.total_flow_input,
            }
            for name, value in steps[index].items():
                if name == "automation_mix":
                    for level, control in page.automation_inputs.items():
                        control.setValue(value.get(level, 0))
                else:
                    controls[name].setValue(value)
            assert not page.publish_button.isEnabled()
            assert page.apply_count_button.isEnabled(), page.edit_status.text()
            page.apply_count_button.click()
            assert not page.apply_count_button.isEnabled()
        except Exception:
            finish(traceback.format_exc())

    vm.workspace_catalog_changed.connect(workspaces)
    vm.map_catalog_changed.connect(lambda _: QTimer.singleShot(0, ready))
    vm.scene_draft_ready.connect(drafted)
    vm.scene_preview_ready.connect(lambda _: QTimer.singleShot(250, advance))
    generator.revision_failed.connect(finish)
    window.show()
    QTimer.singleShot(0, vm.initialize)
    QTimer.singleShot(120000, lambda: finish("Timed out waiting for scene revision"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
