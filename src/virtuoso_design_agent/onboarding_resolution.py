"""Resolve a read-only onboarding draft into a normal, safe TaskSpec."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from .design_context import (
    AnalysisName,
    DesignContext,
    DesignRoleBinding,
    InstanceParameterPermission,
    TopologyEditPolicy,
)
from .generic_simulation import (
    GenericNetlistParameterBinding,
    GenericOaSimulationSpec,
)
from .models import (
    AcSweep,
    AnalysisKind,
    AnalysisStageExecution,
    AnalysisStageSpec,
    AtomicCandidateSet,
    CircuitKind,
    ExecutionLimits,
    ExistingSchematicTopologyAlternativeSpec,
    ExistingSchematicTopologyRefinementSpec,
    ExistingSchematicWinnerVerificationSpec,
    InstanceParameterSweep,
    InstanceParameterUpdate,
    LinearitySweep,
    MetricConstraint,
    NoiseSweep,
    Objective,
    Operation,
    SafetyPolicy,
    TaskSpec,
)
from .onboarding import ExistingSchematicOnboardingDraft
from .topology_delta import (
    TopologyDeltaError,
    TopologyDeltaExecutionSpec,
    TopologySnapshot,
    apply_topology_delta_execution,
    topology_fingerprint,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OnboardingTopologyAlternative(_StrictModel):
    """One compact, user-confirmed local alternative derived from the draft."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    context_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    topology_delta: TopologyDeltaExecutionSpec
    roles: list[DesignRoleBinding] = Field(min_length=1, max_length=64)
    added_instance_parameter_permissions: list[InstanceParameterPermission] = Field(
        default_factory=list,
        max_length=32,
    )
    generic_simulation: GenericOaSimulationSpec | None = None
    added_netlist_parameter_bindings: list[GenericNetlistParameterBinding] = Field(
        default_factory=list,
        max_length=32,
    )
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list,
        max_length=32,
    )
    evidence_source: Literal["user_input"] = "user_input"

    @model_validator(mode="after")
    def require_forward_delta(self) -> "OnboardingTopologyAlternative":
        if self.topology_delta.direction != "forward":
            raise ValueError("onboarding topology alternative requires a forward delta")
        if self.generic_simulation is not None and self.added_netlist_parameter_bindings:
            raise ValueError(
                "onboarding topology alternative must use either a complete generic "
                "simulation or inherited simulation bindings, not both"
            )
        return self


