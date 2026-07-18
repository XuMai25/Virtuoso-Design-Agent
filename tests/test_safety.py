from __future__ import annotations

import pytest

from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.safety import SafetyViolation, authorize_execution


def _create_task(**safety_overrides) -> TaskSpec:
    safety = {
        "allow_remote_write": True,
        "allowed_library": "vda_test",
        "required_cell_prefix": "vda_",
    }
    safety.update(safety_overrides)
    return TaskSpec.model_validate(
        {
            "id": "create",
            "operation": "schematic.create",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "safety": safety,
        }
    )


def test_correct_token_and_scope_are_authorized() -> None:
    task = _create_task()
    plan = build_plan(task)
    authorize_execution(task, plan, plan.confirmation_token)


def test_wrong_token_is_blocked() -> None:
    task = _create_task()
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="token mismatch"):
        authorize_execution(task, plan, "wrong")


def test_library_mismatch_is_blocked() -> None:
    task = _create_task(allowed_library="other")
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="does not match"):
        authorize_execution(task, plan, plan.confirmation_token)


def test_cell_prefix_is_blocked() -> None:
    task = _create_task(required_cell_prefix="safe_")
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="must start"):
        authorize_execution(task, plan, plan.confirmation_token)


def test_remote_compute_needs_separate_permission() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "simulate",
            "operation": "simulation.run",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"nmos_width_um": 0.5, "pmos_width_um": 1.0},
        }
    )
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="remote compute"):
        authorize_execution(task, plan, plan.confirmation_token)
