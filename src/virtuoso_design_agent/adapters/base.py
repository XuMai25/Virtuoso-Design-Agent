"""Adapter port used by the bounded executor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import EvidenceSource, TaskSpec


class AdapterInterrupted(RuntimeError):
    """Adapter execution stopped before producing trustworthy candidate evidence."""


@dataclass(frozen=True)
class AdapterResult:
    data: dict[str, Any]
    evidence_source: EvidenceSource


def merge_analysis_bundle(
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge independent analyses only when their shared evidence agrees."""

    if not results:
        raise RuntimeError("analysis bundle did not contain any results")
    names = list(results)
    first_name = names[0]
    first_parameters = {
        str(name): float(value)
        for name, value in results[first_name].get("parameters", {}).items()
    }
    merged_metrics: dict[str, float] = {}
    merged_sources: dict[str, Any] = {}
    metric_owners: dict[str, str] = {}
    issues: list[str] = []
    warnings: list[str] = []
    completion: dict[str, bool] = {}

    def matches(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)

    for analysis, data in results.items():
        parameters = {
            str(name): float(value)
            for name, value in data.get("parameters", {}).items()
        }
        if parameters.keys() != first_parameters.keys() or any(
            not matches(parameters[name], first_parameters[name])
            for name in first_parameters
        ):
            raise RuntimeError(
                f"analysis bundle parameter mismatch between {first_name} and "
                f"{analysis}"
            )
        complete = bool(data.get("analysis_complete", True))
        completion[analysis] = complete
        raw_issues = [str(value) for value in data.get("analysis_issues", [])]
        if not complete and not raw_issues:
            raw_issues = ["analysis did not produce its required core metrics"]
        issues.extend(f"{analysis}: {value}" for value in raw_issues)
        warnings.extend(
            f"{analysis}: {value}"
            for value in data.get("analysis_warnings", [])
        )
        sources = data.get("metric_sources", {})
        for metric, raw_value in data.get("metrics", {}).items():
            name = str(metric)
            value = float(raw_value)
            if not math.isfinite(value):
                raise RuntimeError(
                    f"analysis bundle metric {analysis}.{name} is not finite"
                )
            source = sources.get(name)
            if name in merged_metrics:
                if not matches(merged_metrics[name], value):
                    raise RuntimeError(
                        "analysis bundle shared metric mismatch for "
                        f"{name}: {metric_owners[name]}={merged_metrics[name]!r}, "
                        f"{analysis}={value!r}"
                    )
                if merged_sources[name] != source:
                    raise RuntimeError(
                        "analysis bundle evidence-source mismatch for shared metric "
                        f"{name}"
                    )
                continue
            merged_metrics[name] = value
            merged_sources[name] = source
            metric_owners[name] = analysis

    return {
        "parameters": first_parameters,
        "metrics": merged_metrics,
        "metric_sources": merged_sources,
        "analysis_complete": all(completion.values()),
        "analysis_issues": issues,
        "analysis_warnings": warnings,
        "analysis_bundle": {
            "analyses": names,
            "completion": completion,
            "parameter_consistency": "matched",
            "shared_metric_consistency": "matched",
        },
        "analysis_results": results,
    }


class DesignAdapter(Protocol):
    name: str

    def probe(self, pdk_profile: str) -> AdapterResult: ...

    def probe_simulator(self, pdk_profile: str) -> AdapterResult: ...

    def characterize_devices(self, task: TaskSpec) -> AdapterResult: ...

    def create_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def inspect_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def generate_schematic_symbol(self, task: TaskSpec) -> AdapterResult: ...

    def inspect_schematic_symbol(self, task: TaskSpec) -> AdapterResult: ...

    def transform_schematic(self, task: TaskSpec) -> AdapterResult: ...

    def verify_parameters(
        self, task: TaskSpec, expected: dict[str, dict[str, str]]
    ) -> AdapterResult: ...

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult: ...

    def discover_parameter_binding(self, task: TaskSpec) -> AdapterResult: ...

    def prepare_ade(self, task: TaskSpec) -> AdapterResult: ...

    def capture_ade(self, task: TaskSpec) -> AdapterResult: ...

    def run_ade(self, task: TaskSpec) -> AdapterResult: ...

    def apply_ade_variables(self, task: TaskSpec) -> AdapterResult: ...

    def apply_ade_corners(self, task: TaskSpec) -> AdapterResult: ...

    def apply_ade_setup(self, task: TaskSpec) -> AdapterResult: ...

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult: ...

    def simulate_analysis_stages(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult: ...
