"""Circuit capability catalog for the current product stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import (
    AnalysisKind,
    CircuitKind,
    Operation,
    SchematicTransformAction,
    TaskSpec,
)


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


_STANDARD_OPERATIONS = (
    Operation.SCHEMATIC_CREATE,
    Operation.SCHEMATIC_INSPECT,
    Operation.PARAMETERS_APPLY,
    Operation.ADE_PREPARE,
    Operation.ADE_CAPTURE,
    Operation.ADE_RUN,
    Operation.ADE_VARIABLES_APPLY,
    Operation.ADE_CORNERS_APPLY,
    Operation.ADE_SETUP_APPLY,
    Operation.SIMULATION_RUN,
    Operation.DESIGN_TUNE,
    Operation.DESIGN_CLOSE_LOOP,
)

OA_SEMANTIC_PARAMETER_NAMES: dict[CircuitKind, frozenset[str]] = {
    CircuitKind.INVERTER: frozenset(
        {"nmos_width_um", "pmos_width_um", "length_um"}
    ),
    CircuitKind.COMMON_SOURCE: frozenset(
        {
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
        }
    ),
}

CIRCUIT_CATALOG: dict[CircuitKind, CircuitCapability] = {
    CircuitKind.EXISTING_SCHEMATIC: CircuitCapability(
        circuit=CircuitKind.EXISTING_SCHEMATIC,
        stage="Bridge-preserving manual OA surface",
        executable=True,
        operations=(
            Operation.SCHEMATIC_INSPECT,
            Operation.PARAMETERS_APPLY,
            Operation.ADE_PREPARE,
            Operation.ADE_CAPTURE,
            Operation.ADE_RUN,
            Operation.ADE_VARIABLES_APPLY,
            Operation.ADE_CORNERS_APPLY,
            Operation.ADE_SETUP_APPLY,
        ),
        parameters=(),
        explicit_instance_parameters=True,
        evidence_gate=(
            "unfiltered Bridge schematic readback + targeted CDF value verification + "
            "live non-overwrite ADE prepare/setup patch/background run-resume + exact-"
            "history/result/log and OA-to-runtime-input consistency; native Maestro "
            "CL and VDDxCL sweep setup/input-bundle/RDB point binding and pinned "
            "scalar-to-constraint mapping plus test-scope CL x environmental-corner "
            "raw-result binding live on TSMC N28; human capture, real PVT corners, "
            "and multi-test/multi-analysis mapping pending"
        ),
    ),
    CircuitKind.INVERTER: CircuitCapability(
        circuit=CircuitKind.INVERTER,
        stage="L5A vertical slice",
        executable=True,
        operations=_STANDARD_OPERATIONS + (Operation.SCHEMATIC_TRANSFORM,),
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
            "bounded search + live non-overwrite ADE prepare/setup patch/background "
            "run-resume and OA-to-runtime-input consistency; native Maestro CL and "
            "VDDxCL sweep setup/input-bundle/exact-history RDB binding plus "
            "delay/skew/supply-energy constraint mapping, test/corner-scoped values, "
            "global selection CAS, and environmental-corner quality selection live; "
            "capture, real PVT corners, and multi-test/multi-analysis mapping pending"
        ),
    ),
    CircuitKind.COMMON_SOURCE: CircuitCapability(
        circuit=CircuitKind.COMMON_SOURCE,
        stage="Gate 2 optional PVT-aware quality tuning verified",
        executable=True,
        operations=_STANDARD_OPERATIONS + (Operation.SCHEMATIC_TRANSFORM,),
        parameters=(
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "bias_v",
            "vdd_v",
            "load_ff",
        ),
        explicit_instance_parameters=True,
        evidence_gate=(
            "OA readback + si netlist consistency + DC region + complex AC + "
            "bounded W/L/RD/RS plus bias/load quality tuning, OA writeback, "
            "infeasible restore, checkpoint recovery, fixed-design TT/SS/FF "
            "verification, and optional read-only PVT-aware bias tuning live on "
            "TSMC N28; non-overwrite ADE "
            "prepare/setup/background exact-history run-resume is live on the "
            "inverter handoff; live PVT-aware OA-design-variable writeback and "
            "common-source capture/variable/sweep/real-PVT ADE gates remain pending"
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
    if task.operating_conditions:
        from .profiles import load_pdk_profile

        profile = load_pdk_profile(task.pdk_profile)
        requested_corners = {
            condition.process_corner for condition in task.operating_conditions
        }
        missing_corners = sorted(requested_corners - set(profile.process_corners))
        if missing_corners:
            raise UnsupportedCapability(
                f"PDK profile {profile.name} does not map process corner(s): "
                + ", ".join(missing_corners)
            )
    if task.operation is Operation.SCHEMATIC_TRANSFORM:
        transform_action = task.resolved_schematic_transform_action()
        if task.circuit is CircuitKind.INVERTER:
            expected = {"vdd_v", "load_ff"}
        elif transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
            expected = set()
        else:
            expected = {"source_resistance_ohm"}
        if supplied != expected:
            if task.circuit is CircuitKind.INVERTER:
                raise UnsupportedCapability(
                    "schematic.transform for inverter requires exactly vdd_v and "
                    "load_ff"
                )
            if transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
                raise UnsupportedCapability(
                    "remove_source_degeneration does not accept parameters"
                )
            raise UnsupportedCapability(
                "add_source_degeneration requires exactly source_resistance_ohm"
            )
    if (
        task.circuit is CircuitKind.COMMON_SOURCE
        and task.operation in {Operation.SCHEMATIC_CREATE, Operation.PARAMETERS_APPLY}
    ):
        testbench_only = sorted(supplied & {"bias_v", "vdd_v", "load_ff"})
        if testbench_only:
            raise UnsupportedCapability(
                f"{task.operation.value} cannot persist testbench-only parameters: "
                + ", ".join(testbench_only)
            )
    if (
        task.circuit is CircuitKind.COMMON_SOURCE
        and "load_ff" in supplied
        and task.resolved_analysis() is AnalysisKind.DC
    ):
        raise UnsupportedCapability(
            "load_ff is a common-source dynamic-analysis testbench parameter and "
            "cannot be used with analysis='dc'"
        )
    if (
        task.circuit is CircuitKind.COMMON_SOURCE
        and task.operation is Operation.SCHEMATIC_CREATE
        and "source_resistance_ohm" in supplied
    ):
        raise UnsupportedCapability(
            "schematic.create builds the nominal common-source topology; use "
            "schematic.transform to add source degeneration to an existing cellview"
        )


def task_requests_oa_parameter_write(task: TaskSpec) -> bool:
    names = OA_SEMANTIC_PARAMETER_NAMES.get(task.circuit, frozenset())
    supplied = set(task.parameters) | set(task.parameter_space)
    return bool(names & supplied)


def catalog_as_dicts() -> list[dict]:
    result: list[dict] = []
    for capability in CIRCUIT_CATALOG.values():
        item = asdict(capability)
        item["circuit"] = capability.circuit.value
        item["operations"] = [operation.value for operation in capability.operations]
        result.append(item)
    return result
