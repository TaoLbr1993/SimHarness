from __future__ import annotations

import platform
from typing import cast

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QPushButton,
    QStyle,
    QStyleOptionSpinBox,
    QWidget,
)
from ui.models import TRAFFIC_SCENARIO_PRESETS, MapSummary, SimulationConfigurationDraft
from ui.models.protocol import GeneratedScenarioSummary
from ui.views.scene_configuration_page import SceneConfigurationPage
from ui.views.theme import ThemeMode, load_stylesheet


def _application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def test_generated_scene_shows_settings_preserves_routes_and_waits_for_manual_launch() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)
    assert page.generate_button.text() == "打开 TrafficForge  →"
    launched: list[object] = []
    opened: list[bool] = []
    page.launch_requested.connect(launched.append)
    page.generation_page_requested.connect(lambda: opened.append(True))
    page.description.setPlainText("希望测试拥堵场景下车辆速度")
    page.generate_button.click()
    assert opened == [True]
    assert not launched
    page.add_automation_button.click()
    page.automation_rows[0].count_input.setValue(999)
    scene = GeneratedScenarioSummary(
        task_id="20261104_100000_aabbcc",
        description="希望测试拥堵场景下车辆速度",
        traffic_state="congested",
        desired_density_veh_per_km_lane=65,
        planned_vehicles=1300,
        duration_s=600,
        warmup_s=120,
        seed=42,
        inflow_veh_per_hour_per_lane=1950,
        unverified_requirements=("雨天",),
    )
    summary = MapSummary(
        map_id="nl2-20261104_100000_aabbcc",
        kind="sumo",
        display_name="拥堵车速测试",
        validated=True,
        network_schema_version="sumo-net/display-1.0",
        manifest_available=False,
        generated_scenario=scene,
    )
    page.set_maps((summary,))
    assert page.scene_name.text() == "拥堵车速测试"
    assert page.duration_time.time() == QTime(0, 10)
    assert page.current_configuration().automation_demands == ()
    assert page.automation_section.isHidden()
    assert "65 辆/公里/车道" in page.generated_summary.text()
    assert "1300 辆" in page.generated_summary.text()
    assert "非实测" in page.generated_summary.text()
    assert "雨天" in page.generated_summary.text()
    assert not launched
    assert not page.duration_time.isEnabled()
    assert "重新生成" in page.duration_time.toolTip()
    page.set_maps((summary,))
    assert page.duration_time.time() == QTime(0, 10)
    page.set_generation_state(True, "正在生成")
    assert not page.generate_button.isEnabled()
    assert not page.create_button.isEnabled()
    page.set_generation_state(False, "场景已保存")
    page.create_button.click()
    assert len(launched) == 1
    assert cast(SimulationConfigurationDraft, launched[0]).automation_demands == ()
    assert cast(SimulationConfigurationDraft, launched[0]).duration_ms == 600_000
    plain = summary.model_copy(update={"map_id": "plain", "generated_scenario": None})
    page.set_maps((summary, plain))
    page.map_combo.setCurrentIndex(page.map_combo.findData("plain"))
    assert page.duration_time.isEnabled()
    page.duration_time.setTime(QTime(0, 8))
    assert page.current_configuration().duration_ms == 480_000
    page.close()


@pytest.mark.parametrize(
    "button_name", ["save_configuration_button", "create_button", "test_button"]
)
@pytest.mark.parametrize("invalid_field", ["description", "scene_name"])
def test_invalid_saved_text_is_visible_and_retained_until_user_corrects_it(
    button_name: str,
    invalid_field: str,
) -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)
    sent: list[object] = []
    page.configuration_save_requested.connect(sent.append)
    page.launch_requested.connect(sent.append)
    page.test_requested.connect(sent.append)
    description = "完整的生成需求" * 200 if invalid_field == "description" else "测试拥堵"
    name = "名" * 201 if invalid_field == "scene_name" else "拥堵车速测试"
    page.scene_name.setText(name)
    page.description.setPlainText(description)
    button = cast(QPushButton, getattr(page, button_name))
    button.click()
    assert not sent
    assert "无法保存或启动" in page.generation_status.text()
    assert ("1000" if invalid_field == "description" else "200") in page.generation_status.text()
    assert page.scene_name.text() == name
    assert page.description.toPlainText() == description
    page.scene_name.setText("名" * 200)
    page.description.setPlainText("需" * 1000)
    button.click()
    assert len(sent) == 1
    assert cast(SimulationConfigurationDraft, sent[0]).description == "需" * 1000
    page.close()


