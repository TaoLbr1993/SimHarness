"""Bounded JSON HTTP requests with credentials sourced from the environment."""

import json
import logging
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trafficverse.agent_harness.config import ModelConfig
from trafficverse.agent_harness.tracing import record_request, record_response

logger = logging.getLogger(__name__)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def post_json(config: ModelConfig, path: str, payload: dict[str, object]) -> object:
    """Return JSON or None; optional decision traces omit headers and redact the key."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = ""
    if config.api_key_env:
        api_key = os.environ.get(config.api_key_env, "")
        if not api_key:
            logger.warning("model.missing_credential; falling back to SUMO")
            record_response("未发送请求：后端未读取到所配置的 Key 环境变量。")
            return None
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        record_request(path, payload, api_key)
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
            record_response("响应超过大小限制，本轮回退 SUMO。")
            return None
        record_response(raw.decode("utf-8", errors="replace"), api_key)
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
        record_response("请求失败或响应不是有效 JSON，本轮回退 SUMO。")
        return None
