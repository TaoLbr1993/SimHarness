"""Experiment-scoped visual Agent configuration and lifecycle controls."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.models import AgentApiSummary
from ui.models.agent_session import AgentSessionConfig, AgentSessionView
from ui.views.components import PAGE_CONTENT_MARGIN, page_header, panel
from ui.widgets.agent_decisions import AgentDecisionsWidget
from ui.widgets.agent_group_editor import AgentGroupEditor

_LABELS = {
    "idle": "等待接入",
    "running": "Agent 已接入",
    "pausing": "正在暂停",
    "paused": "已暂停接入",
    "stopping": "正在结束",
    "stopped": "接入已结束",
    "failed": "接入失败",
}


class AgentConnectionPage(QWidget):
    command_requested = Signal(str, object)
    visibility_changed = Signal(bool)
    monitor_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("agentConnectionPage")
        self._view: AgentSessionView | None = None
        self._busy = False
        self._group_number = 0
        self._assets: tuple[AgentApiSummary, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        monitor = QPushButton("查看仿真")
        monitor.clicked.connect(self.monitor_requested)
        root.addWidget(page_header("智能体接入", "为当前实验分配控制器，然后开始决策", monitor))
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(PAGE_CONTENT_MARGIN, 16, PAGE_CONTENT_MARGIN, 16)
        self.experiment = QLabel("尚未关联实验")
        self.experiment.setWordWrap(True)
        self.status = QLabel("请先在仿真配置中勾选“等待外部 Agent 控制”，再开始仿真。")
        self.status.setWordWrap(True)
        self.runtime_note = QLabel("模型与环境变量在后端 Agent 运行器中读取；页面不保存 API Key。")
        self.runtime_note.setObjectName("caption")
        self.runtime_note.setWordWrap(True)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(PAGE_CONTENT_MARGIN, 10, PAGE_CONTENT_MARGIN, 4)
        for widget in (self.experiment, self.status, self.runtime_note):
            summary_layout.addWidget(widget)
        root.addWidget(summary)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("控制组 · 每组一个模型，可控制多辆车"))
        toolbar.addStretch()
        self.add_button = QPushButton("＋ 添加控制组")
        self.add_button.clicked.connect(self.add_group)
        toolbar.addWidget(self.add_button)
        layout.addLayout(toolbar)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.remove_group)
        layout.addWidget(self.tabs)
        self.parameters = QWidget()
        form = QFormLayout(self.parameters)
        form.setContentsMargins(0, 0, 0, 0)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.1, 100000)
        self.radius.setValue(100)
        self.max_steps = QSpinBox()
        self.max_steps.setRange(1, 1000000)
        self.max_steps.setValue(100)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 60)
        self.delay.setDecimals(2)
        form.addRow("局部观测半径 / 米", self.radius)
        form.addRow("本次最多推进帧数", self.max_steps)
        form.addRow("每轮额外间隔 / 秒", self.delay)
        layout.addWidget(panel("实验接入参数", self.parameters))
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.sections = QTabWidget()
        self.sections.addTab(scroll, "接入配置")
        self.decisions = AgentDecisionsWidget()
        history = QWidget()
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(PAGE_CONTENT_MARGIN, 12, PAGE_CONTENT_MARGIN, 12)
        history_layout.addWidget(self.decisions)
        self.sections.addTab(history, "逐轮输入 / 输出")
        root.addWidget(self.sections, 1)
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(PAGE_CONTENT_MARGIN, 8, PAGE_CONTENT_MARGIN, 12)
        self.message = QLabel("未绑定或无有效模型动作的车辆继续使用 SUMO 原生控制。")
        self.message.setWordWrap(True)
        actions_layout.addWidget(self.message)
        footer = QHBoxLayout()
        footer.addStretch()
        self.check_button = QPushButton("检查配置")
        self.stop_button = QPushButton("结束接入")
        self.start_button = QPushButton("开始接入")
        self.start_button.setObjectName("primaryButton")
        self.check_button.clicked.connect(lambda: self._command("check"))
        self.start_button.clicked.connect(self._start_or_pause)
        self.stop_button.clicked.connect(lambda: self._command("stop"))
        for button in (self.check_button, self.stop_button, self.start_button):
            footer.addWidget(button)
        actions_layout.addLayout(footer)
        root.addWidget(actions)
        self.add_group()
        self._update_controls()

    def groups(self) -> tuple[AgentGroupEditor, ...]:
        return tuple(
            widget
            for index in range(self.tabs.count())
            if isinstance(widget := self.tabs.widget(index), AgentGroupEditor)
        )

    def add_group(self) -> None:
        self._group_number += 1
        editor = AgentGroupEditor(f"控制组 {self._group_number}")
        editor.set_assets(self._assets)
        if self._view:
            editor.set_vehicles(self._view.vehicle_ids)
        editor.selection_changed.connect(self._update_controls)
        index = self.tabs.addTab(editor, f"控制组 {self._group_number}")
        editor.name_input.textChanged.connect(
            lambda name: self.tabs.setTabText(self.tabs.indexOf(editor), name or "未命名控制组")
        )
        self.tabs.setCurrentIndex(index)
        self._update_controls()

    def remove_group(self, index: int) -> None:
        if self.tabs.count() <= 1 or self._locked():
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        self._update_controls()

    def configuration(self) -> AgentSessionConfig:
        groups = tuple(editor.configuration() for editor in self.groups())
        seen: set[str] = set()
        names: set[str] = set()
        for group in groups:
            if not group.name or group.name in names:
                raise ValueError("每个控制组需要一个不同的名称")
            names.add(group.name)
            if not group.model.base_url or not group.model.model_id:
                raise ValueError(f"{group.name}：请填写服务地址和模型 ID")
            if not group.all_vehicles and not group.vehicle_ids:
                raise ValueError(f"{group.name}：请选择车辆或填写车辆 ID")
            if group.all_vehicles and len(groups) > 1:
                raise ValueError("全部车辆不能与其他控制组同时使用")
            if seen.intersection(group.vehicle_ids):
                raise ValueError("同一辆车不能分配到多个控制组")
            seen.update(group.vehicle_ids)
        return AgentSessionConfig(
            bindings=groups,
            observation_radius_m=self.radius.value(),
            max_steps=self.max_steps.value(),
            step_delay_s=self.delay.value(),
        )

    def set_configuration(self, value: object) -> None:
        if not isinstance(value, AgentSessionConfig):
            return
        while self.tabs.count():
            old = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if old is not None:
                old.deleteLater()
        for group in value.bindings:
            self.add_group()
            self.groups()[-1].set_configuration(group)
            self.tabs.setTabText(self.tabs.count() - 1, group.name)
        self.radius.setValue(value.observation_radius_m)
        self.max_steps.setValue(value.max_steps)
        self.delay.setValue(value.step_delay_s)
        self._update_controls()

    def set_assets(self, value: object) -> None:
        self._assets = (
            tuple(item for item in value if isinstance(item, AgentApiSummary))
            if isinstance(value, tuple)
            else ()
        )
        for editor in self.groups():
            editor.set_assets(self._assets)

    def set_view(self, value: object) -> None:
        previous_status = self._view.status if self._view else None
        self._view = value if isinstance(value, AgentSessionView) else None
        if self._view:
            view = self._view
            self.experiment.setText(f"当前实验：{view.experiment_id}  ·  第 {view.sequence} 帧")
            self.status.setText(f"{_LABELS.get(view.status, view.status)} · {view.message}")
            if not view.external_stepping:
                self.status.setText("此实验未开启外部步进，请创建勾选“等待外部 Agent 控制”的仿真。")
            for editor in self.groups():
                editor.set_vehicles(view.vehicle_ids)
            if view.status == "running" and previous_status != "running":
                self.sections.setCurrentIndex(1)
        else:
            self.experiment.setText("尚未关联实验")
            self.status.setText("请先开始等待外部 Agent 控制的仿真。")
            for editor in self.groups():
                editor.set_vehicles(())
        self._update_controls()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_controls()

    def _locked(self) -> bool:
        return self._busy or bool(
            self._view and self._view.status in {"running", "pausing", "stopping"}
        )

    def _update_controls(self) -> None:
        locked = self._locked()
        for editor in self.groups():
            editor.setEnabled(not locked)
        self.parameters.setEnabled(not locked)
        self.add_button.setEnabled(
            not locked
            and len(self.groups()) < 16
            and not any(editor.selector.currentData() for editor in self.groups())
        )
        self.tabs.setTabsClosable(not locked and self.tabs.count() > 1)
        ready = bool(
            self._view
            and self._view.external_stepping
            and self._view.experiment_status == "RUNNING"
        )
        status = self._view.status if self._view else "idle"
        self.start_button.setText(
            "暂停接入" if status == "running" else "继续接入" if status == "paused" else "开始接入"
        )
        self.start_button.setEnabled(
            ready and not self._busy and status not in {"pausing", "stopping"}
        )
        self.check_button.setEnabled(ready and not locked)
        self.stop_button.setEnabled(not self._busy and status in {"running", "paused", "pausing"})

    def _start_or_pause(self) -> None:
        self._command("pause" if self._view and self._view.status == "running" else "start")

    def _command(self, action: str) -> None:
        try:
            config = self.configuration() if action in {"check", "start"} else None
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.command_requested.emit(action, config)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)
