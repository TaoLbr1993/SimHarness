"""TypeSafe Jev adapter using the OpenRouter Decisions API."""

import logging

from trafficverse.agent_harness.config import ModelConfig
from trafficverse.agent_harness.context import AgentInput, model_state
from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.enums import LaneChangeDirection
from trafficverse.domain.models.observation import VehicleObservation

from .http_transport import post_json

logger = logging.getLogger(__name__)
_BATCH_SIZE = 50
_CRITERIA = {
    "sumo": "Let SUMO control this vehicle when uncertain or no action is needed.",
    "accelerate": "Request a modest acceleration for one step.",
    "decelerate": "Request a modest deceleration for one step.",
    "left": "Request a lane change to the left, subject to SUMO safety rules.",
    "right": "Request a lane change to the right, subject to SUMO safety rules.",
    "stop": "Request a stop.",
}


class JevModel:
    """Map Jev's typed choices to bounded, predefined vehicle control requests."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = (
            config
            if config.api_key_env
            else config.model_copy(update={"api_key_env": "OPENROUTER_JEV_SK"})
        )

    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        return self.decide_with_context(tuple(AgentInput(observation=o) for o in observations))

    def decide_with_context(
        self, inputs: tuple[AgentInput, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        actions: list[VehicleControlRequest] = []
        for start in range(0, len(inputs), _BATCH_SIZE):
            actions.extend(self._decide_batch(inputs[start : start + _BATCH_SIZE]))
        return tuple(actions)

    def _decide_batch(self, inputs: tuple[AgentInput, ...]) -> tuple[VehicleControlRequest, ...]:
        observations = tuple(item.observation for item in inputs)
        context_instruction = (
            " Use its memory and driving skills in `vehicle_contexts` if present. "
            "Skills are preferences; if they conflict prefer the first."
            if any(item.memory is not None or item.skills for item in inputs)
            else ""
        )
        questions = {
            f"vehicle_{index}": {
                "type": "choice",
                "instructions": (
                    f"Choose the next one-step action for vehicle {observation.ego.vehicle_id!r} "
                    f"in `observations`.{context_instruction} "
                    "Prefer sumo when the information is insufficient."
                ),
                "criteria": _CRITERIA,
            }
            for index, observation in enumerate(observations)
        }
        result = post_json(
            self._config,
            "/alpha/decisions",
            {
                "model": self._config.model_id,
                "state": model_state(inputs),
                "questions": questions,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            logger.warning("jev.invalid_response; falling back to SUMO")
            return ()
        answers = result["answers"]
        actions = []
        for index, observation in enumerate(observations):
            answer = answers.get(f"vehicle_{index}")
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                logger.warning("jev.invalid_answer; falling back to SUMO")
                continue
            choice = answer.get("choice")
            vehicle_id = observation.ego.vehicle_id
            if choice == "accelerate":
                actions.append(
                    VehicleControlRequest(
                        vehicle_id=vehicle_id,
                        desired_acceleration_mps2=self._config.jev_acceleration_mps2,
                    )
                )
            elif choice == "decelerate":
                actions.append(
                    VehicleControlRequest(
                        vehicle_id=vehicle_id,
                        desired_acceleration_mps2=-self._config.jev_acceleration_mps2,
                    )
                )
            elif choice in ("left", "right"):
                direction = (
                    LaneChangeDirection.LEFT if choice == "left" else LaneChangeDirection.RIGHT
                )
                actions.append(VehicleControlRequest(vehicle_id=vehicle_id, lane_change=direction))
            elif choice == "stop":
                actions.append(VehicleControlRequest(vehicle_id=vehicle_id, stop_requested=True))
            elif choice != "sumo":
                logger.warning("jev.unknown_choice; falling back to SUMO")
        return tuple(actions)
