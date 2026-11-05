"""Edit authored scene parameters without treating them as observed traffic metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.scene_language import Language, normalize_language, text

DemandMode = Literal["count", "inflow", "density", "total_flow"]


@dataclass(frozen=True)
class ScenarioSettingsValues:
    vehicle_count: int
    desired_density_veh_per_km_lane: float
    duration_s: int
    warmup_s: int
    seed: int
    inflow_veh_per_hour_per_lane: float
    total_flow_veh_per_hour: float
    reference_speed_kmh: float
    demand_mode: DemandMode
    automation_mix: tuple[tuple[str, float], ...] = ()


class _DecimalInput(QDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        return (
            self.locale()
            .toString(value, "f", self.decimals())
            .rstrip("0")
            .rstrip(self.locale().decimalPoint())
        )


class ScenarioSettingsEditor(QWidget):
    """Keep local uncommitted inputs and emit change events for the containing page."""

    changed = Signal()

    def __init__(self, *, traffic_mode: bool, language: Language = "en") -> None:
        super().__init__()
        self._language = normalize_language(language)
        self._field_labels: list[tuple[QLabel, QAbstractSpinBox, str, str]] = []
        self.setObjectName("studioSettingsEditor")
        self._loading = False
        self._baseline: dict[str, object] = {}
        self._source: ScenarioSettingsValues | None = None
        self._traffic_mode = traffic_mode
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self.demand_mode_combo = QComboBox()
        self.demand_mode_combo.setAccessibleName("车流编排依据")
        for caption, mode in (
            ("按车辆总数", "count"),
            ("按每入口车道流量", "inflow"),
            ("按目标密度推算", "density"),
            ("按全网总发车率", "total_flow"),
        ):
            self.demand_mode_combo.addItem(caption, mode)
        layout.addWidget(self.demand_mode_combo)
        self.mode_hint = self._label("", wrap=True)
        layout.addWidget(self.mode_hint)
        self.vehicle_count_input = self._integer(1, 20000, " 辆")
        self.density_input = self._decimal(0.000001, 120)
        self.duration_input = self._integer(60, 3600, " 秒")
        self.warmup_input = self._integer(0, 3500, " 秒")
        basic = QGridLayout()
        basic.setHorizontalSpacing(12)
        basic.setVerticalSpacing(9)
        for index, (caption, english, control) in enumerate(
            (
                ("计划发车总数", "Planned vehicles", self.vehicle_count_input),
                ("目标密度 · 辆/公里/车道", "Target density · veh/km/lane", self.density_input),
                ("仿真时长", "Duration", self.duration_input),
                ("预热时间", "Warm-up", self.warmup_input),
            )
        ):
            self._add_field(basic, index, caption, english, control)
        layout.addLayout(basic)

        self.advanced_button = QPushButton("更多参数：流量、种子与参考速度")
        self.advanced_button.setProperty("studioRole", "example")
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button)
        self.advanced = QWidget()
        self.advanced.setObjectName("studioAdvancedSettings")
        advanced = QGridLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setHorizontalSpacing(12)
        advanced.setVerticalSpacing(9)
        self.inflow_input = self._decimal(0.000001, 10000)
        self.total_flow_input = self._decimal(0.000001, 1200000)
        self.seed_input = self._integer(0, 2147483647)
        self.reference_speed_input = self._decimal(0.000001, 100)
        for index, (caption, english, control) in enumerate(
            (
                ("每入口车道 · 辆/小时", "Inflow · veh/h/inlet lane", self.inflow_input),
                ("全网总发车率 · 辆/小时", "Total flow · veh/h", self.total_flow_input),
                ("随机种子", "Random seed", self.seed_input),
                ("密度推算参考速度 · km/h", "Reference speed · km/h", self.reference_speed_input),
            )
        ):
            self._add_field(advanced, index, caption, english, control)
        self.advanced.hide()
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)

        self.automation_inputs: dict[str, QDoubleSpinBox] = {}
        self.automation_editor = QWidget()
        self.automation_editor.setObjectName("studioAutomationEditor")
        shares = QGridLayout(self.automation_editor)
        shares.setContentsMargins(0, 0, 0, 0)
        shares.setHorizontalSpacing(12)
        shares.setVerticalSpacing(7)
        for index in range(6):
            level = f"L{index}"
            control = self._decimal(0, 100, " %")
            self.automation_inputs[level] = control
            self._add_field(
                shares, index, f"{level} 请求比例", f"{level} share", control, columns=3
            )
        self.automation_editor.setVisible(not traffic_mode)
        layout.addWidget(self.automation_editor)

        self._controls: dict[str, QSpinBox | QDoubleSpinBox] = {
            "vehicle_count": self.vehicle_count_input,
            "desired_density_veh_per_km_lane": self.density_input,
            "duration_s": self.duration_input,
            "warmup_s": self.warmup_input,
            "seed": self.seed_input,
            "inflow_veh_per_hour_per_lane": self.inflow_input,
            "total_flow_veh_per_hour": self.total_flow_input,
            "reference_speed_kmh": self.reference_speed_input,
        }
        modes = {
            "vehicle_count": "count",
            "desired_density_veh_per_km_lane": "density",
            "inflow_veh_per_hour_per_lane": "inflow",
            "total_flow_veh_per_hour": "total_flow",
            "reference_speed_kmh": "density",
        }
        for key, control in self._controls.items():
            control.valueChanged.connect(lambda _, name=key: self._value_changed(modes.get(name)))
            control.lineEdit().textEdited.connect(self._changed)
            control.editingFinished.connect(self._changed)
        for control in self.automation_inputs.values():
            control.valueChanged.connect(self._changed)
            control.lineEdit().textEdited.connect(self._changed)
            control.editingFinished.connect(self._changed)
        self.demand_mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.set_language(language)

    @property
    def language(self) -> Language:
        """Return the presentation language without affecting authored values."""
        return self._language

    def set_language(self, language: str) -> None:
        """Translate labels and units while retaining even unfinished numeric edits."""
        self._language = normalize_language(language)
        self.demand_mode_combo.setAccessibleName(self._text("车流编排依据", "Demand calculation"))
        with QSignalBlocker(self.demand_mode_combo):
            for index, (zh, en) in enumerate(
                (
                    ("按车辆总数", "By vehicle count"),
                    ("按每入口车道流量", "By inflow per inlet lane"),
                    ("按目标密度推算", "By target density"),
                    ("按全网总发车率", "By total departure rate"),
                )
            ):
                self.demand_mode_combo.setItemText(index, self._text(zh, en))
        for label, control, zh, en in self._field_labels:
            caption = self._text(zh, en)
            label.setText(caption)
            control.setAccessibleName(caption)
        self.advanced_button.setText(
            self._text("更多参数：流量、种子与参考速度", "More: flow, seed and reference speed")
        )
        self._set_suffix(self.vehicle_count_input, self._text(" 辆", " veh"))
        for control in (self.duration_input, self.warmup_input):
            self._set_suffix(control, self._text(" 秒", " s"))
        self._update_hint()

    def _text(self, zh: str, en: str) -> str:
        return text(self._language, zh, en)

    @staticmethod
    def _set_suffix(control: QSpinBox, suffix: str) -> None:
        if control.suffix() == suffix:
            return
        editor = control.lineEdit()
        raw, old = editor.text(), control.suffix()
        cursor, start, length = (
            editor.cursorPosition(),
            editor.selectionStart(),
            len(editor.selectedText()),
        )
        modified = editor.isModified()
        # Qt normally rewrites the line edit from value() when its suffix changes.
        # Keep raw input instead so an empty/invalid draft cannot become a valid old value.
        with QSignalBlocker(control), QSignalBlocker(editor):
            control.setSuffix(suffix)
            if old and raw.endswith(old):
                raw = raw[: -len(old)] + suffix
            editor.setText(raw)
            editor.setModified(modified)
            if start >= 0:
                editor.setSelection(start, length)
            else:
                editor.setCursorPosition(min(cursor, len(raw)))

    @staticmethod
    def _label(text: str, *, wrap: bool = False) -> QLabel:
        label = QLabel(text)
        label.setProperty("studioRole", "caption")
        label.setWordWrap(wrap)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _add_field(
        self,
        grid: QGridLayout,
        index: int,
        caption: str,
        english: str,
        control: QAbstractSpinBox,
        *,
        columns: int = 2,
    ) -> None:
        label = self._label(caption)
        self._field_labels.append((label, control, caption, english))
        label.setBuddy(control)
        control.setAccessibleName(caption)
        grid.addWidget(label, 2 * (index // columns), index % columns)
        grid.addWidget(control, 2 * (index // columns) + 1, index % columns)

    @staticmethod
    def _integer(minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setSuffix(suffix)
        control.setGroupSeparatorShown(True)
        control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        control.setMinimumHeight(36)
        return control

    @staticmethod
    def _decimal(minimum: float, maximum: float, suffix: str = "") -> QDoubleSpinBox:
        control = _DecimalInput()
        control.setDecimals(6)
        control.setRange(minimum, maximum)
        control.setSuffix(suffix)
        control.setGroupSeparatorShown(True)
        control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        control.setMinimumHeight(36)
        return control

    def _value_changed(self, mode: str | None) -> None:
        if self._loading:
            return
        if mode is not None:
            self.demand_mode_combo.setCurrentIndex(self.demand_mode_combo.findData(mode))
        self._changed()

    def _mode_changed(self) -> None:
        if not self._loading and self._baseline:
            active_field = {
                "count": "vehicle_count",
                "inflow": "inflow_veh_per_hour_per_lane",
                "total_flow": "total_flow_veh_per_hour",
            }.get(str(self.demand_mode_combo.currentData()))
            # Alternate quantity inputs describe the last preview until reapplied.
            # Discard an earlier quantity edit when the user selects a different driver.
            self._loading = True
            try:
                for key in (
                    "vehicle_count",
                    "inflow_veh_per_hour_per_lane",
                    "total_flow_veh_per_hour",
                ):
                    value = self._baseline[key]
                    if key != active_field and isinstance(value, (float, int)):
                        control = self._controls[key]
                        if isinstance(control, QSpinBox):
                            control.setValue(int(value))
                        else:
                            control.setValue(value)
            finally:
                self._loading = False
        if not self._loading and self.demand_mode_combo.currentData() in {"inflow", "total_flow"}:
            self.advanced_button.setChecked(True)
        self._update_hint()
        self._changed()

    def _update_hint(self) -> None:
        hints = {
            "count": (
                "以车辆总数为准，流量随时长重新计算；密度仅作参考目标。",
                "Vehicle count stays fixed; flow follows duration. Density is a reference target.",
            ),
            "inflow": (
                "以每入口车道流量为准，按可用入口车道与时长计算车辆数。",
                "Inflow, available inlet lanes and duration determine vehicle count.",
            ),
            "density": (
                "以目标密度 × 参考速度推算流量，再生成车辆计划。",
                "Target density × reference speed determines flow and the departure plan.",
            ),
            "total_flow": (
                "以全网总发车率 × 时长计算车辆数，结果按整数辆取整。",
                "Total departure rate × duration determines vehicle count, rounded down.",
            ),
        }
        self.mode_hint.setText(
            self._text(*hints[str(self.demand_mode_combo.currentData())])
            + self._text(
                " 修改数量、密度、参考速度或流量会切换对应编排依据。",
                " Editing count, density, reference speed or flow switches the calculation method.",
            )
        )

    def _changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _values(self) -> dict[str, object]:
        values: dict[str, object] = {
            key: control.value() for key, control in self._controls.items()
        }
        values["demand_mode"] = self.demand_mode_combo.currentData()
        if not self._traffic_mode:
            values["automation_mix"] = [
                {"level": level, "share_percent": control.value()}
                for level, control in self.automation_inputs.items()
                if control.value() > 0
            ]
        return values

    def load(self, values: ScenarioSettingsValues) -> None:
        """Replace all local inputs only when a new authoritative draft arrives."""
        self._source = values
        self._loading = True
        try:
            for key, control in self._controls.items():
                control.setValue(getattr(values, key))
                control.interpretText()
            mix = dict(values.automation_mix)
            for level, share in self.automation_inputs.items():
                share.setValue(mix.get(level, 0))
                share.interpretText()
            self.demand_mode_combo.setCurrentIndex(
                self.demand_mode_combo.findData(values.demand_mode)
            )
            self._baseline = self._values()
        finally:
            self._loading = False
        self._update_hint()
        self.changed.emit()

    def cancel(self) -> None:
        if self._source is not None:
            self.load(self._source)

    def validation_error(self) -> str | None:
        controls = list(self._controls.values())
        if not self._traffic_mode:
            controls.extend(self.automation_inputs.values())
        if any(not control.hasAcceptableInput() for control in controls):
            return self._text(
                "请填写有效数字，空值或超出范围的输入不能应用。",
                "Enter valid numbers. Empty or out-of-range values cannot be applied.",
            )
        if self.warmup_input.value() >= self.duration_input.value():
            return self._text(
                "预热时间必须小于仿真时长。", "Warm-up must be shorter than duration."
            )
        if (
            not self._traffic_mode
            and abs(sum(control.value() for control in self.automation_inputs.values()) - 100)
            > 0.000001
        ):
            return self._text(
                "L0–L5 请求比例合计必须为 100%。",
                "Requested L0–L5 shares must add up to 100%.",
            )
        return None

    def is_dirty(self) -> bool:
        return bool(self._baseline) and (
            self.validation_error() is not None or self._values() != self._baseline
        )

    def revision(self) -> dict[str, object]:
        """Return changed settings and exactly one quantity that controls demand."""
        values = self._values()
        patch = {key: value for key, value in values.items() if value != self._baseline.get(key)}
        mode = str(values["demand_mode"])
        drivers = {
            "count": "vehicle_count",
            "inflow": "inflow_veh_per_hour_per_lane",
            "density": "desired_density_veh_per_km_lane",
            "total_flow": "total_flow_veh_per_hour",
        }
        for key in ("vehicle_count", "inflow_veh_per_hour_per_lane", "total_flow_veh_per_hour"):
            if key != drivers[mode]:
                patch.pop(key, None)
        if "demand_mode" in patch or any(key in patch for key in drivers.values()):
            patch["demand_mode"] = mode
            patch[drivers[mode]] = values[drivers[mode]]
        return patch
