from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QWidget
from ui.widgets.image_preview_dialog import ImagePreviewDialog


def _application() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _pixmap() -> QPixmap:
    pixmap = QPixmap(2400, 1600)
    pixmap.fill(QColor("steelblue"))
    return pixmap


def test_image_fits_then_zooms_and_returns_to_original_without_changing_source() -> None:
    app = _application()
    source = _pixmap()
    image_before = source.toImage()
    dialog = ImagePreviewDialog(source)
    dialog.show()
    app.processEvents()
    assert not dialog.isModal()
    fit = dialog.zoom_factor
    assert 0 < fit < 1
    dialog.zoom_in_button.click()
    assert dialog.zoom_factor == pytest.approx(fit * 1.25)
    dialog.zoom_out_button.click()
    assert dialog.zoom_factor == pytest.approx(fit)
    dialog.original_button.click()
    assert dialog.zoom_factor == pytest.approx(1)
    assert dialog.image_view.horizontalScrollBar().maximum() > 0
    assert dialog.image_view.verticalScrollBar().maximum() > 0
    dialog.fit_button.click()
    assert dialog.zoom_factor < 1
    assert source.toImage() == image_before
    dialog.close()


def test_wheel_zoom_is_bounded_and_dialog_escape_does_not_close_parent() -> None:
    app = _application()
    parent = QWidget()
    parent.show()
    dialog = ImagePreviewDialog(_pixmap(), parent)
    dialog.show()
    app.processEvents()
    view = dialog.image_view
    start = dialog.zoom_factor
    wheel = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(view.viewport(), wheel)
    assert dialog.zoom_factor == pytest.approx(start * 1.25)
    for _ in range(50):
        dialog.zoom_in_button.click()
    assert dialog.zoom_factor == pytest.approx(8)
    assert not dialog.zoom_in_button.isEnabled()
    for _ in range(100):
        dialog.zoom_out_button.click()
    assert dialog.zoom_factor == pytest.approx(view.minimum_zoom)
    assert not dialog.zoom_out_button.isEnabled()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert parent.isVisible()
    parent.close()


def test_drag_pans_an_enlarged_image_without_changing_zoom() -> None:
    app = _application()
    dialog = ImagePreviewDialog(_pixmap())
    dialog.show()
    app.processEvents()
    dialog.original_button.click()
    viewport = dialog.image_view.viewport()
    scrollbar = dialog.image_view.horizontalScrollBar()
    previous = scrollbar.value()
    for event_type, x, button, buttons in (
        (QEvent.Type.MouseButtonPress, 200, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseMove, 240, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, 240, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            event_type,
            QPointF(x, 200),
            QPointF(viewport.mapToGlobal(QPoint(x, 200))),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, event)
    assert scrollbar.value() < previous
    assert dialog.zoom_factor == pytest.approx(1)
    dialog.close()


def test_fit_tracks_window_resize_but_manual_zoom_is_preserved() -> None:
    app = _application()
    dialog = ImagePreviewDialog(_pixmap())
    dialog.resize(640, 480)
    dialog.show()
    app.processEvents()
    small = dialog.zoom_factor
    dialog.resize(900, 650)
    app.processEvents()
    assert dialog.zoom_factor > small
    dialog.original_button.click()
    dialog.resize(700, 500)
    app.processEvents()
    assert dialog.zoom_factor == pytest.approx(1)
    dialog.close()


def test_missing_image_disables_all_image_actions_and_keeps_close_available() -> None:
    app = _application()
    dialog = ImagePreviewDialog(QPixmap())
    dialog.show()
    app.processEvents()
    assert "unavailable" in dialog.hint.text()
    assert not dialog.zoom_in_button.isEnabled()
    assert not dialog.zoom_out_button.isEnabled()
    assert not dialog.fit_button.isEnabled()
    assert not dialog.original_button.isEnabled()
    assert dialog.close_button.isEnabled()
    dialog.close_button.click()
    assert not dialog.isVisible()


def test_language_switch_preserves_image_zoom_and_pan() -> None:
    app = _application()
    dialog = ImagePreviewDialog(_pixmap())
    dialog.show()
    app.processEvents()
    assert dialog.language == "en"
    assert dialog.fit_button.text() == "Fit to window"
    dialog.original_button.click()
    dialog.zoom_in_button.click()
    app.processEvents()
    view = dialog.image_view
    view.horizontalScrollBar().setValue(460)
    view.verticalScrollBar().setValue(250)
    zoom = dialog.zoom_factor
    pan = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
    scene = view.scene()
    assert scene is not None
    items = scene.items()
    dialog.set_language("zh")
    app.processEvents()
    assert dialog.fit_button.text() == "适应窗口"
    assert dialog.close_button.text() == "关闭"
    assert dialog.zoom_factor == pytest.approx(zoom)
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value()) == pan
    assert scene.items() == items
    dialog.set_language("en")
    app.processEvents()
    assert dialog.close_button.text() == "Close"
    assert dialog.zoom_factor == pytest.approx(zoom)
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value()) == pan
    dialog.close()


