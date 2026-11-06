"""Round navigation and vehicle-scoped decision cards using typed trace previews."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.models.agent_decision import DecisionRecord, DecisionSummary
from ui.models.decision_text import (
    OUTCOME_LABELS,
    SKILL_LABELS,
    STATE_LABELS,
    action_rows,
    action_text,
    decode_text,
    response_excerpt,
)


def _label(text: str = "", name: str = "decisionBody") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _card(title: str, subtitle: str, body: QWidget) -> QFrame:
    card = QFrame()
    card.setObjectName("decisionCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(8)
    layout.addWidget(_label(title, "decisionCardTitle"))
    if subtitle:
        layout.addWidget(_label(subtitle, "decisionCaption"))
    layout.addWidget(body)
    layout.addStretch()
    return card


class AgentDecisionCards(QWidget):
    record_selected = Signal(int)
    raw_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._record: DecisionRecord | None = None
        self._actions: tuple[tuple[str, str], ...] | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(14)
        sidebar = QWidget()
        sidebar.setObjectName("decisionSidebar")
        sidebar.setFixedWidth(188)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 4, 0, 0)
        side.addWidget(_label("决策记录", "decisionCardTitle"))
        side.addWidget(_label("每个控制组 · 每个输入帧", "decisionCaption"))
        self.rounds = QListWidget()
        self.rounds.setObjectName("decisionRounds")
        self.rounds.setWordWrap(True)
        self.rounds.currentItemChanged.connect(self._choose_round)
        side.addWidget(self.rounds, 1)
        layout.addWidget(sidebar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("decisionScroll")
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("decisionContent")
        stack = QVBoxLayout(content)
        stack.setContentsMargins(0, 0, 8, 0)
        stack.setSpacing(12)
        hero = QFrame()
        hero.setObjectName("decisionHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        top = QHBoxLayout()
        self.heading = _label(name="decisionHeading")
        self.badge = _label(name="decisionBadge")
        self.badge.setMaximumWidth(220)
        top.addWidget(self.heading, 1)
        top.addWidget(self.badge)
        hero_layout.addLayout(top)
        self.subtitle = _label(name="decisionCaption")
        hero_layout.addWidget(self.subtitle)
        stack.addWidget(hero)
        stats = QHBoxLayout()
        self.metrics: dict[str, QLabel] = {}
        for key, title in (
            ("vehicles", "控制车辆"),
            ("actions", "有效动作"),
            ("sumo", "SUMO 控制"),
            ("time", "推理耗时"),
        ):
            value = _label("—", "decisionMetric")
            self.metrics[key] = value
            stats.addWidget(_card(title, "", value), 1)
        stack.addLayout(stats)
        vehicle_row = QHBoxLayout()
        vehicle_row.addWidget(_label("车辆输入", "decisionCardTitle"))
        self.vehicle = QComboBox()
        self.vehicle.setMinimumWidth(160)
        self.vehicle.currentIndexChanged.connect(self._show_vehicle)
        vehicle_row.addWidget(self.vehicle)
        vehicle_row.addStretch()
        raw = QPushButton("查看原始详情 ↗")
        raw.setObjectName("decisionRawButton")
        raw.clicked.connect(self.raw_requested)
        vehicle_row.addWidget(raw)
        stack.addLayout(vehicle_row)
        self.preview_note = _label(name="decisionCaption")
        stack.addWidget(self.preview_note)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.observation = _label()
        self.memory = _label()
        self.skill = _label()
        self.action = _label()
        for row, column, title, subtitle, body in (
            (0, 0, "周边观测", "自车状态与感知范围内的车辆", self.observation),
            (0, 1, "Memory · 车辆记忆", "这一次实际加入模型的历史", self.memory),
            (1, 0, "Skill · 驾驶风格", "这一次实际加入模型的指令", self.skill),
            (1, 1, "车辆控制", "校验后的动作与提交状态", self.action),
        ):
            grid.addWidget(_card(title, subtitle, body), row, column)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        stack.addLayout(grid)
        self.output = QPlainTextEdit()
        self.output.setObjectName("decisionOutput")
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(120)
        self.output.setMaximumHeight(180)
        stack.addWidget(
            _card("模型输出", "只摘录模型实际返回的内容；候选动作不代表已执行", self.output)
        )
        self.batch_actions = _label()
        stack.addWidget(
            _card("本组动作", "通过校验的请求；底层执行结果以仿真事件为准", self.batch_actions)
        )
        stack.addStretch()
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area, 1)
        self.clear()

    def clear(self, message: str = "开始接入后，逐轮信息将在这里更新。") -> None:
        self._record = None
        self._actions = None
        self.heading.setText("等待智能体决策")
        self.subtitle.setText(message)
        self.badge.setText("等待记录")
        self._set_tone("neutral")
        for metric in self.metrics.values():
            metric.setText("—")
        self.vehicle.blockSignals(True)
        self.vehicle.clear()
        self.vehicle.blockSignals(False)
        self.vehicle.setEnabled(False)
        for label in (self.observation, self.memory, self.skill, self.action):
            label.setText("等待本轮输入…")
        self.preview_note.setText("")
        self.output.setPlainText("模型返回后显示响应摘要。")
        self.batch_actions.setText("暂无动作。")

    def set_summaries(self, items: tuple[DecisionSummary, ...], selected: int | None) -> None:
        position = self.rounds.verticalScrollBar().value()
        previous = self.rounds.currentItem()
        previous_id = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        self.rounds.blockSignals(True)
        self.rounds.clear()
        for summary in items:
            item = QListWidgetItem(
                f"第 {summary.sequence} 帧 · {summary.group_name}\n"
                f"{summary.model_id}\n"
                f"{STATE_LABELS.get(summary.state, summary.state)} · "
                f"{OUTCOME_LABELS.get(summary.outcome, summary.outcome)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, summary.record_id)
            item.setSizeHint(QSize(168, 94))
            item.setToolTip(f"{summary.group_name} / {summary.model_id}")
            self.rounds.addItem(item)
            if summary.record_id == selected:
                self.rounds.setCurrentItem(item)
        self.rounds.blockSignals(False)
        if previous_id == selected:
            self.rounds.verticalScrollBar().setValue(position)

    def _choose_round(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current:
            self.record_selected.emit(int(current.data(Qt.ItemDataRole.UserRole)))

    def _set_tone(self, tone: str) -> None:
        self.badge.setProperty("tone", tone)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def set_record(self, record: DecisionRecord) -> None:
        same = self._record is not None and self._record.record_id == record.record_id
        previous_vehicle = self.vehicle.currentText()
        self._record = record
        self._actions = action_rows(record.actions_text)
        self.heading.setText(f"第 {record.sequence} 帧 · {record.group_name}")
        self.subtitle.setText(
            f"{record.model_id}  ·  仿真时间 {record.simulation_time_ms / 1000:g} s"
        )
        self.badge.setText(
            f"{STATE_LABELS.get(record.state, record.state)} · "
            f"{OUTCOME_LABELS.get(record.outcome, record.outcome)}"
        )
        tone = "neutral"
        if record.outcome in {"discarded", "failed"} or record.state == "failed":
            tone = "warning"
        elif record.outcome == "submitted":
            tone = "success"
        elif record.state == "running":
            tone = "active"
        self._set_tone(tone)
        self.metrics["vehicles"].setText(str(record.vehicle_count))
        self.metrics["actions"].setText(
            "—" if record.state == "running" else str(record.action_count)
        )
        self.metrics["sumo"].setText(
            str(record.fallback_count) if record.outcome == "submitted" else "—"
        )
        self.metrics["time"].setText(
            "推理中" if record.state == "running" else f"{record.elapsed_ms / 1000:.2f} s"
        )
        preview = record.input_preview
        self.vehicle.blockSignals(True)
        self.vehicle.clear()
        if preview:
            self.vehicle.addItems([item.vehicle_id for item in preview.vehicles])
        index = self.vehicle.findText(previous_vehicle)
        self.vehicle.setCurrentIndex(max(0, index))
        self.vehicle.blockSignals(False)
        self.vehicle.setEnabled(self.vehicle.count() > 0)
        note = "切换车辆可查看该车的输入与动作。"
        if not preview or not preview.vehicles:
            note = "本轮无车辆输入概要。旧版记录可切换至原始详情查看。"
        elif preview.truncated:
            note = (
                f"概要展示 {len(preview.vehicles)} / {record.vehicle_count} 辆车，"
                "每车最多 3 辆邻车、2 轮记忆、3 个 Skill；部分内容已省略。"
            )
        if record.truncated:
            note += " 原始长文本已截断；卡片概要独立保存。"
        self.preview_note.setText(note)
        self._show_vehicle()
        text = response_excerpt(record.output_text, running=record.state == "running")
        if self.output.toPlainText() != text:
            position = self.output.verticalScrollBar().value()
            self.output.setPlainText(text)
            if same:
                self.output.verticalScrollBar().setValue(position)
        if record.state == "running":
            batch = "等待模型返回和动作校验。"
        elif self._actions is None:
            batch = "动作文本不完整，无法生成概要；请查看原始详情。"
        else:
            batch = "\n".join(f"{vehicle}  →  {action}" for vehicle, action in self._actions[:30])
            if len(self._actions) > 30:
                batch += "\n仅展示前 30 个动作；其余见原始详情。"
            batch = batch or "本组没有有效外部动作。"
        if record.outcome in {"discarded", "failed"}:
            batch = "本轮结果未提交，下面的动作未执行。\n" + batch
        self.batch_actions.setText(batch)
        if not same:
            self.scroll_area.verticalScrollBar().setValue(0)

    def _show_vehicle(self, _index: int = 0) -> None:
        record = self._record
        index = self.vehicle.currentIndex()
        if not record or not record.input_preview or index < 0:
            for label in (self.observation, self.memory, self.skill, self.action):
                label.setText("暂无车辆概要，请查看原始详情。")
            return
        item = record.input_preview.vehicles[index]
        lines = [
            f"{item.speed_mps:g} m/s  ·  {item.speed_mps * 3.6:.1f} km/h",
            f"车道 {item.lane_id}",
            f"感知半径 {item.radius_m:g} m · 周边 {item.nearby_count} 辆车",
        ]
        lines.extend(
            f"{near.vehicle_id}  ·  {near.distance_m:.1f} m  ·  {near.speed_mps:g} m/s"
            for near in item.nearby
        )
        self.observation.setText("\n".join(lines))
        memory = (
            "本轮未加入 Memory。"
            if item.memory_steps is None
            else "Memory 已启用，当前还没有历史。"
            if item.memory_steps == 0
            else f"已加入 {item.memory_steps} 轮历史 · 展示最近 {len(item.recent_memory)} 轮"
        )
        for turn in item.recent_memory:
            result = (
                f"后续速度 {turn.next_speed_mps:g} m/s"
                if turn.next_speed_mps is not None
                else "后续状态未知"
            )
            memory += (
                f"\n第 {turn.sequence} 帧 · {action_text(decode_text(turn.action_text))}\n{result}"
            )
        self.memory.setText(memory)
        self.skill.setText(
            "\n\n".join(
                f"{SKILL_LABELS.get(skill.name, skill.name)}\n{skill.content}"
                for skill in item.skills
            )
            or "本轮未加入 Skill。"
        )
        if record.state == "running":
            action = "模型正在推理，尚未生成本轮动作。"
        elif self._actions is None:
            action = "动作文本不完整，无法确认该车动作。"
        else:
            action = dict(self._actions).get(item.vehicle_id, "本轮没有有效外部动作")
            if item.vehicle_id not in dict(self._actions) and record.outcome == "submitted":
                action += "\n由 SUMO 控制。"
            elif record.outcome == "submitted":
                action += "\n已随本轮提交；执行结果以仿真事件为准。"
        if record.outcome == "discarded":
            action += "\n本轮结果已丢弃，未执行。"
        elif record.outcome in {"pending", "failed"} and record.state != "running":
            action += "\n尚未提交到仿真。"
        self.action.setText(action)
