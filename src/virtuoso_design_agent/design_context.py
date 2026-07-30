"""Topology-conditioned design intent for reusable, bounded refinement.

The context deliberately describes *what* a user-provided schematic means and
which local changes are allowed.  It does not infer an arbitrary circuit or
duplicate Bridge execution.  The same contract can guard existing specialized
workers today and a generic OA simulation worker later.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from .topology_delta import (
    AddInstanceOperation,
    AddNetOperation,
    AddPinOperation,
    ReconnectTerminalOperation,
    RemoveInstanceOperation,
    RemoveNetOperation,
    RemovePinOperation,
    ReplaceMasterOperation,
    TopologyDeltaExecutionSpec,
    TopologyOperation,
    snapshot_from_inspection,
    topology_fingerprint,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_ROLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]*$"
_METRIC_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ParameterMode = Literal["fixed", "search"]
AnalysisName = Literal["dc", "ac", "transient", "noise", "psrr"]
TopologyOperationName = Literal[
    "add_instance",
    "remove_instance",
    "reconnect_terminal",
    "replace_master",
    "add_net",
    "remove_net",
    "add_pin",
    "remove_pin",
]


class DesignContextError(RuntimeError):
    """A task or schematic does not satisfy its declared design context."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TerminalBinding(_StrictModel):
    instance: StrictStr = Field(min_length=1)
    terminal: StrictStr = Field(min_length=1)
    net: StrictStr = Field(min_length=1)


class DesignRoleBinding(_StrictModel):
    """One semantic role bound to one kind of OA graph object."""

    role: StrictStr = Field(pattern=_ROLE_PATTERN)
    instances: list[StrictStr] = Field(default_factory=list, max_length=32)
    nets: list[StrictStr] = Field(default_factory=list, max_length=32)
    pins: list[StrictStr] = Field(default_factory=list, max_length=32)
    terminals: list[TerminalBinding] = Field(default_factory=list, max_length=32)
    evidence_source: Literal["user_input", "software_inference"] = "user_input"

    @model_validator(mode="after")
    def exactly_one_target_kind(self) -> "DesignRoleBinding":
        groups = (self.instances, self.nets, self.pins, self.terminals)
        if sum(bool(group) for group in groups) != 1:
            raise ValueError(
                "a design role must bind exactly one of instances, nets, pins, or "
                "terminals"
            )
        values: list[str]
        if self.terminals:
            values = [
                f"{item.instance}.{item.terminal}@{item.net}"
                for item in self.terminals
            ]
        else:
            values = list(self.instances or self.nets or self.pins)
        if len(values) != len(set(values)):
            raise ValueError(f"design role {self.role!r} contains duplicate targets")
        return self


class InstanceParameterPermission(_StrictModel):
    instance: StrictStr = Field(min_length=1)
    parameters: list[StrictStr] = Field(min_length=1, max_length=64)
    modes: list[ParameterMode] = Field(
        default_factory=lambda: ["fixed", "search"],
        min_length=1,
        max_length=2,
    )

    @field_validator("parameters", "modes")
    @classmethod
    def require_unique_values(cls, value: list[str], info: Any) -> list[str]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must contain unique nonempty values")
        return sorted(value)


class SemanticParameterPermission(_StrictModel):
    parameter: StrictStr = Field(pattern=_ROLE_PATTERN)
    modes: list[ParameterMode] = Field(
        default_factory=lambda: ["fixed", "search"],
        min_length=1,
        max_length=2,
    )

    @field_validator("modes")
    @classmethod
    def require_unique_modes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("semantic parameter modes must be unique")
        return sorted(value)


