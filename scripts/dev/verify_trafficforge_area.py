"""Exercise the live map picker and draft preview without publishing or simulating."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.viewmodels import RunViewModel
from ui.views import MainWindow
from ui.widgets.map_area_dialog import MapAreaDialog


def wait_for(condition: Callable[[], bool], *, timeout_s: float, name: str) -> None:
    """Allow real network and Chromium events to complete during this live-only check."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        QTest.qWait(100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/trafficforge-area"))
    args = parser.parse_args()
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    rest = RestApiClient("http://127.0.0.1:8000")
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42))
    window = MainWindow(vm, traffic_mode=True, scene_language="en")
    window.resize(1480, 1040)
    window.show()
    page = window.generation_page
    report: dict[str, object] = {}
    failure: list[str] = []

    def interact_with_picker() -> None:
        dialog = app.activeModalWidget()
        if not isinstance(dialog, MapAreaDialog):
            failure.append("Area button did not open the picker")
            return
        try:
            wait_for(
                lambda: dialog._ready and dialog._config is not None and dialog._tiles_ready,
                timeout_s=90,
                name="Map tiles or selection service did not become ready",
            )
            dialog.search_input.setText("北京航空航天大学学院路校区")
            dialog.search_button.click()
            wait_for(
                lambda: dialog.search_button.isEnabled(),
                timeout_s=100,
                name="Location search did not finish",
            )
            if not dialog._places:
                raise RuntimeError(dialog.lookup_status.text())
            report["search_candidates"] = [place.model_dump() for place in dialog._places]
            dialog.place_list.setCurrentRow(0)
            QTest.qWait(500)
            wait_for(
                lambda: dialog._tiles_ready, timeout_s=90, name="Located map tiles unavailable"
            )
            dialog.draw_button.click()
            QTest.qWait(200)
            target = dialog.web.focusProxy() or dialog.web
            start = QPoint(int(target.width() * 0.27), int(target.height() * 0.20))
            end = QPoint(int(target.width() * 0.73), int(target.height() * 0.80))
            QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(target, end, delay=200)
            QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=end, delay=200)
            wait_for(
                lambda: dialog.confirm_button.isEnabled(),
                timeout_s=10,
                name="Drawing did not produce a valid area",
            )
            bounds = dialog.selected_bounds
            assert bounds is not None
            report["selected_bounds"] = bounds.model_dump()
            report["width_m"] = bounds.width_m
            report["height_m"] = bounds.height_m
            report["diagonal_m"] = bounds.diagonal_m
            QTest.qWait(250)
            dialog.grab().save(str(output / "picker.png"))
            dialog.confirm_button.click()
        except Exception as exc:
            failure.append(str(exc))
            report["map_status"] = dialog.map_status.text()
            report["config_status"] = dialog.config_status.text()
            report["selection_status"] = dialog.selection_label.text()
            dialog.grab().save(str(output / "picker-failure.png"))
            dialog.reject()

    try:
        vm.initialize()
        wait_for(lambda: bool(vm._workspaces), timeout_s=30, name="No workspace loaded")
        vm.select_workspace(str(vm._workspaces[0].workspace_id))
        vm.enter_selected_workspace()
        wait_for(
            lambda: window.scene_page.map_combo.count() > 0,
            timeout_s=30,
            name="Map catalog did not load",
        )
        window._show_page("generate")
        QTest.qWait(350)
        window.grab().save(str(output / "entry.png"))
        QTimer.singleShot(250, interact_with_picker)
        page.area_button.click()
        if failure:
            raise RuntimeError(failure[0])
        assert page.selected_bounds is not None
        assert page.description.text() == ""
        assert page.results.isHidden()
        report["entry_still_simple_after_selection"] = True
        window.grab().save(str(output / "selected.png"))
        page.generate_button.click()
        wait_for(lambda: not vm._generation_busy, timeout_s=600, name="Scene generation timed out")
        if not page.error_card.isHidden():
            raise RuntimeError(page.error_message.text())
        wait_for(lambda: page._image_ready, timeout_s=60, name="Preview image did not load")
        assert page._draft is not None
        report["task_id"] = page._draft["task_id"]
        report["generated_bounds"] = page._draft.get("selected_bounds")
        report["planned_vehicles"] = (page._draft.get("demand") or {}).get("planned_vehicles")
        report["publish_enabled"] = page.publish_button.isEnabled()
        report["experiment_created"] = vm.experiment_id is not None
        assert report["selected_bounds"] == report["generated_bounds"]
        assert report["publish_enabled"]
        assert not report["experiment_created"]
        assert page._draft.get("catalog_map_id") is None
        (output / "draft.json").write_text(
            json.dumps(page._draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        QTest.qWait(300)
        window.grab().save(str(output / "preview.png"))
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
    finally:
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.processEvents()
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
