"""Bounded JSON HTTP requests with credentials sourced from the environment."""

import json
import logging
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trafficverse.agent_harness.config import ModelConfig

logger = logging.getLogger(__name__)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def post_json(config: ModelConfig, path: str, payload: dict[str, object]) -> object:
    """Return parsed JSON or None; never log credentials, URLs, or response bodies."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key_env:
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            logger.warning("model.missing_credential; falling back to SUMO")
            return None
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = Request(
            config.base_url + path,
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=config.timeout_s) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            logger.warning("model.response_too_large; falling back to SUMO")
            return None
        return json.loads(raw)
    except (
        HTTPError,
        URLError,
        HTTPException,
        TimeoutError,
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        logger.warning("model.request_or_response_failed; falling back to SUMO")
        return None
