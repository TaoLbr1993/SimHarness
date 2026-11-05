from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QImageWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from ui.views.scene_generation_page import SceneGenerationPage


def _application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _image() -> bytes:
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(QColor("#6175ed"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    QImageWriter(buffer, b"png").write(image)
    return bytes(buffer.data().data())


def _draft() -> dict[str, Any]:
    return {
        "task_id": "20261104_100000_aabbcc",
        "description": "生成北航附近 1 公里路网，测试拥堵场景",
        "plan": {
            "traffic_state": "congested",
            "desired_density_veh_per_km_lane": 65.0,
            "duration_s": 600,
            "warmup_s": 120,
            "seed": 42,
            "inflow_veh_per_hour_per_lane": 1950,
            "defaults_used": ["仿真时长", "预热时间", "L0 100%"],
            "automation_note": None,
        },
        "demand": {
            "planned_vehicles": 1000,
            "realized_schedule_veh_per_hour": 6000.0,
            "automation_mix": [
                {"level": "L0", "vehicle_count": 750, "share_percent": 75},
                {"level": "L3", "vehicle_count": 250, "share_percent": 25},
            ],
        },
        "unverified_requirements": ["雨天"],
    }


def _ready_page(*, traffic_mode: bool = True) -> SceneGenerationPage:
    _application()
    page = SceneGenerationPage(language="zh", traffic_mode=traffic_mode)
    draft = _draft()
    page.description.setText(draft["description"])
    page.set_draft(draft)
    page.set_preview_image(_image())
    return page


def test_generation_is_explicit_and_duplicate_submission_is_prevented() -> None:
    _application()
    page = SceneGenerationPage(language="zh", traffic_mode=True)
    requests: list[str] = []
    page.generation_requested.connect(requests.append)
    page.generate_button.click()
    assert requests == []
    assert "先写一句" in page.status_label.text()
    page.description.setText("生成一个十字路口")
    page.description.returnPressed.emit()
    page.generate_button.click()
    assert requests == ["生成一个十字路口"]
    assert not page.description.isEnabled()
    page.set_generation_state(False, "服务连接失败，请重试")
    assert page.description.isEnabled()
    assert page.generate_button.isEnabled()
    assert not page.publish_button.isEnabled()
    page.close()


def test_failed_first_generation_shows_reason_and_retry_without_expanding_preview() -> None:
    _application()
    page = SceneGenerationPage(language="zh", traffic_mode=True)
    requests: list[str] = []
    page.generation_requested.connect(requests.append)
    assert page.error_card.isHidden()
    page.description.setText("生成安慧北里雅园2号楼附近1公里路网")
    page.generate_button.click()
    assert page.generate_button.text() == "正在生成…"
    assert page.results.isHidden()
    reason = "没有找到匹配地点，请补充城市、校区或更完整的地名"
    page.set_generation_error(reason)
    assert not page.error_card.isHidden()
    assert reason in page.error_message.text()
    assert page.results.isHidden()
    assert page.description.isEnabled()
    assert page.generate_button.text() == "重试生成  →"
    assert not page.publish_button.isEnabled()
    page.description.setText(_draft()["description"])
    page.generate_button.click()
    assert len(requests) == 2
    assert page.error_card.isHidden()
    assert page.generate_button.text() == "正在生成…"
    page.set_draft(_draft())
    page.set_generation_state(False, "预览已生成")
    page.set_preview_image(_image())
    assert not page.results.isHidden()
    assert page.error_card.isHidden()
    assert page.generate_button.text() == "重新生成  →"
    assert page.publish_button.isEnabled()
    page.close()


def test_draft_waits_for_image_and_explicit_confirmation_then_cannot_be_added_twice() -> None:
    _application()
    page = SceneGenerationPage(language="zh", traffic_mode=True)
    published: list[str] = []
    configured: list[str] = []
    page.publish_requested.connect(published.append)
    page.configure_requested.connect(configured.append)
    draft = _draft()
    page.description.setText(draft["description"])
    page.set_draft(draft)
    assert not published
    assert not page.publish_button.isEnabled()
    page.set_preview_image(_image())
    assert page.publish_button.isEnabled()
    page.publish_button.click()
    page.publish_button.click()
    assert published == [draft["task_id"]]
    assert not configured
    page.set_published("nl2-" + draft["task_id"])
    assert not page.publish_button.isEnabled()
    page.configure_button.click()
    assert configured == ["nl2-" + draft["task_id"]]
    page.close()


def test_edited_requirement_invalidates_old_preview_until_restored() -> None:
    page = _ready_page()
    page.description.setText("换成两个十字路口")
    assert not page.publish_button.isEnabled()
    assert "上一次预览" in page.status_label.text()
    page.description.setText(_draft()["description"])
    assert page.publish_button.isEnabled()
    page.set_generation_state(True, "正在重新生成")
    assert not page.publish_button.isEnabled()
    page.close()


def test_image_failure_can_retry_and_cannot_confirm_a_missing_map() -> None:
    page = _ready_page()
    retried: list[str] = []
    page.preview_retry_requested.connect(retried.append)
    page.set_preview_error("无法加载图片，请重试")
    assert not page.publish_button.isEnabled()
    page.preview_retry_button.click()
    assert retried == [_draft()["task_id"]]
    page.set_preview_image(b"invalid image")
    assert not page.publish_button.isEnabled()
    assert not page.preview_retry_button.isHidden()
    assert "无法显示" in page.map_image.text()
    page.set_preview_image(_image())
    assert page.publish_button.isEnabled()
    page.set_publication_state(True, "正在添加")
    page.set_publication_state(False, "添加失败，请重试")
    assert page.publish_button.isEnabled()
    page.close()


def test_failed_regeneration_of_identical_text_cannot_publish_the_previous_draft() -> None:
    page = _ready_page()
    published: list[str] = []
    page.publish_requested.connect(published.append)
    page.generate_button.click()
    page.set_generation_state(False, "生成失败，请重试")
    page.set_preview_image(_image())
    page.publish_button.click()
    assert not published
    assert not page.publish_button.isEnabled()
    page.set_draft(_draft())
    page.set_preview_image(_image())
    assert page.publish_button.isEnabled()
    page.close()


def test_notes_are_available_in_an_expandable_detail_without_html_interpretation() -> None:
    page = _ready_page()
    draft = _draft()
    draft["notes"] = ["采用学院路校区", "<b>部分信号由 SUMO 推断</b>"]
    page.set_draft(draft)
    assert page.notes_label.isHidden()
    page.notes_button.click()
    assert not page.notes_label.isHidden()
    assert "学院路" in page.notes_label.text()
    assert "<b>" in page.notes_label.text()
    page.close()


def test_traffic_mode_shows_total_demand_and_autonomy_mode_uses_actual_mix() -> None:
    traffic = _ready_page(traffic_mode=True)
    assert traffic.composition_row.isHidden()
    assert traffic.stat_values["vehicles"].text() == "1,000 辆"
    assert "总发车量 6000 辆/小时" in traffic.flow_label.text()
    assert "不是同一时刻" in traffic.demand_note.text()
    assert "雨天" in traffic.requirements_label.text()
    traffic.close()
    autonomy = _ready_page(traffic_mode=False)
    assert not autonomy.composition_row.isHidden()
    assert autonomy.composition_chart.shares == [0.75, 0.25]
    assert "L3  25%  ·  250 辆" in autonomy.composition_legend.text()
    assert "L0 100%" in autonomy.defaults_label.text()
    autonomy.set_theme("dark")
    assert autonomy.property("studioTheme") == "dark"
    autonomy.close()


def test_count_edit_requires_apply_and_new_preview_before_confirmation() -> None:
    page = _ready_page()
    revisions: list[tuple[str, object]] = []
    publications: list[str] = []
    page.revision_requested.connect(lambda task, count: revisions.append((task, count)))
    page.publish_requested.connect(publications.append)
    page.vehicle_count_input.setValue(500)
    assert page.stat_values["vehicles"].text() == "1,000 辆"
    assert not page.publish_button.isEnabled()
    assert page.apply_count_button.isEnabled()
    page.publish_button.click()
    assert not publications
    page.apply_count_button.click()
    page.apply_count_button.click()
    assert revisions == [(_draft()["task_id"], {"vehicle_count": 500, "demand_mode": "count"})]
    assert not page.generate_button.isEnabled()
    assert not page.cancel_count_button.isEnabled()
    revised = _draft()
    revised["task_id"] = "20261104_100002_ccddff"
    revised["demand"]["planned_vehicles"] = 500
    revised["manual_overrides"] = {"vehicle_count": 500}
    page.set_draft(revised)
    assert page.stat_values["vehicles"].text() == "500 辆"
    assert not page.publish_button.isEnabled()
    page.set_preview_image(_image())
    page.publish_button.click()
    assert publications == [revised["task_id"]]
    page.close()


def test_failed_count_update_keeps_edits_retryable_or_cancelable() -> None:
    page = _ready_page()
    page.vehicle_count_input.setValue(250)
    page.apply_count_button.click()
    page.set_revision_state(False, "更新失败：场景文件缺失")
    assert "场景文件缺失" in page.edit_status.text()
    assert page.vehicle_count_input.value() == 250
    assert page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    page.cancel_count_button.click()
    assert page.vehicle_count_input.value() == 1000
    assert not page.apply_count_button.isEnabled()
    assert page.publish_button.isEnabled()
    page.close()


def test_vehicle_count_can_be_typed_and_invalid_text_cannot_publish_old_quantity() -> None:
    page = _ready_page()
    editor = page.vehicle_count_input.lineEdit()
    editor.selectAll()
    QTest.keyClicks(editor, "350")
    assert page.vehicle_count_input.value() == 350
    assert page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert not page.vehicle_count_input.hasAcceptableInput()
    assert not page.publish_button.isEnabled()
    assert not page.apply_count_button.isEnabled()
    page.cancel_count_button.click()
    assert page.vehicle_count_input.value() == 1000
    assert page.publish_button.isEnabled()
    page.close()


def test_count_edit_on_published_scene_creates_new_confirmation_without_overwrite() -> None:
    page = _ready_page()
    page.set_published("nl2-" + _draft()["task_id"])
    page.vehicle_count_input.setValue(200)
    assert page.apply_count_button.isEnabled()
    assert not page.configure_button.isEnabled()
    page.cancel_count_button.click()
    assert page.configure_button.isEnabled()
    assert not page.publish_button.isEnabled()
    page.close()


def test_spinbox_focus_out_fixup_restores_action_state() -> None:
    page = _ready_page()
    page.show()
    _application().processEvents()
    editor = page.vehicle_count_input.lineEdit()
    editor.setFocus()
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert not page.publish_button.isEnabled()
    page.description.setFocus()
    _application().processEvents()
    assert page.vehicle_count_input.hasAcceptableInput()
    assert page.publish_button.isEnabled()
    page.vehicle_count_input.setValue(350)
    editor.setFocus()
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    page.description.setFocus()
    _application().processEvents()
    assert page.vehicle_count_input.hasAcceptableInput()
    assert page.vehicle_count_input.value() == 350
    assert page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    page.close()


def test_map_zoom_uses_original_image_and_never_changes_the_draft() -> None:
    page = _ready_page()
    publications: list[str] = []
    page.publish_requested.connect(publications.append)
    page.zoom_button.click()
    dialog = page._image_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.original_button.click()
    dialog.zoom_in_button.click()
    assert dialog.zoom_factor > 1
    assert page.stat_values["vehicles"].text() == "1,000 辆"
    assert not publications and page.publish_button.isEnabled()
    dialog.close()
    page.map_image.activated.emit()
    assert page._image_dialog is dialog and dialog.isVisible()
    page.set_draft(_draft())
    assert not dialog.isVisible()
    assert cast(object, page._image_dialog) is None
    assert not page.zoom_button.isEnabled()
    page.close()


def test_all_numeric_settings_apply_together_and_cancel_restores_source() -> None:
    page = _ready_page()
    revisions: list[tuple[str, object]] = []
    page.revision_requested.connect(lambda task, payload: revisions.append((task, payload)))
    page.duration_input.setValue(900)
    page.warmup_input.setValue(90)
    page.seed_input.setValue(99)
    page.reference_speed_input.setValue(35)
    page.density_input.setValue(30)
    assert page.demand_mode_combo.currentData() == "density"
    assert not page.publish_button.isEnabled()
    page.apply_count_button.click()
    assert revisions == [
        (
            _draft()["task_id"],
            {
                "duration_s": 900,
                "warmup_s": 90,
                "seed": 99,
                "reference_speed_kmh": 35.0,
                "desired_density_veh_per_km_lane": 30.0,
                "demand_mode": "density",
            },
        )
    ]
    page.set_revision_state(False, "参数更新失败，请重试")
    assert page.duration_input.value() == 900
    assert page.apply_count_button.isEnabled()
    page.cancel_count_button.click()
    assert page.duration_input.value() == 600
    assert page.warmup_input.value() == 120
    assert page.seed_input.value() == 42
    assert page.density_input.value() == 65
    assert page.demand_mode_combo.currentData() == "inflow"
    assert page.publish_button.isEnabled()
    page.close()


def test_invalid_time_or_percentages_block_apply_and_confirmation() -> None:
    page = _ready_page(traffic_mode=False)
    page.warmup_input.setValue(600)
    assert not page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    assert "预热" in page.edit_status.text()
    page.cancel_count_button.click()
    page.automation_inputs["L3"].setValue(30)
    assert "100%" in page.edit_status.text()
    assert not page.apply_count_button.isEnabled()
    page.automation_inputs["L0"].setValue(70)
    assert page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    revisions: list[object] = []
    page.revision_requested.connect(lambda _, payload: revisions.append(payload))
    page.apply_count_button.click()
    assert revisions == [
        {
            "automation_mix": [
                {"level": "L0", "share_percent": 70.0},
                {"level": "L3", "share_percent": 30.0},
            ]
        }
    ]
    page.close()


def test_flow_edits_select_one_driving_quantity_and_keep_preview_totals_unchanged() -> None:
    page = _ready_page()
    page.inflow_input.setValue(250)
    assert page.demand_mode_combo.currentData() == "inflow"
    page.total_flow_input.setValue(1800)
    assert page.demand_mode_combo.currentData() == "total_flow"
    assert page.stat_values["vehicles"].text() == "1,000 辆"
    revisions: list[object] = []
    page.revision_requested.connect(lambda _, payload: revisions.append(payload))
    page.apply_count_button.click()
    assert revisions == [{"demand_mode": "total_flow", "total_flow_veh_per_hour": 1800.0}]
    page.close()


def test_switching_back_to_source_mode_drops_inactive_quantity_edit() -> None:
    page = _ready_page()
    page.vehicle_count_input.setValue(500)
    assert page.demand_mode_combo.currentData() == "count"
    page.demand_mode_combo.setCurrentIndex(page.demand_mode_combo.findData("inflow"))
    assert page.vehicle_count_input.value() == 1000
    assert not page.apply_count_button.isEnabled()
    assert page.publish_button.isEnabled()
    page.close()


def test_empty_additional_numeric_input_is_not_silently_published() -> None:
    page = _ready_page()
    page.settings_editor.advanced_button.setChecked(True)
    editor = page.seed_input.lineEdit()
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert not page.seed_input.hasAcceptableInput()
    assert not page.apply_count_button.isEnabled()
    assert not page.publish_button.isEnabled()
    page.cancel_count_button.click()
    assert page.seed_input.value() == 42
    assert page.publish_button.isEnabled()
    page.close()


def test_density_reference_edit_keeps_explicitly_restored_inflow_mode() -> None:
    page = _ready_page()
    page.density_input.setValue(30)
    page.demand_mode_combo.setCurrentIndex(page.demand_mode_combo.findData("inflow"))
    revisions: list[object] = []
    page.revision_requested.connect(lambda _, payload: revisions.append(payload))
    page.apply_count_button.click()
    assert revisions == [
        {
            "desired_density_veh_per_km_lane": 30.0,
            "demand_mode": "inflow",
            "inflow_veh_per_hour_per_lane": 1950.0,
        }
    ]
    page.close()


def test_studio_starts_in_english_and_examples_follow_interface_language() -> None:
    _application()
    page = SceneGenerationPage(traffic_mode=True)
    assert str(page.language) == "en"
    assert page.language_combo.currentData() == "en"
    assert page.generate_button.text() == "Generate preview  →"
    brand = next(
        label for label in page.findChildren(QLabel) if label.property("studioRole") == "pageTitle"
    )
    assert brand.text() == "TrafficForge"
    assert page.title.text() == "Turn an idea into a traffic scene"
    assert "Beihang" in page.description.placeholderText()
    assert all(
        not any("\u4e00" <= char <= "\u9fff" for char in label.text())
        for label in page.findChildren(QLabel)
    )
    example = page.examples.findChildren(QPushButton)[0]
    example.click()
    assert "Beihang" in page.description.text()
    description = page.description.text()
    changed: list[str] = []
    page.language_changed.connect(changed.append)
    page.language_combo.setCurrentIndex(page.language_combo.findData("zh"))
    assert str(page.language) == "zh"
    assert page.generate_button.text() == "生成预览  →"
    assert brand.text() == "TrafficForge"
    assert page.description.text() == description
    example.click()
    assert page.description.text() == "生成北航学院路校区附近 1 公里路网，测试拥堵场景"
    assert changed == ["zh"]
    page.close()


def test_language_switch_preserves_draft_image_dirty_inputs_and_expanded_notes() -> None:
    _application()
    page = SceneGenerationPage(traffic_mode=False)
    draft = _draft()
    draft["notes"] = ["原始地点：北航学院路校区"]
    page.description.setText(draft["description"])
    page.set_draft(draft)
    page.set_preview_image(_image())
    page.notes_button.setChecked(True)
    page.vehicle_count_input.setValue(321)
    page.settings_editor.advanced_button.setChecked(True)
    revision = page.settings_editor.revision()
    image_pixels = page.map_image.source_pixmap.toImage()
    requests: list[object] = []
    page.generation_requested.connect(requests.append)
    page.publish_requested.connect(requests.append)
    page.revision_requested.connect(lambda *args: requests.append(args))
    page.zoom_button.click()
    dialog = page._image_dialog
    assert dialog is not None
    dialog.zoom_in_button.click()
    zoom = dialog.zoom_factor
    for language in ("zh", "en"):
        page.set_language(language)
        assert page._draft is draft
        assert page.description.text() == draft["description"]
        assert page.vehicle_count_input.value() == 321
        assert page.settings_editor.revision() == revision
        assert page.map_image.source_pixmap.toImage() == image_pixels
        assert page.notes_button.isChecked()
        assert not page.notes_label.isHidden()
        assert "原始地点：北航学院路校区" in page.notes_label.text()
        assert page.settings_editor.advanced_button.isChecked()
        assert page.apply_count_button.isEnabled()
        assert not page.publish_button.isEnabled()
        assert dialog.language == language and dialog.zoom_factor == zoom
    assert page.stat_values["vehicles"].text() == "1,000 vehicles"
    assert "L3  25%  ·  250 vehicles" in page.composition_legend.text()
    assert "Total departures: 6000 veh/h" in page.flow_label.text()
    assert page.defaults_label.text() == "Defaults: Duration, Warm-up, L0 100%"
    assert not requests
    dialog.close()
    page.close()


def test_language_switch_keeps_pending_request_and_original_failure_cause() -> None:
    _application()
    page = SceneGenerationPage(traffic_mode=True)
    page.description.setText("Generate a junction")
    submitted: list[str] = []
    page.generation_requested.connect(submitted.append)
    page.generate_button.click()
    page.set_language("zh")
    assert page.generate_button.text() == "正在生成…"
    assert not page.generate_button.isEnabled() and not page.description.isEnabled()
    assert page.progress.isHidden() is False
    page.set_language("en")
    assert page.generate_button.text() == "Generating…"
    assert submitted == ["Generate a junction"]
    reason = "高德查询配额已用尽，请稍后重试"
    page.set_generation_error(reason)
    assert "original language" in page.error_message.text()
    assert reason in page.error_message.text()
    page.set_language("zh")
    assert page.error_message.text() == reason
    assert page.generate_button.text() == "重试生成  →"
    assert page.results.isHidden() and not page.error_card.isHidden()
    page.close()
