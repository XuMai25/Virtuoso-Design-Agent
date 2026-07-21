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


def test_background_ade_run_needs_compute_but_not_oa_write_permission() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "any_library",
                "cell": "existing_tb",
                "view": "maestro",
            },
            "ade_run": {},
        }
    )
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="remote compute"):
        authorize_execution(task, plan, plan.confirmation_token)

    authorized = task.model_copy(
        update={"safety": task.safety.model_copy(update={"allow_remote_compute": True})}
    )
    authorized_plan = build_plan(authorized)
    authorize_execution(
        authorized, authorized_plan, authorized_plan.confirmation_token
    )


def test_ade_variable_patch_requires_full_oa_write_scope() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-variables",
            "operation": "ade.variables.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "updates": [
                    {"name": "bias_v", "expected_value": None, "value": "0.35"}
                ],
            },
            "safety": {"allowed_library": "vda_test"},
        }
    )
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="remote OA write"):
        authorize_execution(task, plan, plan.confirmation_token)

    authorized = task.model_copy(
        update={"safety": task.safety.model_copy(update={"allow_remote_write": True})}
    )
    authorized_plan = build_plan(authorized)
    authorize_execution(
        authorized, authorized_plan, authorized_plan.confirmation_token
    )


def test_in_place_transform_still_requires_explicit_remote_write_permission() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "transform",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": 1_000.0},
            "safety": {"allowed_library": "vda_test"},
        }
    )
    plan = build_plan(task)
    with pytest.raises(SafetyViolation, match="remote OA write"):
        authorize_execution(task, plan, plan.confirmation_token)
