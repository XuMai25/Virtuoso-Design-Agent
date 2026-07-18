"""Execution-time safety checks independent from the adapter implementation."""

from __future__ import annotations

from .models import ExecutionPlan, TaskSpec


class SafetyViolation(RuntimeError):
    pass


def authorize_execution(task: TaskSpec, plan: ExecutionPlan, token: str) -> None:
    if token != plan.confirmation_token:
        raise SafetyViolation("plan token mismatch; regenerate and review the plan")

    policy = task.safety
    if plan.requires_remote_compute and not policy.allow_remote_compute:
        raise SafetyViolation(
            "task includes remote compute but allow_remote_compute is false"
        )

    if plan.requires_remote_write:
        if not policy.allow_remote_write:
            raise SafetyViolation(
                "task includes remote OA write but allow_remote_write is false"
            )
        if not policy.allowed_library:
            raise SafetyViolation("remote OA write requires allowed_library")
        if task.target.library != policy.allowed_library:
            raise SafetyViolation(
                f"target library {task.target.library!r} does not match "
                f"allowed_library {policy.allowed_library!r}"
            )
        if policy.required_cell_prefix and not task.target.cell.startswith(
            policy.required_cell_prefix
        ):
            raise SafetyViolation(
                f"target cell must start with {policy.required_cell_prefix!r}"
            )
