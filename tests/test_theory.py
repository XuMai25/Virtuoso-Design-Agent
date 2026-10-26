from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import RunStatus
from virtuoso_design_agent.theory import (
    DifferentialPairTheoryRequest,
    DeviceDataSource,
    size_differential_pair,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT / "examples" / "theory" / "differential-pair-gmid.synthetic.json"
)


def _payload() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_theory_derives_widths_and_exhausts_declared_lookup_domain() -> None:
    request = DifferentialPairTheoryRequest.model_validate(_payload())

    result = size_differential_pair(request)

    assert result.status is RunStatus.SUCCEEDED
    assert result.device_data_source is DeviceDataSource.SYNTHETIC_EXAMPLE
    assert len(result.request_sha256) == 64
    assert result.characterization_conditions["process_corner"] == "synthetic_tt"
    assert result.optimality_boundary.declared_combinations == 4
    assert result.optimality_boundary.evaluated_combinations == 4
    assert result.optimality_boundary.domain_exhausted is True
    assert result.optimality_boundary.global_optimum_claim is False
    assert result.optimality_boundary.continuous_optimum_claim is False
    assert result.recommended_candidate_id == (
        "in=n-in-efficient|load=p-load-short|tail=n-tail-long"
    )
    recommended = next(
        candidate
        for candidate in result.candidates
        if candidate.id == result.recommended_candidate_id
    )
    assert recommended.input_width_um == pytest.approx(
        recommended.branch_current_a / 2e-5
    )
    assert recommended.load_width_um == pytest.approx(
        recommended.branch_current_a / 4e-5
    )
    assert recommended.tail_width_um == pytest.approx(
        2.0 * recommended.branch_current_a / 3e-5
    )
    assert recommended.metrics["estimated_bandwidth_hz"] == pytest.approx(52.5e6)
    assert recommended.limiting_current_bounds == ["estimated_bandwidth_hz"]
    bandwidth_sensitivity = recommended.local_log_sensitivities[
        "estimated_bandwidth_hz"
    ]
    assert 0.0 < bandwidth_sensitivity["branch_current_a"] < 1.0
    assert bandwidth_sensitivity["load_capacitance_f"] == pytest.approx(
        -bandwidth_sensitivity["branch_current_a"]
    )
    assert recommended.evidence_source.value == "software_inference"
    assert any("Synthetic device data" in warning for warning in result.warnings)


def test_power_bound_is_an_infeasible_complete_derivation_not_missing_analysis() -> None:
    request = DifferentialPairTheoryRequest.model_validate(_payload())

    result = size_differential_pair(request)
    rejected = result.candidates[0]

    assert rejected.analysis_complete is True
    assert rejected.feasible is False
    assert rejected.metrics["estimated_power_w"] > 150e-6
    assert any("estimated_power_w bound" in item for item in rejected.infeasibility_reasons)


def test_no_feasible_result_never_promotes_the_closest_candidate() -> None:
    payload = _payload()
    constraints = payload["constraints"]
    assert isinstance(constraints, list)
    gain_constraint = constraints[0]
    assert isinstance(gain_constraint, dict)
    gain_constraint["value"] = 1000.0
    request = DifferentialPairTheoryRequest.model_validate(payload)

    result = size_differential_pair(request)

    assert result.status is RunStatus.PARTIAL
    assert result.recommended_candidate_id is None
    assert result.best_evaluated_candidate_id is not None
    assert result.optimality_boundary.classification == (
        "no_feasible_design_in_declared_discrete_characterization_domain"
    )
    assert "does not prove" in result.optimality_boundary.statement


def test_frequency_above_parasitic_asymptote_is_retained_as_rejection() -> None:
    payload = _payload()
    characterization = payload["device_characterization"]
    assert isinstance(characterization, dict)
    characterization["nmos_input_points"] = [
        copy.deepcopy(characterization["nmos_input_points"][0])
    ]
    characterization["pmos_load_points"] = [
        copy.deepcopy(characterization["pmos_load_points"][0])
    ]
    constraints = payload["constraints"]
    assert isinstance(constraints, list)
    bandwidth_constraint = constraints[1]
    assert isinstance(bandwidth_constraint, dict)
    bandwidth_constraint["value"] = 1e12
    request = DifferentialPairTheoryRequest.model_validate(payload)

    result = size_differential_pair(request)

    assert result.optimality_boundary.declared_combinations == 1
    assert result.optimality_boundary.evaluated_combinations == 1
    assert result.optimality_boundary.domain_exhausted is True
    assert result.candidates[0].analysis_complete is False
    assert "parasitic asymptote" in result.candidates[0].analysis_issues[0]
    assert result.recommended_candidate_id is None


def test_pdk_characterization_requires_a_bound_artifact_hash() -> None:
    payload = _payload()
    characterization = payload["device_characterization"]
    assert isinstance(characterization, dict)
    characterization["source"] = "pdk_characterization"

    with pytest.raises(ValidationError, match="source_artifact_sha256"):
        DifferentialPairTheoryRequest.model_validate(payload)


def test_characterization_voltage_must_match_the_analyzed_operating_point() -> None:
    payload = _payload()
    characterization = payload["device_characterization"]
    assert isinstance(characterization, dict)
    characterization["input_nmos_vds_v"] = 0.6

    with pytest.raises(
        ValidationError,
        match="maximum_characterization_voltage_mismatch_v",
    ):
        DifferentialPairTheoryRequest.model_validate(payload)


def test_mirror_diode_and_single_ended_output_headrooms_remain_distinct() -> None:
    payload = _payload()
    payload["mirror_diode_node_v"] = 0.44
    payload["maximum_characterization_voltage_mismatch_v"] = 0.02
    request = DifferentialPairTheoryRequest.model_validate(payload)

    result = size_differential_pair(request)
    recommended = next(
        candidate
        for candidate in result.candidates
        if candidate.id == result.recommended_candidate_id
    )

    assert recommended.metrics["estimated_input_diode_headroom_v"] == pytest.approx(
        0.20
    )
    assert recommended.metrics["estimated_input_output_headroom_v"] == pytest.approx(
        0.21
    )
    assert recommended.metrics["estimated_load_diode_headroom_v"] == pytest.approx(
        0.29
    )
    assert recommended.metrics["estimated_load_output_headroom_v"] == pytest.approx(
        0.28
    )


def test_request_rejects_an_untracked_candidate_list() -> None:
    payload = _payload()
    payload["candidates"] = [{"width_um": 1.0}]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DifferentialPairTheoryRequest.model_validate(payload)


def test_theory_cli_emits_and_optionally_saves_the_same_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "theory-result.json"

    assert main(["theory", str(EXAMPLE), "--output", str(output)]) == 0

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert printed == saved
    assert printed["derived_metric_evidence_source"] == "software_inference"
    assert printed["optimality_boundary"]["global_optimum_claim"] is False
