from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from virtuoso_design_agent.characterization_seed import (
    DeviceCharacterizationGridTemplate,
    derive_device_characterization_task,
)
from virtuoso_design_agent.cli import main


_NOW = "2026-07-24T00:00:00Z"


def _template() -> dict:
    return {
        "schema_version": 1,
        "id": "test-n28-grid",
        "pdk_profile": "nics4304_tsmc28",
        "vgs_magnitudes_v": [0.3, 0.4, 0.5],
        "vds_magnitudes_v": [0.15, 0.3, 0.45],
        "vsb_magnitudes_v": [0.0, 0.075, 0.15],
        "holdout_biases": [
            {
                "vgs_magnitude_v": 0.35,
                "vds_magnitude_v": 0.375,
                "vsb_magnitude_v": 0.075,
            }
        ],
        "temperature_c": 27.0,
        "maximum_holdout_normalized_error": 0.25,
        "timeout_seconds": 900,
    }


def _circuit_run() -> dict:
    result = {
        "parameters": {"bias_v": 0.4, "vdd_v": 0.9},
        "metrics": {},
        "metric_sources": {},
        "analysis_complete": True,
        "analysis_issues": [],
        "analysis_warnings": [],
        "tool_version": "test-spectre",
        "evidence": {
            "side_effects": {
                "oa_access_performed": True,
                "oa_write_performed": False,
                "remote_compute_performed": True,
            },
            "netlist": {
                "source": "eda_result",
                "sha256": "b" * 64,
                "parameter_consistency": "matched",
                "topology_variant": "source_degenerated_common_source",
                "instances": {
                    "MN0": {
                        "nodes": ["OUT", "IN", "NSRC", "VSS"],
                        "model": "nch_lvt_mac",
                        "netlist_width_um": 1.0,
                        "finger_width_um": 1.0,
                        "fingers": 1.0,
                        "multiplicity": 1.0,
                        "total_width_um": 1.0,
                        "length_um": 0.03,
                        "model_parameters": {
                            "ad": "7.5e-14",
                            "dfm_flag": "0",
                        },
                        "unparsed_model_parameter_tokens": [],
                    },
                    "RS0": {
                        "nodes": ["NSRC", "VSS"],
                        "model": "resistor",
                        "resistance_ohm": 2_000.0,
                    },
                },
            },
            "testbench": {
                "model_configuration": {
                    "profile": "nics4304_tsmc28",
                    "process_corner": "top_tt",
                    "temperature_c": 27.0,
                }
            },
        },
    }
    return {
        "schema_version": 1,
        "task_id": "source-degenerated-circuit",
        "plan_token": "token",
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": _NOW,
        "finished_at": _NOW,
        "actions": [
            {
                "action": "simulation.candidate.1",
                "status": "succeeded",
                "started_at": _NOW,
                "finished_at": _NOW,
                "evidence_source": "eda_result",
                "details": {
                    "analysis_complete": True,
                    "analysis_issues": [],
                    "operating_condition_results": [
                        {
                            "condition": {
                                "name": "top_tt_27c_0p90v",
                                "process_corner": "top_tt",
                                "temperature_c": 27.0,
                                "vdd_v": 0.9,
                            },
                            "result": result,
                        }
                    ],
                },
            }
        ],
    }


def _write_run(tmp_path: Path, payload: dict | None = None) -> Path:
    path = tmp_path / "circuit.json"
    path.write_text(json.dumps(payload or _circuit_run()), encoding="utf-8")
    return path


def test_characterization_task_is_derived_from_exact_real_si_signature(
    tmp_path: Path,
) -> None:
    run_path = _write_run(tmp_path)
    task = derive_device_characterization_task(
        DeviceCharacterizationGridTemplate.model_validate(_template()),
        run_path,
        task_id="derived-mn0-characterization",
        instance_name="MN0",
        polarity="nmos",
        operating_condition_name="top_tt_27c_0p90v",
    )

    settings = task.device_characterization
    assert settings is not None
    assert settings.width_um == 1.0
    assert settings.lengths_um == [0.03]
    assert settings.model_parameters_by_polarity == {
        "nmos": {"ad": "7.5e-14", "dfm_flag": "0"}
    }
    assert settings.holdout_points[0].length_um == 0.03
    binding = settings.source_instance_binding
    assert binding is not None
    assert binding.source_run_sha256 == hashlib.sha256(run_path.read_bytes()).hexdigest()
    assert binding.source_netlist_sha256 == "b" * 64
    assert binding.source_topology_variant == "source_degenerated_common_source"
    assert binding.source_model_parameter_count == 2
    assert task.safety.allow_remote_compute is True
    assert task.safety.allow_remote_write is False
    assert task.safety.replace_existing is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("demo", "real Bridge run"),
        ("unparsed", "not fully parsed"),
        ("multi_finger", "fingers=1"),
        ("temperature", "temperature does not match"),
        ("write", r"read-only OA \+ compute"),
        ("incomplete", "analysis is incomplete"),
    ],
)
def test_characterization_task_derivation_rejects_unsafe_or_ambiguous_sources(
    tmp_path: Path, mutation: str, message: str
) -> None:
    circuit = _circuit_run()
    result = circuit["actions"][0]["details"]["operating_condition_results"][0][
        "result"
    ]
    if mutation == "demo":
        circuit["adapter"] = "demo"
    elif mutation == "unparsed":
        result["evidence"]["netlist"]["instances"]["MN0"][
            "unparsed_model_parameter_tokens"
        ] = ["opaque=(foo"]
    elif mutation == "multi_finger":
        result["evidence"]["netlist"]["instances"]["MN0"]["fingers"] = 2.0
    elif mutation == "temperature":
        result["evidence"]["testbench"]["model_configuration"][
            "temperature_c"
        ] = 85.0
    elif mutation == "write":
        result["evidence"]["side_effects"]["oa_write_performed"] = True
    else:
        result["analysis_complete"] = False
    run_path = _write_run(tmp_path, circuit)

    with pytest.raises(ValueError, match=message):
        derive_device_characterization_task(
            DeviceCharacterizationGridTemplate.model_validate(_template()),
            run_path,
            task_id="derived-mn0-characterization",
            instance_name="MN0",
            polarity="nmos",
            operating_condition_name="top_tt_27c_0p90v",
        )


def test_characterization_task_cli_writes_a_runnable_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(_template()), encoding="utf-8")
    run_path = _write_run(tmp_path)
    output_path = tmp_path / "task.json"

    assert (
        main(
            [
                "characterization-task-from-run",
                str(template_path),
                str(run_path),
                "--id",
                "derived-mn0-characterization",
                "--instance",
                "MN0",
                "--polarity",
                "nmos",
                "--operating-condition",
                "top_tt_27c_0p90v",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["operation"] == "device.characterize"
    assert saved["device_characterization"]["source_instance_binding"][
        "source_instance"
    ] == "MN0"
