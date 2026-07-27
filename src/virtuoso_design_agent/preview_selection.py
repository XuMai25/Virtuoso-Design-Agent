"""Select cheap Spectre previews and audit them against same-domain OA truth."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from statistics import fmean
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator

from .models import (
    CircuitKind,
    EvidenceSource,
    ObjectiveGoal,
    Operation,
    Relation,
    RunRecord,
    RunStatus,
    SelectionScope,
    StrictModel,
    TaskSpec,
)
from .netlist_preview import render_spectre_preview_deck
from .planner import build_plan
from .profiles import load_pdk_profile


_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_METRIC_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PreviewMetricConstraint(_FiniteStrictModel):
    metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    relation: Relation
    value: float
    tolerance: float = Field(default=0.0, ge=0.0)
    expected_evidence_source: Literal[
        EvidenceSource.EDA_RESULT,
        EvidenceSource.SOFTWARE_INFERENCE,
    ]

    @model_validator(mode="after")
    def target_requires_tolerance(self) -> "PreviewMetricConstraint":
        if self.relation is Relation.TARGET and self.tolerance <= 0.0:
            raise ValueError("target preview constraints require tolerance > 0")
        return self


class PreviewSelectionObjective(_FiniteStrictModel):
    preview_metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    reference_metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    goal: ObjectiveGoal
    preview_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    reference_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )


class PreviewReferenceMetricRule(_FiniteStrictModel):
    preview_metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    reference_metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    preview_evidence_source: Literal[
        EvidenceSource.EDA_RESULT,
        EvidenceSource.SOFTWARE_INFERENCE,
    ] = EvidenceSource.EDA_RESULT
    reference_evidence_source: Literal[
        EvidenceSource.EDA_RESULT,
        EvidenceSource.SOFTWARE_INFERENCE,
    ] = EvidenceSource.EDA_RESULT


class PreviewSelectionPolicy(_FiniteStrictModel):
    """Hash-bound policy for a cheap-preview-to-OA-reference comparison."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=_ID_PATTERN)
    assessment_mode: Literal[
        "retrospective_calibration",
        "prospective_validation",
    ]
    expected_preview_task_id: StrictStr = Field(min_length=1, max_length=128)
    expected_preview_task_sha256: str = Field(pattern=_HASH_PATTERN)
    expected_preview_run_sha256: str = Field(pattern=_HASH_PATTERN)
    expected_reference_task_id: StrictStr = Field(min_length=1, max_length=128)
    expected_reference_run_sha256: str = Field(pattern=_HASH_PATTERN)
    expected_analysis: Literal["dc", "ac"]
    expected_pdk_profile: StrictStr = Field(min_length=1, max_length=128)
    expected_candidate_generator: StrictStr = Field(pattern=_ID_PATTERN)
    expected_candidate_source_id: StrictStr = Field(pattern=_ID_PATTERN)
    candidate_source_sha256: str = Field(pattern=_HASH_PATTERN)
    reference_candidate_source_binding: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    constraints: list[PreviewMetricConstraint] = Field(
        min_length=1,
        max_length=32,
    )
    objective: PreviewSelectionObjective
    comparison_metrics: list[PreviewReferenceMetricRule] = Field(
        min_length=1,
        max_length=32,
    )
    shortlist_size: int = Field(default=3, ge=1, le=16)
    minimum_artifact_count_per_variant: int = Field(default=8, ge=1, le=64)
    minimum_ac_sample_count: int = Field(default=2, ge=2)
    minimum_feasibility_agreement: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )
    minimum_reference_feasible_recall: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )
    minimum_spearman_rank_correlation: float = Field(
        default=0.7,
        ge=-1.0,
        le=1.0,
    )
    require_reference_winner_in_shortlist: bool = True
    require_preview_winner_match: bool = False
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT

    @model_validator(mode="after")
    def validate_metric_contract(self) -> "PreviewSelectionPolicy":
        constraint_names = [item.metric for item in self.constraints]
        if len(constraint_names) != len(set(constraint_names)):
            raise ValueError("preview selection constraints must name unique metrics")
        pairs = [
            (item.preview_metric, item.reference_metric)
            for item in self.comparison_metrics
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("preview/reference comparison metric pairs must be unique")
        return self


class PreviewConstraintEvaluation(_FiniteStrictModel):
    metric: StrictStr
    relation: Relation
    expected: float
    actual: float
    tolerance: float = Field(ge=0.0)
    passed: bool
    evidence_source: EvidenceSource


class PreviewMetricComparison(_FiniteStrictModel):
    preview_metric: StrictStr
    reference_metric: StrictStr
    preview_value: float
    reference_value: float
    signed_error_percent: float
    absolute_error_percent: float = Field(ge=0.0)
    preview_evidence_source: EvidenceSource
    reference_evidence_source: EvidenceSource


class PreviewCandidateSelection(_FiniteStrictModel):
    index: int = Field(ge=1)
    preview_variant_id: StrictStr
    source_candidate_id: StrictStr
    preview_feasible: bool
    reference_feasible: bool
    preview_objective_value: float
    reference_objective_value: float
    preview_rank: float = Field(ge=1.0)
    reference_rank: float = Field(ge=1.0)
    constraints: list[PreviewConstraintEvaluation]
    comparisons: list[PreviewMetricComparison]


class PreviewMetricErrorSummary(_FiniteStrictModel):
    preview_metric: StrictStr
    reference_metric: StrictStr
    candidate_count: int = Field(ge=1)
    mean_absolute_error_percent: float = Field(ge=0.0)
    maximum_absolute_error_percent: float = Field(ge=0.0)
    maximum_error_candidate_id: StrictStr


class PreviewSelectionResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: StrictStr
    policy_sha256: str = Field(pattern=_HASH_PATTERN)
    assessment_mode: Literal[
        "retrospective_calibration",
        "prospective_validation",
    ]
    preview_task_id: StrictStr
    preview_task_sha256: str = Field(pattern=_HASH_PATTERN)
    preview_run_sha256: str = Field(pattern=_HASH_PATTERN)
    reference_task_id: StrictStr
    reference_run_sha256: str = Field(pattern=_HASH_PATTERN)
    plan_token: StrictStr
    status: RunStatus
    pdk_profile: StrictStr
    analysis: Literal["dc", "ac"]
    candidate_generator: StrictStr
    candidate_source_id: StrictStr
    candidate_source_sha256: str = Field(pattern=_HASH_PATTERN)
    declared_candidate_count: int = Field(ge=1)
    evaluated_candidate_count: int = Field(ge=1)
    shortlist_size: int = Field(ge=0)
    shortlist_candidate_ids: list[StrictStr]
    shortlist_variant_ids: list[StrictStr]
    preview_winner_candidate_id: StrictStr | None
    reference_winner_candidate_id: StrictStr
    winner_agreement: bool
    reference_winner_in_shortlist: bool
    spearman_rank_correlation: float = Field(ge=-1.0, le=1.0)
    feasibility_agreement_fraction: float = Field(ge=0.0, le=1.0)
    reference_feasible_recall: float = Field(ge=0.0, le=1.0)
    artifact_integrity_gate_passed: Literal[True] = True
    feasibility_gate_passed: bool
    rank_correlation_gate_passed: bool
    winner_retention_gate_passed: bool
    selection_utility_gate_passed: bool
    selection_scope: Literal[
        SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
    ] = SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
    continuous_optimum_claim: Literal[False] = False
    global_optimum_claim: Literal[False] = False
    candidates: list[PreviewCandidateSelection]
    metric_error_summaries: list[PreviewMetricErrorSummary]
    preview_measurement_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    reference_measurement_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )
    selection_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    policy_evidence_source: Literal[EvidenceSource.USER_INPUT] = (
        EvidenceSource.USER_INPUT
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
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _finite_metric_map(value: object, label: str) -> dict[str, float]:
    raw = _require_mapping(value, label)
    metrics: dict[str, float] = {}
    for name, number in raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label} contains an invalid metric name")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{label} metric {name!r} is not numeric")
        converted = float(number)
        if not math.isfinite(converted):
            raise ValueError(f"{label} metric {name!r} is not finite")
        metrics[name] = converted
    return metrics


