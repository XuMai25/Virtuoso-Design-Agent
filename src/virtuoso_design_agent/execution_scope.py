"""Compile a disabled TaskSpec into its minimum executable safety scope."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .models import (
    CircuitKind,
    DesignTarget,
    Operation,
    SideEffect,
    TaskSpec,
)
from .planner import build_plan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExecutionScopeRemoteStep(_StrictModel):
    capability: StrictStr = Field(min_length=1)
    side_effect: Literal["remote_compute", "remote_write"]


class ExecutionScopeCompilation(_StrictModel):
    """Exact handoff from a disabled task to a token awaiting confirmation."""

    schema_version: Literal[1] = 1
    status: Literal["ready_for_confirmation"] = "ready_for_confirmation"
    source_task_id: StrictStr
    source_task_file_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_canonical_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_plan_token: StrictStr
    target: DesignTarget | None = None
    pdk_profile: StrictStr
    operation: Operation
    circuit: CircuitKind
    remote_steps: list[ExecutionScopeRemoteStep] = Field(min_length=1)
    allow_remote_compute: bool
    allow_remote_write: bool
    allowed_library: StrictStr | None = None
    required_cell_prefix: StrictStr
    replace_existing: Literal[False] = False
    execution_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    execution_plan_token: StrictStr
    user_confirmation_required: Literal[True] = True
    execution_performed: Literal[False] = False
    evidence_sources: dict[StrictStr, Literal["software_inference"]]


def canonical_task_bytes(task: TaskSpec) -> bytes:
    return (task.model_dump_json(indent=2, exclude_none=True) + "\n").encode(
        "utf-8"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def compile_execution_scope(
    source_task_path: Path,
) -> tuple[TaskSpec, ExecutionScopeCompilation]:
    """Enable exactly the remote side effects already present in a task plan.

    This compiler does not execute the task and does not record user approval.
    The returned plan token is the object that must be reviewed and confirmed.
    """

    try:
        source_bytes = source_task_path.read_bytes()
        source_task = TaskSpec.model_validate_json(source_bytes)
    except OSError as exc:
        raise ValueError(f"cannot read disabled task {source_task_path}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError("execution-scope source is not a valid TaskSpec") from exc

    if (
        source_task.safety.allow_remote_compute
        or source_task.safety.allow_remote_write
    ):
        raise ValueError(
            "execution-scope source must disable remote compute and OA write"
        )
    if source_task.safety.replace_existing:
        raise ValueError("execution-scope refuses replace_existing tasks")

    source_plan = build_plan(source_task)
    remote_steps = [
        ExecutionScopeRemoteStep(
            capability=step.capability,
            side_effect=step.side_effect.value,
        )
        for step in source_plan.steps
        if step.side_effect in {
            SideEffect.REMOTE_COMPUTE,
            SideEffect.REMOTE_WRITE,
        }
    ]
    if not remote_steps:
        raise ValueError("task plan has no remote compute or OA write to enable")

    task_payload = source_task.model_dump(mode="json", exclude_none=True)
    task_payload["safety"]["allow_remote_compute"] = (
        source_plan.requires_remote_compute
    )
    task_payload["safety"]["allow_remote_write"] = source_plan.requires_remote_write
    execution_task = TaskSpec.model_validate(task_payload)
    execution_plan = build_plan(execution_task)
    if (
        execution_plan.requires_remote_compute
        != execution_task.safety.allow_remote_compute
        or execution_plan.requires_remote_write
        != execution_task.safety.allow_remote_write
    ):
        raise ValueError("compiled execution safety does not match planned side effects")

    canonical_source = canonical_task_bytes(source_task)
    execution_bytes = canonical_task_bytes(execution_task)
    compilation = ExecutionScopeCompilation(
        source_task_id=source_task.id,
        source_task_file_sha256=_sha256(source_bytes),
        source_task_canonical_sha256=_sha256(canonical_source),
        source_plan_token=source_plan.confirmation_token,
        target=source_task.target,
        pdk_profile=source_task.pdk_profile,
        operation=source_task.operation,
        circuit=source_task.circuit,
        remote_steps=remote_steps,
        allow_remote_compute=execution_task.safety.allow_remote_compute,
        allow_remote_write=execution_task.safety.allow_remote_write,
        allowed_library=execution_task.safety.allowed_library,
        required_cell_prefix=execution_task.safety.required_cell_prefix,
        execution_task_sha256=_sha256(execution_bytes),
        execution_plan_token=execution_plan.confirmation_token,
        evidence_sources={
            "source_task_and_plan": "software_inference",
            "plan_remote_step_requirements": "software_inference",
            "minimal_scope_compilation": "software_inference",
        },
    )
    return execution_task, compilation


__all__ = [
    "ExecutionScopeCompilation",
    "ExecutionScopeRemoteStep",
    "canonical_task_bytes",
    "compile_execution_scope",
]
