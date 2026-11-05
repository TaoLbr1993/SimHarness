"""Verify real Qt count edits, image zoom and publication using an existing local draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.api_client.scenario_generator import GenerationResponse, ScenarioGeneratorClient
from ui.viewmodels import RunViewModel
from ui.views import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_task_id")
    parser.add_argument("vehicle_count", type=int)
    parser.add_argument("--mode", choices=("traffic", "autonomy"), default="traffic")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = repo / "artifacts/scene-preview-edit-stage" / args.mode
    output.mkdir(parents=True, exist_ok=True)
    source = repo / "trafficforge/backend/workspaces" / args.source_task_id / "output"
    source_files = [path for path in source.iterdir() if path.is_file()]
    package = repo / "configs/maps" / ("nl2-" + args.source_task_id)
    if package.is_dir():
        source_files.extend(path for path in package.iterdir() if path.is_file())
    original_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}

    def get_json(url: str) -> object:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)

    original = GenerationResponse.model_validate(
        get_json(f"http://127.0.0.1:8001/artifacts/{args.source_task_id}/scenario.json")
    )
    app = QApplication(sys.argv[:1])
    rest = RestApiClient("http://127.0.0.1:8000")
    client = ScenarioGeneratorClient()
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42), generator=client)
    window = MainWindow(vm, traffic_mode=args.mode == "traffic")
    window.resize(1480, 1040)
    page = window.generation_page
    state: dict = {
        "mode": args.mode,
        "source": args.source_task_id,
        "phase": "initial",
        "operations": [],
    }

    def finish(error: str | None = None) -> None:
        if state.get("finished"):
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
            state["phase"] != "initial"
            or vm.active_workspace is None
            or not window.scene_page.map_combo.count()
        ):
            return
        state["phase"] = "source"
        window._show_page("generate")
        page.description.setText(original.description)
        # Preload a real saved response to isolate editing from another paid model generation.
        client._draft = original
        client.draft_ready.emit(original.model_dump(mode="json"))
        client.load_preview(original.task_id)

    def workspaces(items: tuple) -> None:
        if vm.active_workspace is None and items:
            vm.select_workspace(str(items[0].workspace_id))
            vm.enter_selected_workspace()
        QTimer.singleShot(0, ready)

    def draft_received(payload: dict) -> None:
        if state["phase"] == "editing":
            try:
                assert payload["task_id"] != original.task_id
                assert payload["parent_task_id"] == original.task_id
                assert payload["manual_overrides"] == {"vehicle_count": args.vehicle_count}
                assert payload["demand"]["planned_vehicles"] == args.vehicle_count
                assert payload["catalog_map_id"] is None and payload["metrics"] is None
                state["phase"] = "revised"
                state["task_id"] = payload["task_id"]
                state["automation_mix"] = payload["demand"].get("automation_mix", [])
                (output / "revised.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as exc:
                finish(str(exc))

    def image_ready(data: bytes) -> None:
        if state["phase"] == "source":
            QTimer.singleShot(300, zoom_and_edit)
        elif state["phase"] == "revised":
            QTimer.singleShot(300, inspect_revision)

    def zoom_and_edit() -> None:
        try:
            page.zoom_button.click()
            dialog = page._image_dialog
            assert dialog is not None and dialog.isVisible()
            dialog.original_button.click()
            dialog.zoom_in_button.click()
            assert dialog.zoom_factor > 1
            state["zoom_factor"] = dialog.zoom_factor
            QTimer.singleShot(200, capture_zoom)
        except Exception as exc:
            finish(str(exc))

    def capture_zoom() -> None:
        try:
            dialog = page._image_dialog
            assert dialog is not None
            dialog.grab().save(str(output / "map-zoom.png"))
            dialog.close()
            editor = page.vehicle_count_input.lineEdit()
            editor.selectAll()
            QTest.keyClicks(editor, str(args.vehicle_count))
            assert page.vehicle_count_input.value() == args.vehicle_count
            assert not page.publish_button.isEnabled()
            assert page.apply_count_button.isEnabled()
            state["dirty_blocks_confirmation"] = True
            window.grab().save(str(output / "editing.png"))
            state["phase"] = "editing"
            page.apply_count_button.click()
        except Exception as exc:
            finish(str(exc))

    def inspect_revision() -> None:
        try:
            changed = repo / "trafficforge/backend/workspaces" / state["task_id"] / "output"
            vehicles = ET.parse(changed / "traffic.rou.xml").getroot().findall("vehicle")
            assert len(vehicles) == args.vehicle_count
            assert (changed / "network.net.xml").read_bytes() == (
                source / "network.net.xml"
            ).read_bytes()
            assert (changed / "map_preview.png").read_bytes() == (
                source / "map_preview.png"
            ).read_bytes()
            assert all(
                hashlib.sha256(path.read_bytes()).hexdigest() == value
                for path, value in original_hashes.items()
            )
            assert not (repo / "configs/maps" / ("nl2-" + state["task_id"])).exists()
            assert not any(
                (changed / name).exists()
                for name in ("tripinfo.xml", "summary.xml", "statistics.xml")
            )
            if args.mode == "autonomy":
                counts = Counter(vehicle.attrib["id"].split("_")[1] for vehicle in vehicles)
                assert all(
                    counts[item["level"]] == item["vehicle_count"]
                    for item in state["automation_mix"]
                )
            assert vm.experiment_id is None
            assert page.publish_button.isEnabled()
            state["xml_vehicle_count"] = len(vehicles)
            state["original_inputs_unchanged"] = True
            state["new_draft_unpublished"] = True
            window.grab().save(str(output / "revised-preview.png"))
            if args.no_publish:
                finish()
            else:
                page.publish_button.click()
        except Exception as exc:
            finish(str(exc))

    def published(map_id: str) -> None:
        try:
            assert map_id == "nl2-" + state["task_id"]
            package = repo / "configs/maps" / map_id
            vehicles = ET.parse(package / "traffic.rou.xml").getroot().findall("vehicle")
            metadata = json.loads((package / "scenario.manifest.json").read_text(encoding="utf-8"))
            assert (
                len(vehicles)
                == metadata["spec"].get("trafficforge", metadata["spec"].get("nl2map", {}))[
                    "planned_vehicles"
                ]
                == args.vehicle_count
            )
            assert metadata["spec"]["definition"]["parent_task_id"] == original.task_id
            assert all(
                hashlib.sha256(path.read_bytes()).hexdigest() == value
                for path, value in original_hashes.items()
            )
            assert vm.experiment_id is None
            state["published_map_id"] = map_id
            state["published_quantity_matches"] = True
            QTimer.singleShot(300, finish)
        except Exception as exc:
            finish(str(exc))

    def operation(name: str, payload: object) -> None:
        state["operations"].append(name)
        if name.startswith("experiment."):
            finish("Editing unexpectedly started an experiment")

    vm.workspace_catalog_changed.connect(workspaces)
    vm.map_catalog_changed.connect(lambda _: QTimer.singleShot(0, ready))
    vm.scene_draft_ready.connect(draft_received)
    vm.scene_preview_ready.connect(image_ready)
    vm.scene_preview_failed.connect(finish)
    vm.generated_scene_selected.connect(published)
    client.revision_failed.connect(finish)
    client.publication_failed.connect(finish)
    rest.request_succeeded.connect(operation)
    rest.request_failed.connect(lambda name, reason: finish(name + ": " + reason))
    window.show()
    QTimer.singleShot(0, vm.initialize)
    QTimer.singleShot(120_000, lambda: finish("Revision integration timeout"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
