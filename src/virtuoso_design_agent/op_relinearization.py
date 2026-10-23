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
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictStr, model_serializer, model_validator

from .instance_path import INSTANCE_PATH_PATTERN
from .generic_simulation import GenericTestbenchOverrides
from .linear_network import solve_complex_linear_system
from .metrics import evaluate_constraints
from .models import (
    AtomicCandidate,
    AtomicCandidateSet,
    CandidateEvaluation,
    CandidateSetSource,
    ConstraintEvaluation,
    EvidenceSource,
    InstanceParameterUpdate,
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
from .spectre_values import spectre_scalar


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


_NUMERIC_UNITS = {
    "m": (1.0, ""), "um": (1e-6, "u"), "nm": (1e-9, "n"),
    "ohm": (1.0, ""), "kohm": (1e3, "k"),
    "F": (1.0, ""), "pF": (1e-12, "p"), "fF": (1e-15, "f"),
    "V": (1.0, ""), "mV": (1e-3, "m"), "A": (1.0, ""), "uA": (1e-6, "u"),
    "1": (1.0, ""),
}


class NumericInstanceParameterTarget(_FiniteStrictModel):
    instance: StrictStr = Field(pattern=INSTANCE_PATH_PATTERN)
    parameter: StrictStr = Field(min_length=1, max_length=128)


class NumericLinkedInstanceParameterTarget(NumericInstanceParameterTarget):
    scale: float = 1.0
    offset: float = 0.0
    quantization_grid: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def nonzero_scale(self) -> "NumericLinkedInstanceParameterTarget":
        if self.scale == 0:
            raise ValueError("linked-field scale cannot be zero; declare constant fields as fixed")
        return self

    @model_serializer(mode="wrap")
    def serialize_optional_relation(self, handler):
        raw = handler(self)
        for name, default in (("scale", 1.0), ("offset", 0.0), ("quantization_grid", None)):
            if raw.get(name, default) == default:
                raw.pop(name, None)
        return raw


class NumericInstanceParameterBinding(NumericInstanceParameterTarget):
    """Explicit literal CDF meaning; declared affine fields share one axis."""

    unit: Literal["m", "um", "nm", "ohm", "kohm", "F", "pF", "fF", "V", "mV", "A", "uA", "1"]
    literal_format: Literal["spectre_si"]
    meaning: StrictStr = Field(min_length=1, max_length=2000)
    linked_parameters: list[NumericLinkedInstanceParameterTarget] = Field(default_factory=list, max_length=31)

    def targets(self) -> list[tuple[str, str]]:
        return [(self.instance, self.parameter),
                *((item.instance, item.parameter) for item in self.linked_parameters)]

    def members(self) -> list[tuple[str, str, float, float, float | None]]:
        return [(self.instance, self.parameter, 1.0, 0.0, None),
                *((p.instance, p.parameter, p.scale, p.offset, p.quantization_grid) for p in self.linked_parameters)]

    @model_serializer(mode="wrap")
    def serialize_optional_links(self, handler):
        raw = handler(self)
        if not self.linked_parameters:
            raw.pop("linked_parameters", None)
        return raw

    @model_validator(mode="after")
    def require_meaning(self) -> "NumericInstanceParameterBinding":
        if not self.meaning.strip():
            raise ValueError("numeric CDF meaning cannot be blank")
        if len(self.targets()) != len(set(self.targets())):
            raise ValueError("linked CDF targets must be unique, including the primary field")
        return self


_TESTBENCH_UNIT_KINDS = {"V": "voltage", "mV": "voltage", "A": "current", "uA": "current",
                         "F": "capacitor", "pF": "capacitor", "fF": "capacitor",
                         "ohm": "resistor", "kohm": "resistor"}


class NumericLinkedTestbenchTarget(_FiniteStrictModel):
    name: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    scale: float = 1.0
    offset: float = 0.0
    quantization_grid: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def nonzero_scale(self) -> "NumericLinkedTestbenchTarget":
        if self.scale == 0:
            raise ValueError("linked-field scale cannot be zero; declare constant fields as fixed")
        return self

    @model_serializer(mode="wrap")
    def serialize_optional_relation(self, handler):
        raw = handler(self)
        for name, default in (("scale", 1.0), ("offset", 0.0), ("quantization_grid", None)):
            if raw.get(name, default) == default:
                raw.pop(name, None)
        return raw


def _related_value(base: float, scale: float, offset: float, grid: float | None, label: str) -> float:
    value = scale * base + offset
    if not math.isfinite(value):
        raise ValueError(f"linked-field relation is nonfinite for {label}")
    if grid is not None:
        quotient = value / grid
        if (not math.isfinite(quotient) or abs(quotient) > 2 ** 52
                or not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=8 * math.ulp(quotient))):
            raise ValueError(f"linked-field value for {label} is not representable on its explicit grid")
    return value