def test_scene_configuration_only_lists_direct_sumo_packages() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)

    assert page.external_stepping.text() == "等待外部 Agent 控制"
    assert not page.external_stepping.isChecked()
    selected_maps: list[str] = []
    page.map_selected.connect(selected_maps.append)
    page.set_maps(
        (
            MapSummary(
                map_id="town04",
                kind="core_run",
                display_name="Town04",
                carla_map="Town04",
                carla_version="0.9.16",
                validated=True,
                network_schema_version="traffic-network/1.0",
            ),
            MapSummary(
                map_id="image2road",
                kind="sumo",
                display_name="image2road",
                validated=True,
                network_schema_version="sumo-net/display-1.0",
                manifest_available=False,
                sumo_config_file="image2road.sumocfg",
                sumo_step_ms=1000,
            ),
        )
    )

    assert page.map_combo.count() == 1
    assert page.map_combo.itemData(0) == "image2road"
    assert "Town04" not in page.map_combo.itemText(0)
    assert selected_maps == ["image2road"]
    assert page.map_preview_status.text() == "正在加载地图路网预览……"
    assert "▶  开始仿真" in {button.text() for button in page.findChildren(QPushButton)}

    page.close()


def test_scene_configuration_matches_reference_defaults() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)

    assert page.scene_name.text() == "未命名场景"
    assert page.scene_name.placeholderText() == "请输入场景名称"
    assert page.description.toPlainText() == ""
    assert page.description.placeholderText() == "请输入场景描述"
    assert page.duration_time.time() == QTime(1, 0, 0)
    assert page.findChild(QComboBox, "simulationWeatherTimeCombo") is None
    assert page.findChild(QPushButton, "saveSimulationDraftButton") is None
    assert page.save_configuration_button.text() == "保存配置"
    assert page.test_button.text() == "测试"
    assert page.findChild(QWidget, "simulationHeaderActions") is not None
    assert page.automation_rows == []
    assert page.vehicle_total.text() == "总计：0"

    page.close()


def test_configuration_actions_emit_the_current_typed_configuration() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)
    page.set_maps(
        (
            MapSummary(
                map_id="image2road",
                kind="sumo",
                display_name="Image road",
                validated=True,
                network_schema_version="sumo-net/display-1.0",
                manifest_available=False,
                sumo_config_file="image2road.sumocfg",
                sumo_step_ms=1000,
            ),
        )
    )
    page.scene_name.setText("Morning run")
    page.description.setPlainText("Exact traffic mix")
    page.duration_time.setTime(QTime(0, 2, 0))
    saved: list[object] = []
    launched: list[object] = []
    tested: list[object] = []
    page.configuration_save_requested.connect(saved.append)
    page.launch_requested.connect(launched.append)
    page.test_requested.connect(tested.append)

    page.save_configuration_button.click()
    page.create_button.click()
    page.test_button.click()

    assert saved == launched == tested
    configuration = cast(SimulationConfigurationDraft, saved[0])
    assert configuration.scene_name == "Morning run"
    assert configuration.map_id == "image2road"
    assert configuration.duration_ms == 120_000
    assert configuration.automation_demands == ()

    page.close()


def test_scene_configuration_adds_levels_and_updates_vehicle_total() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)

    assert page.automation_rows == []
    assert page.vehicle_total.text() == "总计：0"

    page.add_automation_button.click()
    added = page.automation_rows[0]
    assert added.level == "L0"
    added.count_input.setValue(200)

    assert page.vehicle_total.text() == "总计：200"

    page.close()


