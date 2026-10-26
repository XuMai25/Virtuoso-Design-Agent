"""Compile a bounded theory result into an atomic VDA tuning task.

The compiler is deliberately local.  It does not call the Bridge, write OA, or
promote analytical predictions to EDA evidence.  Its output is a normal
``TaskSpec`` whose plan token binds the exact candidate tuples and provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .characterization import DeviceDataSource
from .models import (
    EvidenceSource,
    Operation,
    RunStatus,
    StrictModel,
    TaskSpec,
    TheorySeedCandidate,
    TheorySeedCandidateSet,
    TheorySeedSource,
)
from .theory import DifferentialPairTheoryResult, TheoryCandidateEvaluation


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TheorySeedParameterMapping(_FiniteStrictModel):
    """Map topology-local theory quantities onto VDA semantic parameters."""

    input_width: StrictStr = "input_width_um"
    input_length: StrictStr = "length_um"
    load_width: StrictStr = "pmos_load_width_um"
    load_length: StrictStr = "pmos_load_length_um"
    tail_width: StrictStr = "tail_width_um"
    tail_length: StrictStr = "tail_length_um"

    @model_validator(mode="after")
    def names_must_be_distinct(self) -> "TheorySeedParameterMapping":
        names = list(self.model_dump().values())
        if len(names) != len(set(names)):
            raise ValueError("theory-seed parameter mapping names must be distinct")
        return self


class DifferentialPairTheorySeedPolicy(_FiniteStrictModel):
    """User-declared boundary for converting theory points to EDA candidates."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_theory_request_id: StrictStr = Field(min_length=1, max_length=96)
    expected_theory_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximum_candidates: int = Field(default=6, ge=1, le=32)
    ranked_candidate_count: int = Field(default=2, ge=1, le=32)
    width_decimal_places: int = Field(default=6, ge=3, le=12)
    width_grid_um: float | None = Field(default=None, gt=0.0)
    selection_strategy: Literal["ranked_then_log_maximin"] = (
        "ranked_then_log_maximin"
    )
    parameter_mapping: TheorySeedParameterMapping = Field(
        default_factory=TheorySeedParameterMapping
    )
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT

    @model_validator(mode="after")
    def ranked_prefix_fits_budget(self) -> "DifferentialPairTheorySeedPolicy":
        if self.ranked_candidate_count > self.maximum_candidates:
            raise ValueError(
                "ranked_candidate_count cannot exceed maximum_candidates"
            )
        return self


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive(value: float | None, label: str, candidate_id: str) -> float:
    if value is None or not math.isfinite(value) or value <= 0.0:
        raise ValueError(
            f"theory candidate {candidate_id} has no finite positive {label}"
        )
    return float(value)


def _candidate_parameters(
    candidate: TheoryCandidateEvaluation,
    policy: DifferentialPairTheorySeedPolicy,
) -> dict[str, float]:
    mapping = policy.parameter_mapping
    widths = {
        mapping.input_width: _positive(
            candidate.input_width_um, "input_width_um", candidate.id
        ),
        mapping.load_width: _positive(
            candidate.load_width_um, "load_width_um", candidate.id
        ),
        mapping.tail_width: _positive(
            candidate.tail_width_um, "tail_width_um", candidate.id
        ),
    }
    rounded_widths = {
        name: _quantize_width(value, policy)
        for name, value in widths.items()
    }
    if any(value <= 0.0 for value in rounded_widths.values()):
        raise ValueError("width rounding produced a non-positive theory candidate")
    return rounded_widths | {
        mapping.input_length: _positive(
            candidate.input_length_um, "input_length_um", candidate.id
        ),
        mapping.load_length: _positive(
            candidate.load_length_um, "load_length_um", candidate.id
        ),
        mapping.tail_length: _positive(
            candidate.tail_length_um, "tail_length_um", candidate.id
        ),
    }


def _quantize_width(
    value: float,
    policy: DifferentialPairTheorySeedPolicy,
) -> float:
    """Compile a continuous theory width onto the declared OA/PDK grid."""

    if policy.width_grid_um is None:
        return round(value, policy.width_decimal_places)
    width = Decimal(str(value))
    grid = Decimal(str(policy.width_grid_um))
    grid_steps = (width / grid).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    snapped = grid_steps * grid
    return round(float(snapped), policy.width_decimal_places)


def _ranked_feasible_candidates(
    result: DifferentialPairTheoryResult,
    policy: DifferentialPairTheorySeedPolicy,
) -> list[tuple[TheoryCandidateEvaluation, dict[str, float]]]:
    feasible = [
        candidate
        for candidate in result.candidates
        if candidate.analysis_complete
        and candidate.feasible
        and candidate.objective_value is not None
        and math.isfinite(candidate.objective_value)
    ]
    feasible.sort(key=lambda item: (float(item.objective_value), item.id))
    prepared: list[tuple[TheoryCandidateEvaluation, dict[str, float]]] = []
    seen: set[tuple[tuple[str, float], ...]] = set()
    for candidate in feasible:
        parameters = _candidate_parameters(candidate, policy)
        signature = tuple(sorted(parameters.items()))
        if signature in seen:
            continue
        seen.add(signature)
        prepared.append((candidate, parameters))
    return prepared


