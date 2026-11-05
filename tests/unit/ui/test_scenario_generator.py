import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication
from ui.api_client.scenario_generator import ScenarioGeneratorClient


def _draft() -> dict[str, Any]:
    return {
        "status": "generated",
        "task_id": "20261104_100000_aabbcc",
        "description": "测试拥堵车速",
        "catalog_map_id": None,
        "plan": {
            "traffic_state": "congested",
            "duration_s": 600,
            "warmup_s": 120,
            "seed": 42,
            "desired_density_veh_per_km_lane": 65,
        },
        "demand": {"planned_vehicles": 1300},
        "artifact_urls": {
            "output/map_preview.png": "/artifacts/20261104_100000_aabbcc/map_preview.png"
        },
    }


def test_client_previews_without_publication_and_confirms_exact_draft() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient(base_url="http://127.0.0.1:8001")
        ready: list[str] = []
        drafts: list[object] = []
        failures: list[str] = []
        client.succeeded.connect(ready.append)
        client.draft_ready.connect(drafts.append)
        client.failed.connect(failures.append)
        client.generate("测试拥堵车速")
        request, body = manager.return_value.post.call_args.args
        assert request.url().toString() == "http://127.0.0.1:8001/generate"
        assert json.loads(body) == {
            "description": "测试拥堵车速",
            "output_kind": "scenario",
            "run_simulation": False,
            "publish_to_catalog": False,
            "simulation_mode": "traffic",
            "ui_language": "en",
        }
        reply.readAll.return_value = QByteArray(json.dumps(_draft()).encode())
        client._finish(reply)
        assert not ready and not failures and len(drafts) == 1
        assert manager.return_value.post.call_count == 1
        image_request = manager.return_value.get.call_args.args[0]
        assert image_request.url().path() == "/artifacts/20261104_100000_aabbcc/map_preview.png"
        client.publish("20261104_100000_aabbcc")
        assert (
            manager.return_value.post.call_args.args[0]
            .url()
            .path()
            .endswith("/scenarios/20261104_100000_aabbcc/publish")
        )
        reply.readAll.return_value = QByteArray(b'{"map_id":"nl2-20261104_100000_aabbcc"}')
        client._finish_publish(reply, "20261104_100000_aabbcc")
        assert ready == ["nl2-20261104_100000_aabbcc"]
        assert manager.return_value.post.call_count == 2


def test_client_does_not_fetch_an_external_or_stale_preview() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        failures: list[tuple[str, str]] = []
        client.preview_failed.connect(lambda task, reason: failures.append((task, reason)))
        payload = _draft()
        payload["artifact_urls"]["output/map_preview.png"] = "https://example.com/private.png"
        client.generate("测试拥堵车速")
        reply.readAll.return_value = QByteArray(json.dumps(payload).encode())
        client._finish(reply)
        assert failures and not manager.return_value.get.called
        client.publish("20261104_100001_ffffff")
        assert manager.return_value.post.call_count == 1


def test_language_switch_fetches_only_local_image_and_preserves_inflight_revision() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        response = MagicMock(spec=QNetworkReply)
        response.error.return_value = QNetworkReply.NetworkError.NoError
        english, chinese = MagicMock(spec=QNetworkReply), MagicMock(spec=QNetworkReply)
        manager.return_value.post.return_value = response
        manager.return_value.get.side_effect = [english, chinese]
        client = ScenarioGeneratorClient()
        payload = _draft()
        task_id = payload["task_id"]
        for language in ("en", "zh"):
            name = f"map_preview.{language}.png"
            payload["artifact_urls"][f"output/{name}"] = f"/artifacts/{task_id}/{name}"
        client.generate("保留输入原文")
        response.readAll.return_value = QByteArray(json.dumps(payload).encode())
        client._finish(response)
        assert (
            manager.return_value.get.call_args.args[0].url().path().endswith("map_preview.en.png")
        )
        original = client._draft
        client.revise(task_id, 500)
        mutation = client._pending
        client.set_language("zh")
        assert client._pending is mutation and client._draft is original
        assert manager.return_value.post.call_count == 2
        assert (
            manager.return_value.get.call_args.args[0].url().path().endswith("map_preview.zh.png")
        )
        english.abort.assert_called_once()
        received: list[bytes] = []
        client.preview_ready.connect(lambda _, data: received.append(data))
        client._finish_preview(english, task_id)
        assert not received and client._preview_pending is chinese
        chinese.error.return_value = QNetworkReply.NetworkError.NoError
        chinese.readAll.return_value = QByteArray(b"\x89PNG\r\n\x1a\nlocalized")
        client._finish_preview(chinese, task_id)
        assert received == [b"\x89PNG\r\n\x1a\nlocalized"]


def test_localized_preview_cannot_escape_the_current_task() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        failures: list[tuple[str, str]] = []
        client.preview_failed.connect(lambda task, reason: failures.append((task, reason)))
        payload = _draft()
        payload["artifact_urls"]["output/map_preview.en.png"] = "https://example.com/preview.png"
        client.generate("A signalized intersection")
        reply.readAll.return_value = QByteArray(json.dumps(payload).encode())
        client._finish(reply)
        assert failures and "no valid map preview" in failures[0][1]
        assert not manager.return_value.get.called


