from __future__ import annotations

from virtuoso_design_agent.models import SideEffect, TaskSpec
from virtuoso_design_agent.planner import build_plan


def _task(operation: str, **extra) -> TaskSpec:
    data = {
        "id": operation.replace(".", "-"),
        "operation": operation,
        "circuit": "inverter",
        "target": {"library": "vda_test", "cell": "vda_inv"},
        "parameters": {"nmos_width_um": 0.5, "pmos_width_um": 1.0},
    }
    data.update(extra)
    return TaskSpec.model_validate(data)


def test_plan_token_is_stable() -> None:
    task = _task("schematic.create")
    assert build_plan(task).confirmation_token == build_plan(task).confirmation_token


def test_inspect_plan_is_remote_read_only() -> None:
    plan = build_plan(_task("schematic.inspect"))
    assert all(step.side_effect is not SideEffect.REMOTE_WRITE for step in plan.steps)
    assert all(step.side_effect is not SideEffect.REMOTE_COMPUTE for step in plan.steps)


def test_simulation_plan_does_not_write_oa() -> None:
    plan = build_plan(_task("simulation.run"))
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert "netlist.generate" in [step.capability for step in plan.steps]


def test_apply_plan_has_before_and_after_readback() -> None:
    plan = build_plan(_task("parameters.apply"))
    capabilities = [step.capability for step in plan.steps]
    assert capabilities.count("schematic.inspect") == 2
    assert "parameters.apply" in capabilities


def test_tuning_plan_discloses_candidate_oa_staging() -> None:
    plan = build_plan(
        _task(
            "design.tune",
            parameter_space={"nmos_width_um": [0.4, 0.5]},
            constraints=[{"metric": "delay_ps", "relation": "<=", "value": 50}],
        )
    )
    stage = next(step for step in plan.steps if step.capability == "parameters.stage")
    persist = next(step for step in plan.steps if step.capability == "evidence.persist")
    assert stage.side_effect is SideEffect.REMOTE_WRITE
    assert "checkpoint" in persist.description


def test_common_source_simulation_plan_is_dc_op_and_read_only_to_oa() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-dc",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9},
        }
    )
    plan = build_plan(task)
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.run"
    )
    assert "DC operating point" in simulation.description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
