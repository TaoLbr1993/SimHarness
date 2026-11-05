"""Check that English studio presentation preserves user content and navigation."""

import pytest
from PySide6.QtWidgets import QApplication
from ui.scene_language import normalize_language
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
