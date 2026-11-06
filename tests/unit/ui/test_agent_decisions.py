from datetime import datetime, timezone
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from ui.api_client import RestApiClient
from ui.models.agent_decision import DecisionPage, DecisionRecord, DecisionSummary
from ui.models.agent_session import AgentSessionView
from ui.viewmodels.agent_decisions import AgentDecisionsViewModel
from ui.views.agent_connection_page import AgentConnectionPage
from ui.widgets.agent_decisions import AgentDecisionsWidget


def summary(record_id: int) -> DecisionSummary:
    return DecisionSummary(
        record_id=record_id,
        sequence=record_id,
        simulation_time_ms=record_id * 50,
        group_name="driver",
        model_id="test",
        started_at=datetime.now(timezone.utc),
    )


def test_memory_skill_are_independent_panels_and_running_opens_history() -> None:
    app = QApplication.instance() or QApplication([])
    page = AgentConnectionPage()
    group = page.groups()[0]
    memory_title = group.memory_panel.findChild(QLabel, "panelTitle")
    skill_title = group.skill_panel.findChild(QLabel, "panelTitle")
    assert memory_title is not None and memory_title.text() == "Memory · 车辆记忆"
    assert skill_title is not None and skill_title.text() == "Skill · 驾驶风格"
    assert not group.memory.isChecked() and not any(c.isChecked() for c in group.skills.values())
    page.set_view(
        AgentSessionView(
            experiment_id=UUID(int=1),
            experiment_status="RUNNING",
            external_stepping=True,
            status="running",
        )
    )
    assert page.sections.currentIndex() == 1
    page.sections.setCurrentIndex(0)
    page.set_view(page._view)
    assert page.sections.currentIndex() == 0
    page.close()
    assert app


def test_record_selection_pagination_and_stale_detail_are_isolated() -> None:
    app = QApplication.instance() or QApplication([])
    widget = AgentDecisionsWidget()
    pages: list[int | None] = []
    details = []
    widget.page_requested.connect(pages.append)
    widget.detail_requested.connect(lambda session, record: details.append((session, record)))
    session = UUID(int=1)
    widget.set_page(
        DecisionPage(
            session_id=session, total=22, items=(summary(22), summary(21)), next_before_id=21
        )
    )
    assert details[-1] == (session, 22)
    widget._row_clicked(1, 0)
    assert not widget.follow.isChecked() and details[-1] == (session, 21)
    assert pages[-1] == 23
    selected = DecisionRecord(
        **summary(21).model_dump(),
        input_text='{"memory":[]}',
        output_text='{"actions":[]}',
        actions_text="[]",
    )
    widget.set_detail(session, selected)
    assert "memory" in widget.editors["input_text"].toPlainText()
    widget.set_detail(session, selected.model_copy(update={"record_id": 22, "input_text": "stale"}))
    assert "stale" not in widget.editors["input_text"].toPlainText()
    widget._older()
    assert pages[-1] == 21
    widget.set_page(DecisionPage(session_id=UUID(int=2)))
    widget.set_detail(session, selected)
    assert widget.editors["input_text"].toPlainText() == ""
    assert widget.follow.isChecked()
    widget.close()
    assert app


def test_new_session_rejects_old_http_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    rest = RestApiClient("http://localhost:8000")
    monkeypatch.setattr(rest, "get_agent_decisions", lambda *_args: None)
    model = AgentDecisionsViewModel(rest)
    view = AgentSessionView(
        experiment_id=UUID(int=1),
        session_id=UUID(int=2),
        experiment_status="RUNNING",
        external_stepping=True,
    )
    model.set_session(view)
    model.refresh()
    old_operation = model._list_operation
    assert old_operation is not None
    model.set_session(view.model_copy(update={"session_id": UUID(int=3)}))
    pages: list[object] = []
    model.page_changed.connect(pages.append)
    model._success(old_operation, DecisionPage(session_id=UUID(int=2)).model_dump(mode="json"))
    assert not pages
    model.deleteLater()
    assert app
