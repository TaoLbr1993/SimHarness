from __future__ import annotations

import json
from typing import Any, NoReturn
from urllib.error import URLError
from urllib.request import Request
from uuid import UUID

import pytest

from trafficverse.agent_harness.__main__ import load_agent
from trafficverse.agent_harness.config import Binding, ModelConfig
from trafficverse.agent_harness.context import AgentInput, MemoryRecord, SkillContent
from trafficverse.agent_harness.models import http_transport
from trafficverse.agent_harness.models.api import ApiModel
from trafficverse.agent_harness.models.jev import JevModel
from trafficverse.agent_harness.models.local import LocalModel
from trafficverse.agent_harness.models.validation import parse_actions
from trafficverse.domain.enums import AutomationLevel, LaneChangeDirection, VehicleAction
from trafficverse.domain.models import Vector3, VehicleState
from trafficverse.domain.models.observation import VehicleObservation


def _observation(vehicle_id: str) -> VehicleObservation:
    return VehicleObservation(
        ego=VehicleState(
            experiment_id=UUID(int=1),
            vehicle_id=vehicle_id,
            simulation_time_ms=1000,
            sequence=1,
            automation_level=AutomationLevel.L0,
            position=Vector3(x=0, y=0),
            speed_mps=5,
            acceleration_mps2=0,
            heading_rad=0,
            lane_id="lane",
            controller_id="sumo",
            action=VehicleAction.KEEP_LANE,
            risk_score=0,
        ),
        radius_m=100,
    )