def _normalized_log_vectors(
    candidates: list[tuple[TheoryCandidateEvaluation, dict[str, float]]],
) -> list[tuple[float, ...]]:
    names = sorted(candidates[0][1])
    raw = [
        tuple(math.log(candidate[1][name]) for name in names)
        for candidate in candidates
    ]
    columns = list(zip(*raw, strict=True))
    bounds = [(min(column), max(column)) for column in columns]
    return [
        tuple(
            0.0 if high == low else (value - low) / (high - low)
            for value, (low, high) in zip(vector, bounds, strict=True)
        )
        for vector in raw
    ]


def _squared_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True))


def _select_candidates(
    ranked: list[tuple[TheoryCandidateEvaluation, dict[str, float]]],
    policy: DifferentialPairTheorySeedPolicy,
) -> list[tuple[TheoryCandidateEvaluation, dict[str, float]]]:
    if not ranked:
        raise ValueError("theory result contains no feasible seed candidate")
    target = min(policy.maximum_candidates, len(ranked))
    ranked_prefix = min(policy.ranked_candidate_count, target)
    selected_indices = list(range(ranked_prefix))
    if len(selected_indices) == target:
        return [ranked[index] for index in selected_indices]

    vectors = _normalized_log_vectors(ranked)
    remaining = set(range(ranked_prefix, len(ranked)))
    while len(selected_indices) < target:
        scored = [
            (
                min(
                    _squared_distance(vectors[index], vectors[selected])
                    for selected in selected_indices
                ),
                index,
            )
            for index in remaining
        ]
        # Maximum diversity first; the original theory rank and candidate id are
        # deterministic tie breakers rather than another hidden objective.
        _, chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[0]
        selected_indices.append(chosen)
        remaining.remove(chosen)
    return [ranked[index] for index in selected_indices]


def build_theory_seeded_task(
    policy: DifferentialPairTheorySeedPolicy,
    theory_result_path: Path,
    task_template_path: Path,
) -> TaskSpec:
    """Bind a theory result and emit exact tuples for the normal VDA executor."""

    result = DifferentialPairTheoryResult.model_validate_json(
        theory_result_path.read_text(encoding="utf-8")
    )
    if result.request_id != policy.expected_theory_request_id:
        raise ValueError("theory result request id does not match seed policy")
    if result.request_sha256 != policy.expected_theory_request_sha256:
        raise ValueError("theory result request SHA-256 does not match seed policy")
    if result.status is not RunStatus.SUCCEEDED or result.recommended_candidate_id is None:
        raise ValueError("only a successful theory result with a recommendation can seed EDA")
    if not result.optimality_boundary.domain_exhausted:
        raise ValueError("the theory result did not exhaust its declared discrete domain")
    if result.optimality_boundary.continuous_optimum_claim:
        raise ValueError("a theory result claiming a continuous optimum is not accepted")
    if result.optimality_boundary.global_optimum_claim:
        raise ValueError("a theory result claiming a global optimum is not accepted")

    ranked = _ranked_feasible_candidates(result, policy)
    selected = _select_candidates(ranked, policy)
    if selected[0][0].id != result.recommended_candidate_id:
        raise ValueError(
            "theory recommendation does not match the reproducible feasible ranking"
        )

    policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
    source = TheorySeedSource(
        policy_id=policy.id,
        policy_sha256=policy_sha256,
        theory_request_id=result.request_id,
        theory_request_sha256=result.request_sha256,
        theory_result_sha256=_file_sha256(theory_result_path),
        device_data_source=result.device_data_source.value,
        device_data_artifact_id=result.device_data_artifact_id,
        device_data_artifact_sha256=result.device_data_artifact_sha256,
        optimality_classification=result.optimality_boundary.classification,
        declared_theory_combinations=(
            result.optimality_boundary.declared_combinations
        ),
        evaluated_theory_combinations=(
            result.optimality_boundary.evaluated_combinations
        ),
        theory_domain_exhausted=result.optimality_boundary.domain_exhausted,
        selection_strategy=policy.selection_strategy,
        width_quantization=(
            "nearest_grid_half_up"
            if policy.width_grid_um is not None
            else "decimal_places"
        ),
        width_grid_um=policy.width_grid_um,
    )
    candidate_set = TheorySeedCandidateSet(
        source=source,
        candidates=[
            TheorySeedCandidate(
                id=f"theory-seed-{index:03d}",
                source_candidate_id=candidate.id,
                parameters=parameters,
                predicted_metrics=dict(candidate.metrics),
            )
            for index, (candidate, parameters) in enumerate(selected, start=1)
        ],
    )

    raw_template = json.loads(task_template_path.read_text(encoding="utf-8"))
    if not isinstance(raw_template, dict):
        raise ValueError("theory-seed task template must contain one JSON object")
    for name in ("parameter_space", "instance_parameter_space", "theory_seed"):
        if raw_template.get(name):
            raise ValueError(
                f"theory-seed task template must not predeclare {name}"
            )
        raw_template.pop(name, None)
    raw_template["theory_seed"] = candidate_set.model_dump(mode="json")
    task = TaskSpec.model_validate(raw_template)
    if task.operation not in {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}:
        raise ValueError("theory-seed output must be a tuning operation")
    if task.circuit.value != "differential_pair":
        raise ValueError("the current theory seed compiler requires differential_pair")
    if task.pdk_profile != result.pdk_profile:
        raise ValueError("theory result PDK profile does not match task template")
    return task


__all__ = [
    "DifferentialPairTheorySeedPolicy",
    "TheorySeedParameterMapping",
    "build_theory_seeded_task",
]
