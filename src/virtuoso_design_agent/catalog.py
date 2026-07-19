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
    explicit_instance_parameters: bool
    evidence_gate: str


_ALL_OPERATIONS = tuple(Operation)

CIRCUIT_CATALOG: dict[CircuitKind, CircuitCapability] = {
    CircuitKind.EXISTING_SCHEMATIC: CircuitCapability(
        circuit=CircuitKind.EXISTING_SCHEMATIC,
        stage="Bridge-preserving manual OA surface",
        executable=True,
        operations=(Operation.SCHEMATIC_INSPECT, Operation.PARAMETERS_APPLY),
        parameters=(),
        explicit_instance_parameters=True,
        evidence_gate=(
            "unfiltered Bridge schematic readback + targeted CDF value verification"
        ),
    ),
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
        explicit_instance_parameters=True,
        evidence_gate=(
            "OA readback + si netlist consistency + transient timing/supply energy + "
            "bounded search"
        ),
    ),
    CircuitKind.COMMON_SOURCE: CircuitCapability(
        circuit=CircuitKind.COMMON_SOURCE,
        stage="Gate 2A DC operating point",
        executable=True,
        operations=_ALL_OPERATIONS,
        parameters=(
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "bias_v",
            "vdd_v",
        ),
        explicit_instance_parameters=True,
        evidence_gate=(
            "OA readback + si netlist consistency + DC Id/VGS/VDS/VDSAT/gm/gds "
            "before AC gain/bandwidth"
        ),
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
        explicit_instance_parameters=False,
        evidence_gate="DC operating point + AC gain/bandwidth + degeneration check",
    ),
    CircuitKind.DIFFERENTIAL_PAIR: CircuitCapability(
        circuit=CircuitKind.DIFFERENTIAL_PAIR,
        stage="Gate 3",
        executable=False,
        operations=(),
        parameters=("input_width_um", "length_um", "tail_current_ua", "load_ff"),
        explicit_instance_parameters=False,
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
    if (
        task.circuit is CircuitKind.COMMON_SOURCE
        and task.operation in {Operation.SCHEMATIC_CREATE, Operation.PARAMETERS_APPLY}
    ):
        testbench_only = sorted(supplied & {"bias_v", "vdd_v"})
        if testbench_only:
            raise UnsupportedCapability(
                f"{task.operation.value} cannot persist testbench-only parameters: "
                + ", ".join(testbench_only)
            )


def catalog_as_dicts() -> list[dict]:
    result: list[dict] = []
    for capability in CIRCUIT_CATALOG.values():
        item = asdict(capability)
        item["circuit"] = capability.circuit.value
        item["operations"] = [operation.value for operation in capability.operations]
        result.append(item)
    return result
