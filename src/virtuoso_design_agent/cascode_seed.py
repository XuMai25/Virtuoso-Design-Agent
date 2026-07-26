"""Derive a bounded cascode bias/width neighborhood from one real MOS OP.

This is a deliberately small analytical compiler, not an optimizer claim.  It
uses a hash-bound OA->si->Spectre operating point to estimate threshold and
overdrive, then emits complete atomic tuples for the normal VDA executor.  The
result stays ``software_inference`` until every tuple is checked by real DC/AC.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator

from .models import (
    AtomicCandidate,
    AtomicCandidateSet,
    CandidateEvaluation,
    CandidateSetSource,
    DesignTarget,
    EvidenceSource,
    Operation,
    RunRecord,
    RunStatus,
    StrictModel,
    TaskSpec,
)


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CascodeSeedParameterMapping(_FiniteStrictModel):
    """Map the analytical roles onto topology-specific semantic parameters."""

    source_width: StrictStr = "device_width_um"
    source_length: StrictStr = "length_um"
    cascode_width: StrictStr = "cascode_width_um"
    cascode_length: StrictStr = "cascode_length_um"
    cascode_bias: StrictStr = "cascode_bias_v"

    @model_validator(mode="after")
    def names_are_distinct(self) -> "CascodeSeedParameterMapping":
        names = list(self.model_dump().values())
        if len(names) != len(set(names)):
            raise ValueError("cascode seed parameter mapping names must be distinct")
        return self


class CascodeSeedPolicy(_FiniteStrictModel):
    """User-declared boundary for one reusable same-polarity cascode seed."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_source_task_id: StrictStr = Field(min_length=1, max_length=128)
    expected_source_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_candidate_index: int = Field(ge=1)
    pdk_profile: StrictStr = Field(min_length=1, max_length=128)
    expected_topology_variant: StrictStr = Field(
        default="common_source",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    expected_device_model: StrictStr | None = Field(default=None, min_length=1)
    lower_saturation_margin_v: float = Field(default=0.05, gt=0.0)
    width_ratios: list[float] = Field(
        default_factory=lambda: [0.75, 1.0, 1.25],
        min_length=1,
        max_length=8,
    )
    bias_offsets_v: list[float] = Field(
        default_factory=lambda: [-0.025, 0.0, 0.025],
        min_length=1,
        max_length=8,
    )
    cascode_length_ratio: float = Field(default=1.0, gt=0.0)
    width_grid_um: float = Field(default=0.005, gt=0.0)
    length_grid_um: float = Field(default=0.001, gt=0.0)
    bias_grid_v: float = Field(default=0.005, gt=0.0)
    minimum_width_um: float = Field(default=0.1, gt=0.0)
    maximum_width_um: float = Field(default=10.0, gt=0.0)
    minimum_bias_v: float = Field(default=0.05, gt=0.0)
    maximum_bias_v: float = Field(default=1.2, gt=0.0)
    maximum_candidates: int = Field(default=16, ge=1, le=64)
    parameter_mapping: CascodeSeedParameterMapping = Field(
        default_factory=CascodeSeedParameterMapping
    )
    assume_same_model: Literal[True] = True
    assume_matching_fingers_and_multiplicity: Literal[True] = True
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT

    @field_validator("width_ratios")
    @classmethod
    def validate_width_ratios(cls, values: list[float]) -> list[float]:
        if any(value <= 0.0 for value in values):
            raise ValueError("cascode width ratios must be positive")
        if len(values) != len(set(values)):
            raise ValueError("cascode width ratios must be unique")
        if not any(math.isclose(value, 1.0, abs_tol=1e-12) for value in values):
            raise ValueError("cascode width ratios must include the 1:1 anchor")
        return values

    @field_validator("bias_offsets_v")
    @classmethod
    def validate_bias_offsets(cls, values: list[float]) -> list[float]:
        if len(values) != len(set(values)):
            raise ValueError("cascode bias offsets must be unique")
        if not any(math.isclose(value, 0.0, abs_tol=1e-12) for value in values):
            raise ValueError("cascode bias offsets must include the zero anchor")
        return values

    @model_validator(mode="after")
    def validate_domain(self) -> "CascodeSeedPolicy":
        if self.maximum_width_um <= self.minimum_width_um:
            raise ValueError("cascode maximum width must exceed minimum width")
        if self.maximum_bias_v <= self.minimum_bias_v:
            raise ValueError("cascode maximum bias must exceed minimum bias")
        combinations = len(self.width_ratios) * len(self.bias_offsets_v)
        if combinations > self.maximum_candidates:
            raise ValueError(
                "declared cascode width/bias domain exceeds maximum_candidates"
            )
        return self


class CascodeSeedOperatingPoint(_FiniteStrictModel):
    source_width_um: float = Field(gt=0.0)
    source_length_um: float = Field(gt=0.0)
    source_total_width_um: float = Field(gt=0.0)
    drain_current_ua: float = Field(gt=0.0)
    vgs_v: float = Field(gt=0.0)
    vdsat_v: float = Field(gt=0.0)
    estimated_threshold_v: float = Field(gt=0.0)
    target_internal_node_v: float = Field(gt=0.0)
    device_model: StrictStr = Field(min_length=1)


class CascodeSeedResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: StrictStr
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_id: StrictStr
    source_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_candidate_index: int = Field(ge=1)
    source_action: StrictStr
    source_target: DesignTarget
    source_topology_variant: StrictStr
    pdk_profile: StrictStr
    source_parameters: dict[StrictStr, float]
    operating_point: CascodeSeedOperatingPoint
    equations: dict[StrictStr, StrictStr]
    assumptions: list[StrictStr]
    candidate_set: AtomicCandidateSet
    declared_combinations: int = Field(ge=1)
    emitted_combinations: int = Field(ge=1)
    domain_exhausted: Literal[True] = True
    continuous_optimum_claim: Literal[False] = False
    global_optimum_claim: Literal[False] = False
    source_measurement_evidence: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    topology_and_target_evidence: Literal[EvidenceSource.BRIDGE_READBACK] = (
        EvidenceSource.BRIDGE_READBACK
    )
    seed_evidence: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    status: Literal[RunStatus.SUCCEEDED] = RunStatus.SUCCEEDED
    notes: list[StrictStr]


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


def _quantize(value: float, grid: float) -> float:
    decimal_value = Decimal(str(value))
    decimal_grid = Decimal(str(grid))
    steps = (decimal_value / decimal_grid).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return float(steps * decimal_grid)


def _mapping_value(mapping: Any, name: str, *, context: str) -> Any:
    if not isinstance(mapping, dict) or name not in mapping:
        raise ValueError(f"cascode seed source is missing {context} {name!r}")
    return mapping[name]


def _finite_float(value: Any, *, context: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cascode seed source has invalid {context}") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise ValueError(f"cascode seed source has invalid {context}")
    return number


def _source_candidate(
    run: RunRecord, policy: CascodeSeedPolicy
) -> CandidateEvaluation:
    matches = [
        candidate
        for candidate in run.candidates
        if candidate.index == policy.source_candidate_index
    ]
    if len(matches) != 1:
        raise ValueError("source run does not contain the exact declared candidate")
    candidate = matches[0]
    if candidate.evidence_source is not EvidenceSource.EDA_RESULT:
        raise ValueError("cascode seed requires a real EDA candidate")
    if not candidate.analysis_complete:
        raise ValueError("cascode seed source candidate is analysis-incomplete")
    return candidate


def _source_action(run: RunRecord, policy: CascodeSeedPolicy) -> tuple[str, dict]:
    action_name = f"simulation.candidate.{policy.source_candidate_index}"
    matches = [
        action
        for action in run.actions
        if action.action == action_name
        and action.status == "succeeded"
        and action.evidence_source is EvidenceSource.EDA_RESULT
    ]
    if len(matches) != 1:
        raise ValueError(
            "source run must contain one successful real-EDA candidate action"
        )
    return action_name, matches[0].details


def _source_bridge_profile(run: RunRecord) -> str:
    matches = [
        action
        for action in run.actions
        if action.action == "bridge.probe"
        and action.status == "succeeded"
        and action.evidence_source is EvidenceSource.BRIDGE_READBACK
    ]
    if len(matches) != 1:
        raise ValueError(
            "source run must contain one successful bridge_readback profile probe"
        )
    details = matches[0].details
    if details.get("connected") is not True:
        raise ValueError("cascode seed source Bridge probe is not connected")
    profile = details.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ValueError("cascode seed source Bridge probe is missing its profile")
    return profile


def _matched_number(left: Any, right: Any, *, context: str) -> float:
    left_number = _finite_float(left, context=context)
    right_number = _finite_float(right, context=context)
    if not math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"cascode seed source {context} is internally inconsistent")
    return left_number


def derive_cascode_seed(
    policy: CascodeSeedPolicy,
    source_run_path: Path,
) -> CascodeSeedResult:
    """Compile an OP-derived finite cascode neighborhood without touching OA."""

    source_run_sha256 = _file_sha256(source_run_path)
    if source_run_sha256 != policy.expected_source_run_sha256:
        raise ValueError("cascode seed source run SHA-256 mismatch")
    run = RunRecord.model_validate_json(source_run_path.read_text(encoding="utf-8"))
    if run.task_id != policy.expected_source_task_id:
        raise ValueError("cascode seed source task identity mismatch")
    if run.status is not RunStatus.SUCCEEDED:
        raise ValueError("cascode seed requires a successful source run")
    if run.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("cascode seed requires the real Bridge adapter")
    source_profile = _source_bridge_profile(run)
    if source_profile != policy.pdk_profile:
        raise ValueError(
            "cascode seed source PDK profile mismatch: "
            f"expected {policy.pdk_profile!r}, got {source_profile!r}"
        )

    candidate = _source_candidate(run, policy)
    action_name, details = _source_action(run, policy)
    action_parameters = _mapping_value(
        details, "parameters", context="action field"
    )
    for name, value in candidate.parameters.items():
        _matched_number(
            value,
            _mapping_value(action_parameters, name, context="action parameter"),
            context=f"candidate/action parameter {name}",
        )

    evidence = _mapping_value(details, "evidence", context="action field")
    schematic = _mapping_value(
        evidence, "schematic_readback", context="evidence block"
    )
    netlist = _mapping_value(evidence, "netlist", context="evidence block")
    operating_point = _mapping_value(
        evidence, "operating_point", context="evidence block"
    )
    if schematic.get("source") != EvidenceSource.BRIDGE_READBACK.value:
        raise ValueError("cascode seed requires bridge_readback topology evidence")
    topology_variant = str(schematic.get("topology_variant") or "")
    if topology_variant != policy.expected_topology_variant:
        raise ValueError(
            "cascode seed source topology mismatch: "
            f"expected {policy.expected_topology_variant!r}, got {topology_variant!r}"
        )
    if (
        netlist.get("source") != EvidenceSource.EDA_RESULT.value
        or netlist.get("generator") != "Cadence si -batch"
        or netlist.get("parameter_consistency") != "matched"
    ):
        raise ValueError("cascode seed requires a parameter-matched real si netlist")
    target = DesignTarget.model_validate(
        _mapping_value(schematic, "target", context="schematic readback field")
    )

    mapping = policy.parameter_mapping
    source_width = _finite_float(
        _mapping_value(candidate.parameters, mapping.source_width, context="parameter"),
        context=mapping.source_width,
        positive=True,
    )
    source_length = _finite_float(
        _mapping_value(candidate.parameters, mapping.source_length, context="parameter"),
        context=mapping.source_length,
        positive=True,
    )
    geometry = _mapping_value(
        schematic, "device_geometry", context="schematic readback field"
    )
    source_total_width = _finite_float(
        _mapping_value(geometry, "total_width_um", context="device geometry"),
        context="source total width",
        positive=True,
    )
    source_finger_width = _matched_number(
        source_width,
        _mapping_value(geometry, "finger_width_um", context="device geometry"),
        context="source/geometry finger width",
    )
    source_fingers = _finite_float(
        _mapping_value(geometry, "fingers", context="device geometry"),
        context="source fingers",
        positive=True,
    )
    source_multiplicity = _finite_float(
        _mapping_value(geometry, "multiplicity", context="device geometry"),
        context="source multiplicity",
        positive=True,
    )
    if not (
        math.isclose(source_fingers, 1.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(source_multiplicity, 1.0, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError(
            "current cascode seed requires unit source fingers and multiplicity; "
            "MNCAS count parameters are not part of this candidate mapping"
        )
    _matched_number(
        source_total_width,
        source_finger_width * source_fingers * source_multiplicity,
        context="source geometry total width",
    )
    instances = _mapping_value(netlist, "instances", context="netlist field")
    mn0 = _mapping_value(instances, "MN0", context="netlist instance")
    device_model = str(_mapping_value(mn0, "model", context="MN0 field"))
    if policy.expected_device_model is not None and (
        device_model != policy.expected_device_model
    ):
        raise ValueError("cascode seed source device model mismatch")
    _matched_number(
        source_length,
        _mapping_value(mn0, "length_um", context="MN0 field"),
        context="source/netlist length",
    )
    _matched_number(
        source_finger_width,
        _mapping_value(mn0, "finger_width_um", context="MN0 field"),
        context="source/netlist finger width",
    )
    _matched_number(
        source_fingers,
        _mapping_value(mn0, "fingers", context="MN0 field"),
        context="source/netlist fingers",
    )
    _matched_number(
        source_multiplicity,
        _mapping_value(mn0, "multiplicity", context="MN0 field"),
        context="source/netlist multiplicity",
    )
    _matched_number(
        source_total_width,
        _mapping_value(mn0, "total_width_um", context="MN0 field"),
        context="source/netlist total width",
    )

    metrics = candidate.metrics
    detail_metrics = _mapping_value(details, "metrics", context="action field")
    for metric in ("vgs_v", "vdsat_v", "drain_current_ua"):
        if candidate.metric_sources.get(metric) is not EvidenceSource.EDA_RESULT:
            raise ValueError(f"cascode seed metric {metric!r} is not eda_result")
        _matched_number(
            _mapping_value(metrics, metric, context="candidate metric"),
            _mapping_value(detail_metrics, metric, context="action metric"),
            context=f"candidate/action metric {metric}",
        )
    if _finite_float(metrics.get("saturation_region"), context="saturation region") < 1.0:
        raise ValueError("cascode seed source device is not in saturation")
    vgs_v = abs(_finite_float(metrics["vgs_v"], context="vgs_v"))
    vdsat_v = abs(_finite_float(metrics["vdsat_v"], context="vdsat_v"))
    drain_current_ua = abs(
        _finite_float(metrics["drain_current_ua"], context="drain_current_ua")
    )
    threshold_v = vgs_v - vdsat_v
    if threshold_v <= 0.0:
        raise ValueError("cascode seed VGS/VDSAT do not yield a positive VTH estimate")
    target_internal_node_v = vdsat_v + policy.lower_saturation_margin_v

    width_ratios = sorted(
        policy.width_ratios,
        key=lambda value: (abs(math.log(value)), value),
    )
    bias_offsets = sorted(
        policy.bias_offsets_v,
        key=lambda value: (abs(value), value),
    )
    prepared: list[AtomicCandidate] = []
    seen: set[tuple[float, float, float]] = set()
    for ratio in width_ratios:
        cascode_width = _quantize(source_width * ratio, policy.width_grid_um)
        cascode_length = _quantize(
            source_length * policy.cascode_length_ratio,
            policy.length_grid_um,
        )
        if not policy.minimum_width_um <= cascode_width <= policy.maximum_width_um:
            raise ValueError("quantized cascode width is outside declared bounds")
        relative_overdrive = math.sqrt(
            (source_width * cascode_length)
            / (cascode_width * source_length)
        )
        cascode_overdrive_v = vdsat_v * relative_overdrive
        cascode_vgs_v = threshold_v + cascode_overdrive_v
        bias_center_v = target_internal_node_v + cascode_vgs_v
        for offset_v in bias_offsets:
            cascode_bias_v = _quantize(
                bias_center_v + offset_v, policy.bias_grid_v
            )
            if not policy.minimum_bias_v <= cascode_bias_v <= policy.maximum_bias_v:
                raise ValueError("quantized cascode bias is outside declared bounds")
            signature = (cascode_width, cascode_length, cascode_bias_v)
            if signature in seen:
                raise ValueError(
                    "cascode seed quantization collapsed distinct declared points"
                )
            seen.add(signature)
            prepared.append(
                AtomicCandidate(
                    id=f"cascode-seed-{len(prepared) + 1:03d}",
                    parameters={
                        mapping.cascode_width: cascode_width,
                        mapping.cascode_length: cascode_length,
                        mapping.cascode_bias: cascode_bias_v,
                    },
                    predicted_metrics={
                        "source_drain_current_ua": drain_current_ua,
                        "estimated_threshold_v": threshold_v,
                        "predicted_internal_node_v": target_internal_node_v,
                        "predicted_lower_saturation_margin_v": (
                            policy.lower_saturation_margin_v
                        ),
                        "predicted_cascode_overdrive_v": cascode_overdrive_v,
                        "predicted_cascode_vgs_v": cascode_vgs_v,
                        "predicted_cascode_bias_center_v": bias_center_v,
                        "declared_bias_offset_v": offset_v,
                        "declared_width_ratio": ratio,
                    },
                )
            )

    policy_sha256 = _canonical_sha256(policy.model_dump(mode="json"))
    candidate_set = AtomicCandidateSet(
        source=CandidateSetSource(
            generator="vda.cascode-seed",
            id=policy.id,
            bindings={
                "policy_sha256": policy_sha256,
                "source_run_sha256": source_run_sha256,
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        ),
        candidates=prepared,
    )
    return CascodeSeedResult(
        policy_id=policy.id,
        policy_sha256=policy_sha256,
        source_task_id=run.task_id,
        source_run_sha256=source_run_sha256,
        source_candidate_index=policy.source_candidate_index,
        source_action=action_name,
        source_target=target,
        source_topology_variant=topology_variant,
        pdk_profile=policy.pdk_profile,
        source_parameters=dict(candidate.parameters),
        operating_point=CascodeSeedOperatingPoint(
            source_width_um=source_width,
            source_length_um=source_length,
            source_total_width_um=source_total_width,
            drain_current_ua=drain_current_ua,
            vgs_v=vgs_v,
            vdsat_v=vdsat_v,
            estimated_threshold_v=threshold_v,
            target_internal_node_v=target_internal_node_v,
            device_model=device_model,
        ),
        equations={
            "threshold": "VTH_est = abs(VGS_source) - abs(VDSAT_source)",
            "internal_node": "V_NCAS = abs(VDSAT_source) + declared_margin",
            "cascode_overdrive": (
                "VOV_cas = abs(VDSAT_source) * "
                "sqrt((W_source*L_cas)/(W_cas*L_source))"
            ),
            "cascode_bias": "V_CAS = V_NCAS + VTH_est + VOV_cas + offset",
        },
        assumptions=[
            "VDSAT is used only as a local overdrive proxy for finite seeding",
            "source and cascode devices use the same model and matching fingers/multiplicity",
            "the source operating current is the target cascode current",
            "every emitted tuple still requires OA readback, si netlist, DC, and AC evidence",
        ],
        candidate_set=candidate_set,
        declared_combinations=len(policy.width_ratios) * len(policy.bias_offsets_v),
        emitted_combinations=len(prepared),
        notes=[
            "The first tuple is the 1:1-width, zero-offset analytical anchor.",
            "This finite neighborhood is physics-seeded; it is not a continuous or global optimum.",
            "Measured DC feasibility and AC gain/bandwidth/GBW must decide the final tuple.",
        ],
    )


def build_task_from_cascode_seed(
    result_path: Path,
    task_template_path: Path,
) -> TaskSpec:
    """Bind a cascode seed artifact into a normal atomic VDA tuning task."""

    result = CascodeSeedResult.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    raw = json.loads(task_template_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("cascode seed task template must contain one JSON object")
    for name in (
        "candidate_set",
        "parameter_space",
        "instance_parameter_space",
        "theory_seed",
    ):
        if raw.get(name):
            raise ValueError(f"cascode seed task template must not predeclare {name}")
        raw.pop(name, None)

    template_parameters = raw.get("parameters", {})
    if not isinstance(template_parameters, dict):
        raise ValueError("cascode seed task template parameters must be an object")
    mismatches = {
        name: {"expected": expected, "actual": template_parameters.get(name)}
        for name, expected in result.source_parameters.items()
        if name not in template_parameters
        or not math.isclose(
            float(template_parameters[name]),
            expected,
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    }
    if mismatches:
        raise ValueError(
            "cascode seed task fixed parameters changed from the source OP: "
            + json.dumps(mismatches, sort_keys=True)
        )

    source = result.candidate_set.source
    bindings = dict(source.bindings)
    bindings.update(
        {
            "cascode_seed_result_sha256": _file_sha256(result_path),
            "task_template_sha256": _file_sha256(task_template_path),
        }
    )
    raw["candidate_set"] = result.candidate_set.model_copy(
        update={"source": source.model_copy(update={"bindings": bindings})}
    ).model_dump(mode="json")
    task = TaskSpec.model_validate(raw)
    if task.operation not in {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}:
        raise ValueError("cascode seed output must be a tuning operation")
    if task.circuit.value != "common_source":
        raise ValueError("cascode seed output requires circuit='common_source'")
    if task.pdk_profile != result.pdk_profile:
        raise ValueError("cascode seed PDK profile does not match task template")
    if task.target != result.source_target:
        raise ValueError("cascode seed target does not match its source OA cellview")
    if task.limits.max_iterations < result.emitted_combinations:
        raise ValueError("cascode seed task budget cannot exhaust the emitted domain")
    return task


__all__ = [
    "CascodeSeedOperatingPoint",
    "CascodeSeedParameterMapping",
    "CascodeSeedPolicy",
    "CascodeSeedResult",
    "build_task_from_cascode_seed",
    "derive_cascode_seed",
]
