"""Load named UTF-8 driving style skills from a configured directory."""

import re
from pathlib import Path
from typing import Protocol

from trafficverse.agent_harness.context import SkillContent

_SKILL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_MAX_SKILL_BYTES = 16 * 1024


class SkillProvider(Protocol):
    def load(self, name: str) -> SkillContent: ...


class FileSkillProvider:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = (directory or Path(__file__).parent / "definitions").resolve()

    def load(self, name: str) -> SkillContent:
        if _SKILL_NAME.fullmatch(name) is None:
            raise ValueError("invalid skill name")
        path = (self._directory / f"{name}.md").resolve()
        if path.parent != self._directory:
            raise ValueError("skill path must stay within the configured directory")
        with path.open("rb") as stream:
            content = stream.read(_MAX_SKILL_BYTES + 1)
        if len(content) > _MAX_SKILL_BYTES:
            raise ValueError("skill content exceeds the size limit")
        text = content.decode("utf-8").strip()
        if not text:
            raise ValueError("skill content is empty")
        return SkillContent(name=name, content=text)
