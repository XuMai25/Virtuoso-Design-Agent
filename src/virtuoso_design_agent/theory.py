"""Topology-specific, evidence-bounded analog sizing before EDA simulation.

The first implementation intentionally supports one verified VDA topology.  It
uses a declared finite gm/Id characterization domain and analytical equations
to derive the minimum branch current for every characterization combination.
It never calls the Bridge and never claims a continuous or global optimum.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Literal

from pydantic import Field, StrictStr, model_validator

from .characterization import DeviceDataSource
from .metrics import evaluate_constraints
from .models import (
    ConstraintEvaluation,
    EvidenceSource,
    MetricConstraint,
    Objective,
    ObjectiveGoal,
    Relation,
    RunStatus,
    StrictModel,
)

class MosCharacterizationPoint(StrictModel):
    """One positive-magnitude gm/Id lookup point for a MOS device."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    length_um: float = Field(gt=0.0)
    gm_over_id_per_v: float = Field(gt=0.0)
    drain_current_density_a_per_um: float = Field(gt=0.0)
    gds_over_id_per_v: float = Field(gt=0.0)
    output_capacitance_f_per_um: float = Field(gt=0.0)
    vdsat_v: float = Field(gt=0.0)
    model: StrictStr | None = Field(default=None, min_length=1, max_length=96)
    vgs_magnitude_v: float | None = Field(default=None, ge=0.0)
    vds_magnitude_v: float | None = Field(default=None, ge=0.0)
    vsb_magnitude_v: float | None = Field(default=None, ge=0.0)
    source_artifact_id: StrictStr | None = Field(default=None, min_length=1)
    source_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class DeviceCharacterization(StrictModel):
    """Finite device domain plus the conditions and artifact that define it."""

    source: DeviceDataSource
    source_artifact_id: StrictStr | None = Field(default=None, min_length=1)
    source_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    process_corner: StrictStr = Field(min_length=1, max_length=64)
    temperature_c: float = Field(ge=-273.15, le=300.0)
    input_nmos_vds_v: float = Field(gt=0.0)
    load_pmos_vsd_v: float = Field(gt=0.0)
    tail_nmos_vds_v: float = Field(gt=0.0)
    nmos_input_points: list[MosCharacterizationPoint] = Field(
        min_length=1,
        max_length=24,
    )
    pmos_load_points: list[MosCharacterizationPoint] = Field(
        min_length=1,
        max_length=24,
    )
    nmos_tail_points: list[MosCharacterizationPoint] = Field(
        min_length=1,
        max_length=24,
    )

    @model_validator(mode="after")
    def validate_source_and_ids(self) -> "DeviceCharacterization":
        if self.source is not DeviceDataSource.SYNTHETIC_EXAMPLE:
            if self.source_artifact_id is None or self.source_artifact_sha256 is None:
                raise ValueError(
                    "PDK/EDA device data require source_artifact_id and "
                    "source_artifact_sha256"
                )
        for role, points in (
            ("nmos_input_points", self.nmos_input_points),
            ("pmos_load_points", self.pmos_load_points),
            ("nmos_tail_points", self.nmos_tail_points),
        ):
            ids = [point.id for point in points]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{role} contains duplicate point ids")
            if self.source is not DeviceDataSource.SYNTHETIC_EXAMPLE:
                for point in points:
                    required = {
                        "model": point.model,
                        "vgs_magnitude_v": point.vgs_magnitude_v,
                        "vds_magnitude_v": point.vds_magnitude_v,
                        "vsb_magnitude_v": point.vsb_magnitude_v,
                        "source_artifact_id": point.source_artifact_id,
                        "source_artifact_sha256": point.source_artifact_sha256,
                    }
                    missing = [name for name, value in required.items() if value is None]
                    if missing:
                        raise ValueError(
                            f"{role} point {point.id} is missing PDK provenance: "
                            + ", ".join(missing)
                        )
        return self


