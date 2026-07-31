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
            "cascode_width_um",
            "cascode_length_um",
        }
    ),
    CircuitKind.DIFFERENTIAL_PAIR: frozenset(
        {
            "input_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "tail_width_um",
            "tail_length_um",
            "pmos_load_width_um",
            "pmos_load_length_um",
        }
    ),
}


def task_semantic_parameter_names(task: TaskSpec) -> set[str]:
    """Return every semantic name declared by fixed or finite-search inputs."""

    supplied = set(task.parameters) | set(task.parameter_space)
    if task.candidate_set is not None:
        supplied.update(task.candidate_set.candidates[0].parameters)
    if task.theory_seed is not None:
        supplied.update(task.theory_seed.candidates[0].parameters)
    return supplied


CIRCUIT_CATALOG: dict[CircuitKind, CircuitCapability] = {
    CircuitKind.MOS_DEVICE: CircuitCapability(
        circuit=CircuitKind.MOS_DEVICE,
        stage=(
            "Gate 7A characterization + Gate 7B/7C common-source + "
            "Gate 7D differential-pair validation"
        ),
        executable=True,
        operations=(Operation.DEVICE_CHARACTERIZE,),
        parameters=(),
        explicit_instance_parameters=False,
        evidence_gate=(
            "standalone foundry-model Spectre operating points + complete raw-file "
            "SHA-256 manifest + finite NMOS/PMOS sign-normalized gm/gds/gmb, signed "
            "4x4 terminal-charge derivatives, and separate cjd/cjs table + declared "
            "interpolation holdout audit; "
            "240-point nominal TSMC N28 top_tt table and four real holdouts live; "
            "exact-width and 31-parameter si signature nominal/source-degenerated "
            "common-source held-out DC/gain/phase/BW/GBW validation live; "
            "real-si-derived characterization task binding live; three exact-width "
            "and exact-signature artifacts bound to one active-load differential-pair "
            "OA/si/Spectre run with held-out DC/gain/phase/BW/GBW validation live; "
            "multi-finger/multiplicity and optional PVT remain pending"
        ),
    ),
    CircuitKind.NETLIST_PREVIEW: CircuitCapability(
        circuit=CircuitKind.NETLIST_PREVIEW,
        stage="Lightweight topology screening",
        executable=True,
        operations=(Operation.SIMULATION_RUN,),
        parameters=(),
        explicit_instance_parameters=False,
        evidence_gate=(
            "validated structured circuit graph rendered directly to standalone "
            "foundry-model Spectre DC/AC decks; no OA, si, or Maestro access. Raw "
            "Spectre results are eda_result, while cross-variant comparison is "
            "software_inference. One nominal TSMC N28 common-source/cascode A/B is "
            "live with complete DC OP, 241-point AC, per-file SHA-256 manifests, and "
            "zero residual Spectre/si/Maestro processes. A hash-bound nine-point "
            "retrospective calibration retained the OA-to-si winner in a top-three "
            "shortlist with Spearman rho 0.933; the shortlist compiler and normal "
            "OA-to-si replay retained the same winner while reducing three-point OA "
            "wall time by 71.667% versus the full nine-point run. A reference-free "
            "freeze plus later audit then prospectively retained the true winner for "
            "one unseen eight-point differential-pair local domain with Spearman rho "
            "1.0 and checkpoint recovery. Absolute preview values still differ from "
            "OA-to-si by as much as 30.35%; other topologies and PVT still require "
            "their own prospective same-source or ADE validation"
        ),
    ),
    CircuitKind.EXISTING_SCHEMATIC: CircuitCapability(
        circuit=CircuitKind.EXISTING_SCHEMATIC,
        stage="Bridge-preserving topology-conditioned OA surface",
        executable=True,
        operations=(
            Operation.SCHEMATIC_INSPECT,
            Operation.SCHEMATIC_SYMBOL_GENERATE,
            Operation.SCHEMATIC_TRANSFORM,
            Operation.PARAMETERS_APPLY,
            Operation.SIMULATION_RUN,
            Operation.DESIGN_TUNE,
            Operation.DESIGN_CLOSE_LOOP,
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
            "predeclared topology-delta CAS with bounded add/remove-instance, "
            "terminal reconnect, net/pin operations, complete independent readback and "
            "exact inverse restoration live on a non-overwrite TSMC N28 cell + "
            "local design-context binding for user-declared roles, frozen objects, "
            "parameter permissions, analysis/metric intent, and topology-delta scope + "
            "typed generic OA-to-si DC/AC testbench, signal, OP-save, and "
            "CDF-to-netlist binding contract live on one TSMC N28 cascode stage, "
            "with exact specialized-path OP/AC agreement and zero OA writes + "
            "generic finite raw-instance tuning with checkpointed OA staging, direct "
            "CDF-to-si binding, Spectre AC, final commit, and transport resume live for "
            "one flat nominal TSMC N28 width field; no-feasible restoration and budget "
            "paths locally verified + one predeclared reversible topology alternative "
            "has completed a live four-point, multi-field, objective-ranked raw-parameter "
            "domain with OA-to-si-to-Spectre evidence, exact inverse, atomic winner "
            "writeback, and transport resume on nominal flat TSMC N28; controller-level "
            "no-feasible and post-save fault paths remain local + "
            "ordered generic DC/AC/transient/noise stage gates with EDA-completeness "
            "aware early rejection and candidate-boundary shared-netlist execution "
            "live on three nominal TSMC N28 multi-field tuples; all metrics exactly "
            "matched isolated execution while simulation action time fell 58.1% + "
            "winner-only shared-netlist DC/AC/transient/noise verification live on "
            "the nominal two-point GBW winner across explicit TT/27C/0.90V and "
            "SS/125C/0.81V conditions, including transport recovery and final OA "
            "readback + up to seven common-baseline independent topology alternatives "
            "with exact inverse/checkpoint selection locally verified; a three-topology, "
            "two-parameter OA-to-si DC/AC domain, transport resume, and cascode winner "
            "writeback are live + non-overwrite schematic-to-symbol generation with exact "
            "source/pin binding, session-setting restoration, and independent symbol "
            "readback is live + explicit one-level primitive-child OA/subcircuit graph "
            "binding, canonical child topology/placement evidence, and flat-versus-"
            "hierarchical OA-to-si-to-Spectre DC/AC equivalence are live on nominal TSMC "
            "N28 (maximum relative metric difference 4.09e-15) + "
            "strict one-level TOP/CHILD parameter scopes with exact child topology/placement "
            "binding, targeted child CDF write/readback, subcircuit parameter consistency, "
            "three-point specification-first DC/AC selection, and child winner writeback are "
            "live on the retained nominal TSMC N28 hierarchy fixture; shared-child per-instance "
            "override, derived CDF semantics, and deeper hierarchy remain rejected + "
            "local logical/physical pin and full-placement binding + exact-state "
            "post-save inverse recovery + live instance-scoped "
            "NMOS symbol-master/CDF-subset OA-to-si-to-Spectre round-trip + "
            "live non-overwrite ADE prepare/setup patch/background run-resume + exact-"
            "history/result/log and OA-to-runtime-input consistency; native Maestro "
            "CL and VDDxCL sweep setup/input-bundle/RDB point binding and pinned "
            "scalar-to-constraint mapping plus test-scope CL x environmental-corner "
            "raw-result binding live on TSMC N28; generic-delta post-save automatic "
            "recovery and pin/placement replay are live, while controller-internal "
            "post-save injection remains pending; PMOS master migration, human capture, "
            "Maestro real-PVT corner mapping, and multi-test/multi-analysis mapping remain "
            "pending"
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
        stage="Gate 10 held-out-covered W/RD local-response EDA validation verified",
        executable=True,
        operations=_STANDARD_OPERATIONS + (Operation.SCHEMATIC_TRANSFORM,),
        parameters=(
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "cascode_width_um",
            "cascode_length_um",
            "cascode_bias_v",
            "bias_v",
            "vdd_v",
            "load_ff",
        ),
        explicit_instance_parameters=True,
        evidence_gate=(
            "OA readback + si netlist consistency + DC region + complex AC + "
            "bounded W/L/RD/RS plus bias/load quality tuning, OA writeback, "
            "infeasible restore, checkpoint recovery, fixed-design TT/SS/FF "
            "verification, optional read-only PVT-aware bias tuning, reversible "
            "source-degeneration patching, and explicit MN0.fingers bounded tuning "
            "live on TSMC N28; six hash-bound local-response W/RD/RS tuples now "
            "complete full-quality OA/si/Spectre selection and writeback with "
            "recommendation agreement, while the first pass pointwise output-swing "
            "prediction accuracy remains partial; held-out parameter-direction "
            "coverage and the smaller fixed-RS new-anchor W/RD refresh are now "
            "live with 6/6 feasible points, recommendation agreement, 60/60 "
            "pointwise comparisons passing, checkpoint recovery, and independent "
            "best-point OA readback; RS sensitivity and PVT are not extrapolated; "
            "a recoverable cascode-common-source delta, two-device DC/KCL gate, "
            "nine matching hash-bound OP-derived W/L/VCAS DC/AC candidates, best "
            "writeback, and exact baseline restoration are live at nominal top_tt; "
            "same-action structured-si plus EDA-OP matrix replay predicts baseline/"
            "cascode low-frequency gain within 0.158%/0.646%, while complete "
            "gmb/charge-derivative live capture and dynamic/noise/linearity "
            "prediction remain pending; "
            "non-overwrite ADE "
            "prepare/setup/background exact-history run-resume is live on the "
            "inverter handoff; live PVT-aware OA-design-variable writeback and "
            "common-source capture/variable/sweep/real-PVT ADE gates remain pending"
        ),
    ),
    CircuitKind.SOURCE_DEGENERATED_COMMON_SOURCE: CircuitCapability(
        circuit=CircuitKind.SOURCE_DEGENERATED_COMMON_SOURCE,
        stage="Gate 7C verified through the common_source topology variant",
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
        evidence_gate=(
            "no separate executor or duplicated template; use common_source plus "
            "the reversible source-degeneration transform. OA/si/DC/AC, bounded "
            "tuning, and exact-signature small-signal migration are live"
        ),
    ),
    CircuitKind.DIFFERENTIAL_PAIR: CircuitCapability(
        circuit=CircuitKind.DIFFERENTIAL_PAIR,
        stage=(
            "Gate 10 held-out Wn/Wp/Wtail local-response EDA validation verified at "
            "nominal TSMC N28"
        ),
        executable=True,
        operations=(
            Operation.SCHEMATIC_CREATE,
            Operation.SCHEMATIC_INSPECT,
            Operation.PARAMETERS_APPLY,
            Operation.SIMULATION_RUN,
            Operation.DESIGN_TUNE,
            Operation.DESIGN_CLOSE_LOOP,
            Operation.SCHEMATIC_TRANSFORM,
        ),
        parameters=(
            "input_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "tail_width_um",
            "tail_length_um",
            "pmos_load_width_um",
            "pmos_load_length_um",
            "tail_current_ua",
            "tail_output_resistance_ohm",
            "tail_bias_v",
            "common_mode_v",
            "vdd_v",
            "load_ff",
        ),
        explicit_instance_parameters=True,
        evidence_gate=(
            "exact MN0/MN1/RD0/RD1 core, optional MNTAIL, and optional symmetric "
            "RS0/RS1 OA topology + symmetric W/L/R plus tail W/L and source-R "
            "readback + reversible exact-delta add/remove with placement restore + "
            "si netlist consistency + Spectre DC branch balance/tail-current/KCL/"
            "source-resistor-current/dual-saturation metrics + bounded OA "
            "writeback/recovery + differential "
            "AC gain/bandwidth/GBW + finite-tail paired CMRR response/bandwidth + "
            "sampled input-common-mode range + coherent differential transient "
            "THD/P1dB + differential noise + real-tail bias/width and source-R "
            "tuning live on TSMC N28; reversible PMOS current-mirror-load delta, "
            "matched W/L surface, DC mirror/KCL/region metrics, explicit "
            "single-ended OUTN AC/CMRR/transient/noise semantics, ICMR, bounded "
            "bias/load and geometry search, budget/infeasible paths, exact "
            "restore, and checkpoint recovery are live at nominal TSMC N28; "
            "a same-si-netlist three-run PSRR+/PSRR- contract, one nominal point, "
            "and a four-point band-limited bias/load search with raw AC hashes and "
            "transport resume are live; Gate 8 additionally binds passed Gate 7D "
            "characterization/validation hashes into six atomic, 5 nm-grid theory "
            "tuples and verifies OA write/readback, si, DC/differential/common AC, "
            "EDA-only ranking, checkpoint resume, selected-point PSRR/noise/"
            "linearity/ICMR, and final writeback. Four of six tuples were feasible, "
            "while the pointwise theory-accuracy gate remained partial. Hash-bound "
            "local DC-OP relinearization then produced six Wn/Wp/Wtail tuples; all "
            "six completed same-source DC/differential/common AC, passed constraints, "
            "and agreed with the predicted minimum-power recommendation. Exact live "
            "validation passed 90/90 comparisons with 2.569% worst new-point error; "
            "three transport system events restored the anchor and resumed safely, "
            "and the selected widths were independently read back from OA. Legacy "
            "results missing serialized error floors require their canonical-hash-"
            "bound original policy rather than schema defaults. A generic topology-"
            "delta then composed the current-mirror load with symmetric source "
            "degeneration on a new cell and completed OA/si, DC/AC/CMRR/noise/"
            "transient/ICMR/PSRR, exact inverse, and restored DC live; its P1dB was "
            "not bracketed and nominal PSRR remained about 11-13 dB. "
            "The provisional 20 dB PSRR gate was infeasible; PVT/mismatch, "
            "new-selected-point PSRR/noise/linearity/ICMR, slew/P1dB, and ADE "
            "handoff remain pending"
        ),
    ),
}


def validate_task_capability(task: TaskSpec) -> None:
    capability = CIRCUIT_CATALOG[task.circuit]
    if not capability.executable or task.operation not in capability.operations:
        raise UnsupportedCapability(
            f"{task.circuit.value} is {capability.stage}; "
            f"operation {task.operation.value} is not executable yet"
        )
    if (
        (
            task.instance_parameter_updates
            or task.instance_parameter_space
            or (
                task.candidate_set is not None
                and task.candidate_set.candidates[0].instance_parameter_updates
            )
        )
        and not capability.explicit_instance_parameters
    ):
        raise UnsupportedCapability(
            f"{task.circuit.value} does not expose explicit instance parameters"
        )
    allowed = set(capability.parameters)
    supplied = task_semantic_parameter_names(task)
    unknown = sorted(supplied - allowed)
    if unknown:
        raise UnsupportedCapability(
            f"unsupported parameters for {task.circuit.value}: {', '.join(unknown)}"
        )
    winner_conditions = (
        task.winner_verification.operating_conditions
        if task.winner_verification is not None
        else []
    )
    all_operating_conditions = [
        *task.operating_conditions,
        *winner_conditions,
    ]
    if all_operating_conditions:
        from .profiles import load_pdk_profile

        profile = load_pdk_profile(task.pdk_profile)
        requested_corners = {
            condition.process_corner for condition in all_operating_conditions
        }
        available_corners = set(profile.process_corners) | {profile.model_section}
        missing_corners = sorted(requested_corners - available_corners)
        if missing_corners:
            raise UnsupportedCapability(
                f"PDK profile {profile.name} does not map process corner(s): "
                + ", ".join(missing_corners)
            )
    if task.operation is Operation.SCHEMATIC_TRANSFORM:
        transform_action = task.resolved_schematic_transform_action()
        if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
            expected = set()
        elif task.circuit is CircuitKind.INVERTER:
            expected = {"vdd_v", "load_ff"}
        elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            if transform_action is SchematicTransformAction.ADD_TAIL_DEVICE:
                expected = {"tail_width_um", "tail_length_um"}
            elif transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
                expected = set()
            elif (
                transform_action
                is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
            ):
                expected = {"pmos_load_width_um", "pmos_load_length_um"}
            elif transform_action is SchematicTransformAction.RESTORE_RESISTIVE_LOAD:
                expected = {"load_resistance_ohm"}
            else:
                expected = {"source_resistance_ohm"}
        elif transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
            expected = set()
        else:
            expected = {"source_resistance_ohm"}
        if supplied != expected:
            if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
                raise UnsupportedCapability(
                    "generic topology_delta does not accept semantic parameters"
                )
            if task.circuit is CircuitKind.INVERTER:
                raise UnsupportedCapability(
                    "schematic.transform for inverter requires exactly vdd_v and "
                    "load_ff"
                )
            if task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                if transform_action is SchematicTransformAction.ADD_TAIL_DEVICE:
                    raise UnsupportedCapability(
                        "add_tail_device requires exactly tail_width_um and "
                        "tail_length_um"
                    )
                if transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
                    raise UnsupportedCapability(
                        "remove_source_degeneration does not accept parameters"
                    )
                if (
                    transform_action
                    is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
                ):
                    raise UnsupportedCapability(
                        "replace_resistive_load_with_current_mirror requires exactly "
                        "pmos_load_width_um and pmos_load_length_um"
                    )
                if transform_action is SchematicTransformAction.RESTORE_RESISTIVE_LOAD:
                    raise UnsupportedCapability(
                        "restore_resistive_load requires exactly load_resistance_ohm"
                    )
                raise UnsupportedCapability(
                    "add_source_degeneration requires exactly source_resistance_ohm"
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
        testbench_only = sorted(
            supplied & {"bias_v", "cascode_bias_v", "vdd_v", "load_ff"}
        )
        if testbench_only:
            raise UnsupportedCapability(
                f"{task.operation.value} cannot persist testbench-only parameters: "
                + ", ".join(testbench_only)
            )
    if (
        task.circuit is CircuitKind.DIFFERENTIAL_PAIR
        and task.operation in {Operation.SCHEMATIC_CREATE, Operation.PARAMETERS_APPLY}
    ):
        testbench_only = sorted(
            supplied
            & {
                "tail_current_ua",
                "tail_output_resistance_ohm",
                "tail_bias_v",
                "common_mode_v",
                "vdd_v",
                "load_ff",
            }
        )
        if testbench_only:
            raise UnsupportedCapability(
                f"{task.operation.value} cannot persist testbench-only parameters: "
                + ", ".join(testbench_only)
            )
    if (
        task.circuit is CircuitKind.DIFFERENTIAL_PAIR
        and task.operation is Operation.SCHEMATIC_CREATE
        and supplied
        & {
            "tail_width_um",
            "tail_length_um",
            "pmos_load_width_um",
            "pmos_load_length_um",
        }
    ):
        raise UnsupportedCapability(
            "schematic.create builds the nominal differential-pair core; use "
            "schematic.transform for tail-device or active-load geometry"
        )
    if (
        task.circuit is CircuitKind.DIFFERENTIAL_PAIR
        and task.operation is Operation.SCHEMATIC_CREATE
        and "source_resistance_ohm" in supplied
    ):
        raise UnsupportedCapability(
            "schematic.create builds the nominal differential-pair core; use "
            "schematic.transform add_source_degeneration after add_tail_device"
        )
    if (
        task.circuit is CircuitKind.DIFFERENTIAL_PAIR
        and "tail_bias_v" in supplied
        and supplied & {"tail_current_ua", "tail_output_resistance_ohm"}
    ):
        raise UnsupportedCapability(
            "tail_bias_v for an OA tail device is mutually exclusive with the "
            "external ideal-tail current/resistance parameters"
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
        task.circuit is CircuitKind.DIFFERENTIAL_PAIR
        and "load_ff" in supplied
        and task.resolved_analysis() is AnalysisKind.DC
    ):
        raise UnsupportedCapability(
            "load_ff is a differential-pair dynamic-analysis testbench parameter "
            "and cannot be used with analysis='dc'"
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
    if (
        task.circuit is CircuitKind.COMMON_SOURCE
        and task.operation is Operation.SCHEMATIC_CREATE
        and supplied & {"cascode_width_um", "cascode_length_um"}
    ):
        raise UnsupportedCapability(
            "schematic.create builds the nominal common-source topology; use a "
            "generic topology_delta to add MNCAS/VCAS/NCAS before applying "
            "cascode geometry"
        )


def task_requests_oa_parameter_write(task: TaskSpec) -> bool:
    names = OA_SEMANTIC_PARAMETER_NAMES.get(task.circuit, frozenset())
    supplied = task_semantic_parameter_names(task)
    return bool(
        names & supplied
        or task.instance_parameter_updates
        or task.instance_parameter_space
        or (
            task.candidate_set is not None
            and task.candidate_set.candidates[0].instance_parameter_updates
        )
    )


def catalog_as_dicts() -> list[dict]:
    result: list[dict] = []
    for capability in CIRCUIT_CATALOG.values():
        item = asdict(capability)
        item["circuit"] = capability.circuit.value
        item["operations"] = [operation.value for operation in capability.operations]
        result.append(item)
    return result
