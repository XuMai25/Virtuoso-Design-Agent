"""Linearize one structured OA/si/Spectre operating point without another AC run."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .characterization import (
    DeviceDataSource,
    MOS_CHARGE_DERIVATIVE_NAMES,
    MosCharacterizationArtifact,
    MosPolarity,
    MosSmallSignalPoint,
)
from .models import EvidenceSource, RunRecord, RunStatus, StrictModel
from .small_signal import (
    BoundaryVoltage,
    CapacitorSmallSignalInstance,
    LinearExpression,
    MosSmallSignalInstance,
    ResistorSmallSignalInstance,
    SmallSignalNetworkRequest,
    SmallSignalNetworkResult,
    analyze_small_signal_network,
)


_LEGACY_CAPACITANCE_NAMES = ("cgs_f", "cgd_f", "cgb_f", "cdb_f", "csb_f")


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OperatingPointSmallSignalPolicy(_FiniteStrictModel):
    """Explicit graph boundaries for linearizing one circuit-run action."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=72,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    simulation_action: StrictStr = Field(min_length=1, max_length=128)
    expected_topology_variant: StrictStr = Field(min_length=1, max_length=128)
    expected_pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    expected_process_corner: StrictStr = Field(min_length=1, max_length=64)
    expected_temperature_c: float = Field(ge=-273.15, le=300.0)
    device_polarities: dict[StrictStr, MosPolarity] = Field(
        min_length=1, max_length=1024
    )
    boundary_voltages: list[BoundaryVoltage] = Field(min_length=1, max_length=256)
    input_expression: LinearExpression
    output_expression: LinearExpression
    capacitors: list[CapacitorSmallSignalInstance] = Field(
        default_factory=list, max_length=4096
    )
    frequencies_hz: list[float] = Field(min_length=1, max_length=4096)
    reference_points: int = Field(default=1, ge=1, le=32)
    maximum_reference_variation_db: float = Field(default=0.5, ge=0.0, le=10.0)
    analysis_mode: Literal["low_frequency", "frequency_response"] = "low_frequency"
    require_gmb_when_body_effect_active: bool = False
    require_complete_intrinsic_capacitance: bool = False

    @model_validator(mode="after")
    def validate_frequency_contract(self) -> "OperatingPointSmallSignalPolicy":
        if self.reference_points > len(self.frequencies_hz):
            raise ValueError("reference_points exceeds the frequency count")
        if any(
            not math.isfinite(value) or value <= 0.0
            for value in self.frequencies_hz
        ):
            raise ValueError("frequencies_hz must be finite and positive")
        if any(
            right <= left
            for left, right in zip(self.frequencies_hz, self.frequencies_hz[1:])
        ):
            raise ValueError("frequencies_hz must be strictly increasing")
        if (
            self.analysis_mode == "frequency_response"
            and len(self.frequencies_hz) < 2
        ):
            raise ValueError("frequency_response requires at least two frequencies")
        return self


class OperatingPointMetricComparison(_FiniteStrictModel):
    predicted: float
    actual: float
    absolute_error: float = Field(ge=0.0)
    relative_error: float | None = Field(default=None, ge=0.0)
    error_kind: Literal["absolute", "relative", "wrapped_degrees"]
    predicted_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    actual_evidence_source: Literal[EvidenceSource.EDA_RESULT] = (
        EvidenceSource.EDA_RESULT
    )


class OperatingPointSmallSignalResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: str
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    circuit_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_action_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    circuit_task_id: str
    simulation_action: str
    status: RunStatus
    analysis_scope: Literal[
        "low_frequency_conductance",
        "linear_frequency_response",
    ]
    topology_variant: str
    pdk_profile: str
    process_corner: str
    temperature_c: float
    graph_binding: dict[str, Any]
    missing_quantities_by_instance: dict[str, list[str]]
    covered_metrics: list[str]
    uncovered_metrics: list[str]
    network_result: SmallSignalNetworkResult
    eda_comparison: dict[str, OperatingPointMetricComparison]
    evidence_sources: dict[str, EvidenceSource]
    assumptions: list[str]
    warnings: list[str]


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _device_operating_points(
    operating_point: dict[str, Any], mos_names: list[str]
) -> dict[str, dict[str, Any]]:
    values = _mapping(
        operating_point.get("device_values"), "operating-point device_values"
    )
    nested = {
        name: values.get(name)
        for name in mos_names
        if isinstance(values.get(name), dict)
    }
    if len(nested) == len(mos_names):
        return {
            name: _mapping(nested[name], f"operating point for {name}")
            for name in mos_names
        }
    if len(mos_names) == 1 and all(
        name in values
        for name in (
            "ids_a",
            "vgs_v",
            "vds_v",
            "vdsat_v",
            "gm_s",
            "gds_s",
        )
    ):
        return {mos_names[0]: values}
    raise ValueError(
        "operating-point device_values do not cover every structured si MOS instance"
    )


