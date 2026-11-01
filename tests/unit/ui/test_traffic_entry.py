from __future__ import annotations

from uuid import UUID

from PySide6.QtWidgets import QApplication, QWidget
from ui.api_client import RealtimeClient, RestApiClient
from ui.viewmodels import RunViewModel
from ui.views.main_window import MainWindow

from trafficverse.cli import _build_parser, _run_ui


def test_entry_dispatches_to_the_requested_presentation(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "ui.app.main.run", lambda *args, **kwargs: calls.append((args, kwargs)) or 0
    )
    for command in ("ui", "traffic-ui"):
        args = _build_parser().parse_args([command])
        assert _run_ui(args) == 0
    assert [kwargs["traffic_mode"] for _, kwargs in calls] == [False, True]


def test_general_window_sets_map_presentation_and_hides_scripted_scenarios() -> None:
    application = QApplication.instance() or QApplication([])
    viewmodel = RunViewModel(
        RestApiClient("http://127.0.0.1:8000"),
        RealtimeClient("http://127.0.0.1:8000"),
        UUID("00000000-0000-0000-0000-000000000042"),
    )
    window = MainWindow(viewmodel, load_web_map=False, traffic_mode=True)
    navigation = window.navigation.findChild(QWidget, "navContainer_traffic_scenes")
    assert navigation is not None and navigation.isHidden()
    assert window.live_page.map_widget._pending["setTrafficPresentation"] is True
    assert window.scene_page.map_widget._pending["setTrafficPresentation"] is True
    window.close()
    assert application is not None
