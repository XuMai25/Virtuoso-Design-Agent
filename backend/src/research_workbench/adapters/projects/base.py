from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ProjectInspector(Protocol):
    """Future boundary for read-only filesystem and Git inspection."""

    def inspect(self, path: Path) -> dict[str, object]: ...
