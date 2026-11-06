"""Read-only per-round model inputs, raw outputs, and validated action inspection."""

import json
from contextlib import suppress
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.models.agent_decision import DecisionPage, DecisionRecord
from ui.models.decision_text import OUTCOME_LABELS as _OUTCOME
from ui.models.decision_text import STATE_LABELS as _STATE
from ui.widgets.agent_decision_cards import AgentDecisionCards


class AgentDecisionsWidget(QWidget):
    page_requested = Signal(object)
    detail_requested = Signal(object, int)

    def __init__(self) -> None:
        super().__init__()
        self._page = DecisionPage()
        self._selection: int | None = None
        self._page_stack: list[int | None] = [None]
        self._detail_signature: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.summary = QLabel("开始接入后，这里会显示每轮输入和输出。")
        self.summary.setWordWrap(True)
        toolbar.addWidget(self.summary, 1)
        self.follow = QCheckBox("跟随最新一轮")
        self.follow.setChecked(True)
        self.follow.toggled.connect(self._follow_changed)
        toolbar.addWidget(self.follow)
        self.newer = QPushButton("较新记录")
        self.older = QPushButton("较早记录")
        self.newer.clicked.connect(self._newer)
        self.older.clicked.connect(self._older)
        toolbar.addWidget(self.newer)
        toolbar.addWidget(self.older)
        layout.addLayout(toolbar)
        original = QWidget()
        original_layout = QVBoxLayout(original)
        original_layout.setContentsMargins(0, 10, 0, 0)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["输入帧", "控制组 / 模型", "车辆", "推理 / 提交状态", "耗时", "有效 / SUMO"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(100)
        self.table.setMaximumHeight(240)
        self.table.cellClicked.connect(self._row_clicked)
        original_layout.addWidget(self.table)
        self.detail_note = QLabel("选择一条记录查看详情。")
        self.detail_note.setWordWrap(True)
        layout.addWidget(self.detail_note)
        self.detail_tabs = QTabWidget()
        self.editors: dict[str, QPlainTextEdit] = {}
        for key, title in (
            ("input_text", "输入 · 观测 / Memory / Skill"),
            ("request_text", "模型请求"),
            ("output_text", "原始输出"),
            ("actions_text", "通过校验的动作"),
        ):
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setMinimumHeight(120)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.editors[key] = editor
            self.detail_tabs.addTab(editor, title)
        original_layout.addWidget(self.detail_tabs, 1)
        self.cards = AgentDecisionCards()
        self.cards.record_selected.connect(self._manual_select)
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setObjectName("decisionModes")
        self.mode_tabs.addTab(self.cards, "信息卡片")
        self.mode_tabs.addTab(original, "原始详情")
        self.cards.raw_requested.connect(lambda: self.mode_tabs.setCurrentIndex(1))
        layout.addWidget(self.mode_tabs, 1)
        self._buttons()

    def clear(self) -> None:
        self.follow.blockSignals(True)
        self.follow.setChecked(True)
        self.follow.blockSignals(False)
        self._page = DecisionPage()
        self._selection = None
        self._detail_signature = None
        self._page_stack = [None]
        self.table.setRowCount(0)
        self.cards.set_summaries((), None)
        self.cards.clear()
        for editor in self.editors.values():
            editor.clear()
        self.summary.setText("开始接入后，这里会显示每轮输入和输出。")
        self.detail_note.setText("选择一条记录查看详情。")
        self._buttons()

    def set_page(self, value: object) -> None:
        if not isinstance(value, DecisionPage):
            self.clear()
            return
        if self._page.session_id != value.session_id:
            self.clear()
        self._page = value
        selected = self._selection
        if self.follow.isChecked() or selected not in {item.record_id for item in value.items}:
            selected = value.items[0].record_id if value.items else None
        self.table.setRowCount(len(value.items))
        for row, item in enumerate(value.items):
            cells = (
                str(item.sequence),
                f"{item.group_name} / {item.model_id}",
                str(item.vehicle_count),
                f"{_STATE.get(item.state, item.state)} · "
                f"{_OUTCOME.get(item.outcome, item.outcome)}",
                f"{item.elapsed_ms / 1000:.2f} s",
                f"{item.action_count} / {item.fallback_count}",
            )
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, item.record_id)
                self.table.setItem(row, column, cell)
            if selected == item.record_id:
                self.table.selectRow(row)
        self.summary.setText(f"共 {value.total} 条控制组决策记录 · 每页最多 20 条")
        if value.storage_warning:
            self.summary.setText(self.summary.text() + " · 历史文件写入失败，部分旧记录可能不可用")
        self._buttons()
        if selected is not None:
            self._select(selected)
        else:
            self._selection = None
            self._detail_signature = None
            self.cards.set_summaries((), None)
            self.cards.clear()
            for editor in self.editors.values():
                editor.clear()

    def _select(self, record_id: int) -> None:
        if self._selection != record_id:
            self._detail_signature = None
            for editor in self.editors.values():
                editor.clear()
            self.detail_note.setText("正在读取该轮记录…")
            self.cards.clear("正在读取这一轮的输入与输出…")
        self._selection = record_id
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == record_id:
                self.table.selectRow(row)
        self.cards.set_summaries(self._page.items, record_id)
        if self._page.session_id:
            self.detail_requested.emit(self._page.session_id, record_id)

    def set_detail(self, session_id: UUID, value: object) -> None:
        if not isinstance(value, DecisionRecord) or session_id != self._page.session_id:
            return
        if value.record_id != self._selection:
            return
        signature = value.model_dump_json()
        if signature == self._detail_signature:
            return
        self._detail_signature = signature
        self.cards.set_record(value)
        note = (
            f"第 {value.sequence} 帧 · {value.group_name} · {value.message} · "
            f"{_OUTCOME.get(value.outcome, value.outcome)}"
        )
        if value.truncated:
            note += " · 大体积内容已截断（每栏最多 128,000 字符）"
        self.detail_note.setText(note)
        for key, editor in self.editors.items():
            content = getattr(value, key)
            with suppress(ValueError, TypeError):
                content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
            if not content:
                content = "等待模型返回…" if value.state == "running" else "本轮无此内容"
            if editor.toPlainText() != content:
                position = editor.verticalScrollBar().value()
                editor.setPlainText(content)
                editor.verticalScrollBar().setValue(position)

    def _row_clicked(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self._manual_select(int(item.data(Qt.ItemDataRole.UserRole)))

    def _manual_select(self, record_id: int) -> None:
        self.follow.setChecked(False)
        self._select(record_id)

    def _follow_changed(self, checked: bool) -> None:
        if checked:
            self._page_stack = [None]
            self.page_requested.emit(None)
        elif self._page_stack[-1] is None and self._page.items:
            self._page_stack[-1] = self._page.items[0].record_id + 1
            self.page_requested.emit(self._page_stack[-1])

    def _older(self) -> None:
        if self._page.next_before_id is not None:
            self.follow.setChecked(False)
            self._page_stack.append(self._page.next_before_id)
            self.page_requested.emit(self._page.next_before_id)

    def _newer(self) -> None:
        if len(self._page_stack) > 1:
            self._page_stack.pop()
            self.page_requested.emit(self._page_stack[-1])

    def _buttons(self) -> None:
        self.newer.setEnabled(len(self._page_stack) > 1)
        self.older.setEnabled(self._page.next_before_id is not None)
