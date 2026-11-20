"""Geographic area selection without publishing a scene or advancing simulation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.api_client.map_selection import MapSelectionClient
from ui.models.map_selection import MapSearchPlace, MapSelectionBounds, SelectionConfig
from ui.scene_language import Language, normalize_language, text

_HTML = Path(__file__).resolve().parents[1] / "web/area_selection/index.html"


class _AreaBridge(QObject):
    ready = Signal()
    bounds_changed = Signal(str)
    tiles_ready = Signal(bool)
    tiles_failed = Signal()
    drawing_finished = Signal()

    @Slot()
    def mapReady(self) -> None:  # noqa: N802
        self.ready.emit()

    @Slot(str)
    def boundsChanged(self, value: str) -> None:  # noqa: N802
        self.bounds_changed.emit(value)

    @Slot(bool)
    def tilesReady(self, value: bool) -> None:  # noqa: N802
        self.tiles_ready.emit(value)

    @Slot()
    def tilesFailed(self) -> None:  # noqa: N802
        self.tiles_failed.emit()

    @Slot()
    def drawingFinished(self) -> None:  # noqa: N802
        self.drawing_finished.emit()


class _AreaPage(QWebEnginePage):
    script_failed = Signal(str)

    def javaScriptConsoleMessage(  # noqa: N802
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.script_failed.emit(message[:300])

    def acceptNavigationRequest(  # noqa: N802
        self, url: QUrl | str, navigation_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        url = QUrl(url)
        if not is_main_frame:
            return False
        if url.isLocalFile() and Path(url.toLocalFile()).resolve() == _HTML:
            return True
        if (
            navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            and url.scheme() == "https"
            and url.host() == "www.openstreetmap.org"
        ):
            QDesktopServices.openUrl(url)
        return False


class MapAreaDialog(QDialog):
    """Select immutable WGS84 bounds; callers apply them only after acceptance."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        language: Language = "en",
        selected_bounds: MapSelectionBounds | None = None,
        base_url: str | None = None,
        load_page: bool = True,
    ) -> None:
        super().__init__(parent)
        self._language = normalize_language(language)
        self._bounds = selected_bounds
        self._config: SelectionConfig | None = None
        self._ready = False
        self._tiles_ready = False
        self._places: list[MapSearchPlace] = []
        self.setObjectName("mapAreaDialog")
        self.setWindowTitle(self._text("选择地图区域", "Select a map area"))
        self.resize(1040, 760)
        self.setMinimumSize(720, 560)
        self._client = MapSelectionClient(self, base_url=base_url)
        self._client.config_ready.connect(self._config_ready)
        self._client.places_ready.connect(self._places_ready)
        self._client.failed.connect(self._request_failed)
        self._build_ui()
        self._update_selection()
        if load_page:
            self.web.load(QUrl.fromLocalFile(str(_HTML)))
            self._client.load_config()

    @property
    def selected_bounds(self) -> MapSelectionBounds | None:
        return self._bounds

    def _text(self, zh: str, en: str) -> str:
        return text(self._language, zh, en)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel(
            self._text("找到位置，再框选测试区域", "Find a place. Frame your test area.")
        )
        title.setObjectName("mapAreaTitle")
        layout.addWidget(title)
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("mapAreaSearch")
        self.search_input.setMaxLength(240)
        self.search_input.setPlaceholderText(
            self._text("搜索城市、地标或地址…", "Search a city, landmark or address…")
        )
        self.search_input.returnPressed.connect(self._search)
        search_row.addWidget(self.search_input, 1)
        self.search_button = QPushButton(self._text("搜索", "Search"))
        self.search_button.clicked.connect(self._search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        self.lookup_status = QLabel()
        self.lookup_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lookup_status.setWordWrap(True)
        self.lookup_status.hide()
        layout.addWidget(self.lookup_status)
        self.place_list = QListWidget()
        self.place_list.setMaximumHeight(95)
        self.place_list.currentRowChanged.connect(self._choose_place)
        self.place_list.hide()
        layout.addWidget(self.place_list)
        controls = QHBoxLayout()
        self.draw_button = QPushButton(self._text("绘制区域", "Draw area"))
        self.draw_button.setCheckable(True)
        self.draw_button.setEnabled(False)
        self.draw_button.toggled.connect(self._draw_toggled)
        controls.addWidget(self.draw_button)
        self.clear_button = QPushButton(self._text("清除框选", "Clear selection"))
        self.clear_button.clicked.connect(self._clear)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        self.retry_button = QPushButton(self._text("重新加载", "Reload map / service"))
        self.retry_button.clicked.connect(self._retry)
        controls.addWidget(self.retry_button)
        layout.addLayout(controls)
        self.web = QWebEngineView(self)
        self.web.setObjectName("mapAreaWebView")
        # A named Chromium profile persists normal HTTP caching across visits. Only visible
        # tiles are requested; the native client is identified per the OSM tile policy.
        profile = QWebEngineProfile("TrafficForgeArea", self.web)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setHttpCacheMaximumSize(64 * 1024 * 1024)
        profile.setHttpUserAgent(profile.httpUserAgent() + " TrafficForge/1.0")
        page = _AreaPage(profile, self.web)
        page.script_failed.connect(self._script_error)
        self.web.setPage(page)
        # Delete the profile after its page is gone, even when the dialog is destroyed.
        profile.setParent(None)
        page.destroyed.connect(profile.deleteLater)
        self._profile = profile
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self._bridge = _AreaBridge(self)
        self._bridge.ready.connect(self._map_ready)
        self._bridge.bounds_changed.connect(self._bounds_changed)
        self._bridge.tiles_ready.connect(self._tile_state)
        self._bridge.tiles_failed.connect(self._tile_failure)
        self._bridge.drawing_finished.connect(self._drawing_finished)
        channel = QWebChannel(page)
        channel.registerObject("areaBridge", self._bridge)
        page.setWebChannel(channel)
        self.web.loadStarted.connect(self._loading)
        self.web.loadFinished.connect(self._page_loaded)
        layout.addWidget(self.web, 1)
        self.map_status = QLabel(self._text("正在加载底图…", "Loading the base map…"))
        self.map_status.setWordWrap(True)
        self.map_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.map_status)
        self.config_status = QLabel(
            self._text("正在连接地图服务…", "Connecting to the map service…")
        )
        self.config_status.setWordWrap(True)
        self.config_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.config_status)
        self.selection_label = QLabel()
        self.selection_label.setWordWrap(True)
        self.selection_label.setObjectName("mapAreaSelection")
        layout.addWidget(self.selection_label)
        footer = QHBoxLayout()
        note = QLabel(
            self._text(
                "默认提取主干路，确认后回到需求输入页。",
                "Main roads by default. Return to your request after selecting.",
            )
        )
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        cancel = QPushButton(self._text("取消", "Cancel"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.confirm_button = QPushButton(self._text("使用此区域", "Use this area"))
        self.confirm_button.setObjectName("mapAreaConfirm")
        self.confirm_button.clicked.connect(self.accept)
        self.confirm_button.setDefault(False)
        footer.addWidget(self.confirm_button)
        layout.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def _dispatch(self, method: str, value: object = None) -> None:
        if self._ready:
            payload = json.dumps(value, ensure_ascii=False)
            self.web.page().runJavaScript(f"window.TrafficForgeArea.{method}({payload});")

    def _map_ready(self) -> None:
        self._ready = True
        self.draw_button.setEnabled(True)
        self._dispatch("setLanguage", self._language)
        if self._bounds is not None:
            self._dispatch("setBounds", self._bounds.model_dump())
        self._update_selection()

    def _loading(self) -> None:
        self._ready = False
        self._tiles_ready = False
        self.draw_button.setEnabled(False)
        self.map_status.setText(self._text("正在加载底图…", "Loading the base map…"))
        self._drawing_finished()

    def _page_loaded(self, success: bool) -> None:
        if not success:
            self._ready = False
            self.draw_button.setEnabled(False)
            self.map_status.setText(
                self._text(
                    "地图页面加载失败，请点击重新加载。",
                    "Map page failed to load. Click Reload map / service.",
                )
            )
            self._update_selection()

    def _script_error(self, reason: str) -> None:
        self._ready = False
        self._tiles_ready = False
        self.draw_button.setEnabled(False)
        self.map_status.setText(
            self._text(
                "地图页面出现错误，请点击重新加载：",
                "Map page error. Click Reload map / service: ",
            )
            + reason
        )
        self._update_selection()

    def _tile_state(self, ready: bool) -> None:
        self._tiles_ready = ready
        self.map_status.setText(
            self._text("底图已就绪 · 滚轮缩放，拖动平移", "Map ready · Scroll to zoom, drag to pan")
            if ready
            else self._text("正在加载底图…", "Loading the base map…")
        )
        self._update_selection()

    def _tile_failure(self) -> None:
        self._tiles_ready = False
        self.map_status.setText(
            self._text(
                "底图加载失败。请检查网络后点击重新加载。",
                "Base map could not load. Check your connection, then click Reload map / service.",
            )
        )
        self._update_selection()

    def _config_ready(self, config: SelectionConfig) -> None:
        self._config = config
        self.config_status.setText(
            self._text("区域最大对角线", "Maximum area diagonal")
            + f": {config.max_diagonal_m / 1000:g} km"
        )
        self._update_selection()

    def _bounds_changed(self, value: str) -> None:
        try:
            self._bounds = (
                MapSelectionBounds.model_validate_json(value) if len(value) < 1024 else None
            )
        except ValidationError:
            self._bounds = None
        self._update_selection()

    def _update_selection(self) -> None:
        valid = self._bounds is not None and self._config is not None
        if self._bounds is None:
            self.selection_label.setText(self._text("尚未框选区域", "No area selected"))
        else:
            bounds = self._bounds
            summary = f"{bounds.width_m / 1000:.2f} × {bounds.height_m / 1000:.2f} km"
            summary += f"  ·  {bounds.longitude:.5f}, {bounds.latitude:.5f}"
            if self._config is not None and bounds.diagonal_m > self._config.max_diagonal_m:
                valid = False
                summary += self._text(
                    "  ·  范围过大，请重新框选更小区域", "  ·  Too large. Draw a smaller area."
                )
            self.selection_label.setText(summary)
        self.clear_button.setEnabled(self._bounds is not None)
        self.confirm_button.setEnabled(
            bool(valid and self._ready and self._tiles_ready and not self.draw_button.isChecked())
        )

    def _draw_toggled(self, checked: bool) -> None:
        self._dispatch("draw", checked)
        self._update_selection()

    def _drawing_finished(self) -> None:
        self.draw_button.blockSignals(True)
        self.draw_button.setChecked(False)
        self.draw_button.blockSignals(False)
        self._update_selection()

    def _clear(self) -> None:
        self._bounds = None
        self._dispatch("clear")
        self._drawing_finished()

    def _search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        self.place_list.hide()
        self.search_button.setEnabled(False)
        self.lookup_status.setText(self._text("正在搜索地点…", "Searching for places…"))
        self.lookup_status.show()
        self._client.search(query)

    def _places_ready(self, places: list[MapSearchPlace]) -> None:
        self.search_button.setEnabled(True)
        self._places = places
        self.place_list.blockSignals(True)
        self.place_list.clear()
        self.place_list.addItems([place.label for place in places])
        self.place_list.blockSignals(False)
        self.place_list.setVisible(bool(places))
        self.lookup_status.setText(
            self._text(
                "选择一个地点定位，再绘制区域。", "Choose a place to locate it, then draw an area."
            )
            if places
            else self._text(
                "未找到地点，请补充城市或完整地址。",
                "No places found. Add a city or a more complete address.",
            )
        )

    def _choose_place(self, index: int) -> None:
        if 0 <= index < len(self._places):
            self._clear()
            self._dispatch("locate", self._places[index].model_dump())
            self._drawing_finished()

    def _request_failed(self, operation: str, reason: str) -> None:
        if operation == "config":
            self._config = None
            self.config_status.setText(
                self._text(
                    "地图服务不可用，请启动或更新 TrafficForge 后重新加载。",
                    "Map service unavailable. Start or update TrafficForge, then reload.",
                )
                + " "
                + reason
            )
        else:
            self.search_button.setEnabled(True)
            self.lookup_status.setText(
                self._text(
                    "地点搜索失败，请检查地图服务或补充地址后重试。",
                    "Place search failed. Check the map service or refine the address, then retry.",
                )
                + " "
                + reason
            )
            self.lookup_status.show()
        self._update_selection()

    def _retry(self) -> None:
        self._client.load_config()
        if self._ready:
            self._dispatch("retry")
        else:
            self.web.load(QUrl.fromLocalFile(str(_HTML)))

    def accept(self) -> None:
        if self.confirm_button.isEnabled():
            super().accept()

    def done(self, result: int) -> None:
        self._client.close()
        self.web.stop()
        super().done(result)