def _effective_width_um(instance: dict[str, Any], name: str) -> float:
    if "total_width_um" in instance:
        return _finite(instance["total_width_um"], f"{name}.total_width_um")
    width = _finite(
        instance.get("netlist_width_um", instance.get("width_um")),
        f"{name}.netlist_width_um",
    )
    fingers = _finite(instance.get("fingers", 1.0), f"{name}.fingers")
    multiplicity = _finite(
        instance.get("multiplicity", 1.0), f"{name}.multiplicity"
    )
    return width * fingers * multiplicity


def _normalized_point(
    *,
    point_id: str,
    model: str,
    polarity: MosPolarity,
    length_um: float,
    width_um: float,
    values: dict[str, Any],
    vsb_magnitude_v: float,
) -> tuple[MosSmallSignalPoint, list[str]]:
    current_a = abs(_finite(values.get("ids_a"), f"{point_id}.ids_a"))
    if current_a <= 0.0:
        raise ValueError(f"{point_id}.ids_a must be nonzero")
    if width_um <= 0.0:
        raise ValueError(f"{point_id} effective width must be positive")
    gm_s = abs(_finite(values.get("gm_s"), f"{point_id}.gm_s"))
    gds_s = abs(_finite(values.get("gds_s"), f"{point_id}.gds_s"))
    if gm_s <= 0.0:
        raise ValueError(f"{point_id}.gm_s must be nonzero")
    missing: list[str] = []
    if "gmb_s" in values:
        gmb_s = abs(_finite(values["gmb_s"], f"{point_id}.gmb_s"))
    else:
        gmb_s = 0.0
        missing.append("gmb_s")

    legacy_caps: dict[str, float] = {}
    for name in _LEGACY_CAPACITANCE_NAMES:
        if name in values:
            legacy_caps[name] = abs(_finite(values[name], f"{point_id}.{name}"))
        else:
            legacy_caps[name] = 0.0
            missing.append(name)
    charge_matrix: dict[str, float] = {}
    raw_matrix = values.get("charge_derivative_matrix_f")
    if raw_matrix is None:
        missing.append("charge_derivative_matrix_f")
    else:
        matrix = _mapping(raw_matrix, f"{point_id}.charge_derivative_matrix_f")
        if set(matrix) != set(MOS_CHARGE_DERIVATIVE_NAMES):
            raise ValueError(f"{point_id} has an incomplete charge-derivative matrix")
        charge_matrix = {
            name: _finite(matrix[name], f"{point_id}.{name}") / width_um
            for name in MOS_CHARGE_DERIVATIVE_NAMES
        }
        missing = [
            name for name in missing if name not in _LEGACY_CAPACITANCE_NAMES
        ]
    junction_caps: dict[str, float] = {}
    for name in ("cjd_f", "cjs_f"):
        if name in values:
            junction_caps[name] = abs(_finite(values[name], f"{point_id}.{name}"))
        else:
            junction_caps[name] = 0.0
            missing.append(name)

    return (
        MosSmallSignalPoint(
            id=point_id,
            model=model,
            polarity=polarity,
            length_um=length_um,
            vgs_magnitude_v=abs(_finite(values.get("vgs_v"), f"{point_id}.vgs_v")),
            vds_magnitude_v=abs(_finite(values.get("vds_v"), f"{point_id}.vds_v")),
            vsb_magnitude_v=vsb_magnitude_v,
            vdsat_magnitude_v=abs(
                _finite(values.get("vdsat_v"), f"{point_id}.vdsat_v")
            ),
            drain_current_density_a_per_um=current_a / width_um,
            gm_over_id_per_v=gm_s / current_a,
            gds_over_id_per_v=gds_s / current_a,
            gmb_over_id_per_v=gmb_s / current_a,
            cgs_f_per_um=legacy_caps["cgs_f"] / width_um,
            cgd_f_per_um=legacy_caps["cgd_f"] / width_um,
            cgb_f_per_um=legacy_caps["cgb_f"] / width_um,
            cdb_f_per_um=legacy_caps["cdb_f"] / width_um,
            csb_f_per_um=legacy_caps["csb_f"] / width_um,
            cjd_f_per_um=junction_caps["cjd_f"] / width_um,
            cjs_f_per_um=junction_caps["cjs_f"] / width_um,
            charge_derivative_matrix_f_per_um=charge_matrix,
        ),
        sorted(set(missing)),
    )


