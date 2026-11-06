import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from ui.models.agent_decision import (
    DecisionInputPreview,
    DecisionPage,
    DecisionRecord,
    DecisionSummary,
    MemoryInputPreview,
    NearbyInputPreview,
    SkillInputPreview,
    VehicleInputPreview,
)
from ui.models.decision_text import action_rows, response_excerpt
from ui.widgets.agent_decisions import AgentDecisionsWidget


def record() -> DecisionRecord:
    return DecisionRecord(
        record_id=1,
        sequence=5,
        simulation_time_ms=250,
        group_name="driver",
        model_id="local",
        started_at=datetime.now(timezone.utc),
        state="completed",
        outcome="submitted",
        vehicle_count=2,
        action_count=1,
        fallback_count=1,
        message="有效动作 1，其余 1 辆使用 SUMO",
        input_text="truncated input...",
        truncated=True,
        output_text=json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"actions": [{"vehicle_id": "a", "desired_speed_mps": 8}]}
                            )
                        }
                    }
                ]
            }
        ),
        actions_text='[{"vehicle_id":"a", "desired_speed_mps":8}]',
        input_preview=DecisionInputPreview(
            vehicles=(
                VehicleInputPreview(
                    vehicle_id="a",
                    speed_mps=7,
                    lane_id="edge_0",
                    radius_m=100,
                    nearby_count=1,
                    nearby=(NearbyInputPreview(vehicle_id="b", speed_mps=9, distance_m=18),),
                    memory_steps=1,
                    recent_memory=(MemoryInputPreview(sequence=4, action_text="null"),),
                    skills=(SkillInputPreview(name="human_normal", content="<img src='bad'>"),),
                ),
                VehicleInputPreview(
                    vehicle_id="b", speed_mps=9, lane_id="edge_0", radius_m=100, nearby_count=1
                ),
            )
        ),
    )


def test_default_cards_share_selection_with_original_and_preserve_vehicle_on_update() -> None:
    app = QApplication.instance() or QApplication([])
    widget = AgentDecisionsWidget()
    data = record()
    session = UUID(int=1)
    widget.set_page(
        DecisionPage(
            session_id=session, total=1, items=(DecisionSummary.model_validate(data.model_dump()),)
        )
    )
    widget.set_detail(session, data)
    assert widget.mode_tabs.currentWidget() is widget.cards
    assert "100 m" in widget.cards.observation.text()
    assert "均衡驾驶" in widget.cards.skill.text()
    assert widget.cards.skill.textFormat() == Qt.TextFormat.PlainText
    assert "后续状态未知" in widget.cards.memory.text()
    assert "目标速度 8 m/s" in widget.cards.action.text()
    widget.cards.vehicle.setCurrentIndex(1)
    assert "由 SUMO 控制" in widget.cards.action.text()
    widget.set_detail(session, data.model_copy(update={"elapsed_ms": 1500}))
    assert widget.cards.vehicle.currentText() == "b"
    widget.cards.raw_requested.emit()
    assert widget.mode_tabs.currentIndex() == 1
    assert "truncated input" in widget.editors["input_text"].toPlainText()
    widget.mode_tabs.setCurrentIndex(0)
    assert widget.cards.vehicle.currentText() == "b"
    widget.clear()
    assert widget.cards.vehicle.count() == 0 and widget.cards.rounds.count() == 0
    assert widget.cards.metrics["vehicles"].text() == "—"
    widget.close()
    assert app


@pytest.mark.parametrize("outcome", ["pending", "discarded", "failed"])
def test_unsubmitted_result_is_never_presented_as_sumo_execution(outcome: str) -> None:
    app = QApplication.instance() or QApplication([])
    widget = AgentDecisionsWidget()
    widget.cards.set_record(record().model_copy(update={"outcome": outcome}))
    widget.cards.vehicle.setCurrentIndex(1)
    assert widget.cards.metrics["sumo"].text() == "—"
    assert "由 SUMO 控制" not in widget.cards.action.text()
    assert "未执行" in widget.cards.action.text() or "尚未提交" in widget.cards.action.text()
    widget.close()
    assert app


def test_card_navigation_selects_same_raw_record_and_stops_following() -> None:
    app = QApplication.instance() or QApplication([])
    widget = AgentDecisionsWidget()
    data = record()
    first = DecisionSummary.model_validate(data.model_dump())
    widget.set_page(
        DecisionPage(
            session_id=UUID(int=1),
            total=2,
            items=(first.model_copy(update={"record_id": 2}), first),
        )
    )
    widget.cards.rounds.setCurrentRow(1)
    assert not widget.follow.isChecked()
    assert widget._selection == 1 and widget.table.currentRow() == 1
    widget._row_clicked(0, 0)
    assert widget._selection == 2 and widget.cards.rounds.currentRow() == 0
    widget.close()
    assert app


def test_provider_previews_handle_invalid_jev_and_long_text_without_fake_explanations() -> None:
    assert response_excerpt("invalid-json") == "invalid-json"
    assert "正在推理" in response_excerpt("", running=True)
    assert "vehicle_0  →  加速" in response_excerpt(
        '{"answers":{"vehicle_0":{"type":"choice","choice":"accelerate"}}}'
    )
    assert "摘要已省略" in response_excerpt("x" * 7000)
    assert action_rows("[broken") is None
    assert action_rows(json.dumps([{"vehicle_id": "a", "desired_speed_mps": 10**400}])) == (
        ("a", "无外部动作"),
    )
    assert action_rows('[{"vehicle_id":"a", "desired_speed_mps":0}]') == (("a", "目标速度 0 m/s"),)
