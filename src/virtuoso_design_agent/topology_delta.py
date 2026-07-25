"""Serializable, reversible contracts for bounded schematic topology changes.

This module is deliberately independent of Virtuoso and Bridge execution.  It
normalizes structured schematic readback, applies an allowlisted set of graph
operations locally, and proves that the inverse operations restore the exact
structural fingerprint.  Device/CDF parameters are intentionally outside the
topology fingerprint; their write/readback contract remains separate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator

from .models import EvidenceSource, StrictModel

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_INSTANCE_NONSTRUCTURAL_KEYS = {"parameters", "params"}
_INSTANCE_CORE_KEYS = {
    "name",
    "library",
    "lib",
    "cell",
    "view",
    "terminals",
}


class TopologyDeltaError(RuntimeError):
    """A topology contract or readback failed an exact structural check."""


class TopologyMaster(StrictModel):
    library: str = Field(min_length=1)
    cell: str = Field(min_length=1)
    view: str | None = Field(default=None, min_length=1)


class TopologyInstance(StrictModel):
    name: str = Field(min_length=1)
    master: TopologyMaster
    terminals: dict[str, str]
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("terminals")
    @classmethod
    def validate_terminals(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not terminal or not net for terminal, net in value.items()):
            raise ValueError("instance terminal and net names must be non-empty")
        return dict(sorted(value.items()))

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized = _json_value(value, context="instance attributes")
        return dict(sorted(normalized.items()))


class TopologyNet(StrictModel):
    name: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized = _json_value(value, context="net attributes")
        return dict(sorted(normalized.items()))


class TopologyPin(StrictModel):
    name: str = Field(min_length=1)
    net: str = Field(min_length=1)
    direction: str | None = Field(default=None, min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized = _json_value(value, context="pin attributes")
        return dict(sorted(normalized.items()))


class TopologySnapshot(StrictModel):
    schema_version: Literal[1] = 1
    instances: list[TopologyInstance] = Field(default_factory=list)
    nets: list[TopologyNet] = Field(default_factory=list)
    pins: list[TopologyPin] = Field(default_factory=list)

    @field_validator("instances")
    @classmethod
    def sort_instances(
        cls, value: list[TopologyInstance]
    ) -> list[TopologyInstance]:
        _require_unique((item.name for item in value), "instance")
        return sorted(value, key=lambda item: item.name)

    @field_validator("nets")
    @classmethod
    def sort_nets(cls, value: list[TopologyNet]) -> list[TopologyNet]:
        _require_unique((item.name for item in value), "net")
        return sorted(value, key=lambda item: item.name)

    @field_validator("pins")
    @classmethod
    def sort_pins(cls, value: list[TopologyPin]) -> list[TopologyPin]:
        _require_unique((item.name for item in value), "pin")
        return sorted(value, key=lambda item: item.name)


class AddInstanceOperation(StrictModel):
    operation: Literal["add_instance"] = "add_instance"
    instance: TopologyInstance


class RemoveInstanceOperation(StrictModel):
    operation: Literal["remove_instance"] = "remove_instance"
    expected: TopologyInstance


class ReconnectTerminalOperation(StrictModel):
    operation: Literal["reconnect_terminal"] = "reconnect_terminal"
    instance: str = Field(min_length=1)
    terminal: str = Field(min_length=1)
    expected_net: str = Field(min_length=1)
    net: str = Field(min_length=1)


class ReplaceMasterOperation(StrictModel):
    operation: Literal["replace_master"] = "replace_master"
    instance: str = Field(min_length=1)
    expected_master: TopologyMaster
    master: TopologyMaster


class AddNetOperation(StrictModel):
    operation: Literal["add_net"] = "add_net"
    net: TopologyNet


class RemoveNetOperation(StrictModel):
    operation: Literal["remove_net"] = "remove_net"
    expected: TopologyNet


class AddPinOperation(StrictModel):
    operation: Literal["add_pin"] = "add_pin"
    pin: TopologyPin


class RemovePinOperation(StrictModel):
    operation: Literal["remove_pin"] = "remove_pin"
    expected: TopologyPin


TopologyOperation: TypeAlias = Annotated[
    AddInstanceOperation
    | RemoveInstanceOperation
    | ReconnectTerminalOperation
    | ReplaceMasterOperation
    | AddNetOperation
    | RemoveNetOperation
    | AddPinOperation
    | RemovePinOperation,
    Field(discriminator="operation"),
]

_OPERATIONS_ADAPTER = TypeAdapter(list[TopologyOperation])


class TopologyDeltaContract(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    expected_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    operations: list[TopologyOperation] = Field(default_factory=list, max_length=128)
    inverse_operations: list[TopologyOperation] = Field(
        default_factory=list,
        max_length=128,
    )


class TopologyDeltaAudit(StrictModel):
    schema_version: Literal[1] = 1
    contract_id: str
    before_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    actual_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    operation_count: int = Field(ge=0)
    inverse_operation_count: int = Field(ge=0)
    forward_readback_match: Literal[True]
    inverse_restored_before: Literal[True]
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


def _require_unique(values: Iterable[str], kind: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {kind} names: {sorted(duplicates)}")


def _json_value(value: Any, *, context: str) -> Any:
    """Return a deterministic JSON value or reject opaque adapter objects."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise TopologyDeltaError(f"{context} is not finite JSON data") from exc
    return json.loads(encoded)


