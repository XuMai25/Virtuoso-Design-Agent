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


def test_apply_plan_has_before_and_after_readback() -> None:
    plan = build_plan(_task("parameters.apply"))
    capabilities = [step.capability for step in plan.steps]
    assert capabilities.count("schematic.inspect") == 2
    assert "parameters.apply" in capabilities
