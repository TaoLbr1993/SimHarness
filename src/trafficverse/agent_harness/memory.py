"""Bounded in-process memory isolated by experiment and vehicle."""

from collections import deque
from typing import Protocol
from uuid import UUID

from .context import MemoryRecord


class MemoryStore(Protocol):
    def read(self, experiment_id: UUID, vehicle_id: str) -> tuple[MemoryRecord, ...]: ...

    def append(self, experiment_id: UUID, vehicle_id: str, record: MemoryRecord) -> None: ...

    def retain(self, experiment_id: UUID, vehicle_ids: frozenset[str]) -> None: ...


class InMemoryMemoryStore:
    """Keep the last N transitions for active vehicles only."""

    def __init__(self, max_steps: int) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self._max_steps = max_steps
        self._records: dict[tuple[UUID, str], deque[MemoryRecord]] = {}

    def read(self, experiment_id: UUID, vehicle_id: str) -> tuple[MemoryRecord, ...]:
        return tuple(self._records.get((experiment_id, vehicle_id), ()))

    def append(self, experiment_id: UUID, vehicle_id: str, record: MemoryRecord) -> None:
        if (record.observation.ego.experiment_id, record.observation.ego.vehicle_id) != (
            experiment_id,
            vehicle_id,
        ):
            raise ValueError("memory record does not match its vehicle or experiment")
        key = (experiment_id, vehicle_id)
        if key not in self._records:
            self._records[key] = deque(maxlen=self._max_steps)
        self._records[key].append(record)

    def retain(self, experiment_id: UUID, vehicle_ids: frozenset[str]) -> None:
        for key in tuple(self._records):
            if key[0] == experiment_id and key[1] not in vehicle_ids:
                del self._records[key]
