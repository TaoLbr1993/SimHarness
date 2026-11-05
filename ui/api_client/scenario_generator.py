"""Asynchronous draft, image and explicit publication client; never starts a run."""

from __future__ import annotations

import json
import math
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ui.models.protocol import GeneratedAutomationShare
from ui.models.scenario_revision import (
    DemandMode,
    RequestedAutomationShare,
    ScenarioRevisionRequest,
)
from ui.scene_language import Language, normalize_language, text

_TASK_PATTERN = r"^\d{8}_\d{6}_[0-9a-f]{6}$"


class PreviewPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    traffic_state: Literal["free_flow", "moderate", "congested"]
    duration_s: int = Field(ge=60, le=3600)
    warmup_s: int = Field(ge=0, le=3500)
    seed: int = Field(ge=0)
    desired_density_veh_per_km_lane: float = Field(gt=0, le=120, allow_inf_nan=False)
    demand_mode: DemandMode | None = None
    reference_speed_kmh: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    inflow_veh_per_hour_per_lane: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    requested_total_flow_veh_per_hour: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    requested_vehicle_count: int | None = Field(default=None, ge=1, le=20000)
    automation_mix: list[RequestedAutomationShare] = Field(default_factory=list, max_length=6)


class PreviewDemand(BaseModel):
    model_config = ConfigDict(extra="allow")
    planned_vehicles: int = Field(ge=1, le=20000)
    automation_mix: list[GeneratedAutomationShare] = Field(default_factory=list)


class GenerationResponse(BaseModel):
    """The authored draft contains settings, never measured preview values."""

    model_config = ConfigDict(extra="allow")
    status: str
    task_id: str = Field(pattern=_TASK_PATTERN)
    description: str
    catalog_map_id: str | None = None
    parent_task_id: str | None = Field(default=None, pattern=_TASK_PATTERN)
    plan: PreviewPlan | None = None
    demand: PreviewDemand | None = None
    artifact_urls: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class PublicationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    map_id: str = Field(pattern=r"^nl2-\d{8}_\d{6}_[0-9a-f]{6}$")


