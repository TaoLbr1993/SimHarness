"""Open TrafficForge at its simple entry, in English by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.models import WorkspaceSummary
from ui.scene_language import normalize_language
from ui.viewmodels import RunViewModel
from ui.views import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "zh"), default="en")
    parser.add_argument("--mode", choices=("traffic", "autonomy"), default="traffic")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--capture-dir", type=Path, help="Save an entry screenshot for local checks"
    )
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    rest = RestApiClient(args.api_url)
    vm = RunViewModel(rest, RealtimeClient(rest.base_url), UUID(int=42))
    window = MainWindow(
        vm, traffic_mode=args.mode == "traffic", scene_language=normalize_language(args.language)
    )
    window.resize(1480, 1040)
    opened = False

    def capture() -> None:
        if args.capture_dir is None:
            return
        args.capture_dir.mkdir(parents=True, exist_ok=True)
        page = window.generation_page
        state = {
            "language": page.language,
            "description_empty": not page.description.text(),
            "results_hidden": page.results.isHidden(),
            "examples_visible": page.examples.isVisible(),
            "generate_enabled": page.generate_button.isEnabled(),
            "experiment_created": vm.experiment_id is not None,
            "window_title": window.windowTitle(),
        }
        (args.capture_dir / "entry-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        window.grab().save(str(args.capture_dir / "entry.png"))

    def ready() -> None:
        nonlocal opened
        if opened or vm.active_workspace is None or not window.scene_page.map_combo.count():
            return
        opened = True
        window._show_page("generate")
        window.generation_page.description.setFocus()
        window.raise_()
        window.activateWindow()
        QTimer.singleShot(350, capture)

    def workspaces(items: tuple[WorkspaceSummary, ...]) -> None:
        if vm.active_workspace is None and items:
            vm.select_workspace(str(items[0].workspace_id))
            vm.enter_selected_workspace()
        QTimer.singleShot(0, ready)

    vm.workspace_catalog_changed.connect(workspaces)
    vm.map_catalog_changed.connect(lambda _: QTimer.singleShot(0, ready))
    window.show()
    QTimer.singleShot(0, vm.initialize)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
