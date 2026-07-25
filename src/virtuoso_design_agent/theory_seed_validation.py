"""Audit whether an atomic theory shortlist predicted its real EDA run."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr

from .models import (
    EvidenceSource,
    RunRecord,
    RunStatus,
    SelectionScope,
    StrictModel,
    TaskSpec,
)
from .planner import build_plan


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TheorySeedMetricValidationRule(_FiniteStrictModel):
    predicted_metric: StrictStr = Field(min_length=1)
    measured_metric: StrictStr = Field(min_length=1)
    predicted_scale: float = Field(default=1.0, gt=0.0)
    maximum_absolute_error_percent: float = Field(gt=0.0)


class TheorySeedValidationPolicy(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_task_id: StrictStr = Field(min_length=1, max_length=128)
    expected_task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_eda_feasible_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    require_recommendation_agreement: bool = False
    metric_rules: list[TheorySeedMetricValidationRule] = Field(
        min_length=1,
        max_length=32,
    )
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT


class TheorySeedMetricComparison(_FiniteStrictModel):
    predicted_metric: StrictStr
    measured_metric: StrictStr
    predicted_value: float
    measured_value: float
    signed_error_percent: float
    absolute_error_percent: float = Field(ge=0.0)
    maximum_absolute_error_percent: float = Field(gt=0.0)
    passed: bool
    prediction_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    measurement_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )


class TheorySeedCandidateValidation(_FiniteStrictModel):
    index: int = Field(ge=1)
    theory_seed_candidate_id: StrictStr
    theory_source_candidate_id: StrictStr
    parameters: dict[StrictStr, float]
    eda_feasible: bool
    comparisons: list[TheorySeedMetricComparison]
    prediction_accuracy_passed: bool


class TheorySeedValidationResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: StrictStr
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: StrictStr
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_token: StrictStr
    status: RunStatus
    declared_candidate_count: int = Field(ge=1)
    evaluated_candidate_count: int = Field(ge=1)
    eda_feasible_candidate_count: int = Field(ge=0)
    eda_feasible_fraction: float = Field(ge=0.0, le=1.0)
    theory_recommendation_candidate_id: StrictStr
    eda_selected_candidate_id: StrictStr
    recommendation_agreement: bool
    candidate_generation_gate_passed: bool
    prediction_accuracy_gate_passed: bool
    candidates: list[TheorySeedCandidateValidation]
    theory_prediction_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    eda_measurement_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    validation_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    notes: list[StrictStr] = Field(default_factory=list)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)


def validate_theory_seed_run(
    policy: TheorySeedValidationPolicy,
    task_path: Path,
    run_path: Path,
) -> TheorySeedValidationResult:
    """Validate provenance, shortlist utility, and pointwise prediction error."""

    task_sha256 = _file_sha256(task_path)
    run_sha256 = _file_sha256(run_path)
    if task_sha256 != policy.expected_task_sha256:
        raise ValueError("theory-seed validation task SHA-256 mismatch")
    if run_sha256 != policy.expected_run_sha256:
        raise ValueError("theory-seed validation run SHA-256 mismatch")

    task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
    run = RunRecord.model_validate_json(run_path.read_text(encoding="utf-8"))
    if task.id != policy.expected_task_id or run.task_id != task.id:
        raise ValueError("theory-seed validation task identity mismatch")
    if task.theory_seed is None:
        raise ValueError("validation task has no theory_seed candidate set")
    if run.status is not RunStatus.SUCCEEDED:
        raise ValueError("only a successful real EDA run can validate theory seeds")
    if run.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("theory-seed validation requires the real Bridge adapter")
    expected_token = build_plan(task).confirmation_token
    if run.plan_token != expected_token:
        raise ValueError("theory-seed validation plan token mismatch")

    audit = run.search_audit
    declared = len(task.theory_seed.candidates)
    if (
        audit is None
        or not audit.domain_exhausted
        or audit.selection_scope
        is not SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
        or audit.declared_candidate_count != declared
        or audit.completed_candidate_count != declared
        or audit.theory_seed_source != task.theory_seed.source
    ):
        raise ValueError("run did not exhaust the exact theory-seeded discrete domain")
    if len(run.candidates) != declared:
        raise ValueError("run candidate count does not match the theory seed task")

    validations: list[TheorySeedCandidateValidation] = []
    for index, (seed, candidate) in enumerate(
        zip(task.theory_seed.candidates, run.candidates, strict=True),
        start=1,
    ):
        if candidate.index != index:
            raise ValueError("run candidate order does not match the theory seed task")
        if (
            candidate.theory_seed_candidate_id != seed.id
            or candidate.theory_seed_source_candidate_id != seed.source_candidate_id
            or candidate.theory_seed_predicted_metrics != seed.predicted_metrics
            or candidate.theory_seed_evidence_source
            is not EvidenceSource.SOFTWARE_INFERENCE
            or candidate.evidence_source is not EvidenceSource.EDA_RESULT
        ):
            raise ValueError("run candidate theory/EDA provenance mismatch")
        for name, expected in seed.parameters.items():
            actual = candidate.parameters.get(name)
            if actual is None or not _same_number(actual, expected):
                raise ValueError(
                    f"run candidate {index} parameter {name!r} does not match its seed"
                )

        comparisons: list[TheorySeedMetricComparison] = []
        for rule in policy.metric_rules:
            predicted_raw = seed.predicted_metrics.get(rule.predicted_metric)
            measured = candidate.metrics.get(rule.measured_metric)
            if predicted_raw is None or measured is None:
                raise ValueError(
                    f"candidate {index} is missing validation metric "
                    f"{rule.predicted_metric!r}/{rule.measured_metric!r}"
                )
            if (
                candidate.metric_sources.get(rule.measured_metric)
                is not EvidenceSource.EDA_RESULT
            ):
                raise ValueError(
                    f"candidate {index} metric {rule.measured_metric!r} is not eda_result"
                )
            predicted = predicted_raw * rule.predicted_scale
            if not math.isfinite(predicted) or predicted == 0.0:
                raise ValueError("theory validation prediction must be finite and nonzero")
            signed_error = (measured - predicted) / abs(predicted) * 100.0
            absolute_error = abs(signed_error)
            comparisons.append(
                TheorySeedMetricComparison(
                    predicted_metric=rule.predicted_metric,
                    measured_metric=rule.measured_metric,
                    predicted_value=predicted,
                    measured_value=measured,
                    signed_error_percent=signed_error,
                    absolute_error_percent=absolute_error,
                    maximum_absolute_error_percent=(
                        rule.maximum_absolute_error_percent
                    ),
                    passed=(
                        absolute_error <= rule.maximum_absolute_error_percent
                    ),
                )
            )
        validations.append(
            TheorySeedCandidateValidation(
                index=index,
                theory_seed_candidate_id=seed.id,
                theory_source_candidate_id=seed.source_candidate_id,
                parameters=dict(candidate.parameters),
                eda_feasible=candidate.feasible,
                comparisons=comparisons,
                prediction_accuracy_passed=all(item.passed for item in comparisons),
            )
        )

    if run.selected_parameters is None:
        raise ValueError("successful theory-seed run has no selected parameters")
    selected = [
        candidate
        for candidate in run.candidates
        if all(
            name in candidate.parameters
            and _same_number(candidate.parameters[name], value)
            for name, value in run.selected_parameters.items()
        )
    ]
    if len(selected) != 1 or not selected[0].feasible:
        raise ValueError("run selection does not identify one feasible EDA candidate")

    feasible_count = sum(item.eda_feasible for item in validations)
    feasible_fraction = feasible_count / declared
    selected_id = str(selected[0].theory_seed_candidate_id)
    recommendation_id = task.theory_seed.candidates[0].id
    recommendation_agreement = selected_id == recommendation_id
    generation_passed = feasible_fraction >= policy.minimum_eda_feasible_fraction
    if policy.require_recommendation_agreement:
        generation_passed = generation_passed and recommendation_agreement
    prediction_passed = all(
        item.prediction_accuracy_passed for item in validations
    )
    status = (
        RunStatus.SUCCEEDED
        if generation_passed and prediction_passed
        else RunStatus.PARTIAL
    )
    failed_comparisons = sum(
        not comparison.passed
        for item in validations
        for comparison in item.comparisons
    )
    notes = [
        (
            "theory recommendation matched the EDA-selected candidate"
            if recommendation_agreement
            else "theory recommendation did not match the EDA-selected candidate"
        ),
        (
            f"{failed_comparisons} pointwise metric comparisons exceeded their "
            "declared error limits"
        ),
    ]
    return TheorySeedValidationResult(
        policy_id=policy.id,
        policy_sha256=_canonical_sha256(policy.model_dump(mode="json")),
        task_id=task.id,
        task_sha256=task_sha256,
        run_sha256=run_sha256,
        plan_token=run.plan_token,
        status=status,
        declared_candidate_count=declared,
        evaluated_candidate_count=len(run.candidates),
        eda_feasible_candidate_count=feasible_count,
        eda_feasible_fraction=feasible_fraction,
        theory_recommendation_candidate_id=recommendation_id,
        eda_selected_candidate_id=selected_id,
        recommendation_agreement=recommendation_agreement,
        candidate_generation_gate_passed=generation_passed,
        prediction_accuracy_gate_passed=prediction_passed,
        candidates=validations,
        notes=notes,
    )


__all__ = [
    "TheorySeedCandidateValidation",
    "TheorySeedMetricComparison",
    "TheorySeedMetricValidationRule",
    "TheorySeedValidationPolicy",
    "TheorySeedValidationResult",
    "validate_theory_seed_run",
]