def _readback_payload(readback: Mapping[str, Any]) -> Mapping[str, Any]:
    if all(name in readback for name in ("instances", "nets", "pins")):
        return readback
    nested = readback.get("readback")
    if isinstance(nested, Mapping):
        return _readback_payload(nested)
    nested = readback.get("bridge_schematic")
    if isinstance(nested, Mapping):
        return _readback_payload(nested)
    raise TopologyDeltaError(
        "schematic readback must contain structured instances, nets, and pins"
    )


def _instance_from_readback(raw: Any) -> TopologyInstance:
    if not isinstance(raw, Mapping):
        raise TopologyDeltaError("schematic instance readback must be an object")
    name = raw.get("name")
    library = raw.get("library", raw.get("lib"))
    cell = raw.get("cell")
    terminals = raw.get("terminals")
    if not isinstance(name, str) or not isinstance(library, str) or not isinstance(
        cell, str
    ):
        raise TopologyDeltaError(
            "schematic instance readback requires string name/library/cell"
        )
    if not isinstance(terminals, Mapping):
        raise TopologyDeltaError(
            f"schematic instance {name!r} requires structured terminals"
        )
    if any(
        not isinstance(terminal, str) or not isinstance(net, str)
        for terminal, net in terminals.items()
    ):
        raise TopologyDeltaError(
            f"schematic instance {name!r} terminal names and nets must be strings"
        )
    view = raw.get("view")
    if view is not None and not isinstance(view, str):
        raise TopologyDeltaError(f"schematic instance {name!r} has invalid view")
    attributes = {
        str(key): _json_value(value, context=f"instance {name!r} attribute {key!r}")
        for key, value in raw.items()
        if key not in _INSTANCE_CORE_KEYS | _INSTANCE_NONSTRUCTURAL_KEYS
    }
    return TopologyInstance(
        name=name,
        master=TopologyMaster(library=library, cell=cell, view=view),
        terminals=dict(terminals),
        attributes=dict(sorted(attributes.items())),
    )


def _net_from_readback(raw: Any) -> TopologyNet:
    if isinstance(raw, str):
        return TopologyNet(name=raw)
    if not isinstance(raw, Mapping):
        raise TopologyDeltaError("schematic net readback must be a string or object")
    name = raw.get("name", raw.get("net"))
    if not isinstance(name, str):
        raise TopologyDeltaError("schematic net object requires a string name")
    attributes = {
        str(key): _json_value(value, context=f"net {name!r} attribute {key!r}")
        for key, value in raw.items()
        if key not in {"name", "net"}
    }
    return TopologyNet(name=name, attributes=dict(sorted(attributes.items())))


