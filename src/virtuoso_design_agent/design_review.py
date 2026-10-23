"""Local, source-bound design reviews; never connects to an adapter."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .metrics import evaluate_constraints
from .models import (
    CandidateEvaluation, EvidenceSource, ExecutionCheckpoint, Relation,
    RunRecord, RunStatus, TaskSpec,
)
from .planner import build_plan
from .stability import STABILITY_METRICS
from .stability_evidence import audit_stability_artifacts


def read_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant, parse_float=finite_float)


def file_reference(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def load_bound_run(task_path: Path, run_path: Path, binding_audit_path: Path | None = None) -> tuple[TaskSpec, RunRecord]:
    task = TaskSpec.model_validate(read_json(task_path))
    run = RunRecord.model_validate(read_json(run_path))
    if run.task_id != task.id:
        raise ValueError("task/run identity mismatch")
    if binding_audit_path is not None:
        # Explicit historical compatibility: reuse an existing exact-byte audit,
        # not a guessed old plan or a newly manufactured matching token.
        audit = read_json(binding_audit_path)
        if (not isinstance(audit, dict) or audit.get("schema_version") != 1
                or audit.get("task_id") != task.id
                or audit.get("task_sha256") != file_reference(task_path)["sha256"]
                or audit.get("run_sha256") != file_reference(run_path)["sha256"]
                or audit.get("plan_token") != run.plan_token
                or audit.get("candidate_execution_gate_passed") is not True
                or audit.get("evaluated_candidate_count") != len(run.candidates)
                or audit.get("validation_evidence_source") != "software_inference"):
            raise ValueError("historical binding audit does not bind these exact task/run bytes")
        # Only consume identity and measured comparisons. Old audits did not
        # store modern error-normalization fields; never invent those fields or
        # promote an old prediction-accuracy gate while importing identity.
        measured = audit.get("candidates", [])
        if not isinstance(measured, list) or len(measured) != len(run.candidates):
            raise ValueError("historical binding audit candidate count mismatch")
        for saved, candidate in zip(measured, run.candidates, strict=True):
            if (not isinstance(saved, dict) or saved.get("index") != candidate.index
                    or {**task.parameters, **saved.get("parameters", {})} != candidate.parameters
                    or saved.get("instance_parameters", {}) != candidate.instance_parameters
                    or not isinstance(saved.get("comparisons"), list) or not saved["comparisons"]):
                raise ValueError("historical binding audit candidate identity mismatch")
            for comparison in saved["comparisons"]:
                if (not isinstance(comparison, dict) or comparison.get("metric") not in candidate.metrics
                        or comparison.get("measured_value") != candidate.metrics[comparison["metric"]]):
                    raise ValueError("historical binding audit measured metric mismatch")
    elif run.plan_token != build_plan(task).confirmation_token:
        raise ValueError("task/run identity or plan token mismatch; an existing exact-byte binding audit is required for historical records")
    indices = [candidate.index for candidate in run.candidates]
    if indices != list(range(1, len(indices) + 1)):
        raise ValueError("candidate records must be a unique contiguous prefix")
    return task, run


def _constraints(task: TaskSpec, candidate: CandidateEvaluation) -> list[dict]:
    result = []
    finite = {key: value for key, value in candidate.metrics.items() if math.isfinite(value)}
    for constraint, evaluation in zip(
        task.constraints, evaluate_constraints(finite, task.constraints), strict=True
    ):
        value = evaluation.actual
        if value is None:
            margin = None
        elif constraint.relation is Relation.LESS_OR_EQUAL:
            margin = constraint.value + constraint.tolerance - value
        elif constraint.relation is Relation.GREATER_OR_EQUAL:
            margin = value - constraint.value + constraint.tolerance
        else:
            margin = constraint.tolerance - abs(value - constraint.value)
        result.append({
            **evaluation.model_dump(mode="json"),
            "status": "missing" if value is None else ("pass" if evaluation.passed else "fail"),
            "margin": margin,
            "margin_definition": "positive is inside the allowed bound; same unit as metric",
            "measurement_source": candidate.metric_sources.get(
                constraint.metric, candidate.evidence_source
            ).value,
            "evaluation_source": "software_inference",
        })
    return result


def _candidate(task: TaskSpec, candidate: CandidateEvaluation) -> dict:
    constraints = _constraints(task, candidate)
    missing = [item["metric"] for item in constraints if item["status"] == "missing"]
    if task.objective and not math.isfinite(candidate.metrics.get(task.objective.metric, math.nan)):
        missing.append(task.objective.metric)
    gated_out = bool(candidate.terminated_after_stage and any(
        stage.analysis_complete and not stage.passed for stage in candidate.analysis_stages
    ))
    incomplete = (
        not candidate.analysis_complete
        or candidate.evidence_source is EvidenceSource.SYSTEM_EVENT
        or (bool(missing) and not gated_out)
    )
    status = "incomplete" if incomplete else (
        "infeasible" if gated_out or any(item["status"] == "fail" for item in constraints) else "feasible"
    )
    return {
        "index": candidate.index, "atomic_candidate_id": candidate.atomic_candidate_id,
        "topology_variant_id": candidate.topology_variant_id,
        "status": status, "recorded_feasible": candidate.feasible,
        "parameters": candidate.parameters, "instance_parameters": candidate.instance_parameters,
        "testbench_overrides": candidate.testbench_overrides.model_dump(mode="json")
        if candidate.testbench_overrides else None,
        "metrics": candidate.metrics, "metric_sources": {
            name: candidate.metric_sources.get(name, candidate.evidence_source).value
            for name in candidate.metrics
        },
        "constraints": constraints, "missing_metrics": sorted(set(missing)),
        "issues": candidate.analysis_issues, "warnings": candidate.analysis_warnings,
        "later_stages_skipped_by_gate": gated_out,
        "terminated_after_stage": candidate.terminated_after_stage,
        "stages": [{"id": stage.stage_id, "analysis": stage.analysis.value,
                    "complete": stage.analysis_complete, "passed": stage.passed,
                    "issues": stage.analysis_issues} for stage in candidate.analysis_stages],
        "operating_conditions": [condition.model_dump(mode="json")
                                 for condition in candidate.operating_conditions],
        "source": candidate.evidence_source.value,
    }


def selected_candidate(run: RunRecord) -> CandidateEvaluation | None:
    if not run.candidates:
        return None
    if run.selected_parameters is None and run.selected_instance_parameters is None:
        return None
    matches = [c for c in run.candidates if (
        c.parameters == (run.selected_parameters or {})
        and c.instance_parameters == (run.selected_instance_parameters or {})
        and c.testbench_overrides == run.selected_testbench_overrides
        and (run.selected_topology_variant_id is None
             or c.topology_variant_id == run.selected_topology_variant_id)
    )]
    if len(matches) != 1:
        raise ValueError("recorded selection does not identify one candidate")
    candidate = matches[0]
    if not candidate.analysis_complete or not candidate.feasible:
        raise ValueError("recorded selection references an incomplete or infeasible candidate")
    for name, value in (run.selected_metrics or {}).items():
        expected = (run.winner_verification.metrics if run.winner_verification
                    and name in run.winner_verification.metrics else candidate.metrics)
        if expected.get(name) != value:
            raise ValueError("selected metrics disagree with candidate evidence")
    return candidate


def _writeback(run: RunRecord, selected: CandidateEvaluation | None) -> dict:
    # Historical confirmation only. Never claims today's OA was freshly read.
    actions = [a for a in run.actions if a.action == "schematic.inspect.after"
               and a.status == "succeeded" and a.evidence_source is EvidenceSource.BRIDGE_READBACK]
    status = "not_confirmed_in_record"
    reference = None
    if actions:
        action = actions[-1]
        reference = run.actions.index(action)
        raw = action.details
        actual_instances = raw.get("confirmed_instance_parameters", raw.get("instance_parameters", {}))
        actual_parameters = raw.get("semantic_parameters", {})
        expected_instances = selected.instance_parameters if selected else {}
        expected_parameters = selected.oa_parameters if selected else {}
        instances_match = all(
            isinstance(actual_instances.get(name), dict)
            and all(actual_instances[name].get(key) == value for key, value in fields.items())
            for name, fields in expected_instances.items()
        )
        parameters_match = all(actual_parameters.get(key) == value
                               for key, value in expected_parameters.items())
        status = "readback_recorded_selection_unverified"
        if selected and (expected_instances or expected_parameters) and instances_match and parameters_match:
            status = "selected_parameter_readback_matched_in_record"
    return {"status": status, "action_index": reference,
            "current_remote_state_verified": False, "source": "software_inference"}


def _stability_evidence(task: TaskSpec, run: RunRecord) -> list[dict]:
    """Audit only explicit STB bundles. Never connects or resubmits missing work."""
    from .executor import TaskExecutor
    checks = []

    def results(value):
        if isinstance(value, dict):
            evidence = value.get("evidence")
            simulation = evidence.get("simulation", {}) if isinstance(evidence, dict) else {}
            if isinstance(simulation, dict) and isinstance(simulation.get("stability_artifacts"), dict):
                yield value, simulation["stability_artifacts"]
            for key, child in value.items():
                if key != "evidence" and isinstance(child, (dict, list)):
                    yield from results(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    yield from results(child)

    for candidate in run.candidates:
        roles = [("candidate", task, candidate)]
        if run.winner_verification is not None and candidate == selected_candidate(run):
            roles.append(("winner", TaskExecutor._winner_verification_task(task, task, candidate), run.winner_verification))
        for role, effective_task, point in roles:
            if "stability" not in {a.value for a in effective_task.resolved_analyses()}:
                continue
            if point.terminated_after_stage and not any(s.analysis.value == "stability" for s in point.analysis_stages):
                continue  # An earlier explicit gate legitimately skipped STB.
            if run.adapter != "virtuoso-bridge-subprocess":
                continue
            seen, rows = set(), []
            for index, action in enumerate(run.actions):
                if not re.match(rf"^simulation\.{role}\.{candidate.index}(?:\.|$)", action.action):
                    continue
                for result, retained in results(action.details):
                    identity = (retained.get("evidence_file"), retained.get("evidence_sha256"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    audit = audit_stability_artifacts(Path(str(identity[0])), expected_sha256=identity[1])
                    issues = list(audit["issues"])
                    if not identity[1]:
                        issues.append("original run lacks an evidence JSON hash")
                    binding = audit.get("binding", {})
                    settings = effective_task.generic_simulation
                    if candidate.testbench_overrides and settings:
                        settings = candidate.testbench_overrides.apply_to(settings)
                    if (binding.get("task_id") != task.id or binding.get("target") != task.target.model_dump(mode="json")
                            or binding.get("stability") != settings.stability.model_dump(mode="json")
                            or binding.get("sweep") != effective_task.stability_sweep.model_dump(mode="json")):
                        issues.append("retained STB target/task/probe/sweep binding differs from the reviewed task")
                    evidence = result.get("evidence", {})
                    if (binding.get("netlist_sha256") != evidence.get("netlist", {}).get("sha256")
                            or binding.get("wrapper_sha256") != evidence.get("testbench", {}).get("sha256")):
                        issues.append("retained STB inputs differ from the action's OA netlist/testbench hashes")
                    for metric, value in audit["metrics"].items():
                        if metric in STABILITY_METRICS and not math.isclose(
                            value, result.get("metrics", {}).get(metric, math.nan), rel_tol=1e-10, abs_tol=1e-10
                        ):
                            issues.append("recorded STB metric differs from local replay: " + metric)
                    rows.append({"candidate_index": candidate.index, "role": role, "action_index": index,
                                 "status": "verified" if audit["analysis_complete"] and not issues else "incomplete",
                                 "issues": issues, "evidence_file": identity[0], "evidence_sha256": identity[1],
                                 "native_comparison": audit["native_comparison"],
                                 "operating_condition": binding.get("operating_condition"), "source": "software_inference"})
            expected_conditions = [c.model_dump(mode="json") for c in effective_task.operating_conditions] or [None]
            if len(rows) != len(expected_conditions) or any(
                sum(row["operating_condition"] == expected for row in rows) != 1 for expected in expected_conditions
            ):
                rows.append({"candidate_index": candidate.index, "role": role, "status": "incomplete",
                             "issues": ["STB artifacts do not cover the declared nominal/finite conditions exactly"],
                             "source": "software_inference"})
            checks.extend(rows)
    return checks


def build_design_review(task_path: Path, run_path: Path, checkpoint_path: Path | None = None,
                        binding_audit_path: Path | None = None) -> dict:
    task, run = load_bound_run(task_path, run_path, binding_audit_path)
    selected = selected_candidate(run)
    candidates = [_candidate(task, candidate) for candidate in run.candidates]
    stability_evidence = _stability_evidence(task, run)
    for candidate in candidates:
        if any(row["candidate_index"] == candidate["index"] and row["role"] == "candidate"
               and row["status"] != "verified" for row in stability_evidence):
            candidate["status"] = "incomplete"
    source_refs = {"task": file_reference(task_path), "run": file_reference(run_path)}
    if binding_audit_path:
        source_refs["historical_binding_audit"] = file_reference(binding_audit_path)
    checkpoint = None
    if checkpoint_path:
        cp = ExecutionCheckpoint.model_validate(read_json(checkpoint_path))
        if (cp.task_id, cp.plan_token, cp.adapter, cp.started_at) != (
            run.task_id, run.plan_token, run.adapter, run.started_at
        ):
            raise ValueError("checkpoint belongs to a different task or execution")
        if cp.candidates != run.candidates[:len(cp.candidates)]:
            raise ValueError("checkpoint candidate prefix disagrees with run")
        source_refs["checkpoint"] = file_reference(checkpoint_path)
        checkpoint = {"complete": cp.complete, "next_candidate_index": cp.next_candidate_index,
                      "active_candidate_index": cp.active_candidate_index,
                      "pending_write": any(value is not None for value in (
                          cp.pending_oa_parameters, cp.pending_oa_instance_parameters, cp.pending_topology_sha256)),
                      "resume_authorized": False}
    failures = [{"index": index, "action": action.action, "status": action.status,
                 "source": action.evidence_source.value}
                for index, action in enumerate(run.actions) if action.status != "succeeded"]
    search = run.search_audit.model_dump(mode="json") if run.search_audit else None
    if search and search["attempted_candidate_count"] != len(candidates):
        raise ValueError("search audit candidate count disagrees with run")
    has_incomplete = any(c["status"] == "incomplete" for c in candidates)
    budget_exhausted = bool(search and not search["domain_exhausted"]
                            and len(candidates) >= task.limits.max_iterations)
    missing_stages = sorted({s.id for s in task.analysis_stages} - {
        s["id"] for c in candidates for s in c["stages"]})
    return {
        "schema_version": 1, "kind": "design_review", "read_only": True,
        "source": "software_inference", "sources": source_refs,
        "binding": {"mode": "historical_exact_byte_audit" if binding_audit_path else "current_plan_token",
                    "recorded_plan_token": run.plan_token,
                    "current_plan_token": build_plan(task).confirmation_token,
                    "execution_authorized": False},
        "task": {"id": task.id, "operation": task.operation.value, "circuit": task.circuit.value,
                 "target": task.target.model_dump(mode="json") if task.target else None,
                 "pdk_profile": task.pdk_profile, "analysis": task.analysis.value if task.analysis else None,
                 "analysis_stages": [s.model_dump(mode="json") for s in task.analysis_stages],
                 "operating_conditions": [c.model_dump(mode="json") for c in task.operating_conditions],
                 "measurement_plan": task.measurement_plan.model_dump(mode="json") if task.measurement_plan else None,
                 "parameters": task.parameters, "objective": task.objective.model_dump(mode="json")
                 if task.objective else None, "limits": task.limits.model_dump(mode="json"),
                 "intent_source": "user_input"},
        "execution": {"status": run.status.value, "adapter": run.adapter,
                      "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat(),
                      "wall_seconds": (run.finished_at - run.started_at).total_seconds(),
                      "action_seconds": sum((a.finished_at - a.started_at).total_seconds() for a in run.actions),
                      "agent_thinking_seconds": None, "failures": failures,
                      "incomplete_evidence": has_incomplete or any(r["status"] != "verified" for r in stability_evidence),
                      "budget_exhausted": budget_exhausted},
        "stability_evidence": stability_evidence,
        "candidates": candidates, "selected_candidate_index": selected.index if selected else None,
        "recorded_selection": {"parameters": run.selected_parameters,
                               "instance_parameters": run.selected_instance_parameters,
                               "topology_variant_id": run.selected_topology_variant_id},
        "search_audit": search, "checkpoint": checkpoint, "writeback": _writeback(run, selected),
        "winner_verification": run.winner_verification.model_dump(mode="json") if run.winner_verification else None,
        "missing_declared_stages": missing_stages,
        "winner_verification_missing": task.winner_verification is not None and run.winner_verification is None,
        "notes": run.notes,
        "boundaries": ["仅复盘保存记录；未连接远端或核对当前 OA。显式 STB bundle 核对本地原始文件哈希并重放已保存的解码向量，其他分析不自动复核原始文件。",
                       "有限候选最佳不是连续或全局最优；预算耗尽不等于规格不可行。",
                       "demo/software_inference 不证明电路性能；缺指标不等于电路不达标。",
                       "参数、拓扑、PDK 或条件变化后旧性能只能对比，不能直接用于新设计闭环。"],
    }


def render_design_review(review: dict) -> str:
    task = review["task"]
    selected = review["selected_candidate_index"]
    candidate = next((c for c in review["candidates"] if c["index"] == selected), None)
    if candidate is None and review["candidates"]:
        candidate = review["candidates"][-1]
    lines = [f"# 设计复盘：{task['id']}", "", f"- 状态：{review['execution']['status']}",
             f"- 来源绑定：{review['binding']['mode']}；不授予执行权限。",
             f"- PDK：{task['pdk_profile']}；目标：`{task['target']}`",
             f"- 已记录候选：{len(review['candidates'])}；已选候选：{selected}",
             f"- 参数回读：{review['writeback']['status']}（不是当前 OA 状态）",
             f"- 执行/证据失败条目：{len(review['execution']['failures'])}；预算耗尽：{review['execution']['budget_exhausted']}",
             "", "## 规格与余量", ""]
    if candidate:
        definitions = {m["metric"]: m for m in (task.get("measurement_plan") or {}).get("measurements", [])}
        lines.extend([f"以下为候选 {candidate['index']}；"
                      + ("已记录选择。" if selected else "仅诊断展示，不是推荐。"), "",
                      "| 指标 | 单位 | 要求 | 实际 | 余量（原单位） | 状态 | 来源 |",
                      "| --- | --- | --- | ---: | ---: | --- | --- |"])
        for c in candidate["constraints"]:
            unit = definitions.get(c["metric"], {}).get("unit", "未声明")
            lines.append(f"| {c['metric']} | {unit} | {c['relation']} {c['expected']} ± {c['tolerance']} | "
                         f"{c['actual']} | {c['margin']} | {c['status']} | {c['measurement_source']} |")
        lines.extend(["", "参数与 testbench 条件：", "", "```json",
                      json.dumps({k: candidate[k] for k in ("parameters", "instance_parameters", "testbench_overrides")},
                                 ensure_ascii=False, indent=2), "```"])
    else:
        lines.append("没有可复盘的候选指标；不能由命令成功推断规格通过。")
    if review["search_audit"]:
        lines.extend(["", "## 选择边界", "", review["search_audit"]["statement"]])
    if review["winner_verification"]:
        w = review["winner_verification"]
        lines.extend(["", f"Winner 额外复核：complete={w['analysis_complete']}，feasible={w['feasible']}。"])
    if review.get("stability_evidence"):
        lines.extend(["", "## STB 原始证据与原生对照", ""])
        for row in review["stability_evidence"]:
            lines.append(f"- 候选 {row['candidate_index']} / {row['role']}：{row['status']}；"
                         + ("；".join(row["issues"]) or "本地文件哈希及同作业 native margin 对照通过"))
            if row.get("evidence_file"):
                lines.append(f"  - [保留的 STB 证据](<{row['evidence_file']}>)")
    lines.extend(["", "## 未闭合与使用注意", ""])
    lines.extend(f"- {text}" for text in [*review["boundaries"], *review["notes"]])
    if review["missing_declared_stages"]:
        lines.append(f"- 未出现在候选记录中的声明 stage：{review['missing_declared_stages']}")
    if review["winner_verification_missing"]:
        lines.append("- 任务声明了 winner 额外复核，但记录中没有对应结果。")
    lines.extend(["", "## 原始来源", ""])
    for label, reference in review["sources"].items():
        lines.append(f"- {label}: [{Path(reference['path']).name}](<{reference['path']}>)；SHA-256 `{reference['sha256']}`")
    return "\n".join(lines) + "\n"
