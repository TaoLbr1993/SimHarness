"""Exercise the shipped offline map bundle in a real Qt WebEngine page."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
from ui.localization import language, set_language
from ui.scene_language import Language
from ui.views.scene_configuration_page import SceneConfigurationPage

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def application() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _wait_until(predicate: Callable[[], bool], timeout_ms: int = 8_000) -> None:
    loop = QEventLoop()
    poll = QTimer()
    deadline = QTimer()
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start(20)
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate(), "Map did not become ready; inspect WebEngine console errors"


def _javascript(widget: SceneConfigurationPage, source: str) -> str:
    result: list[str] = []
    widget.map_widget.page().runJavaScript(source, lambda value: result.append(str(value)))
    _wait_until(lambda: bool(result))
    return result[0]


@pytest.mark.parametrize("selected_language", ["en", "zh"])
def test_setup_preview_loads_queued_network_and_map_controls(
    selected_language: Language, tmp_path: Path, application: QApplication
) -> None:
    previous_language = language()
    set_language(selected_language)
    stack = QStackedWidget()
    stack.resize(1200, 900)
    stack.addWidget(QWidget())
    page = SceneConfigurationPage(load_web_map=True, traffic_mode=True)
    stack.addWidget(page)
    page.map_widget.set_traffic_presentation(True)
    page.set_preview_network(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"trafficverse_role": "sumo_lane", "width_m": 3.2},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [250, 180]]},
                }
            ],
        }
    )
    try:
        stack.show()
        stack.setCurrentWidget(page)
        _wait_until(lambda: page.map_widget._ready)
        _wait_until(lambda: not page.map_widget._pending)
        state = json.loads(
            _javascript(
                page,
                """JSON.stringify({
            status: document.getElementById('map-status').textContent,
            controls: document.querySelectorAll('.maplibregl-ctrl button').length,
            width: document.querySelector('.maplibregl-canvas').clientWidth,
            height: document.querySelector('.maplibregl-canvas').clientHeight
        })""",
            )
        )
        assert ("Lanes 1" if selected_language == "en" else "车道 1") in state["status"]
        assert state["controls"] >= 4
        assert state["width"] > 100 and state["height"] >= 410
        for maximized in (True, False):
            page.map_widget.set_maximized(maximized)
            title = _javascript(
                page,
                """
                document.querySelector('.trafficverse-maximize-control button').title
            """,
            )
            expected = (
                ("Restore map" if maximized else "Maximize map")
                if selected_language == "en"
                else ("还原地图" if maximized else "最大化地图")
            )
            assert title == expected
        assert page.map_widget.grab().save(str(tmp_path / f"map-{selected_language}.png"))
    finally:
        stack.close()
        stack.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        set_language(previous_language)
