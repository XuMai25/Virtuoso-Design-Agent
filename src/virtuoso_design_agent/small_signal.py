"""Topology-independent linear small-signal network analysis."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .characterization import (
    DeviceDataSource,
    MosCharacterizationArtifact,
    MosPolarity,
    MosSmallSignalPoint,
)
from .models import EvidenceSource, RunStatus, StrictModel


_NODE_PATTERN = re.compile(r"^\S+$")
_NAME_PATTERN = r"^\S+$"


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ComplexValue(_FiniteStrictModel):
    real: float = 0.0
    imaginary: float = 0.0

    @model_validator(mode="after")
    def finite_parts(self) -> "ComplexValue":
        if not math.isfinite(self.real) or not math.isfinite(self.imaginary):
            raise ValueError("complex value must be finite")
        return self

    def as_complex(self) -> complex:
        return complex(self.real, self.imaginary)

    @classmethod
    def from_complex(cls, value: complex) -> "ComplexValue":
        return cls(real=value.real, imaginary=value.imag)


class BoundaryVoltage(_FiniteStrictModel):
    node: StrictStr = Field(min_length=1, max_length=256)
    voltage: ComplexValue


class LinearExpression(_FiniteStrictModel):
    terms: dict[str, float] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_terms(self) -> "LinearExpression":
        for node, coefficient in self.terms.items():
            if len(node) > 256 or not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"invalid node name {node}")
            if not math.isfinite(coefficient) or coefficient == 0.0:
                raise ValueError("linear-expression coefficients must be finite/nonzero")
        return self


class MosSmallSignalInstance(_FiniteStrictModel):
    name: StrictStr = Field(min_length=1, max_length=96, pattern=_NAME_PATTERN)
    model: StrictStr = Field(min_length=1, max_length=96, pattern=_NAME_PATTERN)
    point_id: StrictStr = Field(min_length=1, max_length=96)
    drain: StrictStr = Field(min_length=1, max_length=256)
    gate: StrictStr = Field(min_length=1, max_length=256)
    source: StrictStr = Field(min_length=1, max_length=256)
    bulk: StrictStr = Field(min_length=1, max_length=256)
    width_um: float = Field(gt=0.0)
    length_um: float = Field(gt=0.0)
    multiplicity: float = Field(default=1.0, gt=0.0)
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(ge=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)
    maximum_voltage_mismatch_v: float = Field(default=0.01, ge=0.0, le=0.5)


class ResistorSmallSignalInstance(_FiniteStrictModel):
    name: StrictStr = Field(min_length=1, max_length=96, pattern=_NAME_PATTERN)
    positive: StrictStr = Field(min_length=1, max_length=256)
    negative: StrictStr = Field(min_length=1, max_length=256)
    resistance_ohm: float = Field(gt=0.0)


class CapacitorSmallSignalInstance(_FiniteStrictModel):
    name: StrictStr = Field(min_length=1, max_length=96, pattern=_NAME_PATTERN)
    positive: StrictStr = Field(min_length=1, max_length=256)
    negative: StrictStr = Field(min_length=1, max_length=256)
    capacitance_f: float = Field(gt=0.0)


class SmallSignalNetworkRequest(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(min_length=1, max_length=96, pattern=_NAME_PATTERN)
    characterization: MosCharacterizationArtifact
    mosfets: list[MosSmallSignalInstance] = Field(min_length=1, max_length=1024)
    resistors: list[ResistorSmallSignalInstance] = Field(
        default_factory=list, max_length=4096
    )
    capacitors: list[CapacitorSmallSignalInstance] = Field(
        default_factory=list, max_length=4096
    )
    boundary_voltages: list[BoundaryVoltage] = Field(min_length=1, max_length=256)
    input_expression: LinearExpression
    output_expression: LinearExpression
    frequencies_hz: list[float] = Field(min_length=1, max_length=4096)
    reference_points: int = Field(default=1, ge=1, le=32)
    maximum_reference_variation_db: float = Field(default=0.5, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def validate_network(self) -> "SmallSignalNetworkRequest":
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

        component_names = [item.name for item in self.mosfets]
        component_names += [item.name for item in self.resistors]
        component_names += [item.name for item in self.capacitors]
        if len(component_names) != len(set(component_names)):
            raise ValueError("small-signal component names must be unique")

        boundary_nodes = [item.node for item in self.boundary_voltages]
        if "0" in boundary_nodes:
            raise ValueError("ground node 0 is implicit and must not be declared")
        if len(boundary_nodes) != len(set(boundary_nodes)):
            raise ValueError("boundary-voltage nodes must be unique")

        point_by_id = {point.id: point for point in self.characterization.points}
        circuit_nodes: set[str] = set()
        for instance in self.mosfets:
            point = point_by_id.get(instance.point_id)
            if point is None:
                raise ValueError(
                    f"MOS {instance.name} references unknown point {instance.point_id}"
                )
            if instance.model != point.model:
                raise ValueError(
                    f"MOS {instance.name} model does not match point {point.id}"
                )
            if instance.drain == instance.source:
                raise ValueError(f"MOS {instance.name} drain and source must differ")
            self._validate_nodes(
                instance.name,
                (instance.drain, instance.gate, instance.source, instance.bulk),
            )
            circuit_nodes.update(
                (instance.drain, instance.gate, instance.source, instance.bulk)
            )
            if not math.isclose(
                instance.length_um, point.length_um, rel_tol=1e-9, abs_tol=1e-12
            ):
                raise ValueError(
                    f"MOS {instance.name} length does not match point {point.id}"
                )
            for label, actual, characterized in (
                ("VGS", instance.vgs_magnitude_v, point.vgs_magnitude_v),
                ("VDS", instance.vds_magnitude_v, point.vds_magnitude_v),
                ("VSB", instance.vsb_magnitude_v, point.vsb_magnitude_v),
            ):
                if abs(actual - characterized) > (
                    instance.maximum_voltage_mismatch_v + 1e-12
                ):
                    raise ValueError(
                        f"MOS {instance.name} {label} does not match point {point.id}"
                    )

        for instance in [*self.resistors, *self.capacitors]:
            if instance.positive == instance.negative:
                raise ValueError(f"component {instance.name} connects one node twice")
            self._validate_nodes(
                instance.name, (instance.positive, instance.negative)
            )
            circuit_nodes.update((instance.positive, instance.negative))

        for boundary in self.boundary_voltages:
            self._validate_nodes("boundary", (boundary.node,))
            if boundary.node not in circuit_nodes:
                raise ValueError(f"boundary node {boundary.node} is not in the network")
        fixed_nodes = set(boundary_nodes) | {"0"}
        if not set(self.input_expression.terms).issubset(fixed_nodes):
            raise ValueError("input_expression may reference only fixed boundary nodes")
        if not set(self.output_expression.terms).issubset(circuit_nodes):
            raise ValueError("output_expression references a node outside the network")
        unknown_nodes = circuit_nodes - fixed_nodes
        if not unknown_nodes:
            raise ValueError("small-signal network has no unknown nodes")
        if not set(self.output_expression.terms).intersection(unknown_nodes):
            raise ValueError("output_expression must include at least one unknown node")
        return self

    @staticmethod
    def _validate_nodes(owner: str, nodes: tuple[str, ...]) -> None:
        for node in nodes:
            if not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"{owner} has invalid node name {node}")


class DerivedMosSmallSignal(_FiniteStrictModel):
    instance: str
    point_id: str
    polarity: MosPolarity
    drain_current_a: float = Field(gt=0.0)
    gm_s: float = Field(gt=0.0)
    gds_s: float = Field(ge=0.0)
    gmb_s: float = Field(ge=0.0)
    cgs_f: float = Field(ge=0.0)
    cgd_f: float = Field(ge=0.0)
    cgb_f: float = Field(ge=0.0)
    cdb_f: float = Field(ge=0.0)
    csb_f: float = Field(ge=0.0)
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class SmallSignalResponsePoint(_FiniteStrictModel):
    frequency_hz: float = Field(gt=0.0)
    node_voltages: dict[str, ComplexValue]
    input_value: ComplexValue
    output_value: ComplexValue
    transfer: ComplexValue
    magnitude_v_per_v: float = Field(ge=0.0)
    magnitude_db: float
    phase_deg: float
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class SmallSignalNetworkResult(_FiniteStrictModel):
    schema_version: Literal[1] = 1
    request_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RunStatus
    topology_independent_core: Literal[True] = True
    characterization_id: str
    characterization_source: DeviceDataSource
    pdk_profile: str
    process_corner: str
    temperature_c: float
    characterization_artifact_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    characterization_raw_data_evidence_source: EvidenceSource
    characterization_point_evidence_source: EvidenceSource
    derived_metric_evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )
    derived_mos_values: list[DerivedMosSmallSignal]
    points: list[SmallSignalResponsePoint]
    reference_status: Literal["flat", "not_flat"]
    reference_variation_db: float = Field(ge=0.0)
    low_frequency_gain_v_per_v: float = Field(ge=0.0)
    low_frequency_gain_db: float
    low_frequency_phase_deg: float
    bandwidth_status: Literal["resolved", "unresolved"]
    bandwidth_3db_hz: float | None = Field(default=None, gt=0.0)
    equations: list[str]
    assumptions: list[str]
    warnings: list[str]


def _derived_mos(
    instance: MosSmallSignalInstance, point: MosSmallSignalPoint
) -> DerivedMosSmallSignal:
    scale_um = instance.width_um * instance.multiplicity
    current = point.drain_current_density_a_per_um * scale_um
    return DerivedMosSmallSignal(
        instance=instance.name,
        point_id=point.id,
        polarity=point.polarity,
        drain_current_a=current,
        gm_s=point.gm_over_id_per_v * current,
        gds_s=point.gds_over_id_per_v * current,
        gmb_s=point.gmb_over_id_per_v * current,
        cgs_f=point.cgs_f_per_um * scale_um,
        cgd_f=point.cgd_f_per_um * scale_um,
        cgb_f=point.cgb_f_per_um * scale_um,
        cdb_f=point.cdb_f_per_um * scale_um,
        csb_f=point.csb_f_per_um * scale_um,
    )


def _add(
    matrix: list[list[complex]],
    indexes: dict[str, int],
    row: str,
    column: str,
    value: complex,
) -> None:
    if row != "0" and column != "0":
        matrix[indexes[row]][indexes[column]] += value


def _stamp_admittance(
    matrix: list[list[complex]],
    indexes: dict[str, int],
    positive: str,
    negative: str,
    admittance: complex,
) -> None:
    _add(matrix, indexes, positive, positive, admittance)
    _add(matrix, indexes, negative, negative, admittance)
    _add(matrix, indexes, positive, negative, -admittance)
    _add(matrix, indexes, negative, positive, -admittance)


def _stamp_mos(
    matrix: list[list[complex]],
    indexes: dict[str, int],
    instance: MosSmallSignalInstance,
    values: DerivedMosSmallSignal,
    omega: float,
) -> None:
    # gm/gmb are positive normal-mode derivatives for the declared physical
    # drain/gate/source/bulk ordering for both NMOS and PMOS.
    signed_gm = values.gm_s
    signed_gmb = values.gmb_s
    drain, gate, source, bulk = (
        instance.drain,
        instance.gate,
        instance.source,
        instance.bulk,
    )
    _add(matrix, indexes, drain, drain, values.gds_s)
    _add(matrix, indexes, drain, source, -values.gds_s - signed_gm - signed_gmb)
    _add(matrix, indexes, drain, gate, signed_gm)
    _add(matrix, indexes, drain, bulk, signed_gmb)
    _add(matrix, indexes, source, drain, -values.gds_s)
    _add(matrix, indexes, source, source, values.gds_s + signed_gm + signed_gmb)
    _add(matrix, indexes, source, gate, -signed_gm)
    _add(matrix, indexes, source, bulk, -signed_gmb)
    for first, second, capacitance in (
        (gate, source, values.cgs_f),
        (gate, drain, values.cgd_f),
        (gate, bulk, values.cgb_f),
        (drain, bulk, values.cdb_f),
        (source, bulk, values.csb_f),
    ):
        if capacitance > 0.0:
            _stamp_admittance(
                matrix, indexes, first, second, complex(0.0, omega * capacitance)
            )


def _solve(matrix: list[list[complex]], right_hand_side: list[complex]) -> list[complex]:
    size = len(right_hand_side)
    augmented = [row[:] + [right_hand_side[index]] for index, row in enumerate(matrix)]
    scale = max((abs(value) for row in matrix for value in row), default=0.0)
    threshold = max(scale, 1e-30) * 1e-12
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= threshold:
            raise ValueError("small-signal network matrix is singular or ill-conditioned")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    result = [0j] * size
    for row in range(size - 1, -1, -1):
        remainder = augmented[row][size] - sum(
            augmented[row][column] * result[column]
            for column in range(row + 1, size)
        )
        result[row] = remainder / augmented[row][row]
    return result


def _expression_value(expression: LinearExpression, voltages: dict[str, complex]) -> complex:
    return sum(
        coefficient * voltages.get(node, 0j)
        for node, coefficient in expression.terms.items()
    )


def _bandwidth(
    points: list[SmallSignalResponsePoint], reference_db: float
) -> float | None:
    threshold_db = reference_db - 10.0 * math.log10(2.0)
    for left, right in zip(points, points[1:]):
        if left.magnitude_db >= threshold_db >= right.magnitude_db:
            if math.isclose(left.magnitude_db, right.magnitude_db):
                return left.frequency_hz
            fraction = (threshold_db - left.magnitude_db) / (
                right.magnitude_db - left.magnitude_db
            )
            return 10.0 ** (
                math.log10(left.frequency_hz)
                + fraction
                * (math.log10(right.frequency_hz) - math.log10(left.frequency_hz))
            )
    return None


def analyze_small_signal_network(
    request: SmallSignalNetworkRequest,
) -> SmallSignalNetworkResult:
    """Solve a MOS/resistor/capacitor network without topology-specific equations."""

    canonical_request = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    point_by_id = {point.id: point for point in request.characterization.points}
    derived_by_name = {
        instance.name: _derived_mos(instance, point_by_id[instance.point_id])
        for instance in request.mosfets
    }
    circuit_nodes: set[str] = set()
    for instance in request.mosfets:
        circuit_nodes.update(
            (instance.drain, instance.gate, instance.source, instance.bulk)
        )
    for instance in [*request.resistors, *request.capacitors]:
        circuit_nodes.update((instance.positive, instance.negative))
    nodes = sorted(circuit_nodes - {"0"})
    indexes = {node: index for index, node in enumerate(nodes)}
    fixed_voltages = {
        item.node: item.voltage.as_complex() for item in request.boundary_voltages
    }
    fixed_voltages["0"] = 0j
    unknown_nodes = [node for node in nodes if node not in fixed_voltages]
    unknown_indexes = [indexes[node] for node in unknown_nodes]
    fixed_nodes = [node for node in nodes if node in fixed_voltages]
    fixed_indexes = [indexes[node] for node in fixed_nodes]
    response_points: list[SmallSignalResponsePoint] = []

    for frequency_hz in request.frequencies_hz:
        matrix = [[0j for _ in nodes] for _ in nodes]
        omega = 2.0 * math.pi * frequency_hz
        for resistor in request.resistors:
            _stamp_admittance(
                matrix,
                indexes,
                resistor.positive,
                resistor.negative,
                1.0 / resistor.resistance_ohm,
            )
        for capacitor in request.capacitors:
            _stamp_admittance(
                matrix,
                indexes,
                capacitor.positive,
                capacitor.negative,
                complex(0.0, omega * capacitor.capacitance_f),
            )
        for mosfet in request.mosfets:
            _stamp_mos(
                matrix,
                indexes,
                mosfet,
                derived_by_name[mosfet.name],
                omega,
            )

        unknown_matrix = [
            [matrix[row][column] for column in unknown_indexes]
            for row in unknown_indexes
        ]
        right_hand_side = [
            -sum(
                matrix[row][column] * fixed_voltages[node]
                for node, column in zip(fixed_nodes, fixed_indexes)
            )
            for row in unknown_indexes
        ]
        try:
            solution = _solve(unknown_matrix, right_hand_side)
        except ValueError as exc:
            raise ValueError(f"{exc} at {frequency_hz:.12g} Hz") from exc
        voltages = dict(fixed_voltages)
        voltages.update(dict(zip(unknown_nodes, solution)))
        input_value = _expression_value(request.input_expression, voltages)
        if abs(input_value) <= 1e-30:
            raise ValueError("input_expression evaluates to zero")
        output_value = _expression_value(request.output_expression, voltages)
        transfer = output_value / input_value
        magnitude = abs(transfer)
        magnitude_db = 20.0 * math.log10(max(magnitude, 1e-300))
        response_points.append(
            SmallSignalResponsePoint(
                frequency_hz=frequency_hz,
                node_voltages={
                    node: ComplexValue.from_complex(voltages[node])
                    for node in sorted(voltages)
                },
                input_value=ComplexValue.from_complex(input_value),
                output_value=ComplexValue.from_complex(output_value),
                transfer=ComplexValue.from_complex(transfer),
                magnitude_v_per_v=magnitude,
                magnitude_db=magnitude_db,
                phase_deg=math.degrees(math.atan2(transfer.imag, transfer.real)),
            )
        )

    reference_transfers = [
        point.transfer.as_complex()
        for point in response_points[: request.reference_points]
    ]
    reference_transfer = sum(reference_transfers) / len(reference_transfers)
    reference_magnitude = abs(reference_transfer)
    if reference_magnitude <= 0.0:
        raise ValueError("low-frequency reference transfer is zero")
    reference_db = 20.0 * math.log10(reference_magnitude)
    reference_variation = max(
        abs(point.magnitude_db - reference_db)
        for point in response_points[: request.reference_points]
    )
    reference_status = (
        "flat"
        if reference_variation <= request.maximum_reference_variation_db
        else "not_flat"
    )
    bandwidth = (
        _bandwidth(response_points, reference_db)
        if reference_status == "flat"
        else None
    )
    warnings = [
        "This linearized network does not solve the nonlinear DC operating point; "
        "each MOS instance must bind a matching characterization point.",
        "Width/multiplicity scaling is linear; finger topology, narrow-width and "
        "layout-dependent effects require separately characterized points.",
        "A solved network is software_inference and still requires EDA validation.",
    ]
    if request.characterization.source is DeviceDataSource.SYNTHETIC_EXAMPLE:
        warnings.append(
            "Synthetic characterization is only for software-path verification."
        )
    if reference_status == "not_flat":
        warnings.append(
            "The declared low-frequency reference is not flat; reference-derived "
            "metrics must not seed sizing."
        )
    return SmallSignalNetworkResult(
        request_id=request.id,
        request_sha256=hashlib.sha256(canonical_request).hexdigest(),
        status=(
            RunStatus.SUCCEEDED
            if reference_status == "flat"
            else RunStatus.PARTIAL
        ),
        characterization_id=request.characterization.id,
        characterization_source=request.characterization.source,
        pdk_profile=request.characterization.pdk_profile,
        process_corner=request.characterization.process_corner,
        temperature_c=request.characterization.temperature_c,
        characterization_artifact_sha256=(
            request.characterization.source_artifact_sha256
        ),
        characterization_raw_data_evidence_source=(
            request.characterization.raw_data_evidence_source
        ),
        characterization_point_evidence_source=(
            request.characterization.normalized_point_evidence_source
        ),
        derived_mos_values=list(derived_by_name.values()),
        points=response_points,
        reference_status=reference_status,
        reference_variation_db=reference_variation,
        low_frequency_gain_v_per_v=reference_magnitude,
        low_frequency_gain_db=reference_db,
        low_frequency_phase_deg=math.degrees(
            math.atan2(reference_transfer.imag, reference_transfer.real)
        ),
        bandwidth_status="resolved" if bandwidth is not None else "unresolved",
        bandwidth_3db_hz=bandwidth,
        equations=[
            "Id = (Id/W) * W * multiplicity",
            "gm = (gm/Id) * Id; gds = (gds/Id) * Id",
            "Y(f) * V(f) = 0 with fixed boundary-node voltages",
            "transfer(f) = output_expression(V) / input_expression(V)",
        ],
        assumptions=[
            "MOS parameters are linearized at the declared characterization bias.",
            "Each MOS uses the physical drain/source order of its normal-mode point.",
            "Independent supplies and bias sources are represented as fixed AC nodes.",
            "The network contains MOS, resistor, and capacitor small-signal stamps only.",
        ],
        warnings=warnings,
    )