def _pin_from_readback(raw: Any) -> TopologyPin:
    if isinstance(raw, str):
        return TopologyPin(name=raw, net=raw)
    if not isinstance(raw, Mapping):
        raise TopologyDeltaError("schematic pin readback must be a string or object")
    name = raw.get("name")
    net = raw.get("net", name)
    direction = raw.get("direction")
    if not isinstance(name, str) or not isinstance(net, str):
        raise TopologyDeltaError("schematic pin object requires string name/net")
    if direction is not None and not isinstance(direction, str):
        raise TopologyDeltaError(f"schematic pin {name!r} has invalid direction")
    attributes = {
        str(key): _json_value(value, context=f"pin {name!r} attribute {key!r}")
        for key, value in raw.items()
        if key not in {"name", "net", "direction"}
    }
    return TopologyPin(
        name=name,
        net=net,
        direction=direction,
        attributes=dict(sorted(attributes.items())),
    )


def snapshot_from_inspection(readback: Mapping[str, Any]) -> TopologySnapshot:
    """Normalize Bridge/demo structured readback into a stable topology graph."""

    payload = _readback_payload(readback)
    raw_instances = payload.get("instances")
    raw_nets = payload.get("nets")
    raw_pins = payload.get("pins")
    if not isinstance(raw_instances, list):
        raise TopologyDeltaError("schematic instances readback must be a list")
    if not isinstance(raw_nets, list):
        raise TopologyDeltaError("schematic nets readback must be a list")
    if not isinstance(raw_pins, list):
        raise TopologyDeltaError("schematic pins readback must be a list")
    snapshot = TopologySnapshot(
        instances=[_instance_from_readback(item) for item in raw_instances],
        nets=[_net_from_readback(item) for item in raw_nets],
        pins=[_pin_from_readback(item) for item in raw_pins],
    )
    _assert_referential_integrity(snapshot)
    return snapshot


def topology_fingerprint(snapshot: TopologySnapshot) -> str:
    payload = snapshot.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_referential_integrity(snapshot: TopologySnapshot) -> None:
    nets = {item.name for item in snapshot.nets}
    for instance in snapshot.instances:
        missing = sorted(set(instance.terminals.values()) - nets)
        if missing:
            raise TopologyDeltaError(
                f"instance {instance.name!r} references missing nets: {missing}"
            )
    for pin in snapshot.pins:
        if pin.net not in nets:
            raise TopologyDeltaError(
                f"pin {pin.name!r} references missing net {pin.net!r}"
            )


def _operation_list(
    operations: Sequence[TopologyOperation | Mapping[str, Any]],
) -> list[TopologyOperation]:
    return _OPERATIONS_ADAPTER.validate_python(list(operations))


