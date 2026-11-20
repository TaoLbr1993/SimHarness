"""Process-local presentation language. Protocol values and user data stay unchanged."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ui.scene_language import Language


def set_language(language: Language) -> None:
    application = QCoreApplication.instance()
    if application is None:
        raise RuntimeError("Create the Qt application before selecting its language")
    application.setProperty("simharnessInterfaceLanguage", language)


def language() -> Language:
    application = QCoreApplication.instance()
    return (
        "en"
        if application and application.property("simharnessInterfaceLanguage") == "en"
        else "zh"
    )


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    path = Path(__file__).with_name("locales") / "en.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError("Invalid English UI catalog")
    return data


@lru_cache(maxsize=1)
def _patterns() -> tuple[tuple[re.Pattern[str], str, tuple[int, ...]], ...]:
    path = Path(__file__).with_name("locales") / "en-patterns.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        (re.compile(row["pattern"], re.DOTALL), row["target"], tuple(row["translate"]))
        for row in rows
    )


def tr(source: str) -> str:
    """Translate known UI copy only; unknown service messages remain verbatim."""
    if language() != "en" or not re.search(r"[\u4e00-\u9fff]", source):
        return source
    catalog = _catalog()
    if source in catalog:
        return catalog[source]
    for pattern, target, translated_fields in _patterns():
        if match := pattern.fullmatch(source):
            values = list(match.groups())
            for index in translated_fields:
                values[index] = tr(values[index])
            return target.format(*values)
    for separator in ("\n", " · "):
        if separator in source:
            return separator.join(tr(part) for part in source.split(separator))
    return source
