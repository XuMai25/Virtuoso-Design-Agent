"""Circuit capability catalog for the current product stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import CircuitKind, Operation, TaskSpec


class UnsupportedCapability(ValueError):
    pass


@dataclass(frozen=True)
class CircuitCapability:
    circuit: CircuitKind
    stage: str
    executable: bool
    operations: tuple[Operation, ...]
    parameters: tuple[str, ...]
    evidence_gate: str


_ALL_OPERATIONS = tuple(Operation)

CIRCUIT_CATALOG: dict[CircuitKind, CircuitCapability] = {
    CircuitKind.INVERTER: CircuitCapability(
        circuit=CircuitKind.INVERTER,
        stage="L5A vertical slice",
        executable=True,
        operations=_ALL_OPERATIONS,
        parameters=(
            "nmos_width_um",
            "pmos_width_um",
            "length_um",
            "load_ff",
            "vdd_v",
        ),
        evidence_gate=(
            "OA readback + si netlist consistency + transient timing/supply energy + "
            "bounded search"
        ),
    ),
    CircuitKind.COMMON_SOURCE: CircuitCapability(
        circuit=CircuitKind.COMMON_SOURCE,
        stage="Gate 2",
        executable=False,
        operations=(),
        parameters=("device_width_um", "length_um", "bias_ua", "load_ff"),
        evidence_gate="DC operating point before AC gain/bandwidth",
    ),
    CircuitKind.SOURCE_DEGENERATED_COMMON_SOURCE: CircuitCapability(
        circuit=CircuitKind.SOURCE_DEGENERATED_COMMON_SOURCE,
        stage="Gate 2",
        executable=False,
        operations=(),
        parameters=(
            "device_width_um",
            "length_um",
            "bias_ua",
            "source_resistance_ohm",
            "load_ff",
        ),
        evidence_gate="DC operating point + AC gain/bandwidth + degeneration check",
    ),
    CircuitKind.DIFFERENTIAL_PAIR: CircuitCapability(
        circuit=CircuitKind.DIFFERENTIAL_PAIR,
        stage="Gate 3",
        executable=False,
        operations=(),
        parameters=("input_width_um", "length_um", "tail_current_ua", "load_ff"),
        evidence_gate="DC balance/common-mode range + differential AC + CMRR",
    ),
}


def validate_task_capability(task: TaskSpec) -> None:
    capability = CIRCUIT_CATALOG[task.circuit]
    if not capability.executable or task.operation not in capability.operations:
        raise UnsupportedCapability(
            f"{task.circuit.value} is {capability.stage}; "
            f"operation {task.operation.value} is not executable yet"
        )
    allowed = set(capability.parameters)
    supplied = set(task.parameters) | set(task.parameter_space)
    unknown = sorted(supplied - allowed)
    if unknown:
        raise UnsupportedCapability(
            f"unsupported parameters for {task.circuit.value}: {', '.join(unknown)}"
        )


def catalog_as_dicts() -> list[dict]:
    result: list[dict] = []
    for capability in CIRCUIT_CATALOG.values():
        item = asdict(capability)
        item["circuit"] = capability.circuit.value
        item["operations"] = [operation.value for operation in capability.operations]
        result.append(item)
    return result
