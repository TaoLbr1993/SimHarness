from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication
from ui.api_client import RestApiClient
from ui.models.agent_session import AgentSessionView
from ui.viewmodels.agent_connection import AgentConnectionViewModel
from ui.views.agent_connection_page import AgentConnectionPage


def test_group_configuration_context_and_overlap() -> None:
    app = QApplication.instance() or QApplication([])
    page = AgentConnectionPage()
    group = page.groups()[0]
    assert not group.memory.isChecked()
    assert all(not box.isChecked() for box in group.skills.values())
    group.url.setText("http://localhost:8002/v1")
    group.model_id.setText("test-model")
    group.vehicle_ids.setText("a, b")
    group.memory.setChecked(True)
    group.skills["human_normal"].setChecked(True)
    config = page.configuration()
    assert config.bindings[0].skills == ("human_normal",)
    assert config.bindings[0].memory.enabled
    assert group.skill_status.text() == "已选 1 项"
    page.add_group()
    other = page.groups()[1]
    other.url.setText("http://localhost:8002/v1")
    other.model_id.setText("other")
    other.vehicle_ids.setText("a")
    with pytest.raises(ValueError, match="多个"):
        page.configuration()
    page.remove_group(1)
    group.selector.setCurrentIndex(1)
    assert not page.add_button.isEnabled()
    assert page.configuration().bindings[0].all_vehicles
    page.close()
    assert app


def test_session_state_locks_configuration_but_not_pause() -> None:
    app = QApplication.instance() or QApplication([])
    page = AgentConnectionPage()
    view = AgentSessionView(
        experiment_id=UUID(int=1),
        status="running",
        experiment_status="RUNNING",
        external_stepping=True,
        vehicle_ids=("new-car",),
    )
    page.set_view(view)
    assert not page.groups()[0].isEnabled()
    assert page.start_button.text() == "暂停接入" and page.start_button.isEnabled()
    assert page.groups()[0].vehicle_list.count() == 1
    page.set_view(view.model_copy(update={"status": "paused"}))
    assert page.groups()[0].isEnabled()
    assert page.start_button.text() == "继续接入"
    page.set_view(view.model_copy(update={"external_stepping": False}))
    assert not page.start_button.isEnabled()
    page.close()
    assert app


def test_stale_experiment_response_cannot_change_current_page() -> None:
    app = QApplication.instance() or QApplication([])
    model = AgentConnectionViewModel(RestApiClient("http://localhost:8000"))
    views = []
    model.view_changed.connect(views.append)
    model.bind_experiment(str(UUID(int=1)))
    model.bind_experiment(str(UUID(int=2)))
    views.clear()
    model._success(f"agent-session:view:{UUID(int=1)}", {})
    assert not views
    model.deleteLater()
    assert app


def test_actions_remain_visible_when_configuration_needs_scrolling() -> None:
    app = QApplication.instance() or QApplication([])
    page = AgentConnectionPage()
    page.resize(980, 650)
    page.show()
    app.processEvents()
    for button in (page.check_button, page.start_button, page.stop_button):
        position = button.mapTo(page, button.rect().bottomRight())
        assert page.rect().contains(position)
        assert button.isVisible()
    page.close()
