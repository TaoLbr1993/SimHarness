from __future__ import annotations

import asyncio
from uuid import UUID

from trafficverse.application.simulation_runner import SimulationRunner
from trafficverse.domain.enums import ExperimentStatus
from trafficverse.domain.models import SimulationFrame, TrafficSnapshot

EXPERIMENT_ID = UUID("00000000-0000-0000-0000-000000000050")


class FakeManager:
    step_ms = 50
    speed_multiplier = 1.0
    external_stepping = False

    def __init__(self, status: ExperimentStatus) -> None:
        self.status = status
        self.ticks = 0

    async def get_status(self) -> ExperimentStatus:
        return self.status

    async def run_tick(self) -> SimulationFrame:
        self.ticks += 1
        return SimulationFrame(
            traffic=TrafficSnapshot(
                experiment_id=EXPERIMENT_ID,
                simulation_time_ms=self.ticks * 50,
                sequence=self.ticks,
            )
        )


def test_runner_only_calls_manager_tick_while_running() -> None:
    async def exercise() -> None:
        manager = FakeManager(ExperimentStatus.READY)
        runner = SimulationRunner(manager)

        assert await runner.step_if_running() is False
        manager.status = ExperimentStatus.RUNNING
        assert await runner.step_if_running() is True
        manager.status = ExperimentStatus.PAUSED
        assert await runner.step_if_running() is False
        assert manager.ticks == 1

    asyncio.run(exercise())


def test_runner_close_cancels_background_poll_without_leaking_task() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()

        async def waiting(_: float) -> None:
            entered.set()
            await asyncio.Event().wait()

        runner = SimulationRunner(
            FakeManager(ExperimentStatus.CREATED),
            sleep=waiting,
        )
        runner.start()
        await entered.wait()
        await runner.close()

    asyncio.run(exercise())