def _source_map(value: object, label: str) -> dict[str, EvidenceSource]:
    raw = _require_mapping(value, label)
    sources: dict[str, EvidenceSource] = {}
    for name, source in raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label} contains an invalid metric name")
        try:
            sources[name] = EvidenceSource(source)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} metric {name!r} has an invalid source") from exc
    return sources


def _manifest_fingerprint(manifest: list[dict]) -> str:
    return _canonical_sha256(manifest)


def _validate_manifest(
    variant_id: str,
    raw_variant: dict,
    *,
    minimum_count: int,
    expected_deck_sha256: str,
) -> None:
    manifest_raw = raw_variant.get("artifact_manifest")
    if not isinstance(manifest_raw, list) or len(manifest_raw) < minimum_count:
        raise ValueError(
            f"preview variant {variant_id} has an incomplete artifact manifest"
        )
    manifest: list[dict] = []
    seen_paths: set[str] = set()
    for item in manifest_raw:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise ValueError(
                f"preview variant {variant_id} has a malformed manifest row"
            )
        path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if not isinstance(path, str) or not path or path in seen_paths:
            raise ValueError(
                f"preview variant {variant_id} has an invalid manifest path"
            )
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError(
                f"preview variant {variant_id} has an unsafe manifest path"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError(
                f"preview variant {variant_id} has an empty manifest artifact"
            )
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(
                f"preview variant {variant_id} has an invalid manifest hash"
            )
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError(
                f"preview variant {variant_id} has an invalid manifest hash"
            ) from exc
        manifest.append(dict(item))
        seen_paths.add(path)
    if raw_variant.get("manifest_sha256") != _manifest_fingerprint(manifest):
        raise ValueError(f"preview variant {variant_id} manifest hash mismatch")
    deck_rows = [
        item
        for item in manifest
        if item["path"] == f"preview_{variant_id}.scs"
    ]
    if len(deck_rows) != 1 or deck_rows[0]["sha256"] != expected_deck_sha256:
        raise ValueError(
            f"preview variant {variant_id} deck is not bound into its manifest"
        )


def _metric(
    metrics: dict[str, float],
    sources: dict[str, EvidenceSource],
    variant_id: str,
    metric: str,
    expected_source: EvidenceSource,
) -> float:
    full_name = f"{variant_id}__{metric}"
    if full_name not in metrics:
        raise ValueError(
            f"preview variant {variant_id} is missing metric {metric!r}"
        )
    if sources.get(full_name) is not expected_source:
        raise ValueError(
            f"preview metric {full_name!r} evidence source mismatch"
        )
    return metrics[full_name]


def _constraint_passed(rule: PreviewMetricConstraint, actual: float) -> bool:
    if rule.relation is Relation.LESS_OR_EQUAL:
        return actual <= rule.value + rule.tolerance
    if rule.relation is Relation.GREATER_OR_EQUAL:
        return actual >= rule.value - rule.tolerance
    return abs(actual - rule.value) <= rule.tolerance


def _average_ranks(
    values: list[float],
    goal: ObjectiveGoal,
) -> list[float]:
    ordered = sorted(
        range(len(values)),
        key=lambda index: (
            -values[index] if goal is ObjectiveGoal.MAXIMIZE else values[index],
            index,
        ),
    )
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        average = ((start + 1) + end) / 2.0
        for position in range(start, end):
            ranks[ordered[position]] = average
        start = end
    return ranks


def _spearman(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("rank vectors must be nonempty and equal length")
    if len(left) == 1:
        return 1.0
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    left_energy = sum((value - left_mean) ** 2 for value in left)
    right_energy = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_energy * right_energy)
    if denominator == 0.0:
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)


