from __future__ import annotations

from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan


def _close_loop(delay_limit: float = 45.0) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "loop",
            "operation": "design.close_loop",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"length_um": 0.03, "load_ff": 2.0, "vdd_v": 0.9},
            "parameter_space": {
                "nmos_width_um": [0.4, 0.5, 0.6],
                "pmos_width_um": [0.8, 1.0, 1.2],
            },
            "constraints": [
                {"metric": "delay_ps", "relation": "<=", "value": delay_limit},
                {"metric": "rise_fall_skew_ps", "relation": "<=", "value": 8},
            ],
            "objective": {"metric": "delay_ps", "goal": "minimize"},
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
            },
            "limits": {"max_iterations": 9, "timeout_seconds": 600},
        }
    )


def test_demo_close_loop_selects_and_applies_feasible_candidate() -> None:
    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 9
    assert record.selected_parameters is not None
    assert record.selected_parameters["nmos_width_um"] == 0.6
    assert record.selected_parameters["pmos_width_um"] == 1.2
    assert any(action.action == "parameters.apply.best" for action in record.actions)
    assert all(
        candidate.evidence_source.value == "software_inference"
        for candidate in record.candidates
    )


def test_infeasible_search_does_not_write_best_attempt_to_oa() -> None:
    task = _close_loop(delay_limit=1.0)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert record.selected_parameters is not None
    assert not any(action.action == "parameters.apply.best" for action in record.actions)
    assert "no parameters were written back" in record.notes[0]


def test_missing_objective_metric_blocks_writeback() -> None:
    data = _close_loop().model_dump(mode="json")
    data["objective"] = {"metric": "missing_energy_fj", "goal": "minimize"}
    task = TaskSpec.model_validate(data)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert not any(action.action == "parameters.apply.best" for action in record.actions)


def test_partial_candidate_failure_downgrades_run_status() -> None:
    class FlakyAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            if parameters["nmos_width_um"] == 0.4:
                raise RuntimeError("injected candidate failure")
            return super().simulate(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(FlakyAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert any("candidate simulation(s) failed" in note for note in record.notes)
    assert any(action.action == "parameters.apply.best" for action in record.actions)
