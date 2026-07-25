"""Derive a topology-local theory request from validated PDK artifacts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .characterization import (
    DeviceDataSource,
    MosCharacterizationArtifact,
    MosSmallSignalPoint,
    interpolate_mos_characterization_point,
)
from .models import (
    EvidenceSource,
    MetricConstraint,
    Objective,
    RunRecord,
    RunStatus,
    StrictModel,
)
from .small_signal_validation import SmallSignalCircuitValidationResult
from .theory import (
    DeviceCharacterization,
    DifferentialPairTheoryRequest,
    MosCharacterizationPoint,
    WidthBounds,
)


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DifferentialPairTheoryRoleMapping(_FiniteStrictModel):
    input_instances: list[StrictStr] = Field(
        default_factory=lambda: ["MN0", "MN1"], min_length=2, max_length=2
    )
    load_instances: list[StrictStr] = Field(
        default_factory=lambda: ["MP0", "MP1"], min_length=2, max_length=2
    )
    input_reference_instance: StrictStr = "MN0"
    load_reference_instance: StrictStr = "MP0"
    mirror_diode_load_instance: StrictStr = "MP0"
    tail_instance: StrictStr = "MNTAIL"

    @model_validator(mode="after")
    def references_belong_to_roles(self) -> "DifferentialPairTheoryRoleMapping":
        if len(set(self.input_instances)) != 2:
            raise ValueError("input_instances must be distinct")
        if len(set(self.load_instances)) != 2:
            raise ValueError("load_instances must be distinct")
        if self.input_reference_instance not in self.input_instances:
            raise ValueError("input_reference_instance is not an input instance")
        if self.load_reference_instance not in self.load_instances:
            raise ValueError("load_reference_instance is not a load instance")
        if self.mirror_diode_load_instance not in self.load_instances:
            raise ValueError("mirror_diode_load_instance is not a load instance")
        if self.tail_instance in set(self.input_instances) | set(self.load_instances):
            raise ValueError("tail_instance must be distinct from input/load instances")
        return self


class DifferentialPairTheoryDerivationPolicy(_FiniteStrictModel):
    """Explicit constraints and provenance CAS for a real-PDK theory request."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    request_id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_validation_policy_id: StrictStr = Field(min_length=1, max_length=96)
    expected_validation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    load_capacitance_f: float = Field(gt=0.0)
    sizing_margin_fraction: float = Field(default=0.05, ge=0.0, le=1.0)
    maximum_characterization_voltage_mismatch_v: float = Field(
        default=0.02, ge=0.0, le=0.2
    )
    input_width: WidthBounds
    load_width: WidthBounds
    tail_width: WidthBounds
    constraints: list[MetricConstraint] = Field(min_length=1, max_length=16)
    objective: Objective
    roles: DifferentialPairTheoryRoleMapping = Field(
        default_factory=DifferentialPairTheoryRoleMapping
    )
    vgs_selection: Literal["all_characterized_values"] = "all_characterized_values"
    load_bias_rule: Literal["diode_connected_vds_equals_vgs"] = (
        "diode_connected_vds_equals_vgs"
    )
    output_capacitance_rule: Literal[
        "drain_charge_self_plus_cjd",
        "legacy_cgd_cdb_plus_cjd",
    ] = (
        "drain_charge_self_plus_cjd"
    )
    evidence_source: Literal[EvidenceSource.USER_INPUT] = EvidenceSource.USER_INPUT


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_artifact(path: Path) -> tuple[str, MosCharacterizationArtifact]:
    run = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if run.adapter != "virtuoso-bridge-subprocess" or run.status is not RunStatus.SUCCEEDED:
        raise ValueError("theory derivation requires a successful real-Bridge characterization")
    matches = [
        action
        for action in run.actions
        if action.action == "device.characterize.validate"
        and action.status == "succeeded"
        and action.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    ]
    if len(matches) != 1:
        raise ValueError(
            "characterization run must contain exactly one validated artifact action"
        )
    artifact = MosCharacterizationArtifact.model_validate(
        matches[0].details.get("artifact")
    )
    return _file_sha256(path), artifact


def _bound_points(
    validation: SmallSignalCircuitValidationResult,
) -> dict[str, MosSmallSignalPoint]:
    points = {
        item.instance: item.interpolation.point
        for item in validation.device_dc_validation
    }
    if len(points) != len(validation.device_dc_validation):
        raise ValueError("validation repeats a device instance")
    return points


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _role_artifact(
    validation: SmallSignalCircuitValidationResult,
    artifacts: dict[str, MosCharacterizationArtifact],
    instance: str,
) -> MosCharacterizationArtifact:
    raw_map = validation.graph_binding.get("characterization_artifact_by_instance")
    if not isinstance(raw_map, dict) or not isinstance(raw_map.get(instance), str):
        raise ValueError(f"validation does not bind a characterization to {instance}")
    artifact_id = str(raw_map[instance])
    if artifact_id not in artifacts:
        raise ValueError(f"characterization artifact {artifact_id} was not supplied")
    return artifacts[artifact_id]


