"""Nonblocking session lifecycle and stale-response filtering for the desktop."""

from uuid import UUID

from pydantic import ValidationError
from PySide6.QtCore import QObject, QTimer, Signal

from ui.api_client import RestApiClient
from ui.models.agent_session import AgentSessionCheck, AgentSessionConfig, AgentSessionView


class AgentConnectionViewModel(QObject):
    view_changed = Signal(object)
    config_loaded = Signal(object)
    message_changed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, rest: RestApiClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rest = rest
        self._experiment_id: UUID | None = None
        self._pending = False
        self._loaded = False
        self._visible = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        rest.request_succeeded.connect(self._success)
        rest.request_failed.connect(self._failure)

    def bind_experiment(self, value: str) -> None:
        experiment_id = UUID(value) if value else None
        if self._experiment_id == experiment_id:
            return
        self._experiment_id = experiment_id
        self._pending = False
        self._loaded = False
        self.busy_changed.emit(False)
        self.view_changed.emit(None)
        if self._visible:
            self.refresh()

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        if visible:
            self._timer.start()
            self.refresh()
        else:
            self._timer.stop()

    def refresh(self) -> None:
        if self._experiment_id is not None and not self._pending:
            self._pending = True
            self._rest.get_agent_session(self._experiment_id)

    def command(self, action: str, config: AgentSessionConfig | None = None) -> None:
        if self._experiment_id is None:
            self.message_changed.emit("请先开始等待外部 Agent 控制的仿真")
            return
        if self._pending:
            self.message_changed.emit("请求处理中，请稍后重试")
            return
        self._pending = True
        self.busy_changed.emit(True)
        self._rest.agent_session_command(self._experiment_id, action, config)

    def _matches(self, operation: str) -> bool:
        return bool(self._experiment_id and operation.endswith(f":{self._experiment_id}"))

    def _success(self, operation: str, payload: object) -> None:
        if not self._matches(operation):
            return
        self._pending = False
        self.busy_changed.emit(False)
        try:
            if operation.startswith("agent-session:check:"):
                result = AgentSessionCheck.model_validate(payload)
                message = "配置检查通过；未调用模型" if result.ready else "；".join(result.issues)
                if result.warnings:
                    message += "；" + "；".join(result.warnings)
                self.message_changed.emit(message)
                return
            view = AgentSessionView.model_validate(payload)
            if not self._loaded:
                self._loaded = True
                if view.config is not None:
                    self.config_loaded.emit(view.config)
            self.view_changed.emit(view)
        except ValidationError:
            self.message_changed.emit("Agent 接入响应格式不正确，请检查前后端版本")

    def _failure(self, operation: str, message: str) -> None:
        if not self._matches(operation):
            return
        self._pending = False
        self.busy_changed.emit(False)
        self.message_changed.emit(message)
