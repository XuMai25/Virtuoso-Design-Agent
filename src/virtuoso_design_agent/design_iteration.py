"""Reconstruct guarded design progress from immutable, bound task/run records.

No second executor or mutable database: the request lists the rounds to review.
Every call revalidates the source files and regenerates the state and next draft.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .design_review import build_design_review, file_reference, load_bound_run, read_json, selected_candidate
from .measurements import canonical_sha256, missing_required_measurements
from .metrics import evaluate_constraints
from .models import (DEFAULT_PDK_PROFILE, AnalysisStageSpec, CandidateEvaluation,
                     DesignTarget, MetricConstraint, Objective, RunRecord, TaskSpec)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IterationRound(_Contract):
    task: Path
    run: Path
    candidate_index: int | None = Field(default=None, ge=1)
    checkpoint: Path | None = None
    binding_audit: Path | None = None
    completion: Path | None = None
    artifact_root: Path | None = None


class IterationRequest(_Contract):
    schema_version: Literal[1] = 1
    stages: list[AnalysisStageSpec] = Field(min_length=1, max_length=5)
    rounds: list[IterationRound] = Field(min_length=1, max_length=64)
    max_rounds: int = Field(ge=1, le=64)
    max_action_seconds: float | None = Field(default=None, gt=0)
    stagnation_rounds: int | None = Field(default=None, ge=2, le=64)
    minimum_objective_improvement: float = Field(default=0, ge=0)
    continue_after_feasible: bool = False

    @model_validator(mode="after")
    def unique_stages(self) -> "IterationRequest":
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError("iteration stage ids must be unique")
        if len({s.analysis for s in self.stages}) != len(self.stages):
            raise ValueError("iteration stages cannot repeat an analysis")
        if any(not s.constraint_metrics for s in self.stages[:-1]):
            raise ValueError("every non-final iteration stage requires a protected constraint gate")
        names = [name for stage in self.stages for name in stage.constraint_metrics]
        if len(names) != len(set(names)):
            raise ValueError("iteration constraints must be assigned to one stage only")
        return self


def _resolve(base: Path, path: Path | None) -> Path | None:
    return None if path is None else (path if path.is_absolute() else base / path).resolve()


def _point(run: RunRecord, index: int | None) -> CandidateEvaluation | None:
    if index is not None:
        candidates = [c for c in run.candidates if c.index == index]
        if len(candidates) != 1:
            raise ValueError("iteration candidate index is absent from the bound run")
        return candidates[0]
    selected = selected_candidate(run)
    if selected is not None:
        return selected
    if len(run.candidates) == 1:
        return run.candidates[0]
    if run.candidates:
        raise ValueError("unselected multi-candidate run requires an explicit candidate_index")
    return None


def design_input_fingerprints(task: TaskSpec, candidate: CandidateEvaluation | None) -> dict:
    simulation = task.generic_simulation
    if simulation is not None and candidate is not None and candidate.testbench_overrides:
        simulation = candidate.testbench_overrides.apply_to(simulation)
    topology = (candidate.topology_sha256 if candidate else None) or (
        task.design_context.expected_topology_sha256 if task.design_context else None)
    instances = {u.instance: dict(u.parameters) for u in task.instance_parameter_updates}
    for instance, parameters in (candidate.instance_parameters if candidate else {}).items():
        instances.setdefault(instance, {}).update(parameters)
    facts = {
        "pdk": task.pdk_profile,
        "target": task.target.model_dump(mode="json") if task.target else None,
        "topology": topology,
        "parameters": {
            "semantic": {**task.parameters, **(candidate.parameters if candidate else {})},
            "instance": instances,
        },
        "testbench": simulation.model_dump(mode="json", exclude_none=True) if simulation else None,
        "conditions": [c.model_dump(mode="json") for c in task.operating_conditions],
        "analysis": {
            "kinds": [a.value for a in task.resolved_analyses()],
            "stages": [s.model_dump(mode="json") for s in task.analysis_stages],
            **{name: value.model_dump(mode="json") if value else None
               for name, value in (("ac", task.ac_sweep), ("transient", task.linearity_sweep),
                                   ("noise", task.noise_sweep), ("stability", task.stability_sweep))},
            "winner_verification": task.winner_verification.model_dump(mode="json")
            if task.winner_verification else None,
            "measurement_plan": task.measurement_plan.model_dump(mode="json")
            if task.measurement_plan else None,
        },
    }
    return {"sha256": {name: canonical_sha256(value) for name, value in facts.items()},
            "unverified_dimensions": ["live_OA", "current_PDK_model_files"] +
                                     [name for name in ("topology", "testbench") if facts[name] is None],
            "source": "recorded_inputs", "current_remote_state_verified": False}


def _reconcile_task(task: TaskSpec, number: int) -> TaskSpec | None:
    if task.target is None:
        return None
    return TaskSpec.model_validate({
        "id": f"{task.id}-reconcile-{number}", "operation": "schematic.inspect",
        "circuit": task.circuit.value, "target": task.target.model_dump(mode="json"),
        "pdk_profile": task.pdk_profile,
        "safety": {**task.safety.model_dump(mode="json"), "allow_remote_write": False,
                   "allow_remote_compute": False, "replace_existing": False},
    })


def _condition_issues(task: TaskSpec, rows: list) -> list[str]:
    if not task.operating_conditions:
        return []
    expected = {c.name: c for c in task.operating_conditions}
    if len(rows) != len(expected) or {r.name for r in rows} != set(expected):
        return ["recorded conditions do not cover the declared finite operating points"]
    from .executor import TaskExecutor
    issues = []
    for row in rows:
        condition = expected[row.name]
        vdd = condition.vdd_v if condition.vdd_v is not None else TaskExecutor._default_operating_condition_vdd(task)
        if ((row.process_corner, row.temperature_c, row.vdd_v) !=
                (condition.process_corner, condition.temperature_c, vdd) or not row.analysis_complete):
            issues.append(f"operating point {row.name} differs from its declared condition or is incomplete")
    return issues


def _winner_issues(task: TaskSpec, run: RunRecord, candidate: CandidateEvaluation | None) -> list[str]:
    if task.winner_verification is None:
        return []
    result = run.winner_verification
    if result is None or candidate is None:
        return ["declared winner verification is missing"]
    if not result.analysis_complete or not result.feasible:
        return ["winner verification is incomplete or failed"]
    from .executor import TaskExecutor
    verification_task = TaskExecutor._winner_verification_task(task, task, candidate)
    issues = _condition_issues(verification_task, result.operating_conditions)
    for stage in verification_task.analysis_stages:
        matches = [s for s in result.analysis_stages if s.stage_id == stage.id and s.analysis == stage.analysis]
        if len(matches) != 1 or not matches[0].analysis_complete:
            issues.append(f"winner stage {stage.id} is missing or incomplete")
            continue
        measured = matches[0]
        constraints = [c for c in verification_task.constraints if c.metric in stage.constraint_metrics]
        rows = measured.operating_conditions if verification_task.operating_conditions else [measured]
        issues.extend(_condition_issues(verification_task, measured.operating_conditions))
        for row in rows:
            if any(not e.passed for e in evaluate_constraints(row.metrics, constraints)):
                issues.append(f"winner stage {stage.id} does not satisfy all measured constraints")
            if any(row.metric_sources.get(c.metric, row.evidence_source).value != "eda_result" for c in constraints):
                issues.append(f"winner stage {stage.id} lacks EDA metric provenance")
    return issues


def _derived_condition_metric(task: TaskSpec, candidate: CandidateEvaluation, metric: str) -> bool:
    """Accept worst-of-points inference only with actual per-point EDA lineage."""
    if not task.operating_conditions:
        return False
    rows = [r for r in candidate.operating_conditions if metric in r.metrics]
    if not rows:
        rows = [r for stage in candidate.analysis_stages for r in stage.operating_conditions if metric in r.metrics]
    by_name = {}
    for row in rows:
        if row.metric_sources.get(metric, row.evidence_source).value != "eda_result":
            return False
        if row.name in by_name and by_name[row.name].metrics[metric] != row.metrics[metric]:
            return False
        by_name[row.name] = row
    if _condition_issues(task, list(by_name.values())):
        return False
    values = [r.metrics[metric] for r in by_name.values()]
    if not values or candidate.metrics.get(metric) not in values:
        return False
    if task.objective and task.objective.metric == metric:
        worst = min(values) if task.objective.goal.value == "maximize" else max(values)
        return candidate.metrics[metric] == worst
    return True


def build_design_iteration(request_path: Path, template_path: Path | None = None) -> tuple[dict, TaskSpec | None]:
    request_reference = file_reference(request_path)
    request = IterationRequest.model_validate(read_json(request_path))
    base = request_path.resolve().parent
    history: list[dict] = []
    anchor = None
    root_signature = None
    prior_passed: set[str] = set()
    previous_inputs = None
    action_seconds = 0.0
    seen_sources: set[str] = set()
    objective_scores: list[float] = []
    last_task = None
    previous_started_at = None
    for number, source in enumerate(request.rounds, 1):
        task_path, run_path = _resolve(base, source.task), _resolve(base, source.run)
        checkpoint_path, audit_path = _resolve(base, source.checkpoint), _resolve(base, source.binding_audit)
        assert task_path is not None and run_path is not None
        references = {"task": file_reference(task_path), "run": file_reference(run_path)}
        task, run = load_bound_run(task_path, run_path, audit_path)
        review = build_design_review(task_path, run_path, checkpoint_path, audit_path,
                                     completion_path=_resolve(base, source.completion),
                                     artifact_root=_resolve(base, source.artifact_root))
        if any(review["sources"][name] != references[name] for name in references):
            raise ValueError("iteration source files changed during review")
        source_key = review["sources"]["run"]["sha256"]
        if source_key in seen_sources:
            raise ValueError("iteration history repeats the same run; it cannot spend budget or advance twice")
        seen_sources.add(source_key)
        if run.finished_at < run.started_at or any(a.finished_at < a.started_at for a in run.actions):
            raise ValueError("iteration source has negative execution durations")
        if previous_started_at is not None and run.started_at <= previous_started_at:
            raise ValueError("iteration rounds must be in strictly increasing execution-start order")
        previous_started_at = run.started_at
        if not task.constraints:
            raise ValueError("iteration requires an explicit protected specification")
        constraints = [c.model_dump(mode="json") for c in task.constraints]
        signature = canonical_sha256({"target": task.target.model_dump(mode="json") if task.target else None,
                                      "constraints": constraints,
                                      "objective": task.objective.model_dump(mode="json") if task.objective else None,
                                      "winner_verification": task.winner_verification.model_dump(mode="json")
                                      if task.winner_verification else None})
        if root_signature is None:
            root_signature = signature
        elif signature != root_signature:
            raise ValueError("iteration target, specification or objective changed; start an explicit new campaign")
        assigned = {name for stage in request.stages for name in stage.constraint_metrics}
        if assigned != {c.metric for c in task.constraints}:
            raise ValueError("iteration stages must cover every declared constraint exactly once")
        if task.generic_simulation:
            for stage in request.stages:
                if (stage.analysis in task.resolved_analyses() and not set(stage.constraint_metrics)
                        <= task.generic_simulation.metric_names_for_analysis(stage.analysis.value)):
                    raise ValueError("iteration stage cannot produce its declared constraint metrics")
        candidate = _point(run, source.candidate_index)
        inputs = design_input_fingerprints(task, candidate)
        changed = [name for name, sha in inputs["sha256"].items()
                   if previous_inputs and previous_inputs["sha256"][name] != sha]
        # Parameters/topology are the search dimensions. A new testbench,
        # conditions or qualification policy needs its own feasible anchor.
        qualification_changed = bool(set(changed) & {"pdk", "testbench", "conditions", "analysis"})
        if qualification_changed:
            anchor = None
            prior_passed.clear()
            objective_scores.clear()
        stages = []
        available = candidate.metrics if candidate else {}
        for stage in request.stages:
            stage_constraints = [c for c in task.constraints if c.metric in stage.constraint_metrics]
            matching = [s for s in candidate.analysis_stages if s.analysis == stage.analysis] if candidate else []
            # Do not let aggregate metrics from another analysis masquerade as
            # completion of this stage. Earlier complete stages survive a later
            # execution failure, but that failure still blocks further writes.
            stage_metrics = matching[0].metrics if len(matching) == 1 else (
                available if candidate and not candidate.analysis_stages
                and stage.analysis in task.resolved_analyses() else {})
            evaluations = evaluate_constraints(stage_metrics, stage_constraints)
            missing = [e.metric for e in evaluations if e.actual is None]
            condition_rows = matching[0].operating_conditions if len(matching) == 1 else (
                candidate.operating_conditions if candidate else [])
            condition_issues = _condition_issues(task, condition_rows)
            required_measurement_issues = [
                name for metrics in ([r.metrics for r in condition_rows] if task.operating_conditions else [stage_metrics])
                for name in missing_required_measurements(task.measurement_plan, metrics, stage.analysis.value)
            ]
            condition_evaluations = [
                {"name": row.name, "constraints": [e.model_dump(mode="json")
                 for e in evaluate_constraints(row.metrics, stage_constraints)]}
                for row in condition_rows
            ] if task.operating_conditions else []
            complete = bool(candidate and (matching[0].analysis_complete if len(matching) == 1 else
                            candidate.analysis_complete and stage.analysis in task.resolved_analyses()
                            and not candidate.analysis_stages) and not missing and not condition_issues and not required_measurement_issues
                            and all(e["actual"] is not None for row in condition_evaluations for e in row["constraints"]))
            passed = all(e.passed for e in evaluations) and all(
                e["passed"] for row in condition_evaluations for e in row["constraints"])
            stages.append({"id": stage.id, "analysis": stage.analysis.value,
                           "status": "missing" if not complete else
                           "pass" if passed else "fail",
                           "constraints": [e.model_dump(mode="json") for e in evaluations],
                           "missing_metrics": missing, "condition_issues": condition_issues,
                           "missing_required_measurements": sorted(set(required_measurement_issues)),
                           "operating_conditions": condition_evaluations})
        real = run.adapter == "virtuoso-bridge-subprocess" and candidate is not None and candidate.evidence_source.value == "eda_result"
        required_metrics = {c.metric for c in task.constraints}
        if task.objective:
            required_metrics.add(task.objective.metric)
        metric_sources = {name: candidate.metric_sources.get(name, candidate.evidence_source).value
                          for name in available} if candidate else {}
        condition_lineage = {name for name in required_metrics if name in available and candidate is not None
                             and _derived_condition_metric(task, candidate, name)}
        derived = sorted(name for name in condition_lineage if metric_sources[name] == "software_inference")
        unmeasured = sorted(name for name in required_metrics if name in available and (
            name not in condition_lineage if task.operating_conditions else metric_sources[name] != "eda_result"))
        missing_required = sorted(required_metrics - set(available))
        stability_evidence_issues = [issue for row in review["stability_evidence"]
                                    if candidate is not None and row["candidate_index"] == candidate.index
                                    for issue in row["issues"]]
        measured = real and not unmeasured
        completion = review["completion"]
        reconcile = (run.status.value == "failed" or candidate is None or
                     not candidate.analysis_complete or
                     bool(review["checkpoint"] and review["checkpoint"]["pending_write"]) or
                     bool(stability_evidence_issues) or
                     any(row["status"] == "incomplete" for row in review["input_evidence"]) or
                     bool(completion and completion["status"] == "incomplete"))
        reanchor = bool(completion and completion["status"] != "incomplete" and (
            completion.get("selected_point_is_final_state") is not True
            or candidate is not None and candidate.index != review["selected_candidate_index"]))
        if reanchor:
            # Keep historical metrics, but do not carry a restored probe's starting state forward.
            anchor = None
            prior_passed.clear()
            objective_scores.clear()
        regressed = [s["id"] for s in stages if s["id"] in prior_passed and s["status"] == "fail"]
        missing_protected = [s["id"] for s in stages if s["id"] in prior_passed and s["status"] == "missing"]
        first_unmet = next((s for s in stages if s["status"] != "pass"), None)
        verification_issues = _winner_issues(task, run, candidate)
        verification_ok = not verification_issues
        feasible = measured and not reconcile and first_unmet is None and verification_ok and not missing_required
        score = None
        if candidate and task.objective and task.objective.metric in available:
            score = available[task.objective.metric] * (1 if task.objective.goal.value == "maximize" else -1)
        if feasible and not reanchor and (anchor is None or (score is not None and
                                           (anchor["objective_score"] is None or score > anchor["objective_score"]))):
            anchor = {"round": number, "sources": review["sources"], "candidate_index": candidate.index,
                      "parameters": candidate.parameters, "instance_parameters": candidate.instance_parameters,
                      "testbench_overrides": candidate.testbench_overrides.model_dump(mode="json")
                      if candidate.testbench_overrides else None,
                      "topology_sha256": candidate.topology_sha256, "inputs": inputs,
                      "objective_score": score, "current_remote_state_verified": False}
        if measured and not reconcile and not reanchor:
            prior_passed.update(s["id"] for s in stages if s["status"] == "pass")
        action_seconds += review["execution"]["action_seconds"]
        if measured and not reconcile and not reanchor and score is not None:
            objective_scores.append(score)
        else:
            objective_scores.clear()
        status = (
            "reconcile_execution" if reconcile else
            "software_only" if not real else
            "recorded_state_requires_reanchor" if reanchor else
            "revalidation_required" if not regressed and (missing_protected or missing_required or
                (first_unmet and first_unmet["status"] == "missing")) else
            "evidence_source_unverified" if unmeasured else
            "regressed" if regressed else
            "winner_verification_required" if first_unmet is None and not verification_ok else
            "specifications_satisfied" if feasible and not request.continue_after_feasible else
            "refine_objective" if feasible else "refine_stage"
        )
        stop_reason = None
        if status not in {"reconcile_execution", "software_only", "specifications_satisfied", "regressed",
                          "recorded_state_requires_reanchor"}:
            if number >= request.max_rounds:
                status, stop_reason = "budget_exhausted", "maximum design rounds reached"
            elif review["execution"]["budget_exhausted"]:
                status, stop_reason = "budget_exhausted", "the recorded bounded search exhausted its candidate budget"
            elif request.max_action_seconds is not None and action_seconds >= request.max_action_seconds:
                status, stop_reason = "budget_exhausted", "recorded action-time budget reached"
            elif request.stagnation_rounds and len(objective_scores) >= request.stagnation_rounds:
                recent = objective_scores[-request.stagnation_rounds:]
                if max(recent[1:]) - recent[0] <= request.minimum_objective_improvement:
                    status, stop_reason = "stagnated", "objective improvement below the declared threshold"
        history.append({"round": number, "sources": review["sources"], "task_id": task.id,
                        "candidate_index": candidate.index if candidate else None,
                        "status": status, "active_stage": first_unmet["id"] if first_unmet else None,
                        "stages": stages, "regressed_stages": regressed,
                        "protected_stages_needing_revalidation": missing_protected,
                        "inputs": inputs, "changed_inputs": changed,
                        "qualification_context_changed": qualification_changed,
                        "previous_metrics_invalidated": bool(changed),
                        "historical_evidence_use": "comparison_only; no old metric completes this round",
                        "metric_sources": metric_sources, "required_metrics_without_eda_source": unmeasured,
                        "derived_condition_metrics_with_eda_lineage": derived, "missing_required_metrics": missing_required,
                        "stop_reason": stop_reason, "full_specification_feasible": feasible,
                        "recorded_search_budget_exhausted": review["execution"]["budget_exhausted"],
                        "selection_scope": (review["search_audit"] or {}).get("selection_scope"),
                        "winner_verification_issues": verification_issues,
                        "stability_evidence_issues": stability_evidence_issues,
                        "input_evidence": review["input_evidence"],
                        "input_artifact_root": review["input_artifact_root"],
                        "completion": completion, "recorded_state_requires_reanchor": reanchor,
                        "objective_score": score})
        previous_inputs, last_task = inputs, task
    assert last_task is not None
    latest = history[-1]
    report = {"schema_version": 1, "kind": "design_iteration", "source": "software_inference",
              "request": request_reference, "read_only": True,
              "execution_authorized": False, "status": latest["status"],
              "history": history, "best_feasible_anchor": anchor,
              "action_seconds": action_seconds, "agent_thinking_seconds": None,
              "current_remote_state_verified": False, "task_draft": None, "draft_issues": [],
              "next_action": "reanchor" if latest["status"] == "recorded_state_requires_reanchor"
              else "stop" if latest["status"] in {
                  "specifications_satisfied", "budget_exhausted", "stagnated", "software_only"}
              else "inspect_before_restore" if latest["status"] in {"regressed", "reconcile_execution"}
              else "revalidate" if latest["status"] in {"revalidation_required", "winner_verification_required", "evidence_source_unverified"}
              else "bounded_refinement"}
    draft = None
    if report["next_action"] == "inspect_before_restore":
        draft = _reconcile_task(last_task, len(history))
    elif template_path is not None and report["next_action"] not in {"stop", "reanchor"}:
        raw = read_json(template_path)
        report["template"] = file_reference(template_path)
        if (last_task.target is None or DesignTarget.model_validate(raw.get("target")) != last_task.target
                or raw.get("pdk_profile", DEFAULT_PDK_PROFILE) != last_task.pdk_profile):
            raise ValueError("next template changes the iteration target or PDK")
        if raw.get("constraints", []) != [c.model_dump(mode="json") for c in last_task.constraints]:
            # Compare normalized constraints; a missing default tolerance is OK.
            if [MetricConstraint.model_validate(c) for c in raw.get("constraints", [])] != last_task.constraints:
                raise ValueError("next template weakens or changes the protected specification")
        if (Objective.model_validate(raw["objective"]) if raw.get("objective") else None) != last_task.objective:
            raise ValueError("next template changes the protected objective")
        if raw.get("operation") not in {"simulation.run", "design.tune", "design.close_loop"}:
            raise ValueError("next template must use an existing simulation/tuning operation")
        if raw.get("circuit") != last_task.circuit.value:
            raise ValueError("next template changes the circuit contract")
        raw["analysis"] = None
        raw["analysis_stages"] = [s.model_dump(mode="json") for s in request.stages]
        raw["id"] = f"{raw['id']}-iteration-{len(history) + 1}"
        raw["create_if_missing"] = False
        raw["safety"] = {**last_task.safety.model_dump(mode="json"), "allow_remote_compute": False,
                         "allow_remote_write": False, "replace_existing": False}
        try:
            draft = TaskSpec.model_validate(raw)
            if draft.winner_verification != last_task.winner_verification:
                raise ValueError("next template changes the protected winner verification")
            if last_task.design_context is not None:
                if draft.design_context is None or any(
                    getattr(draft.design_context, field) != getattr(last_task.design_context, field)
                    for field in ("instance_parameter_permissions", "hierarchy_parameter_scopes",
                                  "topology_edits", "frozen_instances", "frozen_nets", "frozen_pins")
                ):
                    raise ValueError("next template changes the protected topology/parameter edit envelope")
        except ValueError as exc:
            draft = None
            report["draft_issues"].append(str(exc))
    if draft is not None:
        from .planner import build_plan
        report["task_draft"] = {"id": draft.id, "operation": draft.operation.value,
                                "plan_token": build_plan(draft).confirmation_token,
                                "remote_compute": False, "remote_write": False,
                                "restoration_executed": False}
    if file_reference(request_path) != request_reference:
        raise ValueError("iteration request changed during review")
    for row in history:
        if any(file_reference(Path(ref["path"])) != ref for ref in row["sources"].values()):
            raise ValueError("iteration source changed during review")
    return report, draft


def render_design_iteration(report: dict) -> str:
    lines = ["# 设计迭代", "", f"状态：`{report['status']}`；下一步：`{report['next_action']}`。", "",
             "仅消费已保存记录；未查询当前 OA，也未执行写入、恢复或仿真。", "",
             "| 轮次 | 状态 | 当前阶段 | 变化的输入 |", "| --- | --- | --- | --- |"]
    for row in report["history"]:
        lines.append(f"| {row['round']} | {row['status']} | {row['active_stage']} | {', '.join(row['changed_inputs'])} |")
    for row in report["history"]:
        for evidence in row.get("input_evidence", []):
            lines.append(f"\n- 第 {row['round']} 轮输入证据（候选 {evidence['candidate_index']}）：{evidence['status']}；"
                         + ("；".join(evidence["issues"]) or "仅核对保存记录，不是新的远端结果。"))
        if row.get("completion"):
            completion = row["completion"]
            lines.append(f"\n- 第 {row['round']} 轮已记录收尾：{completion['status']}；"
                         f"最后观察 {completion.get('observed_at', '未确认')}，当前 OA 未重新读取。")
        if row.get("recorded_state_requires_reanchor"):
            lines.append("\n- 该历史选中点不能作为最后确认的 OA/testbench 起点；"
                         "请显式引用 baseline/final 或新鲜记录重新定锚，不自动恢复或生成续跑任务。")
    anchor = report["best_feasible_anchor"]
    lines.extend(["", f"最近最佳可行锚点：第 {anchor['round']} 轮。" if anchor else "尚无完整真实可行锚点。",
                  "历史指标仅作对照，不补齐新设计缺失的指标；回退前必须先独立核对实际状态。",
                  "预算耗尽或停滞只描述本次搜索，不证明规格物理不可行。", ""])
    lines.extend(report["draft_issues"])
    return "\n".join(lines)
