from __future__ import annotations

from typing import Protocol


class ExperimentRunner(Protocol):
    """Future opt-in runner boundary. Phase one never instantiates a runner."""

    def plan(self, experiment_id: str) -> dict[str, object]: ...
