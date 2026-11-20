"""Response validation and cancellation without issuing HTTP requests."""

from typing import cast

from PySide6.QtCore import QByteArray
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication
from ui.api_client.map_selection import MapSelectionClient
from ui.models.map_selection import SelectionConfig


class _Reply:
    def __init__(self, body: bytes, *, failed: bool = False) -> None:
        self.body = body
        self.failed = failed
        self.deleted = False
        self.aborted = False

    def error(self) -> QNetworkReply.NetworkError:
        return (
            QNetworkReply.NetworkError.ServiceUnavailableError
            if self.failed
            else QNetworkReply.NetworkError.NoError
        )

    def errorString(self) -> str:  # noqa: N802
        return "Unavailable"

    def readAll(self) -> QByteArray:  # noqa: N802
        return QByteArray(self.body)

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted = True

    def abort(self) -> None:
        self.aborted = True


def _client() -> MapSelectionClient:
    existing = QApplication.instance()
    if not isinstance(existing, QApplication):
        QApplication([])
    return MapSelectionClient()


def test_config_and_error_response_retain_actionable_reason() -> None:
    client = _client()
    configs: list[SelectionConfig] = []
    errors: list[tuple[str, str]] = []
    client.config_ready.connect(configs.append)
    client.failed.connect(lambda op, msg: errors.append((op, msg)))
    reply = _Reply(b'{"max_diagonal_m":6000}')
    client._pending["config"] = cast(QNetworkReply, reply)
    client._finish("config", cast(QNetworkReply, reply))
    assert configs == [SelectionConfig(max_diagonal_m=6000)]
    failed = _Reply(b'{"detail":"Configure the Amap Web API key and retry."}', failed=True)
    client._pending["search"] = cast(QNetworkReply, failed)
    client._finish("search", cast(QNetworkReply, failed))
    assert errors == [("search", "Configure the Amap Web API key and retry.")]
    assert reply.deleted and failed.deleted


def test_stale_reply_and_close_cannot_overwrite_selection() -> None:
    client = _client()
    configs: list[SelectionConfig] = []
    client.config_ready.connect(configs.append)
    stale = _Reply(b'{"max_diagonal_m":3000}')
    current = _Reply(b'{"max_diagonal_m":6000}')
    client._pending["config"] = cast(QNetworkReply, current)
    client._finish("config", cast(QNetworkReply, stale))
    assert configs == [] and stale.deleted
    client.close()
    assert current.aborted
    client._finish("config", cast(QNetworkReply, current))
    assert configs == [] and current.deleted
    assert client._pending == {}


def test_invalid_configuration_and_unbounded_response_are_rejected() -> None:
    client = _client()
    errors: list[str] = []
    client.failed.connect(lambda _op, msg: errors.append(msg))
    for body in (b'{"max_diagonal_m":-1}', b"x" * 128_001):
        reply = _Reply(body)
        client._pending["config"] = cast(QNetworkReply, reply)
        client._finish("config", cast(QNetworkReply, reply))
    assert len(errors) == 2
    assert "Unexpected response" in errors[0]
    assert "too large" in errors[1]