class WidthBounds(StrictModel):
    minimum_um: float = Field(gt=0.0)
    maximum_um: float = Field(gt=0.0)

    @model_validator(mode="after")
    def maximum_must_cover_minimum(self) -> "WidthBounds":
        if self.maximum_um < self.minimum_um:
            raise ValueError("maximum_um must be greater than or equal to minimum_um")
        return self


_GAIN_METRICS = {
    "estimated_differential_gain_v_per_v",
    "estimated_differential_gain_db",
}
_FREQUENCY_METRICS = {
    "estimated_bandwidth_hz",
    "estimated_gain_bandwidth_product_hz",
}
_LOWER_BOUND_METRICS = _GAIN_METRICS | _FREQUENCY_METRICS | {
    "estimated_minimum_headroom_v",
}
_UPPER_BOUND_METRICS = {
    "estimated_power_w",
    "estimated_area_proxy_um2",
}
_SUPPORTED_CONSTRAINT_METRICS = _LOWER_BOUND_METRICS | _UPPER_BOUND_METRICS
_SUPPORTED_OBJECTIVE_METRICS = {
    "estimated_power_w",
    "estimated_area_proxy_um2",
}


class DifferentialPairTheoryRequest(StrictModel):
    """Sizing request for the Gate-6 current-mirror differential pair."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    topology: Literal[
        "nmos_differential_pair_pmos_current_mirror_load_with_tail_device"
    ]
    pdk_profile: StrictStr = Field(min_length=1)
    vdd_v: float = Field(gt=0.0)
    mirror_diode_node_v: float = Field(gt=0.0)
    output_common_mode_v: float = Field(gt=0.0)
    tail_node_v: float = Field(gt=0.0)
    load_capacitance_f: float = Field(gt=0.0)
    sizing_margin_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_characterization_voltage_mismatch_v: float = Field(
        default=0.01,
        ge=0.0,
        le=0.2,
    )
    input_width: WidthBounds
    load_width: WidthBounds
    tail_width: WidthBounds
    device_characterization: DeviceCharacterization
    constraints: list[MetricConstraint] = Field(min_length=1, max_length=16)
    objective: Objective

    @model_validator(mode="after")
    def validate_theory_domain(self) -> "DifferentialPairTheoryRequest":
        if not self.tail_node_v < self.output_common_mode_v < self.vdd_v:
            raise ValueError(
                "require tail_node_v < output_common_mode_v < vdd_v"
            )
        if not self.tail_node_v < self.mirror_diode_node_v < self.vdd_v:
            raise ValueError(
                "require tail_node_v < mirror_diode_node_v < vdd_v"
            )
        characterization = self.device_characterization
        actual_voltages = {
            "input_nmos_vds_v": (
                self.output_common_mode_v - self.tail_node_v,
                self.mirror_diode_node_v - self.tail_node_v,
            ),
            "load_pmos_vsd_v": (
                self.vdd_v - self.output_common_mode_v,
                self.vdd_v - self.mirror_diode_node_v,
            ),
            "tail_nmos_vds_v": (self.tail_node_v,),
        }
        for name, actuals in actual_voltages.items():
            characterized = float(getattr(characterization, name))
            for actual in actuals:
                if abs(characterized - actual) > (
                    self.maximum_characterization_voltage_mismatch_v + 1e-12
                ):
                    raise ValueError(
                        f"{name} characterization voltage {characterized} V "
                        f"differs from topology operating voltage {actual} V by "
                        "more than maximum_characterization_voltage_mismatch_v"
                    )
        combinations = (
            len(self.device_characterization.nmos_input_points)
            * len(self.device_characterization.pmos_load_points)
            * len(self.device_characterization.nmos_tail_points)
        )
        if combinations > 4096:
            raise ValueError(
                "declared characterization domain exceeds 4096 combinations; "
                "split it explicitly instead of silently truncating"
            )
        seen: set[str] = set()
        for constraint in self.constraints:
            if constraint.metric in seen:
                raise ValueError(f"duplicate constraint metric {constraint.metric}")
            seen.add(constraint.metric)
            if constraint.metric not in _SUPPORTED_CONSTRAINT_METRICS:
                raise ValueError(
                    f"unsupported theory constraint metric {constraint.metric}"
                )
            if constraint.value < 0.0:
                raise ValueError(
                    f"{constraint.metric} theory constraint cannot be negative"
                )
            if constraint.metric in _LOWER_BOUND_METRICS:
                if constraint.relation is not Relation.GREATER_OR_EQUAL:
                    raise ValueError(
                        f"{constraint.metric} only supports a >= theory constraint"
                    )
            elif constraint.relation is not Relation.LESS_OR_EQUAL:
                raise ValueError(
                    f"{constraint.metric} only supports a <= theory constraint"
                )
        if self.objective.metric not in _SUPPORTED_OBJECTIVE_METRICS:
            raise ValueError(
                f"unsupported theory objective metric {self.objective.metric}"
            )
        if self.objective.goal is not ObjectiveGoal.MINIMIZE:
            raise ValueError("the first theory gate only supports minimization")
        return self


class TheoryCandidateEvaluation(StrictModel):
    id: str
    input_point_id: str
    load_point_id: str
    tail_point_id: str
    branch_current_a: float | None = None
    input_width_um: float | None = None
    load_width_um: float | None = None
    tail_width_um: float | None = None
    input_length_um: float | None = None
    load_length_um: float | None = None
    tail_length_um: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    constraints: list[ConstraintEvaluation] = Field(default_factory=list)
    constraint_margins: dict[str, float | None] = Field(default_factory=dict)
    local_log_sensitivities: dict[str, dict[str, float]] = Field(
        default_factory=dict
    )
    feasible: bool
    total_violation: float
    objective_value: float | None = None
    analysis_complete: bool
    limiting_current_bounds: list[str] = Field(default_factory=list)
    analysis_issues: list[str] = Field(default_factory=list)
    infeasibility_reasons: list[str] = Field(default_factory=list)
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class TheoryOptimalityBoundary(StrictModel):
    classification: Literal[
        "best_in_declared_discrete_characterization_domain",
        "no_feasible_design_in_declared_discrete_characterization_domain",
    ]
    declared_combinations: int
    evaluated_combinations: int
    domain_exhausted: bool
    continuous_optimum_claim: Literal[False] = False
    global_optimum_claim: Literal[False] = False
    statement: str


class DifferentialPairTheoryResult(StrictModel):
    schema_version: Literal[1] = 1
    request_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    topology: str
    pdk_profile: str
    status: RunStatus
    device_data_source: DeviceDataSource
    device_data_artifact_id: str | None = None
    device_data_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    characterization_conditions: dict[str, str | float]
    device_data_evidence_source: Literal[EvidenceSource.USER_INPUT] = (
        EvidenceSource.USER_INPUT
    )
    derived_metric_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    recommended_candidate_id: str | None
    best_evaluated_candidate_id: str | None
    optimality_boundary: TheoryOptimalityBoundary
    candidates: list[TheoryCandidateEvaluation]
    equations: list[str]
    assumptions: list[str]
    warnings: list[str] = Field(default_factory=list)


def _effective_threshold(constraint: MetricConstraint) -> float:
    if constraint.relation is Relation.GREATER_OR_EQUAL:
        return max(constraint.value - constraint.tolerance, 0.0)
    return constraint.value + constraint.tolerance


def _constraint_margin(
    constraint: MetricConstraint, actual: float | None
) -> float | None:
    if actual is None:
        return None
    if constraint.relation is Relation.GREATER_OR_EQUAL:
        return actual - (constraint.value - constraint.tolerance)
    if constraint.relation is Relation.LESS_OR_EQUAL:
        return (constraint.value + constraint.tolerance) - actual
    return constraint.tolerance - abs(actual - constraint.value)


def _frequency_current_bound(
    *,
    target_hz: float,
    numerator_per_current: float,
    capacitance_per_current: float,
    load_capacitance_f: float,
) -> float | None:
    """Solve f=I*N/(2*pi*(CL+I*A)) for the minimum I."""

    denominator = numerator_per_current - (
        2.0 * math.pi * target_hz * capacitance_per_current
    )
    if denominator <= 0.0:
        return None
    return 2.0 * math.pi * target_hz * load_capacitance_f / denominator


def _evaluate_combination(
    request: DifferentialPairTheoryRequest,
    input_point: MosCharacterizationPoint,
    load_point: MosCharacterizationPoint,
    tail_point: MosCharacterizationPoint,
) -> TheoryCandidateEvaluation:
    candidate_id = f"in={input_point.id}|load={load_point.id}|tail={tail_point.id}"
    cap_per_current = (
        input_point.output_capacitance_f_per_um
        / input_point.drain_current_density_a_per_um
        + load_point.output_capacitance_f_per_um
        / load_point.drain_current_density_a_per_um
    )
    output_conductance_per_current = (
        input_point.gds_over_id_per_v + load_point.gds_over_id_per_v
    )

    current_bounds = {
        "input_minimum_width": (
            request.input_width.minimum_um
            * input_point.drain_current_density_a_per_um
        ),
        "load_minimum_width": (
            request.load_width.minimum_um
            * load_point.drain_current_density_a_per_um
        ),
        "tail_minimum_width": (
            request.tail_width.minimum_um
            * tail_point.drain_current_density_a_per_um
            / 2.0
        ),
    }
    issues: list[str] = []
    margin_scale = 1.0 + request.sizing_margin_fraction
    for constraint in request.constraints:
        if constraint.metric not in _FREQUENCY_METRICS:
            continue
        target_hz = _effective_threshold(constraint) * margin_scale
        numerator = (
            output_conductance_per_current
            if constraint.metric == "estimated_bandwidth_hz"
            else input_point.gm_over_id_per_v
        )
        bound = _frequency_current_bound(
            target_hz=target_hz,
            numerator_per_current=numerator,
            capacitance_per_current=cap_per_current,
            load_capacitance_f=request.load_capacitance_f,
        )
        if bound is None:
            asymptote_hz = numerator / (2.0 * math.pi * cap_per_current)
            issues.append(
                f"{constraint.metric} target {target_hz:.12g} Hz reaches or "
                f"exceeds this lookup combination's {asymptote_hz:.12g} Hz "
                "parasitic asymptote"
            )
        else:
            current_bounds[constraint.metric] = bound

    if issues:
        constraints = evaluate_constraints({}, request.constraints)
        return TheoryCandidateEvaluation(
            id=candidate_id,
            input_point_id=input_point.id,
            load_point_id=load_point.id,
            tail_point_id=tail_point.id,
            constraints=constraints,
            constraint_margins={item.metric: None for item in constraints},
            feasible=False,
            total_violation=sum(item.normalized_violation for item in constraints),
            analysis_complete=False,
            analysis_issues=issues,
        )

    branch_current_a = max(current_bounds.values())
    limiting_value = max(current_bounds.values())
    limiting_bounds = sorted(
        name
        for name, value in current_bounds.items()
        if math.isclose(value, limiting_value, rel_tol=1e-9, abs_tol=1e-18)
    )

    input_width_um = (
        branch_current_a / input_point.drain_current_density_a_per_um
    )
    load_width_um = branch_current_a / load_point.drain_current_density_a_per_um
    tail_width_um = (
        2.0 * branch_current_a / tail_point.drain_current_density_a_per_um
    )
    upper_current_bounds = {
        "input_maximum_width": (
            request.input_width.maximum_um
            * input_point.drain_current_density_a_per_um
        ),
        "load_maximum_width": (
            request.load_width.maximum_um
            * load_point.drain_current_density_a_per_um
        ),
        "tail_maximum_width": (
            request.tail_width.maximum_um
            * tail_point.drain_current_density_a_per_um
            / 2.0
        ),
    }
    area_per_current = (
        2.0 * input_point.length_um
        / input_point.drain_current_density_a_per_um
        + 2.0 * load_point.length_um
        / load_point.drain_current_density_a_per_um
        + 2.0 * tail_point.length_um
        / tail_point.drain_current_density_a_per_um
    )
    for constraint in request.constraints:
        if constraint.metric == "estimated_power_w":
            upper_current_bounds[constraint.metric] = (
                _effective_threshold(constraint) / (2.0 * request.vdd_v)
            )
        elif constraint.metric == "estimated_area_proxy_um2":
            upper_current_bounds[constraint.metric] = (
                _effective_threshold(constraint) / area_per_current
            )
    maximum_branch_current_a = min(upper_current_bounds.values())
    infeasibility_reasons: list[str] = []
    implicit_domain_violation = 0.0
    if branch_current_a > maximum_branch_current_a * (1.0 + 1e-12):
        limiting_upper = min(upper_current_bounds, key=upper_current_bounds.get)
        infeasibility_reasons.append(
            f"minimum derived branch current {branch_current_a:.12g} A exceeds "
            f"{limiting_upper} bound {maximum_branch_current_a:.12g} A"
        )
        if not limiting_upper.startswith("estimated_"):
            implicit_domain_violation = (
                branch_current_a - maximum_branch_current_a
            ) / max(maximum_branch_current_a, 1e-30)

    output_capacitance_f = (
        request.load_capacitance_f + branch_current_a * cap_per_current
    )
    output_conductance_s = branch_current_a * output_conductance_per_current
    output_resistance_ohm = 1.0 / output_conductance_s
    input_gm_s = branch_current_a * input_point.gm_over_id_per_v
    gain_v_per_v = input_gm_s * output_resistance_ohm
    bandwidth_hz = 1.0 / (
        2.0 * math.pi * output_resistance_ohm * output_capacitance_f
    )
    gbw_hz = gain_v_per_v * bandwidth_hz
    input_output_headroom_v = (
        request.output_common_mode_v
        - request.tail_node_v
        - input_point.vdsat_v
    )
    input_diode_headroom_v = (
        request.mirror_diode_node_v
        - request.tail_node_v
        - input_point.vdsat_v
    )
    load_output_headroom_v = (
        request.vdd_v - request.output_common_mode_v - load_point.vdsat_v
    )
    load_diode_headroom_v = (
        request.vdd_v - request.mirror_diode_node_v - load_point.vdsat_v
    )
    tail_headroom_v = request.tail_node_v - tail_point.vdsat_v
    minimum_headroom_v = min(
        input_output_headroom_v,
        input_diode_headroom_v,
        load_output_headroom_v,
        load_diode_headroom_v,
        tail_headroom_v,
    )
    metrics = {
        "estimated_branch_current_a": branch_current_a,
        "estimated_tail_current_a": 2.0 * branch_current_a,
        "estimated_power_w": 2.0 * request.vdd_v * branch_current_a,
        "estimated_input_width_um": input_width_um,
        "estimated_load_width_um": load_width_um,
        "estimated_tail_width_um": tail_width_um,
        "estimated_output_capacitance_f": output_capacitance_f,
        "estimated_output_resistance_ohm": output_resistance_ohm,
        "estimated_differential_gain_v_per_v": gain_v_per_v,
        "estimated_differential_gain_db": 20.0 * math.log10(gain_v_per_v),
        "estimated_bandwidth_hz": bandwidth_hz,
        "estimated_gain_bandwidth_product_hz": gbw_hz,
        "estimated_input_headroom_v": min(
            input_output_headroom_v, input_diode_headroom_v
        ),
        "estimated_input_output_headroom_v": input_output_headroom_v,
        "estimated_input_diode_headroom_v": input_diode_headroom_v,
        "estimated_load_headroom_v": min(
            load_output_headroom_v, load_diode_headroom_v
        ),
        "estimated_load_output_headroom_v": load_output_headroom_v,
        "estimated_load_diode_headroom_v": load_diode_headroom_v,
        "estimated_tail_headroom_v": tail_headroom_v,
        "estimated_minimum_headroom_v": minimum_headroom_v,
        "estimated_area_proxy_um2": branch_current_a * area_per_current,
        "estimated_bandwidth_asymptote_hz": (
            output_conductance_per_current / (2.0 * math.pi * cap_per_current)
        ),
        "estimated_gbw_asymptote_hz": (
            input_point.gm_over_id_per_v / (2.0 * math.pi * cap_per_current)
        ),
        "constructed_kcl_residual_a": 0.0,
    }
    load_capacitance_fraction = request.load_capacitance_f / output_capacitance_f
    local_log_sensitivities = {
        "estimated_bandwidth_hz": {
            "branch_current_a": load_capacitance_fraction,
            "load_capacitance_f": -load_capacitance_fraction,
        },
        "estimated_gain_bandwidth_product_hz": {
            "branch_current_a": load_capacitance_fraction,
            "load_capacitance_f": -load_capacitance_fraction,
        },
        "estimated_power_w": {"branch_current_a": 1.0},
        "estimated_area_proxy_um2": {"branch_current_a": 1.0},
    }
    constraints = evaluate_constraints(metrics, request.constraints)
    feasible = not infeasibility_reasons and all(
        item.passed for item in constraints
    )
    objective_value = metrics[request.objective.metric]
    return TheoryCandidateEvaluation(
        id=candidate_id,
        input_point_id=input_point.id,
        load_point_id=load_point.id,
        tail_point_id=tail_point.id,
        branch_current_a=branch_current_a,
        input_width_um=input_width_um,
        load_width_um=load_width_um,
        tail_width_um=tail_width_um,
        input_length_um=input_point.length_um,
        load_length_um=load_point.length_um,
        tail_length_um=tail_point.length_um,
        metrics=metrics,
        constraints=constraints,
        constraint_margins={
            constraint.metric: _constraint_margin(
                constraint, metrics.get(constraint.metric)
            )
            for constraint in request.constraints
        },
        local_log_sensitivities=local_log_sensitivities,
        feasible=feasible,
        total_violation=(
            sum(item.normalized_violation for item in constraints)
            + implicit_domain_violation
        ),
        objective_value=objective_value,
        analysis_complete=True,
        limiting_current_bounds=limiting_bounds,
        infeasibility_reasons=infeasibility_reasons,
    )


def _best_evaluated(
    request: DifferentialPairTheoryRequest,
    candidates: list[TheoryCandidateEvaluation],
) -> TheoryCandidateEvaluation | None:
    complete = [candidate for candidate in candidates if candidate.analysis_complete]
    if not complete:
        return None

    def rank(candidate: TheoryCandidateEvaluation) -> tuple[float, float, float, str]:
        objective = (
            candidate.objective_value
            if candidate.objective_value is not None
            else math.inf
        )
        return (
            0.0 if candidate.feasible else 1.0,
            candidate.total_violation,
            objective,
            candidate.id,
        )

    return min(complete, key=rank)


def size_differential_pair(
    request: DifferentialPairTheoryRequest,
) -> DifferentialPairTheoryResult:
    """Exhaust the declared LUT triplets and derive each minimum-current design."""

    characterization = request.device_characterization
    canonical_request = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    combinations = itertools.product(
        characterization.nmos_input_points,
        characterization.pmos_load_points,
        characterization.nmos_tail_points,
    )
    candidates = [
        _evaluate_combination(request, input_point, load_point, tail_point)
        for input_point, load_point, tail_point in combinations
    ]
    best = _best_evaluated(request, candidates)
    recommended = best if best is not None and best.feasible else None
    declared = (
        len(characterization.nmos_input_points)
        * len(characterization.pmos_load_points)
        * len(characterization.nmos_tail_points)
    )
    if recommended is None:
        classification = (
            "no_feasible_design_in_declared_discrete_characterization_domain"
        )
        statement = (
            "Every declared lookup-point combination was considered, but no "
            "feasible analytical design was found. This does not prove the "
            "continuous PDK design space is infeasible."
        )
        status = RunStatus.PARTIAL
    else:
        classification = "best_in_declared_discrete_characterization_domain"
        statement = (
            "The recommendation has the best requested objective among feasible "
            "designs derived from every declared lookup-point combination. It is "
            "not a continuous-space or global optimum and still requires OA "
            "netlist and EDA validation."
        )
        status = RunStatus.SUCCEEDED

    warnings = [
        "Characterization values are request inputs; this local analyzer does not "
        "independently verify the bound artifact."
    ]
    if characterization.source is DeviceDataSource.SYNTHETIC_EXAMPLE:
        warnings.append(
            "Synthetic device data are suitable only for software-path tests and "
            "must not be treated as TSMC N28 or other PDK evidence."
        )
    return DifferentialPairTheoryResult(
        request_id=request.id,
        request_sha256=hashlib.sha256(canonical_request).hexdigest(),
        topology=request.topology,
        pdk_profile=request.pdk_profile,
        status=status,
        device_data_source=characterization.source,
        device_data_artifact_id=characterization.source_artifact_id,
        device_data_artifact_sha256=characterization.source_artifact_sha256,
        characterization_conditions={
            "process_corner": characterization.process_corner,
            "temperature_c": characterization.temperature_c,
            "input_nmos_vds_v": characterization.input_nmos_vds_v,
            "load_pmos_vsd_v": characterization.load_pmos_vsd_v,
            "tail_nmos_vds_v": characterization.tail_nmos_vds_v,
        },
        recommended_candidate_id=(recommended.id if recommended is not None else None),
        best_evaluated_candidate_id=(best.id if best is not None else None),
        optimality_boundary=TheoryOptimalityBoundary(
            classification=classification,
            declared_combinations=declared,
            evaluated_combinations=len(candidates),
            domain_exhausted=len(candidates) == declared,
            statement=statement,
        ),
        candidates=candidates,
        equations=[
            "gm = Id_branch * (gm/Id)_input",
            "gout = Id_branch * ((gds/Id)_input + (gds/Id)_load)",
            "Cout = Cload + Id_branch * (Cout_density_input/J_input + Cout_density_load/J_load)",
            "Ad0 = gm/gout",
            "BW = gout/(2*pi*Cout)",
            "GBW = gm/(2*pi*Cout)",
            "Id_required(f) = 2*pi*f*Cload/(N - 2*pi*f*Cpar_per_current)",
            "Pdc = VDD * Itail = 2*VDD*Id_branch",
            "dln(BW)/dln(Id) = dln(GBW)/dln(Id) = Cload/Cout",
        ],
        assumptions=[
            "Matched differential branches and current-mirror load are assumed; "
            "constructed KCL balance is not a mismatch prediction.",
            "A one-pole output-node model is used; internal poles, zeros, slew, "
            "noise, distortion, mismatch, and stability are not predicted.",
            "gm/Id, gds/Id, current density, capacitance density, and VDSAT are "
            "constant at each declared characterization point.",
            "Reported local log sensitivities hold the selected lookup point and "
            "its density ratios fixed; they are not PDK process sensitivities.",
            "Widths and branch current are derived by equations; lookup-point "
            "triplets are exhaustively enumerated only over the declared finite domain.",
        ],
        warnings=warnings,
    )
