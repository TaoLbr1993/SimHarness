"""Check that English studio presentation preserves user content and navigation."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication
from ui.api_client import RealtimeClient, RestApiClient
from ui.models import WorkspaceSummary
from ui.scene_language import normalize_language
from ui.viewmodels import RunViewModel
from ui.views.main_window import MainWindow
from ui.views.navigation import NavigationRail


def test_studio_navigation_switch_preserves_workspace_name_and_active_page() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    navigation = NavigationRail()
    navigation.set_workspace("北京真实路网测试")
    navigation.set_active("generate")
    navigation.set_scene_language("en")
    assert navigation._buttons["generate"].text() == "TrafficForge"
    assert navigation._buttons["generate"].accessibleName() == "TrafficForge"
    assert navigation._buttons["generate"].property("active")
    assert navigation.workspace_name.text() == "北京真实路网测试"
    navigation.set_scene_language("zh-CN")
    assert navigation._buttons["generate"].text() == "TrafficForge"
    assert navigation._buttons["generate"].accessibleName() == "TrafficForge"
    assert navigation.workspace_name.text() == "北京真实路网测试"
    assert navigation._buttons["generate"].property("active")


def test_language_validation_keeps_unknown_locales_explicit() -> None:
    assert normalize_language("en-US") == "en"
    assert normalize_language("zh_CN") == "zh"
    with pytest.raises(ValueError):
        normalize_language("invalid")


@pytest.mark.parametrize("navigation_language", ["en", "zh"])
def test_studio_language_and_page_changes_do_not_translate_other_navigation(
    navigation_language: str,
) -> None:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    original_font = app.font()
    viewmodel = RunViewModel(
        RestApiClient("http://127.0.0.1:8000"),
        RealtimeClient("http://127.0.0.1:8000"),
        UUID(int=42),
    )
    window = MainWindow(viewmodel, load_web_map=False, traffic_mode=True)
    workspace = WorkspaceSummary(
        workspace_id=UUID(int=1),
        name="Language regression",
        created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    viewmodel._workspaces = (workspace,)
    viewmodel._active_workspace_id = workspace.workspace_id
    window.navigation.set_scene_language(navigation_language)
    expected = {
        key: (button.text(), button.accessibleName())
        for key, button in window.navigation._buttons.items()
    }
    window.generation_page.description.setText("Keep this scene description")
    try:
        for studio_language in ("en", "zh", "en"):
            window.generation_page.set_language(studio_language)
            for page in ("generate", "scene", "agent_connection", "agents", "live"):
                window._show_page(page)
                assert {
                    key: (button.text(), button.accessibleName())
                    for key, button in window.navigation._buttons.items()
                } == expected
                assert window.generation_page.language == studio_language
                assert window.generation_page.description.text() == "Keep this scene description"
        assert viewmodel._scene_language == "en"
    finally:
        window.close()
        app.setFont(original_font)
    assert app is not None