def _reference_candidate_id(candidate) -> str:
    identifiers = [
        value
        for value in (
            candidate.atomic_candidate_id,
            candidate.theory_seed_candidate_id,
        )
        if value is not None
    ]
    if len(identifiers) != 1:
        raise ValueError(
            "reference candidates must have exactly one atomic/theory identity"
        )
    return str(identifiers[0])


def _selected_reference_candidate(run: RunRecord):
    if run.selected_parameters is None:
        raise ValueError("reference run has no selected parameters")
    matches = [
        candidate
        for candidate in run.candidates
        if all(
            name in candidate.parameters
            and _same_number(candidate.parameters[name], value)
            for name, value in run.selected_parameters.items()
        )
    ]
    if len(matches) != 1 or not matches[0].feasible:
        raise ValueError(
            "reference selected parameters do not identify one feasible candidate"
        )
    return matches[0]


def validate_preview_selection(
    policy: PreviewSelectionPolicy,
    preview_task_path: Path,
    preview_run_path: Path,
    reference_run_path: Path,
) -> PreviewSelectionResult:
    """Validate preview integrity, produce a shortlist, and compare OA truth."""

    task_sha256 = _file_sha256(preview_task_path)
    preview_run_sha256 = _file_sha256(preview_run_path)
    reference_run_sha256 = _file_sha256(reference_run_path)
    if task_sha256 != policy.expected_preview_task_sha256:
        raise ValueError("preview selection task SHA-256 mismatch")
    if preview_run_sha256 != policy.expected_preview_run_sha256:
        raise ValueError("preview selection run SHA-256 mismatch")
    if reference_run_sha256 != policy.expected_reference_run_sha256:
        raise ValueError("preview selection reference run SHA-256 mismatch")

    task = TaskSpec.model_validate_json(
        preview_task_path.read_text(encoding="utf-8")
    )
    preview_run = RunRecord.model_validate_json(
        preview_run_path.read_text(encoding="utf-8")
    )
    reference_run = RunRecord.model_validate_json(
        reference_run_path.read_text(encoding="utf-8")
    )
    if (
        task.id != policy.expected_preview_task_id
        or preview_run.task_id != task.id
    ):
        raise ValueError("preview selection task identity mismatch")
    if reference_run.task_id != policy.expected_reference_task_id:
        raise ValueError("preview selection reference task identity mismatch")
    if (
        task.operation is not Operation.SIMULATION_RUN
        or task.circuit is not CircuitKind.NETLIST_PREVIEW
        or task.target is not None
        or task.netlist_preview is None
    ):
        raise ValueError("preview selection requires a target-free netlist preview")
    if (
        not task.safety.allow_remote_compute
        or task.safety.allow_remote_write
        or task.safety.replace_existing
    ):
        raise ValueError("preview selection task has an invalid safety boundary")
    if task.analysis is None or task.analysis.value != policy.expected_analysis:
        raise ValueError("preview selection analysis mismatch")
    if task.pdk_profile != policy.expected_pdk_profile:
        raise ValueError("preview selection PDK profile mismatch")
    expected_token = build_plan(task).confirmation_token
    if preview_run.plan_token != expected_token:
        raise ValueError("preview selection plan token mismatch")
    if (
        preview_run.status is not RunStatus.SUCCEEDED
        or preview_run.adapter != "virtuoso-bridge-subprocess"
    ):
        raise ValueError("preview selection requires a successful real Bridge run")

    probe_actions = [
        action
        for action in preview_run.actions
        if action.action == "bridge.spectre.probe"
    ]
    if len(probe_actions) != 1:
        raise ValueError("preview run must contain one Spectre capability probe")
    probe = probe_actions[0]
    if (
        probe.status != "succeeded"
        or probe.evidence_source is not EvidenceSource.BRIDGE_READBACK
        or probe.details.get("connected") is not True
        or probe.details.get("profile") != task.pdk_profile
        or probe.details.get("virtuoso_started") is not False
        or probe.details.get("oa_access_performed") is not False
        or probe.details.get("oa_write_performed") is not False
    ):
        raise ValueError("preview Spectre probe does not prove the no-OA boundary")

    eda_actions = [
        action
        for action in preview_run.actions
        if action.evidence_source is EvidenceSource.EDA_RESULT
    ]
    if (
        len(eda_actions) != 1
        or eda_actions[0].action != "simulation.candidate.1"
        or eda_actions[0].status != "succeeded"
    ):
        raise ValueError("preview run must contain one successful EDA action")
    action = eda_actions[0]
    if (
        action.details.get("analysis_complete") is not True
        or action.details.get("analysis_issues") != []
    ):
        raise ValueError("preview EDA action is incomplete")
    metrics = _finite_metric_map(action.details.get("metrics"), "preview metrics")
    metric_sources = _source_map(
        action.details.get("metric_sources"),
        "preview metric sources",
    )
    if len(preview_run.candidates) != 1:
        raise ValueError("netlist preview run must have one aggregate candidate")
    aggregate = preview_run.candidates[0]
    if (
        aggregate.evidence_source is not EvidenceSource.EDA_RESULT
        or not aggregate.analysis_complete
        or aggregate.analysis_issues
        or aggregate.metrics != metrics
        or aggregate.metric_sources != metric_sources
    ):
        raise ValueError("preview aggregate candidate does not match its EDA action")

    evidence = _require_mapping(action.details.get("evidence"), "preview evidence")
    spec = task.netlist_preview
    if (
        evidence.get("source") != EvidenceSource.EDA_RESULT.value
        or evidence.get("preview_spec_sha256") != spec.canonical_sha256()
        or evidence.get("preview_spec_source") != EvidenceSource.USER_INPUT.value
        or evidence.get("source_bindings") != spec.source_bindings
        or evidence.get("source_bindings_evidence_source")
        != EvidenceSource.SOFTWARE_INFERENCE.value
        or evidence.get("variant_source_ids") != spec.variant_source_ids
        or evidence.get("variant_source_ids_evidence_source")
        != EvidenceSource.SOFTWARE_INFERENCE.value
        or evidence.get("analysis") != policy.expected_analysis
        or evidence.get("analysis_source") != EvidenceSource.USER_INPUT.value
        or evidence.get("pdk_profile") != task.pdk_profile
        or evidence.get("non_overwrite_preflight") != "absent"
    ):
        raise ValueError("preview EDA evidence does not match the compiled task")
    if spec.source_bindings.get("candidate_source_sha256") != (
        policy.candidate_source_sha256
    ):
        raise ValueError("preview candidate source SHA-256 mismatch")
    if spec.variant_source_ids_evidence_source != "software_inference":
        raise ValueError("preview variant mapping is not software_inference")

    remote_root = evidence.get("remote_run_root")
    if not isinstance(remote_root, str) or not remote_root.startswith("/data/xum/"):
        raise ValueError("preview remote root is outside /data/xum")
    variants_raw = _require_mapping(evidence.get("variants"), "preview variants")
    declared_variant_ids = [variant.id for variant in spec.variants]
    if set(variants_raw) != set(declared_variant_ids):
        raise ValueError("preview evidence variant set does not match the task")
    if not spec.variant_source_ids:
        raise ValueError("preview task has no candidate identity mapping")
    candidate_variant_ids = list(spec.variant_source_ids)
    candidate_ids = [spec.variant_source_ids[item] for item in candidate_variant_ids]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("preview source candidate identities are not unique")
    if policy.shortlist_size > len(candidate_ids):
        raise ValueError("preview shortlist is larger than the candidate domain")

    profile = load_pdk_profile(task.pdk_profile).model_dump(mode="json")
    ac_sweep = task.ac_sweep.model_dump(mode="json") if task.ac_sweep else None
    for variant_id in declared_variant_ids:
        raw_variant = _require_mapping(
            variants_raw.get(variant_id),
            f"preview variant {variant_id}",
        )
        if (
            raw_variant.get("analysis_complete") is not True
            or raw_variant.get("analysis_issues") != []
        ):
            raise ValueError(f"preview variant {variant_id} is incomplete")
        if policy.expected_analysis == "ac":
            response = _require_mapping(
                raw_variant.get("ac_response"),
                f"preview variant {variant_id} AC response",
            )
            if (
                response.get("analysis_complete") is not True
                or response.get("issues") != []
                or not isinstance(response.get("sample_count"), int)
                or response["sample_count"] < policy.minimum_ac_sample_count
            ):
                raise ValueError(
                    f"preview variant {variant_id} has an incomplete AC waveform"
                )
        lifecycle = _require_mapping(
            raw_variant.get("process_lifecycle"),
            f"preview variant {variant_id} process lifecycle",
        )
        if lifecycle.get("bounded_remote_process") is not True:
            raise ValueError(f"preview variant {variant_id} process was not bounded")
        variant_root = raw_variant.get("remote_run_root")
        simulation_dir = raw_variant.get("remote_simulation_dir")
        expected_prefix = f"{remote_root}/{variant_id}"
        if (
            not isinstance(variant_root, str)
            or variant_root != expected_prefix
            or not isinstance(simulation_dir, str)
            or not simulation_dir.startswith(f"{expected_prefix}/")
        ):
            raise ValueError(f"preview variant {variant_id} remote path mismatch")
        rendered = render_spectre_preview_deck(
            spec,
            variant_id,
            profile,
            analysis=policy.expected_analysis,
            ac_sweep=ac_sweep,
        )
        deck_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        if raw_variant.get("deck_sha256") != deck_sha256:
            raise ValueError(f"preview variant {variant_id} rendered deck hash mismatch")
        _validate_manifest(
            variant_id,
            raw_variant,
            minimum_count=policy.minimum_artifact_count_per_variant,
            expected_deck_sha256=deck_sha256,
        )

    audit = reference_run.search_audit
    if (
        reference_run.status is not RunStatus.SUCCEEDED
        or reference_run.adapter != "virtuoso-bridge-subprocess"
        or audit is None
        or not audit.domain_exhausted
        or audit.selection_scope
        is not SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
        or audit.declared_candidate_count != len(candidate_ids)
        or audit.attempted_candidate_count != len(candidate_ids)
        or audit.completed_candidate_count != len(candidate_ids)
        or len(reference_run.candidates) != len(candidate_ids)
    ):
        raise ValueError("reference run did not exhaust the exact real EDA domain")
    if audit.candidate_set_source is not None:
        source = audit.candidate_set_source
        source_id = source.id
        reference_bindings = source.bindings
    elif audit.theory_seed_source is not None:
        source = audit.theory_seed_source
        source_id = source.policy_id
        reference_bindings = {}
    else:
        raise ValueError("reference run has no candidate source provenance")
    if (
        source.generator != policy.expected_candidate_generator
        or source_id != policy.expected_candidate_source_id
        or source.evidence_source is not EvidenceSource.SOFTWARE_INFERENCE
    ):
        raise ValueError("reference candidate source identity mismatch")
    if policy.reference_candidate_source_binding is not None:
        if reference_bindings.get(policy.reference_candidate_source_binding) != (
            policy.candidate_source_sha256
        ):
            raise ValueError("reference candidate source SHA-256 binding mismatch")

    reference_by_id = {}
    ordered_reference_ids: list[str] = []
    for index, candidate in enumerate(reference_run.candidates, start=1):
        candidate_id = _reference_candidate_id(candidate)
        ordered_reference_ids.append(candidate_id)
        if (
            candidate.index != index
            or candidate.evidence_source is not EvidenceSource.EDA_RESULT
            or not candidate.analysis_complete
            or candidate.analysis_issues
        ):
            raise ValueError(f"reference candidate {candidate_id} is incomplete")
        reference_by_id[candidate_id] = candidate
    if ordered_reference_ids != candidate_ids:
        raise ValueError("preview/reference candidate identity or order mismatch")

    selected_reference = _selected_reference_candidate(reference_run)
    reference_winner_id = _reference_candidate_id(selected_reference)
    reference_objective_values: list[float] = []
    preview_objective_values: list[float] = []
    constraint_sets: list[list[PreviewConstraintEvaluation]] = []
    preview_feasible: list[bool] = []
    comparisons_by_candidate: list[list[PreviewMetricComparison]] = []
    for variant_id, candidate_id in zip(
        candidate_variant_ids,
        candidate_ids,
        strict=True,
    ):
        candidate = reference_by_id[candidate_id]
        constraint_evaluations: list[PreviewConstraintEvaluation] = []
        for rule in policy.constraints:
            actual = _metric(
                metrics,
                metric_sources,
                variant_id,
                rule.metric,
                rule.expected_evidence_source,
            )
            constraint_evaluations.append(
                PreviewConstraintEvaluation(
                    metric=rule.metric,
                    relation=rule.relation,
                    expected=rule.value,
                    actual=actual,
                    tolerance=rule.tolerance,
                    passed=_constraint_passed(rule, actual),
                    evidence_source=rule.expected_evidence_source,
                )
            )
        constraint_sets.append(constraint_evaluations)
        preview_feasible.append(all(item.passed for item in constraint_evaluations))

        preview_objective = _metric(
            metrics,
            metric_sources,
            variant_id,
            policy.objective.preview_metric,
            policy.objective.preview_evidence_source,
        )
        reference_objective = candidate.metrics.get(
            policy.objective.reference_metric
        )
        if reference_objective is None or not math.isfinite(reference_objective):
            raise ValueError(
                f"reference candidate {candidate_id} is missing its objective"
            )
        if candidate.metric_sources.get(policy.objective.reference_metric) is not (
            policy.objective.reference_evidence_source
        ):
            raise ValueError(
                f"reference candidate {candidate_id} objective source mismatch"
            )
        preview_objective_values.append(preview_objective)
        reference_objective_values.append(reference_objective)

        comparisons: list[PreviewMetricComparison] = []
        for rule in policy.comparison_metrics:
            preview_value = _metric(
                metrics,
                metric_sources,
                variant_id,
                rule.preview_metric,
                rule.preview_evidence_source,
            )
            reference_value = candidate.metrics.get(rule.reference_metric)
            if reference_value is None or not math.isfinite(reference_value):
                raise ValueError(
                    f"reference candidate {candidate_id} lacks comparison metric "
                    f"{rule.reference_metric!r}"
                )
            if candidate.metric_sources.get(rule.reference_metric) is not (
                rule.reference_evidence_source
            ):
                raise ValueError(
                    f"reference metric {candidate_id}/{rule.reference_metric} "
                    "evidence source mismatch"
                )
            if reference_value == 0.0:
                raise ValueError(
                    "percentage comparison requires a nonzero reference metric"
                )
            signed_error = (
                (preview_value - reference_value) / abs(reference_value) * 100.0
            )
            comparisons.append(
                PreviewMetricComparison(
                    preview_metric=rule.preview_metric,
                    reference_metric=rule.reference_metric,
                    preview_value=preview_value,
                    reference_value=reference_value,
                    signed_error_percent=signed_error,
                    absolute_error_percent=abs(signed_error),
                    preview_evidence_source=rule.preview_evidence_source,
                    reference_evidence_source=rule.reference_evidence_source,
                )
            )
        comparisons_by_candidate.append(comparisons)

    feasible_reference_values = [
        reference_objective_values[index]
        for index, candidate in enumerate(reference_run.candidates)
        if candidate.feasible
    ]
    if not feasible_reference_values:
        raise ValueError("reference run has no feasible EDA candidate")
    best_reference_value = (
        max(feasible_reference_values)
        if policy.objective.goal is ObjectiveGoal.MAXIMIZE
        else min(feasible_reference_values)
    )
    selected_reference_value = selected_reference.metrics[
        policy.objective.reference_metric
    ]
    if not _same_number(selected_reference_value, best_reference_value):
        raise ValueError("reference selected candidate is not objective-optimal")

    preview_ranks = _average_ranks(
        preview_objective_values,
        policy.objective.goal,
    )
    reference_ranks = _average_ranks(
        reference_objective_values,
        policy.objective.goal,
    )
    correlation = _spearman(preview_ranks, reference_ranks)
    feasible_indices = [
        index for index, is_feasible in enumerate(preview_feasible) if is_feasible
    ]
    feasible_indices.sort(
        key=lambda index: (
            -preview_objective_values[index]
            if policy.objective.goal is ObjectiveGoal.MAXIMIZE
            else preview_objective_values[index],
            candidate_ids[index],
        )
    )
    shortlist_indices = feasible_indices[: policy.shortlist_size]
    shortlist_ids = [candidate_ids[index] for index in shortlist_indices]
    shortlist_variants = [candidate_variant_ids[index] for index in shortlist_indices]
    preview_winner_id = shortlist_ids[0] if shortlist_ids else None
    winner_agreement = preview_winner_id == reference_winner_id
    winner_in_shortlist = reference_winner_id in shortlist_ids

    reference_feasible = [candidate.feasible for candidate in reference_run.candidates]
    feasibility_agreement = sum(
        preview == reference
        for preview, reference in zip(
            preview_feasible,
            reference_feasible,
            strict=True,
        )
    ) / len(candidate_ids)
    reference_feasible_count = sum(reference_feasible)
    reference_feasible_recall = sum(
        preview and reference
        for preview, reference in zip(
            preview_feasible,
            reference_feasible,
            strict=True,
        )
    ) / reference_feasible_count

    feasibility_gate = (
        feasibility_agreement >= policy.minimum_feasibility_agreement
        and reference_feasible_recall
        >= policy.minimum_reference_feasible_recall
    )
    rank_gate = correlation >= policy.minimum_spearman_rank_correlation
    winner_gate = (
        not policy.require_reference_winner_in_shortlist or winner_in_shortlist
    ) and (not policy.require_preview_winner_match or winner_agreement)
    utility_gate = feasibility_gate and rank_gate and winner_gate

    candidate_results = [
        PreviewCandidateSelection(
            index=index + 1,
            preview_variant_id=candidate_variant_ids[index],
            source_candidate_id=candidate_ids[index],
            preview_feasible=preview_feasible[index],
            reference_feasible=reference_feasible[index],
            preview_objective_value=preview_objective_values[index],
            reference_objective_value=reference_objective_values[index],
            preview_rank=preview_ranks[index],
            reference_rank=reference_ranks[index],
            constraints=constraint_sets[index],
            comparisons=comparisons_by_candidate[index],
        )
        for index in range(len(candidate_ids))
    ]
    error_summaries: list[PreviewMetricErrorSummary] = []
    for comparison_index, rule in enumerate(policy.comparison_metrics):
        errors = [
            candidate.comparisons[comparison_index].absolute_error_percent
            for candidate in candidate_results
        ]
        max_index = max(range(len(errors)), key=errors.__getitem__)
        error_summaries.append(
            PreviewMetricErrorSummary(
                preview_metric=rule.preview_metric,
                reference_metric=rule.reference_metric,
                candidate_count=len(errors),
                mean_absolute_error_percent=fmean(errors),
                maximum_absolute_error_percent=errors[max_index],
                maximum_error_candidate_id=candidate_ids[max_index],
            )
        )

    notes = [
        (
            "the preview shortlist retained the real OA/si reference winner"
            if winner_in_shortlist
            else "the preview shortlist dropped the real OA/si reference winner"
        ),
        (
            "the preview winner matched the real OA/si reference winner"
            if winner_agreement
            else (
                "the preview winner differed from the real OA/si reference winner"
                if preview_winner_id is not None
                else "the preview coarse constraints produced no shortlist"
            )
        ),
        (
            "this result calibrates a known candidate domain retrospectively and "
            "does not establish prospective cross-topology generalization"
            if policy.assessment_mode == "retrospective_calibration"
            else "this result applies the declared policy prospectively to a held-out domain"
        ),
        (
            "standalone preview values are shortlist evidence only; final design "
            "acceptance still requires OA/si or explicitly saved ADE evidence"
        ),
    ]
    return PreviewSelectionResult(
        policy_id=policy.id,
        policy_sha256=_canonical_sha256(policy.model_dump(mode="json")),
        assessment_mode=policy.assessment_mode,
        preview_task_id=task.id,
        preview_task_sha256=task_sha256,
        preview_run_sha256=preview_run_sha256,
        reference_task_id=reference_run.task_id,
        reference_run_sha256=reference_run_sha256,
        plan_token=preview_run.plan_token,
        status=RunStatus.SUCCEEDED if utility_gate else RunStatus.PARTIAL,
        pdk_profile=task.pdk_profile,
        analysis=policy.expected_analysis,
        candidate_generator=policy.expected_candidate_generator,
        candidate_source_id=policy.expected_candidate_source_id,
        candidate_source_sha256=policy.candidate_source_sha256,
        declared_candidate_count=len(candidate_ids),
        evaluated_candidate_count=len(candidate_results),
        shortlist_size=len(shortlist_ids),
        shortlist_candidate_ids=shortlist_ids,
        shortlist_variant_ids=shortlist_variants,
        preview_winner_candidate_id=preview_winner_id,
        reference_winner_candidate_id=reference_winner_id,
        winner_agreement=winner_agreement,
        reference_winner_in_shortlist=winner_in_shortlist,
        spearman_rank_correlation=correlation,
        feasibility_agreement_fraction=feasibility_agreement,
        reference_feasible_recall=reference_feasible_recall,
        feasibility_gate_passed=feasibility_gate,
        rank_correlation_gate_passed=rank_gate,
        winner_retention_gate_passed=winner_gate,
        selection_utility_gate_passed=utility_gate,
        candidates=candidate_results,
        metric_error_summaries=error_summaries,
        notes=notes,
    )


__all__ = [
    "PreviewCandidateSelection",
    "PreviewMetricComparison",
    "PreviewMetricConstraint",
    "PreviewMetricErrorSummary",
    "PreviewReferenceMetricRule",
    "PreviewSelectionObjective",
    "PreviewSelectionPolicy",
    "PreviewSelectionResult",
    "validate_preview_selection",
]
