from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.characterization import MosCharacterizationArtifact
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.small_signal import (
    SmallSignalNetworkRequest,
    analyze_small_signal_network,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "theory" / "common-source-small-signal.synthetic.json"


def _point(
    point_id: str = "nmos-typical",
    *,
    gds_over_id: float = 0.0,
    cdb_f_per_um: float = 0.0,
) -> dict:
    return {
        "id": point_id,
        "model": "nch_lvt_mac",
        "polarity": "nmos",
        "length_um": 0.03,
        "vgs_magnitude_v": 0.5,
        "vds_magnitude_v": 0.5,
        "vsb_magnitude_v": 0.0,
        "vdsat_magnitude_v": 0.1,
        "drain_current_density_a_per_um": 1e-3,
        "gm_over_id_per_v": 1.0,
        "gds_over_id_per_v": gds_over_id,
        "gmb_over_id_per_v": 0.0,
        "cgs_f_per_um": 0.0,
        "cgd_f_per_um": 0.0,
        "cgb_f_per_um": 0.0,
        "cdb_f_per_um": cdb_f_per_um,
        "csb_f_per_um": 0.0,
    }


def _artifact(point: dict | None = None) -> dict:
    return {
        "id": "synthetic-device-table",
        "source": "synthetic_example",
        "pdk_profile": "synthetic_cmos",
        "process_corner": "synthetic_tt",
        "temperature_c": 27.0,
        "raw_data_evidence_source": "user_input",
        "normalized_point_evidence_source": "user_input",
        "points": [point or _point()],
    }


def _pairwise_charge_matrix(**capacitances: float) -> dict[str, float]:
    matrix = {
        f"c{row}{column}": 0.0
        for row in ("g", "d", "s", "b")
        for column in ("g", "d", "s", "b")
    }
    for terminals, capacitance in capacitances.items():
        first, second = terminals
        matrix[f"c{first}{first}"] += capacitance
        matrix[f"c{second}{second}"] += capacitance
        matrix[f"c{first}{second}"] -= capacitance
        matrix[f"c{second}{first}"] -= capacitance
    return matrix


def _mos(name: str, drain: str, gate: str, source: str, point_id: str) -> dict:
    return {
        "name": name,
        "model": "nch_lvt_mac",
        "point_id": point_id,
        "drain": drain,
        "gate": gate,
        "source": source,
        "bulk": "0",
        "width_um": 1.0,
        "length_um": 0.03,
        "vgs_magnitude_v": 0.5,
        "vds_magnitude_v": 0.5,
        "vsb_magnitude_v": 0.0,
    }


def _common_source_payload() -> dict:
    frequencies = [10.0 ** (3.0 + index / 20.0) for index in range(121)]
    return {
        "id": "common-source-network",
        "characterization": _artifact(
            _point(gds_over_id=0.01, cdb_f_per_um=1e-12)
        ),
        "mosfets": [_mos("MN0", "OUT", "IN", "0", "nmos-typical")],
        "resistors": [
            {
                "name": "RD",
                "positive": "OUT",
                "negative": "VDD",
                "resistance_ohm": 10_000.0,
            }
        ],
        "boundary_voltages": [
            {"node": "IN", "voltage": {"real": 1.0}},
            {"node": "VDD", "voltage": {}},
        ],
        "input_expression": {"terms": {"IN": 1.0}},
        "output_expression": {"terms": {"OUT": 1.0}},
        "frequencies_hz": frequencies,
        "reference_points": 3,
    }


def test_same_solver_analyzes_a_common_source_gain_and_pole() -> None:
    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(_common_source_payload())
    )

    expected_gain = 1e-3 / (1e-5 + 1e-4)
    expected_bandwidth = (1e-5 + 1e-4) / (2.0 * math.pi * 1e-12)
    assert result.topology_independent_core is True
    assert result.process_corner == "synthetic_tt"
    assert result.low_frequency_gain_v_per_v == pytest.approx(
        expected_gain, rel=1e-6
    )
    assert abs(result.low_frequency_phase_deg) == pytest.approx(180.0, abs=0.01)
    assert result.bandwidth_status == "resolved"
    assert result.bandwidth_3db_hz == pytest.approx(expected_bandwidth, rel=0.003)
    assert result.derived_mos_values[0].gm_s == pytest.approx(1e-3)
    assert result.characterization_raw_data_evidence_source.value == "user_input"
    assert result.characterization_point_evidence_source.value == "user_input"
    assert result.derived_metric_evidence_source.value == "software_inference"


