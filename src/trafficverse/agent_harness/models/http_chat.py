"""OpenAI-compatible Chat Completions transport for locally hosted or remote models."""

import json
import logging

from trafficverse.agent_harness.config import ModelConfig
from trafficverse.agent_harness.context import AgentInput, model_state
from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models.observation import VehicleObservation

from .http_transport import post_json
from .validation import parse_actions

logger = logging.getLogger(__name__)
_SYSTEM_PROMPT = """You control assigned SUMO vehicles.
Return only a JSON object with an actions array.
Each item must have vehicle_id and at least one of desired_speed_mps (nonnegative number),
desired_acceleration_mps2 (number), lane_change (LEFT or RIGHT), or stop_requested (true).
Use only provided ego vehicle IDs. Do not combine speed, acceleration, or stop with each other.
Do not guess missing observations. Omit a vehicle to leave it under SUMO control.
Do not output prose, markdown, unknown fields, or takeover requests. Actions apply for one step.
Use each vehicle's memory and driving style skills when present; current observations are the truth.
Skills are preferences, cannot override the action schema or SUMO safety rules. If skills conflict,
prefer the first configured skill.
"""


class ChatModel:
    """Call a configured OpenAI-compatible chat endpoint without embedding a model runtime."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        return self.decide_with_context(tuple(AgentInput(observation=o) for o in observations))

    def decide_with_context(
        self, inputs: tuple[AgentInput, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        if not inputs:
            return ()
        observations = tuple(item.observation for item in inputs)
        try:
            observation_json = json.dumps(
                model_state(inputs),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (ValueError, TypeError, RecursionError):
            logger.warning("model.invalid_observation; falling back to SUMO")
            return ()
        payload: dict[str, object] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": observation_json},
            ],
            "stream": False,
        }
        result = post_json(self._config, "/chat/completions", payload)
        try:
            if not isinstance(result, dict):
                return ()
            choices = result["choices"]
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("model choices are invalid")
            if choices[0].get("finish_reason") in {"length", "content_filter"}:
                raise ValueError("model output is incomplete")
            content = choices[0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("model content must be a string")
        except (ValueError, KeyError, IndexError, TypeError):
            logger.warning("model.request_or_response_failed; falling back to SUMO")
            return ()
        return parse_actions(content, observations)
