"""Scenario configuration page for composing a workspace simulation run."""

from __future__ import annotations

import platform

from pydantic import ValidationError
from PySide6.QtCore import Property, QPoint, Qt, QTime, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen, QPolygon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ui.models import (
    AutomationDemand,
    MapSummary,
    SimulationConfigurationDraft,
    TrafficScenarioPreset,
)
from ui.views.components import PAGE_CONTENT_MARGIN, page_header
from ui.widgets import MapLibreDeckMapWidget

_AUTOMATION_LEVELS = tuple(f"L{level}" for level in range(6))
_AUTOMATION_STEPPER_HEIGHT = 36
_MACOS_AUTOMATION_STEPPER_HEIGHT = 48


class _AutomationCountSpinBox(QSpinBox):
    """Draw stable step arrows without relying on platform-native glyphs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arrow_color = QColor()

    def _get_arrow_color(self) -> QColor:
        return self._arrow_color

    def _set_arrow_color(self, color: QColor) -> None:
        self._arrow_color = color

    arrowColor = Property(QColor, _get_arrow_color, _set_arrow_color)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        color = (
            self._arrow_color
            if self._arrow_color.isValid()
            else self.palette().color(QPalette.ColorRole.Text)
        )
        pen = QPen(color)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for subcontrol, points_down in (
            (QStyle.SubControl.SC_SpinBoxUp, False),
            (QStyle.SubControl.SC_SpinBoxDown, True),
        ):
            button = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                subcontrol,
                self,
            )
            center = button.center()
            vertical_offset = 2 if points_down else -2
            painter.drawPolyline(
                QPolygon(
                    (
                        QPoint(center.x() - 4, center.y() - vertical_offset),
                        QPoint(center.x(), center.y() + vertical_offset),
                        QPoint(center.x() + 4, center.y() - vertical_offset),
                    )
                )
            )


class _AutomationConfigurationRow(QFrame):
    """One editable automation-level vehicle allocation."""

    changed = Signal()
    add_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self, level: str, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("automationConfigurationRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        self.level_combo = QComboBox()
        self.level_combo.setObjectName("automationLevelCombo")
        for value in _AUTOMATION_LEVELS:
            self.level_combo.addItem(f"{value}智驾", value)
        self.level_combo.setCurrentIndex(_AUTOMATION_LEVELS.index(level))
        self.level_combo.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.level_combo, 3)

        self.count_input = _AutomationCountSpinBox()
        self.count_input.setObjectName("automationVehicleCount")
        uses_large_stepper = platform.system() == "Darwin"
        self.count_input.setProperty("macosStepper", uses_large_stepper)
        self.count_input.setMinimumHeight(
            _MACOS_AUTOMATION_STEPPER_HEIGHT if uses_large_stepper else _AUTOMATION_STEPPER_HEIGHT
        )
        self.count_input.setRange(0, 100_000)
        self.count_input.setValue(count)
        self.count_input.setSuffix(" 辆")
        self.count_input.valueChanged.connect(self.changed)
        layout.addWidget(self.count_input, 2)

        add_button = QPushButton("+")
        add_button.setObjectName("automationRowAction")
        add_button.setAccessibleName("添加智驾分类")
        add_button.clicked.connect(self.add_requested)
        layout.addWidget(add_button)

        remove_button = QPushButton("×")
        remove_button.setObjectName("automationRowAction")
        remove_button.setProperty("action", "remove")
        remove_button.setAccessibleName("删除智驾分类")
        remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(remove_button)

    @property
    def level(self) -> str:
        value = self.level_combo.currentData()
        return value if isinstance(value, str) else "L0"

    @property
    def vehicle_count(self) -> int:
        return self.count_input.value()


class SceneConfigurationPage(QWidget):
    """Collect map, automation mix, and duration settings for a simulation."""

    map_selected = Signal(str)
    launch_requested = Signal(object)
    test_requested = Signal(object)
    configuration_save_requested = Signal(object)
    generation_page_requested = Signal()
    refresh_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        load_web_map: bool = False,
        traffic_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sceneConfigurationPage")
        self._load_web_map = load_web_map
        self._traffic_mode = traffic_mode
        self._maps: dict[str, MapSummary] = {}
        self._applied_generated_id: str | None = None
        self._generation_busy = False
        self._create_allowed = True
        self.automation_rows: list[_AutomationConfigurationRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(page_header("仿真配置", "", self._header_actions()))

        scroll = QScrollArea()
        scroll.setObjectName("simulationConfigurationScroll")
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("simulationConfigurationBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_CONTENT_MARGIN + 14, 16, PAGE_CONTENT_MARGIN + 14, 20)
        layout.setSpacing(24)
        layout.addWidget(self._scene_information())
        layout.addLayout(self._configuration_columns(), 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def _header_actions(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("simulationHeaderActions")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        self.external_stepping = QCheckBox("等待外部 Agent 控制")
        self.external_stepping.setObjectName("externalSteppingCheckBox")
        row.addWidget(self.external_stepping)
        self.test_button = QPushButton("测试")
        self.test_button.setObjectName("testSimulationConfigurationButton")
        self.test_button.clicked.connect(self._request_test)
        row.addWidget(self.test_button)
        self.create_button = QPushButton("▶  开始仿真")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self._request_launch)
        row.addWidget(self.create_button)
        return widget

    def _scene_information(self) -> QFrame:
        section, layout = self._section("场景信息")

        name_label = self._field_label("场景名称", required=True)
        self.scene_name = QLineEdit()
        self.scene_name.setObjectName("sceneNameInput")
        self.scene_name.setText("未命名场景")
        self.scene_name.setPlaceholderText("请输入场景名称")
        self.scene_name.setClearButtonEnabled(True)
        self.scene_name.setAccessibleName("仿真场景名称")
        layout.addWidget(name_label)
        layout.addWidget(self.scene_name)

        description_label = self._field_label("场景描述")
        self.description = QTextEdit()
        self.description.setObjectName("sceneDescriptionInput")
        self.description.setPlaceholderText("请输入场景描述")
        self.description.setAccessibleName("仿真场景描述")
        self.description.setFixedHeight(88)
        layout.addWidget(description_label)
        layout.addWidget(self.description)
        actions = QHBoxLayout()
        self.generate_button = QPushButton("打开 TrafficForge  →")
        self.generate_button.setObjectName("generateLanguageScenarioButton")
        self.generate_button.clicked.connect(self.generation_page_requested)
        actions.addWidget(self.generate_button)
        self.refresh_scenes_button = QPushButton("刷新场景列表")
        self.refresh_scenes_button.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(self.refresh_scenes_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.generation_status = QLabel(
            "通过 TrafficForge 创建预览并确认添加，再在这里配置或启动仿真。"
        )
        self.generation_status.setObjectName("scenarioGenerationStatus")
        self.generation_status.setWordWrap(True)
        self.generation_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.generation_status)
        return section

    def _configuration_columns(self) -> QGridLayout:
        columns = QGridLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setHorizontalSpacing(28)
        columns.setVerticalSpacing(0)
        columns.addWidget(self._map_configuration(), 0, 0)
        columns.addWidget(self._right_configuration(), 0, 1)
        columns.setColumnStretch(0, 1)
        columns.setColumnStretch(1, 1)
        return columns

    def _map_configuration(self) -> QFrame:
        section, layout = self._section("地图选择")
        self.map_combo = QComboBox()
        self.map_combo.setObjectName("simulationMapCombo")
        self.map_combo.setMinimumContentsLength(24)
        self.map_combo.currentIndexChanged.connect(self._select_map)
        layout.addWidget(self.map_combo)

        self.map_widget = MapLibreDeckMapWidget(load_page=self._load_web_map)
        self.map_widget.setObjectName("simulationMapPreview")
        self.map_widget.setMinimumHeight(410)
        layout.addWidget(self.map_widget, 1)

        self.map_preview_status = QLabel("选择地图资源后加载标准路网预览")
        self.map_preview_status.setObjectName("simulationMapStatus")
        layout.addWidget(self.map_preview_status)
        return section

    def _right_configuration(self) -> QFrame:
        section = QFrame()
        section.setObjectName("simulationRightColumn")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        automation = self._automation_configuration()
        self.automation_section = automation
        if self._traffic_mode:
            automation.hide()
        layout.addWidget(automation)
        self.generated_summary = QLabel()
        self.generated_summary.setObjectName("generatedScenarioSummary")
        self.generated_summary.setWordWrap(True)
        self.generated_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.generated_summary.hide()
        layout.addWidget(self.generated_summary)
        layout.addStretch(1)
        layout.addWidget(self._simulation_parameters())
        layout.addLayout(self._footer_actions())
        return section

    def _automation_configuration(self) -> QFrame:
        section, layout = self._section("智驾数量配置", self._automation_total_badge())
        self.automation_rows_layout = QVBoxLayout()
        self.automation_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.automation_rows_layout.setSpacing(8)
        layout.addLayout(self.automation_rows_layout)

        self.add_automation_button = QPushButton("⊕  添加智驾分类")
        self.add_automation_button.setObjectName("addAutomationCategoryButton")
        self.add_automation_button.clicked.connect(self._add_automation_row)
        layout.addWidget(self.add_automation_button)
        return section

    def _automation_total_badge(self) -> QLabel:
        self.vehicle_total = QLabel("总计：0")
        self.vehicle_total.setObjectName("automationTotal")
        return self.vehicle_total

    def _simulation_parameters(self) -> QFrame:
        section, layout = self._section("仿真参数配置")
        duration_label = self._field_label("仿真时长")
        self.duration_time = QTimeEdit(QTime(1, 0, 0))
        self.duration_time.setObjectName("simulationDurationTime")
        self.duration_time.setDisplayFormat("HH:mm:ss")
        layout.addWidget(duration_label)
        layout.addWidget(self.duration_time)
        return section

    def _footer_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        self.save_configuration_button = QPushButton("保存配置")
        self.save_configuration_button.setObjectName("saveSimulationConfigurationButton")
        self.save_configuration_button.setProperty("role", "primaryAction")
        self.save_configuration_button.clicked.connect(self._request_configuration_save)
        actions.addWidget(self.save_configuration_button, 1)
        return actions

    @staticmethod
    def _section(
        title: str,
        trailing: QWidget | None = None,
    ) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("simulationConfigurationSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("simulationSectionTitle")
        heading.addWidget(label)
        heading.addStretch(1)
        if trailing is not None:
            heading.addWidget(trailing)
        layout.addLayout(heading)
        return frame, layout

    @staticmethod
    def _field_label(text: str, *, required: bool = False) -> QLabel:
        label = QLabel(f"{text} *" if required else text)
        label.setObjectName("simulationFieldLabel")
        label.setProperty("required", required)
        return label

    def set_maps(self, maps: tuple[MapSummary, ...]) -> None:
        current_id = self.map_combo.currentData()
        self._maps = {item.map_id: item for item in maps if item.kind == "sumo"}
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        for item in maps:
            if item.kind != "sumo":
                continue
            name = item.display_name or item.carla_map or item.map_id
            self.map_combo.addItem(name, item.map_id)
        self.map_combo.blockSignals(False)
        if self.map_combo.count():
            preserved_index = self.map_combo.findData(current_id)
            index = preserved_index if preserved_index >= 0 else 0
            self.map_combo.blockSignals(True)
            self.map_combo.setCurrentIndex(index)
            self.map_combo.blockSignals(False)
            self._select_map(index)
        else:
            self._show_generated_scene("")
            self.map_preview_status.setText("暂无可运行的 SUMO 地图资源")

    @Slot(object)
    def set_preview_network(self, network: object) -> None:
        self.map_widget.set_network(network)
        self.map_preview_status.setText("已加载标准路网预览")

    def set_create_enabled(self, enabled: bool) -> None:
        self._create_allowed = enabled
        self.create_button.setEnabled(enabled and not self._generation_busy)
        self.test_button.setEnabled(enabled and not self._generation_busy)

    @Slot(bool, str)
    def set_generation_state(self, busy: bool, message: str) -> None:
        """Show generation progress separately from all observed simulation results."""
        self._generation_busy = busy
        self.generate_button.setEnabled(not busy)
        self.generation_status.setText(message)
        self.save_configuration_button.setEnabled(not busy)
        self.set_create_enabled(self._create_allowed)

    @Slot(str)
    def select_generated_scene(self, map_id: str) -> None:
        """Select a published draft after the refreshed catalog has arrived."""
        index = self.map_combo.findData(map_id)
        if index >= 0:
            self.map_combo.blockSignals(True)
            self.map_combo.setCurrentIndex(index)
            self.map_combo.blockSignals(False)
            self._select_map(index)

    def current_configuration(self) -> SimulationConfigurationDraft:
        """Return the current editable values as a typed local protocol model."""
        return SimulationConfigurationDraft(
            scene_name=self.scene_name.text().strip(),
            description=self.description.toPlainText().strip(),
            map_id=str(self.map_combo.currentData() or ""),
            duration_ms=self.duration_time.time().msecsSinceStartOfDay(),
            automation_demands=()
            if self._traffic_mode or self._selected_generated_scene()
            else tuple(
                AutomationDemand(level=row.level, vehicle_count=row.vehicle_count)
                for row in self.automation_rows
            ),
        )

    @Slot()
    def _request_configuration_save(self) -> None:
        configuration = self._validated_configuration()
        if configuration is not None:
            self.configuration_save_requested.emit(configuration)

    @Slot()
    def _request_launch(self) -> None:
        configuration = self._validated_configuration()
        if configuration is not None:
            self.launch_requested.emit(configuration)

    @Slot()
    def _request_test(self) -> None:
        configuration = self._validated_configuration()
        if configuration is not None:
            self.test_requested.emit(configuration)

    def _validated_configuration(self) -> SimulationConfigurationDraft | None:
        """Keep the user's text intact and surface save-boundary errors in the page."""
        name = self.scene_name.text().strip()
        description = self.description.toPlainText().strip()
        if not name or len(name) > 200:
            self.generation_status.setText(
                "无法保存或启动：场景名称需为 1–200 个字符，请修改名称。"
            )
            return None
        if len(description) > 1000:
            self.generation_status.setText(
                "无法保存或启动：配置描述最多 1000 个字符，请编辑描述后重试。"
                "完整生成需求仍保留在已保存场景中；当前文本没有被截断。"
            )
            return None
        try:
            return self.current_configuration()
        except ValidationError:
            self.generation_status.setText(
                "无法保存或启动：配置参数格式不正确，请检查填写内容后重试。"
            )
            return None

    def apply_simulation_copy(self, simulation_name: str, parameter_summary: str) -> None:
        """Prefill editable fields from a simulation record."""
        self.scene_name.setText(f"{simulation_name} 副本")
        self.description.setPlainText(
            f"复制自“{simulation_name}”。原仿真参数摘要：{parameter_summary}"
        )

    def apply_traffic_scenario(self, preset: TrafficScenarioPreset) -> bool:
        """Apply a catalog preset and select its validated SUMO package."""
        map_index = self.map_combo.findData(preset.map_id)
        if map_index < 0:
            return False
        self.scene_name.setText(preset.name)
        self.description.setPlainText(preset.description)
        hours, remainder = divmod(preset.duration_s, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.duration_time.setTime(QTime(hours, minutes, seconds))
        for row in tuple(self.automation_rows):
            self._remove_automation_row(row)
        for level, count in preset.automation_counts:
            self._append_automation_row(level, count)
        self.map_combo.setCurrentIndex(map_index)
        if self.map_combo.currentData() == preset.map_id:
            self._select_map(map_index)
        return True

    @Slot(int)
    def _select_map(self, index: int) -> None:
        map_id = self.map_combo.itemData(index)
        if isinstance(map_id, str):
            self._show_generated_scene(map_id)
            self.map_preview_status.setText("正在加载地图路网预览……")
            self.map_selected.emit(map_id)

    def _selected_generated_scene(self) -> bool:
        selected = self._maps.get(str(self.map_combo.currentData()))
        return selected is not None and selected.generated_scenario is not None

    def _show_generated_scene(self, map_id: str) -> None:
        selected = self._maps.get(map_id)
        scene = selected.generated_scenario if selected is not None else None
        self.duration_time.setEnabled(scene is None)
        self.duration_time.setToolTip(
            "生成场景的时长与发车计划一起保存；修改时长请在描述中指定后重新生成。"
            if scene is not None
            else ""
        )
        self.automation_section.setVisible(not self._traffic_mode and scene is None)
        self.generated_summary.setVisible(scene is not None)
        if scene is None or selected is None:
            self._applied_generated_id = None
            return
        states = {"free_flow": "低流量", "moderate": "中等流量", "congested": "拥堵"}
        flow = (
            f"{scene.inflow_veh_per_hour_per_lane:g} 辆/小时/入口车道"
            if scene.inflow_veh_per_hour_per_lane is not None
            else "按计划总车辆数调度"
        )
        details = [
            "场景设定（非实测）· 生成时未启动仿真",
            f"目标交通状态：{states[scene.traffic_state]}",
            f"目标密度参考值：{scene.desired_density_veh_per_km_lane:g} 辆/公里/车道",
            f"计划发车：{scene.planned_vehicles} 辆；发车强度：{flow}",
            f"预热：{scene.warmup_s} 秒；随机种子：{scene.seed}",
            "时长与车流计划保持一致；修改时长请在描述中指定后重新生成。",
            "保留原场景的车辆路线与发车计划。实际车速、密度和拥堵程度在运行后观测。",
        ]
        if not self._traffic_mode and scene.automation_mix:
            details.append(
                "自动驾驶等级发车计划："
                + "；".join(
                    f"{item.level} {item.share_percent:g}%（{item.vehicle_count} 辆）"
                    for item in scene.automation_mix
                )
            )
        if scene.unverified_requirements:
            details.append("待核对要求：" + "；".join(scene.unverified_requirements))
        self.generated_summary.setText("\n".join(details))
        if self._applied_generated_id != map_id:
            self.scene_name.setText(selected.display_name or map_id)
            self.description.setPlainText(scene.description)
            self.duration_time.setTime(QTime(0, 0).addSecs(scene.duration_s))
            self._applied_generated_id = map_id

    def _add_automation_row(self) -> None:
        used_levels = {row.level for row in self.automation_rows}
        next_level = next((level for level in _AUTOMATION_LEVELS if level not in used_levels), None)
        if next_level is None:
            self.add_automation_button.setEnabled(False)
            return
        self._append_automation_row(next_level, 0)

    def _append_automation_row(self, level: str, count: int) -> None:
        row = _AutomationConfigurationRow(level, count)
        row.changed.connect(self._update_vehicle_total)
        row.add_requested.connect(self._add_automation_row)
        row.remove_requested.connect(self._remove_automation_row)
        self.automation_rows.append(row)
        self.automation_rows_layout.addWidget(row)
        self.add_automation_button.setEnabled(len(self.automation_rows) < len(_AUTOMATION_LEVELS))
        self._update_vehicle_total()

    def _remove_automation_row(self, item: object) -> None:
        if not isinstance(item, _AutomationConfigurationRow) or item not in self.automation_rows:
            return
        self.automation_rows.remove(item)
        self.automation_rows_layout.removeWidget(item)
        item.deleteLater()
        self.add_automation_button.setEnabled(True)
        self._update_vehicle_total()

    def _update_vehicle_total(self) -> None:
        total = sum(row.vehicle_count for row in self.automation_rows)
        self.vehicle_total.setText(f"总计：{total}")
