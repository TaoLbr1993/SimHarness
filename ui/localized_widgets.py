"""Qt presentation adapters: translate captions, never editable values or item data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6 import QtWidgets as Qt

from ui.localization import tr


class _Presentation:
    # Qt constructors and addItem/addRow have heterogeneous native overloads.
    # Any is confined to this binding boundary; domain models never pass through it.
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if args and isinstance(args[0], str) and not isinstance(self, Qt.QLineEdit):
            args = (tr(args[0]), *args[1:])
        super().__init__(*args, **kwargs)

    def _display(self, text: str) -> str:
        return text if self.property("literalText") else tr(text)  # type: ignore[attr-defined]

    def setText(self, text: str) -> None:
        super().setText(self._display(text))  # type: ignore[misc]

    def setToolTip(self, text: str) -> None:
        super().setToolTip(self._display(text))  # type: ignore[misc]

    def setAccessibleName(self, text: str) -> None:
        super().setAccessibleName(self._display(text))  # type: ignore[misc]

    def setWindowTitle(self, text: str) -> None:
        super().setWindowTitle(tr(text))  # type: ignore[misc]


class QLabel(_Presentation, Qt.QLabel):
    pass


class QPushButton(_Presentation, Qt.QPushButton):
    pass


class QCheckBox(_Presentation, Qt.QCheckBox):
    pass


class QToolButton(_Presentation, Qt.QToolButton):
    pass


class QMainWindow(_Presentation, Qt.QMainWindow):
    pass


class QDialog(_Presentation, Qt.QDialog):
    pass


class QLineEdit(Qt.QLineEdit):
    def setPlaceholderText(self, text: str) -> None:
        super().setPlaceholderText(tr(text))

    def setToolTip(self, text: str) -> None:
        super().setToolTip(tr(text))

    def setAccessibleName(self, text: str) -> None:
        super().setAccessibleName(tr(text))


class QComboBox(_Presentation, Qt.QComboBox):
    def addItem(self, *args: Any, **kwargs: Any) -> None:
        values = list(args)
        index = 0 if values and isinstance(values[0], str) else 1
        if len(values) > index:
            values[index] = values[index] if self.property("literalItems") else tr(values[index])
        super().addItem(*values, **kwargs)

    def addItems(self, texts: Sequence[str]) -> None:
        for text in texts:
            self.addItem(text)


class QFormLayout(Qt.QFormLayout):
    def addRow(self, *args: Any) -> None:
        if len(args) == 2 and isinstance(args[0], str):
            args = (tr(args[0]), args[1])
        super().addRow(*args)


class QTabWidget(Qt.QTabWidget):
    def addTab(self, *args: Any) -> int:
        values = list(args)
        if values and isinstance(values[-1], str) and not self.property("literalItems"):
            values[-1] = tr(values[-1])
        return super().addTab(*values)

    def setTabText(self, index: int, text: str) -> None:
        super().setTabText(index, text if self.property("literalItems") else tr(text))


class QTableWidget(Qt.QTableWidget):
    def setHorizontalHeaderLabels(self, labels: Sequence[str]) -> None:
        super().setHorizontalHeaderLabels([tr(label) for label in labels])


class QSpinBox(Qt.QSpinBox):
    def setSuffix(self, suffix: str) -> None:
        super().setSuffix(tr(suffix))


class QDoubleSpinBox(Qt.QDoubleSpinBox):
    def setSuffix(self, suffix: str) -> None:
        super().setSuffix(tr(suffix))
