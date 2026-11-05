"""Read-only image viewer with bounded zoom, fit and drag-to-pan controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPixmap, QResizeEvent, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.scene_language import Language, normalize_language, text

_ZOOM_STEP = 1.25
_MIN_ZOOM = 0.05
_MAX_ZOOM = 8.0


class _ImageView(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("imagePreviewView")
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._fit_mode = True
        self._has_image = not pixmap.isNull()
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self._image_item: QGraphicsPixmapItem | None = None
        if self._has_image:
            # Preserve the source pixels; a high-DPI widget capture may carry display metadata.
            source = pixmap.copy()
            source.setDevicePixelRatio(1.0)
            self._image_item = scene.addPixmap(source)
            scene.setSceneRect(self._image_item.boundingRect())

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """Replace pixels in place; a same-size language variant leaves the view unchanged."""
        source = pixmap.copy()
        source.setDevicePixelRatio(1.0)
        scene = self.scene()
        assert scene is not None
        center = self.mapToScene(self.viewport().rect().center())
        previous_rect = scene.sceneRect()
        if self._image_item is None:
            self._image_item = scene.addPixmap(source)
        else:
            self._image_item.setPixmap(source)
        self._has_image = True
        bounds = self._image_item.boundingRect()
        if bounds != previous_rect:
            scene.setSceneRect(bounds)
            if self._fit_mode:
                self.fit_image()
            else:
                self.centerOn(center)

    @property
    def zoom_factor(self) -> float:
        return self.transform().m11()

    @property
    def minimum_zoom(self) -> float:
        # Fitting a very large source must remain possible below the normal zoom-out bound.
        return min(_MIN_ZOOM, self._fit_scale())

    def _fit_scale(self) -> float:
        rect = self.sceneRect()
        if rect.isEmpty():
            return 1.0
        return min(
            1.0,
            max(1, self.viewport().width() - 4) / rect.width(),
            max(1, self.viewport().height() - 4) / rect.height(),
        )

    def fit_image(self) -> None:
        if not self._has_image:
            return
        self._fit_mode = True
        self._apply_zoom(self._fit_scale())
        self.centerOn(self.sceneRect().center())

    def original_size(self) -> None:
        if not self._has_image:
            return
        self._fit_mode = False
        self._apply_zoom(1.0)

    def zoom_by(self, factor: float, *, under_mouse: bool = False) -> None:
        if not self._has_image:
            return
        self._fit_mode = False
        self._apply_zoom(self.zoom_factor * factor, under_mouse=under_mouse)

    def _apply_zoom(self, target: float, *, under_mouse: bool = False) -> None:
        target = min(_MAX_ZOOM, max(self.minimum_zoom, target))
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
            if under_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        ratio = target / self.zoom_factor
        self.scale(ratio, ratio)
        self.zoom_changed.emit(self.zoom_factor)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta or not self._has_image:
            event.ignore()
            return
        self.zoom_by(_ZOOM_STEP ** max(-3.0, min(3.0, delta / 120)), under_mouse=True)
        event.accept()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_image()


class ImagePreviewDialog(QDialog):
    """Show an independent image copy without changing the scene's authored settings."""

    def __init__(
        self, pixmap: QPixmap, parent: QWidget | None = None, *, language: Language = "en"
    ) -> None:
        super().__init__(parent)
        self._language = normalize_language(language)
        self._has_image = not pixmap.isNull()
        self.setObjectName("imagePreviewDialog")
        self.setWindowTitle("路网预览 · 放大查看")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        screen = self.screen()
        available = screen.availableGeometry()
        self.resize(
            min(1200, int(available.width() * 0.9)), min(850, int(available.height() * 0.9))
        )
        self.setMinimumSize(min(480, available.width()), min(320, available.height()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setAccessibleName("缩小图片")
        self.zoom_out_button.setToolTip("缩小（−）")
        toolbar.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(64)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setAccessibleName("当前缩放比例")
        toolbar.addWidget(self.zoom_label)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setAccessibleName("放大图片")
        self.zoom_in_button.setToolTip("放大（+ / =）")
        toolbar.addWidget(self.zoom_in_button)
        self.original_button = QPushButton("原始尺寸 1:1")
        self.original_button.setToolTip("恢复 100%（1）")
        toolbar.addWidget(self.original_button)
        self.fit_button = QPushButton("适应窗口")
        self.fit_button.setToolTip("适应窗口（0 / F）")
        toolbar.addWidget(self.fit_button)
        toolbar.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        toolbar.addWidget(self.close_button)
        layout.addLayout(toolbar)
        self.image_view = _ImageView(pixmap, self)
        layout.addWidget(self.image_view, 1)
        self.hint = QLabel("滚轮缩放 · 按住左键拖拽平移 · Esc 关闭")
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.zoom_in_button.clicked.connect(lambda: self.image_view.zoom_by(_ZOOM_STEP))
        self.zoom_out_button.clicked.connect(lambda: self.image_view.zoom_by(1 / _ZOOM_STEP))
        self.original_button.clicked.connect(self.image_view.original_size)
        self.fit_button.clicked.connect(self.image_view.fit_image)
        self.image_view.zoom_changed.connect(self._update_zoom)
        self._shortcuts: list[QShortcut] = []
        for keys, action in (
            (("+", "="), self.zoom_in_button.click),
            (("-",), self.zoom_out_button.click),
            (("1",), self.original_button.click),
            (("0", "F"), self.fit_button.click),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(action)
                self._shortcuts.append(shortcut)
        if pixmap.isNull():
            self.image_view.hide()
            self.hint.setText("路网图片尚未加载或无法显示，请关闭后重试加载图片。")
            self.zoom_label.setText("—")
            for button in (
                self.zoom_in_button,
                self.zoom_out_button,
                self.original_button,
                self.fit_button,
            ):
                button.setEnabled(False)
        else:
            QTimer.singleShot(0, self.image_view.fit_image)
        self.set_language(language)

    @property
    def language(self) -> Language:
        """Return the current presentation language."""
        return self._language

    def set_language(self, language: str) -> None:
        """Change viewer copy without replacing the image or resetting its view transform."""
        self._language = normalize_language(language)
        self.setWindowTitle(self._text("路网预览 · 放大查看", "Road network preview · Zoom"))
        self.image_view.setAccessibleName(
            self._text(
                "路网图片，可滚轮缩放并拖拽平移", "Road network image. Scroll to zoom; drag to pan."
            )
        )
        self.zoom_out_button.setAccessibleName(self._text("缩小图片", "Zoom out"))
        self.zoom_out_button.setToolTip(self._text("缩小（−）", "Zoom out (−)"))
        self.zoom_label.setAccessibleName(self._text("当前缩放比例", "Current zoom"))
        self.zoom_in_button.setAccessibleName(self._text("放大图片", "Zoom in"))
        self.zoom_in_button.setToolTip(self._text("放大（+ / =）", "Zoom in (+ / =)"))
        self.original_button.setText(self._text("原始尺寸 1:1", "Actual size 1:1"))
        self.original_button.setToolTip(self._text("恢复 100%（1）", "Restore 100% (1)"))
        self.fit_button.setText(self._text("适应窗口", "Fit to window"))
        self.fit_button.setToolTip(self._text("适应窗口（0 / F）", "Fit to window (0 / F)"))
        self.close_button.setText(self._text("关闭", "Close"))
        self.hint.setText(
            self._text(
                "滚轮缩放 · 按住左键拖拽平移 · Esc 关闭",
                "Scroll to zoom · Drag to pan · Esc to close",
            )
            if self._has_image
            else self._text(
                "路网图片尚未加载或无法显示，请关闭后重试加载图片。",
                "The road network image is unavailable. Close this viewer and retry loading it.",
            )
        )

    def _text(self, zh: str, en: str) -> str:
        return text(self._language, zh, en)

    def set_pixmap(self, pixmap: QPixmap) -> bool:
        """Update a localized image without resetting same-size zoom/pan or reopening the dialog.

        An invalid image preserves the current view. A first valid image leaves the empty
        state and fits to the window; subsequent same-size variants only replace pixels.
        """
        if pixmap.isNull():
            return False
        had_image = self._has_image
        self.image_view.set_pixmap(pixmap)
        self._has_image = True
        self.image_view.show()
        self.original_button.setEnabled(True)
        self.fit_button.setEnabled(True)
        self.set_language(self._language)
        self._update_zoom(self.image_view.zoom_factor)
        if not had_image:
            QTimer.singleShot(0, self.image_view.fit_image)
        return True

    @property
    def zoom_factor(self) -> float:
        """Return the displayed scale relative to the original image pixel dimensions."""
        return self.image_view.zoom_factor

    def _update_zoom(self, factor: float) -> None:
        self.zoom_label.setText(f"{factor * 100:.1f}%")
        self.zoom_in_button.setEnabled(factor < _MAX_ZOOM - 1e-6)
        self.zoom_out_button.setEnabled(factor > self.image_view.minimum_zoom + 1e-6)