def _relation_matches(actual: float, expected: float, base: float, scale: float, offset: float) -> bool:
    # Only binary arithmetic roundoff, not a fixed physical-unit tolerance. This
    # permits cancellation near zero without hiding fF-scale or large-offset drift.
    tolerance = 8 * max(math.ulp(scale * base), math.ulp(offset), math.ulp(expected))
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=tolerance)


class NumericTestbenchParameterBinding(_FiniteStrictModel):
    """An explicit recorded DC-source/load coordinate, not a new simulator input."""

    target: Literal["source_dc", "load"]
    name: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    unit: Literal["V", "mV", "A", "uA", "F", "pF", "fF", "ohm", "kohm"]
    meaning: StrictStr = Field(min_length=1, max_length=2000)
    linked_names: list[Annotated[StrictStr, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")] | NumericLinkedTestbenchTarget] = Field(
        default_factory=list, max_length=31,
    )

    def targets(self) -> list[str]:
        return [self.name, *[p if isinstance(p, str) else p.name for p in self.linked_names]]

    def members(self) -> list[tuple[str, float, float, float | None]]:
        return [(self.name, 1.0, 0.0, None),
                *[(p, 1.0, 0.0, None) if isinstance(p, str) else (p.name, p.scale, p.offset, p.quantization_grid)
                  for p in self.linked_names]]

    @model_serializer(mode="wrap")
    def serialize_optional_links(self, handler):
        raw = handler(self)
        if not self.linked_names:
            raw.pop("linked_names", None)
        return raw

    @model_validator(mode="after")
    def validate_quantity(self) -> "NumericTestbenchParameterBinding":
        source_unit = _TESTBENCH_UNIT_KINDS[self.unit] in {"voltage", "current"}
        if (self.target == "source_dc") != source_unit:
            raise ValueError("testbench target and declared unit are incompatible")
        if not self.meaning.strip():
            raise ValueError("numeric testbench meaning cannot be blank")
        if len(self.targets()) != len(set(self.targets())):
            raise ValueError("linked testbench targets must be unique, including the primary field")
        return self

    def value_from(self, overrides: GenericTestbenchOverrides | None, label: str = "testbench") -> float:
        return self.values_from(overrides, label)[self.name]

    def values_from(self, overrides: GenericTestbenchOverrides | None, label: str = "testbench") -> dict[str, float]:
        values = {}
        for name in self.targets():
            value = None
            if overrides is not None:
                if self.target == "source_dc":
                    source = overrides.sources.get(name)
                    value = source.dc_value if source is not None else None
                else:
                    value = overrides.loads.get(name)
            if value is None or not math.isfinite(value) or (self.target == "load" and value <= 0):
                raise ValueError(f"{label} lacks an explicit testbench {self.target} value for {name}")
            scaled = value / _NUMERIC_UNITS[self.unit][0]
            if not math.isfinite(scaled):
                raise ValueError(f"{label} testbench unit conversion is nonfinite for {name}")
            values[name] = scaled
        base = values[self.name]
        for name, scale, offset, grid in self.members():
            expected = _related_value(base, scale, offset, grid, name)
            if not _relation_matches(values[name], expected, base, scale, offset):
                raise ValueError(f"{label} linked testbench fields for {self.name} do not match the declared relation")
        return values


class RelinearizationParameter(_FiniteStrictModel):
    name: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    minimum: float
    maximum: float
    proposal_step: float = Field(gt=0.0)
    steps_each_side: int = Field(default=1, ge=1, le=4)
    quantization_grid: float | None = Field(default=None, gt=0.0)
    decimal_places: int = Field(default=9, ge=3, le=12)
    instance_parameter: NumericInstanceParameterBinding | None = None
    testbench_parameter: NumericTestbenchParameterBinding | None = None

    @model_serializer(mode="wrap")
    def serialize_optional_binding(self, handler):
        raw = handler(self)
        if self.instance_parameter is None:
            raw.pop("instance_parameter", None)  # Preserve legacy semantic-policy hashes.
        if self.testbench_parameter is None:
            raw.pop("testbench_parameter", None)
        return raw

    @model_validator(mode="after")
    def validate_bounds(self) -> "RelinearizationParameter":
        if self.maximum <= self.minimum:
            raise ValueError("relinearization parameter maximum must exceed minimum")
        if self.instance_parameter is not None and self.testbench_parameter is not None:
            raise ValueError("a parameter cannot bind both CDF and testbench fields")
        signed_dc = self.testbench_parameter is not None and self.testbench_parameter.target == "source_dc"
        if not signed_dc and (self.minimum <= 0 or self.maximum <= 0):
            raise ValueError("CDF, semantic and load model bounds must remain positive")
        relations = ([(scale, offset) for _, _, scale, offset, _ in self.instance_parameter.members()]
                     if self.instance_parameter else
                     [(scale, offset) for _, scale, offset, _ in self.testbench_parameter.members()]
                     if self.testbench_parameter else [])
        for scale, offset in relations:
            endpoints = [_related_value(v, scale, offset, None, self.name) for v in (self.minimum, self.maximum)]
            if not signed_dc and min(endpoints) <= 0:
                raise ValueError("linked CDF/load values must remain positive throughout the declared bounds")
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


class _RelinearizationDefinition(_FiniteStrictModel):
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
    include_measured_anchor: bool = True
    evidence_source: Literal[EvidenceSource.USER_INPUT, EvidenceSource.SOFTWARE_INFERENCE] = EvidenceSource.USER_INPUT

    @model_validator(mode="after")
    def validate_declared_model(self) -> "_RelinearizationDefinition":
        training = self.training_candidate_indices
        holdout = self.holdout_candidate_indices
        if len(training) != len(set(training)) or len(holdout) != len(set(holdout)):
            raise ValueError("training and holdout candidate indices must be unique")
        if set(training) & set(holdout):
            raise ValueError("training and holdout candidate indices must be disjoint")
        if self.anchor_candidate_index not in training:
            raise ValueError("anchor candidate must belong to the training set")
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("relinearization parameters must be unique")
        raw_targets = [target for p in self.parameters if p.instance_parameter is not None
                       for target in p.instance_parameter.targets()]
        if len(raw_targets) != len(set(raw_targets)):
            raise ValueError("numeric CDF bindings must not alias the same instance parameter")
        testbench_targets = [(p.testbench_parameter.target, name)
                             for p in self.parameters if p.testbench_parameter is not None
                             for name in p.testbench_parameter.targets()]
        if len(testbench_targets) != len(set(testbench_targets)):
            raise ValueError("numeric testbench bindings must not alias the same field")
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


class OperatingPointRelinearizationPolicy(_RelinearizationDefinition):
    @model_validator(mode="after")
    def require_training_count(self) -> "OperatingPointRelinearizationPolicy":
        if len(self.training_candidate_indices) < len(self.parameters) + 1:
            raise ValueError("training set needs the anchor plus at least one independent point per parameter")
        return self


class RelinearizationDataRequest(_RelinearizationDefinition):
    """Same declarations, but incomplete sampling may be diagnosed, never fitted."""

    training_candidate_indices: list[int] = Field(min_length=1, max_length=64)
    holdout_candidate_indices: list[int] = Field(default_factory=list, max_length=32)


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
    parameter_bindings: dict[StrictStr, NumericInstanceParameterBinding] = Field(default_factory=dict)
    testbench_parameter_bindings: dict[StrictStr, NumericTestbenchParameterBinding] = Field(default_factory=dict)
    fixed_testbench_overrides: GenericTestbenchOverrides | None = None
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

    @model_serializer(mode="wrap")
    def serialize_optional_bindings(self, handler):
        raw = handler(self)
        if not self.parameter_bindings:
            raw.pop("parameter_bindings", None)
        if not self.testbench_parameter_bindings:
            raw.pop("testbench_parameter_bindings", None)
        if self.fixed_testbench_overrides is None:
            raw.pop("fixed_testbench_overrides", None)
        return raw


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
    fixed_testbench_overrides: GenericTestbenchOverrides | None = None
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

    @model_serializer(mode="wrap")
    def serialize_optional_testbench(self, handler):
        raw = handler(self)
        if self.fixed_testbench_overrides is None:
            raw.pop("fixed_testbench_overrides", None)
        return raw


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
        _parameter_value(candidate, parameter)


def _binding_value(fields: dict[str, dict[str, str]], binding: NumericInstanceParameterBinding,
                   label: str, parameter_name: str) -> float:
    values = {}
    for instance, parameter in binding.targets():
        literal = fields.get(instance, {}).get(parameter)
        value = spectre_scalar(literal) if literal is not None else None
        if value is not None:
            value /= _NUMERIC_UNITS[binding.unit][0]
        if value is None or not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{label} lacks positive literal parameter {parameter_name} ({instance}.{parameter})")
        values[(instance, parameter)] = value
    base = values[(binding.instance, binding.parameter)]
    for instance, parameter, scale, offset, grid in binding.members():
        expected = _related_value(base, scale, offset, grid, f"{instance}.{parameter}")
        if not _relation_matches(values[(instance, parameter)], expected, base, scale, offset):
            raise ValueError(f"{label} linked CDF fields for {parameter_name} do not match the declared relation")
    return base


def _parameter_value(candidate: CandidateEvaluation, parameter: RelinearizationParameter) -> float:
    if parameter.testbench_parameter is not None:
        return parameter.testbench_parameter.value_from(candidate.testbench_overrides, f"source candidate {candidate.index}")
    binding = parameter.instance_parameter
    if binding is not None:
        return _binding_value(candidate.instance_parameters, binding,
                              f"source candidate {candidate.index}", parameter.name)
    value = candidate.parameters.get(parameter.name)
    if value is None or not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"source candidate {candidate.index} lacks positive literal parameter {parameter.name}")
    return value


