from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import RunStatus
from virtuoso_design_agent.theory_calibration import (
    calibrate_differential_pair_theory,
)


_HASH = "a" * 64
_TARGET = {
    "library": "vb_pdk_smoke",
    "cell": "vda_diffpair_active_gate6_001",
    "view": "schematic",
}
_MODEL = {
    "source": "pdk_profile",
    "profile": "nics4304_tsmc28",
    "process_corner": "top_tt",
    "includes": [{"path": "/data/xum/models.scs", "section": "top_tt"}],
}


def _candidate(index: int, wn: float, wp: float) -> tuple[dict, dict]:
    gain_factor = 0.92
    input_cap_per_um = 0.5e-15
    load_cap_per_um = 0.7e-15
    load_cap = 0.5e-15
    input_gds = (15.0 + wn) * 1e-6
    load_gds = (25.0 + wp) * 1e-6
    gm = (175.0 + 5.0 * wn) * 1e-6
    output_conductance = input_gds + load_gds
    raw_gain = gm / output_conductance
    gain = gain_factor * raw_gain
    effective_capacitance = (
        load_cap + input_cap_per_um * wn + load_cap_per_um * wp
    )
    bandwidth = output_conductance / (2.0 * math.pi * effective_capacitance)
    gbw = gain * bandwidth
    parameters = {
        "input_width_um": wn,
        "pmos_load_width_um": wp,
        "length_um": 0.03,
        "pmos_load_length_um": 0.03,
        "tail_width_um": 0.8,
        "tail_length_um": 0.03,
        "tail_bias_v": 0.32,
        "common_mode_v": 0.55,
        "vdd_v": 0.9,
        "load_ff": 0.5,
    }
    metrics = {
        "differential_low_frequency_gain_v_per_v": gain,
        "differential_bandwidth_3db_hz": bandwidth,
        "differential_gain_bandwidth_product_hz": gbw,
    }
    candidate = {
        "index": index,
        "parameters": parameters,
        "metrics": metrics,
        "metric_sources": {name: "eda_result" for name in metrics},
        "analysis_complete": True,
    }
    action = {
        "action": f"simulation.candidate.{index}",
        "status": "succeeded",
        "evidence_source": "eda_result",
        "details": {
            "evidence": {
                "schematic_readback": {
                    "source": "bridge_readback",
                    "target": _TARGET,
                },
                "netlist": {
                    "source": "eda_result",
                    "topology_variant": (
                        "pmos_current_mirror_load_nmos_differential_pair_"
                        "with_tail_device"
                    ),
                    "parameter_consistency": "matched",
                    "sha256": _HASH,
                },
                "testbench": {
                    "sha256": _HASH,
                    "model_configuration": _MODEL,
                },
                "operating_point": {
                    "source": "eda_result",
                    "device_values": {
                        "MN1": {
                            "ids_a": 8e-6,
                            "gm_s": gm,
                            "gds_s": input_gds,
                        },
                        "MP1": {"ids_a": -8e-6, "gds_s": load_gds},
                    },
                    "node_values_v": {"OUTP": 0.5, "OUTN": 0.5, "TAIL": 0.2},
                    "raw_files": {
                        "dc": {"sha256": _HASH},
                        "operating_point": {"sha256": _HASH},
                    },
                },
                "ac_response": {
                    "source": "eda_result",
                    "analysis_complete": True,
                    "issues": [],
                },
            }
        },
    }
    return candidate, action


