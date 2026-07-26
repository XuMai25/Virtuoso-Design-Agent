import json
from pathlib import Path

import pytest

from virtuoso_design_agent.characterization import MOS_CHARGE_DERIVATIVE_NAMES
from virtuoso_design_agent.models import EvidenceSource, RunStatus
from virtuoso_design_agent.op_small_signal import (
    OperatingPointSmallSignalPolicy,
    analyze_operating_point_small_signal_run,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_POLICY = (
    ROOT / "examples" / "theory" / "common-source-op-small-signal-policy.json"
)
CASCODE_POLICY = (
    ROOT
    / "examples"
    / "theory"
    / "common-source-cascode-op-small-signal-policy.json"
)
BASELINE_RUN = (
    ROOT
    / "artifacts"
    / "runs"
    / "common-source-cascode-roundtrip-baseline-ac"
    / "run-A-20260726.json"
)
CASCODE_RUN = (
    ROOT
    / "artifacts"
    / "runs"
    / "common-source-cascode-ac-seeded"
    / "run-20260726T-live-real-network.json"
)


def _policy(path: Path) -> OperatingPointSmallSignalPolicy:
    return OperatingPointSmallSignalPolicy.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def test_real_common_source_op_linearization_matches_existing_ac() -> None:
    result = analyze_operating_point_small_signal_run(
        _policy(BASELINE_POLICY), BASELINE_RUN
    )

    comparison = result.eda_comparison["low_frequency_gain_v_per_v"]
    assert result.status is RunStatus.SUCCEEDED
    assert result.analysis_scope == "low_frequency_conductance"
    assert result.graph_binding["topology_equation_hardcoded"] is False
    assert result.graph_binding["device_instances"] == ["MN0"]
    assert result.network_result.low_frequency_gain_v_per_v == pytest.approx(
        4.59574362807521
    )
    assert comparison.actual == pytest.approx(4.588483049639973)
    assert comparison.relative_error == pytest.approx(0.0015823483178840062)
    assert result.evidence_sources["operating_point"] is EvidenceSource.EDA_RESULT
    assert (
        result.evidence_sources["network_solution"]
        is EvidenceSource.SOFTWARE_INFERENCE
    )
    assert "bandwidth_3db_hz" in result.uncovered_metrics


def test_real_cascode_op_linearization_predicts_gain_without_topology_formula() -> None:
    baseline = analyze_operating_point_small_signal_run(
        _policy(BASELINE_POLICY), BASELINE_RUN
    )
    cascode = analyze_operating_point_small_signal_run(
        _policy(CASCODE_POLICY), CASCODE_RUN
    )

    comparison = cascode.eda_comparison["low_frequency_gain_v_per_v"]
    predicted_uplift = (
        cascode.network_result.low_frequency_gain_v_per_v
        / baseline.network_result.low_frequency_gain_v_per_v
        - 1.0
    )
    actual_uplift = (
        comparison.actual
        / baseline.eda_comparison["low_frequency_gain_v_per_v"].actual
        - 1.0
    )
    assert cascode.status is RunStatus.PARTIAL
    assert cascode.graph_binding["device_instances"] == ["MN0", "MNCAS"]
    assert cascode.network_result.low_frequency_gain_v_per_v == pytest.approx(
        6.399649524471076
    )
    assert comparison.actual == pytest.approx(6.441244288873575)
    assert comparison.relative_error == pytest.approx(0.006457566665240296)
    assert predicted_uplift == pytest.approx(0.39251665070607466)
    assert actual_uplift == pytest.approx(0.40378513316704435)
    assert "gmb_s" in cascode.missing_quantities_by_instance["MNCAS"]
    assert any("Body-effect-active" in item for item in cascode.warnings)


def test_cascode_policy_can_require_body_effect_data() -> None:
    policy = _policy(CASCODE_POLICY).model_copy(
        update={"require_gmb_when_body_effect_active": True}
    )

    with pytest.raises(ValueError, match="missing gmb_s"):
        analyze_operating_point_small_signal_run(policy, CASCODE_RUN)


def test_op_linearization_rejects_topology_drift() -> None:
    policy = _policy(BASELINE_POLICY).model_copy(
        update={"expected_topology_variant": "cascode_common_source"}
    )

    with pytest.raises(ValueError, match="topology variant"):
        analyze_operating_point_small_signal_run(policy, BASELINE_RUN)


def test_full_op_derivative_plane_removes_conductance_coverage_gap(
    tmp_path: Path,
) -> None:
    payload = json.loads(CASCODE_RUN.read_text(encoding="utf-8"))
    action = next(
        item
        for item in payload["actions"]
        if item["action"] == "simulation.candidate.9"
    )
    devices = action["details"]["evidence"]["operating_point"]["device_values"]
    zero_matrix = {name: 0.0 for name in MOS_CHARGE_DERIVATIVE_NAMES}
    for instance in ("MN0", "MNCAS"):
        devices[instance].update(
            {
                "gmb_s": 20e-6,
                "charge_derivative_matrix_f": zero_matrix,
                "cjd_f": 2e-16,
                "cjs_f": 3e-16,
            }
        )
    run_path = tmp_path / "cascode-full-op.json"
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    result = analyze_operating_point_small_signal_run(
        _policy(CASCODE_POLICY), run_path
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.missing_quantities_by_instance == {"MN0": [], "MNCAS": []}
    derived = {
        item.instance: item for item in result.network_result.derived_mos_values
    }
    assert derived["MN0"].gmb_s == pytest.approx(20e-6)
    assert derived["MNCAS"].gmb_s == pytest.approx(20e-6)
    assert all(
        item.capacitance_model == "terminal_charge_derivative_matrix"
        for item in derived.values()
    )
