from __future__ import annotations

from pathlib import Path

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.executor import load_execution_checkpoint
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]
DEMO_TASK = ROOT / "examples" / "tasks" / "inverter-close-loop.demo.json"


def test_catalog_marks_only_inverter_executable(capsys) -> None:
    assert main(["catalog"]) == 0
    output = capsys.readouterr().out
    assert "inverter: L5A vertical slice [executable]" in output
    assert "differential_pair: Gate 3 [planned]" in output


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
