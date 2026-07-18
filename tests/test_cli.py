from __future__ import annotations

from pathlib import Path

from virtuoso_design_agent.cli import main


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