def test_general_traffic_keeps_sumo_demand_and_external_control() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False, traffic_mode=True)
    page.add_automation_button.click()
    page.automation_rows[0].count_input.setValue(200)
    page.external_stepping.setChecked(True)

    assert not page.add_automation_button.isVisibleTo(page)
    assert page.current_configuration().automation_demands == ()
    assert page.external_stepping.isChecked()
    assert page.external_stepping.isVisibleTo(page)
    page.close()


def test_scene_configuration_removes_automation_level() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)
    page.add_automation_button.click()
    removed = page.automation_rows[0]

    page._remove_automation_row(removed)

    assert page.automation_rows == []
    assert removed not in page.automation_rows
    assert page.add_automation_button.isEnabled()
    assert page.vehicle_total.text() == "总计：0"

    page.close()


def test_scene_configuration_applies_traffic_scenario_preset() -> None:
    _application()
    page = SceneConfigurationPage(load_web_map=False)
    preset = TRAFFIC_SCENARIO_PRESETS[1]
    page.set_maps(
        (
            MapSummary(
                map_id=preset.map_id,
                kind="sumo",
                display_name=preset.name,
                validated=True,
                network_schema_version="sumo-net/display-1.0",
                manifest_available=False,
                sumo_config_file="scenario.sumocfg",
                sumo_step_ms=50,
            ),
        )
    )

    applied = page.apply_traffic_scenario(preset)

    assert applied
    assert page.scene_name.text() == preset.name
    assert page.map_combo.currentData() == preset.map_id
    assert page.duration_time.time() == QTime(0, 1, 0)
    assert [(row.level, row.vehicle_count) for row in page.automation_rows] == list(
        preset.automation_counts
    )

    page.close()


def test_macos_automation_count_uses_large_stepper_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    page = SceneConfigurationPage(load_web_map=False)
    page.add_automation_button.click()
    count_input = page.automation_rows[0].count_input

    assert count_input.property("macosStepper") is True
    assert count_input.minimumHeight() == 48
    for theme in (ThemeMode.LIGHT, ThemeMode.DARK):
        stylesheet = load_stylesheet(theme)
        assert 'QSpinBox#automationVehicleCount[macosStepper="true"]::up-button' in stylesheet
        assert 'QSpinBox#automationVehicleCount[macosStepper="true"]::down-button' in stylesheet
        assert "width: 40px;" in stylesheet

    previous_stylesheet = application.styleSheet()
    application.setStyleSheet(load_stylesheet(ThemeMode.DARK))
    page.show()
    application.processEvents()
    option = QStyleOptionSpinBox()
    count_input.initStyleOption(option)
    up_button = count_input.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxUp,
        count_input,
    )
    down_button = count_input.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxDown,
        count_input,
    )

    assert up_button.width() >= 40
    assert down_button.width() >= 40
    assert up_button.height() >= 24
    assert down_button.height() >= 24

    page.close()
    application.setStyleSheet(previous_stylesheet)


def test_windows_automation_stepper_renders_explicit_arrow_icons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    previous_stylesheet = application.styleSheet()
    application.setStyleSheet(load_stylesheet(ThemeMode.DARK))
    page = SceneConfigurationPage(load_web_map=False)
    page.add_automation_button.click()
    page.show()
    application.processEvents()
    count_input = page.automation_rows[0].count_input
    assert count_input.property("arrowColor").name() == "#cfd3dc"
    pixmap = count_input.grab()
    image = pixmap.toImage()
    pixel_ratio = pixmap.devicePixelRatio()
    option = QStyleOptionSpinBox()
    count_input.initStyleOption(option)

    for subcontrol in (
        QStyle.SubControl.SC_SpinBoxUp,
        QStyle.SubControl.SC_SpinBoxDown,
    ):
        button = count_input.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox,
            option,
            subcontrol,
            count_input,
        )
        bright_pixels = sum(
            image.pixelColor(x, y).lightness() >= 150
            for x in range(
                round(button.left() * pixel_ratio),
                round((button.right() + 1) * pixel_ratio),
            )
            for y in range(
                round(button.top() * pixel_ratio),
                round((button.bottom() + 1) * pixel_ratio),
            )
        )
        assert bright_pixels >= 4

    page.close()
    application.setStyleSheet(previous_stylesheet)