def _vgs_axis(
    artifact: MosCharacterizationArtifact,
    reference: MosSmallSignalPoint,
) -> list[float]:
    values = sorted(
        {
            point.vgs_magnitude_v
            for point in artifact.points
            if point.model == reference.model
            and point.polarity is reference.polarity
            and math.isclose(
                point.length_um,
                reference.length_um,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        }
    )
    if not values:
        raise ValueError(f"artifact {artifact.id} has no matching VGS axis")
    return values


def _theory_point(
    *,
    role: str,
    index: int,
    artifact: MosCharacterizationArtifact,
    reference: MosSmallSignalPoint,
    vgs_magnitude_v: float,
    vds_magnitude_v: float,
    output_capacitance_rule: str,
) -> MosCharacterizationPoint:
    interpolation = interpolate_mos_characterization_point(
        artifact,
        point_id=f"theory-{role}-{index:02d}",
        model=reference.model,
        polarity=reference.polarity,
        length_um=reference.length_um,
        vgs_magnitude_v=vgs_magnitude_v,
        vds_magnitude_v=vds_magnitude_v,
        vsb_magnitude_v=reference.vsb_magnitude_v,
    )
    point = interpolation.point
    matrix = point.charge_derivative_matrix_f_per_um
    if output_capacitance_rule == "drain_charge_self_plus_cjd":
        if not matrix or "cdd" not in matrix:
            raise ValueError(
                f"artifact {artifact.id} lacks the validated charge matrix required "
                "by drain_charge_self_plus_cjd"
            )
        output_capacitance = float(matrix["cdd"]) + point.cjd_f_per_um
    else:
        if matrix:
            raise ValueError(
                "legacy_cgd_cdb_plus_cjd is only valid for an explicitly legacy "
                "artifact without a charge matrix"
            )
        output_capacitance = (
            point.cgd_f_per_um + point.cdb_f_per_um + point.cjd_f_per_um
        )
    if not math.isfinite(output_capacitance) or output_capacitance <= 0.0:
        raise ValueError(
            f"artifact {artifact.id} produced a non-positive drain output capacitance"
        )
    if artifact.source_artifact_sha256 is None:
        raise ValueError(f"artifact {artifact.id} has no raw manifest SHA-256")
    return MosCharacterizationPoint(
        id=f"{role}-{index:02d}",
        length_um=point.length_um,
        gm_over_id_per_v=point.gm_over_id_per_v,
        drain_current_density_a_per_um=point.drain_current_density_a_per_um,
        gds_over_id_per_v=point.gds_over_id_per_v,
        output_capacitance_f_per_um=output_capacitance,
        vdsat_v=point.vdsat_magnitude_v,
        model=point.model,
        vgs_magnitude_v=point.vgs_magnitude_v,
        vds_magnitude_v=point.vds_magnitude_v,
        vsb_magnitude_v=point.vsb_magnitude_v,
        source_artifact_id=artifact.id,
        source_artifact_sha256=artifact.source_artifact_sha256,
    )


def derive_differential_pair_theory_request(
    policy: DifferentialPairTheoryDerivationPolicy,
    validation_path: Path,
    characterization_run_paths: list[Path],
) -> DifferentialPairTheoryRequest:
    """Use all characterized VGS values around one validated topology bias."""

    if _file_sha256(validation_path) != policy.expected_validation_sha256:
        raise ValueError("small-signal validation SHA-256 does not match policy")
    validation = SmallSignalCircuitValidationResult.model_validate_json(
        validation_path.read_text(encoding="utf-8")
    )
    if (
        validation.policy_id != policy.expected_validation_policy_id
        or validation.status is not RunStatus.SUCCEEDED
        or not validation.gate_passed
    ):
        raise ValueError("small-signal validation identity/status does not pass policy")
    if validation.topology_variant != (
        "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
    ):
        raise ValueError("theory derivation requires the validated active-load topology")

    loaded = [_load_artifact(path) for path in characterization_run_paths]
    supplied_hashes = {run_hash for run_hash, _ in loaded}
    if supplied_hashes != set(validation.characterization_run_sha256s):
        raise ValueError(
            "characterization run SHA-256 set does not match the validation record"
        )
    artifacts = {artifact.id: artifact for _, artifact in loaded}
    if len(artifacts) != len(loaded):
        raise ValueError("supplied characterization artifacts repeat an id")
    for artifact in artifacts.values():
        if (
            artifact.source is not DeviceDataSource.PDK_CHARACTERIZATION
            or artifact.pdk_profile != validation.pdk_profile
            or artifact.process_corner != validation.process_corner
            or not math.isclose(
                artifact.temperature_c,
                validation.temperature_c,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise ValueError(
                f"artifact {artifact.id} source/PVT does not match validation"
            )

    bound = _bound_points(validation)
    roles = policy.roles
    required_instances = (
        set(roles.input_instances)
        | set(roles.load_instances)
        | {roles.tail_instance}
    )
    missing = sorted(required_instances - set(bound))
    if missing:
        raise ValueError("validation is missing role instances: " + ", ".join(missing))

    input_reference = bound[roles.input_reference_instance]
    load_reference = bound[roles.load_reference_instance]
    tail_reference = bound[roles.tail_instance]
    input_artifact = _role_artifact(
        validation, artifacts, roles.input_reference_instance
    )
    load_artifact = _role_artifact(
        validation, artifacts, roles.load_reference_instance
    )
    tail_artifact = _role_artifact(validation, artifacts, roles.tail_instance)

    input_points = [
        _theory_point(
            role="input",
            index=index,
            artifact=input_artifact,
            reference=input_reference,
            vgs_magnitude_v=vgs,
            vds_magnitude_v=input_reference.vds_magnitude_v,
            output_capacitance_rule=policy.output_capacitance_rule,
        )
        for index, vgs in enumerate(
            _vgs_axis(input_artifact, input_reference), start=1
        )
    ]
    load_points = [
        _theory_point(
            role="load",
            index=index,
            artifact=load_artifact,
            reference=load_reference,
            vgs_magnitude_v=vgs,
            vds_magnitude_v=vgs,
            output_capacitance_rule=policy.output_capacitance_rule,
        )
        for index, vgs in enumerate(
            _vgs_axis(load_artifact, load_reference), start=1
        )
    ]
    tail_points = [
        _theory_point(
            role="tail",
            index=index,
            artifact=tail_artifact,
            reference=tail_reference,
            vgs_magnitude_v=vgs,
            vds_magnitude_v=tail_reference.vds_magnitude_v,
            output_capacitance_rule=policy.output_capacitance_rule,
        )
        for index, vgs in enumerate(
            _vgs_axis(tail_artifact, tail_reference), start=1
        )
    ]

    tail_node_v = tail_reference.vds_magnitude_v
    output_common_mode_v = _mean(
        [
            tail_node_v + bound[instance].vds_magnitude_v
            for instance in roles.input_instances
        ]
    )
    mirror_point = bound[roles.mirror_diode_load_instance]
    mirror_diode_node_v = validation.vdd_v - mirror_point.vgs_magnitude_v
    input_vds_v = _mean(
        [bound[instance].vds_magnitude_v for instance in roles.input_instances]
    )
    load_vsd_v = _mean(
        [bound[instance].vds_magnitude_v for instance in roles.load_instances]
    )

    return DifferentialPairTheoryRequest(
        id=policy.request_id,
        topology="nmos_differential_pair_pmos_current_mirror_load_with_tail_device",
        pdk_profile=validation.pdk_profile,
        vdd_v=validation.vdd_v,
        mirror_diode_node_v=mirror_diode_node_v,
        output_common_mode_v=output_common_mode_v,
        tail_node_v=tail_node_v,
        load_capacitance_f=policy.load_capacitance_f,
        sizing_margin_fraction=policy.sizing_margin_fraction,
        maximum_characterization_voltage_mismatch_v=(
            policy.maximum_characterization_voltage_mismatch_v
        ),
        input_width=policy.input_width,
        load_width=policy.load_width,
        tail_width=policy.tail_width,
        device_characterization=DeviceCharacterization(
            source=DeviceDataSource.PDK_CHARACTERIZATION,
            source_artifact_id=(
                f"{validation.policy_id}.heldout-validation"
            ),
            source_artifact_sha256=policy.expected_validation_sha256,
            process_corner=validation.process_corner,
            temperature_c=validation.temperature_c,
            input_nmos_vds_v=input_vds_v,
            load_pmos_vsd_v=load_vsd_v,
            tail_nmos_vds_v=tail_reference.vds_magnitude_v,
            nmos_input_points=input_points,
            pmos_load_points=load_points,
            nmos_tail_points=tail_points,
        ),
        constraints=policy.constraints,
        objective=policy.objective,
    )


__all__ = [
    "DifferentialPairTheoryDerivationPolicy",
    "DifferentialPairTheoryRoleMapping",
    "derive_differential_pair_theory_request",
]