def apply_topology_operations(
    snapshot: TopologySnapshot,
    operations: Sequence[TopologyOperation | Mapping[str, Any]],
) -> TopologySnapshot:
    """Apply allowlisted graph operations with compare-and-swap preconditions."""

    parsed = _operation_list(operations)
    instances = {item.name: deepcopy(item) for item in snapshot.instances}
    nets = {item.name: deepcopy(item) for item in snapshot.nets}
    pins = {item.name: deepcopy(item) for item in snapshot.pins}

    for index, operation in enumerate(parsed):
        label = f"operation[{index}] {operation.operation}"
        if isinstance(operation, AddNetOperation):
            name = operation.net.name
            if name in nets:
                raise TopologyDeltaError(f"{label}: net {name!r} already exists")
            nets[name] = deepcopy(operation.net)
        elif isinstance(operation, RemovePinOperation):
            name = operation.expected.name
            actual = pins.get(name)
            if actual != operation.expected:
                raise TopologyDeltaError(
                    f"{label}: pin {name!r} does not match expected old state"
                )
            del pins[name]
        elif isinstance(operation, RemoveInstanceOperation):
            name = operation.expected.name
            actual = instances.get(name)
            if actual != operation.expected:
                raise TopologyDeltaError(
                    f"{label}: instance {name!r} does not match expected old state"
                )
            del instances[name]
        elif isinstance(operation, ReplaceMasterOperation):
            instance = instances.get(operation.instance)
            if instance is None:
                raise TopologyDeltaError(
                    f"{label}: instance {operation.instance!r} does not exist"
                )
            if instance.master != operation.expected_master:
                raise TopologyDeltaError(
                    f"{label}: instance {operation.instance!r} master does not "
                    "match expected old state"
                )
            instances[operation.instance] = instance.model_copy(
                update={"master": operation.master}
            )
        elif isinstance(operation, ReconnectTerminalOperation):
            instance = instances.get(operation.instance)
            if instance is None:
                raise TopologyDeltaError(
                    f"{label}: instance {operation.instance!r} does not exist"
                )
            if operation.terminal not in instance.terminals:
                raise TopologyDeltaError(
                    f"{label}: terminal {operation.instance}.{operation.terminal} "
                    "does not exist"
                )
            if instance.terminals[operation.terminal] != operation.expected_net:
                raise TopologyDeltaError(
                    f"{label}: terminal {operation.instance}.{operation.terminal} "
                    "does not match expected old net"
                )
            if operation.net not in nets:
                raise TopologyDeltaError(
                    f"{label}: destination net {operation.net!r} does not exist"
                )
            terminals = dict(instance.terminals)
            terminals[operation.terminal] = operation.net
            instances[operation.instance] = instance.model_copy(
                update={"terminals": dict(sorted(terminals.items()))}
            )
        elif isinstance(operation, AddInstanceOperation):
            name = operation.instance.name
            if name in instances:
                raise TopologyDeltaError(
                    f"{label}: instance {name!r} already exists"
                )
            missing = sorted(set(operation.instance.terminals.values()) - set(nets))
            if missing:
                raise TopologyDeltaError(
                    f"{label}: instance {name!r} references missing nets: {missing}"
                )
            instances[name] = deepcopy(operation.instance)
        elif isinstance(operation, AddPinOperation):
            name = operation.pin.name
            if name in pins:
                raise TopologyDeltaError(f"{label}: pin {name!r} already exists")
            if operation.pin.net not in nets:
                raise TopologyDeltaError(
                    f"{label}: pin {name!r} references missing net "
                    f"{operation.pin.net!r}"
                )
            pins[name] = deepcopy(operation.pin)
        elif isinstance(operation, RemoveNetOperation):
            name = operation.expected.name
            actual = nets.get(name)
            if actual != operation.expected:
                raise TopologyDeltaError(
                    f"{label}: net {name!r} does not match expected old state"
                )
            users = sorted(
                f"{instance.name}.{terminal}"
                for instance in instances.values()
                for terminal, net in instance.terminals.items()
                if net == name
            )
            users.extend(sorted(f"pin:{pin.name}" for pin in pins.values() if pin.net == name))
            if users:
                raise TopologyDeltaError(
                    f"{label}: net {name!r} is still referenced by {users}"
                )
            del nets[name]
        else:  # pragma: no cover - exhaustive discriminated union
            raise AssertionError(f"unsupported topology operation: {operation}")

    result = TopologySnapshot(
        instances=list(instances.values()),
        nets=list(nets.values()),
        pins=list(pins.values()),
    )
    _assert_referential_integrity(result)
    return result


def invert_topology_operations(
    operations: Sequence[TopologyOperation | Mapping[str, Any]],
) -> list[TopologyOperation]:
    inverse: list[TopologyOperation] = []
    for operation in reversed(_operation_list(operations)):
        if isinstance(operation, AddInstanceOperation):
            inverse.append(RemoveInstanceOperation(expected=operation.instance))
        elif isinstance(operation, RemoveInstanceOperation):
            inverse.append(AddInstanceOperation(instance=operation.expected))
        elif isinstance(operation, ReconnectTerminalOperation):
            inverse.append(
                ReconnectTerminalOperation(
                    instance=operation.instance,
                    terminal=operation.terminal,
                    expected_net=operation.net,
                    net=operation.expected_net,
                )
            )
        elif isinstance(operation, ReplaceMasterOperation):
            inverse.append(
                ReplaceMasterOperation(
                    instance=operation.instance,
                    expected_master=operation.master,
                    master=operation.expected_master,
                )
            )
        elif isinstance(operation, AddNetOperation):
            inverse.append(RemoveNetOperation(expected=operation.net))
        elif isinstance(operation, RemoveNetOperation):
            inverse.append(AddNetOperation(net=operation.expected))
        elif isinstance(operation, AddPinOperation):
            inverse.append(RemovePinOperation(expected=operation.pin))
        elif isinstance(operation, RemovePinOperation):
            inverse.append(AddPinOperation(pin=operation.expected))
    return inverse


