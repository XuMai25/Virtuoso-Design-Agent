"""Compile a task contract into a stable, reviewable execution plan."""

from __future__ import annotations

import hashlib
import json

from .catalog import validate_task_capability
from .models import ExecutionPlan, Operation, PlanStep, SideEffect, TaskSpec


def _step(
    step_id: str, capability: str, description: str, side_effect: SideEffect
) -> PlanStep:
    return PlanStep(
        id=step_id,
        capability=capability,
        description=description,
        side_effect=side_effect,
    )


def _steps_for(task: TaskSpec) -> list[PlanStep]:
    probe = _step(
        "01-probe",
        "bridge.probe",
        "确认 Bridge、Virtuoso SKILL channel 与目标 profile 可用",
        SideEffect.READ_ONLY,
    )
    inspect = _step(
        "inspect",
        "schematic.inspect",
        "结构化回读实例、网络、pins 与参数",
        SideEffect.READ_ONLY,
    )
    persist = _step(
        "persist",
        "evidence.persist",
        "把动作、判定和证据写入本地 run record",
        SideEffect.LOCAL_WRITE,
    )

    if task.operation is Operation.SCHEMATIC_CREATE:
        return [
            probe,
            _step(
                "02-create",
                "schematic.create",
                "按受控模板创建反相器 schematic",
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "03-inspect"}),
            persist.model_copy(update={"id": "04-persist"}),
        ]
    if task.operation is Operation.SCHEMATIC_INSPECT:
        return [
            probe,
            inspect.model_copy(update={"id": "02-inspect"}),
            persist.model_copy(update={"id": "03-persist"}),
        ]
    if task.operation is Operation.PARAMETERS_APPLY:
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-apply",
                "parameters.apply",
                "应用明确给出的器件参数",
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "04-after"}),
            persist.model_copy(update={"id": "05-persist"}),
        ]
    if task.operation is Operation.SIMULATION_RUN:
        return [
            probe,
            _step(
                "02-simulate",
                "simulation.run",
                "运行一个候选点的 Spectre transient",
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "03-evaluate",
                "results.evaluate",
                "从波形指标逐条判断规格",
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(update={"id": "04-persist"}),
        ]
    if task.operation is Operation.DESIGN_TUNE:
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-sweep",
                "simulation.sweep",
                "在 max_iterations 内运行有限参数候选",
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "04-select",
                "results.select",
                "按规格违例与 objective 选择候选",
                SideEffect.READ_ONLY,
            ),
            _step(
                "05-apply-best",
                "parameters.apply",
                "把最佳候选应用回 schematic",
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "06-after"}),
            persist.model_copy(update={"id": "07-persist"}),
        ]
    return [
        probe,
        _step(
            "02-create-or-verify",
            "schematic.ensure",
            "创建缺失 schematic，或验证已有 topology",
            SideEffect.REMOTE_WRITE,
        ),
        _step(
            "03-sweep",
            "simulation.sweep",
            "在 max_iterations 内运行有限参数候选",
            SideEffect.REMOTE_COMPUTE,
        ),
        _step(
            "04-select",
            "results.select",
            "按规格违例与 objective 选择候选",
            SideEffect.READ_ONLY,
        ),
        _step(
            "05-apply-best",
            "parameters.apply",
            "把最佳候选应用回 schematic",
            SideEffect.REMOTE_WRITE,
        ),
        inspect.model_copy(update={"id": "06-after"}),
        persist.model_copy(update={"id": "07-persist"}),
    ]


def build_plan(task: TaskSpec) -> ExecutionPlan:
    validate_task_capability(task)
    steps = _steps_for(task)
    payload = {
        "task": task.model_dump(mode="json", exclude_none=True),
        "steps": [step.model_dump(mode="json") for step in steps],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    token = hashlib.sha256(canonical).hexdigest()[:16]
    return ExecutionPlan(
        task_id=task.id,
        operation=task.operation,
        circuit=task.circuit,
        steps=steps,
        confirmation_token=token,
    )
