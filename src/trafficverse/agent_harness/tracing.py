"""Thread-safe decision snapshots, with disk history and bounded display text/cache."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models.observation import LocalObservationFrame, VehicleObservation

from .config import Binding
from .context import AgentInput, model_state
from .trace_models import (
    DecisionInputPreview,
    DecisionPage,
    DecisionRecord,
    DecisionSummary,
    MemoryInputPreview,
    NearbyInputPreview,
    SkillInputPreview,
    VehicleInputPreview,
)

logger = logging.getLogger(__name__)
_TEXT_LIMIT = 128_000
_CACHE_LIMIT = 64
_active: ContextVar[DecisionCapture | None] = ContextVar("agent_decision_capture", default=None)


class DecisionHistory:
    """Store each group/round separately; retained files are read only on demand."""

    def __init__(self, session_id: UUID, directory: Path | None = None) -> None:
        self.session_id = session_id
        self._directory = directory
        self._records: OrderedDict[int, DecisionRecord] = OrderedDict()
        self._total = 0
        self._lock = threading.RLock()
        self._storage_warning = False

    def _save(self, record: DecisionRecord) -> None:
        self._records[record.record_id] = record
        self._records.move_to_end(record.record_id)
        while len(self._records) > _CACHE_LIMIT:
            self._records.popitem(last=False)
        if self._directory is None:
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"{record.record_id:08d}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(record.model_dump_json(), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            self._storage_warning = True
            logger.warning("agent.trace_write_failed")

    def get(self, record_id: int) -> DecisionRecord:
        with self._lock:
            record = self._records.get(record_id)
            if record is not None:
                return record
            if record_id < 1 or record_id > self._total or self._directory is None:
                raise KeyError(record_id)
            try:
                return DecisionRecord.model_validate_json(
                    (self._directory / f"{record_id:08d}.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as error:
                raise KeyError(record_id) from error

    def update(self, record_id: int, **changes: object) -> None:
        with self._lock:
            self._save(self.get(record_id).model_copy(update=changes))

    def append_text(self, record_id: int, field: str, value: str, secret: str = "") -> None:
        if secret:
            value = value.replace(secret, "[REDACTED]")
        with self._lock:
            record = self.get(record_id)
            previous = str(getattr(record, field))
            combined = previous + ("\n\n" if previous else "") + value
            self.update(
                record_id,
                **{field: combined[:_TEXT_LIMIT]},
                truncated=record.truncated or len(combined) > _TEXT_LIMIT,
            )

    def begin(
        self,
        binding: Binding,
        frame: LocalObservationFrame,
        observations: tuple[VehicleObservation, ...],
    ) -> DecisionCapture:
        with self._lock:
            self._total += 1
            record = DecisionRecord(
                record_id=self._total,
                sequence=frame.sequence,
                simulation_time_ms=frame.simulation_time_ms,
                group_name=binding.name,
                model_id=binding.model.model_id if binding.model else binding.wrapper,
                started_at=datetime.now(timezone.utc),
                vehicle_count=len(observations),
            )
            self._save(record)
            capture = DecisionCapture(self, record.record_id)
            capture.inputs(tuple(AgentInput(observation=o) for o in observations))
            return capture

    def page(self, before_id: int | None = None, limit: int = 20) -> DecisionPage:
        with self._lock:
            upper = min(before_id or self._total + 1, self._total + 1)
            lower = max(1, upper - limit)
            items = []
            for record_id in range(upper - 1, lower - 1, -1):
                try:
                    record = self.get(record_id)
                except KeyError:
                    continue
                items.append(DecisionSummary.model_validate(record.model_dump()))
            return DecisionPage(
                session_id=self.session_id,
                total=self._total,
                items=tuple(items),
                next_before_id=lower if lower > 1 else None,
                storage_warning=self._storage_warning,
            )

    def settle(self, sequence: int, outcome: str) -> None:
        with self._lock:
            for record in tuple(self._records.values()):
                if record.sequence == sequence and record.outcome == "pending":
                    self.update(record.record_id, outcome=outcome)


class DecisionCapture:
    def __init__(self, history: DecisionHistory, record_id: int) -> None:
        self.history = history
        self.record_id = record_id
        self._started = time.monotonic()

    def inputs(self, inputs: tuple[AgentInput, ...]) -> None:
        vehicles = []
        shortened = len(inputs) > 100
        for item in inputs[:100]:
            observation = item.observation
            memory = item.memory or ()
            shortened |= (
                len(observation.nearby_vehicles) > 3
                or len(memory) > 2
                or len(item.skills) > 3
                or any(len(skill.content) > 1200 for skill in item.skills[:3])
            )
            vehicles.append(
                VehicleInputPreview(
                    vehicle_id=observation.ego.vehicle_id,
                    speed_mps=observation.ego.speed_mps,
                    lane_id=observation.ego.lane_id,
                    radius_m=observation.radius_m,
                    nearby_count=len(observation.nearby_vehicles),
                    nearby=tuple(
                        NearbyInputPreview(
                            vehicle_id=near.vehicle.vehicle_id,
                            distance_m=near.distance_m,
                            speed_mps=near.vehicle.speed_mps,
                        )
                        for near in sorted(
                            observation.nearby_vehicles, key=lambda near: near.distance_m
                        )[:3]
                    ),
                    memory_steps=len(memory) if item.memory is not None else None,
                    recent_memory=tuple(
                        MemoryInputPreview(
                            sequence=entry.observation.ego.sequence,
                            action_text=(
                                entry.requested_action.model_dump_json()
                                if entry.requested_action
                                else "null"
                            ),
                            next_speed_mps=(
                                entry.next_state.speed_mps if entry.next_state else None
                            ),
                        )
                        for entry in memory[-2:]
                    ),
                    skills=tuple(
                        SkillInputPreview(name=skill.name, content=skill.content[:1200])
                        for skill in item.skills[:3]
                    ),
                )
            )
        self.history.update(
            self.record_id,
            input_preview=DecisionInputPreview(vehicles=tuple(vehicles), truncated=shortened),
        )
        self.context(model_state(inputs))

    def context(self, payload: dict[str, object]) -> None:
        # Keep memory and skills visible even when nearby-vehicle observations are large.
        ordered = {key: value for key, value in payload.items() if key != "observations"}
        if "observations" in payload:
            ordered["observations"] = payload["observations"]
        value = json.dumps(ordered, ensure_ascii=False, indent=2)
        self.history.update(
            self.record_id,
            input_text=value[:_TEXT_LIMIT],
            truncated=self.history.get(self.record_id).truncated or len(value) > _TEXT_LIMIT,
        )

    def finish(self, actions: tuple[VehicleControlRequest, ...], *, failed: bool = False) -> None:
        record = self.history.get(self.record_id)
        skipped = record.vehicle_count == 0
        text = json.dumps(
            [a.model_dump(mode="json") for a in actions], ensure_ascii=False, indent=2
        )
        self.history.update(
            self.record_id,
            state="failed" if failed else "skipped" if skipped else "completed",
            elapsed_ms=round((time.monotonic() - self._started) * 1000),
            action_count=len(actions),
            fallback_count=record.vehicle_count - len(actions),
            actions_text=text[:_TEXT_LIMIT],
            truncated=record.truncated or len(text) > _TEXT_LIMIT,
            message=(
                "调用异常，本轮使用 SUMO"
                if failed
                else "无匹配车辆，未调用模型"
                if skipped
                else (
                    f"有效动作 {len(actions)}，"
                    f"其余 {record.vehicle_count - len(actions)} 辆使用 SUMO"
                )
            ),
        )


@contextmanager
def capture_decision(
    history: DecisionHistory | None,
    binding: Binding,
    frame: LocalObservationFrame,
    observations: tuple[VehicleObservation, ...],
) -> Iterator[DecisionCapture | None]:
    capture = history.begin(binding, frame, observations) if history else None
    token = _active.set(capture)
    try:
        yield capture
    except Exception:
        if capture:
            capture.finish((), failed=True)
        raise
    finally:
        _active.reset(token)


def record_context(inputs: tuple[AgentInput, ...]) -> None:
    if capture := _active.get():
        capture.inputs(inputs)


def record_request(path: str, payload: dict[str, object], secret: str = "") -> None:
    if capture := _active.get():
        capture.history.append_text(
            capture.record_id,
            "request_text",
            path + "\n" + json.dumps(payload, ensure_ascii=False, indent=2),
            secret,
        )


def record_response(value: str, secret: str = "") -> None:
    if capture := _active.get():
        capture.history.append_text(capture.record_id, "output_text", value, secret)