def compile_topology_delta(
    contract_id: str,
    before: TopologySnapshot | Mapping[str, Any],
    operations: Sequence[TopologyOperation | Mapping[str, Any]],
) -> TopologyDeltaContract:
    """Compile operations against one exact before-state and prove rollback."""

    before_snapshot = (
        before
        if isinstance(before, TopologySnapshot)
        else snapshot_from_inspection(before)
    )
    parsed = _operation_list(operations)
    after_snapshot = apply_topology_operations(before_snapshot, parsed)
    inverse = invert_topology_operations(parsed)
    restored = apply_topology_operations(after_snapshot, inverse)
    before_sha256 = topology_fingerprint(before_snapshot)
    if topology_fingerprint(restored) != before_sha256:
        raise TopologyDeltaError("generated inverse did not restore before fingerprint")
    return TopologyDeltaContract(
        id=contract_id,
        expected_before_sha256=before_sha256,
        expected_after_sha256=topology_fingerprint(after_snapshot),
        operations=parsed,
        inverse_operations=inverse,
    )


def apply_topology_delta(
    snapshot: TopologySnapshot | Mapping[str, Any],
    contract: TopologyDeltaContract | Mapping[str, Any],
) -> TopologySnapshot:
    """Apply a compiled contract only to its exact declared before-state."""

    parsed_contract = (
        contract
        if isinstance(contract, TopologyDeltaContract)
        else TopologyDeltaContract.model_validate(contract)
    )
    before = (
        snapshot
        if isinstance(snapshot, TopologySnapshot)
        else snapshot_from_inspection(snapshot)
    )
    actual_before = topology_fingerprint(before)
    if actual_before != parsed_contract.expected_before_sha256:
        raise TopologyDeltaError(
            "topology before fingerprint mismatch: expected "
            f"{parsed_contract.expected_before_sha256}, got {actual_before}"
        )
    after = apply_topology_operations(before, parsed_contract.operations)
    actual_after = topology_fingerprint(after)
    if actual_after != parsed_contract.expected_after_sha256:
        raise TopologyDeltaError(
            "topology local after fingerprint mismatch: expected "
            f"{parsed_contract.expected_after_sha256}, got {actual_after}"
        )
    return after


def validate_topology_readback(
    before_readback: TopologySnapshot | Mapping[str, Any],
    after_readback: TopologySnapshot | Mapping[str, Any],
    contract: TopologyDeltaContract | Mapping[str, Any],
) -> TopologyDeltaAudit:
    """Compare complete normalized readback and verify exact inverse restoration."""

    parsed_contract = (
        contract
        if isinstance(contract, TopologyDeltaContract)
        else TopologyDeltaContract.model_validate(contract)
    )
    before = (
        before_readback
        if isinstance(before_readback, TopologySnapshot)
        else snapshot_from_inspection(before_readback)
    )
    actual_after = (
        after_readback
        if isinstance(after_readback, TopologySnapshot)
        else snapshot_from_inspection(after_readback)
    )
    expected_after = apply_topology_delta(before, parsed_contract)
    actual_after_sha256 = topology_fingerprint(actual_after)
    if actual_after != expected_after:
        raise TopologyDeltaError(
            "topology after readback does not match the complete expected structure: "
            f"expected {parsed_contract.expected_after_sha256}, got "
            f"{actual_after_sha256}"
        )
    restored = apply_topology_operations(
        actual_after,
        parsed_contract.inverse_operations,
    )
    before_sha256 = topology_fingerprint(before)
    if restored != before:
        raise TopologyDeltaError(
            "topology inverse did not restore the complete before structure"
        )
    return TopologyDeltaAudit(
        contract_id=parsed_contract.id,
        before_sha256=before_sha256,
        expected_after_sha256=parsed_contract.expected_after_sha256,
        actual_after_sha256=actual_after_sha256,
        operation_count=len(parsed_contract.operations),
        inverse_operation_count=len(parsed_contract.inverse_operations),
        forward_readback_match=True,
        inverse_restored_before=True,
    )