def test_terminal_charge_matrix_matches_equivalent_legacy_pairwise_capacitance() -> None:
    legacy_payload = _common_source_payload()
    matrix_payload = copy.deepcopy(legacy_payload)
    matrix_payload["id"] = "common-source-terminal-charge-matrix"
    matrix_payload["characterization"]["points"][0][
        "charge_derivative_matrix_f_per_um"
    ] = _pairwise_charge_matrix(db=5e-13)
    matrix_payload["characterization"]["points"][0]["cjd_f_per_um"] = 5e-13

    legacy = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(legacy_payload)
    )
    matrix = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(matrix_payload)
    )

    assert matrix.derived_mos_values[0].capacitance_model == (
        "terminal_charge_derivative_matrix"
    )
    assert matrix.derived_mos_values[0].cjd_f == pytest.approx(5e-13)
    assert matrix.bandwidth_3db_hz == pytest.approx(
        legacy.bandwidth_3db_hz, rel=1e-12
    )
    for matrix_point, legacy_point in zip(matrix.points, legacy.points):
        assert matrix_point.transfer.as_complex() == pytest.approx(
            legacy_point.transfer.as_complex(), rel=1e-12, abs=1e-15
        )
    assert not any("legacy reciprocal" in warning for warning in matrix.warnings)


def test_same_solver_analyzes_source_degeneration_without_a_topology_formula() -> None:
    payload = _common_source_payload()
    payload["id"] = "source-degenerated-common-source-network"
    payload["characterization"] = _artifact()
    payload["mosfets"] = [_mos("MN0", "OUT", "IN", "SRC", "nmos-typical")]
    payload["resistors"].append(
        {
            "name": "RS",
            "positive": "SRC",
            "negative": "0",
            "resistance_ohm": 1_000.0,
        }
    )
    payload["frequencies_hz"] = [1_000.0]
    payload["reference_points"] = 1

    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(payload)
    )

    assert result.points[0].transfer.real == pytest.approx(-5.0)
    assert result.low_frequency_gain_v_per_v == pytest.approx(5.0)
    assert result.bandwidth_status == "unresolved"


def test_same_stamp_uses_physical_terminal_order_for_pmos() -> None:
    pmos_point = _point("pmos-typical", gds_over_id=0.01)
    pmos_point["model"] = "pch_lvt_mac"
    pmos_point["polarity"] = "pmos"
    payload = {
        "id": "pmos-common-source-network",
        "characterization": _artifact(pmos_point),
        "mosfets": [
            {
                **_mos("MP0", "OUT", "IN", "VDD", "pmos-typical"),
                "model": "pch_lvt_mac",
                "bulk": "VDD",
            }
        ],
        "resistors": [
            {
                "name": "RD",
                "positive": "OUT",
                "negative": "0",
                "resistance_ohm": 10_000.0,
            }
        ],
        "boundary_voltages": [
            {"node": "IN", "voltage": {"real": 1.0}},
            {"node": "VDD", "voltage": {}},
        ],
        "input_expression": {"terms": {"IN": 1.0}},
        "output_expression": {"terms": {"OUT": 1.0}},
        "frequencies_hz": [1_000.0],
    }

    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(payload)
    )

    assert result.points[0].transfer.real == pytest.approx(-1e-3 / 1.1e-4)
    assert result.derived_mos_values[0].polarity.value == "pmos"


