"""Wall-clock pacing around the authoritative Simulation Manager tick."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from trafficverse.domain.enums import ExperimentStatus
from trafficverse.domain.models import SimulationFrame

Sleep = Callable[[float], Awaitable[None]]


class TickingSimulationPort(Protocol):
    @property
    def step_ms(self) -> int: ...

    @property
    def speed_multiplier(self) -> float: ...

    @property
    def external_stepping(self) -> bool: ...

    async def get_status(self) -> ExperimentStatus: ...

    async def run_tick(self) -> SimulationFrame: ...


class SimulationRunner:
    """Paces one manager without ever advancing simulation state itself."""

    def __init__(
        self,
        manager: TickingSimulationPort,
        *,
        sleep: Sleep = asyncio.sleep,
        idle_poll_s: float = 0.05,
    ) -> None:
        if idle_poll_s <= 0:
            raise ValueError("idle poll interval must be positive")
        self._manager = manager
        self._sleep = sleep
        self._idle_poll_s = idle_poll_s
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def step_if_running(self) -> bool:
        if self._manager.external_stepping:
            return False
        if await self._manager.get_status() is not ExperimentStatus.RUNNING:
            return False
        await self._manager.run_tick()
        return True

    async def _run(self) -> None:
        while not self._closed:
            started = time.monotonic()
            advanced = await self.step_if_running()
            if advanced:
                budget_s = self._manager.step_ms / 1000.0 / self._manager.speed_multiplier
                await self._sleep(max(0.0, budget_s - (time.monotonic() - started)))
            else:
                await self._sleep(self._idle_poll_s)
