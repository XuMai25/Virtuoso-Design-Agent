"""Adapter port used by the bounded executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import EvidenceSource, TaskSpec


class AdapterInterrupted(RuntimeError):
    """Adapter execution stopped before producing trustworthy candidate evidence."""


@dataclass(frozen=True)
class AdapterResult:
    data: dict[str, Any]
    evidence_source: EvidenceSource


class DesignAdapter(Protocol):
    name: str

    def probe(self, pdk_profile: str) -> AdapterResult: ...

    def create_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def inspect_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def transform_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def verify_parameters(
        self, task: TaskSpec, expected: dict[str, dict[str, str]]
    ) -> AdapterResult: ...

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult: ...

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult: ...