def derive_topology_delta(
    contract_id: str,
    before_readback: TopologySnapshot | Mapping[str, Any],
    after_readback: TopologySnapshot | Mapping[str, Any],
) -> TopologyDeltaContract:
    """Derive the supported minimal delta between two complete readbacks."""

    before = (
        before_readback
        if isinstance(before_readback, TopologySnapshot)
        else snapshot_from_inspection(before_readback)
    )
    after = (
        after_readback
        if isinstance(after_readback, TopologySnapshot)
        else snapshot_from_inspection(after_readback)
    )
    before_instances = {item.name: item for item in before.instances}
    after_instances = {item.name: item for item in after.instances}
    before_nets = {item.name: item for item in before.nets}
    after_nets = {item.name: item for item in after.nets}
    before_pins = {item.name: item for item in before.pins}
    after_pins = {item.name: item for item in after.pins}

    changed_nets = sorted(
        name
        for name in before_nets.keys() & after_nets.keys()
        if before_nets[name] != after_nets[name]
    )
    if changed_nets:
        raise TopologyDeltaError(
            "net attribute changes are not in the topology-delta allowlist: "
            f"{changed_nets}"
        )

    operations: list[TopologyOperation] = []
    for name in sorted(after_nets.keys() - before_nets.keys()):
        operations.append(AddNetOperation(net=after_nets[name]))

    changed_pins = {
        name
        for name in before_pins.keys() & after_pins.keys()
        if before_pins[name] != after_pins[name]
    }
    for name in sorted((before_pins.keys() - after_pins.keys()) | changed_pins):
        operations.append(RemovePinOperation(expected=before_pins[name]))

    for name in sorted(before_instances.keys() - after_instances.keys()):
        operations.append(RemoveInstanceOperation(expected=before_instances[name]))

    for name in sorted(before_instances.keys() & after_instances.keys()):
        old = before_instances[name]
        new = after_instances[name]
        if old.attributes != new.attributes:
            raise TopologyDeltaError(
                f"instance {name!r} structural attributes changed; move/reshape is "
                "not in the topology-delta allowlist"
            )
        if old.terminals.keys() != new.terminals.keys():
            raise TopologyDeltaError(
                f"instance {name!r} terminal set changed; pin-shape mutation is "
                "not in the topology-delta allowlist"
            )
        if old.master != new.master:
            operations.append(
                ReplaceMasterOperation(
                    instance=name,
                    expected_master=old.master,
                    master=new.master,
                )
            )
        for terminal in sorted(old.terminals):
            if old.terminals[terminal] != new.terminals[terminal]:
                operations.append(
                    ReconnectTerminalOperation(
                        instance=name,
                        terminal=terminal,
                        expected_net=old.terminals[terminal],
                        net=new.terminals[terminal],
                    )
                )

    for name in sorted(after_instances.keys() - before_instances.keys()):
        operations.append(AddInstanceOperation(instance=after_instances[name]))

    for name in sorted((after_pins.keys() - before_pins.keys()) | changed_pins):
        operations.append(AddPinOperation(pin=after_pins[name]))

    for name in sorted(before_nets.keys() - after_nets.keys()):
        operations.append(RemoveNetOperation(expected=before_nets[name]))

    contract = compile_topology_delta(contract_id, before, operations)
    if topology_fingerprint(after) != contract.expected_after_sha256:
        raise TopologyDeltaError(
            "readback difference contains a change outside the derived operations"
        )
    return contract


def audit_derived_topology_delta(
    contract_id: str,
    before_readback: Mapping[str, Any],
    after_readback: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and verify an auditable contract for an existing transform record."""

    contract = derive_topology_delta(contract_id, before_readback, after_readback)
    audit = validate_topology_readback(before_readback, after_readback, contract)
    return {
        "contract": contract.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        "scope": {
            "local_post_readback_audit": True,
            "contract_predeclared_before_transform": False,
            "generic_remote_writer_used": False,
            "instance_parameters_excluded": True,
        },
    }
