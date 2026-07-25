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
    StrictModel,
    TaskSpec,
)


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
    notes: list[str] = []
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
    "RelinearizationComparison",
    "RelinearizationMetric",
    "RelinearizationParameter",
    "RelinearizedProposal",
    "build_task_from_relinearization",
    "relinearize_operating_point",
]
