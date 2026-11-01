"""Validate untrusted model output before it reaches the simulation API."""

import json
import logging
import math
import re
from collections import Counter

from pydantic import ValidationError

from trafficverse.api.models import ExternalStepRequest, VehicleControlRequest
from trafficverse.domain.models.observation import VehicleObservation

logger = logging.getLogger(__name__)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def parse_actions(
    content: str, observations: tuple[VehicleObservation, ...]
) -> tuple[VehicleControlRequest, ...]:
    """Accept only valid actions assigned to this agent; reject ambiguous vehicle entries."""
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", content.strip(), re.I | re.S)
    if fenced is not None:
        content = fenced.group(1)
    try:
        payload = json.loads(
            content, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
    except (ValueError, TypeError, RecursionError):
        logger.warning("model.invalid_json; falling back to SUMO")
        return ()
    if not isinstance(payload, dict) or set(payload) != {"actions"}:
        logger.warning("model.invalid_response; falling back to SUMO")
        return ()
    items = payload["actions"]
    if not isinstance(items, list):
        logger.warning("model.invalid_actions; falling back to SUMO")
        return ()
    counts = Counter(
        item.get("vehicle_id")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("vehicle_id"), str)
    )
    allowed = {observation.ego.vehicle_id for observation in observations}
    actions = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("model.invalid_action; falling back to SUMO")
            continue
        vehicle_id = item.get("vehicle_id")
        if not isinstance(vehicle_id, str) or vehicle_id not in allowed or counts[vehicle_id] != 1:
            logger.warning("model.unassigned_or_duplicate_action; falling back to SUMO")
            continue
        if not _strict_action_fields(item):
            logger.warning("model.invalid_action_fields vehicle_id=%s", vehicle_id)
            continue
        try:
            action = VehicleControlRequest.model_validate(item)
            ExternalStepRequest(expected_sequence=0, actions=(action,))
        except ValidationError:
            logger.warning("model.invalid_action vehicle_id=%s", vehicle_id)
            continue
        actions.append(action)
    return tuple(actions)


def _strict_action_fields(item: dict[str, object]) -> bool:
    for name in ("desired_speed_mps", "desired_acceleration_mps2"):
        value = item.get(name)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            try:
                if not math.isfinite(value):
                    return False
            except OverflowError:
                return False
    for name in ("stop_requested", "takeover_requested"):
        value = item.get(name)
        if value is not None and not isinstance(value, bool):
            return False
    return True
