from __future__ import annotations

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from ui.widgets.scenario_settings_editor import ScenarioSettingsEditor, ScenarioSettingsValues


def _application() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _editor() -> ScenarioSettingsEditor:
    _application()
    editor = ScenarioSettingsEditor(traffic_mode=False)
    editor.load(
        ScenarioSettingsValues(
            vehicle_count=100,
            desired_density_veh_per_km_lane=65,
            duration_s=600,
            warmup_s=120,
            seed=42,
            inflow_veh_per_hour_per_lane=12,
            total_flow_veh_per_hour=600,
            reference_speed_kmh=30,
            demand_mode="count",
            automation_mix=(("L0", 60), ("L3", 40)),
        )
    )
    return editor


def test_settings_default_to_english_and_switch_without_marking_clean_values_dirty() -> None:
    editor = _editor()
    assert editor.language == "en"
    assert editor.demand_mode_combo.currentText() == "By vehicle count"
    assert editor.duration_input.suffix() == " s"
    assert editor.vehicle_count_input.accessibleName() == "Planned vehicles"
    assert not editor.is_dirty()
    changes: list[bool] = []
    editor.changed.connect(lambda: changes.append(True))
    editor.set_language("zh")
    assert editor.demand_mode_combo.currentText() == "按车辆总数"
    assert editor.duration_input.suffix() == " 秒"
    assert editor.vehicle_count_input.value() == 100
    assert editor.validation_error() is None
    assert not editor.is_dirty()
    editor.set_language("en")
    assert not editor.is_dirty()
    assert changes == []
    editor.close()


def test_switch_preserves_dirty_values_and_current_demand_mode() -> None:
    editor = _editor()
    editor.inflow_input.setValue(18.5)
    editor.duration_input.setValue(900)
    editor.automation_inputs["L0"].setValue(70)
    editor.automation_inputs["L3"].setValue(30)
    before = editor.revision()
    assert editor.is_dirty()
    editor.set_language("zh")
    assert editor.revision() == before
    assert editor.demand_mode_combo.currentData() == "inflow"
    assert editor.is_dirty()
    editor.set_language("en")
    assert editor.revision() == before
    editor.cancel()
    assert not editor.is_dirty()
    assert editor.vehicle_count_input.value() == 100
    assert editor.duration_input.value() == 600
    assert editor.automation_inputs["L0"].value() == 60
    editor.close()


def test_switch_keeps_empty_invalid_input_and_localized_validation_error() -> None:
    editor = _editor()
    editor.duration_input.lineEdit().setText("")
    editor.seed_input.lineEdit().setText("not a number")
    assert editor.validation_error() == (
        "Enter valid numbers. Empty or out-of-range values cannot be applied."
    )
    editor.set_language("zh")
    assert editor.duration_input.cleanText() == ""
    assert editor.seed_input.lineEdit().text() == "not a number"
    assert editor.is_dirty()
    assert editor.validation_error() == "请填写有效数字，空值或超出范围的输入不能应用。"
    editor.set_language("en")
    assert editor.duration_input.cleanText() == ""
    assert editor.seed_input.lineEdit().text() == "not a number"
    assert editor.validation_error() is not None
    editor.close()


def test_switch_preserves_number_locale_raw_decimal_and_selection() -> None:
    editor = _editor()
    editor.density_input.setLocale(QLocale(QLocale.Language.German))
    editor.density_input.lineEdit().setText("12,5")
    editor.vehicle_count_input.lineEdit().setSelection(0, 2)
    editor.set_language("zh")
    assert editor.density_input.lineEdit().text() == "12,5"
    assert editor.density_input.value() == 12.5
    assert editor.density_input.locale().language() == QLocale.Language.German
    assert editor.vehicle_count_input.lineEdit().selectedText() == "10"
    editor.set_language("en")
    assert editor.density_input.lineEdit().text() == "12,5"
    assert editor.vehicle_count_input.lineEdit().selectedText() == "10"
    editor.close()


def test_semantic_validation_messages_follow_language() -> None:
    editor = _editor()
    editor.warmup_input.setValue(600)
    assert editor.validation_error() == "Warm-up must be shorter than duration."
    editor.set_language("zh")
    assert editor.validation_error() == "预热时间必须小于仿真时长。"
    editor.warmup_input.setValue(120)
    editor.automation_inputs["L0"].setValue(50)
    assert editor.validation_error() == "L0–L5 请求比例合计必须为 100%。"
    editor.set_language("en")
    assert editor.validation_error() == "Requested L0–L5 shares must add up to 100%."
    editor.close()
