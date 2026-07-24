"""Topology-independent device-characterization contracts."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .models import (
    DeviceCharacterizationSourceBinding,
    DeviceCharacterizationSpec,
    EvidenceSource,
    StrictModel,
    TaskSpec,
)


class DeviceDataSource(str, Enum):
    """Declared origin of device values supplied to a theory calculation."""

    PDK_CHARACTERIZATION = "pdk_characterization"
    EDA_OPERATING_POINT = "eda_operating_point"
    SYNTHETIC_EXAMPLE = "synthetic_example"


class MosPolarity(str, Enum):
    NMOS = "nmos"
    PMOS = "pmos"


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MosSmallSignalPoint(_FiniteStrictModel):
    """One width-normalized MOS operating point, independent of circuit role."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    model: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$.-]*$",
    )
    polarity: MosPolarity
    length_um: float = Field(gt=0.0)
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(ge=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)
    vdsat_magnitude_v: float = Field(gt=0.0)
    drain_current_density_a_per_um: float = Field(gt=0.0)
    gm_over_id_per_v: float = Field(gt=0.0)
    gds_over_id_per_v: float = Field(ge=0.0)
    gmb_over_id_per_v: float = Field(default=0.0, ge=0.0)
    cgs_f_per_um: float = Field(default=0.0, ge=0.0)
    cgd_f_per_um: float = Field(default=0.0, ge=0.0)
    cgb_f_per_um: float = Field(default=0.0, ge=0.0)
    cdb_f_per_um: float = Field(default=0.0, ge=0.0)
    csb_f_per_um: float = Field(default=0.0, ge=0.0)