class ExistingSchematicOnboardingResolution(_StrictModel):
    """Explicit user intent that can fill a non-executable onboarding draft."""

    schema_version: Literal[1] = 1
    draft_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    context_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation: Literal["simulation.run", "design.tune", "design.close_loop"]
    roles: list[DesignRoleBinding] = Field(min_length=1, max_length=64)
    instance_parameter_permissions: list[InstanceParameterPermission] = Field(
        default_factory=list,
        max_length=64,
    )
    required_analyses: list[AnalysisName] = Field(default_factory=list, max_length=5)
    optional_analyses: list[AnalysisName] = Field(default_factory=list, max_length=5)
    metrics: list[StrictStr] = Field(default_factory=list, max_length=128)
    generic_simulation: GenericOaSimulationSpec
    analysis: AnalysisKind | None = None
    analysis_stages: list[AnalysisStageSpec] = Field(
        default_factory=list,
        max_length=4,
    )
    analysis_stage_execution: AnalysisStageExecution = (
        AnalysisStageExecution.ISOLATED
    )
    ac_sweep: AcSweep | None = None
    linearity_sweep: LinearitySweep | None = None
    noise_sweep: NoiseSweep | None = None
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list,
    )
    instance_parameter_space: list[InstanceParameterSweep] = Field(
        default_factory=list,
        max_length=12,
    )
    candidate_set: AtomicCandidateSet | None = None
    constraints: list[MetricConstraint] = Field(default_factory=list)
    objective: Objective | None = None
    topology_edits: TopologyEditPolicy = Field(default_factory=TopologyEditPolicy)
    topology_alternatives: list[OnboardingTopologyAlternative] = Field(
        default_factory=list,
        max_length=3,
    )
    winner_verification: ExistingSchematicWinnerVerificationSpec | None = None
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    evidence_source: Literal["user_input"] = "user_input"

    @model_validator(mode="after")
    def reject_inert_simulation_tuning_fields(
        self,
    ) -> "ExistingSchematicOnboardingResolution":
        has_topology_authority = bool(
            self.topology_edits.allowed_operations or self.topology_alternatives
        )
        if self.operation == "simulation.run":
            if (
                self.instance_parameter_updates
                or self.instance_parameter_space
                or self.candidate_set is not None
            ):
                raise ValueError(
                    "simulation.run resolution cannot request parameter writes or search"
                )
            if self.objective is not None:
                raise ValueError("simulation.run resolution cannot declare an objective")
            if any(
                "search" in permission.modes
                for permission in self.instance_parameter_permissions
            ):
                raise ValueError(
                    "simulation.run parameter permissions must use fixed mode only"
                )
            if self.limits.max_iterations != 1:
                raise ValueError("simulation.run resolution requires max_iterations=1")
            if self.winner_verification is not None:
                raise ValueError("simulation.run resolution cannot verify a winner")
        if self.operation != "design.close_loop" and has_topology_authority:
            raise ValueError(
                "onboarding topology alternatives require design.close_loop"
            )
        if self.operation == "design.close_loop":
            if not self.topology_alternatives:
                raise ValueError(
                    "design.close_loop resolution requires a topology alternative"
                )
            if not self.topology_edits.allowed_operations:
                raise ValueError(
                    "design.close_loop resolution requires explicit topology edit authority"
                )
        if self.winner_verification is not None:
            declared_analyses = set(self.required_analyses) | set(
                self.optional_analyses
            )
            winner_analyses = {
                stage.analysis.value
                for stage in self.winner_verification.analysis_stages
            }
            unknown_analyses = sorted(winner_analyses - declared_analyses)
            if unknown_analyses:
                raise ValueError(
                    "winner-verification analyses are outside onboarding intent: "
                    + ", ".join(unknown_analyses)
                )
            winner_metrics = {
                constraint.metric
                for constraint in self.winner_verification.constraints
            }
            unknown_metrics = sorted(winner_metrics - set(self.metrics))
            if unknown_metrics:
                raise ValueError(
                    "winner-verification metrics are outside onboarding intent: "
                    + ", ".join(unknown_metrics)
                )
        return self


