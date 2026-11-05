"""Language selection for TrafficForge, independent of scenario data."""

from typing import Literal

Language = Literal["en", "zh"]


def normalize_language(value: str) -> Language:
    """Accept English and Chinese locale identifiers without changing application locale."""
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    if code == "en":
        return "en"
    if code == "zh":
        return "zh"
    raise ValueError("TrafficForge supports English (en) and Simplified Chinese (zh).")


def text(language: str, zh: str, en: str) -> str:
    """Choose display text while preserving user-authored scenario content."""
    return zh if normalize_language(language) == "zh" else en