class MosCharacterizationArtifact(_FiniteStrictModel):
    """Finite MOS data set plus the conditions and evidence that define it."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    source: DeviceDataSource
    source_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    process_corner: StrictStr = Field(min_length=1, max_length=64)
    temperature_c: float = Field(ge=-273.15, le=300.0)
    characterized_width_um: float | None = Field(default=None, gt=0.0)
    model_parameters_by_polarity: dict[
        Literal["nmos", "pmos"], dict[StrictStr, StrictStr]
    ] = Field(default_factory=dict)
    source_instance_binding: DeviceCharacterizationSourceBinding | None = None
    raw_data_evidence_source: EvidenceSource
    normalized_point_evidence_source: EvidenceSource
    points: list[MosSmallSignalPoint] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_source_and_points(self) -> "MosCharacterizationArtifact":
        if self.source is DeviceDataSource.SYNTHETIC_EXAMPLE:
            if self.source_artifact_sha256 is not None:
                raise ValueError("synthetic characterization cannot claim an artifact hash")
            if (
                self.raw_data_evidence_source is not EvidenceSource.USER_INPUT
                or self.normalized_point_evidence_source
                is not EvidenceSource.USER_INPUT
            ):
                raise ValueError("synthetic characterization data must be user_input")
        else:
            if self.source_artifact_sha256 is None:
                raise ValueError(
                    "PDK/EDA characterization requires source_artifact_sha256"
                )
            if self.raw_data_evidence_source is not EvidenceSource.EDA_RESULT:
                raise ValueError("PDK/EDA raw characterization must be eda_result")
            if (
                self.normalized_point_evidence_source
                is not EvidenceSource.SOFTWARE_INFERENCE
            ):
                raise ValueError(
                    "normalized PDK/EDA characterization points must be "
                    "software_inference"
                )
        point_ids = [point.id for point in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("characterization contains duplicate point ids")
        return self


class MosInterpolationCorner(_FiniteStrictModel):
    point_id: StrictStr = Field(min_length=1, max_length=96)
    weight: float = Field(gt=0.0, le=1.0)
    length_um: float = Field(gt=0.0)
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(ge=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)


class MosInterpolationResult(_FiniteStrictModel):
    point: MosSmallSignalPoint
    method: Literal["rectilinear_linear_exact_length"] = (
        "rectilinear_linear_exact_length"
    )
    corners: list[MosInterpolationCorner] = Field(min_length=1, max_length=8)
    source_artifact_id: str
    source_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_characterized_width_um: float | None = Field(default=None, gt=0.0)
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


_RAW_QUANTITIES = (
    "ids_a",
    "vgs_v",
    "vds_v",
    "vbs_v",
    "vdsat_v",
    "gm_s",
    "gds_s",
    "gmb_s",
    "cgs_f",
    "cgd_f",
    "cgb_f",
    "cdb_f",
    "csb_f",
)

_INTERPOLATED_QUANTITIES = (
    "vdsat_magnitude_v",
    "drain_current_density_a_per_um",
    "gm_over_id_per_v",
    "gds_over_id_per_v",
    "gmb_over_id_per_v",
    "cgs_f_per_um",
    "cgd_f_per_um",
    "cgb_f_per_um",
    "cdb_f_per_um",
    "csb_f_per_um",
)

_ERROR_FLOORS = {
    "vdsat_magnitude_v": 1e-3,
    "drain_current_density_a_per_um": 1e-9,
    "gm_over_id_per_v": 1e-3,
    "gds_over_id_per_v": 1e-4,
    "gmb_over_id_per_v": 1e-4,
    "cgs_f_per_um": 1e-17,
    "cgd_f_per_um": 1e-17,
    "cgb_f_per_um": 1e-17,
    "cdb_f_per_um": 1e-17,
    "csb_f_per_um": 1e-17,
}


def enumerate_mos_characterization_points(
    settings: DeviceCharacterizationSpec,
) -> list[dict[str, Any]]:
    """Expand the declared grid and holdouts into stable simulation identities."""

    points: list[dict[str, Any]] = []
    for polarity_index, polarity in enumerate(settings.polarities):
        for length_index, length_um in enumerate(settings.lengths_um):
            for vgs_index, vgs_v in enumerate(settings.vgs_magnitudes_v):
                for vds_index, vds_v in enumerate(settings.vds_magnitudes_v):
                    for vsb_index, vsb_v in enumerate(settings.vsb_magnitudes_v):
                        points.append(
                            {
                                "id": (
                                    f"tr-{polarity_index}-{length_index}-"
                                    f"{vgs_index}-{vds_index}-{vsb_index}"
                                ),
                                "set": "training",
                                "polarity": polarity,
                                "length_um": float(length_um),
                                "vgs_magnitude_v": float(vgs_v),
                                "vds_magnitude_v": float(vds_v),
                                "vsb_magnitude_v": float(vsb_v),
                            }
                        )
    for index, holdout in enumerate(settings.holdout_points, start=1):
        points.append(
            {
                "id": f"ho-{index:03d}-{holdout.polarity}",
                "set": "holdout",
                **holdout.model_dump(mode="json"),
            }
        )
    return points


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"MOS characterization {label} is not numeric") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"MOS characterization {label} is not finite")
    return number


def _matches(left: float, right: float, *, tolerance: float = 1e-6) -> bool:
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


def _normalized_point(
    declared: dict[str, Any],
    returned: dict[str, Any],
    *,
    width_um: float,
) -> MosSmallSignalPoint:
    for name in (
        "id",
        "set",
        "polarity",
        "length_um",
        "vgs_magnitude_v",
        "vds_magnitude_v",
        "vsb_magnitude_v",
    ):
        if returned.get(name) != declared[name]:
            raise RuntimeError(
                f"MOS characterization point {declared['id']} field {name} "
                "does not match the declared request"
            )
    raw = returned.get("raw")
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"MOS characterization point {declared['id']} has no raw OP values"
        )
    values = {
        name: _finite_float(raw.get(name), f"{declared['id']}.{name}")
        for name in _RAW_QUANTITIES
    }
    polarity = MosPolarity(str(declared["polarity"]))
    sign = 1.0 if polarity is MosPolarity.NMOS else -1.0
    bias_checks = {
        "vgs_v": sign * float(declared["vgs_magnitude_v"]),
        "vds_v": sign * float(declared["vds_magnitude_v"]),
        "vbs_v": -sign * float(declared["vsb_magnitude_v"]),
    }
    for name, expected in bias_checks.items():
        if not _matches(values[name], expected):
            raise RuntimeError(
                f"MOS characterization {declared['id']} measured {name}="
                f"{values[name]!r}, expected {expected!r}"
            )
    if values["ids_a"] * sign <= 0.0:
        raise RuntimeError(
            f"MOS characterization {declared['id']} drain-current sign is invalid"
        )
    for name in ("gm_s", "gds_s", "gmb_s"):
        if values[name] < 0.0:
            raise RuntimeError(
                f"MOS characterization {declared['id']} {name} is negative"
            )
    current = abs(values["ids_a"])
    if current <= 0.0:
        raise RuntimeError(
            f"MOS characterization {declared['id']} has zero drain current"
        )
    if values["gm_s"] <= 0.0:
        raise RuntimeError(f"MOS characterization {declared['id']} has zero gm")
    model = returned.get("model")
    if not isinstance(model, str) or not model:
        raise RuntimeError(
            f"MOS characterization point {declared['id']} has no model identity"
        )
    return MosSmallSignalPoint(
        id=str(declared["id"]),
        model=model,
        polarity=polarity,
        length_um=float(declared["length_um"]),
        vgs_magnitude_v=float(declared["vgs_magnitude_v"]),
        vds_magnitude_v=float(declared["vds_magnitude_v"]),
        vsb_magnitude_v=float(declared["vsb_magnitude_v"]),
        vdsat_magnitude_v=abs(values["vdsat_v"]),
        drain_current_density_a_per_um=current / width_um,
        gm_over_id_per_v=values["gm_s"] / current,
        gds_over_id_per_v=values["gds_s"] / current,
        gmb_over_id_per_v=values["gmb_s"] / current,
        cgs_f_per_um=abs(values["cgs_f"]) / width_um,
        cgd_f_per_um=abs(values["cgd_f"]) / width_um,
        cgb_f_per_um=abs(values["cgb_f"]) / width_um,
        cdb_f_per_um=abs(values["cdb_f"]) / width_um,
        csb_f_per_um=abs(values["csb_f"]) / width_um,
    )


def _axis_bracket(values: list[float], target: float) -> tuple[tuple[float, float], ...]:
    ordered = sorted(values)
    for value in ordered:
        if _matches(value, target, tolerance=1e-12):
            return ((value, 1.0),)
    lower = max((value for value in ordered if value < target), default=None)
    upper = min((value for value in ordered if value > target), default=None)
    if lower is None or upper is None:
        raise RuntimeError(f"holdout value {target} is not bracketed by training data")
    span = upper - lower
    return ((lower, (upper - target) / span), (upper, (target - lower) / span))


def interpolate_mos_characterization_point(
    artifact: MosCharacterizationArtifact,
    *,
    point_id: str,
    model: str,
    polarity: MosPolarity | str,
    length_um: float,
    vgs_magnitude_v: float,
    vds_magnitude_v: float,
    vsb_magnitude_v: float,
) -> MosInterpolationResult:
    """Interpolate one bias point without crossing or extrapolating length planes."""

    requested_polarity = MosPolarity(polarity)
    model_points = [
        point
        for point in artifact.points
        if point.model == model and point.polarity is requested_polarity
    ]
    if not model_points:
        raise RuntimeError(
            f"characterization has no {requested_polarity.value} model {model}"
        )
    exact_lengths = sorted(
        {
            point.length_um
            for point in model_points
            if _matches(point.length_um, length_um, tolerance=1e-12)
        }
    )
    if len(exact_lengths) != 1:
        available = sorted({point.length_um for point in model_points})
        raise RuntimeError(
            f"length {length_um}um has no exact characterized plane for {model}; "
            f"available={available}; length interpolation and extrapolation are disabled"
        )
    exact_length = exact_lengths[0]
    plane = [
        point
        for point in model_points
        if _matches(point.length_um, exact_length, tolerance=1e-12)
    ]
    axes = (
        _axis_bracket(
            sorted({point.vgs_magnitude_v for point in plane}),
            vgs_magnitude_v,
        ),
        _axis_bracket(
            sorted({point.vds_magnitude_v for point in plane}),
            vds_magnitude_v,
        ),
        _axis_bracket(
            sorted({point.vsb_magnitude_v for point in plane}),
            vsb_magnitude_v,
        ),
    )
    lookup = {
        (
            point.vgs_magnitude_v,
            point.vds_magnitude_v,
            point.vsb_magnitude_v,
        ): point
        for point in plane
    }
    predicted = {name: 0.0 for name in _INTERPOLATED_QUANTITIES}
    corners: list[MosInterpolationCorner] = []
    total_weight = 0.0
    for corner in itertools.product(*axes):
        coordinates = tuple(item[0] for item in corner)
        weight = math.prod(item[1] for item in corner)
        source = lookup.get(coordinates)
        if source is None:
            raise RuntimeError(
                f"characterization is missing interpolation corner {coordinates} "
                f"for {model} at L={exact_length}um"
            )
        total_weight += weight
        corners.append(
            MosInterpolationCorner(
                point_id=source.id,
                weight=weight,
                length_um=source.length_um,
                vgs_magnitude_v=source.vgs_magnitude_v,
                vds_magnitude_v=source.vds_magnitude_v,
                vsb_magnitude_v=source.vsb_magnitude_v,
            )
        )
        for name in predicted:
            predicted[name] += weight * float(getattr(source, name))
    if not _matches(total_weight, 1.0, tolerance=1e-9):
        raise RuntimeError(
            f"interpolation weights for {point_id} sum to {total_weight}"
        )
    return MosInterpolationResult(
        point=MosSmallSignalPoint(
            id=point_id,
            model=model,
            polarity=requested_polarity,
            length_um=exact_length,
            vgs_magnitude_v=vgs_magnitude_v,
            vds_magnitude_v=vds_magnitude_v,
            vsb_magnitude_v=vsb_magnitude_v,
            **predicted,
        ),
        corners=corners,
        source_artifact_id=artifact.id,
        source_artifact_sha256=artifact.source_artifact_sha256,
        source_characterized_width_um=artifact.characterized_width_um,
    )


def _interpolate_holdout(
    settings: DeviceCharacterizationSpec,
    training: list[MosSmallSignalPoint],
    holdout: MosSmallSignalPoint,
) -> dict[str, float]:
    axes = (
        _axis_bracket(settings.lengths_um, holdout.length_um),
        _axis_bracket(settings.vgs_magnitudes_v, holdout.vgs_magnitude_v),
        _axis_bracket(settings.vds_magnitudes_v, holdout.vds_magnitude_v),
        _axis_bracket(settings.vsb_magnitudes_v, holdout.vsb_magnitude_v),
    )
    lookup = {
        (
            point.polarity,
            point.length_um,
            point.vgs_magnitude_v,
            point.vds_magnitude_v,
            point.vsb_magnitude_v,
        ): point
        for point in training
    }
    predicted = {name: 0.0 for name in _INTERPOLATED_QUANTITIES}
    total_weight = 0.0
    for corner in itertools.product(*axes):
        coordinates = tuple(item[0] for item in corner)
        weight = math.prod(item[1] for item in corner)
        point = lookup.get((holdout.polarity, *coordinates))
        if point is None:
            raise RuntimeError(
                f"holdout {holdout.id} is missing interpolation corner {coordinates}"
            )
        total_weight += weight
        for name in predicted:
            predicted[name] += weight * float(getattr(point, name))
    if not _matches(total_weight, 1.0, tolerance=1e-9):
        raise RuntimeError(
            f"holdout {holdout.id} interpolation weights sum to {total_weight}"
        )
    return predicted


def _validate_manifest(raw_data: dict[str, Any]) -> str:
    evidence = raw_data.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("MOS characterization result has no evidence bundle")
    manifest = evidence.get("artifact_manifest")
    if not isinstance(manifest, list) or not manifest:
        raise RuntimeError("MOS characterization artifact manifest is empty")
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in manifest:
        if not isinstance(item, dict):
            raise RuntimeError("MOS characterization manifest entry is invalid")
        path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in _path_parts(path)
            or path in paths
        ):
            raise RuntimeError("MOS characterization manifest path is invalid")
        if not isinstance(size, int) or size < 0:
            raise RuntimeError(
                f"MOS characterization manifest {path} has an invalid size"
            )
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise RuntimeError(
                f"MOS characterization manifest {path} has an invalid SHA-256"
            )
        paths.add(path)
        normalized.append({"path": path, "size_bytes": size, "sha256": digest})
    required_suffixes = (".scs", "dcOp.dc", "dcOpInfo.info", "spectre.out")
    for suffix in required_suffixes:
        if not any(
            item["path"].endswith(suffix) and item["size_bytes"] > 0
            for item in normalized
        ):
            raise RuntimeError(
                f"MOS characterization manifest is missing a nonempty {suffix} artifact"
            )
    canonical = json.dumps(
        sorted(normalized, key=lambda item: item["path"]),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    calculated = hashlib.sha256(canonical).hexdigest()
    if evidence.get("manifest_sha256") != calculated:
        raise RuntimeError("MOS characterization manifest fingerprint does not match")
    remote_root = evidence.get("remote_run_root")
    remote_dir = evidence.get("remote_simulation_dir")
    if (
        not isinstance(remote_root, str)
        or not remote_root.startswith("/data/xum/")
        or not isinstance(remote_dir, str)
        or not remote_dir.startswith(remote_root.rstrip("/") + "/")
    ):
        raise RuntimeError("MOS characterization remote artifact path is invalid")
    return calculated


def _path_parts(path: str) -> tuple[str, ...]:
    """Return portable manifest components without interpreting a host path."""

    return tuple(part for part in path.replace("\\", "/").split("/") if part)


def normalize_mos_characterization(
    task: TaskSpec,
    raw_data: dict[str, Any],
) -> dict[str, Any]:
    """Validate raw EDA output and build the reusable MOS table plus holdout audit."""

    settings = task.device_characterization
    if settings is None:
        raise RuntimeError("device characterization settings are missing")
    from .profiles import load_pdk_profile

    profile = load_pdk_profile(task.pdk_profile)
    expected_header = {
        "task_id": task.id,
        "pdk_profile": profile.name,
        "process_corner": profile.model_section,
        "temperature_c": settings.temperature_c,
        "width_um": settings.width_um,
        "model_parameters_by_polarity": settings.model_parameters_by_polarity,
        "source_instance_binding": (
            settings.source_instance_binding.model_dump(mode="json")
            if settings.source_instance_binding is not None
            else None
        ),
        "raw_point_evidence_source": EvidenceSource.EDA_RESULT.value,
    }
    for name, expected in expected_header.items():
        actual = raw_data.get(name)
        if isinstance(expected, float):
            if not _matches(_finite_float(actual, name), expected):
                raise RuntimeError(
                    f"MOS characterization header {name} does not match the task"
                )
        elif actual != expected:
            raise RuntimeError(
                f"MOS characterization header {name} does not match the task"
            )
    tool_version = raw_data.get("tool_version")
    if not isinstance(tool_version, str) or not tool_version.strip():
        raise RuntimeError("MOS characterization result has no Spectre tool version")
    manifest_sha256 = _validate_manifest(raw_data)
    declared = enumerate_mos_characterization_points(settings)
    returned = raw_data.get("points")
    if not isinstance(returned, list) or not returned:
        raise RuntimeError("MOS characterization returned no operating points")
    if len(returned) != len(declared):
        raise RuntimeError(
            "MOS characterization point count does not match the declared grid"
        )
    returned_by_id: dict[str, dict[str, Any]] = {}
    for point in returned:
        if not isinstance(point, dict) or not isinstance(point.get("id"), str):
            raise RuntimeError("MOS characterization returned an invalid point")
        if point["id"] in returned_by_id:
            raise RuntimeError("MOS characterization returned duplicate point ids")
        returned_by_id[point["id"]] = point
    if set(returned_by_id) != {str(point["id"]) for point in declared}:
        raise RuntimeError("MOS characterization point identities do not match the task")

    normalized = [
        (
            item["set"],
            _normalized_point(
                item,
                returned_by_id[str(item["id"])],
                width_um=settings.width_um,
            ),
        )
        for item in declared
    ]
    training = [point for point_set, point in normalized if point_set == "training"]
    holdouts = [point for point_set, point in normalized if point_set == "holdout"]
    artifact = MosCharacterizationArtifact(
        id=f"{task.id}-table",
        source=DeviceDataSource.PDK_CHARACTERIZATION,
        source_artifact_sha256=manifest_sha256,
        pdk_profile=profile.name,
        process_corner=profile.model_section,
        temperature_c=settings.temperature_c,
        characterized_width_um=settings.width_um,
        model_parameters_by_polarity=settings.model_parameters_by_polarity,
        source_instance_binding=settings.source_instance_binding,
        raw_data_evidence_source=EvidenceSource.EDA_RESULT,
        normalized_point_evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        points=training,
    )

    audits: list[dict[str, Any]] = []
    for holdout in holdouts:
        predicted = _interpolate_holdout(settings, training, holdout)
        absolute_errors: dict[str, float] = {}
        normalized_errors: dict[str, float] = {}
        for name, predicted_value in predicted.items():
            actual_value = float(getattr(holdout, name))
            absolute_errors[name] = abs(predicted_value - actual_value)
            normalized_errors[name] = absolute_errors[name] / max(
                abs(predicted_value),
                abs(actual_value),
                _ERROR_FLOORS[name],
            )
        maximum_error = max(normalized_errors.values())
        audits.append(
            {
                "id": holdout.id,
                "polarity": holdout.polarity.value,
                "predicted": predicted,
                "actual": {
                    name: float(getattr(holdout, name))
                    for name in _INTERPOLATED_QUANTITIES
                },
                "absolute_errors": absolute_errors,
                "normalization_floors": dict(_ERROR_FLOORS),
                "normalized_errors": normalized_errors,
                "maximum_normalized_error": maximum_error,
                "passed": maximum_error
                <= settings.maximum_holdout_normalized_error,
                "evidence_source": EvidenceSource.SOFTWARE_INFERENCE.value,
                "actual_point_evidence_source": EvidenceSource.EDA_RESULT.value,
            }
        )
    gate_passed = all(item["passed"] for item in audits)
    return {
        "artifact": artifact.model_dump(mode="json"),
        "point_counts": {
            "training": len(training),
            "holdout": len(holdouts),
            "total": len(normalized),
        },
        "bias_and_sign_consistency": "matched",
        "manifest_consistency": "matched",
        "holdout_audit": {
            "threshold": settings.maximum_holdout_normalized_error,
            "error_definition": (
                "abs(predicted-actual) / max(abs(predicted), abs(actual), "
                "metric_normalization_floor)"
            ),
            "passed": gate_passed,
            "points": audits,
        },
        "raw_data_evidence_source": EvidenceSource.EDA_RESULT.value,
        "normalization_evidence_source": EvidenceSource.SOFTWARE_INFERENCE.value,
    }