class ScenarioGeneratorClient(QObject):
    """Issue one mutation at a time without blocking Qt."""

    draft_ready = Signal(object)
    preview_ready = Signal(str, bytes)
    preview_failed = Signal(str, str)
    succeeded = Signal(str)
    failed = Signal(str)
    publication_failed = Signal(str)
    revision_failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        base_url: str | None = None,
        language: Language = "en",
    ) -> None:
        super().__init__(parent)
        self._language = normalize_language(language)
        self._base_url = (
            base_url or os.getenv("NL2MAP_API_URL") or "http://127.0.0.1:8001"
        ).rstrip("/")
        self._network = QNetworkAccessManager(self)
        self._pending: QNetworkReply | None = None
        self._preview_pending: QNetworkReply | None = None
        self._draft: GenerationResponse | None = None

    def _text(self, zh: str, en: str) -> str:
        return text(self._language, zh, en)

    def set_language(self, language: str) -> None:
        """Reload only the localized image; never mutate or regenerate a scene."""
        selected = normalize_language(language)
        if selected == self._language:
            return
        self._language = selected
        if self._draft is not None:
            self.load_preview(self._draft.task_id)

    def generate(self, description: str, *, simulation_mode: str = "traffic") -> None:
        if self._pending is not None:
            return
        self._draft = None
        if self._preview_pending is not None:
            self._preview_pending.abort()
        payload = {
            "description": description,
            "output_kind": "scenario",
            "run_simulation": False,
            "publish_to_catalog": False,
            "simulation_mode": simulation_mode,
            "ui_language": self._language,
        }
        reply = self._post("/generate", payload, timeout=600_000)
        reply.finished.connect(lambda: self._finish(reply))

    def publish(self, task_id: str) -> None:
        """Called only by the explicit draft confirmation action."""
        if self._pending is not None:
            return
        if self._draft is None or task_id != self._draft.task_id:
            self.publication_failed.emit(
                self._text(
                    "当前草稿已变化，请重新预览后添加。",
                    "The draft has changed. Review the current preview before adding it.",
                )
            )
            return
        reply = self._post(f"/scenarios/{task_id}/publish", {}, timeout=60_000)
        reply.finished.connect(lambda: self._finish_publish(reply, task_id))

    def revise(
        self, task_id: str, revision: ScenarioRevisionRequest | dict[str, object] | int
    ) -> None:
        """Derive a new preview from the current map, preserving the source draft."""
        if self._pending is not None:
            return
        if self._draft is None or task_id != self._draft.task_id:
            self.revision_failed.emit(
                self._text(
                    "当前草稿已变化，请先查看当前预览再修改。",
                    "The draft has changed. Review the current preview before editing it.",
                )
            )
            return
        try:
            if isinstance(revision, int):
                revision = {"vehicle_count": revision}
            if isinstance(revision, ScenarioRevisionRequest):
                revision = revision.model_dump(exclude_none=True)
            edits = ScenarioRevisionRequest.model_validate(revision)
        except ValidationError as exc:
            reason = exc.errors(include_input=False, include_url=False)[0]["msg"]
            self.revision_failed.emit(
                self._text("场景设定无效：", "Invalid scene settings: ") + reason
            )
            return
        reply = self._post(
            f"/scenarios/{task_id}/revise", edits.model_dump(exclude_none=True), timeout=60_000
        )
        reply.finished.connect(
            lambda: self._finish(reply, revision_of=task_id, expected_revision=edits)
        )

    def _post(self, path: str, payload: dict[str, object], *, timeout: int) -> QNetworkReply:
        request = QNetworkRequest(QUrl(self._base_url + path))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(timeout)
        reply = self._network.post(request, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self._pending = reply
        return reply

    def _finish(
        self,
        reply: QNetworkReply,
        *,
        revision_of: str | None = None,
        expected_count: int | None = None,
        expected_revision: ScenarioRevisionRequest | None = None,
    ) -> None:
        self._pending = None
        failure = self.revision_failed if revision_of is not None else self.failed
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                action = (
                    self._text("更新场景设定失败：", "Could not update scene settings: ")
                    if revision_of is not None
                    else self._text("场景生成失败：", "Could not generate the scene: ")
                )
                failure.emit(action + self._error(reply))
                return
            payload = json.loads(bytes(reply.readAll().data()))
            if isinstance(payload, dict) and payload.get("status") != "generated":
                reason = payload.get("error")
                failure.emit(
                    reason
                    if isinstance(reason, str)
                    else self._text(
                        "未能生成场景，请补充需求后重试。",
                        "Could not generate the scene. Add more details and try again.",
                    )
                )
                return
            result = GenerationResponse.model_validate(payload)
            if result.status != "generated" or result.plan is None or result.demand is None:
                failure.emit(
                    result.error
                    or self._text(
                        "未能生成完整场景，请补充需求后重试。",
                        "The scene is incomplete. Add more details and try again.",
                    )
                )
                return
            if result.catalog_map_id is not None:
                failure.emit(
                    self._text(
                        "场景服务版本不兼容：预览时已自动入库，请升级服务后重试。",
                        "Incompatible service: the preview was published automatically. "
                        "Update the service.",
                    )
                )
                return
            if revision_of is not None and (
                result.parent_task_id != revision_of
                or result.task_id == revision_of
                or (expected_count is not None and result.demand.planned_vehicles != expected_count)
                or (
                    expected_revision is not None
                    and not self._revision_matches(result, expected_revision)
                )
            ):
                failure.emit(
                    self._text(
                        "修改结果与请求不一致，未应用到预览。请重试。",
                        "The returned settings do not match your changes. "
                        "Nothing was applied; please retry.",
                    )
                )
                return
            self._draft = result
            self.draft_ready.emit(result.model_dump(mode="json"))
            self.load_preview(result.task_id)
        except ValueError:
            failure.emit(
                self._text(
                    "场景服务返回格式不正确，请检查服务版本后重试。",
                    "Invalid response from the scene service. Check its version and try again.",
                )
            )
        finally:
            reply.deleteLater()

    @staticmethod
    def _revision_matches(result: GenerationResponse, edits: ScenarioRevisionRequest) -> bool:
        """Reject partial or stale server updates instead of showing unapplied settings."""
        if result.plan is None or result.demand is None:
            return False
        plan = result.plan.model_dump()
        for name, expected in edits.model_dump(exclude_none=True).items():
            actual: object
            if name == "vehicle_count":
                actual = result.demand.planned_vehicles
            elif name == "total_flow_veh_per_hour":
                actual = plan.get("requested_total_flow_veh_per_hour")
            elif name == "automation_mix":
                requested = {item["level"]: item["share_percent"] for item in expected}
                received = {item["level"]: item["share_percent"] for item in plan.get(name, [])}
                if any(
                    not math.isclose(requested.get(level, 0), received.get(level, 0), abs_tol=1e-6)
                    for level in requested.keys() | received.keys()
                ):
                    return False
                continue
            else:
                actual = plan.get(name)
            if isinstance(expected, int | float):
                if not isinstance(actual, int | float) or not math.isclose(
                    actual, expected, rel_tol=1e-9, abs_tol=1e-6
                ):
                    return False
            elif actual != expected:
                return False
        return True

    def load_preview(self, task_id: str) -> None:
        draft = self._draft
        if draft is None or draft.task_id != task_id:
            return
        localized_name = f"map_preview.{self._language}.png"
        name = (
            localized_name
            if f"output/{localized_name}" in draft.artifact_urls
            else "map_preview.png"
        )
        path = draft.artifact_urls.get(f"output/{name}", "")
        if not re.fullmatch(_TASK_PATTERN, task_id) or path != f"/artifacts/{task_id}/{name}":
            self.preview_failed.emit(
                task_id,
                self._text(
                    "草稿没有有效地图预览，请重新生成。",
                    "This draft has no valid map preview. Please generate it again.",
                ),
            )
            return
        if self._preview_pending is not None:
            previous, self._preview_pending = self._preview_pending, None
            previous.abort()
        request = QNetworkRequest(QUrl(self._base_url + path))
        request.setTransferTimeout(30_000)
        reply = self._network.get(request)
        self._preview_pending = reply
        reply.finished.connect(lambda: self._finish_preview(reply, task_id))

    def _finish_preview(self, reply: QNetworkReply, task_id: str) -> None:
        try:
            if reply is not self._preview_pending:
                return
            self._preview_pending = None
            if self._draft is None or self._draft.task_id != task_id:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.preview_failed.emit(
                    task_id,
                    self._text(
                        "地图预览加载失败，可重试：", "Could not load the map preview. Retry: "
                    )
                    + self._error(reply),
                )
                return
            data = bytes(reply.readAll().data())
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 16 * 1024 * 1024:
                self.preview_failed.emit(
                    task_id,
                    self._text(
                        "地图图片格式或大小异常，请重新生成。",
                        "Invalid map image format or size. Please generate it again.",
                    ),
                )
                return
            self.preview_ready.emit(task_id, data)
        finally:
            reply.deleteLater()

    def _finish_publish(self, reply: QNetworkReply, task_id: str) -> None:
        self._pending = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.publication_failed.emit(
                    self._text("添加场景失败：", "Could not add the scene: ")
                    + self._error(reply)
                    + self._text(
                        "。可重试，重复添加不会产生副本。", ". Retrying will not create duplicates."
                    )
                )
                return
            result = PublicationResponse.model_validate_json(bytes(reply.readAll().data()))
            if result.map_id != "nl2-" + task_id:
                raise ValueError("Published task does not match the confirmed draft")
            self.succeeded.emit(result.map_id)
        except ValueError:
            self.publication_failed.emit(
                self._text(
                    "添加结果格式不正确，请刷新场景列表或重试。",
                    "Invalid publication response. Refresh the scene list or try again.",
                )
            )
        finally:
            reply.deleteLater()

    @staticmethod
    def _error(reply: QNetworkReply) -> str:
        """Expose the backend's concrete cause when available, without dumping logs."""
        try:
            payload = json.loads(bytes(reply.readAll().data()))
            detail = payload.get("detail") or payload.get("error")
            if isinstance(detail, str):
                return detail[:1200]
        except (ValueError, TypeError, AttributeError):
            pass
        return reply.errorString()