def test_missing_image_language_switch_keeps_actions_disabled() -> None:
    _application()
    dialog = ImagePreviewDialog(QPixmap(), language="zh")
    assert "无法显示" in dialog.hint.text()
    dialog.set_language("en")
    assert "unavailable" in dialog.hint.text()
    assert not dialog.fit_button.isEnabled()
    assert not dialog.zoom_in_button.isEnabled()
    assert dialog.zoom_label.text() == "—"
    dialog.close()


def test_same_size_image_replacement_updates_pixels_without_resetting_zoom_or_pan() -> None:
    app = _application()
    dialog = ImagePreviewDialog(_pixmap())
    dialog.show()
    app.processEvents()
    dialog.original_button.click()
    dialog.zoom_in_button.click()
    app.processEvents()
    view = dialog.image_view
    view.horizontalScrollBar().setValue(417)
    view.verticalScrollBar().setValue(289)
    transform = view.transform()
    center = view.mapToScene(view.viewport().rect().center())
    pan = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
    replacement = QPixmap(2400, 1600)
    replacement.fill(QColor("coral"))
    assert dialog.set_pixmap(replacement)
    dialog.set_language("zh")
    app.processEvents()
    assert view.transform() == transform
    assert view.mapToScene(view.viewport().rect().center()) == center
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value()) == pan
    scene = view.scene()
    assert scene is not None
    assert len(scene.items()) == 1
    item = scene.items()[0]
    assert isinstance(item, QGraphicsPixmapItem)
    assert item.pixmap().toImage().pixelColor(0, 0) == QColor("coral")
    dialog.resize(dialog.width() - 30, dialog.height() - 30)
    app.processEvents()
    assert view.transform() == transform  # The manual zoom mode was retained too.
    dialog.close()


def test_invalid_replacement_preserves_view_and_first_valid_image_recovers_empty_state() -> None:
    app = _application()
    dialog = ImagePreviewDialog(QPixmap())
    dialog.show()
    app.processEvents()
    assert dialog.set_pixmap(_pixmap())
    app.processEvents()
    assert dialog.image_view.isVisible()
    assert dialog.fit_button.isEnabled()
    assert dialog.original_button.isEnabled()
    assert 0 < dialog.zoom_factor < 1
    assert "Scroll to zoom" in dialog.hint.text()
    scene = dialog.image_view.scene()
    assert scene is not None
    item = scene.items()[0]
    assert isinstance(item, QGraphicsPixmapItem)
    pixels = item.pixmap().toImage()
    transform = dialog.image_view.transform()
    assert not dialog.set_pixmap(QPixmap())
    app.processEvents()
    assert item.pixmap().toImage() == pixels
    assert dialog.image_view.transform() == transform
    assert dialog.fit_button.isEnabled()
    dialog.close()
