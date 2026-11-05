"""Natural-language scene studio: describe, inspect a draft, then explicitly add it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.scene_language import Language, normalize_language, text
from ui.views.theme import ThemeMode, load_scene_studio_colors
from ui.widgets.image_preview_dialog import ImagePreviewDialog
from ui.widgets.scenario_settings_editor import (
    DemandMode,
    ScenarioSettingsEditor,
    ScenarioSettingsValues,
)

_STATE_NAMES = {"free_flow": "自由流", "moderate": "常规交通", "congested": "拥堵场景"}
_STYLE_PATH = Path(__file__).with_name("styles") / "scene-generation.qss"

_COPY = {
    "场景工坊": "Scene Studio",
    "SCENE STUDIO": "NL2MAP",
    "描述需求  /  预览场景  /  确认添加": "Describe  /  Preview  /  Add scene",
    "让每一个交通想法，成为场景": "Turn an idea into a traffic scene",
    "描述路网、交通状态或测试目标，预览满意后再加入你的场景。": (
        "Describe a road network, traffic conditions or a test. Preview it, then add your scene."
    ),
    "生成预览  →": "Generate preview  →",
    "重新生成  →": "Regenerate  →",
    "正在生成…": "Generating…",
    "重试生成  →": "Try again  →",
    "↗  城市真实路网": "↗  Real-world roads",
    "＋  信号十字路口": "＋  Signalized junction",
    "◷  晚高峰拥堵": "◷  Rush-hour traffic",
    "◇  等级混合车流": "◇  Mixed automation",
    "生成北航学院路校区附近 1 公里路网，测试拥堵场景": (
        "Generate roads within 1 km of Beihang University's Xueyuan "
        "Road campus for a congestion test"
    ),
    "生成一个四个方向都有信号灯的十字路口": (
        "Generate a four-way intersection with traffic lights on all four approaches"
    ),
    "希望测试拥堵场景下车辆速度，仿真 10 分钟": (
        "Test vehicle speeds in congested traffic over a 10-minute simulation"
    ),
    "生成一个十字路口，共 1000 辆车，L0 占 50%，L3 占 50%，仿真 10 分钟": (
        "Generate a four-way intersection with 1000 vehicles, L0 50%, L3 50%, over 10 minutes"
    ),
    "从一句话开始 · 生成过程不会启动仿真": "Start with a sentence · No simulation starts here",
    "未能生成场景": "Could not generate the scene",
    "请根据以上原因调整需求或稍后重试，输入内容已保留。": (
        "Adjust your request using the details above, or try again later. Your input has been kept."
    ),
    "路网预览": "Road network preview",
    "静态地图": "Static map",
    "⛶  放大查看": "⛶  Enlarge",
    "重试加载地图": "Reload map",
    "根据生成路网绘制 · 车辆尚未运行": "Generated network · Vehicles have not run",
    "场景设定": "Scene settings",
    "待生成": "No preview yet",
    "计划发车": "Planned departures",
    "目标密度 · 辆/公里/车道": "Target density · veh/km/lane",
    "仿真时长": "Duration",
    "预热时间": "Warm-up",
    "编辑场景设定": "Edit scene settings",
    "应用全部修改": "Apply all changes",
    "取消": "Cancel",
    "修改后点击应用，生成新的场景预览。": "Apply your changes to create an updated preview.",
    "发车计划": "Departure plan",
    "自动驾驶等级比例": "Automation mix",
    "计划发车数是整个时段的车辆总量，不是同一时刻在路网上的车辆数。": (
        "Planned departures count all vehicles over the full duration, "
        "not vehicles on the road at once."
    ),
    "以上为生成设定，实际速度与拥堵程度需仿真后评估。": (
        "These are scene settings. Actual speed and congestion must be evaluated after simulation."
    ),
    "生成说明": "Generation notes",
    "查看生成细节": "View generation details",
    "预览尚未添加到场景。确认后可在仿真配置中使用。": (
        "This preview has not been added yet. Confirm to make it available in simulation setup."
    ),
    "进入仿真配置  →": "Simulation setup  →",
    "确认，添加到场景": "Confirm and add scene",
    "设定尚未应用，请先应用全部修改或取消，再确认添加场景。": (
        "You have unapplied changes. Apply or cancel them before adding the scene."
    ),
    "正在按新设定更新场景和发车计划……": "Updating the scene and departure plan…",
    "先写一句场景需求，例如“生成一个十字路口”。": (
        "Describe a scene first, for example: Generate a four-way intersection."
    ),
    "正在理解需求、生成路网和发车计划…": "Interpreting your request and building the scene…",
    "下方为上一次预览，重新生成成功后才能添加。": (
        "The previous preview is shown below. Generate a new preview before adding it."
    ),
    "需求已修改；下方仍是上一次预览，请重新生成后再添加。": (
        "Your request has changed. Regenerate the preview before adding the scene."
    ),
    "场景预览已就绪，请核对地图与车辆设定。": "Preview ready. Review the map and traffic settings.",
    "正在添加到场景…": "Adding the scene…",
    "下方为上一次预览，新预览生成后才能确认添加。": (
        "The previous preview is shown below. Wait for the new preview before confirming."
    ),
    "生成未完成，请查看下方原因。": "Generation did not complete. See the details below.",
    "你的场景，即将就绪": "Your scene is taking shape",
    "先看看路网和交通设定，再决定是否添加到场景。": (
        "Review the roads and traffic settings, then decide whether to add the scene."
    ),
    "正在加载路网预览…": "Loading the road network preview…",
    "正在重新加载路网预览…": "Reloading the road network preview…",
    "路网图片无法显示，请重新生成或重试加载。": "Cannot display the map. Reload or regenerate it.",
    "已按手动设定更新场景，可继续修改或确认添加。": (
        "Your settings have been applied. Continue editing or confirm to add the scene."
    ),
    "自由流": "Free flow",
    "常规交通": "Moderate traffic",
    "拥堵场景": "Congested traffic",
    "未提供等级分配": "No automation mix provided",
    "比例按生成发车计划统计，不代表运行中的实时车辆比例。": (
        "Shares describe the departure plan, not the real-time mix of vehicles on the road."
    ),
    "已按需求设置场景参数。": "Scene parameters follow your request.",
    "请留意这些要求": "Requirements to review",
    "目标密度用于规划车流，不代表运行后的实测密度。请自行判断路网是否适合测试。": (
        "Target density guides traffic demand; it is not a measured "
        "result. Check that this network suits your test."
    ),
    "场景已按手动设定更新；目标密度和拥堵状态仍需在仿真后验证。": (
        "Manual settings have been applied. Target density and "
        "congestion still need simulation to verify."
    ),
    "✓  已添加到场景": "✓  Scene added",
    "已添加。进入仿真配置后，可由你决定何时启动。": (
        "Scene added. Open simulation setup and start it when you are ready."
    ),
    "随机种子": "Random seed",
    "目标密度参考值": "Target density reference",
    "发车流量由目标密度 × 参考速度推算": "Inflow estimated from target density × reference speed",
    "默认轻量车流：推算流量 × 10%": "Light default demand: 10% of the estimated inflow",
    "默认使用 L0 车辆": "L0 vehicles by default",
    "预览已生成": "Preview generated",
    "预览已生成；查看地图和设定后，确认添加到场景。": (
        "Preview generated. Review the map and settings, then confirm to add the scene."
    ),
    "场景设定已更新，发车计划已同步。": "Scene settings and the departure plan have been updated.",
}
_COPY.update(
    {
        "正在理解需求、生成路网与车辆计划……": (
            "Interpreting your request and building the map and traffic plan…"
        ),
        "预览已生成；查看地图和设定后，确认添加到场景。": (
            "Preview ready. Review the map and settings, then confirm to add the scene."
        ),
        "正在应用场景设定并更新发车计划……": ("Applying settings and updating the departure plan…"),
        "正在添加场景……": ("Adding the scene…"),
        "场景已添加，正在刷新系统场景列表……": ("Scene added. Refreshing the scene library…"),
        "场景已添加，尚未运行。可前往仿真配置查看或启动。": (
            "Scene added; no simulation has started. Open Simulation setup to review or run it."
        ),
        "请先填写场景描述。": ("Describe the scene first."),
        "无法读取场景草稿，请重新生成。": (
            "Could not read the scene draft. Please generate it again."
        ),
        "场景设定已更新，发车计划已同步。": (
            "Scene settings and the departure plan have been updated."
        ),
        "当前草稿已变化，请查看当前预览后再修改。": (
            "The draft has changed. Review the current preview before editing."
        ),
        "请先加载当前草稿的地图预览，再添加到场景。": (
            "Load the current map preview before adding this scene."
        ),
        "。可稍后刷新列表。": (". Please refresh it later."),
        "场景已保存，但刷新系统列表失败：": ("Scene saved, but the library could not refresh: "),
    }
)

_SOURCE_COPY = {value: key for key, value in _COPY.items()}


def _label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("studioRole", role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    return label


class _RoadMotif(QWidget):
    """Decorative illustration, never presented as the user's generated road network."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("studioRoadMotif")
        self.setFixedHeight(96)
        self.setAccessibleName("场景工坊装饰图案")
        self.colors = load_scene_studio_colors(ThemeMode.LIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.width() / 2
        gradient = QLinearGradient(center - 200, 0, center + 200, 96)
        gradient.setColorAt(0, QColor(self.colors["gradient"][0]))
        gradient.setColorAt(0.55, QColor(self.colors["gradient"][1]))
        gradient.setColorAt(1, QColor(self.colors["gradient"][2]))
        for offset, opacity in ((0, 0.10), (18, 0.06), (-18, 0.06)):
            painter.setOpacity(opacity)
            painter.setPen(QPen(gradient, 22))
            path = QPainterPath(QPointF(center - 210, 78 + offset))
            path.cubicTo(center - 90, 78 + offset, center - 100, 18, center, 18)
            path.cubicTo(center + 95, 18, center + 80, 78 + offset, center + 210, 78 + offset)
            painter.drawPath(path)
        painter.setOpacity(1)
        painter.setPen(QPen(gradient, 2.0))
        path = QPainterPath(QPointF(center - 170, 70))
        path.cubicTo(center - 68, 70, center - 80, 22, center, 22)
        path.cubicTo(center + 85, 22, center + 65, 70, center + 170, 70)
        painter.drawPath(path)
        for (x, y), color in zip(
            ((-165, 70), (0, 22), (165, 70)), self.colors["nodes"], strict=True
        ):
            painter.setBrush(QColor(self.colors["marker_fill"]))
            painter.setPen(QPen(QColor(color), 3))
            painter.drawEllipse(QPointF(center + x, y), 7, 7)


class _MapImage(QLabel):
    activated = Signal()

    def __init__(self, language: Language) -> None:
        self._language = language
        self._message = "正在加载路网预览…"
        super().__init__(_COPY[self._message] if language == "en" else self._message)
        self.setObjectName("studioMapImage")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(300, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._source = QPixmap()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("路网预览，点击或按回车放大查看")

    @property
    def source_pixmap(self) -> QPixmap:
        return self._source.copy()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._source.isNull():
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def clear_image(self, message: str) -> None:
        self._source = QPixmap()
        self.clear()
        self._message = message
        self.set_language(self._language)

    def set_language(self, language: Language) -> None:
        self._language = language
        self.setAccessibleName(
            text(
                language,
                "路网预览，点击或按回车放大查看",
                "Road network preview; click or press Enter to enlarge",
            )
        )
        if self._source.isNull():
            source = _SOURCE_COPY.get(self._message, self._message)
            rendered = text(language, source, _COPY.get(source, source))
            if language == "en" and any("\u4e00" <= c <= "\u9fff" for c in rendered):
                rendered = (
                    "Map preview unavailable. Service details (original language):\n" + rendered
                )
            self.setText(rendered)

    def load(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.clear_image("路网图片无法显示，请重新生成或重试加载。")
            return False
        self._source = pixmap
        self._scale_image()
        return True

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_image()

    def _scale_image(self) -> None:
        if not self._source.isNull():
            self.setPixmap(
                self._source.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


class _CompositionChart(QWidget):
    def __init__(self, language: Language) -> None:
        super().__init__()
        self.language = language
        self.setObjectName("studioCompositionChart")
        self.setFixedSize(116, 116)
        self.shares: list[float] = []
        self.colors = load_scene_studio_colors(ThemeMode.LIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(12, 12, 92, 92)
        painter.setPen(QPen(QColor(self.colors["chart_track"]), 13))
        painter.drawEllipse(rect)
        start = 90 * 16
        for index, share in enumerate(self.shares):
            span = round(share * 360 * 16)
            series = self.colors["series"]
            painter.setPen(QPen(QColor(series[index % len(series)]), 13))
            painter.drawArc(rect, start, -span)
            start -= span
        painter.setPen(QColor(self.colors["chart_text"]))
        font = QFont(self.font())
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 35, 116, 30),
            Qt.AlignmentFlag.AlignCenter,
            "100%" if sum(self.shares) > 0 else "—",
        )
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 63, 116, 20),
            Qt.AlignmentFlag.AlignCenter,
            text(self.language, "发车计划", "Departures"),
        )


class SceneGenerationPage(QWidget):
    """Present authored settings and the actual map before a publication action."""

    generation_requested = Signal(str)
    publish_requested = Signal(str)
    configure_requested = Signal(str)
    preview_retry_requested = Signal(str)
    revision_requested = Signal(str, object)
    language_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        traffic_mode: bool = False,
        language: Language = "en",
    ) -> None:
        super().__init__(parent)
        self._language = normalize_language(language)
        self._text_sources: dict[QLabel | QPushButton, str] = {}
        self.setObjectName("sceneGenerationPage")
        self._traffic_mode = traffic_mode
        self._draft: dict[str, Any] | None = None
        self._draft_current = False
        self._busy = False
        self._publishing = False
        self._revising = False
        self._image_dialog: ImagePreviewDialog | None = None
        self._image_ready = False
        self._published_map_id: str | None = None
        self._submitted_description = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(_label("场景工坊", "pageTitle"))
        top.addWidget(_label("SCENE STUDIO", "eyebrow"))
        top.addStretch()
        top.addWidget(_label("描述需求  /  预览场景  /  确认添加", "caption"))
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("studioLanguage")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh")
        self.language_combo.currentIndexChanged.connect(
            lambda: self.set_language(str(self.language_combo.currentData()))
        )
        top.addWidget(self.language_combo)
        layout.addLayout(top)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setObjectName("studioScroll")
        content = QWidget()
        content.setObjectName("studioContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(22)
        self._content_layout.addStretch(2)
        self.hero = QWidget()
        self.hero.setObjectName("studioHero")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(24, 0, 24, 0)
        hero_layout.setSpacing(12)
        self.motif = _RoadMotif()
        hero_layout.addWidget(self.motif)
        self.title = _label("让每一个交通想法，成为场景", "heroTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.title)
        self.subtitle = _label(
            "描述路网、交通状态或测试目标，预览满意后再加入你的场景。", "subtitle"
        )
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.subtitle)
        hero_layout.addSpacing(12)

        search_row = QHBoxLayout()
        search_row.addStretch()
        self.search_card = QFrame()
        self.search_card.setObjectName("studioSearchCard")
        self.search_card.setMaximumWidth(920)
        self.search_card.setMinimumHeight(72)
        search_layout = QHBoxLayout(self.search_card)
        search_layout.setContentsMargins(22, 10, 12, 10)
        search_layout.setSpacing(12)
        search_layout.addWidget(_label("✦", "searchMark"))
        self.description = QLineEdit()
        self.description.setObjectName("studioDescription")
        self.description.setMaxLength(8000)
        self.description.setPlaceholderText("例如：生成北航附近 1 公里路网，测试晚高峰拥堵…")
        if not traffic_mode:
            self.description.setPlaceholderText(
                "例如：生成信号十字路口，1000 辆车，L0 与 L3 各占 50%…"
            )
        self.description.setAccessibleName("场景需求")
        self.description.returnPressed.connect(self._request_generation)
        self.description.textChanged.connect(self._description_changed)
        search_layout.addWidget(self.description, 1)
        self.generate_button = QPushButton("生成预览  →")
        self.generate_button.setObjectName("studioGenerateButton")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_button.setMinimumHeight(46)
        self.generate_button.clicked.connect(self._request_generation)
        search_layout.addWidget(self.generate_button)
        search_row.addWidget(self.search_card, 12)
        search_row.addStretch()
        hero_layout.addLayout(search_row)

        self.examples = QWidget()
        self.examples.setObjectName("studioExamples")
        examples_layout = QHBoxLayout(self.examples)
        examples_layout.setContentsMargins(0, 0, 0, 0)
        examples_layout.setSpacing(9)
        examples_layout.addStretch()
        for label, description in (
            ("↗  城市真实路网", "生成北航学院路校区附近 1 公里路网，测试拥堵场景"),
            ("＋  信号十字路口", "生成一个四个方向都有信号灯的十字路口"),
            (
                ("◷  晚高峰拥堵" if traffic_mode else "◇  等级混合车流"),
                (
                    "希望测试拥堵场景下车辆速度，仿真 10 分钟"
                    if traffic_mode
                    else "生成一个十字路口，共 1000 辆车，L0 占 50%，L3 占 50%，仿真 10 分钟"
                ),
            ),
        ):
            button = QPushButton(label)
            button.setProperty("studioRole", "example")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, text=description: self._fill_example(self._t(text))
            )
            examples_layout.addWidget(button)
        examples_layout.addStretch()
        hero_layout.addWidget(self.examples)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        hero_layout.addWidget(self.progress)
        self.status_label = _label("从一句话开始 · 生成过程不会启动仿真", "caption", wrap=True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.status_label)
        self.error_card = QFrame()
        self.error_card.setObjectName("studioGenerationError")
        self.error_card.setMaximumWidth(920)
        self.error_card.setAccessibleName("场景生成失败原因")
        error_layout = QVBoxLayout(self.error_card)
        error_layout.setContentsMargins(18, 14, 18, 14)
        error_layout.setSpacing(8)
        error_layout.addWidget(_label("未能生成场景", "errorTitle"))
        self.error_message = _label("", "errorBody", wrap=True)
        self.error_message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        error_layout.addWidget(self.error_message)
        error_layout.addWidget(
            _label("请根据以上原因调整需求或稍后重试，输入内容已保留。", "errorHint", wrap=True)
        )
        self.error_card.hide()
        error_row = QHBoxLayout()
        error_row.addStretch()
        error_row.addWidget(self.error_card, 10)
        error_row.addStretch()
        hero_layout.addLayout(error_row)
        self._content_layout.addWidget(self.hero)
        self.results = self._build_results()
        self.results.hide()
        self._content_layout.addWidget(self.results)
        self._content_layout.addStretch(3)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area, 1)
        self.set_theme("light")
        for widget in [*self.findChildren(QLabel), *self.findChildren(QPushButton)]:
            if (
                isinstance(widget, (QLabel, QPushButton))
                and not self.settings_editor.isAncestorOf(widget)
                and widget is not self.map_image
            ):
                self._text_sources[widget] = widget.text()
        self._retranslate()
        self._update_actions()

    @property
    def language(self) -> Language:
        """Return the current interface language without changing the request language."""
        return self._language

    def _t(self, source: str, english: str | None = None) -> str:
        return text(
            self._language, source, english if english is not None else _COPY.get(source, source)
        )

    def _set_text(self, widget: QLabel | QPushButton, source: str) -> None:
        source = _SOURCE_COPY.get(source, source)
        self._text_sources[widget] = source
        rendered = self._t(source)
        # Provider errors and model-authored notes remain verbatim, with an English context label.
        if (
            self._language == "en"
            and source not in _COPY
            and widget
            in (
                self.error_message,
                self.status_label,
                self.edit_status,
                self.confirmation_label,
                self.automation_note,
            )
            and any("\u4e00" <= char <= "\u9fff" for char in source)
        ):
            rendered = "Details (original language):\n" + source
        widget.setText(rendered)

    def set_language(self, language: str) -> None:
        """Retranslate in place; drafts, outstanding requests and unsaved edits are unchanged."""
        selected = normalize_language(language)
        if selected == self._language:
            return
        self._language = selected
        self._retranslate()
        self.language_changed.emit(selected)

    def _retranslate(self) -> None:
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(self._language))
        self.language_combo.blockSignals(False)
        self.language_combo.setAccessibleName(self._t("界面语言", "Interface language"))
        for widget, source in list(self._text_sources.items()):
            self._set_text(widget, source)
        self.description.setPlaceholderText(
            self._t(
                "例如：生成北航附近 1 公里路网，测试晚高峰拥堵…",
                "e.g. Roads within 1 km of Beihang University for a rush-hour congestion test…",
            )
            if self._traffic_mode
            else self._t(
                "例如：生成信号十字路口，1000 辆车，L0 与 L3 各占 50%…",
                "e.g. A signalized intersection, 1000 vehicles, L0 50%, L3 50%…",
            )
        )
        self.description.setAccessibleName(self._t("场景需求", "Scene requirements"))
        self.error_card.setAccessibleName(
            self._t("场景生成失败原因", "Scene generation error details")
        )
        self.motif.setAccessibleName(self._t("场景工坊装饰图案", "Decorative road illustration"))
        self.settings_editor.set_language(self._language)
        self.map_image.set_language(self._language)
        self.composition_chart.language = self._language
        self.composition_chart.update()
        if self._image_dialog is not None:
            self._image_dialog.set_language(self._language)
        self._render_draft_copy()
        if self._draft is not None and not self._revising:
            error = self.settings_editor.validation_error()
            if error:
                self._set_text(self.edit_status, error)

    def _build_results(self) -> QWidget:
        results = QWidget()
        results.setObjectName("studioResults")
        layout = QVBoxLayout(results)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        row = QHBoxLayout()
        row.setSpacing(18)
        map_card = QFrame()
        map_card.setProperty("studioRole", "card")
        map_layout = QVBoxLayout(map_card)
        map_layout.setContentsMargins(18, 16, 18, 14)
        heading = QHBoxLayout()
        heading.addWidget(_label("路网预览", "cardTitle"))
        heading.addStretch()
        heading.addWidget(_label("静态地图", "tag"))
        self.zoom_button = QPushButton("⛶  放大查看")
        self.zoom_button.setObjectName("studioZoomButton")
        self.zoom_button.setProperty("studioRole", "example")
        self.zoom_button.clicked.connect(self._open_image)
        heading.addWidget(self.zoom_button)
        map_layout.addLayout(heading)
        self.map_image = _MapImage(self._language)
        self.map_image.activated.connect(self._open_image)
        map_layout.addWidget(self.map_image, 1)
        self.preview_retry_button = QPushButton("重试加载地图")
        self.preview_retry_button.setProperty("studioRole", "example")
        self.preview_retry_button.clicked.connect(self._retry_preview)
        self.preview_retry_button.hide()
        map_layout.addWidget(self.preview_retry_button, alignment=Qt.AlignmentFlag.AlignCenter)
        map_layout.addWidget(_label("根据生成路网绘制 · 车辆尚未运行", "caption"))
        row.addWidget(map_card, 6)

        settings_card = QFrame()
        settings_card.setProperty("studioRole", "card")
        settings_card.setMinimumWidth(300)
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(20, 16, 20, 16)
        settings_layout.setSpacing(16)
        heading = QHBoxLayout()
        heading.addWidget(_label("场景设定", "cardTitle"))
        heading.addStretch()
        self.state_badge = _label("待生成", "state")
        heading.addWidget(self.state_badge)
        settings_layout.addLayout(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        self.stat_values: dict[str, QLabel] = {}
        for index, (key, caption) in enumerate(
            (
                ("vehicles", "计划发车"),
                ("density", "目标密度 · 辆/公里/车道"),
                ("duration", "仿真时长"),
                ("warmup", "预热时间"),
            )
        ):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            value = _label("—", "statValue")
            self.stat_values[key] = value
            cell.addWidget(value)
            cell.addWidget(_label(caption, "caption"))
            grid.addLayout(cell, index // 2, index % 2)
        settings_layout.addLayout(grid)
        editor = QVBoxLayout()
        editor.setSpacing(6)
        editor.addWidget(_label("编辑场景设定", "body"))
        self.settings_editor = ScenarioSettingsEditor(
            traffic_mode=self._traffic_mode, language=self._language
        )
        self.vehicle_count_input = self.settings_editor.vehicle_count_input
        self.density_input = self.settings_editor.density_input
        self.duration_input = self.settings_editor.duration_input
        self.warmup_input = self.settings_editor.warmup_input
        self.seed_input = self.settings_editor.seed_input
        self.inflow_input = self.settings_editor.inflow_input
        self.total_flow_input = self.settings_editor.total_flow_input
        self.reference_speed_input = self.settings_editor.reference_speed_input
        self.demand_mode_combo = self.settings_editor.demand_mode_combo
        self.automation_inputs = self.settings_editor.automation_inputs
        editor.addWidget(self.settings_editor)
        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        self.apply_count_button = QPushButton("应用全部修改")
        self.apply_count_button.setObjectName("studioApplyCountButton")
        self.apply_count_button.clicked.connect(self._request_revision)
        count_row.addWidget(self.apply_count_button, 1)
        self.cancel_count_button = QPushButton("取消")
        self.cancel_count_button.setProperty("studioRole", "example")
        self.cancel_count_button.clicked.connect(self._cancel_count_edit)
        count_row.addWidget(self.cancel_count_button)
        editor.addLayout(count_row)
        self.edit_status = _label("修改后点击应用，生成新的场景预览。", "caption", wrap=True)
        editor.addWidget(self.edit_status)
        self.settings_editor.changed.connect(self._count_changed)
        settings_layout.addLayout(editor)
        divider = QFrame()
        divider.setObjectName("studioDivider")
        divider.setFixedHeight(1)
        settings_layout.addWidget(divider)
        self.composition_title = _label(
            "发车计划" if self._traffic_mode else "自动驾驶等级比例", "cardTitle"
        )
        settings_layout.addWidget(self.composition_title)
        self.composition_row = QWidget()
        self.composition_row.setObjectName("studioComposition")
        composition = QHBoxLayout(self.composition_row)
        composition.setContentsMargins(0, 0, 0, 0)
        self.composition_chart = _CompositionChart(self._language)
        composition.addWidget(self.composition_chart)
        self.composition_legend = _label("", "body", wrap=True)
        composition.addWidget(self.composition_legend, 1)
        settings_layout.addWidget(self.composition_row)
        self.composition_row.setVisible(not self._traffic_mode)
        self.demand_note = _label(
            "计划发车数是整个时段的车辆总量，不是同一时刻在路网上的车辆数。",
            "body",
            wrap=True,
        )
        settings_layout.addWidget(self.demand_note)
        self.flow_label = _label("", "caption", wrap=True)
        settings_layout.addWidget(self.flow_label)
        self.automation_note = _label("", "caption", wrap=True)
        settings_layout.addWidget(self.automation_note)
        settings_layout.addStretch()
        settings_layout.addWidget(
            _label("以上为生成设定，实际速度与拥堵程度需仿真后评估。", "caption", wrap=True)
        )
        row.addWidget(settings_card, 4)
        layout.addLayout(row)
        self.review_card = QFrame()
        self.review_card.setProperty("studioRole", "review")
        review_layout = QVBoxLayout(self.review_card)
        review_layout.setContentsMargins(18, 12, 18, 12)
        review_layout.setSpacing(5)
        self.review_title = _label("生成说明", "cardTitle")
        review_layout.addWidget(self.review_title)
        self.defaults_label = _label("", "caption", wrap=True)
        review_layout.addWidget(self.defaults_label)
        self.requirements_label = _label("", "body", wrap=True)
        review_layout.addWidget(self.requirements_label)
        self.notes_button = QPushButton("查看生成细节")
        self.notes_button.setProperty("studioRole", "example")
        self.notes_button.setCheckable(True)
        review_layout.addWidget(self.notes_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.notes_label = _label("", "caption", wrap=True)
        self.notes_label.hide()
        self.notes_button.toggled.connect(self.notes_label.setVisible)
        review_layout.addWidget(self.notes_label)
        layout.addWidget(self.review_card)
        footer = QHBoxLayout()
        self.confirmation_label = _label(
            "预览尚未添加到场景。确认后可在仿真配置中使用。", "caption", wrap=True
        )
        footer.addWidget(self.confirmation_label, 1)
        self.configure_button = QPushButton("进入仿真配置  →")
        self.configure_button.setObjectName("studioConfigureButton")
        self.configure_button.setMinimumHeight(44)
        self.configure_button.clicked.connect(self._request_configuration)
        self.configure_button.hide()
        footer.addWidget(self.configure_button)
        self.publish_button = QPushButton("确认，添加到场景")
        self.publish_button.setObjectName("studioPublishButton")
        self.publish_button.setMinimumHeight(44)
        self.publish_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.publish_button.clicked.connect(self._request_publication)
        footer.addWidget(self.publish_button)
        layout.addLayout(footer)
        return results

    def _fill_example(self, text: str) -> None:
        self.description.setText(text)
        self.description.setFocus()

    def _open_image(self) -> None:
        if not self._image_ready:
            return
        if self._image_dialog is None:
            self._image_dialog = ImagePreviewDialog(
                self.map_image.source_pixmap, self, language=self._language
            )
        self._image_dialog.show()
        self._image_dialog.raise_()
        self._image_dialog.activateWindow()

    def _count_dirty(self) -> bool:
        return self._draft is not None and self.settings_editor.is_dirty()

    def _count_changed(self) -> None:
        error = self.settings_editor.validation_error() if self._draft is not None else None
        if error:
            self._set_text(self.edit_status, error)
        elif self._count_dirty():
            self._set_text(
                self.edit_status, "设定尚未应用，请先应用全部修改或取消，再确认添加场景。"
            )
        else:
            self._set_text(self.edit_status, "修改后点击应用，生成新的场景预览。")
        self._update_actions()

    def _cancel_count_edit(self) -> None:
        if self._draft is not None and not self._revising:
            self.settings_editor.cancel()
            self._count_changed()

    def _request_revision(self) -> None:
        if not self.apply_count_button.isEnabled() or self._draft is None:
            return
        revision = self.settings_editor.revision()
        self.set_revision_state(True, "正在按新设定更新场景和发车计划……")
        self.revision_requested.emit(str(self._draft["task_id"]), revision)

    def set_revision_state(self, busy: bool, message: str) -> None:
        self._revising = busy
        self._set_text(self.edit_status, message)
        self.progress.setVisible(busy or self._busy)
        self._update_actions()

    def _request_generation(self) -> None:
        description = self.description.text().strip()
        if self._busy or self._publishing or self._revising:
            return
        if not description:
            self._set_text(self.status_label, "先写一句场景需求，例如“生成一个十字路口”。")
            self.description.setFocus()
            return
        self._submitted_description = description
        self.set_generation_state(True, "正在理解需求、生成路网和发车计划…")
        self.generation_requested.emit(description)

    def _description_changed(self) -> None:
        if self._draft is not None and not self._draft_current:
            self._set_text(self.status_label, "下方为上一次预览，重新生成成功后才能添加。")
        elif self._draft is not None and not self._matches_draft():
            self._set_text(
                self.status_label, "需求已修改；下方仍是上一次预览，请重新生成后再添加。"
            )
        elif self._draft is not None and not self._busy and not self._publishing:
            self._set_text(self.status_label, "场景预览已就绪，请核对地图与车辆设定。")
        self._update_actions()

    def _matches_draft(self) -> bool:
        return (
            self._draft is not None
            and self.description.text().strip() == str(self._draft.get("description", "")).strip()
        )

    def _request_publication(self) -> None:
        if not self.publish_button.isEnabled() or self._draft is None:
            return
        self.set_publication_state(True, "正在添加到场景…")
        self.publish_requested.emit(str(self._draft["task_id"]))

    def _request_configuration(self) -> None:
        if self._published_map_id:
            self.configure_requested.emit(self._published_map_id)

    def set_generation_state(self, busy: bool, message: str) -> None:
        self._busy = busy
        if busy:
            self.error_card.hide()
            self._draft_current = False
            self._image_ready = False
            self._published_map_id = None
            self.configure_button.hide()
            self._set_text(self.publish_button, "确认，添加到场景")
            self._set_text(self.confirmation_label, "下方为上一次预览，新预览生成后才能确认添加。")
        self._set_text(self.status_label, message)
        self.progress.setVisible(busy)
        self._set_text(
            self.generate_button,
            "正在生成…" if busy else ("重新生成  →" if self._draft is not None else "生成预览  →"),
        )
        self._update_actions()

    def set_generation_error(self, message: str) -> None:
        """Keep a failed request's cause visible until the user retries successfully."""
        self.set_generation_state(False, "生成未完成，请查看下方原因。")
        self._set_text(self.error_message, message)
        self.error_card.show()
        self._set_text(self.generate_button, "重试生成  →")
        self._content_layout.activate()
        self.scroll_area.ensureWidgetVisible(self.error_card)

    def set_draft(self, draft: dict[str, Any]) -> None:
        self._draft = draft
        self._draft_current = True
        self._busy = False
        self._publishing = False
        self._revising = False
        self._image_ready = False
        self._published_map_id = None
        if self._image_dialog is not None:
            self._image_dialog.close()
            self._image_dialog.deleteLater()
            self._image_dialog = None
        self.progress.hide()
        self.error_card.hide()
        self.results.show()
        self.motif.hide()
        self.examples.hide()
        self._set_text(self.title, "你的场景，即将就绪")
        self.title.setProperty("studioRole", "previewTitle")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self._set_text(self.subtitle, "先看看路网和交通设定，再决定是否添加到场景。")
        self._set_text(self.generate_button, "重新生成  →")
        self._content_layout.setStretch(0, 0)
        self._content_layout.setStretch(self._content_layout.count() - 1, 1)
        self.map_image.clear_image("正在加载路网预览…")
        self.preview_retry_button.hide()
        plan = draft.get("plan") or {}
        demand = draft.get("demand") or {}
        self._set_text(
            self.state_badge, _STATE_NAMES.get(str(plan.get("traffic_state")), "常规交通")
        )
        count = int(demand.get("planned_vehicles", 0))
        requested_mix = plan.get("automation_mix") or demand.get("automation_mix") or []
        mode = plan.get("demand_mode") or (
            "count" if plan.get("requested_vehicle_count") is not None else "inflow"
        )
        if mode not in {"count", "inflow", "density", "total_flow"}:
            mode = "inflow"
        current_total_flow = float(
            demand.get("realized_schedule_veh_per_hour")
            or count * 3600 / int(plan.get("duration_s", 600))
        )
        self.settings_editor.load(
            ScenarioSettingsValues(
                vehicle_count=count,
                desired_density_veh_per_km_lane=float(
                    plan.get("desired_density_veh_per_km_lane", 22)
                ),
                duration_s=int(plan.get("duration_s", 600)),
                warmup_s=int(plan.get("warmup_s", 120)),
                seed=int(plan.get("seed", 42)),
                inflow_veh_per_hour_per_lane=float(
                    plan.get("inflow_veh_per_hour_per_lane")
                    or current_total_flow / max(1, int(demand.get("origin_lane_count", 1)))
                ),
                total_flow_veh_per_hour=float(
                    plan.get("requested_total_flow_veh_per_hour") or current_total_flow
                ),
                reference_speed_kmh=float(plan.get("reference_speed_kmh") or 30),
                demand_mode=cast(DemandMode, mode),
                automation_mix=tuple(
                    (str(item["level"]), float(item["share_percent"])) for item in requested_mix
                )
                or (("L0", 100.0),),
            )
        )
        self._set_text(
            self.edit_status,
            "已按手动设定更新场景，可继续修改或确认添加。"
            if draft.get("manual_overrides")
            else "修改后点击应用，生成新的场景预览。",
        )
        self._render_draft_copy()
        self.notes_button.setChecked(False)
        self.notes_button.setVisible(bool(draft.get("notes")))
        self.notes_label.hide()
        self._set_text(self.confirmation_label, "预览尚未添加到场景。确认后可在仿真配置中使用。")
        self.configure_button.hide()
        self._set_text(self.publish_button, "确认，添加到场景")
        self._description_changed()

    def _render_draft_copy(self) -> None:
        if self._draft is None:
            return
        draft = self._draft
        plan, demand = draft.get("plan") or {}, draft.get("demand") or {}
        count = int(demand.get("planned_vehicles", 0))
        self._set_text(self.stat_values["vehicles"], f"{count:,} " + self._t("辆", "vehicles"))
        self._set_text(
            self.stat_values["density"], f"{plan.get('desired_density_veh_per_km_lane', 0):g}"
        )
        self._set_text(self.stat_values["duration"], self._time_text(plan.get("duration_s", 0)))
        self._set_text(self.stat_values["warmup"], self._time_text(plan.get("warmup_s", 0)))
        composition = demand.get("automation_mix") or []
        self.composition_chart.shares = [float(item["share_percent"]) / 100 for item in composition]
        self.composition_chart.update()
        self._set_text(
            self.composition_legend,
            "\n".join(
                f"{item['level']}  {float(item['share_percent']):g}%"
                f"  ·  {int(item['vehicle_count']):,} " + self._t("辆", "vehicles")
                for item in composition
            )
            or self._t("未提供等级分配"),
        )
        if not self._traffic_mode:
            self._set_text(self.demand_note, "比例按生成发车计划统计，不代表运行中的实时车辆比例。")
        flow = plan.get("inflow_veh_per_hour_per_lane")
        flow_text = (
            text(
                self._language,
                f"每入口车道 {flow:g} 辆/小时 · ",
                f"{flow:g} veh/h per entry lane · ",
            )
            if flow is not None
            else self._t("按总发车数编排 · ", "Scheduled by total departures · ")
        )
        total_flow = demand.get("realized_schedule_veh_per_hour")
        total_text = (
            text(
                self._language,
                f"总发车量 {total_flow:g} 辆/小时\n",
                f"Total departures: {total_flow:g} veh/h\n",
            )
            if total_flow is not None
            else ""
        )
        self._set_text(
            self.flow_label,
            total_text + flow_text + self._t("随机种子") + f" {plan.get('seed', 42)}",
        )
        self._set_text(self.automation_note, str(plan.get("automation_note") or ""))
        self.automation_note.setVisible(bool(plan.get("automation_note")))
        defaults = plan.get("defaults_used", [])
        self._set_text(
            self.defaults_label,
            self._t("默认设定：", "Defaults: ")
            + self._t("、", ", ").join(self._t(item) for item in defaults)
            if defaults
            else "已按需求设置场景参数。",
        )
        pending = draft.get("unverified_requirements", [])
        self._set_text(self.review_title, "请留意这些要求" if pending else "生成说明")
        self._set_text(
            self.requirements_label,
            self._t("待验证：", "To verify (your requirements): ") + "; ".join(pending)
            if pending
            else "目标密度用于规划车流，不代表运行后的实测密度。请自行判断路网是否适合测试。",
        )
        if draft.get("manual_overrides"):
            self._set_text(
                self.requirements_label,
                (
                    self._t("待验证：", "To verify (your requirements): ")
                    + "; ".join(pending)
                    + "\n"
                    if pending
                    else ""
                )
                + self._t("场景已按手动设定更新；目标密度和拥堵状态仍需在仿真后验证。"),
            )
        notes = draft.get("notes", [])
        self._set_text(
            self.notes_label,
            (self._t("", "Original source notes:\n") if notes else "")
            + "\n".join(f"• {note}" for note in notes),
        )
        self._set_text(
            self.notes_button,
            text(
                self._language,
                f"查看生成细节（{len(notes)} 条）",
                f"Generation details ({len(notes)})",
            ),
        )

    def _time_text(self, seconds: int | float) -> str:
        return (
            f"{seconds / 60:g} " + self._t("分钟", "min")
            if seconds % 60 == 0
            else f"{seconds:g} " + self._t("秒", "s")
        )

    def set_preview_image(self, data: bytes) -> None:
        if not self._draft_current:
            return
        self._image_ready = self._draft is not None and self.map_image.load(data)
        if self._image_ready and self._image_dialog is not None:
            self._image_dialog.set_pixmap(self.map_image.source_pixmap)
        self.preview_retry_button.setVisible(not self._image_ready)
        self._update_actions()

    def set_preview_error(self, message: str) -> None:
        self._image_ready = False
        self.map_image.clear_image(message)
        self.preview_retry_button.show()
        self._update_actions()

    def _retry_preview(self) -> None:
        if self._draft is not None and not self._busy:
            self.preview_retry_button.hide()
            self.map_image.clear_image("正在重新加载路网预览…")
            self.preview_retry_requested.emit(str(self._draft["task_id"]))

    def set_publication_state(self, busy: bool, message: str) -> None:
        self._publishing = busy
        self._set_text(self.confirmation_label, message)
        self._update_actions()

    def set_published(self, map_id: str) -> None:
        self._published_map_id = map_id
        self._publishing = False
        self._set_text(self.publish_button, "✓  已添加到场景")
        self.configure_button.show()
        self._set_text(self.confirmation_label, "已添加。进入仿真配置后，可由你决定何时启动。")
        self._update_actions()

    def _update_actions(self) -> None:
        idle = not self._busy and not self._publishing and not self._revising
        dirty = self._count_dirty()
        editable = idle and self._draft_current and self._matches_draft()
        self.settings_editor.setEnabled(editable)
        self.apply_count_button.setEnabled(
            editable and dirty and self.settings_editor.validation_error() is None
        )
        self.cancel_count_button.setEnabled(editable and dirty)
        self.zoom_button.setEnabled(self._image_ready)
        self.generate_button.setEnabled(idle)
        self.description.setEnabled(idle)
        self.examples.setEnabled(idle)
        self.preview_retry_button.setEnabled(idle and self._draft_current)
        self.publish_button.setEnabled(
            idle
            and self._draft_current
            and self._image_ready
            and self._matches_draft()
            and not self._published_map_id
            and not dirty
        )
        self.configure_button.setEnabled(idle and bool(self._published_map_id) and not dirty)

    def set_theme(self, theme: str) -> None:
        dark = theme == "dark"
        self.setProperty("studioTheme", "dark" if dark else "light")
        self.setStyleSheet(_STYLE_PATH.read_text(encoding="utf-8"))
        colors = load_scene_studio_colors(ThemeMode.DARK if dark else ThemeMode.LIGHT)
        self.motif.colors = colors
        self.motif.update()
        self.composition_chart.colors = colors
        self.composition_chart.update()
