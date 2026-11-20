"""Desktop language stays in presentation and never rewrites agent or user data."""

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QStyleOptionButton
from ui.api_client import RealtimeClient, RestApiClient
from ui.localization import language, set_language, tr
from ui.models import MapSummary, WorkspaceSummary
from ui.models.agent_decision import (
    DecisionInputPreview,
    DecisionRecord,
    SkillInputPreview,
    VehicleInputPreview,
)
from ui.models.decision_text import response_excerpt
from ui.models.protocol import GeneratedScenarioSummary
from ui.viewmodels import RunViewModel
from ui.views.main_window import MainWindow
from ui.views.scene_configuration_page import SceneConfigurationPage
from ui.views.theme import ThemeMode, load_stylesheet
from ui.views.workspace_page import WorkspaceOverviewPage
from ui.widgets.agent_decision_cards import AgentDecisionCards

from trafficverse.cli import _build_parser, _run_ui


@pytest.fixture
def english_app() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    previous = language()
    set_language("en")
    try:
        yield app
    finally:
        set_language(previous)


def test_english_entry_needs_no_language_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bool, str]] = []

    def run(api_url: str, scenario_id: UUID, *, traffic_mode: bool, language: str) -> int:
        assert api_url == "http://127.0.0.1:8000"
        calls.append((traffic_mode, language))
        return 0

    monkeypatch.delenv("TRAFFICVERSE_API_URL", raising=False)
    monkeypatch.setattr("ui.app.main.run", run)
    for command in ("traffic-ui-en", "traffic-ui", "ui"):
        assert _run_ui(_build_parser().parse_args([command])) == 0
    assert calls == [(True, "en"), (True, "zh"), (False, "zh")]


