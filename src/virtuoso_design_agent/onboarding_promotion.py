"""Promote a topology-refinement winner into a normal fixed-topology tuning task."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from .design_context import DesignContext, InstanceParameterPermission
from .generic_simulation import (
    GenericNetlistParameterBinding,
    GenericOaSimulationSpec,
)
from .models import (
    AtomicCandidateSet,
    CircuitKind,
    ExecutionLimits,
    ExistingSchematicWinnerVerificationSpec,
    InstanceParameterUpdate,
    Operation,
    RunRecord,
    RunStatus,
    SafetyPolicy,
    TaskSpec,
)
from .onboarding import ExistingSchematicOnboardingDraft, build_onboarding_draft
from .onboarding_resolution import validate_fixed_topology_onboarding_task
from .planner import build_plan
from .spectre_values import spectre_values_equal
from .topology_delta import snapshot_from_inspection, topology_fingerprint


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OnboardingPostRefinementVariantIntent(_StrictModel):
    """One predeclared tuning branch selected by the real topology winner."""

    topology_variant_id: StrictStr = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    draft_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    task_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    context_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    instance_parameter_permissions: list[InstanceParameterPermission] = Field(
        min_length=1,
        max_length=64,
    )
    netlist_parameter_bindings: list[GenericNetlistParameterBinding] = Field(
        min_length=1,
        max_length=64,
    )
    fixed_instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list,
        max_length=32,
    )
    candidate_set: AtomicCandidateSet
    winner_verification: ExistingSchematicWinnerVerificationSpec | None = None
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    evidence_source: Literal["user_input"] = "user_input"


class OnboardingPostRefinementIntent(_StrictModel):
    """Hash-bound branches prepared for the possible stage-one winners."""

    schema_version: Literal[1] = 1
    source_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    variants: list[OnboardingPostRefinementVariantIntent] = Field(
        min_length=1,
        max_length=4,
    )
    evidence_source: Literal["user_input"] = "user_input"

    @model_validator(mode="after")
    def require_unique_branches(self) -> "OnboardingPostRefinementIntent":
        variant_ids = [item.topology_variant_id for item in self.variants]
        task_ids = [item.task_id for item in self.variants]
        context_ids = [item.context_id for item in self.variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("post-refinement topology variant ids must be unique")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("post-refinement task ids must be unique")
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("post-refinement context ids must be unique")
        return self


class OnboardingPostRefinementCompilation(_StrictModel):
    """Auditable boundary between topology selection and parameter tuning."""

    schema_version: Literal[1] = 1
    status: Literal["ready_for_plan"] = "ready_for_plan"
    source_task_id: StrictStr
    source_plan_token: StrictStr
    source_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    selected_topology_variant_id: StrictStr
    selected_topology_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    winner_inspect_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    winner_inspect_run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    post_readback_draft_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    promotion_intent_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_task_id: StrictStr
    compiled_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_plan_token: StrictStr
    evidence_sources: dict[
        StrictStr,
        Literal["bridge_readback", "software_inference", "user_input"],
    ]


def _file_bytes(path: Path, *, kind: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read post-refinement {kind} {path}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _model_bytes(model: BaseModel) -> bytes:
    return (model.model_dump_json(indent=2, exclude_none=True) + "\n").encode(
        "utf-8"
    )


def _selected_context_and_simulation(
    task: TaskSpec,
    variant_id: str,
) -> tuple[DesignContext, GenericOaSimulationSpec, str]:
    assert task.design_context is not None
    assert task.generic_simulation is not None
    assert task.topology_refinement is not None
    refinement = task.topology_refinement
    if variant_id == refinement.baseline_id:
        expected = task.design_context.expected_topology_sha256
        if expected is None:
            raise ValueError("refinement baseline lacks an expected topology fingerprint")
        return task.design_context, task.generic_simulation, expected
    alternatives = {
        item.id: item for item in refinement.resolved_alternatives()
    }
    alternative = alternatives.get(variant_id)
    if alternative is None:
        raise ValueError("source run selected an unknown topology variant")
    expected = alternative.design_context.expected_topology_sha256
    if expected is None:
        raise ValueError("selected alternative lacks an expected topology fingerprint")
    return alternative.design_context, alternative.generic_simulation, expected


def _validate_source_run(task: TaskSpec, record: RunRecord) -> None:
    if (
        task.circuit is not CircuitKind.EXISTING_SCHEMATIC
        or task.operation is not Operation.DESIGN_CLOSE_LOOP
        or task.topology_refinement is None
    ):
        raise ValueError(
            "post-refinement promotion requires an existing-schematic close-loop task"
        )
    if task.target is None or task.design_context is None or task.generic_simulation is None:
        raise ValueError("source refinement task is incomplete")
    if (
        not task.safety.allow_remote_compute
        or not task.safety.allow_remote_write
        or task.safety.replace_existing
    ):
        raise ValueError(
            "source refinement task must carry explicit compute/write authority "
            "without replace_existing"
        )
    if record.task_id != task.id:
        raise ValueError("source refinement run task id differs from its task")
    if record.plan_token != build_plan(task).confirmation_token:
        raise ValueError("source refinement run plan token differs from its task")
    if record.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("post-refinement promotion requires a real Bridge source run")
    if record.status is not RunStatus.SUCCEEDED:
        raise ValueError("source refinement run did not succeed")
    if (
        record.selected_topology_variant_id is None
        or record.selected_topology_sha256 is None
        or record.selected_instance_parameters is None
        or record.selected_metrics is None
    ):
        raise ValueError("source refinement run lacks a complete selected topology")
    alternatives = task.topology_refinement.resolved_alternatives()
    parameter_candidate_count = (
        len(task.candidate_set.candidates)
        if task.candidate_set is not None
        else math.prod(len(sweep.values) for sweep in task.instance_parameter_space)
    )
    expected_candidate_count = (1 + len(alternatives)) * parameter_candidate_count
    audit = record.search_audit
    if (
        audit is None
        or not audit.domain_exhausted
        or audit.declared_candidate_count != expected_candidate_count
        or audit.attempted_candidate_count < expected_candidate_count
        or audit.completed_candidate_count != expected_candidate_count
        or audit.topology_variant_count != 1 + len(alternatives)
    ):
        raise ValueError(
            "source refinement run lacks an exhausted topology-parameter audit"
        )
    if len(record.candidates) != expected_candidate_count or [
        candidate.index for candidate in record.candidates
    ] != list(range(1, expected_candidate_count + 1)):
        raise ValueError(
            "source refinement run candidate record is incomplete or reordered"
        )
    matching_candidates = [
        candidate
        for candidate in record.candidates
        if candidate.topology_variant_id == record.selected_topology_variant_id
        and candidate.topology_sha256 == record.selected_topology_sha256
        and candidate.parameters == (record.selected_parameters or {})
        and candidate.instance_parameters == record.selected_instance_parameters
        and candidate.testbench_overrides == record.selected_testbench_overrides
        and candidate.testbench_override_evidence_source
        == record.selected_testbench_override_evidence_source
        and candidate.metrics == record.selected_metrics
        and candidate.feasible
        and candidate.analysis_complete
    ]
    if len(matching_candidates) != 1:
        raise ValueError(
            "source refinement selected state does not identify one complete feasible "
            "candidate"
        )
    final_readbacks = [
        action
        for action in record.actions
        if action.action == "schematic.inspect.final"
        and action.status == "succeeded"
        and action.evidence_source.value == "bridge_readback"
    ]
    if not final_readbacks:
        raise ValueError("source refinement run lacks final Bridge topology readback")
    topology = final_readbacks[-1].details.get("topology")
    if not isinstance(topology, dict):
        raise ValueError("source refinement final readback lacks structured topology")
    if topology_fingerprint(snapshot_from_inspection(topology)) != (
        record.selected_topology_sha256
    ):
        raise ValueError("source refinement final readback topology differs from winner")


def _compile_candidate_source(
    candidate_set: AtomicCandidateSet,
    bindings: dict[str, str],
) -> AtomicCandidateSet:
    source_bindings = dict(candidate_set.source.bindings)
    for name, digest in bindings.items():
        existing = source_bindings.get(name)
        if existing is not None and existing != digest:
            raise ValueError(f"post-refinement candidate binding {name!r} conflicts")
        source_bindings[name] = digest
    payload = candidate_set.model_dump(mode="json")
    payload["source"]["bindings"] = source_bindings
    return AtomicCandidateSet.model_validate(payload)


def _validate_selected_parameter_readback(
    draft: ExistingSchematicOnboardingDraft,
    selected: dict[str, dict[str, str]],
) -> None:
    inventory = {
        item.instance_path: item.fields for item in draft.parameter_inventory
    }
    for instance, parameters in selected.items():
        fields = inventory.get(instance, {})
        for parameter, expected in parameters.items():
            field = fields.get(parameter)
            if field is None:
                raise ValueError(
                    "winner inspection lacks selected parameter "
                    f"{instance}.{parameter}"
                )
            if not spectre_values_equal(field.raw_value, expected):
                raise ValueError(
                    "winner inspection selected parameter drifted: "
                    f"{instance}.{parameter}"
                )


def compile_onboarding_post_refinement(
    source_task_path: Path,
    source_run_path: Path,
    winner_inspect_task_path: Path,
    winner_inspect_run_path: Path,
    intent_path: Path,
    *,
    child_inspections: Sequence[tuple[str, Path, Path]] = (),
) -> tuple[
    ExistingSchematicOnboardingDraft,
    TaskSpec,
    OnboardingPostRefinementCompilation,
]:
    """Compile one fresh winner readback into a safe normal tuning task."""

    source_task_bytes = _file_bytes(source_task_path, kind="source task")
    source_run_bytes = _file_bytes(source_run_path, kind="source run")
    inspect_task_bytes = _file_bytes(
        winner_inspect_task_path,
        kind="winner inspection task",
    )
    inspect_run_bytes = _file_bytes(
        winner_inspect_run_path,
        kind="winner inspection run",
    )
    intent_bytes = _file_bytes(intent_path, kind="promotion intent")
    source_task_sha256 = _sha256(source_task_bytes)
    intent = OnboardingPostRefinementIntent.model_validate_json(intent_bytes)
    if intent.source_task_sha256 != source_task_sha256:
        raise ValueError("promotion intent source task SHA-256 mismatch")
    task = TaskSpec.model_validate_json(source_task_bytes)
    record = RunRecord.model_validate_json(source_run_bytes)
    _validate_source_run(task, record)
    assert record.selected_topology_variant_id is not None
    assert record.selected_topology_sha256 is not None
    selected_context, selected_simulation, expected_topology = (
        _selected_context_and_simulation(task, record.selected_topology_variant_id)
    )
    if record.selected_topology_sha256 != expected_topology:
        raise ValueError("source run selected topology fingerprint differs from task")
    matching_branches = [
        item
        for item in intent.variants
        if item.topology_variant_id == record.selected_topology_variant_id
    ]
    if len(matching_branches) != 1:
        raise ValueError("promotion intent does not cover the selected topology winner")
    branch = matching_branches[0]
    if branch.task_id == task.id:
        raise ValueError("post-refinement task id must differ from its source task")

    draft = build_onboarding_draft(
        winner_inspect_task_path,
        winner_inspect_run_path,
        draft_id=branch.draft_id,
        child_inspections=child_inspections,
    )
    if branch.task_id == draft.source.task_id:
        raise ValueError("post-refinement task id must differ from its inspection task")
    if (
        draft.source.task_sha256 != _sha256(inspect_task_bytes)
        or draft.source.run_sha256 != _sha256(inspect_run_bytes)
    ):
        raise ValueError("winner inspection changed while promotion was compiling")
    if task.target != draft.source.target or task.pdk_profile != draft.source.pdk_profile:
        raise ValueError("winner inspection target or PDK differs from source task")
    if draft.topology_sha256 != record.selected_topology_sha256:
        raise ValueError("winner inspection topology differs from selected topology")
    if draft.source.action_finished_at < record.finished_at:
        raise ValueError("winner inspection predates the completed refinement run")
    assert record.selected_instance_parameters is not None
    _validate_selected_parameter_readback(
        draft,
        record.selected_instance_parameters,
    )

    winner = branch.winner_verification
    if winner is not None:
        declared_analyses = set(selected_context.required_analyses) | set(
            selected_context.optional_analyses
        )
        unknown_analyses = sorted(
            {
                stage.analysis.value
                for stage in winner.analysis_stages
            }
            - declared_analyses
        )
        if unknown_analyses:
            raise ValueError(
                "post-refinement winner analyses are outside selected context: "
                + ", ".join(unknown_analyses)
            )
        unknown_metrics = sorted(
            {constraint.metric for constraint in winner.constraints}
            - set(selected_context.metrics)
        )
        if unknown_metrics:
            raise ValueError(
                "post-refinement winner metrics are outside selected context: "
                + ", ".join(unknown_metrics)
            )

    draft_bytes = _model_bytes(draft)
    draft_sha256 = _sha256(draft_bytes)
    bindings = {
        "onboarding_refinement_task_sha256": source_task_sha256,
        "onboarding_refinement_run_sha256": _sha256(source_run_bytes),
        "onboarding_winner_inspect_task_sha256": _sha256(inspect_task_bytes),
        "onboarding_winner_inspect_run_sha256": _sha256(inspect_run_bytes),
        "onboarding_promotion_intent_sha256": _sha256(intent_bytes),
        "onboarding_post_readback_draft_sha256": draft_sha256,
    }
    candidate_set = _compile_candidate_source(branch.candidate_set, bindings)
    simulation_payload = selected_simulation.model_dump(mode="json")
    simulation_payload["netlist_parameter_bindings"] = [
        binding.model_dump(mode="json")
        for binding in branch.netlist_parameter_bindings
    ]
    simulation = GenericOaSimulationSpec.model_validate(simulation_payload)
    context = DesignContext(
        id=branch.context_id,
        topology_origin="existing_oa",
        expected_topology_sha256=draft.topology_sha256,
        roles=selected_context.roles,
        instance_parameter_permissions=branch.instance_parameter_permissions,
        hierarchy_parameter_scopes=(
            draft.design_context_draft.hierarchy_parameter_scopes
        ),
        semantic_parameter_permissions=selected_context.semantic_parameter_permissions,
        frozen_instances=draft.design_context_draft.frozen_instances,
        frozen_nets=draft.design_context_draft.frozen_nets,
        frozen_pins=draft.design_context_draft.frozen_pins,
        required_analyses=selected_context.required_analyses,
        optional_analyses=selected_context.optional_analyses,
        metrics=selected_context.metrics,
    )
    task_payload = task.model_dump(mode="json", exclude_none=True)
    task_payload.update(
        {
            "id": branch.task_id,
            "operation": "design.tune",
            "design_context": context.model_dump(mode="json"),
            "generic_simulation": simulation.model_dump(mode="json"),
            "instance_parameter_updates": [
                update.model_dump(mode="json")
                for update in branch.fixed_instance_parameter_updates
            ],
            "instance_parameter_space": [],
            "candidate_set": candidate_set.model_dump(mode="json"),
            "limits": branch.limits.model_dump(mode="json"),
            "safety": SafetyPolicy(
                allow_remote_compute=False,
                allow_remote_write=False,
                allowed_library=task.target.library,
                required_cell_prefix=task.safety.required_cell_prefix,
                replace_existing=False,
            ).model_dump(mode="json"),
        }
    )
    task_payload.pop("expected_target_topology_variant", None)
    task_payload.pop("topology_refinement", None)
    task_payload.pop("theory_seed", None)
    if winner is None:
        task_payload.pop("winner_verification", None)
    else:
        task_payload["winner_verification"] = winner.model_dump(mode="json")
    compiled_task = TaskSpec.model_validate(task_payload)
    validate_fixed_topology_onboarding_task(draft, compiled_task)
    compiled_plan_token = build_plan(compiled_task).confirmation_token
    compiled_task_sha256 = _sha256(_model_bytes(compiled_task))
    compilation = OnboardingPostRefinementCompilation(
        source_task_id=task.id,
        source_plan_token=record.plan_token,
        source_task_sha256=source_task_sha256,
        source_run_sha256=bindings["onboarding_refinement_run_sha256"],
        selected_topology_variant_id=record.selected_topology_variant_id,
        selected_topology_sha256=record.selected_topology_sha256,
        winner_inspect_task_sha256=bindings[
            "onboarding_winner_inspect_task_sha256"
        ],
        winner_inspect_run_sha256=bindings[
            "onboarding_winner_inspect_run_sha256"
        ],
        post_readback_draft_sha256=draft_sha256,
        promotion_intent_sha256=bindings[
            "onboarding_promotion_intent_sha256"
        ],
        compiled_task_id=compiled_task.id,
        compiled_task_sha256=compiled_task_sha256,
        compiled_plan_token=compiled_plan_token,
        evidence_sources={
            "source_refinement_and_post_write_inventory": "bridge_readback",
            "promotion_branch_parameters_and_quality_intent": "user_input",
            "winner_selection_hash_binding_and_task_compilation": (
                "software_inference"
            ),
        },
    )
    return draft, compiled_task, compilation


__all__ = [
    "OnboardingPostRefinementCompilation",
    "OnboardingPostRefinementIntent",
    "OnboardingPostRefinementVariantIntent",
    "compile_onboarding_post_refinement",
]
