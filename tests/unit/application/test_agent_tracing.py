import json
from pathlib import Path
from uuid import UUID

import pytest
from tests.unit.application.test_agent_harness import _frame

from trafficverse.agent_harness.config import Binding, ModelConfig
from trafficverse.agent_harness.context import AgentInput, MemoryRecord, SkillContent
from trafficverse.agent_harness.models import http_transport
from trafficverse.agent_harness.tracing import DecisionHistory, capture_decision, record_context
from trafficverse.application.vehicle_observation import local_observations


def binding() -> Binding:
    return Binding(
        name="driver",
        wrapper="test:Agent",
        all_vehicles=True,
        model=ModelConfig(
            base_url="http://localhost/v1", model_id="test", api_key_env="TRACE_TEST_KEY"
        ),
    )


def test_requests_and_invalid_outputs_recorded_without_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "private-test-credential"
    monkeypatch.setenv("TRACE_TEST_KEY", secret)
    observations = local_observations(_frame(), radius_m=100, all_vehicles=True)
    history = DecisionHistory(UUID(int=1), tmp_path)

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self, _size: int) -> bytes:
            return ("malformed-json " + secret).encode()

    def fake_open(*_args: object, **_kwargs: object) -> Response:
        # The input must be available while the model has not returned yet.
        record = history.get(1)
        assert record.state == "running" and "messages" in record.request_text
        return Response()

    monkeypatch.setattr(http_transport, "urlopen", fake_open)
    with capture_decision(history, binding(), observations, observations.observations) as capture:
        record_context(
            (
                AgentInput(
                    observation=observations.observations[0],
                    memory=(),
                    skills=(SkillContent(name="balanced", content="drive normally"),),
                ),
            )
        )
        model = binding().model
        assert model is not None
        result = http_transport.post_json(
            model, "/chat/completions", {"messages": [{"content": "input"}]}
        )
        assert result is None
        assert capture is not None
        capture.finish(())
    history.settle(observations.sequence, "discarded")
    record = history.get(1)
    assert "malformed-json" in record.output_text and "[REDACTED]" in record.output_text
    assert secret not in record.model_dump_json()
    assert secret not in (tmp_path / "00000001.json").read_text(encoding="utf-8")
    assert record.outcome == "discarded" and record.fallback_count == 2
    assert json.loads(record.input_text)["vehicle_contexts"][0]["skills"][0]["name"] == "balanced"
    assert record.input_preview is not None
    assert record.input_preview.vehicles[0].memory_steps == 0


def test_disk_history_supports_older_pages_beyond_cache(tmp_path: Path) -> None:
    observations = local_observations(_frame(), radius_m=100, all_vehicles=True)
    history = DecisionHistory(UUID(int=1), tmp_path)
    for _ in range(70):
        with capture_decision(history, binding(), observations, ()) as capture:
            assert capture is not None
            capture.finish(())
    newest = history.page(limit=20)
    assert newest.total == 70 and newest.next_before_id == 51
    oldest = history.page(before_id=11, limit=20)
    assert len(oldest.items) == 10 and oldest.items[-1].record_id == 1
    assert oldest.items[-1].state == "skipped"
    assert history.get(1).actions_text == "[]"
    with pytest.raises(KeyError):
        history.get(-1)


def test_large_text_is_marked_and_storage_error_does_not_abort_decision(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    observations = local_observations(_frame(), radius_m=100, all_vehicles=True)
    history = DecisionHistory(UUID(int=1), blocked)
    with capture_decision(history, binding(), observations, observations.observations) as capture:
        assert capture is not None
        history.append_text(1, "output_text", "x" * 150_000)
        capture.finish(())
    record = history.get(1)
    assert record.truncated and len(record.output_text) == 128_000
    assert history.page().storage_warning


def test_input_cards_survive_text_truncation_and_bound_preview_size() -> None:
    observations = local_observations(_frame(), radius_m=100, all_vehicles=True)
    observation = observations.observations[0]
    history = DecisionHistory(UUID(int=1))
    inputs = tuple(
        AgentInput(
            observation=observation.model_copy(
                update={"ego": observation.ego.model_copy(update={"vehicle_id": str(index)})}
            ),
            memory=tuple(MemoryRecord(observation=observation, next_sequence=2) for _ in range(4)),
            skills=(SkillContent(name="human_normal", content="x" * 1500),),
        )
        for index in range(105)
    )
    with capture_decision(history, binding(), observations, (observation,)) as capture:
        record_context(inputs)
        assert capture is not None
        capture.finish(())
    record = history.get(1)
    assert record.truncated
    preview = record.input_preview
    assert preview is not None and preview.truncated
    assert len(preview.vehicles) == 100
    first = preview.vehicles[0]
    assert first.memory_steps == 4 and len(first.recent_memory) == 2
    assert first.recent_memory[0].next_speed_mps is None
    assert first.skills[0].name == "human_normal" and len(first.skills[0].content) == 1200
