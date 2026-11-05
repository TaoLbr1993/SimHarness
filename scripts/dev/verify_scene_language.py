"""Exercise English generation and in-place language switching without publishing or running."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.api_client.scenario_generator import ScenarioGeneratorClient
from ui.models import WorkspaceSummary
from ui.viewmodels import RunViewModel
from ui.views import MainWindow


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    output = root / "artifacts/scene-language-live"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    rest = RestApiClient("http://127.0.0.1:8000")
    generator = ScenarioGeneratorClient()
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42), generator=generator)
    window = MainWindow(vm, load_web_map=False, traffic_mode=True)
    window.resize(1480, 1040)
    page = window.generation_page
    description = (
        "Generate roads within 1 km of Beihang University's Xueyuan Road campus "
        "for a congestion test with 120 vehicles over 10 minutes."
    )
    opened = False
    finished = False
    phase = 0
    task_id = ""
    revised_count = 0
    images: list[bytes] = []

    def finish(error: str | None = None) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        report = {
            "error": error,
            "task_id": task_id,
            "language_roundtrip": phase == 2 and error is None,
            "localized_images_differ": len(images) >= 2 and images[0] != images[1],
            "original_input_preserved": page.description.text() == description,
            "experiment_created": vm.experiment_id is not None,
            "published": bool(page._published_map_id),
        }
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.close()
        app.exit(1 if error is not None else 0)

    def ready() -> None:
        nonlocal opened
        if opened or vm.active_workspace is None or not window.scene_page.map_combo.count():
            return
        opened = True
        window._show_page("generate")
        QTimer.singleShot(400, generate)

    def generate() -> None:
        try:
            assert page.language == "en" and page.results.isHidden()
            assert page.generate_button.text() == "Generate preview  →"
            window.grab().save(str(output / "entry.en.png"))
            page.description.setText(description)
            page.generate_button.click()
        except Exception:
            finish(traceback.format_exc())

    def preview(data: bytes) -> None:
        images.append(data)
        QTimer.singleShot(400, inspect)

    def inspect() -> None:
        nonlocal phase, task_id, revised_count
        if finished:
            return
        try:
            assert vm.experiment_id is None
            assert page.description.text() == description
            assert generator._draft is not None
            if phase == 0:
                task_id = generator._draft.task_id
                assert generator._draft.demand is not None
                assert generator._draft.demand.planned_vehicles == 120
                assert page.publish_button.isEnabled()
                window.grab().save(str(output / "preview.en.png"))
                page._open_image()
                assert page._image_dialog is not None
                page._image_dialog.grab().save(str(output / "zoom.en.png"))
                page._image_dialog.close()
                revised_count = page.vehicle_count_input.value() + 3
                page.vehicle_count_input.setValue(revised_count)
                assert page.settings_editor.is_dirty() and not page.publish_button.isEnabled()
                phase = 1
                page.set_language("zh")
            elif phase == 1:
                assert generator._draft.task_id == task_id
                assert page.language == "zh" and page.settings_editor.is_dirty()
                assert page.vehicle_count_input.value() == revised_count
                assert not page.publish_button.isEnabled()
                window.grab().save(str(output / "preview.zh.png"))
                phase = 2
                page.set_language("en")
            else:
                assert generator._draft.task_id == task_id
                assert page.language == "en" and page.settings_editor.is_dirty()
                assert page.vehicle_count_input.value() == revised_count
                assert not page.publish_button.isEnabled()
                assert images[0] == images[-1] and images[0] != images[1]
                page.cancel_count_button.click()
                assert page.publish_button.isEnabled() and not page.settings_editor.is_dirty()
                window.grab().save(str(output / "preview.en.restored.png"))
                finish()
        except Exception:
            finish(traceback.format_exc())

    def workspaces(items: tuple[WorkspaceSummary, ...]) -> None:
        if vm.active_workspace is None and items:
            vm.select_workspace(str(items[0].workspace_id))
            vm.enter_selected_workspace()
        QTimer.singleShot(0, ready)

    vm.workspace_catalog_changed.connect(workspaces)
    vm.map_catalog_changed.connect(lambda _: QTimer.singleShot(0, ready))
    vm.scene_preview_ready.connect(preview)
    vm.scene_generation_failed.connect(finish)
    vm.scene_preview_failed.connect(finish)
    window.show()
    QTimer.singleShot(0, vm.initialize)
    QTimer.singleShot(240000, lambda: finish("Timed out while verifying language switching"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
