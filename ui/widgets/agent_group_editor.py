"""Model, per-vehicle context and vehicle assignment form for one control group."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ui.models import AgentApiSummary
from ui.models.agent_session import ControlGroup, MemorySettings, ModelSettings
from ui.views.components import panel

SKILLS = (
    ("human_cautious", "谨慎驾驶"),
    ("human_normal", "均衡驾驶"),
    ("human_reckless", "鲁莽驾驶"),
)


class AgentGroupEditor(QWidget):
    selection_changed = Signal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._assets: tuple[AgentApiSummary, ...] = ()
        self._available: tuple[str, ...] = ()
        self._updating = False
        self.name_input = QLineEdit(name)
        self.source = QComboBox()
        self.source.addItem("本次配置")
        self.kind = QComboBox()
        for label, value in (
            ("远程 API · OpenAI 兼容", "api"),
            ("本地服务 · OpenAI 兼容", "local"),
            ("Jev", "jev"),
        ):
            self.kind.addItem(label, value)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://your-model-service/v1")
        self.model_id = QLineEdit()
        self.model_id.setPlaceholderText("模型 ID")
        self.key_env = QLineEdit("MODEL_API_KEY")
        self.key_env.setPlaceholderText("环境变量名称，不是 Key 本身")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 300)
        self.timeout.setValue(30)
        self.jev_acceleration = QDoubleSpinBox()
        self.jev_acceleration.setRange(0.01, 100)
        self.jev_acceleration.setValue(1)
        model = QWidget()
        form = QFormLayout(model)
        form.setContentsMargins(0, 0, 0, 0)
        for title, field in (
            ("控制组名称", self.name_input),
            ("配置来源", self.source),
            ("接入方式", self.kind),
            ("服务地址", self.url),
            ("模型 ID", self.model_id),
            ("Key 环境变量名称", self.key_env),
            ("模型超时 / 秒", self.timeout),
        ):
            form.addRow(title, field)
        self.jev_label = QLabel("Jev 加减速 / m/s²")
        form.addRow(self.jev_label, self.jev_acceleration)
        note = QLabel("凭证从后端 Agent 运行器的环境读取；本地无需鉴权时可以留空。")
        note.setObjectName("caption")
        note.setWordWrap(True)
        form.addRow(note)
        self.memory = QCheckBox("启用按车记忆")
        self.memory_status = QLabel("未启用")
        self.memory_steps = QSpinBox()
        self.memory_steps.setRange(1, 1000)
        self.memory_steps.setValue(10)
        memory_body = QWidget()
        memory_form = QFormLayout(memory_body)
        memory_form.setContentsMargins(0, 0, 0, 0)
        memory_form.addRow(
            self._context_title(
                "按车辆独立保存历史", self.memory_status, QStyle.StandardPixmap.SP_DriveHDIcon
            )
        )
        self.memory.setMinimumHeight(32)
        memory_form.addRow(self.memory)
        self.memory_steps_label = QLabel("每辆车保留最近轮数")
        memory_form.addRow(self.memory_steps_label, self.memory_steps)
        memory_note = QLabel("每轮将历史观测、动作与结果加入模型输入。默认关闭。")
        memory_note.setWordWrap(True)
        memory_note.setObjectName("caption")
        memory_form.addRow(memory_note)
        self.skill_status = QLabel("未启用")
        skill_body = QWidget()
        skill_layout = QVBoxLayout(skill_body)
        skill_layout.setContentsMargins(0, 0, 0, 0)
        skill_layout.addWidget(
            self._context_title(
                "为模型设置驾驶风格",
                self.skill_status,
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
            )
        )
        choices = QWidget()
        choices_layout = QHBoxLayout(choices)
        choices_layout.setContentsMargins(0, 0, 0, 0)
        self.skills = {}
        for key, label in SKILLS:
            checkbox = QCheckBox(label)
            checkbox.setMinimumHeight(32)
            self.skills[key] = checkbox
            choices_layout.addWidget(checkbox)
            checkbox.toggled.connect(self._context_changed)
        skill_layout.addWidget(choices)
        skill_note = QLabel("风格作为决策偏好加入模型输入。默认不选择，建议每组选择一种。")
        skill_note.setWordWrap(True)
        skill_note.setObjectName("caption")
        skill_layout.addWidget(skill_note)
        skill_layout.addStretch()
        self.memory.toggled.connect(self._context_changed)
        vehicles = QWidget()
        vehicle_layout = QVBoxLayout(vehicles)
        vehicle_layout.setContentsMargins(0, 0, 0, 0)
        self.selector = QComboBox()
        self.selector.addItem("指定车辆 ID", False)
        self.selector.addItem("全部车辆（包括后续进入）", True)
        vehicle_layout.addWidget(self.selector)
        self.vehicle_note = QLabel("初始帧暂无车辆，可填写车辆 ID，或选择全部车辆。")
        self.vehicle_note.setWordWrap(True)
        self.vehicle_note.setObjectName("caption")
        vehicle_layout.addWidget(self.vehicle_note)
        self.vehicle_list = QListWidget()
        self.vehicle_list.setMinimumHeight(120)
        self.vehicle_list.setMaximumHeight(230)
        vehicle_layout.addWidget(self.vehicle_list)
        vehicle_layout.addWidget(QLabel("已选或待出现的车辆 ID（逗号分隔）"))
        self.vehicle_ids = QLineEdit()
        self.vehicle_ids.setPlaceholderText("例如 car_001, car_002")
        vehicle_layout.addWidget(self.vehicle_ids)
        note = QLabel("同一辆车只能属于一个控制组。未绑定或本轮无有效动作的车辆使用 SUMO 控制。")
        note.setWordWrap(True)
        note.setObjectName("caption")
        vehicle_layout.addWidget(note)
        vehicle_layout.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        model_row = QHBoxLayout()
        model_row.addWidget(panel("模型与上下文", model), 3)
        model_row.addWidget(panel("车辆分配", vehicles), 2)
        layout.addLayout(model_row)
        context_row = QHBoxLayout()
        self.memory_panel = panel("Memory · 车辆记忆", memory_body)
        self.skill_panel = panel("Skill · 驾驶风格", skill_body)
        context_row.addWidget(self.memory_panel, 1)
        context_row.addWidget(self.skill_panel, 1)
        layout.addLayout(context_row)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.selector.currentIndexChanged.connect(self._selector_changed)
        self.vehicle_ids.editingFinished.connect(self._refresh_vehicles)
        self.vehicle_list.itemChanged.connect(self._vehicle_checked)
        self._kind_changed()
        self._context_changed()

    def _context_title(self, text: str, status: QLabel, icon: QStyle.StandardPixmap) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        mark = QLabel()
        mark.setPixmap(self.style().standardIcon(icon).pixmap(26, 26))
        layout.addWidget(mark)
        layout.addWidget(QLabel(text))
        layout.addStretch()
        status.setObjectName("caption")
        layout.addWidget(status)
        return widget

    def _context_changed(self) -> None:
        enabled = self.memory.isChecked()
        self.memory_status.setText("已启用" if enabled else "未启用")
        self.memory_steps.setVisible(enabled)
        self.memory_steps_label.setVisible(enabled)
        selected = sum(box.isChecked() for box in self.skills.values())
        self.skill_status.setText(f"已选 {selected} 项" if selected else "未启用")

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.jev_label.setVisible(kind == "jev")
        self.jev_acceleration.setVisible(kind == "jev")
        if self._updating:
            return
        if kind == "jev":
            self.url.setText("https://openrouter.ai/api")
            self.model_id.setText("typesafe/jev-1.14")
            self.key_env.setText("OPENROUTER_JEV_SK")
        elif kind == "local":
            self.url.setText("")
            self.url.setPlaceholderText("http://127.0.0.1:8002/v1")
            self.key_env.clear()

    def _selector_changed(self) -> None:
        all_vehicles = bool(self.selector.currentData())
        self.vehicle_list.setEnabled(not all_vehicles)
        self.vehicle_ids.setEnabled(not all_vehicles)
        self.selection_changed.emit()

    def set_assets(self, assets: tuple[AgentApiSummary, ...]) -> None:
        self._assets = assets
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("本次配置")
        for asset in assets:
            self.source.addItem(asset.name)
        self.source.blockSignals(False)

    def _source_changed(self, index: int) -> None:
        if 1 <= index <= len(self._assets):
            asset = self._assets[index - 1]
            self.kind.setCurrentIndex(0)
            self.url.setText(asset.api_base_url)
            self.model_id.setText(asset.model_id)
            self.key_env.setText(asset.credential_env_var)

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(filter(None, re.split(r"[,，\s]+", self.vehicle_ids.text().strip())))
        )

    def set_vehicles(self, values: tuple[str, ...]) -> None:
        if self._available != values:
            self._available = values
            self._refresh_vehicles()

    def _refresh_vehicles(self) -> None:
        selected = self.selected_ids()
        self.vehicle_list.blockSignals(True)
        self.vehicle_list.clear()
        for vehicle_id in dict.fromkeys((*self._available, *selected)):
            item = QListWidgetItem(
                vehicle_id + (" · 待出现" if vehicle_id not in self._available else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, vehicle_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if vehicle_id in selected else Qt.CheckState.Unchecked
            )
            self.vehicle_list.addItem(item)
        self.vehicle_list.blockSignals(False)
        self.vehicle_note.setText(
            f"当前帧在场车辆：{len(self._available)}"
            if self._available
            else "初始帧暂无车辆，可填写车辆 ID，或选择全部车辆。"
        )

    def _vehicle_checked(self, item: QListWidgetItem) -> None:
        values = list(self.selected_ids())
        value = str(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked and value not in values:
            values.append(value)
        elif item.checkState() != Qt.CheckState.Checked and value in values:
            values.remove(value)
        self.vehicle_ids.setText(", ".join(values))

    def configuration(self) -> ControlGroup:
        kind = self.kind.currentData()
        if kind not in {"api", "local", "jev"}:
            raise ValueError("请选择有效的模型接入方式")
        return ControlGroup(
            name=self.name_input.text().strip(),
            kind=kind,
            model=ModelSettings(
                base_url=self.url.text().strip(),
                model_id=self.model_id.text().strip(),
                api_key_env=self.key_env.text().strip() or None,
                timeout_s=self.timeout.value(),
                jev_acceleration_mps2=self.jev_acceleration.value(),
            ),
            vehicle_ids=() if self.selector.currentData() else self.selected_ids(),
            all_vehicles=bool(self.selector.currentData()),
            memory=MemorySettings(
                enabled=self.memory.isChecked(), max_steps=self.memory_steps.value()
            ),
            skills=tuple(key for key, checkbox in self.skills.items() if checkbox.isChecked()),
        )

    def set_configuration(self, group: ControlGroup) -> None:
        self._updating = True
        self.name_input.setText(group.name)
        self.kind.setCurrentIndex(self.kind.findData(group.kind))
        self.url.setText(group.model.base_url)
        self.model_id.setText(group.model.model_id)
        self.key_env.setText(group.model.api_key_env or "")
        self.timeout.setValue(int(group.model.timeout_s))
        self.jev_acceleration.setValue(group.model.jev_acceleration_mps2)
        self.memory.setChecked(group.memory.enabled)
        self.memory_steps.setValue(group.memory.max_steps)
        for key, checkbox in self.skills.items():
            checkbox.setChecked(key in group.skills)
        self.selector.setCurrentIndex(1 if group.all_vehicles else 0)
        self.vehicle_ids.setText(", ".join(group.vehicle_ids))
        self._updating = False
        self._refresh_vehicles()