def _run(points: list[tuple[float, float]]) -> dict:
    pairs = [_candidate(index, *point) for index, point in enumerate(points, 1)]
    return {
        "status": "succeeded",
        "adapter": "virtuoso-bridge-subprocess",
        "candidates": [pair[0] for pair in pairs],
        "actions": [pair[1] for pair in pairs],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def calibration_runs(tmp_path: Path) -> tuple[Path, Path]:
    training = _run(
        [
            (1.5, 1.5),
            (1.5, 2.0),
            (1.5, 2.5),
            (2.0, 1.5),
            (2.0, 2.0),
            (2.0, 2.5),
        ]
    )
    validation = _run([(1.75, 2.25)])
    return (
        _write(tmp_path / "training.json", training),
        _write(tmp_path / "validation.json", validation),
    )


def test_calibration_recovers_declared_local_model_and_cross_validates(
    calibration_runs: tuple[Path, Path],
) -> None:
    result = calibrate_differential_pair_theory(*calibration_runs)

    assert result.status is RunStatus.SUCCEEDED
    assert result.coefficients.differential_gain_correction_factor == pytest.approx(
        0.92
    )
    assert (
        result.coefficients.input_effective_output_capacitance_f_per_um
        == pytest.approx(0.5e-15)
    )
    assert (
        result.coefficients.load_effective_output_capacitance_f_per_um
        == pytest.approx(0.7e-15)
    )
    assert result.leave_one_out_errors.maximum_absolute_gbw_error_percent < 1e-10
    assert (
        result.independent_validation_errors.maximum_absolute_gbw_error_percent
        < 1e-10
    )
    assert result.applicability["interpolation_only"] is True
    assert result.applicability["validation_relationship"] == (
        "unseen_geometry_inside_training_bounds"
    )
    assert "global optimum" in result.conclusions[-1]


def test_calibration_rejects_demo_or_non_eda_metric_evidence(
    calibration_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    training_path, validation_path = calibration_runs
    training = json.loads(training_path.read_text(encoding="utf-8"))
    training["adapter"] = "demo"
    demo_path = _write(tmp_path / "demo.json", training)
    with pytest.raises(ValueError, match="real Bridge adapter"):
        calibrate_differential_pair_theory(demo_path, validation_path)

    training["adapter"] = "virtuoso-bridge-subprocess"
    training["candidates"][0]["metric_sources"][
        "differential_bandwidth_3db_hz"
    ] = "software_inference"
    inferred_path = _write(tmp_path / "inferred.json", training)
    with pytest.raises(ValueError, match="not eda_result"):
        calibrate_differential_pair_theory(inferred_path, validation_path)


def test_calibration_rejects_validation_extrapolation(
    calibration_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    training_path, _ = calibration_runs
    outside = _write(tmp_path / "outside.json", _run([(2.5, 2.0)]))

    with pytest.raises(ValueError, match="outside the trained width domain"):
        calibrate_differential_pair_theory(training_path, outside)


def test_calibration_rejects_an_incomplete_training_grid(
    calibration_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    _, validation_path = calibration_runs
    incomplete = _write(
        tmp_path / "incomplete.json",
        _run(
            [
                (1.5, 1.5),
                (1.5, 2.0),
                (1.5, 2.5),
                (2.0, 1.5),
                (2.0, 2.0),
            ]
        ),
    )

    with pytest.raises(ValueError, match="full rectangular Wn/Wp grid"):
        calibrate_differential_pair_theory(incomplete, validation_path)


def test_calibration_that_misses_the_error_gate_is_partial(
    calibration_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    training_path, validation_path = calibration_runs
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    metrics = validation["candidates"][0]["metrics"]
    metrics["differential_low_frequency_gain_v_per_v"] *= 1.1
    metrics["differential_gain_bandwidth_product_hz"] = (
        metrics["differential_low_frequency_gain_v_per_v"]
        * metrics["differential_bandwidth_3db_hz"]
    )
    failed_gate = _write(tmp_path / "failed-gate.json", validation)

    result = calibrate_differential_pair_theory(training_path, failed_gate)

    assert result.status is RunStatus.PARTIAL
    assert "must not seed sizing" in result.conclusions[0]


def test_calibration_cli_saves_the_same_auditable_record(
    calibration_runs: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    training, validation = calibration_runs
    output = tmp_path / "calibration.json"

    assert (
        main(
            [
                "theory-calibrate",
                str(training),
                "--validation-run",
                str(validation),
                "--id",
                "test-calibration",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["id"] == "test-calibration"
    assert saved["status"] == "succeeded"
    assert saved["evidence_sources"]["OA topology and geometry"] == (
        "bridge_readback"
    )


def test_calibration_rejects_condition_drift(
    calibration_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    training_path, validation_path = calibration_runs
    validation = copy.deepcopy(
        json.loads(validation_path.read_text(encoding="utf-8"))
    )
    validation["candidates"][0]["parameters"]["common_mode_v"] = 0.6
    drifted = _write(tmp_path / "drifted.json", validation)

    with pytest.raises(ValueError, match="does not match"):
        calibrate_differential_pair_theory(training_path, drifted)
