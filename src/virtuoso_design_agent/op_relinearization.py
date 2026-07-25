"""Fit and validate a local EDA response model around one real operating point.

The model is deliberately first order and local.  It consumes completed real-Bridge
candidate evidence, reserves explicit held-out points, and only emits an executable
atomic candidate set when every declared holdout error gate passes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .linear_network import solve_complex_linear_system
from .metrics import evaluate_constraints
from .models import (
    AtomicCandidate,
    AtomicCandidateSet,
    CandidateEvaluation,
    CandidateSetSource,
    ConstraintEvaluation,
    EvidenceSource,
    MetricConstraint,
    Objective,
    ObjectiveGoal,
    RunRecord,
    RunStatus,
    SelectionScope,
    StrictModel,
    TaskSpec,
)
from .planner import build_plan


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RelinearizationParameter(_FiniteStrictModel):
    name: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    minimum: float = Field(gt=0.0)
    maximum: float = Field(gt=0.0)
    proposal_step: float = Field(gt=0.0)
    steps_each_side: int = Field(default=1, ge=1, le=4)
    quantization_grid: float | None = Field(default=None, gt=0.0)
    decimal_places: int = Field(default=9, ge=3, le=12)

    @model_validator(mode="after")
    def validate_bounds(self) -> "RelinearizationParameter":
        if self.maximum <= self.minimum:
            raise ValueError("relinearization parameter maximum must exceed minimum")
        return self


class RelinearizationMetric(_FiniteStrictModel):
    metric: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    role: Literal["operating_point", "performance"]
    maximum_training_error_percent: float | None = Field(default=None, gt=0.0)
    maximum_holdout_error_percent: float = Field(gt=0.0)
    relative_error_floor: float = Field(default=1e-30, gt=0.0)


class OperatingPointRelinearizationPolicy(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_source_task_id: StrictStr = Field(min_length=1, max_length=128)
    expected_source_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_candidate_index: int = Field(ge=1)
    training_candidate_indices: list[int] = Field(min_length=2, max_length=64)
    holdout_candidate_indices: list[int] = Field(min_length=1, max_length=32)
    parameters: list[RelinearizationParameter] = Field(min_length=1, max_length=8)
    metrics: list[RelinearizationMetric] = Field(min_length=2, max_length=32)
    constraints: list[MetricConstraint] = Field(min_length=1, max_length=32)
    objective: Objective
    maximum_candidates: int = Field(default=6, ge=2, le=32)
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT

    @model_validator(mode="after")
    def validate_declared_model(self) -> "OperatingPointRelinearizationPolicy":
        training = self.training_candidate_indices
        holdout = self.holdout_candidate_indices
        if len(training) != len(set(training)) or len(holdout) != len(set(holdout)):
            raise ValueError("training and holdout candidate indices must be unique")
        if set(training) & set(holdout):
            raise ValueError("training and holdout candidate indices must be disjoint")
        if self.anchor_candidate_index not in training:
            raise ValueError("anchor candidate must belong to the training set")
        if len(training) < len(self.parameters) + 1:
            raise ValueError(
                "training set needs the anchor plus at least one independent point "
                "per parameter"
            )
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("relinearization parameters must be unique")
        metric_names = [item.metric for item in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("relinearization metrics must be unique")
        if {item.role for item in self.metrics} != {
            "operating_point",
            "performance",
        }:
            raise ValueError(
                "relinearization requires operating-point and performance metrics"
            )
        required = {item.metric for item in self.constraints} | {
            self.objective.metric
        }
        missing = sorted(required - set(metric_names))
        if missing:
            raise ValueError(
                "constraints/objective require undeclared model metrics: "
                + ", ".join(missing)
            )
        combinations = math.prod(
            2 * item.steps_each_side + 1 for item in self.parameters
        )
        if combinations > 4096:
            raise ValueError("local proposal grid exceeds 4096 combinations")
        return self


class LocalMetricModel(_FiniteStrictModel):
    metric: StrictStr
    role: Literal["operating_point", "performance"]
    anchor_value: float
    response_scale: float = Field(gt=0.0)
    relative_error_floor: float = Field(default=1e-30, gt=0.0)
    normalized_sensitivities: dict[StrictStr, float]
    maximum_training_error_percent: float = Field(ge=0.0)
    allowed_training_error_percent: float = Field(gt=0.0)
    training_gate_passed: bool
    maximum_holdout_error_percent: float = Field(ge=0.0)
    allowed_holdout_error_percent: float = Field(gt=0.0)
    holdout_gate_passed: bool


class RelinearizationComparison(_FiniteStrictModel):
    candidate_index: int = Field(ge=1)
    metric: StrictStr
    actual_value: float
    predicted_value: float
    signed_error_percent: float
    absolute_error_percent: float = Field(ge=0.0)
    maximum_absolute_error_percent: float = Field(gt=0.0)
    passed: bool
    actual_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    predicted_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class RelinearizedProposal(_FiniteStrictModel):
    parameters: dict[StrictStr, float]
    predicted_metrics: dict[StrictStr, float]
    constraints: list[ConstraintEvaluation]
    predicted_feasible: bool
    total_normalized_violation: float = Field(ge=0.0)
    predicted_objective_value: float
    previously_evaluated: bool


class OperatingPointRelinearizationResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: StrictStr
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_id: StrictStr
    source_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RunStatus
    gate_passed: bool
    anchor_candidate_index: int = Field(ge=1)
    training_candidate_indices: list[int]
    holdout_candidate_indices: list[int]
    holdout_parameter_coverage: dict[StrictStr, bool] = Field(default_factory=dict)
    fixed_parameters: dict[StrictStr, float]
    fixed_instance_parameters: dict[StrictStr, dict[StrictStr, StrictStr]]
    constraints: list[MetricConstraint]
    objective: Objective
    models: list[LocalMetricModel]
    holdout_comparisons: list[RelinearizationComparison]
    proposals: list[RelinearizedProposal]
    candidate_set: AtomicCandidateSet | None = None
    source_measurement_evidence: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    model_and_selection_evidence: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    notes: list[StrictStr] = Field(default_factory=list)


class RelinearizationRunMetricComparison(_FiniteStrictModel):
    metric: StrictStr
    predicted_value: float
    measured_value: float
    signed_error_percent: float
    absolute_error_percent: float = Field(ge=0.0)
    maximum_absolute_error_percent: float = Field(gt=0.0)
    error_normalization_floor: float = Field(gt=0.0)
    error_normalization_source: Literal["result_model", "hash_bound_policy"]
    passed: bool
    prediction_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    measurement_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )


class RelinearizationRunCandidateValidation(_FiniteStrictModel):
    index: int = Field(ge=1)
    atomic_candidate_id: StrictStr
    parameters: dict[StrictStr, float]
    instance_parameters: dict[StrictStr, dict[StrictStr, StrictStr]]
    predicted_screening_feasible: bool
    eda_feasible: bool
    comparisons: list[RelinearizationRunMetricComparison]
    prediction_accuracy_passed: bool


class OperatingPointRelinearizationRunValidation(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    relinearization_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bound_policy_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    task_id: StrictStr
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_token: StrictStr
    status: RunStatus
    gate_passed: bool
    declared_candidate_count: int = Field(ge=1)
    evaluated_candidate_count: int = Field(ge=1)
    eda_feasible_candidate_count: int = Field(ge=0)
    eda_feasible_fraction: float = Field(ge=0.0, le=1.0)
    predicted_recommendation_candidate_id: StrictStr
    eda_selected_candidate_id: StrictStr
    recommendation_agreement: bool
    candidate_execution_gate_passed: bool
    prediction_accuracy_gate_passed: bool
    candidates: list[RelinearizationRunCandidateValidation]
    prediction_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
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


def _candidate_map(run: RunRecord) -> dict[int, CandidateEvaluation]:
    result = {candidate.index: candidate for candidate in run.candidates}
    if len(result) != len(run.candidates):
        raise ValueError("source run repeats a candidate index")
    return result


def _validate_source_candidate(
    candidate: CandidateEvaluation,
    policy: OperatingPointRelinearizationPolicy,
) -> None:
    if (
        candidate.evidence_source is not EvidenceSource.EDA_RESULT
        or not candidate.analysis_complete
    ):
        raise ValueError(
            f"source candidate {candidate.index} is not complete eda_result evidence"
        )
    for metric in policy.metrics:
        value = candidate.metrics.get(metric.metric)
        if value is None or not math.isfinite(value):
            raise ValueError(
                f"source candidate {candidate.index} lacks metric {metric.metric}"
            )
        if candidate.metric_sources.get(metric.metric) is not EvidenceSource.EDA_RESULT:
            raise ValueError(
                f"source candidate {candidate.index} metric {metric.metric} is not "
                "eda_result"
            )
    for parameter in policy.parameters:
        value = candidate.parameters.get(parameter.name)
        if value is None or not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                f"source candidate {candidate.index} lacks positive parameter "
                f"{parameter.name}"
            )


def _constant_inputs(
    candidates: list[CandidateEvaluation],
    modeled_names: set[str],
) -> tuple[dict[str, float], dict[str, dict[str, str]]]:
    parameter_names = set(candidates[0].parameters)
    if any(set(candidate.parameters) != parameter_names for candidate in candidates[1:]):
        raise ValueError("source candidates do not expose the same parameter fields")
    fixed: dict[str, float] = {}
    for name in sorted(parameter_names - modeled_names):
        values = [candidate.parameters[name] for candidate in candidates]
        if any(
            not math.isclose(value, values[0], rel_tol=1e-12, abs_tol=1e-15)
            for value in values[1:]
        ):
            raise ValueError(
                f"unmodeled parameter {name!r} varies across training/holdout points"
            )
        fixed[name] = float(values[0])
    instance_parameters = candidates[0].instance_parameters
    if any(
        candidate.instance_parameters != instance_parameters
        for candidate in candidates[1:]
    ):
        raise ValueError(
            "raw instance parameters vary across source points; declare a numeric "
            "semantic mapping before relinearization"
        )
    return fixed, {
        instance: dict(values)
        for instance, values in instance_parameters.items()
    }


def _features(
    candidate: CandidateEvaluation,
    anchor: CandidateEvaluation,
    parameters: list[RelinearizationParameter],
) -> list[float]:
    return [
        (candidate.parameters[item.name] - anchor.parameters[item.name])
        / item.proposal_step
        for item in parameters
    ]


def _fit_slopes(features: list[list[float]], responses: list[float]) -> list[float]:
    width = len(features[0])
    normal = [
        [
            sum(row[left] * row[right] for row in features)
            for right in range(width)
        ]
        for left in range(width)
    ]
    rhs = [
        sum(row[column] * response for row, response in zip(features, responses))
        for column in range(width)
    ]
    try:
        solved = solve_complex_linear_system(normal, rhs)
    except ValueError as exc:
        raise ValueError(
            "training perturbations do not independently span every declared parameter"
        ) from exc
    if any(abs(value.imag) > 1e-10 for value in solved):
        raise ValueError("real relinearization unexpectedly produced complex slopes")
    return [float(value.real) for value in solved]


def _predict_metric(
    model: LocalMetricModel,
    features: list[float],
    parameter_names: list[str],
) -> float:
    delta = sum(
        model.normalized_sensitivities[name] * value
        for name, value in zip(parameter_names, features, strict=True)
    )
    predicted = model.anchor_value + model.response_scale * delta
    if not math.isfinite(predicted):
        raise ValueError(f"local model produced nonfinite {model.metric}")
    return predicted


def _error_percent(actual: float, predicted: float, floor: float) -> tuple[float, float]:
    signed = (predicted - actual) / max(abs(actual), floor) * 100.0
    return signed, abs(signed)


def _quantize(value: float, parameter: RelinearizationParameter) -> float:
    if parameter.quantization_grid is None:
        return round(value, parameter.decimal_places)
    number = Decimal(str(value))
    grid = Decimal(str(parameter.quantization_grid))
    steps = (number / grid).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return round(float(steps * grid), parameter.decimal_places)


def _proposal_values(
    anchor_value: float,
    parameter: RelinearizationParameter,
) -> list[float]:
    if not parameter.minimum <= anchor_value <= parameter.maximum:
        raise ValueError(
            f"anchor {parameter.name} lies outside the declared trust bounds"
        )
    values = {
        _quantize(anchor_value + offset * parameter.proposal_step, parameter)
        for offset in range(-parameter.steps_each_side, parameter.steps_each_side + 1)
        if parameter.minimum
        <= anchor_value + offset * parameter.proposal_step
        <= parameter.maximum
    }
    if not any(
        math.isclose(value, anchor_value, rel_tol=1e-12, abs_tol=1e-15)
        for value in values
    ):
        raise ValueError(
            f"anchor {parameter.name} is not representable on its proposal grid"
        )
    return sorted(values)


def _parameter_signature(
    values: dict[str, float],
    parameters: list[RelinearizationParameter],
) -> tuple[float, ...]:
    return tuple(_quantize(values[item.name], item) for item in parameters)


def _proposal_rank(
    policy: OperatingPointRelinearizationPolicy,
    proposal: RelinearizedProposal,
) -> tuple[float, float, float, tuple[tuple[str, float], ...]]:
    objective = proposal.predicted_objective_value
    objective_rank = (
        objective
        if policy.objective.goal is ObjectiveGoal.MINIMIZE
        else -objective
    )
    return (
        0.0 if proposal.predicted_feasible else 1.0,
        proposal.total_normalized_violation,
        objective_rank,
        tuple(sorted(proposal.parameters.items())),
    )


def relinearize_operating_point(
    policy: OperatingPointRelinearizationPolicy,
    source_run_path: Path,
) -> OperatingPointRelinearizationResult:
    """Fit a forced-through-anchor local model and validate explicit holdouts."""

    source_sha256 = _file_sha256(source_run_path)
    if source_sha256 != policy.expected_source_run_sha256:
        raise ValueError("source run SHA-256 does not match relinearization policy")
    run = RunRecord.model_validate_json(source_run_path.read_text(encoding="utf-8"))
    if run.task_id != policy.expected_source_task_id:
        raise ValueError("source run task id does not match relinearization policy")
    if run.adapter != "virtuoso-bridge-subprocess" or run.status is not RunStatus.SUCCEEDED:
        raise ValueError("relinearization requires a successful real-Bridge run")

    by_index = _candidate_map(run)
    required_indices = set(policy.training_candidate_indices) | set(
        policy.holdout_candidate_indices
    )
    missing = sorted(required_indices - set(by_index))
    if missing:
        raise ValueError(f"source run is missing candidate indices {missing}")
    selected = [by_index[index] for index in sorted(required_indices)]
    for candidate in selected:
        _validate_source_candidate(candidate, policy)

    anchor = by_index[policy.anchor_candidate_index]
    parameter_names = [item.name for item in policy.parameters]
    fixed_parameters, fixed_instance_parameters = _constant_inputs(
        selected, set(parameter_names)
    )
    training = [by_index[index] for index in policy.training_candidate_indices]
    holdout = [by_index[index] for index in policy.holdout_candidate_indices]
    training_features = [
        _features(candidate, anchor, policy.parameters) for candidate in training
    ]
    holdout_features = [
        _features(candidate, anchor, policy.parameters) for candidate in holdout
    ]
    holdout_parameter_coverage = {
        parameter.name: any(
            not math.isclose(row[column], 0.0, rel_tol=0.0, abs_tol=1e-12)
            for row in holdout_features
        )
        for column, parameter in enumerate(policy.parameters)
    }
    uncovered = [
        name for name, covered in holdout_parameter_coverage.items() if not covered
    ]
    if uncovered:
        raise ValueError(
            "held-out points do not perturb declared parameter directions: "
            + ", ".join(uncovered)
        )

    models: list[LocalMetricModel] = []
    comparisons: list[RelinearizationComparison] = []
    for metric in policy.metrics:
        anchor_value = anchor.metrics[metric.metric]
        response_scale = max(abs(anchor_value), metric.relative_error_floor)
        training_responses = [
            (candidate.metrics[metric.metric] - anchor_value) / response_scale
            for candidate in training
        ]
        slopes = _fit_slopes(training_features, training_responses)
        provisional = LocalMetricModel(
            metric=metric.metric,
            role=metric.role,
            anchor_value=anchor_value,
            response_scale=response_scale,
            relative_error_floor=metric.relative_error_floor,
            normalized_sensitivities=dict(zip(parameter_names, slopes, strict=True)),
            maximum_training_error_percent=0.0,
            allowed_training_error_percent=(
                metric.maximum_training_error_percent
                or metric.maximum_holdout_error_percent
            ),
            training_gate_passed=False,
            maximum_holdout_error_percent=0.0,
            allowed_holdout_error_percent=metric.maximum_holdout_error_percent,
            holdout_gate_passed=False,
        )
        training_errors = [
            _error_percent(
                candidate.metrics[metric.metric],
                _predict_metric(
                    provisional,
                    _features(candidate, anchor, policy.parameters),
                    parameter_names,
                ),
                metric.relative_error_floor,
            )[1]
            for candidate in training
        ]
        holdout_errors: list[float] = []
        for candidate in holdout:
            actual = candidate.metrics[metric.metric]
            predicted = _predict_metric(
                provisional,
                _features(candidate, anchor, policy.parameters),
                parameter_names,
            )
            signed_error, absolute_error = _error_percent(
                actual, predicted, metric.relative_error_floor
            )
            holdout_errors.append(absolute_error)
            comparisons.append(
                RelinearizationComparison(
                    candidate_index=candidate.index,
                    metric=metric.metric,
                    actual_value=actual,
                    predicted_value=predicted,
                    signed_error_percent=signed_error,
                    absolute_error_percent=absolute_error,
                    maximum_absolute_error_percent=(
                        metric.maximum_holdout_error_percent
                    ),
                    passed=(
                        absolute_error <= metric.maximum_holdout_error_percent
                    ),
                )
            )
        maximum_holdout = max(holdout_errors)
        maximum_training = max(training_errors)
        allowed_training = (
            metric.maximum_training_error_percent
            or metric.maximum_holdout_error_percent
        )
        models.append(
            provisional.model_copy(
                update={
                    "maximum_training_error_percent": maximum_training,
                    "training_gate_passed": (
                        maximum_training <= allowed_training
                    ),
                    "maximum_holdout_error_percent": maximum_holdout,
                    "holdout_gate_passed": (
                        maximum_holdout
                        <= metric.maximum_holdout_error_percent
                    ),
                }
            )
        )

    model_by_metric = {model.metric: model for model in models}
    observed = {
        _parameter_signature(candidate.parameters, policy.parameters)
        for candidate in run.candidates
        if all(name in candidate.parameters for name in parameter_names)
    }
    anchor_signature = _parameter_signature(anchor.parameters, policy.parameters)
    proposal_axes = [
        _proposal_values(anchor.parameters[item.name], item)
        for item in policy.parameters
    ]
    proposals: list[RelinearizedProposal] = []
    for values in itertools.product(*proposal_axes):
        signature = tuple(float(value) for value in values)
        if signature in observed and signature != anchor_signature:
            continue
        parameters = dict(zip(parameter_names, signature, strict=True))
        synthetic = anchor.model_copy(
            update={"parameters": dict(anchor.parameters) | parameters}
        )
        features = _features(synthetic, anchor, policy.parameters)
        predicted_metrics = {
            metric.metric: _predict_metric(
                model_by_metric[metric.metric], features, parameter_names
            )
            for metric in policy.metrics
        }
        constraints = evaluate_constraints(predicted_metrics, policy.constraints)
        proposals.append(
            RelinearizedProposal(
                parameters=parameters,
                predicted_metrics=predicted_metrics,
                constraints=constraints,
                predicted_feasible=all(item.passed for item in constraints),
                total_normalized_violation=sum(
                    item.normalized_violation for item in constraints
                ),
                predicted_objective_value=predicted_metrics[policy.objective.metric],
                previously_evaluated=(signature == anchor_signature),
            )
        )
    proposals.sort(key=lambda item: _proposal_rank(policy, item))

    validation_passed = all(
        model.training_gate_passed and model.holdout_gate_passed
        for model in models
    )
    feasible_proposals = [item for item in proposals if item.predicted_feasible]
    candidate_set: AtomicCandidateSet | None = None
    notes = [
        "every declared parameter is perturbed by at least one held-out point"
    ]
    failed_training = [
        model.metric for model in models if not model.training_gate_passed
    ]
    failed_holdout = [
        model.metric for model in models if not model.holdout_gate_passed
    ]
    if failed_training:
        notes.append(
            "training error gate failed for: " + ", ".join(failed_training)
        )
    if failed_holdout:
        notes.append(
            "held-out error gate failed for: " + ", ".join(failed_holdout)
        )
    if validation_passed and not feasible_proposals:
        notes.append("validated local model produced no predicted-feasible proposal")
    elif validation_passed:
        anchor_proposal = next(
            item for item in proposals if item.previously_evaluated
        )
        selected = [anchor_proposal]
        selected.extend(
            item
            for item in proposals
            if item is not anchor_proposal
        )
        selected = selected[: policy.maximum_candidates]
        policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
        candidate_set = AtomicCandidateSet(
            source=CandidateSetSource(
                generator="vda.op_relinearization",
                id=policy.id,
                bindings={
                    "policy_sha256": policy_sha256,
                    "source_run_sha256": source_sha256,
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            ),
            candidates=[
                AtomicCandidate(
                    id=f"op-local-{index:03d}",
                    parameters=dict(proposal.parameters),
                    predicted_metrics=dict(proposal.predicted_metrics),
                )
                for index, proposal in enumerate(selected, start=1)
            ],
        )
        notes.append(
            "candidate set starts with the measured anchor, followed by locally "
            "ranked proposals; final ranking still belongs to EDA"
        )

    policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
    gate_passed = validation_passed and candidate_set is not None
    return OperatingPointRelinearizationResult(
        policy_id=policy.id,
        policy_sha256=policy_sha256,
        source_task_id=run.task_id,
        source_run_sha256=source_sha256,
        status=RunStatus.SUCCEEDED if gate_passed else RunStatus.PARTIAL,
        gate_passed=gate_passed,
        anchor_candidate_index=policy.anchor_candidate_index,
        training_candidate_indices=list(policy.training_candidate_indices),
        holdout_candidate_indices=list(policy.holdout_candidate_indices),
        holdout_parameter_coverage=holdout_parameter_coverage,
        fixed_parameters=fixed_parameters,
        fixed_instance_parameters=fixed_instance_parameters,
        constraints=list(policy.constraints),
        objective=policy.objective,
        models=models,
        holdout_comparisons=comparisons,
        proposals=proposals,
        candidate_set=candidate_set,
        notes=notes,
    )


def _same_numeric_mapping(
    left: dict[str, float], right: dict[str, float]
) -> bool:
    return set(left) == set(right) and all(
        math.isclose(left[name], right[name], rel_tol=1e-12, abs_tol=1e-15)
        for name in left
    )


def _merged_instance_parameters(
    task: TaskSpec, candidate: AtomicCandidate
) -> dict[str, dict[str, str]]:
    merged = {
        update.instance: dict(update.parameters)
        for update in task.instance_parameter_updates
    }
    for update in candidate.instance_parameter_updates:
        merged.setdefault(update.instance, {}).update(update.parameters)
    return merged


def _load_bound_validation_policy(
    result: OperatingPointRelinearizationResult,
    policy_path: Path | None,
) -> tuple[OperatingPointRelinearizationPolicy | None, str | None]:
    if policy_path is None:
        return None, None
    policy = OperatingPointRelinearizationPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
    if policy_sha256 != result.policy_sha256:
        raise ValueError("validation policy does not match the result policy hash")
    if (
        policy.id != result.policy_id
        or policy.expected_source_task_id != result.source_task_id
        or policy.expected_source_run_sha256 != result.source_run_sha256
        or policy.constraints != result.constraints
        or policy.objective != result.objective
    ):
        raise ValueError("validation policy does not match result provenance")
    return policy, policy_sha256


def _validation_error_floor(
    model: LocalMetricModel,
    policy_metric: RelinearizationMetric | None,
) -> tuple[float, Literal["result_model", "hash_bound_policy"]]:
    if "relative_error_floor" in model.model_fields_set:
        if policy_metric is not None and not math.isclose(
            model.relative_error_floor,
            policy_metric.relative_error_floor,
            rel_tol=1e-12,
            abs_tol=1e-30,
        ):
            raise ValueError(
                f"model/policy relative-error floor mismatch for {model.metric}"
            )
        return model.relative_error_floor, "result_model"
    if policy_metric is None:
        raise ValueError(
            "legacy relinearization result omits relative_error_floor; pass the "
            "exact hash-bound policy with --policy"
        )
    return policy_metric.relative_error_floor, "hash_bound_policy"


def validate_relinearization_run(
    result_path: Path,
    task_path: Path,
    run_path: Path,
    policy_path: Path | None = None,
) -> OperatingPointRelinearizationRunValidation:
    """Audit a compiled local candidate set against its exhausted real EDA run."""

    result_sha256 = _file_sha256(result_path)
    task_sha256 = _file_sha256(task_path)
    run_sha256 = _file_sha256(run_path)
    result = OperatingPointRelinearizationResult.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
    run = RunRecord.model_validate_json(run_path.read_text(encoding="utf-8"))

    if (
        result.status is not RunStatus.SUCCEEDED
        or not result.gate_passed
        or result.candidate_set is None
    ):
        raise ValueError("run validation requires a passed relinearization result")
    if task.candidate_set is None:
        raise ValueError("run validation task has no atomic candidate set")
    task_source = task.candidate_set.source
    result_source = result.candidate_set.source
    if task_source.bindings.get("relinearization_result_sha256") != result_sha256:
        raise ValueError("task does not bind the exact relinearization result")
    if (
        task_source.generator != result_source.generator
        or task_source.id != result_source.id
        or task_source.evidence_source is not EvidenceSource.SOFTWARE_INFERENCE
        or any(
            task_source.bindings.get(name) != value
            for name, value in result_source.bindings.items()
        )
        or task.candidate_set.candidates != result.candidate_set.candidates
    ):
        raise ValueError("task candidate set does not match relinearization result")
    if run.task_id != task.id:
        raise ValueError("run/task identity mismatch")
    if run.adapter != "virtuoso-bridge-subprocess" or run.status is not RunStatus.SUCCEEDED:
        raise ValueError("run validation requires a successful real-Bridge run")
    expected_token = build_plan(task).confirmation_token
    if run.plan_token != expected_token:
        raise ValueError("run validation plan token mismatch")

    declared = len(task.candidate_set.candidates)
    audit = run.search_audit
    if (
        audit is None
        or not audit.domain_exhausted
        or audit.selection_scope
        is not SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
        or audit.declared_candidate_count != declared
        or audit.attempted_candidate_count != declared
        or audit.completed_candidate_count != declared
        or audit.candidate_set_source != task_source
        or len(run.candidates) != declared
    ):
        raise ValueError("run did not exhaust the exact atomic candidate domain")

    models = {model.metric: model for model in result.models}
    if len(models) != len(result.models):
        raise ValueError("relinearization result repeats a metric model")
    bound_policy, bound_policy_sha256 = _load_bound_validation_policy(
        result, policy_path
    )
    policy_metrics = (
        {metric.metric: metric for metric in bound_policy.metrics}
        if bound_policy is not None
        else {}
    )
    if bound_policy is not None and set(policy_metrics) != set(models):
        raise ValueError("validation policy metric set does not match result models")
    legacy_floor_metrics: list[str] = []
    error_floors: dict[
        str, tuple[float, Literal["result_model", "hash_bound_policy"]]
    ] = {}
    for metric, model in models.items():
        policy_metric = policy_metrics.get(metric)
        floor, source = _validation_error_floor(model, policy_metric)
        if policy_metric is not None:
            expected_scale = max(abs(model.anchor_value), floor)
            expected_training_limit = (
                policy_metric.maximum_training_error_percent
                or policy_metric.maximum_holdout_error_percent
            )
            if (
                model.role != policy_metric.role
                or not math.isclose(
                    model.response_scale,
                    expected_scale,
                    rel_tol=1e-12,
                    abs_tol=1e-30,
                )
                or not math.isclose(
                    model.allowed_training_error_percent,
                    expected_training_limit,
                    rel_tol=1e-12,
                    abs_tol=1e-15,
                )
                or not math.isclose(
                    model.allowed_holdout_error_percent,
                    policy_metric.maximum_holdout_error_percent,
                    rel_tol=1e-12,
                    abs_tol=1e-15,
                )
            ):
                raise ValueError(f"validation policy/model mismatch for {metric}")
        if source == "hash_bound_policy":
            legacy_floor_metrics.append(metric)
        error_floors[metric] = (floor, source)
    validations: list[RelinearizationRunCandidateValidation] = []
    predicted_feasible: list[tuple[str, float]] = []
    for index, (atomic, measured) in enumerate(
        zip(task.candidate_set.candidates, run.candidates, strict=True),
        start=1,
    ):
        expected_parameters = dict(task.parameters)
        expected_parameters.update(atomic.parameters)
        expected_instance_parameters = _merged_instance_parameters(task, atomic)
        if measured.index != index:
            raise ValueError("run candidate order does not match the atomic task")
        if (
            measured.atomic_candidate_id != atomic.id
            or measured.atomic_candidate_predicted_metrics != atomic.predicted_metrics
            or measured.atomic_candidate_evidence_source
            is not EvidenceSource.SOFTWARE_INFERENCE
            or measured.evidence_source is not EvidenceSource.EDA_RESULT
            or not measured.analysis_complete
            or not _same_numeric_mapping(measured.parameters, expected_parameters)
            or measured.instance_parameters != expected_instance_parameters
        ):
            raise ValueError(f"run candidate {index} atomic provenance mismatch")

        comparisons: list[RelinearizationRunMetricComparison] = []
        for metric, model in models.items():
            predicted = atomic.predicted_metrics.get(metric)
            actual = measured.metrics.get(metric)
            if predicted is None or actual is None:
                raise ValueError(
                    f"candidate {index} is missing modeled metric {metric!r}"
                )
            if measured.metric_sources.get(metric) is not EvidenceSource.EDA_RESULT:
                raise ValueError(
                    f"candidate {index} metric {metric!r} is not eda_result"
                )
            error_floor, error_floor_source = error_floors[metric]
            signed_error, absolute_error = _error_percent(
                actual,
                predicted,
                error_floor,
            )
            comparisons.append(
                RelinearizationRunMetricComparison(
                    metric=metric,
                    predicted_value=predicted,
                    measured_value=actual,
                    signed_error_percent=signed_error,
                    absolute_error_percent=absolute_error,
                    maximum_absolute_error_percent=(
                        model.allowed_holdout_error_percent
                    ),
                    error_normalization_floor=error_floor,
                    error_normalization_source=error_floor_source,
                    passed=(
                        absolute_error <= model.allowed_holdout_error_percent
                    ),
                )
            )

        screening = evaluate_constraints(atomic.predicted_metrics, result.constraints)
        screening_feasible = all(item.passed for item in screening)
        if screening_feasible:
            predicted_objective = atomic.predicted_metrics.get(result.objective.metric)
            if predicted_objective is None:
                raise ValueError("atomic candidate is missing the modeled objective")
            predicted_feasible.append((atomic.id, predicted_objective))
        validations.append(
            RelinearizationRunCandidateValidation(
                index=index,
                atomic_candidate_id=atomic.id,
                parameters=dict(atomic.parameters),
                instance_parameters=atomic.instance_parameters(),
                predicted_screening_feasible=screening_feasible,
                eda_feasible=measured.feasible,
                comparisons=comparisons,
                prediction_accuracy_passed=all(item.passed for item in comparisons),
            )
        )

    if not predicted_feasible:
        raise ValueError("relinearization candidate set has no predicted-feasible point")
    if result.objective.goal is ObjectiveGoal.MAXIMIZE:
        predicted_recommendation = max(predicted_feasible, key=lambda item: item[1])
    else:
        predicted_recommendation = min(predicted_feasible, key=lambda item: item[1])

    if run.selected_parameters is None:
        raise ValueError("successful atomic run has no selected parameters")
    selected_instance_parameters = run.selected_instance_parameters or {}
    selected = [
        candidate
        for candidate in run.candidates
        if _same_numeric_mapping(candidate.parameters, run.selected_parameters)
        and candidate.instance_parameters == selected_instance_parameters
    ]
    if (
        len(selected) != 1
        or not selected[0].feasible
        or selected[0].atomic_candidate_id is None
    ):
        raise ValueError("run selection does not identify one feasible atomic candidate")
    eda_selected_id = selected[0].atomic_candidate_id

    accuracy_passed = all(
        candidate.prediction_accuracy_passed for candidate in validations
    )
    recommendation_agreement = predicted_recommendation[0] == eda_selected_id
    failed_metrics = sorted(
        {
            comparison.metric
            for candidate in validations
            for comparison in candidate.comparisons
            if not comparison.passed
        }
    )
    notes = [
        "the exact atomic domain was exhausted and final selection used EDA metrics"
    ]
    if legacy_floor_metrics:
        notes.append(
            "legacy relative-error floors restored from the exact hash-bound "
            "policy for: " + ", ".join(sorted(legacy_floor_metrics))
        )
    if failed_metrics:
        notes.append(
            "live prediction error gate failed for: " + ", ".join(failed_metrics)
        )
    if not recommendation_agreement:
        notes.append(
            "predicted local recommendation differs from the final EDA selection"
        )
    gate_passed = accuracy_passed
    feasible_count = sum(candidate.eda_feasible for candidate in validations)
    return OperatingPointRelinearizationRunValidation(
        relinearization_result_sha256=result_sha256,
        bound_policy_sha256=bound_policy_sha256,
        task_id=task.id,
        task_sha256=task_sha256,
        run_sha256=run_sha256,
        plan_token=run.plan_token,
        status=RunStatus.SUCCEEDED if gate_passed else RunStatus.PARTIAL,
        gate_passed=gate_passed,
        declared_candidate_count=declared,
        evaluated_candidate_count=len(validations),
        eda_feasible_candidate_count=feasible_count,
        eda_feasible_fraction=feasible_count / declared,
        predicted_recommendation_candidate_id=predicted_recommendation[0],
        eda_selected_candidate_id=eda_selected_id,
        recommendation_agreement=recommendation_agreement,
        candidate_execution_gate_passed=True,
        prediction_accuracy_gate_passed=accuracy_passed,
        candidates=validations,
        notes=notes,
    )


def build_task_from_relinearization(
    result_path: Path,
    task_template_path: Path,
) -> TaskSpec:
    """Compile only a passed, hash-bound local model into a normal tuning task."""

    result = OperatingPointRelinearizationResult.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    if (
        result.status is not RunStatus.SUCCEEDED
        or not result.gate_passed
        or result.candidate_set is None
    ):
        raise ValueError("only a passed relinearization result can create a task")
    raw = json.loads(task_template_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("relinearization task template must contain one JSON object")
    for name in (
        "candidate_set",
        "parameter_space",
        "instance_parameter_space",
        "theory_seed",
    ):
        if raw.get(name):
            raise ValueError(
                f"relinearization task template must not predeclare {name}"
            )
        raw.pop(name, None)
    if raw.get("parameters", {}) != result.fixed_parameters:
        raise ValueError(
            "task template fixed parameters do not match the source operating point"
        )
    fixed_instance = {
        str(update["instance"]): {
            str(name): str(value)
            for name, value in update.get("parameters", {}).items()
        }
        for update in raw.get("instance_parameter_updates", [])
    }
    if fixed_instance != result.fixed_instance_parameters:
        raise ValueError(
            "task template fixed instance parameters do not match the source run"
        )
    try:
        template_constraints = [
            MetricConstraint.model_validate(item)
            for item in raw.get("constraints", [])
        ]
    except (TypeError, ValueError) as exc:
        raise ValueError("task template constraints are malformed") from exc
    missing_constraints = [
        item for item in result.constraints if item not in template_constraints
    ]
    if missing_constraints:
        raise ValueError(
            "task template omits or changes a relinearization screening constraint"
        )
    if raw.get("objective") != result.objective.model_dump(mode="json"):
        raise ValueError("task template objective does not match relinearization policy")

    source = result.candidate_set.source
    bindings = dict(source.bindings)
    bindings["relinearization_result_sha256"] = _file_sha256(result_path)
    bindings["task_template_sha256"] = _file_sha256(task_template_path)
    candidate_set = result.candidate_set.model_copy(
        update={"source": source.model_copy(update={"bindings": bindings})}
    )
    raw["candidate_set"] = candidate_set.model_dump(mode="json")
    return TaskSpec.model_validate(raw)


__all__ = [
    "LocalMetricModel",
    "OperatingPointRelinearizationPolicy",
    "OperatingPointRelinearizationResult",
    "OperatingPointRelinearizationRunValidation",
    "RelinearizationComparison",
    "RelinearizationMetric",
    "RelinearizationParameter",
    "RelinearizationRunCandidateValidation",
    "RelinearizationRunMetricComparison",
    "RelinearizedProposal",
    "build_task_from_relinearization",
    "relinearize_operating_point",
    "validate_relinearization_run",
]
