"""Calibrate the Gate-6 one-pole theory model from bound EDA run records."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .models import EvidenceSource, RunStatus, StrictModel


_TOPOLOGY = "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
_FIXED_PARAMETER_NAMES = (
    "length_um",
    "pmos_load_length_um",
    "tail_width_um",
    "tail_length_um",
    "tail_bias_v",
    "common_mode_v",
    "vdd_v",
    "load_ff",
)
_ACTUAL_METRICS = (
    "differential_low_frequency_gain_v_per_v",
    "differential_bandwidth_3db_hz",
    "differential_gain_bandwidth_product_hz",
)


class CalibrationObservation(StrictModel):
    source_run_role: Literal["training", "validation"]
    candidate_index: int = Field(ge=1)
    input_width_um: float = Field(gt=0.0)
    load_width_um: float = Field(gt=0.0)
    branch_current_a: float = Field(gt=0.0)
    input_gm_s: float = Field(gt=0.0)
    input_gds_s: float = Field(gt=0.0)
    load_gds_s: float = Field(gt=0.0)
    raw_gain_v_per_v: float = Field(gt=0.0)
    inferred_effective_output_capacitance_f: float = Field(gt=0.0)
    actual_gain_v_per_v: float = Field(gt=0.0)
    actual_bandwidth_hz: float = Field(gt=0.0)
    actual_gbw_hz: float = Field(gt=0.0)
    netlist_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wrapper_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operating_point_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_source: Literal[EvidenceSource.EDA_RESULT] = EvidenceSource.EDA_RESULT


class CalibrationCoefficients(StrictModel):
    differential_gain_correction_factor: float = Field(gt=0.0)
    input_effective_output_capacitance_f_per_um: float = Field(gt=0.0)
    load_effective_output_capacitance_f_per_um: float = Field(gt=0.0)
    external_load_capacitance_f: float = Field(gt=0.0)
    fit_method: Literal[
        "mean_gain_ratio_and_two_parameter_unconstrained_least_squares"
    ] = "mean_gain_ratio_and_two_parameter_unconstrained_least_squares"
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class CalibrationPrediction(StrictModel):
    source_run_role: Literal["training_leave_one_out", "independent_validation"]
    candidate_index: int = Field(ge=1)
    input_width_um: float = Field(gt=0.0)
    load_width_um: float = Field(gt=0.0)
    predicted_gain_v_per_v: float = Field(gt=0.0)
    predicted_bandwidth_hz: float = Field(gt=0.0)
    predicted_gbw_hz: float = Field(gt=0.0)
    actual_gain_v_per_v: float = Field(gt=0.0)
    actual_bandwidth_hz: float = Field(gt=0.0)
    actual_gbw_hz: float = Field(gt=0.0)
    gain_error_percent: float
    bandwidth_error_percent: float
    gbw_error_percent: float
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )


class CalibrationErrorSummary(StrictModel):
    maximum_absolute_gain_error_percent: float = Field(ge=0.0)
    maximum_absolute_bandwidth_error_percent: float = Field(ge=0.0)
    maximum_absolute_gbw_error_percent: float = Field(ge=0.0)
    rms_gain_error_percent: float = Field(ge=0.0)
    rms_bandwidth_error_percent: float = Field(ge=0.0)
    rms_gbw_error_percent: float = Field(ge=0.0)


class DifferentialPairTheoryCalibration(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    status: RunStatus
    topology: Literal[
        "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
    ]
    pdk_profile: str
    target: dict[str, str]
    training_run_path: str
    training_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_run_path: str
    validation_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_observations: list[CalibrationObservation] = Field(min_length=4)
    validation_observations: list[CalibrationObservation] = Field(min_length=1)
    coefficients: CalibrationCoefficients
    leave_one_out_predictions: list[CalibrationPrediction] = Field(min_length=4)
    leave_one_out_errors: CalibrationErrorSummary
    independent_validation_predictions: list[CalibrationPrediction] = Field(
        min_length=1
    )
    independent_validation_errors: CalibrationErrorSummary
    acceptance_thresholds_percent: dict[str, float]
    applicability: dict[str, Any]
    evidence_sources: dict[str, EvidenceSource]
    conclusions: list[str]
    warnings: list[str]


def _file_payload(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration run record is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"calibration run record is not an object: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not structured")
    return value


def _positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def _same(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-15)


def _extract_run(
    path: Path, *, role: Literal["training", "validation"]
) -> tuple[
    list[CalibrationObservation],
    dict[str, str],
    str,
    dict[str, float],
    dict[str, Any],
    str,
]:
    run, run_sha256 = _file_payload(path)
    if run.get("status") != RunStatus.SUCCEEDED.value:
        raise ValueError(f"{role} run must have status=succeeded")
    if run.get("adapter") == "demo" or "bridge" not in str(run.get("adapter", "")):
        raise ValueError(f"{role} run must come from the real Bridge adapter")
    candidates = run.get("candidates")
    actions = run.get("actions")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"{role} run has no candidates")
    if not isinstance(actions, list):
        raise ValueError(f"{role} run has no structured actions")

    target: dict[str, str] | None = None
    profile: str | None = None
    fixed_parameters: dict[str, float] | None = None
    model_configuration: dict[str, Any] | None = None
    observations: list[CalibrationObservation] = []
    seen_candidate_indexes: set[int] = set()
    for raw_candidate in candidates:
        candidate = _mapping(raw_candidate, f"{role} candidate")
        index = int(candidate.get("index", 0))
        if index < 1 or not bool(candidate.get("analysis_complete", False)):
            raise ValueError(f"{role} candidate {index} is not analysis-complete")
        if index in seen_candidate_indexes:
            raise ValueError(f"{role} run contains duplicate candidate index {index}")
        seen_candidate_indexes.add(index)
        parameters = _mapping(
            candidate.get("parameters"), f"{role} candidate {index} parameters"
        )
        metrics = _mapping(
            candidate.get("metrics"), f"{role} candidate {index} metrics"
        )
        metric_sources = _mapping(
            candidate.get("metric_sources"),
            f"{role} candidate {index} metric sources",
        )
        for metric in _ACTUAL_METRICS:
            if metric_sources.get(metric) != EvidenceSource.EDA_RESULT.value:
                raise ValueError(
                    f"{role} candidate {index} metric {metric} is not eda_result"
                )

        matching_actions = [
            action
            for action in actions
            if isinstance(action, dict)
            and action.get("action") == f"simulation.candidate.{index}"
        ]
        if len(matching_actions) != 1:
            raise ValueError(
                f"{role} candidate {index} does not have one exact simulation action"
            )
        action = matching_actions[0]
        if (
            action.get("status") != "succeeded"
            or action.get("evidence_source") != EvidenceSource.EDA_RESULT.value
        ):
            raise ValueError(
                f"{role} candidate {index} simulation is not successful eda_result"
            )
        details = _mapping(action.get("details"), "simulation details")
        evidence = _mapping(details.get("evidence"), "simulation evidence")
        schematic = _mapping(evidence.get("schematic_readback"), "OA evidence")
        netlist = _mapping(evidence.get("netlist"), "netlist evidence")
        testbench = _mapping(evidence.get("testbench"), "testbench evidence")
        operating_point = _mapping(
            evidence.get("operating_point"), "operating-point evidence"
        )
        ac_response = _mapping(evidence.get("ac_response"), "AC evidence")
        if schematic.get("source") != EvidenceSource.BRIDGE_READBACK.value:
            raise ValueError("calibration OA evidence is not bridge_readback")
        if (
            netlist.get("source") != EvidenceSource.EDA_RESULT.value
            or netlist.get("topology_variant") != _TOPOLOGY
            or netlist.get("parameter_consistency") != "matched"
        ):
            raise ValueError("calibration netlist evidence is not a matched Gate-6 netlist")
        if operating_point.get("source") != EvidenceSource.EDA_RESULT.value:
            raise ValueError("calibration operating point is not eda_result")
        if (
            ac_response.get("source") != EvidenceSource.EDA_RESULT.value
            or not bool(ac_response.get("analysis_complete", False))
            or ac_response.get("issues")
        ):
            raise ValueError("calibration AC response is incomplete")

        current_target = {
            str(name): str(value)
            for name, value in _mapping(schematic.get("target"), "target").items()
        }
        current_model = _mapping(
            testbench.get("model_configuration"), "model configuration"
        )
        current_profile = str(current_model.get("profile") or "")
        if not current_profile:
            raise ValueError("calibration model configuration has no PDK profile")
        current_fixed = {
            name: _positive(parameters.get(name), f"parameter {name}")
            for name in _FIXED_PARAMETER_NAMES
        }
        if target is None:
            target = current_target
            profile = current_profile
            fixed_parameters = current_fixed
            model_configuration = current_model
        elif (
            current_target != target
            or current_profile != profile
            or current_model != model_configuration
            or any(
                not _same(current_fixed[name], fixed_parameters[name])
                for name in _FIXED_PARAMETER_NAMES
            )
        ):
            raise ValueError(
                f"{role} calibration candidates do not share one target/model/condition"
            )

        devices = _mapping(
            operating_point.get("device_values"), "operating-point devices"
        )
        nodes = _mapping(operating_point.get("node_values_v"), "operating-point nodes")
        input_device = _mapping(devices.get("MN1"), "MN1 operating point")
        load_device = _mapping(devices.get("MP1"), "MP1 operating point")
        branch_current = _positive(input_device.get("ids_a"), "MN1 ids")
        try:
            load_current = abs(float(load_device.get("ids_a")))
        except (TypeError, ValueError) as exc:
            raise ValueError("MP1 ids is not numeric") from exc
        if not math.isfinite(load_current) or load_current <= 0.0:
            raise ValueError("MP1 |ids| must be finite and positive")
        if abs(branch_current - load_current) / branch_current > 0.01:
            raise ValueError("calibration output-branch KCL mismatch exceeds 1%")
        input_gm = _positive(input_device.get("gm_s"), "MN1 gm")
        input_gds = _positive(input_device.get("gds_s"), "MN1 gds")
        load_gds = _positive(load_device.get("gds_s"), "MP1 gds")
        actual_gain = _positive(
            metrics.get("differential_low_frequency_gain_v_per_v"), "actual gain"
        )
        actual_bandwidth = _positive(
            metrics.get("differential_bandwidth_3db_hz"), "actual bandwidth"
        )
        actual_gbw = _positive(
            metrics.get("differential_gain_bandwidth_product_hz"), "actual GBW"
        )
        if abs(actual_gain * actual_bandwidth / actual_gbw - 1.0) > 1e-8:
            raise ValueError("calibration GBW does not equal gain times bandwidth")
        output_conductance = input_gds + load_gds
        effective_capacitance = output_conductance / (
            2.0 * math.pi * actual_bandwidth
        )
        external_load = current_fixed["load_ff"] * 1e-15
        if effective_capacitance <= external_load:
            raise ValueError("inferred effective capacitance does not exceed CL")

        raw_files = _mapping(operating_point.get("raw_files"), "OP raw files")
        dc_file = _mapping(raw_files.get("dc"), "DC file evidence")
        op_file = _mapping(raw_files.get("operating_point"), "OP file evidence")
        for node in ("OUTP", "OUTN", "TAIL"):
            _positive(nodes.get(node), f"node {node}")
        observations.append(
            CalibrationObservation(
                source_run_role=role,
                candidate_index=index,
                input_width_um=_positive(parameters.get("input_width_um"), "Wn"),
                load_width_um=_positive(
                    parameters.get("pmos_load_width_um"), "Wp"
                ),
                branch_current_a=branch_current,
                input_gm_s=input_gm,
                input_gds_s=input_gds,
                load_gds_s=load_gds,
                raw_gain_v_per_v=input_gm / output_conductance,
                inferred_effective_output_capacitance_f=effective_capacitance,
                actual_gain_v_per_v=actual_gain,
                actual_bandwidth_hz=actual_bandwidth,
                actual_gbw_hz=actual_gbw,
                netlist_sha256=str(netlist.get("sha256")),
                wrapper_sha256=str(testbench.get("sha256")),
                dc_sha256=str(dc_file.get("sha256")),
                operating_point_sha256=str(op_file.get("sha256")),
            )
        )

    assert target is not None
    assert profile is not None
    assert fixed_parameters is not None
    assert model_configuration is not None
    if role == "training":
        geometries = {
            (item.input_width_um, item.load_width_um) for item in observations
        }
        input_widths = {item.input_width_um for item in observations}
        load_widths = {item.load_width_um for item in observations}
        expected_geometries = {
            (input_width, load_width)
            for input_width in input_widths
            for load_width in load_widths
        }
        if len(geometries) != len(observations):
            raise ValueError("training run contains duplicate Wn/Wp geometry")
        if geometries != expected_geometries:
            raise ValueError("training run does not cover its full rectangular Wn/Wp grid")
    return (
        observations,
        target,
        profile,
        fixed_parameters,
        model_configuration,
        run_sha256,
    )


def _fit(
    observations: list[CalibrationObservation],
    *,
    external_load_capacitance_f: float,
) -> CalibrationCoefficients:
    if len(observations) < 3:
        raise ValueError("theory calibration requires at least three observations")
    gain_factor = sum(
        item.actual_gain_v_per_v / item.raw_gain_v_per_v for item in observations
    ) / len(observations)
    s_nn = sum(item.input_width_um**2 for item in observations)
    s_pp = sum(item.load_width_um**2 for item in observations)
    s_np = sum(
        item.input_width_um * item.load_width_um for item in observations
    )
    b_n = sum(
        item.input_width_um
        * (
            item.inferred_effective_output_capacitance_f
            - external_load_capacitance_f
        )
        for item in observations
    )
    b_p = sum(
        item.load_width_um
        * (
            item.inferred_effective_output_capacitance_f
            - external_load_capacitance_f
        )
        for item in observations
    )
    determinant = s_nn * s_pp - s_np * s_np
    if determinant <= max(s_nn * s_pp, 1.0) * 1e-15:
        raise ValueError(
            "calibration width domain is rank-deficient; vary Wn and Wp independently"
        )
    input_cap = (b_n * s_pp - b_p * s_np) / determinant
    load_cap = (b_p * s_nn - b_n * s_np) / determinant
    if input_cap <= 0.0 or load_cap <= 0.0:
        raise ValueError("calibrated effective capacitance coefficients are not positive")
    return CalibrationCoefficients(
        differential_gain_correction_factor=gain_factor,
        input_effective_output_capacitance_f_per_um=input_cap,
        load_effective_output_capacitance_f_per_um=load_cap,
        external_load_capacitance_f=external_load_capacitance_f,
    )


def _predict(
    observation: CalibrationObservation,
    coefficients: CalibrationCoefficients,
    *,
    role: Literal["training_leave_one_out", "independent_validation"],
) -> CalibrationPrediction:
    capacitance = (
        coefficients.external_load_capacitance_f
        + coefficients.input_effective_output_capacitance_f_per_um
        * observation.input_width_um
        + coefficients.load_effective_output_capacitance_f_per_um
        * observation.load_width_um
    )
    output_conductance = observation.input_gds_s + observation.load_gds_s
    gain = (
        coefficients.differential_gain_correction_factor
        * observation.raw_gain_v_per_v
    )
    bandwidth = output_conductance / (2.0 * math.pi * capacitance)
    gbw = gain * bandwidth

    def error(predicted: float, actual: float) -> float:
        return (predicted / actual - 1.0) * 100.0

    return CalibrationPrediction(
        source_run_role=role,
        candidate_index=observation.candidate_index,
        input_width_um=observation.input_width_um,
        load_width_um=observation.load_width_um,
        predicted_gain_v_per_v=gain,
        predicted_bandwidth_hz=bandwidth,
        predicted_gbw_hz=gbw,
        actual_gain_v_per_v=observation.actual_gain_v_per_v,
        actual_bandwidth_hz=observation.actual_bandwidth_hz,
        actual_gbw_hz=observation.actual_gbw_hz,
        gain_error_percent=error(gain, observation.actual_gain_v_per_v),
        bandwidth_error_percent=error(
            bandwidth, observation.actual_bandwidth_hz
        ),
        gbw_error_percent=error(gbw, observation.actual_gbw_hz),
    )


def _summary(predictions: list[CalibrationPrediction]) -> CalibrationErrorSummary:
    def values(name: str) -> list[float]:
        return [float(getattr(item, name)) for item in predictions]

    gain = values("gain_error_percent")
    bandwidth = values("bandwidth_error_percent")
    gbw = values("gbw_error_percent")

    def rms(items: list[float]) -> float:
        return math.sqrt(sum(value * value for value in items) / len(items))

    return CalibrationErrorSummary(
        maximum_absolute_gain_error_percent=max(abs(value) for value in gain),
        maximum_absolute_bandwidth_error_percent=max(
            abs(value) for value in bandwidth
        ),
        maximum_absolute_gbw_error_percent=max(abs(value) for value in gbw),
        rms_gain_error_percent=rms(gain),
        rms_bandwidth_error_percent=rms(bandwidth),
        rms_gbw_error_percent=rms(gbw),
    )


def calibrate_differential_pair_theory(
    training_run: Path,
    validation_run: Path,
    *,
    calibration_id: str = "gate6-one-pole-calibration",
) -> DifferentialPairTheoryCalibration:
    (
        training,
        target,
        profile,
        fixed,
        model_configuration,
        training_sha,
    ) = _extract_run(training_run, role="training")
    (
        validation,
        validation_target,
        validation_profile,
        validation_fixed,
        validation_model_configuration,
        validation_sha,
    ) = _extract_run(validation_run, role="validation")
    if (
        validation_target != target
        or validation_profile != profile
        or validation_model_configuration != model_configuration
        or any(
            not _same(validation_fixed[name], fixed[name])
            for name in _FIXED_PARAMETER_NAMES
        )
    ):
        raise ValueError(
            "validation run does not match the training target/model/condition"
        )
    input_widths = sorted({item.input_width_um for item in training})
    load_widths = sorted({item.load_width_um for item in training})
    if len(input_widths) < 2 or len(load_widths) < 2:
        raise ValueError("training run must vary both Wn and Wp")
    for item in validation:
        if not (
            input_widths[0] <= item.input_width_um <= input_widths[-1]
            and load_widths[0] <= item.load_width_um <= load_widths[-1]
        ):
            raise ValueError("validation point is outside the trained width domain")

    external_load = fixed["load_ff"] * 1e-15
    coefficients = _fit(training, external_load_capacitance_f=external_load)
    leave_one_out: list[CalibrationPrediction] = []
    for index, held_out in enumerate(training):
        subset = training[:index] + training[index + 1 :]
        loo_coefficients = _fit(
            subset,
            external_load_capacitance_f=external_load,
        )
        leave_one_out.append(
            _predict(held_out, loo_coefficients, role="training_leave_one_out")
        )
    validation_predictions = [
        _predict(item, coefficients, role="independent_validation")
        for item in validation
    ]
    loo_errors = _summary(leave_one_out)
    validation_errors = _summary(validation_predictions)
    thresholds = {"gain": 2.0, "bandwidth": 2.0, "gbw": 3.0}
    accepted = (
        loo_errors.maximum_absolute_gain_error_percent <= thresholds["gain"]
        and loo_errors.maximum_absolute_bandwidth_error_percent
        <= thresholds["bandwidth"]
        and loo_errors.maximum_absolute_gbw_error_percent <= thresholds["gbw"]
        and validation_errors.maximum_absolute_gain_error_percent
        <= thresholds["gain"]
        and validation_errors.maximum_absolute_bandwidth_error_percent
        <= thresholds["bandwidth"]
        and validation_errors.maximum_absolute_gbw_error_percent
        <= thresholds["gbw"]
    )
    status = RunStatus.SUCCEEDED if accepted else RunStatus.PARTIAL
    training_geometries = {
        (item.input_width_um, item.load_width_um) for item in training
    }
    validation_repeats_training_geometry = all(
        (item.input_width_um, item.load_width_um) in training_geometries
        for item in validation
    )
    validation_boundary = (
        "The independent run repeats a trained geometry at a later execution "
        "time; it checks EDA repeatability, not geometric extrapolation."
        if validation_repeats_training_geometry
        else "The independent run uses an unseen geometry inside the measured "
        "Wn/Wp bounds; it checks local interpolation, not extrapolation."
    )
    conclusions = [
        (
            "The calibrated one-pole model met the declared local error gates "
            "inside the measured Wn/Wp domain."
            if accepted
            else "The calibrated one-pole model did not meet every declared local "
            "error gate; it must not seed sizing."
        ),
        validation_boundary,
        "No continuous-space or global optimum is established by calibration.",
    ]
    return DifferentialPairTheoryCalibration(
        id=calibration_id,
        status=status,
        topology=_TOPOLOGY,
        pdk_profile=profile,
        target=target,
        training_run_path=str(training_run.resolve()),
        training_run_sha256=training_sha,
        validation_run_path=str(validation_run.resolve()),
        validation_run_sha256=validation_sha,
        training_observations=training,
        validation_observations=validation,
        coefficients=coefficients,
        leave_one_out_predictions=leave_one_out,
        leave_one_out_errors=loo_errors,
        independent_validation_predictions=validation_predictions,
        independent_validation_errors=validation_errors,
        acceptance_thresholds_percent=thresholds,
        applicability={
            "input_width_um": {"minimum": input_widths[0], "maximum": input_widths[-1]},
            "pmos_load_width_um": {
                "minimum": load_widths[0],
                "maximum": load_widths[-1],
            },
            "fixed_parameters": fixed,
            "model_configuration": model_configuration,
            "training_grid": {
                "input_width_values_um": input_widths,
                "pmos_load_width_values_um": load_widths,
                "observed_points": len(training),
                "full_rectangular_grid": True,
            },
            "validation_relationship": (
                "repeat_of_training_geometry"
                if validation_repeats_training_geometry
                else "unseen_geometry_inside_training_bounds"
            ),
            "interpolation_only": True,
            "length_or_bias_extrapolation_allowed": False,
        },
        evidence_sources={
            "OA topology and geometry": EvidenceSource.BRIDGE_READBACK,
            "netlists, operating points, and AC metrics": EvidenceSource.EDA_RESULT,
            "training/validation selection, fit, inferred capacitance, "
            "predictions, and error gates": (
                EvidenceSource.SOFTWARE_INFERENCE
            ),
            "calibration request and read-only execution authority": (
                EvidenceSource.USER_INPUT
            ),
        },
        conclusions=conclusions,
        warnings=[
            "The fitted capacitance coefficients are topology-effective values, "
            "not standalone PDK Cgg/Cgd/Cdb device parameters.",
            "Calibration is nominal top_tt at one VDD/VCM/BIAS/CL and fixed "
            "30 nm lengths; PVT, other bias points, and other topology variants "
            "remain uncalibrated.",
            "The model does not predict internal poles/zeros, CMRR, PSRR, noise, "
            "distortion, slew, settling, mismatch, or stability.",
            "Predictions in this calibration artifact consume Spectre-derived gm/gds; "
            "without a separate device characterization/interpolator they cannot "
            "estimate an unevaluated circuit point before EDA.",
        ],
    )
