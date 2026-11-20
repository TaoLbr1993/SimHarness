"""Bounded asynchronous lookup for TrafficForge's geographic selection dialog."""

import json
import os

from pydantic import ValidationError
from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ui.models.map_selection import MapSearchResponse, SelectionConfig


class MapSelectionClient(QObject):
    config_ready = Signal(object)
    places_ready = Signal(object)
    failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None, *, base_url: str | None = None) -> None:
        super().__init__(parent)
        self._base_url = (
            base_url
            or os.getenv("TRAFFICFORGE_API_URL", "").strip()
            or os.getenv("NL2MAP_API_URL", "").strip()
            or "http://127.0.0.1:8001"
        ).rstrip("/")
        self._network = QNetworkAccessManager(self)
        self._pending: dict[str, QNetworkReply] = {}

    def load_config(self) -> None:
        self._get("config", "/map/selection-config")

    def search(self, query: str) -> None:
        self._get("search", "/map/places", query=query.strip())

    def close(self) -> None:
        """Abort outstanding operations; stale responses cannot update the dialog."""
        pending = tuple(self._pending.values())
        self._pending.clear()
        for reply in pending:
            reply.abort()

    def _get(self, operation: str, path: str, *, query: str | None = None) -> None:
        previous = self._pending.pop(operation, None)
        if previous is not None:
            previous.abort()
        url = QUrl(self._base_url + path)
        if query is not None:
            parameters = QUrlQuery()
            parameters.addQueryItem("q", query)
            url.setQuery(parameters)
        request = QNetworkRequest(url)
        request.setTransferTimeout(90_000 if operation == "search" else 15_000)
        reply = self._network.get(request)
        self._pending[operation] = reply
        reply.finished.connect(lambda: self._finish(operation, reply))

    def _finish(self, operation: str, reply: QNetworkReply) -> None:
        try:
            if self._pending.get(operation) is not reply:
                return
            self._pending.pop(operation)
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed.emit(operation, self._error(reply))
                return
            raw = bytes(reply.readAll().data())
            if len(raw) > 128_000:
                self.failed.emit(operation, "Response is too large.")
                return
            if operation == "config":
                self.config_ready.emit(SelectionConfig.model_validate_json(raw))
            else:
                self.places_ready.emit(MapSearchResponse.model_validate_json(raw).candidates)
        except ValidationError:
            self.failed.emit(operation, "Unexpected response. Update the TrafficForge service.")
        finally:
            reply.deleteLater()

    @staticmethod
    def _error(reply: QNetworkReply) -> str:
        """Retain the backend's actionable diagnosis, displayed as plain text by the dialog."""
        raw = bytes(reply.readAll().data())
        if len(raw) <= 128_000:
            try:
                response = json.loads(raw)
                if isinstance(response, dict):
                    detail = response.get("detail")
                    if isinstance(detail, str) and detail.strip():
                        return detail[:2000]
            except (ValueError, UnicodeDecodeError):
                pass
        return reply.errorString()
