import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.unit.api.test_app import FakeManager, _dependencies
from tests.unit.application.test_agent_harness import Agent, _frame

from trafficverse.agent_harness.config import ModelConfig
from trafficverse.agent_harness.session_models import SessionBinding, SessionConfig
from trafficverse.agent_harness.sessions import AgentSessionService
from trafficverse.api.app import create_app
from trafficverse.api.dependencies import RuntimeDirectory
from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.enums import ExperimentStatus
from trafficverse.domain.errors import TrafficVerseError
from trafficverse.domain.models import ControlCommand, SimulationFrame
from trafficverse.domain.models.observation import VehicleObservation

EXPERIMENT = UUID(int=1)


class SteppingRuntime(FakeManager):
    def __init__(self) -> None:
        super().__init__(EXPERIMENT, ExperimentStatus.RUNNING)
        self.external_stepping = True
        self.last_frame = _frame()
        self.batches: list[dict[str, ControlCommand]] = []

    async def step_external(
        self, expected_sequence: int, commands: dict[str, ControlCommand]
    ) -> SimulationFrame:
        assert self.last_frame is not None
        assert expected_sequence == self.last_frame.traffic.sequence
        assert self.status is ExperimentStatus.RUNNING
        self.batches.append(commands)
        traffic = self.last_frame.traffic
        self.simulation_time_ms = traffic.simulation_time_ms + 50
        self.last_frame = self.last_frame.model_copy(
            update={
                "traffic": traffic.model_copy(
                    update={
                        "sequence": traffic.sequence + 1,
                        "simulation_time_ms": self.simulation_time_ms,
                    }
                )
            }
        )
        return self.last_frame


def config(*, all_vehicles: bool = False, max_steps: int = 1) -> SessionConfig:
    return SessionConfig(
        max_steps=max_steps,
        bindings=(
            SessionBinding(
                name="driver",
                kind="local",
                model=ModelConfig(base_url="http://127.0.0.1:8002/v1", model_id="test"),
                vehicle_ids=() if all_vehicles else ("a",),
                all_vehicles=all_vehicles,
            ),
        ),
    )


def setup_service() -> tuple[AgentSessionService, RuntimeDirectory, SteppingRuntime]:
    runtime = SteppingRuntime()
    runtimes = RuntimeDirectory()
    runtimes.register(EXPERIMENT, runtime)
    service = AgentSessionService(runtimes, model_loader=lambda binding: Agent())
    return service, runtimes, runtime


def test_resume_uses_current_frame_and_preserves_manager() -> None:
    async def run() -> None:
        service, runtimes, runtime = setup_service()
        await service.start(EXPERIMENT, config())
        await service._sessions[EXPERIMENT].task
        assert (await service.view(EXPERIMENT)).status == "paused"
        assert runtime.batches[0].keys() == {"a"}
        assert runtime.last_frame.traffic.sequence == 2
        manager = service._sessions[EXPERIMENT].manager
        with pytest.raises(TrafficVerseError, match="另一"):
            await runtimes.step_external(EXPERIMENT, 2, {})
        await service.start(EXPERIMENT, config())
        await service._sessions[EXPERIMENT].task
        assert runtime.last_frame.traffic.sequence == 3
        assert service._sessions[EXPERIMENT].manager is manager
        await service.stop(EXPERIMENT)
        assert runtimes.control_owner(EXPERIMENT) is None
        await runtimes.step_external(EXPERIMENT, 3, {})
        assert runtime.status is ExperimentStatus.RUNNING
        await service.close()

    asyncio.run(run())


def test_all_vehicles_receives_each_ego_and_no_initial_extra_step() -> None:
    async def run() -> None:
        service, _, runtime = setup_service()
        await service.start(EXPERIMENT, config(all_vehicles=True))
        await service._sessions[EXPERIMENT].task
        assert len(runtime.batches) == 1
        assert set(runtime.batches[0]) == {"a", "b"}
        await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("command", ["pause", "stop"])
def test_pause_or_stop_during_inference_never_submits_late_actions(command: str) -> None:
    async def run() -> None:
        service, runtimes, runtime = setup_service()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        class BlockingAgent(Agent):
            def decide(
                self, observations: tuple[VehicleObservation, ...]
            ) -> tuple[VehicleControlRequest, ...]:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return super().decide(observations)

        later_calls: list[VehicleObservation] = []

        class UncalledAgent(Agent):
            def decide(
                self, observations: tuple[VehicleObservation, ...]
            ) -> tuple[VehicleControlRequest, ...]:
                later_calls.extend(observations)
                return super().decide(observations)

        later = UncalledAgent()
        calls: list[str] = []

        def load(binding):
            calls.append(binding.name)
            return BlockingAgent() if binding.name == "driver" else later

        service._load_model = load
        settings = config(max_steps=10)
        settings = settings.model_copy(
            update={
                "bindings": (
                    *settings.bindings,
                    settings.bindings[0].model_copy(
                        update={"name": "later", "vehicle_ids": ("b",)}
                    ),
                )
            }
        )
        try:
            await service.start(EXPERIMENT, settings)
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(TrafficVerseError):
                await service.start(EXPERIMENT, config())
            await getattr(service, command)(EXPERIMENT)
            release.set()
            await service._sessions[EXPERIMENT].task
            assert runtime.batches == []
            assert calls == ["driver", "later"]
            assert later_calls == []
            records = await service.decisions(EXPERIMENT)
            assert records.total == 1
            assert records.items[0].group_name == "driver"
            assert records.items[0].state == "completed"
            assert records.items[0].outcome == "discarded"
            assert (await service.view(EXPERIMENT)).status == (
                "paused" if command == "pause" else "stopped"
            )
            assert (runtimes.control_owner(EXPERIMENT) is None) == (command == "stop")
        finally:
            release.set()
            await service.close()

    asyncio.run(run())


