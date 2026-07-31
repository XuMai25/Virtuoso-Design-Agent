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
)
from .generic_simulation import GenericOaSimulationSpec
from .models import (
    AcSweep,
    AnalysisKind,
    AnalysisStageExecution,
    AnalysisStageSpec,
    AtomicCandidateSet,
    CircuitKind,
    ExecutionLimits,
    InstanceParameterSweep,
    InstanceParameterUpdate,
    LinearitySweep,
    MetricConstraint,
    NoiseSweep,
    Objective,
    SafetyPolicy,
    TaskSpec,
)
from .onboarding import ExistingSchematicOnboardingDraft
from .topology_delta import topology_fingerprint


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExistingSchematicOnboardingResolution(_StrictModel):
    """Explicit user intent that can fill a non-executable onboarding draft."""

    schema_version: Literal[1] = 1
    draft_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    context_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation: Literal["simulation.run", "design.tune"]
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
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    evidence_source: Literal["user_input"] = "user_input"

    @model_validator(mode="after")
    def reject_inert_simulation_tuning_fields(
        self,
    ) -> "ExistingSchematicOnboardingResolution":
        if self.operation != "simulation.run":
            return self
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
    draft: ExistingSchematicOnboardingDraft,
    resolution: ExistingSchematicOnboardingResolution,
) -> None:
    instances = {item.name: item for item in draft.topology.instances}
    nets = {item.name for item in draft.topology.nets}
    pins = {item.name for item in draft.topology.pins}
    for role in resolution.roles:
        if role.evidence_source != "user_input":
            raise ValueError("final role bindings require user_input evidence")
        unknown_instances = sorted(set(role.instances) - set(instances))
        unknown_nets = sorted(set(role.nets) - nets)
        unknown_pins = sorted(set(role.pins) - pins)
        if unknown_instances or unknown_nets or unknown_pins:
            raise ValueError(
                f"role {role.role!r} references objects outside the onboarding topology"
            )
        for terminal in role.terminals:
            instance = instances.get(terminal.instance)
            if instance is None:
                raise ValueError(
                    f"role {role.role!r} references an unknown terminal instance"
                )
            actual_net = instance.terminals.get(terminal.terminal)
            if actual_net != terminal.net:
                raise ValueError(
                    f"role {role.role!r} terminal binding does not match onboarding topology"
                )


def _validate_parameter_surface(
    draft: ExistingSchematicOnboardingDraft,
    resolution: ExistingSchematicOnboardingResolution,
) -> None:
    inventory: dict[str, set[str]] = {}
    for item in draft.parameter_inventory:
        if item.instance_path in inventory:
            raise ValueError("onboarding draft repeats an instance parameter inventory")
        inventory[item.instance_path] = set(item.fields)
    permission_fields = {
        (permission.instance, parameter)
        for permission in resolution.instance_parameter_permissions
        for parameter in permission.parameters
    }
    binding_fields = {
        (binding.instance, binding.oa_parameter)
        for binding in resolution.generic_simulation.netlist_parameter_bindings
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


def _validate_simulation_objects(
    draft: ExistingSchematicOnboardingDraft,
    resolution: ExistingSchematicOnboardingResolution,
) -> None:
    top_nodes = {item.name for item in draft.topology.nets} | {"0"}
    unknown_nodes = sorted(
        resolution.generic_simulation.referenced_nodes() - top_nodes
    )
    if unknown_nodes:
        raise ValueError(
            "generic simulation references unknown top-level nodes: "
            + ", ".join(unknown_nodes)
        )
    top_instances = {item.name for item in draft.topology.instances}
    unknown_op_instances = sorted(
        {
            item.instance
            for item in resolution.generic_simulation.operating_point_metrics
        }
        - top_instances
    )
    if unknown_op_instances:
        raise ValueError(
            "generic operating-point metrics reference unknown top-level instances: "
            + ", ".join(unknown_op_instances)
        )


def _validate_hierarchy_bindings(
    draft: ExistingSchematicOnboardingDraft,
    resolution: ExistingSchematicOnboardingResolution,
) -> None:
    candidates = {item.top_instance: item for item in draft.hierarchy_candidates}
    bindings = {
        item.instance: item
        for item in resolution.generic_simulation.hierarchy_bindings
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
    _validate_role_bindings(draft, intent)
    _validate_parameter_surface(draft, intent)
    _validate_simulation_objects(draft, intent)
    _validate_hierarchy_bindings(draft, intent)

    context = DesignContext(
        id=intent.context_id,
        topology_origin="existing_oa",
        expected_topology_sha256=draft.topology_sha256,
        roles=intent.roles,
        instance_parameter_permissions=intent.instance_parameter_permissions,
        hierarchy_parameter_scopes=(
            draft.design_context_draft.hierarchy_parameter_scopes
        ),
        frozen_instances=draft.design_context_draft.frozen_instances,
        frozen_nets=draft.design_context_draft.frozen_nets,
        frozen_pins=draft.design_context_draft.frozen_pins,
        required_analyses=intent.required_analyses,
        optional_analyses=intent.optional_analyses,
        metrics=intent.metrics,
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
    "resolve_onboarding_draft",
]
