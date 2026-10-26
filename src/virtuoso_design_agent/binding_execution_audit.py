"""Audit one promoted-binding shared-netlist execution without rerunning EDA."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from .binding_promotion import OnboardingBindingPromotionCompilation
from .execution_scope import ExecutionScopeCompilation, canonical_task_bytes
from .metrics import evaluate_constraints
from .models import (
    AnalysisKind,
    AnalysisStageExecution,
    CircuitKind,
    ConstraintEvaluation,
    DesignTarget,
    EvidenceSource,
    Operation,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from .planner import build_plan
from .spectre_values import spectre_values_equal


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BindingExecutionValueCheck(_StrictModel):
    instance: StrictStr
    oa_parameter: StrictStr
    netlist_parameter: StrictStr
    oa_value: StrictStr
    netlist_value: StrictStr


class BindingExecutionStageAudit(_StrictModel):
    stage_id: StrictStr
    analysis: AnalysisKind
    analysis_complete: Literal[True] = True
    artifact_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_file_count: StrictInt = Field(ge=1)
    metric_names: list[StrictStr]


class OnboardingBindingExecutionAudit(_StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["passed"] = "passed"
    promotion_compilation_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    execution_scope_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    execution_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: StrictStr
    plan_token: StrictStr
    target: DesignTarget
    pdk_profile: StrictStr
    topology_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    netlist_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    remote_netlist_path: StrictStr
    schematic_readback_count: Literal[1] = 1
    netlist_generation_count: Literal[1] = 1
    primary_bindings: list[BindingExecutionValueCheck] = Field(min_length=1)
    derived_callbacks: list[BindingExecutionValueCheck]
    stages: list[BindingExecutionStageAudit] = Field(min_length=1)
    selected_metrics: dict[StrictStr, StrictFloat]
    constraints: list[ConstraintEvaluation]
    oa_write_performed: Literal[False] = False
    remote_compute_performed: Literal[True] = True
    evidence_sources: dict[
        StrictStr,
        Literal[
            "eda_result",
            "bridge_readback",
            "software_inference",
            "user_input",
            "system_event",
        ],
    ]


def _read_bytes(path: Path, *, kind: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {kind} {path}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _runtime_binding_checks(
    raw_checks: Any,
    expected: dict[tuple[str, str, str], Any],
    *,
    kind: str,
) -> list[BindingExecutionValueCheck]:
    if not isinstance(raw_checks, list):
        raise ValueError(f"binding execution lacks {kind} checks")
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in raw_checks:
        if not isinstance(raw, dict):
            raise ValueError(f"binding execution has malformed {kind} evidence")
        key = (
            str(raw.get("instance", "")),
            str(raw.get("oa_parameter", "")),
            str(raw.get("netlist_parameter", "")),
        )
        if key in indexed:
            raise ValueError(f"binding execution repeats a {kind} check: {key!r}")
        indexed[key] = raw

    missing = sorted(set(expected) - set(indexed))
    if missing:
        raise ValueError(f"binding execution omitted {kind} checks: {missing!r}")
    checks: list[BindingExecutionValueCheck] = []
    for key in sorted(expected):
        raw = indexed[key]
        oa_value = str(raw.get("oa_value", ""))
        netlist_value = str(raw.get("netlist_value", ""))
        if not spectre_values_equal(oa_value, netlist_value):
            raise ValueError(
                f"binding execution {kind} mismatch for {key!r}: "
                f"{oa_value!r} != {netlist_value!r}"
            )
        checks.append(
            BindingExecutionValueCheck(
                instance=key[0],
                oa_parameter=key[1],
                netlist_parameter=key[2],
                oa_value=oa_value,
                netlist_value=netlist_value,
            )
        )
    return checks


def audit_onboarding_binding_execution(
    promotion_path: Path,
    execution_scope_path: Path,
    execution_task_path: Path,
    run_path: Path,
) -> OnboardingBindingExecutionAudit:
    """Verify the exact discovery→task→token→real-run evidence chain."""

    promotion_bytes = _read_bytes(promotion_path, kind="binding promotion record")
    scope_bytes = _read_bytes(execution_scope_path, kind="execution scope record")
    task_bytes = _read_bytes(execution_task_path, kind="execution task")
    run_bytes = _read_bytes(run_path, kind="run record")
    try:
        promotion = OnboardingBindingPromotionCompilation.model_validate_json(
            promotion_bytes
        )
        scope = ExecutionScopeCompilation.model_validate_json(scope_bytes)
        task = TaskSpec.model_validate_json(task_bytes)
        run = RunRecord.model_validate_json(run_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("binding execution audit input is invalid") from exc

    if (
        promotion.compiled_task_id != scope.source_task_id
        or promotion.compiled_task_sha256 != scope.source_task_file_sha256
        or promotion.compiled_plan_token != scope.source_plan_token
    ):
        raise ValueError("execution scope is not bound to the promotion output")
    if (
        _sha256(task_bytes) != scope.execution_task_sha256
        or _sha256(canonical_task_bytes(task)) != scope.execution_task_sha256
    ):
        raise ValueError("execution task bytes differ from the scoped task hash")
    plan = build_plan(task)
    if (
        task.id != scope.source_task_id
        or plan.confirmation_token != scope.execution_plan_token
        or task.operation is not Operation.SIMULATION_RUN
        or task.circuit is not CircuitKind.EXISTING_SCHEMATIC
        or task.target is None
        or task.generic_simulation is None
        or task.design_context is None
        or task.analysis_stage_execution is not AnalysisStageExecution.SHARED_NETLIST
        or not task.analysis_stages
    ):
        raise ValueError(
            "binding execution audit requires the exact shared-netlist simulation task"
        )
    if (
        not task.safety.allow_remote_compute
        or task.safety.allow_remote_write
        or task.safety.replace_existing
        or not scope.allow_remote_compute
        or scope.allow_remote_write
    ):
        raise ValueError("binding execution scope is not compute-only and non-replacing")
    if (
        run.task_id != task.id
        or run.plan_token != plan.confirmation_token
        or run.adapter != "virtuoso-bridge-subprocess"
        or run.status is not RunStatus.SUCCEEDED
        or any(action.status != "succeeded" for action in run.actions)
    ):
        raise ValueError("binding execution run is not a successful exact-plan Bridge run")

    promoted = {
        (
            evidence.binding.instance,
            evidence.binding.oa_parameter,
            evidence.binding.netlist_parameter,
        ): evidence.binding
        for evidence in promotion.promoted_bindings
    }
    task_bindings = {
        (binding.instance, binding.oa_parameter, binding.netlist_parameter): binding
        for binding in task.generic_simulation.netlist_parameter_bindings
    }
    for key, binding in promoted.items():
        if task_bindings.get(key) != binding:
            raise ValueError(f"promoted binding changed in execution task: {key!r}")

    simulation_actions = [
        action
        for action in run.actions
        if isinstance(action.details.get("shared_netlist"), dict)
    ]
    if len(simulation_actions) != 1:
        raise ValueError("binding execution requires exactly one shared-netlist action")
    action = simulation_actions[0]
    if action.evidence_source is not EvidenceSource.EDA_RESULT:
        raise ValueError("shared-netlist action is not EDA evidence")
    details = action.details
    shared = details["shared_netlist"]
    expected_stage_ids = [stage.id for stage in task.analysis_stages]
    if (
        shared.get("source") != "software_inference"
        or shared.get("schematic_readback_count") != 1
        or shared.get("netlist_generation_count") != 1
        or shared.get("executed_stages") != expected_stage_ids
        or shared.get("reuse_contract")
        != "one_worker_one_oa_readback_one_si_netlist"
        or details.get("terminated_after_stage") is not None
    ):
        raise ValueError("shared-netlist execution contract is incomplete")

    evidence = details.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("shared-netlist action omitted evidence")
    schematic = evidence.get("schematic_readback")
    netlist = evidence.get("netlist")
    if not isinstance(schematic, dict) or not isinstance(netlist, dict):
        raise ValueError("shared-netlist action omitted OA or si evidence")
    topology_sha256 = task.design_context.expected_topology_sha256
    if (
        topology_sha256 is None
        or schematic.get("source") != "bridge_readback"
        or schematic.get("topology_sha256") != topology_sha256
        or netlist.get("source") != "eda_result"
        or netlist.get("topology_consistency") != "matched"
        or netlist.get("parameter_consistency") != "matched"
    ):
        raise ValueError("runtime OA/si topology or primary parameter evidence failed")
    netlist_sha256 = netlist.get("sha256")
    remote_path = netlist.get("remote_path")
    if (
        not _valid_sha256(netlist_sha256)
        or not isinstance(remote_path, str)
        or not remote_path.startswith("/data/xum/")
        or not remote_path.endswith("/netlist")
        or shared.get("sha256") != netlist_sha256
        or shared.get("remote_path") != remote_path
    ):
        raise ValueError("runtime si netlist identity is invalid")

    primary_expected = {key: binding for key, binding in promoted.items()}
    primary_checks = _runtime_binding_checks(
        netlist.get("parameter_bindings"),
        primary_expected,
        kind="primary binding",
    )
    callback_expected: dict[tuple[str, str, str], Any] = {}
    for binding in promoted.values():
        for callback in binding.derived_callbacks or []:
            callback_expected[
                (
                    binding.instance,
                    callback.oa_parameter,
                    callback.netlist_parameter,
                )
            ] = callback
    raw_callbacks = netlist.get("derived_callback_bindings")
    if callback_expected and netlist.get("derived_callback_consistency") != "matched":
        raise ValueError("runtime derived callback consistency is not matched")
    callback_checks = _runtime_binding_checks(
        raw_callbacks or [],
        callback_expected,
        kind="derived callback",
    )

    raw_stages = details.get("stage_results")
    if not isinstance(raw_stages, list) or len(raw_stages) != len(task.analysis_stages):
        raise ValueError("shared-netlist stage results are incomplete")
    stage_audits: list[BindingExecutionStageAudit] = []
    for declared, raw_stage in zip(task.analysis_stages, raw_stages, strict=True):
        gate_evaluations = (
            raw_stage.get("gate_evaluations")
            if isinstance(raw_stage, dict)
            else None
        )
        if (
            not isinstance(raw_stage, dict)
            or raw_stage.get("stage_id") != declared.id
            or raw_stage.get("analysis") != declared.analysis.value
            or raw_stage.get("gate_evidence_source") != "software_inference"
            or not isinstance(gate_evaluations, list)
            or any(
                not isinstance(item, dict) or item.get("passed") is not True
                for item in gate_evaluations
            )
        ):
            raise ValueError(f"stage gate failed or drifted: {declared.id}")
        result = raw_stage.get("result")
        if (
            not isinstance(result, dict)
            or result.get("analysis_complete") is not True
            or result.get("analysis_issues") != []
        ):
            raise ValueError(f"analysis stage is incomplete: {declared.id}")
        result_evidence = result.get("evidence")
        simulation = (
            result_evidence.get("simulation")
            if isinstance(result_evidence, dict)
            else None
        )
        side_effects = (
            result_evidence.get("side_effects")
            if isinstance(result_evidence, dict)
            else None
        )
        if (
            not isinstance(simulation, dict)
            or simulation.get("source") != "eda_result"
            or simulation.get("artifact_manifest_complete") is not True
            or not isinstance(simulation.get("artifact_manifest"), list)
            or not simulation["artifact_manifest"]
            or not _valid_sha256(simulation.get("artifact_manifest_sha256"))
            or not isinstance(side_effects, dict)
            or side_effects.get("oa_access_performed") is not True
            or side_effects.get("oa_write_performed") is not False
            or side_effects.get("remote_compute_performed") is not True
        ):
            raise ValueError(f"stage evidence or side effects are invalid: {declared.id}")
        metrics = result.get("metrics")
        if not isinstance(metrics, dict) or any(
            not isinstance(value, (int, float)) or not math.isfinite(float(value))
            for value in metrics.values()
        ):
            raise ValueError(f"stage metrics are invalid: {declared.id}")
        stage_audits.append(
            BindingExecutionStageAudit(
                stage_id=declared.id,
                analysis=declared.analysis,
                artifact_manifest_sha256=simulation["artifact_manifest_sha256"],
                artifact_file_count=len(simulation["artifact_manifest"]),
                metric_names=sorted(str(name) for name in metrics),
            )
        )

    selected_metrics = {
        str(name): float(value) for name, value in run.selected_metrics.items()
    }
    constraints = evaluate_constraints(selected_metrics, task.constraints)
    if not constraints or any(not item.passed for item in constraints):
        raise ValueError("selected metrics do not independently pass task constraints")

    return OnboardingBindingExecutionAudit(
        promotion_compilation_sha256=_sha256(promotion_bytes),
        execution_scope_sha256=_sha256(scope_bytes),
        execution_task_sha256=_sha256(task_bytes),
        run_sha256=_sha256(run_bytes),
        task_id=task.id,
        plan_token=plan.confirmation_token,
        target=task.target,
        pdk_profile=task.pdk_profile,
        topology_sha256=topology_sha256,
        netlist_sha256=netlist_sha256,
        remote_netlist_path=remote_path,
        primary_bindings=primary_checks,
        derived_callbacks=callback_checks,
        stages=stage_audits,
        selected_metrics=selected_metrics,
        constraints=constraints,
        evidence_sources={
            "task_intent_and_constraints": "user_input",
            "oa_topology_and_parameters": "bridge_readback",
            "si_netlist_waveforms_and_manifests": "eda_result",
            "binding_stage_and_constraint_audit": "software_inference",
        },
    )


__all__ = [
    "BindingExecutionStageAudit",
    "BindingExecutionValueCheck",
    "OnboardingBindingExecutionAudit",
    "audit_onboarding_binding_execution",
]