def test_model_failure_falls_back_to_sumo_and_releases_on_runtime_failure() -> None:
    class FailingAgent(Agent):
        def decide(
            self, observations: tuple[VehicleObservation, ...]
        ) -> tuple[VehicleControlRequest, ...]:
            raise ValueError("private provider response")

    async def run() -> None:
        service, runtimes, runtime = setup_service()
        service._load_model = lambda binding: FailingAgent()
        await service.start(EXPERIMENT, config())
        await service._sessions[EXPERIMENT].task
        assert runtime.batches == [{}]
        assert "private" not in (await service.view(EXPERIMENT)).message
        runtime.status = ExperimentStatus.COMPLETED
        with pytest.raises(TrafficVerseError):
            await service.start(EXPERIMENT, config())
        await service.close()
        assert runtimes.control_owner(EXPERIMENT) is None

    asyncio.run(run())


def test_stop_during_last_submitted_step_is_not_overwritten_by_budget_pause() -> None:
    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowStepRuntime(SteppingRuntime):
            async def step_external(
                self, expected_sequence: int, commands: dict[str, ControlCommand]
            ) -> SimulationFrame:
                entered.set()
                await release.wait()
                return await super().step_external(expected_sequence, commands)

        runtimes = RuntimeDirectory()
        runtime = SlowStepRuntime()
        runtimes.register(EXPERIMENT, runtime)
        service = AgentSessionService(runtimes, model_loader=lambda binding: Agent())
        try:
            await service.start(EXPERIMENT, config(max_steps=1))
            await asyncio.wait_for(entered.wait(), 3)
            assert (await service.stop(EXPERIMENT)).status == "stopping"
            release.set()
            await service._sessions[EXPERIMENT].task
            assert (await service.view(EXPERIMENT)).status == "stopped"
            assert runtimes.control_owner(EXPERIMENT) is None
            assert runtime.last_frame.traffic.sequence == 2
            records = await service.decisions(EXPERIMENT)
            assert records.items[0].outcome == "submitted"
        finally:
            release.set()
            await service.close()

    asyncio.run(run())


def test_missing_credential_prevents_start_and_check_does_not_call_model(monkeypatch) -> None:
    monkeypatch.delenv("SIMHARNESS_TEST_MISSING_KEY", raising=False)
    settings = SessionConfig(
        bindings=(
            SessionBinding(
                name="remote",
                kind="api",
                all_vehicles=True,
                model=ModelConfig(
                    base_url="https://model.example.com/v1",
                    model_id="test",
                    api_key_env="SIMHARNESS_TEST_MISSING_KEY",
                ),
            ),
        )
    )

    async def run() -> None:
        service, runtimes, runtime = setup_service()

        def forbidden(binding):
            raise AssertionError("configuration check must not call model")

        service._load_model = forbidden
        check = await service.check(EXPERIMENT, settings)
        assert not check.ready and "SIMHARNESS_TEST_MISSING_KEY" in check.issues[0]
        with pytest.raises(TrafficVerseError):
            await service.start(EXPERIMENT, settings)
        assert runtimes.control_owner(EXPERIMENT) is None and not runtime.batches

    asyncio.run(run())


def test_overlapping_and_all_vehicle_groups_rejected() -> None:
    a = config().bindings[0]
    with pytest.raises(ValidationError, match="multiple control"):
        SessionConfig(bindings=(a, a.model_copy(update={"name": "b"})))
    with pytest.raises(ValidationError, match="overlap"):
        SessionConfig(
            bindings=(
                a.model_copy(update={"all_vehicles": True}),
                a.model_copy(update={"name": "b", "vehicle_ids": ("b",)}),
            )
        )
    with pytest.raises(ValidationError, match="environment"):
        ModelConfig(
            base_url="https://model.example.com", model_id="test", api_key_env="sk-secret-value"
        )


def test_http_sessions_and_external_step_conflict(tmp_path: Path) -> None:
    runtime = SteppingRuntime()
    dependencies = _dependencies(tmp_path, runtime)
    service = AgentSessionService(dependencies.runtimes, model_loader=lambda binding: Agent())
    app = create_app(replace(dependencies, agent_sessions=service))
    base = f"/api/v1/experiments/{EXPERIMENT}"
    with TestClient(app) as client:
        result = client.post(base + "/agent-session/check", json=config().model_dump(mode="json"))
        assert result.status_code == 200 and result.json()["ready"]
        result = client.post(base + "/agent-session/start", json=config().model_dump(mode="json"))
        assert result.status_code == 200
        assert (
            client.post(base + "/step", json={"expected_sequence": 1, "actions": []}).status_code
            == 409
        )
        assert (
            client.get(base + "/agent-session").json()["config"]["bindings"][0]["model"][
                "api_key_env"
            ]
            is None
        )
        assert client.post(base + "/agent-session/pause").status_code == 200
        assert client.post(base + "/agent-session/stop").status_code == 200
