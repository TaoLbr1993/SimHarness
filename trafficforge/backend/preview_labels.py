"""Resolve display names without changing authoritative map or geocoding data."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

from pypinyin import lazy_pinyin

# Include extension planes so unsupported rare characters cannot leak into English labels.
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]+")
_PUNCTUATION = str.maketrans({"（": "(", "）": ")", "，": ",", "、": "/", "：": ":"})
_TYPE_SUFFIXES = (
    ("高速公路", "Expressway"),
    ("立交桥", "Interchange"),
    ("大道", "Avenue"),
    ("大街", "Street"),
    ("公路", "Highway"),
    ("高速", "Expressway"),
    ("胡同", "Hutong"),
    ("校区", "Campus"),
    ("大学", "University"),
    ("学院", "College"),
    ("小学", "Primary School"),
    ("中学", "Middle School"),
    ("学校", "School"),
    ("医院", "Hospital"),
    ("公园", "Park"),
    ("广场", "Square"),
    ("机场", "Airport"),
    ("车站", "Station"),
    ("路", "Road"),
    ("街", "Street"),
    ("桥", "Bridge"),
    ("站", "Station"),
    ("店", "Store"),
)


def osm_english_names(source_path: str | Path | None) -> dict[str, str]:
    """Use unambiguous name:en values for named OSM nodes, ways and relations."""
    if source_path is None:
        return {}
    candidates: dict[str, set[str]] = {}
    for element in ET.parse(source_path).getroot():
        if element.tag not in {"node", "way", "relation"}:
            continue
        tags = {tag.get("k", ""): tag.get("v", "").strip() for tag in element.findall("tag")}
        english = tags.get("name:en", "")
        if not english or _HAN.search(english):
            continue
        for key in ("name", "name:zh"):
            if name := tags.get(key):
                candidates.setdefault(name, set()).add(english)
    # Different places may share a Chinese name; never select an arbitrary English one.
    return {name: next(iter(values)) for name, values in candidates.items() if len(values) == 1}


def _pinyin(value: str) -> str:
    # Pinyin is a deterministic display fallback, not a claim of an official translation.
    syllables = lazy_pinyin(value, errors=lambda chars: " ".join(f"U+{ord(c):04X}" for c in chars))
    return " ".join(syllable.capitalize() for syllable in syllables)


def _romanize(value: str, source_names: Mapping[str, str]) -> str:
    source = source_names.get(value, "")
    if source and not _HAN.search(source):
        return source
    for suffix, english in _TYPE_SUFFIXES:
        if value.endswith(suffix):
            stem = value[: -len(suffix)]
            # A campus can be named after a road; road stems remain proper names.
            name = _romanize(stem, source_names) if suffix == "校区" else _pinyin(stem)
            return f"{name} {english}".strip()
    return _pinyin(value)


def english_name(value: str, source_names: Mapping[str, str] | None = None) -> str:
    """Prefer source English, otherwise romanize proper names and translate type suffixes."""
    value = " ".join(value.split())
    source_names = source_names or {}
    source = source_names.get(value, "")
    if source and not _HAN.search(source):
        return source
    value = value.translate(_PUNCTUATION)
    # Spaces around converted runs preserve mixed names such as "SOHO望京".
    value = _HAN.sub(lambda match: f" {_romanize(match.group(), source_names)} ", value)
    value = " ".join(value.split())
    value = re.sub(r"\(\s+", "(", value)
    return re.sub(r"\s+([),/:])", r"\1", value)
