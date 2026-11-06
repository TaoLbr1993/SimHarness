"""Page-sized decision polling with stale experiment/session/request rejection."""

from uuid import UUID, uuid4

from pydantic import ValidationError
from PySide6.QtCore import QObject, QTimer, Signal

from ui.api_client import RestApiClient
from ui.models.agent_decision import DecisionPage, DecisionRecord
from ui.models.agent_session import AgentSessionView


class AgentDecisionsViewModel(QObject):
    page_changed = Signal(object)
    detail_changed = Signal(object, object)
    error = Signal(str)

    def __init__(self, rest: RestApiClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rest = rest
        self._experiment_id: UUID | None = None
        self._session_id: UUID | None = None
        self._before_id: int | None = None
        self._list_operation: str | None = None
        self._detail_operation: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        rest.request_succeeded.connect(self._success)
        rest.request_failed.connect(self._failure)

    def set_session(self, value: object) -> None:
        view = value if isinstance(value, AgentSessionView) else None
        experiment_id = view.experiment_id if view else None
        session_id = view.session_id if view else None
        if (experiment_id, session_id) == (self._experiment_id, self._session_id):
            return
        self._experiment_id, self._session_id = experiment_id, session_id
        self._before_id = None
        self._list_operation = self._detail_operation = None
        self.page_changed.emit(None)
        if self._timer.isActive():
            self.refresh()

    def set_visible(self, visible: bool) -> None:
        if visible:
            self._timer.start()
            self.refresh()
        else:
            self._timer.stop()

    def load_page(self, before_id: int | None) -> None:
        self._before_id = before_id
        self._list_operation = None
        self._detail_operation = None
        self.refresh()

    def refresh(self) -> None:
        if self._experiment_id and self._session_id and self._list_operation is None:
            operation = f"agent-decisions:list:{uuid4()}"
            self._list_operation = operation
            self._rest.get_agent_decisions(self._experiment_id, operation, self._before_id)

    def load_detail(self, session_id: UUID, record_id: int) -> None:
        if self._experiment_id and session_id == self._session_id:
            operation = f"agent-decisions:detail:{uuid4()}"
            self._detail_operation = operation
            self._rest.get_agent_decision(self._experiment_id, session_id, record_id, operation)

    def _success(self, operation: str, payload: object) -> None:
        try:
            if operation == self._list_operation:
                self._list_operation = None
                page = DecisionPage.model_validate(payload)
                if page.session_id == self._session_id:
                    self.page_changed.emit(page)
            elif operation == self._detail_operation:
                self._detail_operation = None
                self.detail_changed.emit(self._session_id, DecisionRecord.model_validate(payload))
        except ValidationError:
            self.error.emit("逐轮记录格式不正确，请检查前后端版本")

    def _failure(self, operation: str, message: str) -> None:
        if operation not in {self._list_operation, self._detail_operation}:
            return
        if operation == self._list_operation:
            self._list_operation = None
        else:
            self._detail_operation = None
        self.error.emit(message)
