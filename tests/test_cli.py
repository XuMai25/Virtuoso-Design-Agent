from __future__ import annotations

import json
from pathlib import Path

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.executor import load_execution_checkpoint
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]
DEMO_TASK = ROOT / "examples" / "tasks" / "inverter-close-loop.demo.json"


def test_catalog_marks_gate2a_common_source_executable(capsys) -> None:
    assert main(["catalog"]) == 0
    output = capsys.readouterr().out
    assert "existing_schematic: Bridge-preserving manual OA surface [executable]" in output
    assert "inverter: L5A vertical slice [executable]" in output
    assert (
        "common_source: Gate 2 bounded topology and raw-parameter tuning verified "
        "[executable]" in output
    )
    assert output.count(
        "explicit instance parameters: parameters.apply + bounded tuning + OA readback"
    ) == 3
    assert output.count(
        "explicit instance parameters: parameters.apply + OA readback"
    ) == 1
    assert (
        "differential_pair: Gate 4 real-tail multi-analysis same-source verified "
        "[executable]" in output
    )


def test_plan_prints_confirmation_token(capsys) -> None:
    assert main(["plan", str(DEMO_TASK)]) == 0
    output = capsys.readouterr().out
    assert "Plan token:" in output
    assert "remote_write" in output
    assert "remote_compute" in output


def test_run_without_execute_is_plan_only(capsys) -> None:
    assert main(["run", str(DEMO_TASK), "--adapter", "demo"]) == 0
    output = capsys.readouterr().out
    assert "No action executed" in output


def test_tuning_run_creates_complete_checkpoint_by_default(tmp_path, capsys) -> None:
    task = TaskSpec.model_validate_json(DEMO_TASK.read_text(encoding="utf-8"))
    plan = build_plan(task)
    output = tmp_path / "run.json"

    assert (
        main(
            [
                "run",
                str(DEMO_TASK),
                "--adapter",
                "demo",
                "--execute",
                "--token",
                plan.confirmation_token,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    checkpoint = output.with_name("run.checkpoint.json")
    assert output.is_file()
    assert load_execution_checkpoint(checkpoint).complete is True
    assert f"Checkpoint: {checkpoint.resolve()}" in capsys.readouterr().out


def test_cli_prints_selected_raw_instance_parameters(tmp_path, capsys) -> None:
    task_path = tmp_path / "raw-close-loop.json"
    task_payload = {
        "id": "raw-close-loop-cli",
        "operation": "design.close_loop",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_raw_cli"},
        "parameters": {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 10_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
        },
        "instance_parameter_space": [
            {
                "instance": "MN0",
                "parameter": "fingers",
                "values": ["1", "2"],
            }
        ],
        "constraints": [
            {"metric": "drain_current_ua", "relation": ">=", "value": 0.0}
        ],
        "objective": {"metric": "drain_current_ua", "goal": "maximize"},
        "create_if_missing": True,
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
        "limits": {"max_iterations": 2, "timeout_seconds": 600},
    }
    task_path.write_text(json.dumps(task_payload), encoding="utf-8")
    task = TaskSpec.model_validate(task_payload)
    plan = build_plan(task)

    assert (
        main(
            [
                "run",
                str(task_path),
                "--adapter",
                "demo",
                "--execute",
                "--token",
                plan.confirmation_token,
                "--output",
                str(tmp_path / "raw-run.json"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert 'Selected instance parameters: {"MN0": {"fingers": "1"}}' in output