def _fixed_testbench(overrides: GenericTestbenchOverrides | None,
                     bindings: dict[str, NumericTestbenchParameterBinding]) -> GenericTestbenchOverrides | None:
    if overrides is None or not bindings:
        return overrides
    raw = overrides.model_dump(mode="json", exclude_none=True)
    for binding in bindings.values():
        for name in binding.targets():
            if binding.target == "load":
                raw["loads"].pop(name, None)
            else:
                source = raw["sources"].get(name, {})
                source.pop("dc_value", None)
                if not source:
                    raw["sources"].pop(name, None)
    return GenericTestbenchOverrides.model_validate(raw) if raw["sources"] or raw["loads"] else None


def _source_context(candidate: CandidateEvaluation, parameters: list[RelinearizationParameter]) -> tuple:
    bindings = {p.name: p.testbench_parameter for p in parameters if p.testbench_parameter is not None}
    return (candidate.topology_sha256, candidate.topology_variant_id, _fixed_testbench(candidate.testbench_overrides, bindings),
            [(c.name, c.process_corner, c.temperature_c, c.vdd_v) for c in candidate.operating_conditions])


def _constant_inputs(
    candidates: list[CandidateEvaluation],
    parameters: list[RelinearizationParameter],
) -> tuple[dict[str, float], dict[str, dict[str, str]]]:
    if any(_source_context(c, parameters) != _source_context(candidates[0], parameters) for c in candidates[1:]):
        raise ValueError("unmodeled topology, testbench or operating conditions vary across source points")
    modeled_names = {p.name for p in parameters if p.instance_parameter is None and p.testbench_parameter is None}
    if any(p.name in candidates[0].parameters for p in parameters if p.instance_parameter is not None or p.testbench_parameter is not None):
        raise ValueError("numeric CDF/testbench aliases must not shadow recorded semantic parameters")
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
    def fixed_raw(candidate: CandidateEvaluation) -> dict[str, dict[str, str]]:
        values = {name: dict(fields) for name, fields in candidate.instance_parameters.items()}
        for p in parameters:
            if p.instance_parameter is not None:
                for name, parameter in p.instance_parameter.targets():
                    values.get(name, {}).pop(parameter, None)
                    if name in values and not values[name]:
                        values.pop(name)
        return values

    instance_parameters = fixed_raw(candidates[0])
    if any(
        fixed_raw(candidate) != instance_parameters
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
        (_parameter_value(candidate, item) - _parameter_value(anchor, item))
        / item.proposal_step
        for item in parameters
    ]