class TopologyEditPolicy(_StrictModel):
    """Names and operation kinds that a predeclared delta may touch."""

    allowed_operations: list[TopologyOperationName] = Field(
        default_factory=list,
        max_length=8,
    )
    mutable_instances: list[StrictStr] = Field(default_factory=list, max_length=64)
    mutable_nets: list[StrictStr] = Field(default_factory=list, max_length=64)
    mutable_pins: list[StrictStr] = Field(default_factory=list, max_length=64)
    max_operations_per_delta: int = Field(default=16, ge=1, le=128)

    @field_validator(
        "allowed_operations", "mutable_instances", "mutable_nets", "mutable_pins"
    )
    @classmethod
    def require_unique_policy_values(
        cls, value: list[str], info: Any
    ) -> list[str]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError(
                f"topology edit {info.field_name} must contain unique nonempty values"
            )
        return sorted(value)

    @model_validator(mode="after")
    def reject_scope_without_operations(self) -> "TopologyEditPolicy":
        if not self.allowed_operations and (
            self.mutable_instances or self.mutable_nets or self.mutable_pins
        ):
            raise ValueError(
                "topology edit targets require at least one allowed operation"
            )
        return self


class DesignContext(_StrictModel):
    """Hashable user-topology intent and bounded refinement permissions."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=_ID_PATTERN)
    topology_origin: Literal["existing_oa", "user_provided"] = "user_provided"
    expected_topology_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    roles: list[DesignRoleBinding] = Field(min_length=1, max_length=64)
    instance_parameter_permissions: list[InstanceParameterPermission] = Field(
        default_factory=list,
        max_length=64,
    )
    semantic_parameter_permissions: list[SemanticParameterPermission] = Field(
        default_factory=list,
        max_length=64,
    )
    frozen_instances: list[StrictStr] = Field(default_factory=list, max_length=128)
    frozen_nets: list[StrictStr] = Field(default_factory=list, max_length=128)
    frozen_pins: list[StrictStr] = Field(default_factory=list, max_length=128)
    topology_edits: TopologyEditPolicy = Field(default_factory=TopologyEditPolicy)
    required_analyses: list[AnalysisName] = Field(default_factory=list, max_length=5)
    optional_analyses: list[AnalysisName] = Field(default_factory=list, max_length=5)
    metrics: list[StrictStr] = Field(default_factory=list, max_length=128)

    @field_validator(
        "frozen_instances",
        "frozen_nets",
        "frozen_pins",
        "required_analyses",
        "optional_analyses",
        "metrics",
    )
    @classmethod
    def require_unique_context_values(
        cls, value: list[str], info: Any
    ) -> list[str]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError(
                f"design context {info.field_name} must contain unique nonempty values"
            )
        if info.field_name == "metrics" and any(
            re.fullmatch(_METRIC_PATTERN, item) is None for item in value
        ):
            raise ValueError("design context metrics must be metric identifiers")
        return sorted(value)

    @model_validator(mode="after")
    def validate_context(self) -> "DesignContext":
        role_names = [binding.role for binding in self.roles]
        if len(role_names) != len(set(role_names)):
            raise ValueError("design context role names must be unique")
        instance_permissions = [
            permission.instance for permission in self.instance_parameter_permissions
        ]
        if len(instance_permissions) != len(set(instance_permissions)):
            raise ValueError(
                "design context cannot repeat an instance parameter permission"
            )
        semantic_permissions = [
            permission.parameter
            for permission in self.semantic_parameter_permissions
        ]
        if len(semantic_permissions) != len(set(semantic_permissions)):
            raise ValueError(
                "design context cannot repeat a semantic parameter permission"
            )
        overlap = sorted(set(self.required_analyses) & set(self.optional_analyses))
        if overlap:
            raise ValueError(
                "required and optional analyses overlap: " + ", ".join(overlap)
            )
        for kind, frozen, mutable in (
            (
                "instances",
                self.frozen_instances,
                self.topology_edits.mutable_instances,
            ),
            ("nets", self.frozen_nets, self.topology_edits.mutable_nets),
            ("pins", self.frozen_pins, self.topology_edits.mutable_pins),
        ):
            conflict = sorted(set(frozen) & set(mutable))
            if conflict:
                raise ValueError(
                    f"design context {kind} cannot be both frozen and mutable: "
                    + ", ".join(conflict)
                )
        return self

    def canonical_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=False)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate_parameter_scope(
        self,
        *,
        fixed_semantic: set[str],
        searched_semantic: set[str],
        fixed_instance: set[tuple[str, str]],
        searched_instance: set[tuple[str, str]],
    ) -> None:
        semantic_modes = {
            permission.parameter: set(permission.modes)
            for permission in self.semantic_parameter_permissions
        }
        instance_modes = {
            (permission.instance, parameter): set(permission.modes)
            for permission in self.instance_parameter_permissions
            for parameter in permission.parameters
        }
        violations: list[str] = []
        violations.extend(
            f"semantic {name} ({mode})"
            for mode, names in (
                ("fixed", fixed_semantic),
                ("search", searched_semantic),
            )
            for name in sorted(names)
            if mode not in semantic_modes.get(name, set())
        )
        violations.extend(
            f"instance {instance}.{parameter} ({mode})"
            for mode, fields in (
                ("fixed", fixed_instance),
                ("search", searched_instance),
            )
            for instance, parameter in sorted(fields)
            if mode not in instance_modes.get((instance, parameter), set())
        )
        if violations:
            raise ValueError(
                "design context does not permit requested parameter fields: "
                + ", ".join(violations)
            )

    def validate_analysis_scope(
        self,
        analyses: Sequence[str],
        metric_names: set[str],
    ) -> None:
        declared_analyses = set(self.required_analyses) | set(self.optional_analyses)
        unknown_analyses = sorted(set(analyses) - declared_analyses)
        if unknown_analyses:
            raise ValueError(
                "design context does not declare requested analyses: "
                + ", ".join(unknown_analyses)
            )
        unknown_metrics = sorted(metric_names - set(self.metrics))
        if unknown_metrics:
            raise ValueError(
                "design context does not declare requested metrics: "
                + ", ".join(unknown_metrics)
            )


class DesignContextAudit(_StrictModel):
    schema_version: Literal[1] = 1
    context_id: str
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    topology_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_roles: dict[str, list[str]]
    verified_instance_parameter_fields: list[str]
    frozen_object_counts: dict[str, int]
    evidence_source: Literal["software_inference"] = "software_inference"


def _instance_parameters_from_readback(
    readback: Mapping[str, Any],
) -> Mapping[str, Any]:
    raw = readback.get("instance_parameters")
    if isinstance(raw, Mapping):
        return raw
    for key in ("readback", "bridge_schematic"):
        nested = readback.get(key)
        if isinstance(nested, Mapping):
            result = _instance_parameters_from_readback(nested)
            if result:
                return result
    return {}


def audit_design_context(
    readback: Mapping[str, Any], context: DesignContext
) -> DesignContextAudit:
    """Bind a declared context to one structured schematic readback."""

    snapshot = snapshot_from_inspection(readback)
    fingerprint = topology_fingerprint(snapshot)
    if (
        context.expected_topology_sha256 is not None
        and fingerprint != context.expected_topology_sha256
    ):
        raise DesignContextError(
            "design context topology fingerprint mismatch: "
            f"expected={context.expected_topology_sha256}, actual={fingerprint}"
        )

    instances = {item.name: item for item in snapshot.instances}
    nets = {item.name for item in snapshot.nets}
    pins = {item.name for item in snapshot.pins}
    resolved_roles: dict[str, list[str]] = {}
    for binding in context.roles:
        if binding.instances:
            missing = sorted(set(binding.instances) - set(instances))
            values = list(binding.instances)
        elif binding.nets:
            missing = sorted(set(binding.nets) - nets)
            values = list(binding.nets)
        elif binding.pins:
            missing = sorted(set(binding.pins) - pins)
            values = list(binding.pins)
        else:
            missing = []
            values = []
            for terminal in binding.terminals:
                instance = instances.get(terminal.instance)
                if instance is None:
                    missing.append(terminal.instance)
                    continue
                actual_net = instance.terminals.get(terminal.terminal)
                if actual_net != terminal.net:
                    raise DesignContextError(
                        f"design role {binding.role!r} expected "
                        f"{terminal.instance}.{terminal.terminal}={terminal.net}, "
                        f"read back {actual_net!r}"
                    )
                values.append(
                    f"{terminal.instance}.{terminal.terminal}@{terminal.net}"
                )
        if missing:
            raise DesignContextError(
                f"design role {binding.role!r} references missing objects: "
                + ", ".join(missing)
            )
        resolved_roles[binding.role] = sorted(values)

    for kind, declared, actual in (
        ("instances", context.frozen_instances, set(instances)),
        ("nets", context.frozen_nets, nets),
        ("pins", context.frozen_pins, pins),
    ):
        missing = sorted(set(declared) - actual)
        if missing:
            raise DesignContextError(
                f"design context frozen {kind} are missing: " + ", ".join(missing)
            )

    readback_parameters = _instance_parameters_from_readback(readback)
    verified_fields: list[str] = []
    for permission in context.instance_parameter_permissions:
        if permission.instance not in instances:
            raise DesignContextError(
                "design context parameter permission references missing instance "
                f"{permission.instance!r}"
            )
        raw_fields = readback_parameters.get(permission.instance)
        if not isinstance(raw_fields, Mapping):
            raise DesignContextError(
                f"schematic readback lacks parameters for {permission.instance!r}"
            )
        missing = sorted(set(permission.parameters) - set(raw_fields))
        if missing:
            raise DesignContextError(
                f"schematic readback lacks declared parameters for "
                f"{permission.instance}: " + ", ".join(missing)
            )
        verified_fields.extend(
            f"{permission.instance}.{parameter}"
            for parameter in permission.parameters
        )

    return DesignContextAudit(
        context_id=context.id,
        context_sha256=context.canonical_sha256(),
        topology_sha256=fingerprint,
        resolved_roles=dict(sorted(resolved_roles.items())),
        verified_instance_parameter_fields=sorted(verified_fields),
        frozen_object_counts={
            "instances": len(context.frozen_instances),
            "nets": len(context.frozen_nets),
            "pins": len(context.frozen_pins),
        },
    )


def _operation_name(operation: TopologyOperation) -> TopologyOperationName:
    return operation.operation


def _operation_target(
    operation: TopologyOperation,
) -> tuple[Literal["instance", "net", "pin"], str]:
    if isinstance(operation, AddInstanceOperation):
        return "instance", operation.instance.name
    if isinstance(operation, RemoveInstanceOperation):
        return "instance", operation.expected.name
    if isinstance(operation, (ReconnectTerminalOperation, ReplaceMasterOperation)):
        return "instance", operation.instance
    if isinstance(operation, AddNetOperation):
        return "net", operation.net.name
    if isinstance(operation, RemoveNetOperation):
        return "net", operation.expected.name
    if isinstance(operation, AddPinOperation):
        return "pin", operation.pin.name
    if isinstance(operation, RemovePinOperation):
        return "pin", operation.expected.name
    raise TypeError(f"unsupported topology operation: {type(operation).__name__}")


def validate_topology_delta_scope(
    context: DesignContext,
    execution: TopologyDeltaExecutionSpec,
) -> None:
    """Reject a predeclared delta that exceeds the context's local edit envelope."""

    operations = (
        execution.contract.operations
        if execution.direction == "forward"
        else execution.contract.inverse_operations
    )
    policy = context.topology_edits
    if len(operations) > policy.max_operations_per_delta:
        raise ValueError(
            "topology delta exceeds design context max_operations_per_delta"
        )
    allowed_operations = set(policy.allowed_operations)
    mutable = {
        "instance": set(policy.mutable_instances),
        "net": set(policy.mutable_nets),
        "pin": set(policy.mutable_pins),
    }
    frozen = {
        "instance": set(context.frozen_instances),
        "net": set(context.frozen_nets),
        "pin": set(context.frozen_pins),
    }
    violations: list[str] = []
    for operation in operations:
        operation_name = _operation_name(operation)
        kind, target = _operation_target(operation)
        if operation_name not in allowed_operations:
            violations.append(f"operation {operation_name}")
        if target not in mutable[kind]:
            violations.append(f"{kind} {target}")
        if target in frozen[kind]:
            violations.append(f"frozen {kind} {target}")
    if violations:
        raise ValueError(
            "topology delta exceeds design context edit scope: "
            + ", ".join(sorted(set(violations)))
        )
    migration_fields = {
        (migration.instance, parameter)
        for migration in execution.contract.master_parameter_migrations
        for parameter in (
            migration.parameters
            if execution.direction == "forward"
            else migration.expected_parameters
        )
    }
    context.validate_parameter_scope(
        fixed_semantic=set(),
        searched_semantic=set(),
        fixed_instance=migration_fields,
        searched_instance=set(),
    )