def _load_draft(
    path: Path,
) -> tuple[ExistingSchematicOnboardingDraft, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read onboarding draft {path}") from exc
    draft_sha256 = hashlib.sha256(payload).hexdigest()
    draft = ExistingSchematicOnboardingDraft.model_validate_json(payload)
    expected_topology = topology_fingerprint(draft.topology)
    if draft.topology_sha256 != expected_topology:
        raise ValueError("onboarding draft topology SHA-256 is internally inconsistent")
    context = draft.design_context_draft
    if context.expected_topology_sha256 != draft.topology_sha256:
        raise ValueError("onboarding draft design context has a different topology")
    if (
        context.instance_parameter_permissions
        or context.semantic_parameter_permissions
        or context.required_analyses
        or context.optional_analyses
        or context.metrics
        or context.topology_edits.allowed_operations
        or context.topology_edits.mutable_instances
        or context.topology_edits.mutable_nets
        or context.topology_edits.mutable_pins
    ):
        raise ValueError("onboarding draft is no longer a zero-authority draft")
    if context.frozen_instances != [item.name for item in draft.topology.instances]:
        raise ValueError("onboarding draft does not freeze every top-level instance")
    if context.frozen_nets != [item.name for item in draft.topology.nets]:
        raise ValueError("onboarding draft does not freeze every top-level net")
    if context.frozen_pins != [item.name for item in draft.topology.pins]:
        raise ValueError("onboarding draft does not freeze every top-level pin")
    return draft, draft_sha256


def _validate_role_bindings(
    topology: TopologySnapshot,
    roles: list[DesignRoleBinding],
    *,
    scope: str,
) -> None:
    instances = {item.name: item for item in topology.instances}
    nets = {item.name for item in topology.nets}
    pins = {item.name for item in topology.pins}
    for role in roles:
        if role.evidence_source != "user_input":
            raise ValueError(f"{scope} role bindings require user_input evidence")
        unknown_instances = sorted(set(role.instances) - set(instances))
        unknown_nets = sorted(set(role.nets) - nets)
        unknown_pins = sorted(set(role.pins) - pins)
        if unknown_instances or unknown_nets or unknown_pins:
            raise ValueError(
                f"{scope} role {role.role!r} references objects outside the "
                "onboarding topology"
            )
        for terminal in role.terminals:
            instance = instances.get(terminal.instance)
            if instance is None:
                raise ValueError(
                    f"{scope} role {role.role!r} references an unknown terminal instance"
                )
            actual_net = instance.terminals.get(terminal.terminal)
            if actual_net != terminal.net:
                raise ValueError(
                    f"{scope} role {role.role!r} terminal binding does not match topology"
                )


def _validate_parameter_surface(
    draft: ExistingSchematicOnboardingDraft,
    permissions: list[InstanceParameterPermission],
    simulation: GenericOaSimulationSpec,
) -> None:
    inventory: dict[str, set[str]] = {}
    for item in draft.parameter_inventory:
        if item.instance_path in inventory:
            raise ValueError("onboarding draft repeats an instance parameter inventory")
        inventory[item.instance_path] = set(item.fields)
    permission_fields = {
        (permission.instance, parameter)
        for permission in permissions
        for parameter in permission.parameters
    }
    binding_fields = {
        (binding.instance, binding.oa_parameter)
        for binding in simulation.netlist_parameter_bindings
    }
    outside_inventory = sorted(
        (instance, parameter)
        for instance, parameter in permission_fields | binding_fields
        if parameter not in inventory.get(instance, set())
    )
    if outside_inventory:
        formatted = ", ".join(
            f"{instance}.{parameter}" for instance, parameter in outside_inventory
        )
        raise ValueError(
            "resolution parameter fields are outside the onboarding parameter "
            f"inventory: {formatted}"
        )
    if permission_fields != binding_fields:
        raise ValueError(
            "resolution parameter permission/binding surface differs; every authorized "
            "CDF field needs one exact OA-to-si binding"
        )


def validate_fixed_topology_onboarding_task(
    draft: ExistingSchematicOnboardingDraft,
    task: TaskSpec,
) -> None:
    """Rebind one normal fixed-topology task to a fresh onboarding readback."""

    if task.circuit is not CircuitKind.EXISTING_SCHEMATIC or task.operation not in {
        Operation.SIMULATION_RUN,
        Operation.DESIGN_TUNE,
    }:
        raise ValueError(
            "post-refinement onboarding requires a fixed-topology existing-schematic "
            "simulation or tuning task"
        )
    if task.target != draft.source.target or task.pdk_profile != draft.source.pdk_profile:
        raise ValueError("post-refinement task target or PDK differs from its readback")
    if task.design_context is None or task.generic_simulation is None:
        raise ValueError("post-refinement task lacks design context or simulation intent")
    context = task.design_context
    if context.topology_origin != "existing_oa":
        raise ValueError("post-refinement task requires existing-OA topology origin")
    if context.expected_topology_sha256 != draft.topology_sha256:
        raise ValueError("post-refinement task topology differs from its readback")
    if (
        context.topology_edits.allowed_operations
        or context.topology_edits.mutable_instances
        or context.topology_edits.mutable_nets
        or context.topology_edits.mutable_pins
    ):
        raise ValueError("post-refinement tuning must freeze the selected topology")
    if context.frozen_instances != draft.design_context_draft.frozen_instances:
        raise ValueError("post-refinement task does not freeze every read-back instance")
    if context.frozen_nets != draft.design_context_draft.frozen_nets:
        raise ValueError("post-refinement task does not freeze every read-back net")
    if context.frozen_pins != draft.design_context_draft.frozen_pins:
        raise ValueError("post-refinement task does not freeze every read-back pin")
    if (
        context.hierarchy_parameter_scopes
        != draft.design_context_draft.hierarchy_parameter_scopes
    ):
        raise ValueError("post-refinement hierarchy scopes differ from fresh readback")
    if (
        task.safety.allow_remote_compute
        or task.safety.allow_remote_write
        or task.safety.replace_existing
    ):
        raise ValueError("post-refinement compiler output must keep remote flags disabled")
    _validate_role_bindings(
        draft.topology,
        context.roles,
        scope="post-refinement",
    )
    _validate_parameter_surface(
        draft,
        context.instance_parameter_permissions,
        task.generic_simulation,
    )
    _validate_simulation_objects(
        draft.topology,
        task.generic_simulation,
        scope="post-refinement",
    )
    _validate_hierarchy_bindings(
        draft,
        draft.topology,
        task.generic_simulation,
    )


def _validate_alternative_parameter_surface(
    resolution: ExistingSchematicOnboardingResolution,
    alternative: OnboardingTopologyAlternative,
    simulation: GenericOaSimulationSpec,
) -> None:
    added_instances = {
        operation.instance.name
        for operation in alternative.topology_delta.contract.operations
        if operation.operation == "add_instance"
    }
    permission_fields = {
        (permission.instance, parameter)
        for permission in alternative.added_instance_parameter_permissions
        for parameter in permission.parameters
    }
    invalid_permissions = sorted(
        instance
        for instance, _parameter in permission_fields
        if instance not in added_instances
    )
    if invalid_permissions:
        raise ValueError(
            f"alternative {alternative.id!r} added-instance permissions require "
            "instances added by its topology delta: "
            + ", ".join(invalid_permissions)
        )
    nonfixed_permissions = sorted(
        permission.instance
        for permission in alternative.added_instance_parameter_permissions
        if set(permission.modes) != {"fixed"}
    )
    if nonfixed_permissions:
        raise ValueError(
            f"alternative {alternative.id!r} added-instance permissions must be fixed: "
            + ", ".join(nonfixed_permissions)
        )
    update_fields = {
        (update.instance, parameter)
        for update in alternative.instance_parameter_updates
        for parameter in update.parameters
    }
    if update_fields != permission_fields:
        raise ValueError(
            f"alternative {alternative.id!r} added-instance permission/update "
            "surface differs"
        )
    baseline_fields = {
        (permission.instance, parameter)
        for permission in resolution.instance_parameter_permissions
        for parameter in permission.parameters
    }
    binding_fields = {
        (binding.instance, binding.oa_parameter)
        for binding in simulation.netlist_parameter_bindings
    }
    if binding_fields != baseline_fields | permission_fields:
        raise ValueError(
            f"alternative {alternative.id!r} parameter permission/binding surface "
            "differs"
        )


def _validate_simulation_objects(
    topology: TopologySnapshot,
    simulation: GenericOaSimulationSpec,
    *,
    scope: str,
) -> None:
    top_nodes = {item.name for item in topology.nets} | {"0"}
    unknown_nodes = sorted(simulation.referenced_nodes() - top_nodes)
    if unknown_nodes:
        raise ValueError(
            "generic simulation references unknown top-level nodes in "
            f"{scope}: "
            + ", ".join(unknown_nodes)
        )
    top_instances = {item.name for item in topology.instances}
    unknown_op_instances = sorted(
        {
            item.instance
            for item in simulation.operating_point_metrics
        }
        - top_instances
    )
    if unknown_op_instances:
        raise ValueError(
            "generic operating-point metrics reference unknown top-level instances "
            f"in {scope}: "
            + ", ".join(unknown_op_instances)
        )


def _validate_hierarchy_bindings(
    draft: ExistingSchematicOnboardingDraft,
    topology: TopologySnapshot,
    simulation: GenericOaSimulationSpec,
) -> None:
    candidates = {item.top_instance: item for item in draft.hierarchy_candidates}
    bindings = {
        item.instance: item
        for item in simulation.hierarchy_bindings
    }
    scope_instances = {
        item.top_instance
        for item in draft.design_context_draft.hierarchy_parameter_scopes
    }
    if set(bindings) != set(candidates) or scope_instances != set(candidates):
        raise ValueError(
            "resolution must bind every inspected hierarchy candidate and cannot bind "
            "an uninspected child"
        )
    topology_instances = {item.name for item in topology.instances}
    missing_instances = sorted(set(candidates) - topology_instances)
    if missing_instances:
        raise ValueError(
            "onboarding topology refinement cannot remove an inspected hierarchy "
            "instance: " + ", ".join(missing_instances)
        )
    for instance, candidate in candidates.items():
        if candidate.status != "child_inspection_bound":
            raise ValueError(
                f"hierarchy candidate {instance!r} lacks a bound child inspection"
            )
        binding = bindings[instance]
        if (binding.library, binding.cell, binding.view) != (
            candidate.library,
            candidate.cell,
            candidate.target_view,
        ):
            raise ValueError(
                f"hierarchy binding for {instance!r} does not match onboarding child"
            )
        if (
            len(binding.terminal_order) != len(candidate.terminal_set)
            or set(binding.terminal_order) != set(candidate.terminal_set)
        ):
            raise ValueError(
                f"hierarchy binding for {instance!r} terminal order does not match "
                "the inspected terminal set"
            )


def resolve_onboarding_draft(
    draft_path: Path,
    resolution: ExistingSchematicOnboardingResolution | Mapping[str, Any],
) -> TaskSpec:
    """Compile confirmed onboarding intent into the existing TaskSpec contract."""

    intent = (
        resolution
        if isinstance(resolution, ExistingSchematicOnboardingResolution)
        else ExistingSchematicOnboardingResolution.model_validate(resolution)
    )
    draft, actual_draft_sha256 = _load_draft(draft_path)
    if intent.draft_sha256 != actual_draft_sha256:
        raise ValueError("resolution draft SHA-256 does not match the onboarding draft")
    _validate_role_bindings(
        draft.topology,
        intent.roles,
        scope="final",
    )
    _validate_parameter_surface(
        draft,
        intent.instance_parameter_permissions,
        intent.generic_simulation,
    )
    _validate_simulation_objects(
        draft.topology,
        intent.generic_simulation,
        scope="baseline",
    )
    _validate_hierarchy_bindings(
        draft,
        draft.topology,
        intent.generic_simulation,
    )

    baseline_instances = {item.name for item in draft.topology.instances}
    baseline_nets = {item.name for item in draft.topology.nets}
    baseline_pins = {item.name for item in draft.topology.pins}
    mutable_instances = set(intent.topology_edits.mutable_instances)
    mutable_nets = set(intent.topology_edits.mutable_nets)
    mutable_pins = set(intent.topology_edits.mutable_pins)

    context = DesignContext(
        id=intent.context_id,
        topology_origin="existing_oa",
        expected_topology_sha256=draft.topology_sha256,
        roles=intent.roles,
        instance_parameter_permissions=intent.instance_parameter_permissions,
        hierarchy_parameter_scopes=(
            draft.design_context_draft.hierarchy_parameter_scopes
        ),
        frozen_instances=sorted(baseline_instances - mutable_instances),
        frozen_nets=sorted(baseline_nets - mutable_nets),
        frozen_pins=sorted(baseline_pins - mutable_pins),
        topology_edits=intent.topology_edits,
        required_analyses=intent.required_analyses,
        optional_analyses=intent.optional_analyses,
        metrics=intent.metrics,
    )

    topology_alternatives: list[ExistingSchematicTopologyAlternativeSpec] = []
    for alternative in intent.topology_alternatives:
        try:
            after = apply_topology_delta_execution(
                draft.topology,
                alternative.topology_delta,
            )
            restored = apply_topology_delta_execution(
                after,
                alternative.topology_delta.model_copy(
                    update={"direction": "inverse"}
                ),
            )
        except TopologyDeltaError as exc:
            raise ValueError(
                f"alternative {alternative.id!r} topology contract is not an exact "
                "draft-bound round trip"
            ) from exc
        if topology_fingerprint(restored) != draft.topology_sha256:
            raise ValueError(
                f"alternative {alternative.id!r} inverse does not restore the draft"
            )
        _validate_role_bindings(
            after,
            alternative.roles,
            scope=f"alternative {alternative.id!r}",
        )
        if alternative.generic_simulation is None:
            simulation_payload = intent.generic_simulation.model_dump(mode="json")
            simulation_payload["netlist_parameter_bindings"].extend(
                binding.model_dump(mode="json")
                for binding in alternative.added_netlist_parameter_bindings
            )
            alternative_simulation = GenericOaSimulationSpec.model_validate(
                simulation_payload
            )
        else:
            alternative_simulation = alternative.generic_simulation
        _validate_alternative_parameter_surface(
            intent,
            alternative,
            alternative_simulation,
        )
        _validate_simulation_objects(
            after,
            alternative_simulation,
            scope=f"alternative {alternative.id!r}",
        )
        _validate_hierarchy_bindings(
            draft,
            after,
            alternative_simulation,
        )
        alternative_instances = {item.name for item in after.instances}
        alternative_nets = {item.name for item in after.nets}
        alternative_pins = {item.name for item in after.pins}
        alternative_context = DesignContext(
            id=alternative.context_id,
            topology_origin="existing_oa",
            expected_topology_sha256=topology_fingerprint(after),
            roles=alternative.roles,
            instance_parameter_permissions=[
                *intent.instance_parameter_permissions,
                *alternative.added_instance_parameter_permissions,
            ],
            hierarchy_parameter_scopes=(
                draft.design_context_draft.hierarchy_parameter_scopes
            ),
            frozen_instances=sorted(alternative_instances - mutable_instances),
            frozen_nets=sorted(alternative_nets - mutable_nets),
            frozen_pins=sorted(alternative_pins - mutable_pins),
            topology_edits=intent.topology_edits,
            required_analyses=intent.required_analyses,
            optional_analyses=intent.optional_analyses,
            metrics=intent.metrics,
        )
        topology_alternatives.append(
            ExistingSchematicTopologyAlternativeSpec(
                id=alternative.id,
                topology_delta=alternative.topology_delta,
                design_context=alternative_context,
                generic_simulation=alternative_simulation,
                instance_parameter_updates=alternative.instance_parameter_updates,
            )
        )

    topology_refinement = (
        ExistingSchematicTopologyRefinementSpec(
            alternatives=topology_alternatives,
        )
        if topology_alternatives
        else None
    )
    return TaskSpec(
        id=intent.task_id,
        operation=intent.operation,
        circuit=CircuitKind.EXISTING_SCHEMATIC,
        target=draft.source.target,
        pdk_profile=draft.source.pdk_profile,
        analysis=intent.analysis,
        analysis_stages=intent.analysis_stages,
        analysis_stage_execution=intent.analysis_stage_execution,
        ac_sweep=intent.ac_sweep,
        linearity_sweep=intent.linearity_sweep,
        noise_sweep=intent.noise_sweep,
        design_context=context,
        generic_simulation=intent.generic_simulation,
        topology_refinement=topology_refinement,
        winner_verification=intent.winner_verification,
        instance_parameter_updates=intent.instance_parameter_updates,
        instance_parameter_space=intent.instance_parameter_space,
        candidate_set=intent.candidate_set,
        constraints=intent.constraints,
        objective=intent.objective,
        create_if_missing=False,
        safety=SafetyPolicy(
            allow_remote_compute=False,
            allow_remote_write=False,
            allowed_library=draft.source.target.library,
            required_cell_prefix="vda_",
            replace_existing=False,
        ),
        limits=intent.limits,
    )


__all__ = [
    "ExistingSchematicOnboardingResolution",
    "OnboardingTopologyAlternative",
    "resolve_onboarding_draft",
    "validate_fixed_topology_onboarding_task",
]