def _same_parameter_coordinates(left: CandidateEvaluation, right: CandidateEvaluation,
                                parameters: list[RelinearizationParameter]) -> bool:
    return all(math.isclose(_parameter_value(left, p), _parameter_value(right, p), rel_tol=1e-12, abs_tol=0.0)
               for p in parameters)


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
    # Rounding to a manufacturing/model grid must not expand explicit bounds.
    values = {value for value in values if parameter.minimum <= value <= parameter.maximum}
    if not any(
        math.isclose(value, anchor_value, rel_tol=1e-12, abs_tol=1e-15)
        for value in values
    ):
        raise ValueError(
            f"anchor {parameter.name} is not representable on its proposal grid"
        )
    return sorted(values)


def _parameter_signature(
    candidate: CandidateEvaluation,
    parameters: list[RelinearizationParameter],
) -> tuple[float, ...]:
    return tuple(_quantize(_parameter_value(candidate, item), item) for item in parameters)


def _atomic_proposal(proposal: RelinearizedProposal, parameters: list[RelinearizationParameter],
                     anchor: CandidateEvaluation, index: int) -> AtomicCandidate:
    semantic = {}
    instances: dict[str, dict[str, str]] = {}
    testbench = anchor.testbench_overrides.model_dump(mode="json", exclude_none=True) if anchor.testbench_overrides is not None else None
    for p in parameters:
        value = proposal.parameters[p.name]
        if p.testbench_parameter is not None:
            binding = p.testbench_parameter
            assert testbench is not None  # Every modeled coordinate was explicitly read from the anchor.
            if not math.isclose(value, _parameter_value(anchor, p), rel_tol=1e-12, abs_tol=0.0):
                for name, scale, offset, grid in binding.members():
                    related = _related_value(value, scale, offset, grid, name)
                    scaled = related * _NUMERIC_UNITS[binding.unit][0]
                    if (not math.isfinite(scaled) or not math.isclose(scaled / _NUMERIC_UNITS[binding.unit][0],
                            related, rel_tol=1e-12, abs_tol=0.0)):
                        raise ValueError(f"testbench serialization loses precision for {p.name}/{name}")
                    if binding.target == "source_dc":
                        testbench["sources"][name]["dc_value"] = scaled
                    else:
                        testbench["loads"][name] = scaled
            continue
        binding = p.instance_parameter
        if binding is None:
            semantic[p.name] = value
            continue
        is_anchor = math.isclose(value, _parameter_value(anchor, p), rel_tol=1e-12, abs_tol=0.0)
        for instance, parameter, scale, offset, grid in binding.members():
            related = _related_value(value, scale, offset, grid, f"{instance}.{parameter}")
            literal = (anchor.instance_parameters[instance][parameter] if is_anchor else
                       f"{related:.12g}{_NUMERIC_UNITS[binding.unit][1]}")
            decoded = spectre_scalar(literal)
            if decoded is None or not math.isclose(decoded / _NUMERIC_UNITS[binding.unit][0], related, rel_tol=1e-12, abs_tol=0.0):
                raise ValueError(f"numeric CDF serialization loses precision for {p.name}")
            instances.setdefault(instance, {})[parameter] = literal
    return AtomicCandidate(id=f"op-local-{index:03d}", parameters=semantic,
                           instance_parameter_updates=[InstanceParameterUpdate(instance=name, parameters=fields)
                                                       for name, fields in instances.items()],
                           testbench_overrides=GenericTestbenchOverrides.model_validate(testbench) if testbench is not None else None,
                           predicted_metrics=dict(proposal.predicted_metrics))


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

    if not isinstance(policy, OperatingPointRelinearizationPolicy):
        raise ValueError("fitting requires a strict OperatingPointRelinearizationPolicy, not a data-check request")
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
        selected, policy.parameters
    )
    training = [by_index[index] for index in policy.training_candidate_indices]
    holdout = [by_index[index] for index in policy.holdout_candidate_indices]
    # Disjoint indices alone do not reserve an unseen input tuple. Compare actual
    # declared coordinates (including SI-equivalent literals), never rounded grids.
    for candidate in holdout:
        for fitted in training:
            if _same_parameter_coordinates(candidate, fitted, policy.parameters):
                raise ValueError(
                    f"held-out candidate {candidate.index} repeats training candidate {fitted.index} "
                    "coordinates; distinct indices are not an unseen parameter tuple"
                )
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
    observed = set()
    observation_notes = []
    for candidate in run.candidates:
        try:
            _validate_source_candidate(candidate, policy)
            _constant_inputs([anchor, candidate], policy.parameters)
            signature = _parameter_signature(candidate, policy.parameters)
            if any(not math.isclose(_parameter_value(candidate, parameter), value, rel_tol=1e-12, abs_tol=0.0)
                   for parameter, value in zip(policy.parameters, signature, strict=True)):
                raise ValueError("recorded coordinates do not exactly match the current proposal grid")
            observed.add(signature)
        except ValueError as error:
            observation_notes.append(f"source candidate {candidate.index} not reused as an evaluated proposal tuple: {error}")
    anchor_signature = _parameter_signature(anchor, policy.parameters)
    proposal_axes = [
        _proposal_values(_parameter_value(anchor, item), item)
        for item in policy.parameters
    ]
    proposals: list[RelinearizedProposal] = []
    relation_excluded_count, first_relation_issue = 0, None
    for values in itertools.product(*proposal_axes):
        signature = tuple(float(value) for value in values)
        if signature in observed and signature != anchor_signature:
            continue
        parameters = dict(zip(parameter_names, signature, strict=True))
        try:
            for parameter in policy.parameters:
                binding = parameter.instance_parameter or parameter.testbench_parameter
                if binding is None:
                    continue
                for member in binding.members():
                    scale, offset, grid = member[-3:]
                    _related_value(parameters[parameter.name], scale, offset, grid, parameter.name)
        except ValueError as error:
            relation_excluded_count += 1
            first_relation_issue = first_relation_issue or str(error)
            continue  # Do not round a dependent field and silently break its declared relationship.
        features = [(parameters[p.name] - _parameter_value(anchor, p)) / p.proposal_step for p in policy.parameters]
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
    notes.extend(observation_notes)
    if relation_excluded_count:
        notes.append(f"{relation_excluded_count} proposal tuples excluded by explicit linked-field relations/grids; first: {first_relation_issue}")
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
        anchor_proposal = next(item for item in proposals if item.previously_evaluated)
        selected = [
            item
            for item in proposals
            if policy.include_measured_anchor or item is not anchor_proposal
        ]
        if policy.include_measured_anchor:
            selected.remove(anchor_proposal)
            selected.insert(0, anchor_proposal)
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
                _atomic_proposal(proposal, policy.parameters, anchor, index)
                for index, proposal in enumerate(selected, start=1)
            ],
        )
        notes.append(
            (
                "candidate set starts with the measured anchor, followed by locally "
                "ranked proposals; final ranking still belongs to EDA"
                if policy.include_measured_anchor
                else "candidate set excludes every previously measured point and "
                "contains locally ranked prospective proposals only; final ranking "
                "still belongs to EDA"
            )
        )

    policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
    gate_passed = validation_passed and candidate_set is not None
    testbench_bindings = {p.name: p.testbench_parameter for p in policy.parameters if p.testbench_parameter is not None}
    fixed_testbench = _fixed_testbench(anchor.testbench_overrides, testbench_bindings)
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
        parameter_bindings={p.name: p.instance_parameter for p in policy.parameters if p.instance_parameter is not None},
        testbench_parameter_bindings=testbench_bindings,
        fixed_testbench_overrides=fixed_testbench.model_copy(deep=True) if fixed_testbench is not None else None,
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


