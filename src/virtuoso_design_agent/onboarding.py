"""Compile read-only OA inspection evidence into a reviewable onboarding draft.

The draft is deliberately non-executable.  It preserves every discovered CDF
field, freezes the inspected topology, and separates structural evidence from
semantic candidates that still require user or agent intent.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .design_context import (
    DesignContext,
    DesignRoleBinding,
    HierarchyParameterScope,
)
from .models import (
    CircuitKind,
    DesignTarget,
    EvidenceSource,
    Operation,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from .planner import build_plan
from .topology_delta import (
    TopologySnapshot,
    snapshot_from_inspection,
    topology_fingerprint,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POSITIVE_SUPPLY_NAMES = {"AVDD", "DVDD", "PVDD", "VCC", "VCCA", "VDD"}
_RETURN_NAMES = {"AGND", "DGND", "GND", "PGND", "VEE", "VSS"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OnboardingInspectionSource(_StrictModel):
    task_id: str = Field(min_length=1)
    plan_token: str = Field(min_length=1)
    adapter: Literal["virtuoso-bridge-subprocess"]
    target: DesignTarget
    pdk_profile: str = Field(min_length=1)
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_finished_at: datetime
    evidence_source: Literal["bridge_readback"] = "bridge_readback"


class OnboardingRoleCandidate(_StrictModel):
    role: Literal[
        "signal.input",
        "signal.output",
        "supply.positive",
        "supply.return",
        "interface.bidirectional",
        "interface.unknown",
    ]
    pin: str = Field(min_length=1)
    net: str = Field(min_length=1)
    direction: str | None = None
    inference_basis: list[str] = Field(min_length=1)
    evidence_source: Literal["software_inference"] = "software_inference"
    requires_confirmation: Literal[True] = True


class OnboardingSignalCandidates(_StrictModel):
    input_nodes: list[str] = Field(default_factory=list)
    output_nodes: list[str] = Field(default_factory=list)
    positive_supply_nodes: list[str] = Field(default_factory=list)
    return_nodes: list[str] = Field(default_factory=list)
    bidirectional_nodes: list[str] = Field(default_factory=list)


class OnboardingParameterField(_StrictModel):
    raw_value: str
    permission_default: Literal["not_authorized"] = "not_authorized"
    kind_hint: Literal[
        "geometry_width",
        "geometry_length",
        "resistance",
        "multiplicity",
        "bias",
        "unknown",
    ]
    value_source: Literal["bridge_readback"] = "bridge_readback"
    hint_source: Literal["software_inference"] = "software_inference"


class OnboardingParameterInventory(_StrictModel):
    instance_path: str = Field(min_length=1)
    target: DesignTarget
    scope: Literal["top", "child"]
    source_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: dict[str, OnboardingParameterField]


class OnboardingHierarchyCandidate(_StrictModel):
    top_instance: str = Field(min_length=1)
    library: str = Field(min_length=1)
    cell: str = Field(min_length=1)
    source_view: str = Field(min_length=1)
    target_view: Literal["schematic"] = "schematic"
    status: Literal["uninspected", "child_inspection_bound"]
    terminal_set: list[str]
    suggested_subcircuit: str = Field(min_length=1)
    child_source: OnboardingInspectionSource | None = None
    requires_terminal_order_confirmation: Literal[True] = True
    evidence_source: Literal["software_inference"] = "software_inference"


class GenericSimulationOnboardingDraft(_StrictModel):
    executable: Literal[False] = False
    contract_template: dict[str, Any]
    signal_candidates: OnboardingSignalCandidates
    evidence_source: Literal["software_inference"] = "software_inference"


class ExistingSchematicOnboardingDraft(_StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    status: Literal["needs_user_intent"] = "needs_user_intent"
    read_only: Literal[True] = True
    source: OnboardingInspectionSource
    topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    placement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    topology: TopologySnapshot
    design_context_draft: DesignContext
    generic_simulation_draft: GenericSimulationOnboardingDraft
    role_candidates: list[OnboardingRoleCandidate]
    parameter_inventory: list[OnboardingParameterInventory]
    hierarchy_candidates: list[OnboardingHierarchyCandidate]
    unresolved_decisions: list[str] = Field(min_length=1)
    evidence_sources: dict[
        str,
        Literal["bridge_readback", "software_inference", "user_input"],
    ]


@dataclass(frozen=True)
class _LoadedInspection:
    source: OnboardingInspectionSource
    snapshot: TopologySnapshot
    placement_sha256: str
    instance_parameters: dict[str, dict[str, str]]


def _file_sha256(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read onboarding evidence {path}") from exc
    return hashlib.sha256(payload).hexdigest()


def _load_inspection(task_path: Path, run_path: Path) -> _LoadedInspection:
    try:
        task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
        record = RunRecord.model_validate_json(run_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("cannot read onboarding inspection task or run") from exc
    if task.operation is not Operation.SCHEMATIC_INSPECT or (
        task.circuit is not CircuitKind.EXISTING_SCHEMATIC
    ):
        raise ValueError(
            "onboarding requires an existing_schematic schematic.inspect task"
        )
    if task.target is None or task.target.view != "schematic":
        raise ValueError("onboarding inspection requires an exact schematic target")
    if task.safety.allow_remote_compute or task.safety.allow_remote_write:
        raise ValueError(
            "onboarding source must be a dedicated read-only inspection task"
        )
    if record.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("onboarding requires a real Bridge inspection run")
    if record.status is not RunStatus.SUCCEEDED:
        raise ValueError("onboarding inspection run did not succeed")
    if record.task_id != task.id or (
        record.plan_token != build_plan(task).confirmation_token
    ):
        raise ValueError("onboarding run does not match the inspection task plan")
    unexpected = sorted(
        action.action
        for action in record.actions
        if action.action not in {"bridge.probe", "schematic.inspect"}
    )
    if unexpected:
        raise ValueError(
            "onboarding source is not a dedicated inspection run: "
            + ", ".join(unexpected)
        )
    actions = [
        action for action in record.actions if action.action == "schematic.inspect"
    ]
    if len(actions) != 1:
        raise ValueError(
            "onboarding run must contain exactly one schematic.inspect action"
        )
    action = actions[0]
    if (
        action.status != "succeeded"
        or action.evidence_source is not EvidenceSource.BRIDGE_READBACK
    ):
        raise ValueError(
            "onboarding inspect action is not successful bridge_readback evidence"
        )
    details = action.details
    snapshot = snapshot_from_inspection(details)
    if not snapshot.instances and not snapshot.nets and not snapshot.pins:
        raise ValueError("onboarding cannot bind an empty schematic topology")
    if (
        len(snapshot.instances) > 128
        or len(snapshot.nets) > 128
        or len(snapshot.pins) > 128
    ):
        raise ValueError(
            "onboarding topology exceeds the current 128-object design-context bound"
        )
    placement = details.get("placement")
    placement_sha256 = (
        placement.get("sha256") if isinstance(placement, Mapping) else None
    )
    if not isinstance(placement_sha256, str) or _SHA256_PATTERN.fullmatch(
        placement_sha256
    ) is None:
        raise ValueError("onboarding inspection lacks exact placement SHA-256")
    raw_parameters = details.get("instance_parameters")
    if not isinstance(raw_parameters, Mapping):
        raise ValueError("onboarding inspection lacks structured instance parameters")
    instance_names = {item.name for item in snapshot.instances}
    if set(raw_parameters) != instance_names:
        raise ValueError(
            "onboarding instance-parameter inventory does not exactly match topology"
        )
    instance_parameters: dict[str, dict[str, str]] = {}
    for instance, fields in raw_parameters.items():
        if not isinstance(instance, str) or not isinstance(fields, Mapping):
            raise ValueError("onboarding instance parameters are not structured")
        if any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in fields.items()
        ):
            raise ValueError(
                f"onboarding CDF values for {instance!r} must be exact strings"
            )
        instance_parameters[instance] = dict(sorted(fields.items()))
    source = OnboardingInspectionSource(
        task_id=task.id,
        plan_token=record.plan_token,
        adapter="virtuoso-bridge-subprocess",
        target=task.target,
        pdk_profile=task.pdk_profile,
        task_sha256=_file_sha256(task_path),
        run_sha256=_file_sha256(run_path),
        action_finished_at=action.finished_at,
    )
    return _LoadedInspection(
        source=source,
        snapshot=snapshot,
        placement_sha256=placement_sha256,
        instance_parameters=instance_parameters,
    )


def _normalized_pin_name(name: str) -> str:
    return re.sub(r"<[^>]*>$", "", name).upper()


def _role_candidates(
    snapshot: TopologySnapshot,
) -> tuple[list[OnboardingRoleCandidate], OnboardingSignalCandidates]:
    candidates: list[OnboardingRoleCandidate] = []
    by_role: dict[str, list[str]] = {
        "signal.input": [],
        "signal.output": [],
        "supply.positive": [],
        "supply.return": [],
        "interface.bidirectional": [],
    }
    for pin in snapshot.pins:
        normalized = _normalized_pin_name(pin.name)
        basis = [f"pin_direction={pin.direction or 'unknown'}"]
        if normalized in _POSITIVE_SUPPLY_NAMES:
            role = "supply.positive"
            basis.append(f"recognized_supply_name={normalized}")
        elif normalized in _RETURN_NAMES:
            role = "supply.return"
            basis.append(f"recognized_return_name={normalized}")
        elif pin.direction == "input":
            role = "signal.input"
        elif pin.direction == "output":
            role = "signal.output"
        elif pin.direction == "inputOutput":
            role = "interface.bidirectional"
        else:
            role = "interface.unknown"
        candidates.append(
            OnboardingRoleCandidate(
                role=role,
                pin=pin.name,
                net=pin.net,
                direction=pin.direction,
                inference_basis=basis,
            )
        )
        if role in by_role:
            by_role[role].append(pin.net)
    return candidates, OnboardingSignalCandidates(
        input_nodes=sorted(set(by_role["signal.input"])),
        output_nodes=sorted(set(by_role["signal.output"])),
        positive_supply_nodes=sorted(set(by_role["supply.positive"])),
        return_nodes=sorted(set(by_role["supply.return"])),
        bidirectional_nodes=sorted(set(by_role["interface.bidirectional"])),
    )


def _structural_roles(snapshot: TopologySnapshot) -> list[DesignRoleBinding]:
    if snapshot.pins:
        kind = "pins"
        names = [item.name for item in snapshot.pins]
        role_prefix = "inventory.interface_pins"
    elif snapshot.instances:
        kind = "instances"
        names = [item.name for item in snapshot.instances]
        role_prefix = "inventory.instances"
    else:
        kind = "nets"
        names = [item.name for item in snapshot.nets]
        role_prefix = "inventory.nets"
    roles: list[DesignRoleBinding] = []
    for index in range(0, len(names), 32):
        values = names[index : index + 32]
        roles.append(
            DesignRoleBinding.model_validate(
                {
                    "role": f"{role_prefix}.{index // 32 + 1}",
                    kind: values,
                    "evidence_source": "software_inference",
                }
            )
        )
    return roles


def _parameter_kind(name: str) -> str:
    normalized = name.lower()
    if normalized in {"w", "wfg", "width"} or "width" in normalized:
        return "geometry_width"
    if normalized in {"l", "length"} or "length" in normalized:
        return "geometry_length"
    if normalized in {"r", "res", "resistance"} or "resist" in normalized:
        return "resistance"
    if normalized in {"m", "nf", "nfin", "fingers", "simm"}:
        return "multiplicity"
    if "bias" in normalized:
        return "bias"
    return "unknown"


def _parameter_inventory(
    inspection: _LoadedInspection,
    *,
    prefix: str | None = None,
    scope: Literal["top", "child"] = "top",
) -> list[OnboardingParameterInventory]:
    result: list[OnboardingParameterInventory] = []
    for instance, fields in sorted(inspection.instance_parameters.items()):
        path = f"{prefix}/{instance}" if prefix is not None else instance
        result.append(
            OnboardingParameterInventory(
                instance_path=path,
                target=inspection.source.target,
                scope=scope,
                source_run_sha256=inspection.source.run_sha256,
                fields={
                    name: OnboardingParameterField(
                        raw_value=value,
                        kind_hint=_parameter_kind(name),
                    )
                    for name, value in sorted(fields.items())
                },
            )
        )
    return result


def _generic_contract_template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sources": [],
        "loads": [],
        "dc_voltage_metrics": [],
        "dc_current_metrics": [],
        "operating_point_metrics": [],
        "transfer": None,
        "dynamic_analysis": None,
        "netlist_parameter_bindings": [],
        "hierarchy_bindings": [],
        "operating_condition_supply_source": None,
        "temperature_c": None,
    }


def build_onboarding_draft(
    inspect_task_path: Path,
    inspect_run_path: Path,
    *,
    draft_id: str,
    child_inspections: Sequence[tuple[str, Path, Path]] = (),
) -> ExistingSchematicOnboardingDraft:
    """Build a deterministic non-executable draft from exact read-only evidence."""

    top = _load_inspection(inspect_task_path, inspect_run_path)
    top_instances = {item.name: item for item in top.snapshot.instances}
    duplicate_children: dict[tuple[str, str], list[str]] = {}
    for instance in top.snapshot.instances:
        duplicate_children.setdefault(
            (instance.master.library, instance.master.cell), []
        ).append(instance.name)

    supplied_children: dict[str, _LoadedInspection] = {}
    for top_instance, task_path, run_path in child_inspections:
        if top_instance in supplied_children:
            raise ValueError(
                f"duplicate child inspection for top instance {top_instance!r}"
            )
        instance = top_instances.get(top_instance)
        if instance is None:
            raise ValueError(
                f"child inspection references missing top instance {top_instance!r}"
            )
        aliases = sorted(
            duplicate_children[(instance.master.library, instance.master.cell)]
        )
        if aliases != [top_instance]:
            raise ValueError(
                "onboarding cannot bind a shared child scope because one child OA "
                f"write would affect top instances {aliases}"
            )
        child = _load_inspection(task_path, run_path)
        if child.source.pdk_profile != top.source.pdk_profile:
            raise ValueError("child inspection PDK profile differs from top inspection")
        if (
            child.source.target.library,
            child.source.target.cell,
            child.source.target.view,
        ) != (
            instance.master.library,
            instance.master.cell,
            "schematic",
        ):
            raise ValueError(
                f"child inspection for {top_instance!r} does not match top instance master"
            )
        if child.source.target.library != top.source.target.library:
            raise ValueError(
                "one-level onboarding child scopes currently require the top design library"
            )
        terminal_set = set(instance.terminals)
        child_pin_set = {pin.name for pin in child.snapshot.pins}
        if terminal_set != child_pin_set:
            raise ValueError(
                f"child inspection pin set for {top_instance!r} does not match top terminals"
            )
        supplied_children[top_instance] = child

    hierarchy_scopes: list[HierarchyParameterScope] = []
    hierarchy_candidates: list[OnboardingHierarchyCandidate] = []
    parameter_inventory = _parameter_inventory(top)
    for instance in top.snapshot.instances:
        if (
            instance.master.library != top.source.target.library
            or instance.master.cell == top.source.target.cell
        ):
            continue
        child = supplied_children.get(instance.name)
        if child is not None:
            hierarchy_scopes.append(
                HierarchyParameterScope(
                    top_instance=instance.name,
                    library=child.source.target.library,
                    cell=child.source.target.cell,
                    view="schematic",
                    expected_child_topology_sha256=topology_fingerprint(
                        child.snapshot
                    ),
                    expected_child_placement_sha256=child.placement_sha256,
                )
            )
            parameter_inventory.extend(
                _parameter_inventory(child, prefix=instance.name, scope="child")
            )
        hierarchy_candidates.append(
            OnboardingHierarchyCandidate(
                top_instance=instance.name,
                library=instance.master.library,
                cell=instance.master.cell,
                source_view=instance.master.view or "unknown",
                status=(
                    "child_inspection_bound" if child is not None else "uninspected"
                ),
                terminal_set=sorted(instance.terminals),
                suggested_subcircuit=instance.master.cell,
                child_source=child.source if child is not None else None,
            )
        )

    unused_children = sorted(
        set(supplied_children)
        - {item.top_instance for item in hierarchy_candidates}
    )
    if unused_children:
        raise ValueError(
            "child inspections do not identify supported same-library hierarchy: "
            + ", ".join(unused_children)
        )

    topology_sha256 = topology_fingerprint(top.snapshot)
    context = DesignContext(
        id=draft_id,
        topology_origin="existing_oa",
        expected_topology_sha256=topology_sha256,
        roles=_structural_roles(top.snapshot),
        hierarchy_parameter_scopes=hierarchy_scopes,
        frozen_instances=[item.name for item in top.snapshot.instances],
        frozen_nets=[item.name for item in top.snapshot.nets],
        frozen_pins=[item.name for item in top.snapshot.pins],
    )
    role_candidates, signal_candidates = _role_candidates(top.snapshot)
    unresolved = [
        "confirm semantic roles from role_candidates; structural inventory roles "
        "are not design intent",
        "select explicit parameter permissions; every discovered CDF field remains not_authorized",
        "define independent sources and exact DC/AC values; none are inferred",
        "define loads and values; none are inferred",
        "select analyses, metrics, transfer expressions, constraints, objective, and budget",
        "bind each authorized OA parameter to its exact si parameter before simulation or tuning",
    ]
    if hierarchy_candidates:
        unresolved.append(
            "confirm each child primitive boundary, si subcircuit name, and exact terminal order"
        )
    return ExistingSchematicOnboardingDraft(
        id=draft_id,
        source=top.source,
        topology_sha256=topology_sha256,
        placement_sha256=top.placement_sha256,
        topology=top.snapshot,
        design_context_draft=context,
        generic_simulation_draft=GenericSimulationOnboardingDraft(
            contract_template=_generic_contract_template(),
            signal_candidates=signal_candidates,
        ),
        role_candidates=role_candidates,
        parameter_inventory=sorted(
            parameter_inventory, key=lambda item: item.instance_path
        ),
        hierarchy_candidates=sorted(
            hierarchy_candidates, key=lambda item: item.top_instance
        ),
        unresolved_decisions=unresolved,
        evidence_sources={
            "oa_topology_parameters_and_placement": "bridge_readback",
            "role_parameter_kind_and_hierarchy_candidates": "software_inference",
            "final_roles_permissions_testbench_and_specs": "user_input",
        },
    )


__all__ = [
    "ExistingSchematicOnboardingDraft",
    "build_onboarding_draft",
]
