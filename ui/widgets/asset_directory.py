"""Reusable searchable directory tree for UI asset catalogs."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.localization import tr
from ui.localized_widgets import (
    QLabel,
    QLineEdit,
)
from ui.models.assets import AssetDirectoryEntry

_ASSET_ID_ROLE = int(Qt.ItemDataRole.UserRole)


class AssetDirectoryWidget(QFrame):
    """Render asset packages as searchable directories without owning API state."""

    asset_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("assetDirectory")
        self._assets: tuple[AssetDirectoryEntry, ...] = ()
        self._last_selected_asset_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        heading = QHBoxLayout()
        title = QLabel("地图资产目录")
        title.setObjectName("panelTitle")
        self.count_label = QLabel("0 个地图包")
        self.count_label.setObjectName("caption")
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.count_label)
        root.addLayout(heading)

        self.search = QLineEdit()
        self.search.setObjectName("assetSearchInput")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索名称、ID、格式或文件")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setObjectName("assetDirectoryTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels((tr("目录 / 文件"), tr("格式 / 兼容")))
        self.tree.setColumnWidth(0, 210)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._emit_selection)
        root.addWidget(self.tree, 1)

        hint = QLabel("地图包按 manifest 文件清单展示；文件名保留完整后缀。")
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        root.addWidget(hint)

    def set_assets(self, assets: Sequence[AssetDirectoryEntry]) -> None:
        self._assets = tuple(assets)
        self.tree.clear()
        for asset in self._assets:
            status = "已验证" if asset.validated else "待验证"
            platforms = " · ".join(asset.compatibility) or tr(status)
            directory = QTreeWidgetItem((f"{asset.name}/", platforms))
            directory.setData(0, _ASSET_ID_ROLE, asset.asset_id)
            directory.setToolTip(0, asset.asset_id)
            for file in asset.files:
                compatibility = " · ".join(file.compatibility) or file.format_suffix
                child = QTreeWidgetItem((file.name, compatibility))
                child.setData(0, _ASSET_ID_ROLE, asset.asset_id)
                if file.checksum is not None:
                    child.setToolTip(0, file.checksum)
                directory.addChild(child)
            self.tree.addTopLevelItem(directory)
            directory.setExpanded(True)
        self.count_label.setText(f"{len(self._assets)} 个地图包")
        self._filter(self.search.text())
        if self.tree.topLevelItemCount() > 0:
            first = self.tree.topLevelItem(0)
            if first is not None:
                self.tree.setCurrentItem(first)

    def select_asset(self, asset_id: str) -> None:
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is None:
                continue
            if item.data(0, _ASSET_ID_ROLE) == asset_id:
                self.tree.setCurrentItem(item)
                return

    @Slot(str)
    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for index, asset in enumerate(self._assets):
            directory = self.tree.topLevelItem(index)
            if directory is None:
                continue
            asset_text = " ".join((asset.name, asset.asset_id, *asset.compatibility)).casefold()
            asset_matches = not query or query in asset_text
            child_matches = False
            for child_index, file in enumerate(asset.files):
                child = directory.child(child_index)
                file_text = " ".join(
                    (file.name, file.format_suffix, *file.compatibility)
                ).casefold()
                matches = asset_matches or query in file_text
                child.setHidden(not matches)
                child_matches = child_matches or matches
            directory.setHidden(not (asset_matches or child_matches))
            if query and child_matches:
                directory.setExpanded(True)

    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def _emit_selection(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        asset_id = current.data(0, _ASSET_ID_ROLE)
        if not isinstance(asset_id, str) or asset_id == self._last_selected_asset_id:
            return
        self._last_selected_asset_id = asset_id
        self.asset_selected.emit(asset_id)