def _validate_fixed_testbench(result: OperatingPointRelinearizationResult) -> None:
    assert result.candidate_set is not None
    for candidate in result.candidate_set.candidates:
        for binding in result.testbench_parameter_bindings.values():
            binding.value_from(candidate.testbench_overrides, f"atomic candidate {candidate.id}")
    if any(_fixed_testbench(candidate.testbench_overrides, result.testbench_parameter_bindings) != result.fixed_testbench_overrides
           for candidate in result.candidate_set.candidates):
        raise ValueError("relinearization candidates do not retain the fixed testbench overrides")


def _validate_testbench_binding_kinds(result: OperatingPointRelinearizationResult, task: TaskSpec) -> None:
    if not result.testbench_parameter_bindings:
        return
    spec = task.generic_simulation
    if spec is None:
        raise ValueError("numeric testbench bindings require generic_simulation")
    sources = {source.name: source for source in spec.sources}
    loads = {load.name: load for load in spec.loads}
    for binding in result.testbench_parameter_bindings.values():
        for name in binding.targets():
            target = (sources if binding.target == "source_dc" else loads).get(name)
            if target is None or target.kind != _TESTBENCH_UNIT_KINDS[binding.unit]:
                raise ValueError(f"testbench binding {name} is missing or has incompatible kind for {binding.unit}")
            if binding.target == "source_dc" and task.operating_conditions and spec.operating_condition_supply_source == name:
                raise ValueError("an operating-condition supply cannot be modeled as a candidate DC source")