def test_publication_failure_preserves_backend_reason_and_allows_retry() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        failures: list[str] = []
        client.publication_failed.connect(failures.append)
        client.generate("测试拥堵车速")
        reply.readAll.return_value = QByteArray(json.dumps(_draft()).encode())
        client._finish(reply)
        client.publish("20261104_100000_aabbcc")
        reply.error.return_value = QNetworkReply.NetworkError.ServiceUnavailableError
        reply.readAll.return_value = QByteArray(json.dumps({"detail": "目录无法写入"}).encode())
        client._finish_publish(reply, "20261104_100000_aabbcc")
        assert "目录无法写入" in failures[0]
        client.publish("20261104_100000_aabbcc")
        assert manager.return_value.post.call_count == 3


def test_pre_task_generation_failure_displays_the_actual_reason() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        failures: list[str] = []
        client.failed.connect(failures.append)
        client.generate("L0占80%，L3占80%")
        reply.readAll.return_value = QByteArray(
            json.dumps({"status": "agent_error", "error": "L0–L5 车辆比例之和必须为 100%"}).encode()
        )
        client._finish(reply)
        assert failures == ["L0–L5 车辆比例之和必须为 100%"]
        assert not manager.return_value.get.called


def test_revision_preserves_source_on_failure_and_accepts_only_matching_new_draft() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        drafts: list[object] = []
        failures: list[str] = []
        client.draft_ready.connect(drafts.append)
        client.revision_failed.connect(failures.append)
        client.generate("测试拥堵车速")
        reply.readAll.return_value = QByteArray(json.dumps(_draft()).encode())
        client._finish(reply)
        source_id = _draft()["task_id"]
        client.revise(source_id, 500)
        request, body = manager.return_value.post.call_args.args
        assert request.url().path() == f"/scenarios/{source_id}/revise"
        assert json.loads(body) == {"vehicle_count": 500}
        reply.error.return_value = QNetworkReply.NetworkError.ServiceUnavailableError
        reply.readAll.return_value = QByteArray(
            json.dumps({"detail": "修改失败：空间不足"}).encode()
        )
        client._finish(reply, revision_of=source_id, expected_count=500)
        assert "空间不足" in failures[-1] and len(drafts) == 1
        assert client._draft is not None and client._draft.task_id == source_id
        client.revise(source_id, 500)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        changed = _draft()
        changed["task_id"] = "20261104_100002_aaddcc"
        changed["parent_task_id"] = source_id
        changed["demand"]["planned_vehicles"] = 499
        reply.readAll.return_value = QByteArray(json.dumps(changed).encode())
        client._finish(reply, revision_of=source_id, expected_count=500)
        assert "do not match" in failures[-1] and len(drafts) == 1
        client.revise(source_id, 500)
        changed["demand"]["planned_vehicles"] = 500
        changed["artifact_urls"]["output/map_preview.png"] = (
            f"/artifacts/{changed['task_id']}/map_preview.png"
        )
        reply.readAll.return_value = QByteArray(json.dumps(changed).encode())
        client._finish(reply, revision_of=source_id, expected_count=500)
        assert len(drafts) == 2
        assert client._draft.task_id == changed["task_id"]
        assert manager.return_value.get.call_args.args[0].url().path() == (
            f"/artifacts/{changed['task_id']}/map_preview.png"
        )


def test_revision_checks_all_requested_settings_before_replacing_preview() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        drafts: list[object] = []
        failures: list[str] = []
        client.draft_ready.connect(drafts.append)
        client.revision_failed.connect(failures.append)
        client.generate("测试拥堵车速")
        reply.readAll.return_value = QByteArray(json.dumps(_draft()).encode())
        client._finish(reply)
        source_id = _draft()["task_id"]
        edits: dict[str, object] = {"duration_s": 900, "warmup_s": 60, "seed": 7}
        client.revise(source_id, edits)
        assert json.loads(manager.return_value.post.call_args.args[1]) == edits
        callback = reply.finished.connect.call_args.args[0]
        revised = _draft()
        revised.update(task_id="20261105_100000_aabbcc", parent_task_id=source_id)
        revised["plan"].update(duration_s=900, warmup_s=60, seed=42)
        reply.readAll.return_value = QByteArray(json.dumps(revised).encode())
        callback()
        assert "do not match" in failures[-1] and len(drafts) == 1
        assert client._draft is not None and client._draft.task_id == source_id
        client.revise(source_id, edits)
        callback = reply.finished.connect.call_args.args[0]
        revised["plan"]["seed"] = 7
        reply.readAll.return_value = QByteArray(json.dumps(revised).encode())
        callback()
        assert len(drafts) == 2 and client._draft.task_id == revised["task_id"]


@pytest.mark.parametrize(
    "edits",
    [
        {},
        {"duration_s": None},
        {"seed": True},
        {"desired_density_veh_per_km_lane": float("nan")},
        {"duration_s": 60, "warmup_s": 60},
        {"vehicle_count": 500, "inflow_veh_per_hour_per_lane": 200},
        {"demand_mode": "count", "inflow_veh_per_hour_per_lane": 200},
        {"vehicle_count": 500, "desired_density_veh_per_km_lane": 30},
        {"automation_mix": [{"level": "L0", "share_percent": 30}]},
    ],
)
def test_invalid_parameter_edit_does_not_send_a_request(edits: dict[str, object]) -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("ui.api_client.scenario_generator.QNetworkAccessManager") as manager:
        reply = MagicMock(spec=QNetworkReply)
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        manager.return_value.post.return_value = reply
        client = ScenarioGeneratorClient()
        failures: list[str] = []
        client.revision_failed.connect(failures.append)
        client.generate("测试拥堵车速")
        reply.readAll.return_value = QByteArray(json.dumps(_draft()).encode())
        client._finish(reply)
        client.revise(_draft()["task_id"], edits)
        assert failures and manager.return_value.post.call_count == 1
