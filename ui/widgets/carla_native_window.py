"""Qt host for the locally running CARLA native render window."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QWindow
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from ui.localized_widgets import (
    QLabel,
)


class CarlaWindowEmbedError(RuntimeError):
    """The configured native CARLA window cannot be wrapped by Qt."""


class CarlaNativeWindowHost(QWidget):
    """Own only the Qt foreign-window wrapper, never the CARLA process or tick."""

    health_changed = Signal(str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        window_id_env: str = "TRAFFICVERSE_CARLA_WINDOW_ID",
        auto_attach: bool = True,
        window_factory: Callable[[int], QWindow | None] | None = None,
        container_factory: Callable[[QWindow, QWidget], QWidget | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._window_id_env = window_id_env
        self._window_factory = window_factory or _wrap_foreign_window
        self._container_factory = container_factory or _create_window_container
        self._foreign_window: QWindow | None = None
        self._container: QWidget | None = None
        self._status = QLabel()
        self._status.setObjectName("carlaStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._status, 1)
        self._show_recovery("等待 CARLA 原生窗口句柄")
        if auto_attach:
            self.attach_from_environment()

    @property
    def attached(self) -> bool:
        return self._container is not None

    @Slot()
    def attach_from_environment(self) -> None:
        raw_window_id = os.getenv(self._window_id_env)
        if not raw_window_id:
            self._show_recovery(
                f"未设置 {self._window_id_env}；请以窗口模式启动 CARLA，设置其原生窗口 ID 后重试"
            )
            return
        try:
            window_id = int(raw_window_id, 0)
            self.attach(window_id)
        except (CarlaWindowEmbedError, ValueError) as error:
            self._show_recovery(str(error))

    def attach(self, window_id: int) -> None:
        if window_id <= 0:
            raise CarlaWindowEmbedError("CARLA 原生窗口 ID 必须是正整数")
        if self.attached:
            self.detach()
        foreign_window = self._window_factory(window_id)
        if foreign_window is None:
            raise CarlaWindowEmbedError(
                "当前平台无法包装 CARLA 原生窗口；请确认 CARLA 与 PySide6 位于同一图形会话"
            )
        container = self._container_factory(foreign_window, self)
        if container is None:
            foreign_window.setParent(None)
            raise CarlaWindowEmbedError("Qt 无法为 CARLA 原生窗口创建容器")
        container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        container.setMinimumSize(480, 270)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        self._status.hide()
        layout.addWidget(container, 1)
        self._foreign_window = foreign_window
        self._container = container
        container.setFocus(Qt.FocusReason.OtherFocusReason)
        self.health_changed.emit("HEALTHY", "CARLA 原生窗口已嵌入")

    @Slot()
    def detach(self) -> None:
        container = self._container
        foreign_window = self._foreign_window
        self._container = None
        self._foreign_window = None
        if container is not None:
            layout = self.layout()
            if layout is not None:
                layout.removeWidget(container)
            container.hide()
            container.setParent(None)
            container.deleteLater()
        if foreign_window is not None:
            foreign_window.setParent(None)
        self._show_recovery("CARLA 原生窗口已分离")

    @Slot(str)
    def show_unavailable(self, message: str) -> None:
        """Detach a stale foreign window and expose an actionable health message."""
        if self.attached:
            self.detach()
        self._show_recovery(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.detach()
        super().closeEvent(event)

    def _show_recovery(self, message: str) -> None:
        self._status.setText(f"CARLA 三维视图不可用\n{message}")
        self._status.show()
        self.health_changed.emit("UNAVAILABLE", message)


def _wrap_foreign_window(window_id: int) -> QWindow | None:
    return QWindow.fromWinId(window_id)


def _create_window_container(window: QWindow, parent: QWidget) -> QWidget | None:
    return QWidget.createWindowContainer(window, parent)