def _validate_linked_parameters(result: OperatingPointRelinearizationResult) -> None:
    assert result.candidate_set is not None
    for candidate in result.candidate_set.candidates:
        for name, binding in result.parameter_bindings.items():
            if binding.linked_parameters:
                _binding_value(candidate.instance_parameters(), binding,
                               f"atomic candidate {candidate.id}", name)


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
        # The original semantic-only schema predates include_measured_anchor.
        # Only validation may recover that one known historical serialization;
        # it cannot change a floor/limit, regenerate candidates or rehash a run.
        legacy = policy.model_dump(mode="json")
        legacy.pop("include_measured_anchor")
        legacy_schema = (
            "include_measured_anchor" not in policy.model_fields_set
            and not result.parameter_bindings and not result.testbench_parameter_bindings
            and all(p.instance_parameter is None and p.testbench_parameter is None for p in policy.parameters)
            and all("relative_error_floor" not in m.model_fields_set for m in result.models)
        )
        if not legacy_schema or _canonical_sha256(legacy) != result.policy_sha256:
            raise ValueError("validation policy does not match the result policy hash")
        policy_sha256 = result.policy_sha256
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
    binding_audit_path: Path | None = None,
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
    _validate_fixed_testbench(result)
    _validate_testbench_binding_kinds(result, task)
    _validate_linked_parameters(result)
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
    if binding_audit_path is not None:
        from .design_review import load_bound_run
        load_bound_run(task_path, run_path, binding_audit_path)
    elif run.plan_token != build_plan(task).confirmation_token:
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
            or measured.testbench_overrides != atomic.testbench_overrides
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
        and candidate.testbench_overrides == run.selected_testbench_overrides
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
    if binding_audit_path is not None:
        notes.append("historical task/run identity was imported from the existing exact-byte audit "
                     + _file_sha256(binding_audit_path) + "; prediction errors were recomputed, not inherited")
    if result.testbench_parameter_bindings:
        notes.append("candidate and selection overrides match recorded full testbench tuples; unmodeled fields remain fixed; "
                     "this is not an independent simulator-input check")
    elif result.fixed_testbench_overrides is not None:
        notes.append("candidate and selection overrides match the fixed recorded testbench; "
                     "this is not an independent simulator-input check")
    if legacy_floor_metrics:
        notes.append(
            "legacy relative-error floors restored from the exact hash-bound "
            "policy for: " + ", ".join(sorted(legacy_floor_metrics))
        )
    if bound_policy is not None and _canonical_sha256(bound_policy.model_dump(mode="json")) != bound_policy_sha256:
        notes.append("validation-only legacy policy hash matched before include_measured_anchor existed; "
                     "the historical candidate domain and error limits were not regenerated or changed")
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
        fixed_testbench_overrides=result.fixed_testbench_overrides,
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
    _validate_fixed_testbench(result)
    _validate_linked_parameters(result)
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
    task = TaskSpec.model_validate(raw)
    _validate_testbench_binding_kinds(result, task)
    return task


__all__ = [
    "LocalMetricModel",
    "NumericInstanceParameterBinding",
    "NumericInstanceParameterTarget",
    "NumericLinkedInstanceParameterTarget",
    "NumericLinkedTestbenchTarget",
    "RelinearizationDataRequest",
    "NumericTestbenchParameterBinding",
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