def _relative_comparison(predicted: float, actual: float) -> OperatingPointMetricComparison:
    absolute_error = abs(predicted - actual)
    scale = max(abs(actual), 1e-30)
    return OperatingPointMetricComparison(
        predicted=predicted,
        actual=actual,
        absolute_error=absolute_error,
        relative_error=absolute_error / scale,
        error_kind="relative",
    )


def _absolute_comparison(predicted: float, actual: float) -> OperatingPointMetricComparison:
    return OperatingPointMetricComparison(
        predicted=predicted,
        actual=actual,
        absolute_error=abs(predicted - actual),
        error_kind="absolute",
    )


def _phase_comparison(predicted: float, actual: float) -> OperatingPointMetricComparison:
    error = abs((predicted - actual + 180.0) % 360.0 - 180.0)
    return OperatingPointMetricComparison(
        predicted=predicted,
        actual=actual,
        absolute_error=error,
        error_kind="wrapped_degrees",
    )


def analyze_operating_point_small_signal_run(
    policy: OperatingPointSmallSignalPolicy,
    circuit_run_path: Path,
) -> OperatingPointSmallSignalResult:
    """Bind structured si + exact EDA OP values to the generic nodal solver."""

    raw_payload = json.loads(circuit_run_path.read_text(encoding="utf-8"))
    run = RunRecord.model_validate(raw_payload)
    if run.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("operating-point linearization requires a bridge run record")
    if run.status is not RunStatus.SUCCEEDED:
        raise ValueError("source circuit run did not succeed")
    actions = [item for item in run.actions if item.action == policy.simulation_action]
    if len(actions) != 1:
        raise ValueError(
            f"expected exactly one action {policy.simulation_action}; found {len(actions)}"
        )
    action = actions[0]
    if action.status != "succeeded" or action.evidence_source is not EvidenceSource.EDA_RESULT:
        raise ValueError("source simulation action must be succeeded eda_result evidence")
    details = action.details
    evidence = _mapping(details.get("evidence"), "simulation evidence")
    netlist = _mapping(evidence.get("netlist"), "si netlist evidence")
    operating_point = _mapping(
        evidence.get("operating_point"), "operating-point evidence"
    )
    testbench = _mapping(evidence.get("testbench"), "testbench evidence")
    if netlist.get("source") != EvidenceSource.EDA_RESULT.value:
        raise ValueError("structured si netlist must be eda_result evidence")
    if operating_point.get("source") != EvidenceSource.EDA_RESULT.value:
        raise ValueError("operating point must be eda_result evidence")
    topology_variant = str(netlist.get("topology_variant", ""))
    if topology_variant != policy.expected_topology_variant:
        raise ValueError("si topology variant does not match the linearization policy")

    model_configuration = _mapping(
        testbench.get("model_configuration"), "testbench model_configuration"
    )
    pdk_profile = str(model_configuration.get("profile", ""))
    if pdk_profile != policy.expected_pdk_profile:
        raise ValueError("testbench PDK profile does not match the policy")
    includes = model_configuration.get("includes")
    include_sections = {
        str(item.get("section"))
        for item in includes
        if isinstance(includes, list) and isinstance(item, dict) and item.get("section")
    } if isinstance(includes, list) else set()
    configured_corner = model_configuration.get("process_corner")
    if configured_corner is not None:
        if str(configured_corner) != policy.expected_process_corner:
            raise ValueError("testbench process corner does not match the policy")
    elif include_sections and policy.expected_process_corner not in include_sections:
        raise ValueError("testbench model section does not match the policy corner")
    raw_temperature = model_configuration.get("temperature_c")
    warnings: list[str] = []
    assumptions: list[str] = []
    if raw_temperature is None:
        assumptions.append(
            "The policy temperature is used because the source run records the "
            "simulator-default temperature rather than an explicit numeric value."
        )
    elif not math.isclose(
        _finite(raw_temperature, "testbench temperature_c"),
        policy.expected_temperature_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("testbench temperature does not match the policy")

    instances = _mapping(netlist.get("instances"), "si netlist instances")
    mos_names = sorted(
        name
        for name, item in instances.items()
        if isinstance(item, dict) and item.get("model") != "resistor"
    )
    if not mos_names:
        raise ValueError("structured si netlist contains no MOS instance")
    if set(policy.device_polarities) != set(mos_names):
        raise ValueError(
            "device_polarities must exactly cover the structured si MOS instances"
        )
    device_ops = _device_operating_points(operating_point, mos_names)
    node_values = _mapping(
        operating_point.get("node_values_v"), "operating-point node_values_v"
    )
    run_sha256 = _file_sha256(circuit_run_path)
    artifacts: list[MosCharacterizationArtifact] = []
    mosfets: list[MosSmallSignalInstance] = []
    missing_by_instance: dict[str, list[str]] = {}
    geometry_bindings: dict[str, Any] = {}
    body_effect_missing: list[str] = []
    capacitance_incomplete: list[str] = []
    for index, name in enumerate(mos_names, start=1):
        item = _mapping(instances[name], f"si instance {name}")
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 4 or not all(
            isinstance(node, str) for node in nodes
        ):
            raise ValueError(f"si MOS {name} must have four named terminals")
        drain, gate, source, bulk = nodes
        for node in nodes:
            if node != "0" and node not in node_values:
                raise ValueError(f"operating point is missing node voltage {node}")
        source_v = 0.0 if source == "0" else _finite(node_values[source], source)
        bulk_v = 0.0 if bulk == "0" else _finite(node_values[bulk], bulk)
        drain_v = 0.0 if drain == "0" else _finite(node_values[drain], drain)
        gate_v = 0.0 if gate == "0" else _finite(node_values[gate], gate)
        values = device_ops[name]
        for label, op_value, node_value in (
            ("VGS", values.get("vgs_v"), gate_v - source_v),
            ("VDS", values.get("vds_v"), drain_v - source_v),
        ):
            if not math.isclose(
                abs(_finite(op_value, f"{name}.{label}")),
                abs(node_value),
                rel_tol=1e-4,
                abs_tol=1e-5,
            ):
                raise ValueError(f"{name} {label} does not match DC node voltages")
        width_um = _effective_width_um(item, name)
        length_um = _finite(item.get("length_um"), f"{name}.length_um")
        point_id = f"op-{index}"
        point, missing = _normalized_point(
            point_id=point_id,
            model=str(item.get("model", "")),
            polarity=policy.device_polarities[name],
            length_um=length_um,
            width_um=width_um,
            values=values,
            vsb_magnitude_v=abs(source_v - bulk_v),
        )
        missing_by_instance[name] = missing
        if source != bulk and "gmb_s" in missing:
            body_effect_missing.append(name)
        has_intrinsic_model = (
            "charge_derivative_matrix_f" not in missing
            or not any(
                cap_name in missing for cap_name in _LEGACY_CAPACITANCE_NAMES
            )
        )
        has_junction_model = not any(
            cap_name in missing for cap_name in ("cjd_f", "cjs_f")
        )
        if not has_intrinsic_model or not has_junction_model:
            capacitance_incomplete.append(name)
        artifact_id = f"{policy.id}-op-{index}"
        raw_model_parameters = item.get("model_parameters", {})
        model_parameters = (
            {str(key): str(value) for key, value in raw_model_parameters.items()}
            if isinstance(raw_model_parameters, dict)
            else {}
        )
        artifacts.append(
            MosCharacterizationArtifact(
                id=artifact_id,
                source=DeviceDataSource.EDA_OPERATING_POINT,
                source_artifact_sha256=run_sha256,
                pdk_profile=policy.expected_pdk_profile,
                process_corner=policy.expected_process_corner,
                temperature_c=policy.expected_temperature_c,
                characterized_width_um=width_um,
                model_parameters_by_polarity=(
                    {policy.device_polarities[name].value: model_parameters}
                    if model_parameters
                    else {}
                ),
                raw_data_evidence_source=EvidenceSource.EDA_RESULT,
                normalized_point_evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                points=[point],
            )
        )
        mosfets.append(
            MosSmallSignalInstance(
                name=name,
                model=point.model,
                characterization_id=artifact_id,
                point_id=point.id,
                drain=drain,
                gate=gate,
                source=source,
                bulk=bulk,
                width_um=width_um,
                length_um=length_um,
                multiplicity=1.0,
                vgs_magnitude_v=point.vgs_magnitude_v,
                vds_magnitude_v=point.vds_magnitude_v,
                vsb_magnitude_v=point.vsb_magnitude_v,
                maximum_voltage_mismatch_v=1e-9,
            )
        )
        geometry_bindings[name] = {
            "effective_width_um": width_um,
            "length_um": length_um,
            "model": point.model,
            "model_parameters_sha256": (
                _canonical_sha256(model_parameters) if model_parameters else None
            ),
            "characterization_id": artifact_id,
            "point_id": point.id,
        }

    if body_effect_missing and policy.require_gmb_when_body_effect_active:
        raise ValueError(
            "body-effect-active MOS instances are missing gmb_s: "
            f"{body_effect_missing}"
        )
    if (
        capacitance_incomplete
        and policy.require_complete_intrinsic_capacitance
    ):
        raise ValueError(
            "MOS instances are missing intrinsic capacitance data: "
            f"{capacitance_incomplete}"
        )

    resistors: list[ResistorSmallSignalInstance] = []
    for name, raw_item in instances.items():
        item = _mapping(raw_item, f"si instance {name}")
        if item.get("model") != "resistor":
            continue
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 2:
            raise ValueError(f"si resistor {name} must have two named terminals")
        resistors.append(
            ResistorSmallSignalInstance(
                name=name,
                positive=str(nodes[0]),
                negative=str(nodes[1]),
                resistance_ohm=_finite(
                    item.get("resistance_ohm"), f"{name}.resistance_ohm"
                ),
            )
        )
    request = SmallSignalNetworkRequest(
        id=f"{policy.id}-network",
        characterization=artifacts[0],
        additional_characterizations=artifacts[1:],
        mosfets=mosfets,
        resistors=resistors,
        capacitors=policy.capacitors,
        boundary_voltages=policy.boundary_voltages,
        input_expression=policy.input_expression,
        output_expression=policy.output_expression,
        frequencies_hz=policy.frequencies_hz,
        reference_points=policy.reference_points,
        maximum_reference_variation_db=policy.maximum_reference_variation_db,
    )
    network_result = analyze_small_signal_network(request)
    comparisons: dict[str, OperatingPointMetricComparison] = {}
    actual_metrics = details.get("metrics")
    if isinstance(actual_metrics, dict):
        comparison_specs = (
            (
                "low_frequency_gain_v_per_v",
                network_result.low_frequency_gain_v_per_v,
                _relative_comparison,
            ),
            (
                "low_frequency_gain_db",
                network_result.low_frequency_gain_db,
                _absolute_comparison,
            ),
            (
                "low_frequency_phase_deg",
                network_result.low_frequency_phase_deg,
                _phase_comparison,
            ),
        )
        for metric, predicted, builder in comparison_specs:
            if metric in actual_metrics:
                comparisons[metric] = builder(
                    predicted, _finite(actual_metrics[metric], metric)
                )
        if network_result.bandwidth_3db_hz is not None:
            for metric, predicted in (
                ("bandwidth_3db_hz", network_result.bandwidth_3db_hz),
                (
                    "gain_bandwidth_product_hz",
                    network_result.gain_bandwidth_product_hz,
                ),
            ):
                if predicted is not None and metric in actual_metrics:
                    comparisons[metric] = _relative_comparison(
                        predicted, _finite(actual_metrics[metric], metric)
                    )

    covered_metrics = [
        "low_frequency_gain_v_per_v",
        "low_frequency_gain_db",
        "low_frequency_phase_deg",
    ]
    uncovered_metrics = ["noise", "distortion", "slew", "large_signal_settling"]
    analysis_scope: Literal[
        "low_frequency_conductance", "linear_frequency_response"
    ] = "low_frequency_conductance"
    incomplete_requested_scope = bool(body_effect_missing)
    if policy.analysis_mode == "frequency_response":
        analysis_scope = "linear_frequency_response"
        if capacitance_incomplete:
            uncovered_metrics.extend(
                [
                    "bandwidth_3db_hz",
                    "gain_bandwidth_product_hz",
                    "unity_gain_frequency_hz",
                ]
            )
            incomplete_requested_scope = True
        else:
            covered_metrics.extend(
                ["bandwidth_3db_hz", "gain_bandwidth_product_hz"]
            )
    else:
        uncovered_metrics.extend(
            [
                "bandwidth_3db_hz",
                "gain_bandwidth_product_hz",
                "unity_gain_frequency_hz",
            ]
        )
    if body_effect_missing:
        warnings.append(
            "Body-effect-active instances are missing gmb; their low-frequency "
            "prediction is an explicit conductance-only approximation."
        )
    if capacitance_incomplete:
        warnings.append(
            "At least one instance lacks a complete intrinsic capacitance model; "
            "do not use this result to claim bandwidth, GBW, or unity frequency."
        )
    warnings.append(
        "This is a linearization of an already obtained EDA operating point, not "
        "an independent proof that a new DC operating point exists."
    )
    assumptions.extend(
        [
            "The structured si graph is the circuit topology source.",
            "Spectre OP gm/gds/gmb and charge derivatives are local derivatives only.",
            "Supply and bias nodes declared by the policy are AC-fixed boundaries.",
        ]
    )
    action_sha256 = _canonical_sha256(action.model_dump(mode="json"))
    return OperatingPointSmallSignalResult(
        policy_id=policy.id,
        policy_sha256=_canonical_sha256(policy.model_dump(mode="json")),
        circuit_run_sha256=run_sha256,
        source_action_sha256=action_sha256,
        circuit_task_id=run.task_id,
        simulation_action=action.action,
        status=(
            RunStatus.PARTIAL
            if incomplete_requested_scope
            else network_result.status
        ),
        analysis_scope=analysis_scope,
        topology_variant=topology_variant,
        pdk_profile=policy.expected_pdk_profile,
        process_corner=policy.expected_process_corner,
        temperature_c=policy.expected_temperature_c,
        graph_binding={
            "method": "structured_si_graph_plus_same_action_eda_operating_point",
            "topology_equation_hardcoded": False,
            "netlist_sha256": netlist.get("sha256"),
            "device_instances": mos_names,
            "resistor_instances": [item.name for item in resistors],
            "policy_capacitors": [item.name for item in policy.capacitors],
            "geometry_by_instance": geometry_bindings,
            "node_device_consistency": operating_point.get(
                "node_device_consistency"
            ),
            "kcl_consistency": operating_point.get("kcl_consistency"),
        },
        missing_quantities_by_instance=missing_by_instance,
        covered_metrics=covered_metrics,
        uncovered_metrics=sorted(set(uncovered_metrics)),
        network_result=network_result,
        eda_comparison=comparisons,
        evidence_sources={
            "policy": EvidenceSource.USER_INPUT,
            "si_netlist": EvidenceSource.EDA_RESULT,
            "operating_point": EvidenceSource.EDA_RESULT,
            "normalized_device_values": EvidenceSource.SOFTWARE_INFERENCE,
            "network_solution": EvidenceSource.SOFTWARE_INFERENCE,
            "eda_comparison": EvidenceSource.SOFTWARE_INFERENCE,
        },
        assumptions=assumptions,
        warnings=warnings,
    )