@pytest.mark.parametrize("wrapper", [ApiModel, LocalModel])
def test_chat_models_request_local_observations_and_validate_actions(
    wrapper: type[ApiModel] | type[LocalModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[ModelConfig, str, dict[str, Any]]] = []

    def fake_post(config: ModelConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((config, path, payload))
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "actions": [
                                    {"vehicle_id": "a", "desired_speed_mps": 8.0},
                                    {"vehicle_id": "b", "lane_change": "LEFT"},
                                    {"vehicle_id": "unknown", "stop_requested": True},
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("trafficverse.agent_harness.models.http_chat.post_json", fake_post)
    model = wrapper(ModelConfig(base_url="http://localhost:8001/v1", model_id="model"))
    actions = model.decide((_observation("a"), _observation("b")))
    assert [(a.vehicle_id, a.desired_speed_mps, a.lane_change) for a in actions] == [
        ("a", 8.0, LaneChangeDirection.NONE),
        ("b", None, LaneChangeDirection.LEFT),
    ]
    assert calls[0][1] == "/chat/completions"
    sent = json.loads(calls[0][2]["messages"][1]["content"])
    assert [o["ego"]["vehicle_id"] for o in sent["observations"]] == ["a", "b"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"actions": NaN}',
        '{"actions": [], "actions": []}',
        '{"other": []}',
    ],
)
def test_invalid_model_envelope_returns_empty_actions(content: str) -> None:
    assert parse_actions(content, (_observation("a"),)) == ()


def test_fenced_json_is_validated_like_plain_json() -> None:
    content = '```json\n{"actions": [{"vehicle_id":"a","desired_speed_mps":8}]}\n```'
    assert [action.vehicle_id for action in parse_actions(content, (_observation("a"),))] == ["a"]
    invalid = '```json\n{"actions": [{"vehicle_id":"a","desired_speed_mps":-1}]}\n```'
    assert parse_actions(invalid, (_observation("a"),)) == ()


@pytest.mark.parametrize(
    "invalid",
    [
        {"vehicle_id": "a", "desired_speed_mps": -1},
        {"vehicle_id": "a", "desired_speed_mps": "5"},
        {"vehicle_id": "a", "desired_speed_mps": True},
        {"vehicle_id": "a", "desired_speed_mps": 5, "desired_acceleration_mps2": 1},
        {"vehicle_id": "a", "lane_change": "UP"},
        {"vehicle_id": "a", "takeover_requested": True},
        {"vehicle_id": "a", "desired_speed_mps": 1, "unknown": 1},
    ],
)
def test_invalid_action_only_falls_back_for_that_vehicle(invalid: dict[str, object]) -> None:
    content = json.dumps({"actions": [invalid, {"vehicle_id": "b", "stop_requested": True}]})
    actions = parse_actions(content, (_observation("a"), _observation("b")))
    assert [a.vehicle_id for a in actions] == ["b"]


def test_duplicate_model_actions_fall_back_for_that_vehicle() -> None:
    content = json.dumps(
        {
            "actions": [
                {"vehicle_id": "a", "desired_speed_mps": 1},
                {"vehicle_id": "a", "desired_speed_mps": 2},
                {"vehicle_id": "b", "desired_speed_mps": 3},
            ]
        }
    )
    assert [
        a.vehicle_id for a in parse_actions(content, (_observation("a"), _observation("b")))
    ] == ["b"]


def test_jev_uses_decisions_questions_and_maps_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[ModelConfig, str, dict[str, Any]]] = []

    def fake_post(config: ModelConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((config, path, payload))
        return {
            "answers": {
                "vehicle_0": {"type": "choice", "choice": "decelerate"},
                "vehicle_1": {"type": "choice", "choice": "left"},
                "vehicle_2": {"type": "choice", "choice": "sumo"},
            }
        }

    monkeypatch.setattr("trafficverse.agent_harness.models.jev.post_json", fake_post)
    model = JevModel(
        ModelConfig(base_url="https://openrouter.ai/api", model_id="typesafe/jev-1.14")
    )
    actions = model.decide((_observation("a"), _observation("b"), _observation("c")))
    assert calls[0][0].api_key_env == "OPENROUTER_JEV_SK"
    assert calls[0][1] == "/alpha/decisions"
    assert len(calls[0][2]["questions"]) == 3
    assert actions[0].desired_acceleration_mps2 == -1
    assert actions[1].lane_change == LaneChangeDirection.LEFT
    assert [action.vehicle_id for action in actions] == ["a", "b"]


def test_jev_invalid_missing_and_unknown_answers_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(_config: ModelConfig, _path: str, _payload: object) -> dict[str, object]:
        return {
            "answers": {
                "vehicle_0": {"type": "score", "choice": "left"},
                "vehicle_1": {"type": "choice", "choice": "teleport"},
            }
        }

    monkeypatch.setattr("trafficverse.agent_harness.models.jev.post_json", fake_post)
    model = JevModel(ModelConfig(base_url="http://localhost/api", model_id="jev"))
    assert model.decide((_observation("a"), _observation("b"), _observation("c"))) == ()


@pytest.mark.parametrize("kind", ["chat", "jev"])
def test_model_payload_contains_vehicle_memory_and_skills(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_post(_config: ModelConfig, _path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(payload)
        if kind == "chat":
            return {"choices": [{"message": {"content": '{"actions": []}'}}]}
        return {"answers": {"vehicle_0": {"type": "choice", "choice": "sumo"}}}

    observation = _observation("a")
    agent_input = AgentInput(
        observation=observation,
        memory=(MemoryRecord(observation=observation, next_sequence=2),),
        skills=(SkillContent(name="human_cautious", content="Keep distance"),),
    )
    config = ModelConfig(base_url="http://localhost/api", model_id="model")
    if kind == "chat":
        monkeypatch.setattr("trafficverse.agent_harness.models.http_chat.post_json", fake_post)
        ApiModel(config).decide_with_context((agent_input,))
        state = json.loads(captured[0]["messages"][1]["content"])
    else:
        monkeypatch.setattr("trafficverse.agent_harness.models.jev.post_json", fake_post)
        JevModel(config).decide_with_context((agent_input,))
        state = captured[0]["state"]
    vehicle_context = state["vehicle_contexts"][0]
    assert vehicle_context["vehicle_id"] == "a"
    assert vehicle_context["memory"][0]["observation"]["ego"]["vehicle_id"] == "a"
    assert vehicle_context["skills"][0]["content"] == "Keep distance"


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": [None]},
        {"choices": [{"finish_reason": "length", "message": {"content": '{"actions": []}'}}]},
    ],
)
def test_chat_bad_or_incomplete_response_falls_back(
    response: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "trafficverse.agent_harness.models.http_chat.post_json", lambda *_args: response
    )
    model = ApiModel(ModelConfig(base_url="http://localhost/v1", model_id="model"))
    assert model.decide((_observation("a"),)) == ()


def test_http_transport_sets_env_bearer_and_handles_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Request, float]] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self, _size: int) -> bytes:
            return b'{"ok":true}'

    def fake_open(request: Request, timeout: float) -> Response:
        captured.append((request, timeout))
        return Response()

    monkeypatch.setenv("TEST_MODEL_KEY", "secret")
    monkeypatch.setattr(http_transport, "urlopen", fake_open)
    config = ModelConfig(
        base_url="http://localhost:8001/v1", model_id="x", api_key_env="TEST_MODEL_KEY"
    )
    assert http_transport.post_json(config, "/chat/completions", {"model": "x"}) == {"ok": True}
    assert captured[0][0].get_header("Authorization") == "Bearer secret"

    def fail(_request: Request, timeout: float) -> NoReturn:
        raise URLError("failed")

    monkeypatch.setattr(http_transport, "urlopen", fail)
    assert http_transport.post_json(config, "/chat/completions", {}) is None


def test_binding_constructs_configured_model() -> None:
    binding = Binding(
        name="test",
        wrapper="trafficverse.agent_harness.models.local:LocalModel",
        vehicle_ids=("a",),
        model=ModelConfig(base_url="http://localhost:8001/v1", model_id="x"),
    )
    assert isinstance(load_agent(binding), LocalModel)
