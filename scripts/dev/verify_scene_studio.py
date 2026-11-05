"""Exercise the real Qt draft/confirm workflow against local services, without a run."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.viewmodels import RunViewModel
from ui.views import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("traffic", "autonomy"), default="traffic")
    parser.add_argument("--description", required=True)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = repo / "artifacts/scene-studio-stage" / args.mode
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    rest = RestApiClient("http://127.0.0.1:8000")
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42))
    window = MainWindow(vm, traffic_mode=args.mode == "traffic")
    window.resize(1480, 1040)
    page = window.generation_page
    state: dict = {"mode": args.mode, "started": False, "finished": False, "operations": []}

    def get_json(url: str) -> object:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)

    def finish(error: str | None = None) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["error"] = error
        state["experiment_id"] = str(vm.experiment_id) if vm.experiment_id else None
        (output / "result.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in state.items() if k != "operations"}, ensure_ascii=False))
        app.exit(1 if error else 0)

    def ready() -> None:
        if (
            state["started"]
            or vm.active_workspace is None
            or not window.scene_page.map_combo.count()
        ):
            return
        state["started"] = True
        window._show_page("generate")
        QTimer.singleShot(350, start)

    def start() -> None:
        window.grab().save(str(output / "landing.png"))
        page.description.setText(args.description)
        page.generate_button.click()

    def workspaces(items: tuple) -> None:
        if vm.active_workspace is None and items:
            vm.select_workspace(str(items[0].workspace_id))
            vm.enter_selected_workspace()
        QTimer.singleShot(0, ready)

    def drafted(payload: dict) -> None:
        state["task_id"] = payload["task_id"]
        state["draft_received"] = True
        state["planned_vehicles"] = payload["demand"]["planned_vehicles"]
        state["automation_mix"] = payload["demand"].get("automation_mix", [])
        (output / "draft.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if payload["catalog_map_id"] is not None or payload.get("metrics") is not None:
            finish("Draft was published or contains measured metrics")

    def preview_loaded(data: bytes) -> None:
        if not state.get("draft_received"):
            finish("Image arrived before draft")
            return
        QTimer.singleShot(400, inspect_preview)

    def inspect_preview() -> None:
        try:
            task_id = state["task_id"]
            map_id = "nl2-" + task_id
            assert page.publish_button.isEnabled(), "Preview cannot be confirmed"
            assert vm.experiment_id is None, "Generation created an experiment"
            assert not (repo / "configs/maps" / map_id).exists(), "Preview prematurely published"
            scenarios = get_json("http://127.0.0.1:8001/scenarios")
            maps = get_json("http://127.0.0.1:8000/api/v1/maps")
            assert isinstance(scenarios, list) and not any(s["map_id"] == map_id for s in scenarios)
            assert isinstance(maps, list) and not any(m["map_id"] == map_id for m in maps)
            route_path = repo / "trafficforge/workspaces" / task_id / "output/traffic.rou.xml"
            if not route_path.exists():
                route_path = (
                    repo / "trafficforge/backend/workspaces" / task_id / "output/traffic.rou.xml"
                )
            root = ET.parse(route_path).getroot()
            vehicles = root.findall("vehicle")
            assert len(vehicles) == state["planned_vehicles"]
            if args.mode == "autonomy":
                counts = Counter(vehicle.attrib["id"].split("_")[1] for vehicle in vehicles)
                assert sum(item["vehicle_count"] for item in state["automation_mix"]) == len(
                    vehicles
                )
                assert all(
                    counts[item["level"]] == item["vehicle_count"]
                    for item in state["automation_mix"]
                )
            state["preview_absent_from_catalog"] = True
            state["xml_vehicle_count_matches"] = True
            state["preview_loaded_bytes"] = len(data_snapshot[0])
            window.grab().save(str(output / "preview.png"))
            if args.no_publish:
                finish()
            else:
                page.publish_button.click()
        except Exception as exc:
            finish(str(exc))

    def selected(map_id: str) -> None:
        try:
            state["published_map_id"] = map_id
            assert map_id == "nl2-" + state["task_id"]
            assert (repo / "configs/maps" / map_id).is_dir()
            assert vm.experiment_id is None
            state["published_visible_in_catalog"] = True
            QTimer.singleShot(400, inspect_configuration)
        except Exception as exc:
            finish(str(exc))

    def inspect_configuration() -> None:
        try:
            window.grab().save(str(output / "confirmed.png"))
            page.configure_button.click()
            assert window.page_stack.currentWidget() is window.scene_page
            draft = window.scene_page.current_configuration()
            assert draft.map_id == state["published_map_id"] and not draft.automation_demands
            assert not window.scene_page.duration_time.isEnabled()
            assert vm.experiment_id is None
            state["configuration_ready"] = True
            QTimer.singleShot(800, finish)
        except Exception as exc:
            finish(str(exc))

    def operation(name: str, payload: object) -> None:
        state["operations"].append(name)
        if name.startswith("experiment.") or name == "simulation-configuration.save":
            finish("Preview/confirm unexpectedly created a run or saved configuration")

    data_snapshot: list[bytes] = []
    vm.workspace_catalog_changed.connect(workspaces)
    vm.map_catalog_changed.connect(lambda _: QTimer.singleShot(0, ready))
    vm.scene_draft_ready.connect(drafted)
    vm.scene_preview_ready.connect(lambda data: data_snapshot.append(data))
    vm.scene_preview_ready.connect(preview_loaded)
    vm.scene_preview_failed.connect(finish)
    vm.generated_scene_selected.connect(selected)
    vm.generation_state_changed.connect(
        lambda busy, message: (
            finish(message)
            if state["started"] and not busy and not state.get("draft_received")
            else None
        )
    )
    vm.publication_state_changed.connect(
        lambda busy, message: (
            finish(message)
            if not busy and not state.get("published_map_id") and not args.no_publish
            else None
        )
    )
    rest.request_succeeded.connect(operation)
    rest.request_failed.connect(lambda name, reason: finish(name + ": " + reason))
    window.show()
    QTimer.singleShot(0, vm.initialize)
    QTimer.singleShot(600_000, lambda: finish("Qt integration timed out"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