def test_same_solver_analyzes_a_differential_pair_graph() -> None:
    payload = {
        "id": "differential-pair-network",
        "characterization": _artifact(),
        "mosfets": [
            _mos("MN0", "OUTP", "INP", "TAIL", "nmos-typical"),
            _mos("MN1", "OUTN", "INN", "TAIL", "nmos-typical"),
        ],
        "resistors": [
            {
                "name": "RDP",
                "positive": "OUTP",
                "negative": "VDD",
                "resistance_ohm": 10_000.0,
            },
            {
                "name": "RDN",
                "positive": "OUTN",
                "negative": "VDD",
                "resistance_ohm": 10_000.0,
            },
            {
                "name": "RTAIL",
                "positive": "TAIL",
                "negative": "0",
                "resistance_ohm": 100_000.0,
            },
        ],
        "boundary_voltages": [
            {"node": "INP", "voltage": {"real": 0.5}},
            {"node": "INN", "voltage": {"real": -0.5}},
            {"node": "VDD", "voltage": {}},
        ],
        "input_expression": {"terms": {"INP": 1.0, "INN": -1.0}},
        "output_expression": {"terms": {"OUTN": 1.0, "OUTP": -1.0}},
        "frequencies_hz": [1_000.0],
    }

    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(payload)
    )

    assert result.points[0].transfer.real == pytest.approx(10.0)
    assert result.points[0].node_voltages["TAIL"].real == pytest.approx(0.0)
    assert len(result.derived_mos_values) == 2


def test_characterization_requires_real_evidence_binding() -> None:
    payload = _artifact()
    payload["source"] = "pdk_characterization"
    payload["pdk_profile"] = "nics4304_tsmc28"

    with pytest.raises(ValidationError, match="source_artifact_sha256"):
        MosCharacterizationArtifact.model_validate(payload)

    payload["source_artifact_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="raw characterization must be eda_result"):
        MosCharacterizationArtifact.model_validate(payload)

    payload["raw_data_evidence_source"] = "eda_result"
    with pytest.raises(ValidationError, match="must be software_inference"):
        MosCharacterizationArtifact.model_validate(payload)

    payload["normalized_point_evidence_source"] = "software_inference"
    assert MosCharacterizationArtifact.model_validate(payload).source.value == (
        "pdk_characterization"
    )


def test_network_rejects_a_characterization_bias_mismatch() -> None:
    payload = _common_source_payload()
    payload["mosfets"][0]["vds_magnitude_v"] = 0.3

    with pytest.raises(ValidationError, match="VDS does not match"):
        SmallSignalNetworkRequest.model_validate(payload)


def test_network_rejects_a_device_model_mismatch() -> None:
    payload = _common_source_payload()
    payload["mosfets"][0]["model"] = "different_nmos"

    with pytest.raises(ValidationError, match="model does not match"):
        SmallSignalNetworkRequest.model_validate(payload)


def test_network_rejects_a_floating_unknown_node() -> None:
    payload = _common_source_payload()
    payload["characterization"] = _artifact()
    payload["resistors"] = []
    payload["boundary_voltages"] = [payload["boundary_voltages"][0]]
    payload["frequencies_hz"] = [1_000.0]
    payload["reference_points"] = 1
    request = SmallSignalNetworkRequest.model_validate(payload)

    with pytest.raises(ValueError, match="singular or ill-conditioned"):
        analyze_small_signal_network(request)


def test_network_contract_rejects_a_claimed_topology_field() -> None:
    payload = copy.deepcopy(_common_source_payload())
    payload["topology"] = "common_source"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SmallSignalNetworkRequest.model_validate(payload)


def test_network_accepts_hierarchical_and_bus_style_net_names() -> None:
    payload = _common_source_payload()
    payload["mosfets"][0]["drain"] = "I0/OUT<0>"
    payload["resistors"][0]["positive"] = "I0/OUT<0>"
    payload["output_expression"] = {"terms": {"I0/OUT<0>": 1.0}}

    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(payload)
    )

    assert "I0/OUT<0>" in result.points[0].node_voltages


def test_nonflat_reference_is_retained_as_partial_not_a_sizing_metric() -> None:
    payload = _common_source_payload()
    payload["frequencies_hz"] = [1e6, 1e8]
    payload["reference_points"] = 2
    payload["maximum_reference_variation_db"] = 0.01

    result = analyze_small_signal_network(
        SmallSignalNetworkRequest.model_validate(payload)
    )

    assert result.status.value == "partial"
    assert result.reference_status == "not_flat"
    assert result.bandwidth_status == "unresolved"
    assert any("must not seed sizing" in warning for warning in result.warnings)


def test_small_signal_cli_saves_the_same_topology_free_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "small-signal.json"

    assert main(["small-signal", str(EXAMPLE), "--output", str(output)]) == 0

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert printed == saved
    assert printed["topology_independent_core"] is True
    assert printed["bandwidth_status"] == "resolved"
    assert printed["derived_metric_evidence_source"] == "software_inference"