def test_english_shell_pages_and_user_names(english_app: QApplication) -> None:
    viewmodel = RunViewModel(
        RestApiClient("http://127.0.0.1:8000"),
        RealtimeClient("http://127.0.0.1:8000"),
        UUID(int=42),
    )
    window = MainWindow(viewmodel, load_web_map=False, traffic_mode=True, scene_language="en")
    try:
        assert "Traffic" in window.windowTitle()
        for page in window._pages.values():
            for label in page.findChildren(QLabel):
                assert not re.search(r"[\u4e00-\u9fff]", label.text()), label.text()
        assert window.navigation._buttons["agent_connection"].text() == "Agent Connection"
        group = window.agent_connection_page.groups()[0]
        assert group.name_input.text() == "Control group 1"
        assert not group.memory.isChecked()
        assert not any(box.isChecked() for box in group.skills.values())
        group.name_input.setText("地图")
        assert window.agent_connection_page.tabs.tabText(0) == "地图"

        workspace = WorkspaceSummary(
            workspace_id=UUID(int=1),
            name="地图",
            description="系统设置",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        window.navigation.set_workspace(workspace.name)
        window.workspace_page.set_workspace(workspace)
        window.project_detail_page.set_workspace(workspace)
        assert window.navigation.workspace_name.text() == "地图"
        assert window.workspace_page.title.text() == "地图"
        assert window.workspace_page.description.text() == "系统设置"
        assert window.project_detail_page.name_field.value_label.text() == "地图"
        # TrafficForge's existing language selector must not reset the English shell.
        window.generation_page.set_language("zh")
        assert window.navigation._buttons["agent_connection"].text() == "Agent Connection"
    finally:
        window.close()


def test_english_decisions_preserve_vehicle_ids_and_raw_content(
    english_app: QApplication,
) -> None:
    original = "地图\n已提交 · 模型原文"
    record = DecisionRecord(
        record_id=1,
        sequence=7,
        simulation_time_ms=350,
        group_name="中文组",
        model_id="my-model",
        started_at=datetime.now(timezone.utc),
        state="completed",
        outcome="submitted",
        vehicle_count=1,
        action_count=1,
        output_text=original,
        actions_text='[{"vehicle_id":"地图","desired_speed_mps":8}]',
        input_preview=DecisionInputPreview(
            vehicles=(
                VehicleInputPreview(
                    vehicle_id="地图",
                    speed_mps=7,
                    lane_id="中文车道",
                    radius_m=100,
                    nearby_count=0,
                    skills=(SkillInputPreview(name="human_normal", content=original),),
                ),
            )
        ),
    )
    cards = AgentDecisionCards()
    try:
        cards.set_record(record)
        assert cards.heading.text() == "Frame 7 · 中文组"
        assert cards.vehicle.currentText() == "地图"
        assert cards.output.toPlainText() == original
        assert original in cards.skill.text()
        assert "Target speed 8 m/s" in cards.action.text()
        assert "Lane 中文车道" in cards.observation.text()
        assert not re.search(r"[\u4e00-\u9fff]", cards.badge.text())
        assert response_excerpt(original) == original
    finally:
        cards.close()


def test_known_dynamic_text_and_unknown_messages(english_app: QApplication) -> None:
    assert tr("共 12 条控制组决策记录 · 每页最多 20 条") == (
        "12 group decisions · Up to 20 per page"
    )
    assert tr("来自外部服务的自定义消息") == "来自外部服务的自定义消息"
    set_language("zh")
    assert tr("系统设置") == "系统设置"


@pytest.mark.parametrize("theme", tuple(ThemeMode))
def test_workspace_actions_fit_their_english_labels(
    english_app: QApplication, theme: ThemeMode
) -> None:
    page = WorkspaceOverviewPage(load_web_map=False, traffic_mode=True)
    page.setStyleSheet(load_stylesheet(theme))
    page.resize(980, 720)
    try:
        page.show()
        english_app.processEvents()
        for button in (page.rename_button, page.delete_button, page.enter_button):
            option = QStyleOptionButton()
            option.initFrom(button)
            option.text = button.text()
            content = button.style().subElementRect(
                QStyle.SubElement.SE_PushButtonContents, option, button
            )
            assert content.width() >= button.fontMetrics().horizontalAdvance(button.text())
            assert button.width() >= button.sizeHint().width()
    finally:
        page.close()


@pytest.mark.parametrize("traffic_state", ["free_flow", "moderate", "congested"])
@pytest.mark.parametrize("inflow", [None, 195.0])
def test_generated_setup_summary_translates_states_and_units(
    english_app: QApplication, traffic_state: str, inflow: float | None
) -> None:
    scene = GeneratedScenarioSummary.model_validate(
        {
            "task_id": "test-scene",
            "description": "用户原始场景描述",
            "traffic_state": traffic_state,
            "desired_density_veh_per_km_lane": 65,
            "planned_vehicles": 650,
            "duration_s": 600,
            "warmup_s": 120,
            "seed": 42,
            "inflow_veh_per_hour_per_lane": inflow,
        }
    )
    summary = MapSummary(
        map_id="generated-test",
        kind="sumo",
        display_name="用户场景名称",
        validated=True,
        network_schema_version="sumo-net/display-1.0",
        manifest_available=False,
        generated_scenario=scene,
    )
    page = SceneConfigurationPage(load_web_map=False, traffic_mode=True)
    try:
        page.set_maps((summary,))
        text = page.generated_summary.text()
        assert not re.search(r"[\u4e00-\u9fff]", text), text
        assert "65 vehicles/km/lane" in text
        assert "650 vehicles" in text
        assert "120 s; seed: 42" in text
        assert (
            "195 vehicles/hour/entry lane" if inflow else "Schedule by planned vehicle count"
        ) in text
        assert page.scene_name.text() == summary.display_name
        assert page.description.toPlainText() == scene.description
        custom = scene.model_copy(update={"unverified_requirements": ("拥堵",)})
        page.set_maps((summary.model_copy(update={"generated_scenario": custom}),))
        assert "Requirements to verify: 拥堵" in page.generated_summary.text()
    finally:
        page.close()
