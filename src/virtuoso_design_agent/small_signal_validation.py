"""Bind same-source circuit evidence to the generic small-signal solver."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import ConfigDict, Field, StrictStr

from .characterization import (
    MosCharacterizationArtifact,
    MosInterpolationResult,
    MosPolarity,
    interpolate_mos_characterization_point,
)
from .models import (
    DesignTarget,
    EvidenceSource,
    RunRecord,
    RunStatus,
    StrictModel,
)
from .small_signal import (
    BoundaryVoltage,
    CapacitorSmallSignalInstance,
    ComplexValue,
    LinearExpression,
    MosSmallSignalInstance,
    ResistorSmallSignalInstance,
    SmallSignalNetworkRequest,
    SmallSignalNetworkResult,
    analyze_small_signal_network,
)


_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SmallSignalValidationThresholds(_FiniteStrictModel):
    maximum_device_dc_relative_error: float = Field(gt=0.0, le=1.0)
    maximum_gain_error_db: float = Field(gt=0.0, le=20.0)
    maximum_phase_error_deg: float = Field(gt=0.0, le=180.0)
    maximum_bandwidth_relative_error: float = Field(gt=0.0, le=1.0)
    maximum_gbw_relative_error: float = Field(gt=0.0, le=1.0)


class SmallSignalCircuitValidationPolicy(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_target: DesignTarget
    expected_pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    expected_process_corner: StrictStr = Field(min_length=1, max_length=64)
    expected_temperature_c: float = Field(ge=-273.15, le=300.0)
    expected_vdd_v: float = Field(gt=0.0)
    expected_topology_variant: Literal[
        "common_source",
        "source_degenerated_common_source",
        "pmos_current_mirror_load_nmos_differential_pair_with_tail_device",
    ]
    characterization_action: StrictStr = "device.characterize.validate"
    characterization_raw_action: StrictStr = "device.characterize"
    simulation_action: StrictStr = "simulation.candidate.1"
    operating_condition_name: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    require_exact_characterization_width: bool = True
    require_exact_model_parameters: bool = True
    require_characterization_source_instance_binding: bool = False
    thresholds: SmallSignalValidationThresholds


class ScalarValidationComparison(_FiniteStrictModel):
    predicted: float
    actual: float
    error: float = Field(ge=0.0)
    error_kind: Literal["absolute", "relative", "wrapped_degrees"]
    threshold: float = Field(gt=0.0)
    passed: bool


class DeviceDcValidation(_FiniteStrictModel):
    instance: str
    interpolation: MosInterpolationResult
    comparisons: dict[str, ScalarValidationComparison]
    passed: bool


class SmallSignalCircuitValidationResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    policy_id: str
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    characterization_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    characterization_run_sha256s: list[str] = Field(min_length=1, max_length=1024)
    circuit_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    characterization_task_id: str
    characterization_task_ids: list[str] = Field(min_length=1, max_length=1024)
    circuit_task_id: str
    status: RunStatus
    gate_passed: bool
    target: DesignTarget
    pdk_profile: str
    process_corner: str
    temperature_c: float
    vdd_v: float
    topology_variant: str
    graph_binding: dict[str, Any]
    device_dc_validation: list[DeviceDcValidation]
    ac_validation: dict[str, ScalarValidationComparison]
    network_result: SmallSignalNetworkResult
    raw_artifact_bindings: dict[str, Any]
    evidence_sources: dict[str, EvidenceSource]
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
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _valid_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _load_run(path: Path, label: str) -> RunRecord:
    try:
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label} run record {path}") from exc


def _select_action(
    record: RunRecord,
    action_name: str,
    evidence_source: EvidenceSource,
) -> dict[str, Any]:
    matches = [item for item in record.actions if item.action == action_name]
    if len(matches) != 1:
        raise ValueError(
            f"run {record.task_id} must contain exactly one action {action_name}"
        )
    action = matches[0]
    if action.status != "succeeded":
        raise ValueError(f"action {action_name} did not succeed")
    if action.evidence_source is not evidence_source:
        raise ValueError(
            f"action {action_name} evidence is {action.evidence_source.value}, "
            f"expected {evidence_source.value}"
        )
    return action.details


def _select_operating_condition_result(
    details: dict[str, Any], policy: SmallSignalCircuitValidationPolicy
) -> dict[str, Any]:
    if policy.operating_condition_name is None:
        return details
    rows = details.get("operating_condition_results")
    if not isinstance(rows, list):
        raise ValueError("simulation action has no operating-condition results")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("condition"), dict)
        and row["condition"].get("name") == policy.operating_condition_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("result"), dict):
        raise ValueError(
            "simulation action must contain exactly one declared operating condition"
        )
    condition = matches[0]["condition"]
    if condition.get("process_corner") != policy.expected_process_corner:
        raise ValueError("operating-condition corner does not match the policy")
    if not math.isclose(
        _finite(condition.get("temperature_c"), "operating-condition temperature"),
        policy.expected_temperature_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("operating-condition temperature does not match the policy")
    if not math.isclose(
        _finite(condition.get("vdd_v"), "operating-condition VDD"),
        policy.expected_vdd_v,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("operating-condition VDD does not match the policy")
    return _mapping(matches[0]["result"], "operating-condition result")


def _assert_matching_values(
    expected: dict[str, Any], actual: dict[str, Any], label: str
) -> None:
    if set(expected) != set(actual):
        raise ValueError(f"{label} keys do not match")
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if isinstance(expected_value, (int, float)) and isinstance(
            actual_value, (int, float)
        ):
            if not math.isclose(
                float(expected_value),
                float(actual_value),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{label} value {name} does not match")
        elif expected_value != actual_value:
            raise ValueError(f"{label} value {name} does not match")


def _relative_comparison(
    predicted: float, actual: float, threshold: float
) -> ScalarValidationComparison:
    error = abs(predicted - actual) / max(abs(predicted), abs(actual), 1e-30)
    return ScalarValidationComparison(
        predicted=predicted,
        actual=actual,
        error=error,
        error_kind="relative",
        threshold=threshold,
        passed=error <= threshold,
    )


def _absolute_comparison(
    predicted: float, actual: float, threshold: float
) -> ScalarValidationComparison:
    error = abs(predicted - actual)
    return ScalarValidationComparison(
        predicted=predicted,
        actual=actual,
        error=error,
        error_kind="absolute",
        threshold=threshold,
        passed=error <= threshold,
    )


def _phase_comparison(
    predicted: float, actual: float, threshold: float
) -> ScalarValidationComparison:
    error = abs((predicted - actual + 180.0) % 360.0 - 180.0)
    return ScalarValidationComparison(
        predicted=predicted,
        actual=actual,
        error=error,
        error_kind="wrapped_degrees",
        threshold=threshold,
        passed=error <= threshold,
    )


def _device_operating_points(
    operating_point: dict[str, Any], mos_names: list[str]
) -> dict[str, dict[str, Any]]:
    raw = _mapping(operating_point.get("device_values"), "device operating points")
    if all(isinstance(raw.get(name), dict) for name in mos_names):
        return {name: _mapping(raw[name], f"{name} operating point") for name in mos_names}
    if len(mos_names) == 1 and all(
        name in raw for name in ("ids_a", "vgs_v", "vds_v", "vdsat_v", "gm_s", "gds_s")
    ):
        return {mos_names[0]: raw}
    raise ValueError("operating-point device values do not map to si MOS instances")


def _node_voltage(node_values: dict[str, Any], node: str) -> float:
    if node == "0":
        return 0.0
    if node not in node_values:
        raise ValueError(f"operating point is missing node {node}")
    return _finite(node_values[node], f"node {node}")


@dataclass(frozen=True)
class _LoadedCharacterization:
    run_sha256: str
    task_id: str
    artifact: MosCharacterizationArtifact
    manifest_sha256: str
    remote_root: str
    remote_simulation_dir: str
    tool_version: str


def _load_characterization_binding(
    policy: SmallSignalCircuitValidationPolicy,
    path: Path,
) -> _LoadedCharacterization:
    run = _load_run(path, "characterization")
    if run.adapter != "virtuoso-bridge-subprocess":
        raise ValueError(
            "small-signal validation requires bridge run records, not demo evidence"
        )
    if run.status is not RunStatus.SUCCEEDED:
        raise ValueError("characterization run did not succeed")
    normalized = _select_action(
        run,
        policy.characterization_action,
        EvidenceSource.SOFTWARE_INFERENCE,
    )
    raw = _select_action(
        run,
        policy.characterization_raw_action,
        EvidenceSource.EDA_RESULT,
    )
    if normalized.get("raw_data_evidence_source") != "eda_result":
        raise ValueError("characterization raw data is not eda_result")
    if normalized.get("normalization_evidence_source") != "software_inference":
        raise ValueError("characterization normalization evidence is invalid")
    artifact = MosCharacterizationArtifact.model_validate(normalized.get("artifact"))
    raw_evidence = _mapping(raw.get("evidence"), "raw characterization evidence")
    if raw.get("task_id") != run.task_id:
        raise ValueError("raw characterization task id does not match its run")
    if raw.get("pdk_profile") != artifact.pdk_profile:
        raise ValueError("raw characterization PDK does not match its artifact")
    if raw.get("process_corner") != artifact.process_corner:
        raise ValueError("raw characterization corner does not match its artifact")
    if not math.isclose(
        _finite(raw.get("temperature_c"), "raw characterization temperature"),
        artifact.temperature_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("raw characterization temperature does not match its artifact")
    if artifact.characterized_width_um is None or not math.isclose(
        _finite(raw.get("width_um"), "raw characterization width"),
        artifact.characterized_width_um,
        rel_tol=1e-9,
        abs_tol=1e-12,
    ):
        raise ValueError("raw characterization width does not match its artifact")
    if raw.get("model_parameters_by_polarity") != artifact.model_dump(mode="json")[
        "model_parameters_by_polarity"
    ]:
        raise ValueError(
            "raw characterization model parameters do not match its artifact"
        )
    source_binding = (
        artifact.source_instance_binding.model_dump(mode="json")
        if artifact.source_instance_binding is not None
        else None
    )
    if raw.get("source_instance_binding") != source_binding:
        raise ValueError(
            "raw characterization source-instance binding does not match its artifact"
        )
    if (
        policy.require_characterization_source_instance_binding
        and artifact.source_instance_binding is None
    ):
        raise ValueError("characterization has no source-instance binding")
    if artifact.source_instance_binding is not None:
        binding = artifact.source_instance_binding
        if (
            binding.source_pdk_profile != policy.expected_pdk_profile
            or binding.source_process_corner != policy.expected_process_corner
            or not math.isclose(
                binding.source_temperature_c,
                policy.expected_temperature_c,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise ValueError(
                "characterization source-instance PVT does not match the policy"
            )
        if not math.isclose(
            binding.source_width_um,
            artifact.characterized_width_um,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "characterization source-instance width does not match its artifact"
            )
        matching_polarities = {
            point.polarity.value
            for point in artifact.points
            if point.model == binding.source_model
        }
        if len(matching_polarities) != 1:
            raise ValueError(
                "characterization source model does not map to exactly one artifact polarity"
            )
        declared_parameters = artifact.model_parameters_by_polarity.get(
            next(iter(matching_polarities)),
            {},
        )
        if (
            len(declared_parameters) != binding.source_model_parameter_count
            or _canonical_sha256(declared_parameters)
            != binding.source_model_parameters_sha256
        ):
            raise ValueError(
                "characterization source-instance signature does not match its artifact"
            )
    if raw.get("raw_point_evidence_source") != "eda_result":
        raise ValueError("raw characterization points are not eda_result")
    raw_points = raw.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("raw characterization contains no operating points")
    tool_version = str(raw.get("tool_version", "")).strip()
    if not tool_version:
        raise ValueError("raw characterization has no Spectre tool version")
    manifest_hash = _valid_hash(
        raw_evidence.get("manifest_sha256"),
        "raw characterization manifest hash",
    )
    if manifest_hash != artifact.source_artifact_sha256:
        raise ValueError("raw characterization manifest does not match its artifact")
    if (
        raw_evidence.get("source") != "eda_result"
        or raw_evidence.get("artifact_manifest_complete") is not True
        or raw_evidence.get("oa_access_performed") is not False
        or raw_evidence.get("oa_write_performed") is not False
    ):
        raise ValueError("raw characterization evidence boundary is invalid")
    remote_root = raw_evidence.get("remote_run_root")
    remote_dir = raw_evidence.get("remote_simulation_dir")
    if (
        not isinstance(remote_root, str)
        or not remote_root.startswith("/data/xum/")
        or not isinstance(remote_dir, str)
        or not remote_dir.startswith(remote_root.rstrip("/") + "/")
    ):
        raise ValueError("raw characterization remote artifact path is invalid")
    if (
        artifact.pdk_profile != policy.expected_pdk_profile
        or artifact.process_corner != policy.expected_process_corner
        or not math.isclose(
            artifact.temperature_c,
            policy.expected_temperature_c,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise ValueError("characterization conditions do not match the policy")
    return _LoadedCharacterization(
        run_sha256=_file_sha256(path),
        task_id=run.task_id,
        artifact=artifact,
        manifest_sha256=manifest_hash,
        remote_root=remote_root,
        remote_simulation_dir=remote_dir,
        tool_version=tool_version,
    )


def _select_instance_characterization(
    policy: SmallSignalCircuitValidationPolicy,
    artifacts: Sequence[MosCharacterizationArtifact],
    *,
    instance_name: str,
    instance: dict[str, Any],
) -> tuple[MosCharacterizationArtifact, MosPolarity, dict[str, Any]]:
    model = str(instance.get("model", ""))
    width_um = _finite(
        instance.get("netlist_width_um", instance.get("width_um")),
        f"{instance_name}.netlist_width_um",
    )
    length_um = _finite(instance.get("length_um"), f"{instance_name}.length_um")
    unparsed = instance.get("unparsed_model_parameter_tokens", [])
    if not isinstance(unparsed, list) or any(
        not isinstance(token, str) for token in unparsed
    ):
        raise ValueError(f"{instance_name} has invalid unparsed parameter evidence")
    if policy.require_exact_model_parameters and unparsed:
        raise ValueError(
            f"{instance_name} model parameter signature is not fully parsed"
        )
    raw_parameters = instance.get("model_parameters", {})
    if policy.require_exact_model_parameters or any(
        artifact.source_instance_binding is not None for artifact in artifacts
    ):
        model_parameters = _mapping(
            raw_parameters,
            f"{instance_name}.model_parameters",
        )
    else:
        model_parameters = {}

    candidates: list[tuple[MosCharacterizationArtifact, MosPolarity]] = []
    model_matches: list[MosCharacterizationArtifact] = []
    width_matches: list[MosCharacterizationArtifact] = []
    parameter_matches: list[MosCharacterizationArtifact] = []
    for artifact in artifacts:
        polarities = {
            point.polarity for point in artifact.points if point.model == model
        }
        if not polarities:
            continue
        if len(polarities) != 1:
            raise ValueError(
                f"characterization model {model} must map to exactly one polarity"
            )
        polarity = next(iter(polarities))
        model_matches.append(artifact)
        if policy.require_exact_characterization_width:
            if artifact.characterized_width_um is None or not math.isclose(
                artifact.characterized_width_um,
                width_um,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                continue
        width_matches.append(artifact)
        if policy.require_exact_model_parameters:
            expected = artifact.model_parameters_by_polarity.get(
                polarity.value,
                {},
            )
            if model_parameters != expected:
                continue
        parameter_matches.append(artifact)
        binding = artifact.source_instance_binding
        if binding is not None:
            if (
                binding.source_model != model
                or not math.isclose(
                    binding.source_width_um,
                    width_um,
                    rel_tol=1e-9,
                    abs_tol=1e-12,
                )
                or not math.isclose(
                    binding.source_length_um,
                    length_um,
                    rel_tol=1e-9,
                    abs_tol=1e-12,
                )
                or len(model_parameters) != binding.source_model_parameter_count
                or _canonical_sha256(model_parameters)
                != binding.source_model_parameters_sha256
            ):
                continue
        candidates.append((artifact, polarity))
    if not candidates:
        if model_matches and not width_matches:
            planes = [artifact.characterized_width_um for artifact in model_matches]
            raise ValueError(
                f"{instance_name} width {width_um}um does not match the "
                f"characterized width plane(s) {planes}"
            )
        if width_matches and not parameter_matches:
            raise ValueError(
                f"{instance_name} model parameter signature does not match any "
                "characterized device"
            )
        if parameter_matches:
            raise ValueError(
                f"{instance_name} characterization source-instance signature "
                "does not match the circuit"
            )
        raise ValueError(
            f"no characterization artifact exactly matches {instance_name} "
            f"model/width/signature"
        )
    if len(candidates) != 1:
        raise ValueError(
            f"{instance_name} matches multiple characterization artifacts: "
            f"{[artifact.id for artifact, _ in candidates]}"
        )
    artifact, polarity = candidates[0]
    return artifact, polarity, model_parameters


def _bound_network(
    policy: SmallSignalCircuitValidationPolicy,
    artifacts: Sequence[MosCharacterizationArtifact],
    details: dict[str, Any],
) -> tuple[
    SmallSignalNetworkRequest,
    list[MosInterpolationResult],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    evidence = _mapping(details.get("evidence"), "simulation evidence")
    netlist = _mapping(evidence.get("netlist"), "si netlist evidence")
    operating_point = _mapping(
        evidence.get("operating_point"), "operating-point evidence"
    )
    ac_response = _mapping(evidence.get("ac_response"), "AC response evidence")
    testbench = _mapping(evidence.get("testbench"), "testbench evidence")
    instances = _mapping(netlist.get("instances"), "si netlist instances")
    mos_names = [
        name
        for name, item in instances.items()
        if isinstance(item, dict) and item.get("model") != "resistor"
    ]
    if not mos_names:
        raise ValueError("si netlist contains no MOS instance")
    device_ops = _device_operating_points(operating_point, mos_names)
    node_values = _mapping(
        operating_point.get("node_values_v"), "operating-point nodes"
    )
    interpolations: list[MosInterpolationResult] = []
    mosfets: list[MosSmallSignalInstance] = []
    bound_points_by_artifact: dict[str, list[Any]] = {
        artifact.id: [] for artifact in artifacts
    }
    characterization_by_instance: dict[str, str] = {}
    characterized_width_by_instance: dict[str, float | None] = {}
    model_parameter_signatures: dict[str, dict[str, Any]] = {}
    for name in mos_names:
        item = _mapping(instances[name], f"si instance {name}")
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 4 or not all(
            isinstance(node, str) for node in nodes
        ):
            raise ValueError(f"si MOS {name} must have four named terminals")
        drain, gate, source, bulk = nodes
        model = str(item.get("model", ""))
        artifact, polarity, model_parameters = _select_instance_characterization(
            policy,
            artifacts,
            instance_name=name,
            instance=item,
        )
        characterization_by_instance[name] = artifact.id
        characterized_width_by_instance[name] = artifact.characterized_width_um
        device_op = device_ops[name]
        vgs = abs(_finite(device_op.get("vgs_v"), f"{name}.vgs_v"))
        vds = abs(_finite(device_op.get("vds_v"), f"{name}.vds_v"))
        vsb = abs(
            _node_voltage(node_values, source) - _node_voltage(node_values, bulk)
        )
        node_vgs = abs(
            _node_voltage(node_values, gate) - _node_voltage(node_values, source)
        )
        node_vds = abs(
            _node_voltage(node_values, drain) - _node_voltage(node_values, source)
        )
        if not math.isclose(vgs, node_vgs, rel_tol=1e-4, abs_tol=1e-5):
            raise ValueError(f"{name} VGS does not match DC node voltages")
        if not math.isclose(vds, node_vds, rel_tol=1e-4, abs_tol=1e-5):
            raise ValueError(f"{name} VDS does not match DC node voltages")
        length_um = _finite(item.get("length_um"), f"{name}.length_um")
        interpolation = interpolate_mos_characterization_point(
            artifact,
            point_id=f"bound-{name}",
            model=model,
            polarity=polarity,
            length_um=length_um,
            vgs_magnitude_v=vgs,
            vds_magnitude_v=vds,
            vsb_magnitude_v=vsb,
        )
        interpolations.append(interpolation)
        bound_points_by_artifact[artifact.id].append(interpolation.point)
        width_um = _finite(
            item.get("netlist_width_um", item.get("width_um")),
            f"{name}.netlist_width_um",
        )
        if policy.require_exact_model_parameters:
            model_parameter_signatures[name] = {
                "polarity": polarity.value,
                "parameter_count": len(model_parameters),
                "sha256": _canonical_sha256(model_parameters),
            }
        multiplicity = _finite(item.get("multiplicity", 1.0), f"{name}.multiplicity")
        mosfets.append(
            MosSmallSignalInstance(
                name=name,
                model=model,
                characterization_id=artifact.id,
                point_id=interpolation.point.id,
                drain=drain,
                gate=gate,
                source=source,
                bulk=bulk,
                width_um=width_um,
                length_um=length_um,
                multiplicity=multiplicity,
                vgs_magnitude_v=vgs,
                vds_magnitude_v=vds,
                vsb_magnitude_v=vsb,
                maximum_voltage_mismatch_v=1e-9,
            )
        )
    resistors: list[ResistorSmallSignalInstance] = []
    for name, raw_item in instances.items():
        item = _mapping(raw_item, f"si instance {name}")
        if item.get("model") != "resistor":
            continue
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 2:
            raise ValueError(f"si resistor {name} must have two terminals")
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
    source_degeneration_bindings: list[dict[str, Any]] = []
    if policy.expected_topology_variant in {
        "common_source",
        "source_degenerated_common_source",
    }:
        for mosfet in mosfets:
            if mosfet.source in {"0", "VSS"}:
                continue
            matching_resistors = [
                resistor
                for resistor in resistors
                if {resistor.positive, resistor.negative} == {mosfet.source, "VSS"}
            ]
            if len(matching_resistors) != 1:
                raise ValueError(
                    f"{mosfet.name} source node {mosfet.source} must connect to VSS "
                    "through exactly one si resistor"
                )
            resistor = matching_resistors[0]
            source_degeneration_bindings.append(
                {
                    "mos_instance": mosfet.name,
                    "source_node": mosfet.source,
                    "reference_node": "VSS",
                    "resistor_instance": resistor.name,
                    "resistance_ohm": resistor.resistance_ohm,
                }
            )
    if (
        policy.expected_topology_variant == "source_degenerated_common_source"
        and not source_degeneration_bindings
    ):
        raise ValueError(
            "source-degenerated common-source topology has no bound source resistor"
        )
    testbench_values = _mapping(testbench.get("values"), "testbench values")
    capacitors: list[CapacitorSmallSignalInstance] = []
    if "load_ff" in testbench_values:
        load_ff = _finite(testbench_values["load_ff"], "testbench load_ff")
        if load_ff > 0.0:
            load_node = (
                "OUTN"
                if policy.expected_topology_variant
                == "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
                else "OUT"
            )
            capacitors.append(
                CapacitorSmallSignalInstance(
                    name="CL0",
                    positive=load_node,
                    negative="0",
                    capacitance_f=load_ff * 1e-15,
                )
            )
    frequencies = ac_response.get("frequency_hz")
    if not isinstance(frequencies, list):
        raise ValueError("AC response does not retain the EDA frequency grid")
    reference = _mapping(ac_response.get("reference"), "AC reference evidence")
    unused_artifacts = [
        artifact.id
        for artifact in artifacts
        if not bound_points_by_artifact[artifact.id]
    ]
    if unused_artifacts:
        raise ValueError(
            f"characterization artifacts are unused by the si graph: {unused_artifacts}"
        )
    bound_artifacts = [
        MosCharacterizationArtifact(
            id=artifact.id,
            source=artifact.source,
            source_artifact_sha256=artifact.source_artifact_sha256,
            pdk_profile=artifact.pdk_profile,
            process_corner=artifact.process_corner,
            temperature_c=artifact.temperature_c,
            characterized_width_um=artifact.characterized_width_um,
            model_parameters_by_polarity=artifact.model_parameters_by_polarity,
            source_instance_binding=artifact.source_instance_binding,
            raw_data_evidence_source=artifact.raw_data_evidence_source,
            normalized_point_evidence_source=artifact.normalized_point_evidence_source,
            points=bound_points_by_artifact[artifact.id],
        )
        for artifact in artifacts
    ]
    differential = (
        policy.expected_topology_variant
        == "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
    )
    boundary_voltages = (
        [
            BoundaryVoltage(node="INP", voltage=ComplexValue(real=0.5)),
            BoundaryVoltage(node="INN", voltage=ComplexValue(real=-0.5)),
            BoundaryVoltage(node="BIAS", voltage=ComplexValue()),
            BoundaryVoltage(node="VDD", voltage=ComplexValue()),
            BoundaryVoltage(node="VSS", voltage=ComplexValue()),
        ]
        if differential
        else [
            BoundaryVoltage(node="IN", voltage=ComplexValue(real=1.0)),
            BoundaryVoltage(node="VDD", voltage=ComplexValue()),
            BoundaryVoltage(node="VSS", voltage=ComplexValue()),
        ]
    )
    request = SmallSignalNetworkRequest(
        id=(f"{policy.id}-network")[:96],
        characterization=bound_artifacts[0],
        additional_characterizations=bound_artifacts[1:],
        mosfets=mosfets,
        resistors=resistors,
        capacitors=capacitors,
        boundary_voltages=boundary_voltages,
        input_expression=LinearExpression(
            terms={"INP": 1.0, "INN": -1.0} if differential else {"IN": 1.0}
        ),
        output_expression=LinearExpression(
            terms={"OUTN": 1.0} if differential else {"OUT": 1.0}
        ),
        frequencies_hz=[_finite(value, "AC frequency") for value in frequencies],
        reference_points=int(reference.get("points", 0)),
        maximum_reference_variation_db=_finite(
            reference.get("variation_limit_db"), "AC reference variation limit"
        ),
    )
    graph_binding = {
        "method": "structured_si_instances_plus_eda_dc_bias",
        "topology_equation_hardcoded": False,
        "target": policy.expected_target.model_dump(mode="json"),
        "netlist_sha256": netlist["sha256"],
        "topology_variant": netlist["topology_variant"],
        "mos_instances": mos_names,
        "resistor_instances": [item.name for item in resistors],
        "testbench_capacitors": [item.name for item in capacitors],
        "characterization_artifact_by_instance": characterization_by_instance,
        "characterized_width_um_by_instance": characterized_width_by_instance,
        "exact_width_plane_required": policy.require_exact_characterization_width,
        "exact_model_parameters_required": policy.require_exact_model_parameters,
        "model_parameter_signatures": model_parameter_signatures,
        "source_degeneration_bindings": source_degeneration_bindings,
        "node_device_consistency": operating_point.get("node_device_consistency"),
        "kcl_consistency": operating_point.get("kcl_consistency"),
    }
    return request, interpolations, device_ops, graph_binding


def validate_small_signal_runs(
    policy: SmallSignalCircuitValidationPolicy,
    characterization_run_path: Path | Sequence[Path],
    circuit_run_path: Path,
) -> SmallSignalCircuitValidationResult:
    """Validate independent MOS planes against one complete OA/si/Spectre run."""

    characterization_paths = (
        [characterization_run_path]
        if isinstance(characterization_run_path, Path)
        else list(characterization_run_path)
    )
    if not characterization_paths:
        raise ValueError("at least one characterization run is required")
    loaded_characterizations = [
        _load_characterization_binding(policy, path)
        for path in characterization_paths
    ]
    artifacts = [item.artifact for item in loaded_characterizations]
    artifact_ids = [artifact.id for artifact in artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("characterization artifact ids must be unique")
    circuit_run = _load_run(circuit_run_path, "circuit")
    circuit_run_sha256 = _file_sha256(circuit_run_path)
    if circuit_run.adapter != "virtuoso-bridge-subprocess":
        raise ValueError(
            "small-signal validation requires bridge run records, not demo evidence"
        )
    if circuit_run.status is not RunStatus.SUCCEEDED:
        raise ValueError("circuit run did not succeed")
    forbidden_write_actions = {
        "schematic.create",
        "schematic.transform",
        "parameters.apply",
        "parameters.restore",
    }
    if any(item.action in forbidden_write_actions for item in circuit_run.actions):
        raise ValueError("circuit validation run contains an OA write action")
    simulation_action_details = _select_action(
        circuit_run,
        policy.simulation_action,
        EvidenceSource.EDA_RESULT,
    )
    simulation_details = _select_operating_condition_result(
        simulation_action_details, policy
    )
    simulation_parameters = _mapping(
        simulation_details.get("parameters"), "simulation parameters"
    )
    if not math.isclose(
        _finite(simulation_parameters.get("vdd_v"), "simulation VDD"),
        policy.expected_vdd_v,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("simulation VDD does not match the validation policy")
    if simulation_details.get("analysis_complete") is not True:
        raise ValueError("circuit AC analysis is incomplete")
    if simulation_details.get("analysis_issues"):
        raise ValueError("circuit AC analysis retained unresolved issues")
    if not str(simulation_details.get("tool_version", "")).strip():
        raise ValueError("circuit run has no Spectre tool version")
    evidence = _mapping(simulation_details.get("evidence"), "simulation evidence")
    side_effects = _mapping(evidence.get("side_effects"), "side-effect evidence")
    if side_effects != {
        "oa_access_performed": True,
        "oa_write_performed": False,
        "remote_compute_performed": True,
    }:
        raise ValueError("circuit validation side effects are not read-only OA + compute")
    schematic = _mapping(evidence.get("schematic_readback"), "schematic evidence")
    netlist = _mapping(evidence.get("netlist"), "netlist evidence")
    testbench = _mapping(evidence.get("testbench"), "testbench evidence")
    operating_point = _mapping(
        evidence.get("operating_point"), "operating-point evidence"
    )
    ac_response = _mapping(evidence.get("ac_response"), "AC response evidence")
    if schematic.get("source") != "bridge_readback":
        raise ValueError("schematic target is not bridge_readback")
    if schematic.get("target") != policy.expected_target.model_dump(mode="json"):
        raise ValueError("schematic target does not match the validation policy")
    if netlist.get("source") != "eda_result":
        raise ValueError("si netlist is not eda_result")
    if operating_point.get("source") != "eda_result":
        raise ValueError("operating point is not eda_result")
    if ac_response.get("source") != "eda_result":
        raise ValueError("AC response is not eda_result")
    if ac_response.get("extraction_source") != "software_inference":
        raise ValueError("AC metric extraction evidence is invalid")
    if netlist.get("parameter_consistency") != "matched":
        raise ValueError("si netlist parameter consistency is not matched")
    if operating_point.get("node_device_consistency") != "matched":
        raise ValueError("operating-point node/device consistency is not matched")
    if operating_point.get("kcl_consistency") != "matched":
        raise ValueError("operating-point KCL consistency is not matched")
    if (
        policy.expected_topology_variant == "source_degenerated_common_source"
        and operating_point.get("source_degeneration_consistency") != "matched"
    ):
        raise ValueError(
            "operating-point source-degeneration consistency is not matched"
        )
    if schematic.get("topology_variant") != policy.expected_topology_variant:
        raise ValueError("OA topology does not match the validation policy")
    if netlist.get("topology_variant") != policy.expected_topology_variant:
        raise ValueError("si topology does not match the validation policy")
    _assert_matching_values(
        _mapping(schematic.get("semantic_parameters"), "OA semantic parameters"),
        _mapping(netlist.get("semantic_parameters"), "si semantic parameters"),
        "OA/si semantic parameters",
    )
    _assert_matching_values(
        _mapping(schematic.get("device_geometry"), "OA device geometry"),
        _mapping(netlist.get("device_geometry"), "si device geometry"),
        "OA/si device geometry",
    )
    model_configuration = _mapping(
        testbench.get("model_configuration"), "model configuration"
    )
    testbench_values = _mapping(testbench.get("values"), "testbench values")
    if not math.isclose(
        _finite(testbench_values.get("vdd_v"), "testbench VDD"),
        policy.expected_vdd_v,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("testbench VDD does not match the validation policy")
    if model_configuration.get("profile") != policy.expected_pdk_profile:
        raise ValueError("circuit PDK profile does not match the validation policy")
    if model_configuration.get("process_corner") != policy.expected_process_corner:
        raise ValueError("circuit process corner does not match the validation policy")
    if not math.isclose(
        _finite(model_configuration.get("temperature_c"), "circuit temperature"),
        policy.expected_temperature_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("circuit temperature does not match the validation policy")
    netlist_hash = _valid_hash(netlist.get("sha256"), "si netlist hash")
    testbench_hash = _valid_hash(testbench.get("sha256"), "testbench hash")
    raw_files = _mapping(ac_response.get("raw_files"), "AC raw-file evidence")
    raw_ac = _mapping(raw_files.get("ac"), "AC raw file")
    raw_ac_hash = _valid_hash(raw_ac.get("sha256"), "AC raw-file hash")
    if int(raw_ac.get("size_bytes", 0)) <= 0:
        raise ValueError("AC raw file is empty")
    if int(ac_response.get("sample_count", 0)) != len(
        ac_response.get("frequency_hz", [])
    ):
        raise ValueError("AC frequency grid does not match the EDA sample count")
    metric_sources = _mapping(
        simulation_details.get("metric_sources"), "metric evidence sources"
    )
    differential = (
        policy.expected_topology_variant
        == "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
    )
    metric_names = {
        "low_frequency_gain_db": (
            "differential_low_frequency_gain_db"
            if differential
            else "low_frequency_gain_db"
        ),
        "low_frequency_phase_deg": (
            "differential_low_frequency_phase_deg"
            if differential
            else "low_frequency_phase_deg"
        ),
        "phase_at_bandwidth_deg": (
            "differential_phase_at_bandwidth_deg"
            if differential
            else "phase_at_bandwidth_deg"
        ),
        "bandwidth_3db_hz": (
            "differential_bandwidth_3db_hz"
            if differential
            else "bandwidth_3db_hz"
        ),
        "gain_bandwidth_product_hz": (
            "differential_gain_bandwidth_product_hz"
            if differential
            else "gain_bandwidth_product_hz"
        ),
    }
    required_metrics = tuple(metric_names.values())
    if any(metric_sources.get(name) != "eda_result" for name in required_metrics):
        raise ValueError("required AC metrics are not all eda_result")

    request, interpolations, device_ops, graph_binding = _bound_network(
        policy, artifacts, simulation_details
    )
    artifact_by_instance = _mapping(
        graph_binding["characterization_artifact_by_instance"],
        "characterization bindings",
    )
    matching_instances_by_artifact = {
        artifact.id: sorted(
            name
            for name, artifact_id in artifact_by_instance.items()
            if artifact_id == artifact.id
        )
        for artifact in artifacts
    }
    graph_binding["characterization_source_instance_bindings"] = {
        artifact.id: (
            artifact.source_instance_binding.model_dump(mode="json")
            if artifact.source_instance_binding is not None
            else None
        )
        for artifact in artifacts
    }
    graph_binding["characterization_source_matching_instances"] = (
        matching_instances_by_artifact
    )
    graph_binding["characterization_source_run_is_current_circuit_run"] = {
        artifact.id: (
            artifact.source_instance_binding is not None
            and artifact.source_instance_binding.source_run_sha256
            == circuit_run_sha256
        )
        for artifact in artifacts
    }
    if len(artifacts) == 1:
        only = artifacts[0]
        graph_binding["characterization_source_instance_binding"] = (
            only.source_instance_binding.model_dump(mode="json")
            if only.source_instance_binding is not None
            else None
        )
        graph_binding["characterization_source_matching_instances"] = (
            matching_instances_by_artifact[only.id]
        )
        graph_binding["characterization_source_run_is_current_circuit_run"] = (
            only.source_instance_binding is not None
            and only.source_instance_binding.source_run_sha256 == circuit_run_sha256
        )
    network_result = analyze_small_signal_network(request)
    derived_by_name = {item.instance: item for item in network_result.derived_mos_values}
    interpolation_by_name = {
        item.point.id.removeprefix("bound-"): item for item in interpolations
    }
    threshold = policy.thresholds.maximum_device_dc_relative_error
    device_validations: list[DeviceDcValidation] = []
    for name, device_op in device_ops.items():
        derived = derived_by_name[name]
        interpolation = interpolation_by_name[name]
        comparisons = {
            "drain_current_a": _relative_comparison(
                derived.drain_current_a,
                abs(_finite(device_op.get("ids_a"), f"{name}.ids_a")),
                threshold,
            ),
            "gm_s": _relative_comparison(
                derived.gm_s,
                abs(_finite(device_op.get("gm_s"), f"{name}.gm_s")),
                threshold,
            ),
            "gds_s": _relative_comparison(
                derived.gds_s,
                abs(_finite(device_op.get("gds_s"), f"{name}.gds_s")),
                threshold,
            ),
            "vdsat_magnitude_v": _relative_comparison(
                interpolation.point.vdsat_magnitude_v,
                abs(_finite(device_op.get("vdsat_v"), f"{name}.vdsat_v")),
                threshold,
            ),
        }
        device_validations.append(
            DeviceDcValidation(
                instance=name,
                interpolation=interpolation,
                comparisons=comparisons,
                passed=all(item.passed for item in comparisons.values()),
            )
        )
    metrics = _mapping(simulation_details.get("metrics"), "circuit metrics")
    if (
        network_result.bandwidth_3db_hz is None
        or network_result.phase_at_bandwidth_deg is None
        or network_result.gain_bandwidth_product_hz is None
    ):
        raise ValueError("theory response did not resolve bandwidth and GBW")
    ac_validation = {
        "low_frequency_gain_db": _absolute_comparison(
            network_result.low_frequency_gain_db,
            _finite(
                metrics.get(metric_names["low_frequency_gain_db"]),
                "EDA low-frequency gain",
            ),
            policy.thresholds.maximum_gain_error_db,
        ),
        "low_frequency_phase_deg": _phase_comparison(
            network_result.low_frequency_phase_deg,
            _finite(
                metrics.get(metric_names["low_frequency_phase_deg"]),
                "EDA low-frequency phase",
            ),
            policy.thresholds.maximum_phase_error_deg,
        ),
        "phase_at_bandwidth_deg": _phase_comparison(
            network_result.phase_at_bandwidth_deg,
            _finite(
                metrics.get(metric_names["phase_at_bandwidth_deg"]),
                "EDA phase at bandwidth",
            ),
            policy.thresholds.maximum_phase_error_deg,
        ),
        "bandwidth_3db_hz": _relative_comparison(
            network_result.bandwidth_3db_hz,
            _finite(
                metrics.get(metric_names["bandwidth_3db_hz"]),
                "EDA bandwidth",
            ),
            policy.thresholds.maximum_bandwidth_relative_error,
        ),
        "gain_bandwidth_product_hz": _relative_comparison(
            network_result.gain_bandwidth_product_hz,
            _finite(
                metrics.get(metric_names["gain_bandwidth_product_hz"]),
                "EDA GBW",
            ),
            policy.thresholds.maximum_gbw_relative_error,
        ),
    }
    gate_passed = (
        network_result.status is RunStatus.SUCCEEDED
        and all(item.passed for item in device_validations)
        and all(item.passed for item in ac_validation.values())
    )
    characterization_hashes = [item.run_sha256 for item in loaded_characterizations]
    characterization_task_ids = [item.task_id for item in loaded_characterizations]
    all_source_bound = all(
        artifact.source_instance_binding is not None for artifact in artifacts
    )
    return SmallSignalCircuitValidationResult(
        policy_id=policy.id,
        policy_sha256=_canonical_sha256(policy.model_dump(mode="json")),
        characterization_run_sha256=characterization_hashes[0],
        characterization_run_sha256s=characterization_hashes,
        circuit_run_sha256=circuit_run_sha256,
        characterization_task_id=characterization_task_ids[0],
        characterization_task_ids=characterization_task_ids,
        circuit_task_id=circuit_run.task_id,
        status=RunStatus.SUCCEEDED if gate_passed else RunStatus.PARTIAL,
        gate_passed=gate_passed,
        target=policy.expected_target,
        pdk_profile=policy.expected_pdk_profile,
        process_corner=policy.expected_process_corner,
        temperature_c=policy.expected_temperature_c,
        vdd_v=policy.expected_vdd_v,
        topology_variant=policy.expected_topology_variant,
        graph_binding=graph_binding,
        device_dc_validation=device_validations,
        ac_validation=ac_validation,
        network_result=network_result,
        raw_artifact_bindings={
            "characterizations": [
                {
                    "artifact_id": item.artifact.id,
                    "run_sha256": item.run_sha256,
                    "task_id": item.task_id,
                    "manifest_sha256": item.manifest_sha256,
                    "remote_root": item.remote_root,
                    "remote_simulation_dir": item.remote_simulation_dir,
                    "spectre_tool_version": item.tool_version,
                    "characterized_width_um": item.artifact.characterized_width_um,
                }
                for item in loaded_characterizations
            ],
            "characterization_manifest_sha256": (
                loaded_characterizations[0].manifest_sha256
            ),
            "characterization_remote_root": loaded_characterizations[0].remote_root,
            "characterization_remote_simulation_dir": (
                loaded_characterizations[0].remote_simulation_dir
            ),
            "characterization_spectre_tool_version": (
                loaded_characterizations[0].tool_version
            ),
            "si_netlist_sha256": netlist_hash,
            "testbench_sha256": testbench_hash,
            "ac_raw_sha256": raw_ac_hash,
            "remote_netlist_path": netlist.get("remote_path"),
            "remote_testbench_path": testbench.get("remote_path"),
            "spectre_tool_version": simulation_details.get("tool_version"),
        },
        evidence_sources={
            "validation_policy": EvidenceSource.USER_INPUT,
            "oa_schematic": EvidenceSource.BRIDGE_READBACK,
            "si_netlist": EvidenceSource.EDA_RESULT,
            "operating_point": EvidenceSource.EDA_RESULT,
            "ac_raw_and_metrics": EvidenceSource.EDA_RESULT,
            "normalized_characterization": EvidenceSource.SOFTWARE_INFERENCE,
            "characterization_model_parameter_declaration": (
                EvidenceSource.SOFTWARE_INFERENCE
                if all_source_bound
                else EvidenceSource.USER_INPUT
            ),
            "si_model_parameters": EvidenceSource.EDA_RESULT,
            "model_parameter_signature_match": EvidenceSource.SOFTWARE_INFERENCE,
            **(
                {
                    "characterization_source_instance": EvidenceSource.EDA_RESULT,
                    "characterization_task_derivation": (
                        EvidenceSource.SOFTWARE_INFERENCE
                    ),
                }
                if all_source_bound
                else {}
            ),
            "bias_interpolation": EvidenceSource.SOFTWARE_INFERENCE,
            "network_prediction": EvidenceSource.SOFTWARE_INFERENCE,
            "error_gate": EvidenceSource.SOFTWARE_INFERENCE,
        },
        warnings=[
            "The validation binds a read-only OA/si graph and EDA DC bias to an "
            "independent set of nominal MOS characterization planes; it does not "
            "write OA.",
            "Passing this held-out circuit point bounds only the declared target, "
            "PDK/corner/temperature, geometry plane, bias domain, and metrics.",
            "Length interpolation and all bias extrapolation are rejected; the "
            "declared model-parameter signature must match the si instance.",
        ],
    )


def validate_common_source_small_signal_runs(
    policy: SmallSignalCircuitValidationPolicy,
    characterization_run_path: Path | Sequence[Path],
    circuit_run_path: Path,
) -> SmallSignalCircuitValidationResult:
    """Backward-compatible name for the now topology-generic evidence binder."""

    return validate_small_signal_runs(
        policy,
        characterization_run_path,
        circuit_run_path,
    )
