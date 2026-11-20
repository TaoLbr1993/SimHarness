"""Plain-text presentation of untrusted provider data; never infer execution success."""

import json
import math
from typing import cast

from ui.localization import tr

STATE_LABELS = {
    "running": "正在推理",
    "completed": "已返回",
    "skipped": "未调用",
    "failed": "调用异常",
}
OUTCOME_LABELS = {
    "pending": "待提交",
    "submitted": "已提交",
    "discarded": "已丢弃",
    "failed": "未提交",
}
SKILL_LABELS = {
    "human_cautious": "谨慎驾驶",
    "human_normal": "均衡驾驶",
    "human_reckless": "鲁莽驾驶",
}


def decode_text(text: str) -> object:
    try:
        return cast(object, json.loads(text))
    except (ValueError, RecursionError):
        return None


def object_fields(value: object) -> dict[str, object]:
    # JSON is untyped at this boundary; keys from the decoder are strings.
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def number(value: object, unit: str) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return f"{value:g} {unit}"
    except OverflowError:
        return None
    return None


def action_text(value: object) -> str:
    fields = object_fields(value)
    parts = []
    if speed := number(fields.get("desired_speed_mps"), "m/s"):
        parts.append(tr(f"目标速度 {speed}"))
    if acceleration := number(fields.get("desired_acceleration_mps2"), "m/s²"):
        parts.append(tr(f"加速度 {acceleration}"))
    lane = str(fields.get("lane_change", "")).lower()
    if lane in {"left", "right"}:
        parts.append(tr("向左换道" if lane == "left" else "向右换道"))
    if fields.get("stop_requested") is True:
        parts.append(tr("请求停车"))
    if fields.get("takeover_requested") is True:
        parts.append(tr("请求接管"))
    return " · ".join(parts) or tr("无外部动作")


def action_rows(text: str) -> tuple[tuple[str, str], ...] | None:
    value = decode_text(text)
    if not isinstance(value, list):
        return None
    rows = []
    for entry in value:
        fields = object_fields(entry)
        vehicle = fields.get("vehicle_id")
        if isinstance(vehicle, str):
            rows.append((vehicle, action_text(fields)))
    return tuple(rows)


def response_excerpt(text: str, *, running: bool = False) -> str:
    """Unwrap chat responses without inventing reasoning or parsing Jev batch IDs as cars."""
    if not text:
        return tr("模型正在推理，返回后会自动更新。" if running else "本轮没有模型响应。")
    fields = object_fields(decode_text(text))
    choices = fields.get("choices")
    if isinstance(choices, list) and choices:
        message = object_fields(object_fields(choices[0]).get("message"))
        if isinstance(content := message.get("content"), str):
            text = content
            fields = object_fields(decode_text(content))
    actions = fields.get("actions")
    if isinstance(actions, list):
        rows = action_rows(json.dumps(actions)) or ()
        lines = [tr(f"模型返回 {len(actions)} 个候选动作（以校验结果为准）")]
        for vehicle, action in rows[:30]:
            lines.append(f"{vehicle}  →  {action}")
        if len(rows) > 30:
            lines.append(tr("仅展示前 30 个候选动作；其余见原始详情。"))
        # Only show explanations explicitly present in the response.
        reason = fields.get("reason") or fields.get("reasoning") or fields.get("explanation")
        if isinstance(reason, str):
            lines.append(reason)
        text = "\n".join(lines)
    elif isinstance(answers := fields.get("answers"), dict):
        labels = {
            "accelerate": "加速",
            "decelerate": "减速",
            "left": "向左换道",
            "right": "向右换道",
            "stop": "停车",
            "sumo": "交由 SUMO 控制",
        }
        lines = [tr("Jev 返回的批次内选择（实际车辆动作见校验结果）")]
        for key, answer in list(answers.items())[:30]:
            choice = str(object_fields(answer).get("choice", "未知选择"))
            lines.append(f"{key}  →  {tr(labels[choice]) if choice in labels else choice}")
        if len(answers) > 30:
            lines.append(tr("其余选择见原始详情。"))
        text = "\n".join(lines)
    return text[:6000] + (tr("\n…摘要已省略，请查看原始详情。") if len(text) > 6000 else "")
