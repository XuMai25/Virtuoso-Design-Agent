"""Evidence-first next steps using existing local response-model machinery."""

from __future__ import annotations

import math
from pathlib import Path

from .design_review import build_design_review, file_reference, load_bound_run, read_json
from .models import RunStatus, TaskSpec
from .op_relinearization import (
    OperatingPointRelinearizationPolicy, OperatingPointRelinearizationResult,
    build_task_from_relinearization, relinearize_operating_point,
)
from .planner import build_plan
from .op_small_signal import OperatingPointSmallSignalPolicy, analyze_operating_point_small_signal_run


def suggest_design_next(
    task_path: Path, run_path: Path, *, checkpoint_path: Path | None = None,
    model_path: Path | None = None, policy_path: Path | None = None,
    template_path: Path | None = None,
    binding_audit_path: Path | None = None,
    small_signal_policy_path: Path | None = None,
    iteration_path: Path | None = None,
) -> tuple[dict, TaskSpec | None]:
    """No random proposals, hidden remote calls, or fabricated model accuracy."""
    review = build_design_review(task_path, run_path, checkpoint_path, binding_audit_path)
    task, run = load_bound_run(task_path, run_path, binding_audit_path)
    supplied = (model_path is not None, policy_path is not None, template_path is not None)
    if any(supplied) and not all(supplied):
        raise ValueError("model, policy and task template must be supplied together")
    report = {
        "schema_version": 1, "kind": "design_next", "source": "software_inference",
        "read_only": True, "sources": review["sources"], "task_id": task.id,
        "status": "needs_evidence", "recommendations": [], "model": None, "small_signal": None,
        "task_draft": None, "execution_authorized": False,
        "reuse": {"allowed": ["原记录作为历史对比；同一来源的本地理论重算"],
                  "requires_new_evidence": ["尺寸/拓扑/PDK/仿真条件变化后的最终规格",
                                            "当前 OA/会话状态与人工改动"]},
    }
    steps = report["recommendations"]

    def add(kind: str, reason: str, evidence: str) -> None:
        steps.append({"kind": kind, "reason": reason, "evidence": evidence,
                      "execution_authorized": False})

    if iteration_path is not None:
        from .design_iteration import build_design_iteration
        iteration, guarded_draft = build_design_iteration(iteration_path, template_path)
        latest_sources = iteration["history"][-1]["sources"]
        if any(latest_sources[name]["sha256"] != review["sources"][name]["sha256"]
               for name in ("task", "run")):
            raise ValueError("iteration latest round does not bind this exact task/run")
        report["iteration"] = iteration
        add("iteration_guard", "已重放跨轮次规格保护、可行锚点与停止条件；旧指标不会补齐当前缺失项。",
            "iteration.history / iteration.best_feasible_anchor")
        if iteration["status"] not in {"refine_stage", "refine_objective"}:
            report["status"] = iteration["status"]
            report["task_draft"] = iteration["task_draft"]
            return report, guarded_draft

    if run.status is RunStatus.FAILED or review["execution"]["incomplete_evidence"]:
        report["status"] = "reconcile_execution_first"
        add("reconcile", "先查询原请求/作业并独立回读；证据不完整不是电路不可行，禁止重放不确定写入。",
            "run.status / candidates[].analysis_complete / execution.failures")
        return report, None
    if run.winner_verification and (
        not run.winner_verification.analysis_complete or not run.winner_verification.feasible
    ):
        report["status"] = "winner_verification_failed"
        add("review_winner_gate", "先解释 winner 的额外质量失败，不能把 nominal runner-up 自动升级为最终设计。",
            "run.winner_verification")
        return report, None
    if review["winner_verification_missing"]:
        report["status"] = "winner_verification_missing"
        add("missing_winner_gate", "声明的 winner 额外复核尚无结果；先补齐该门，不把 nominal 达标称为完整闭环。",
            "task.winner_verification / run.winner_verification")
        return report, None
    if review["execution"]["budget_exhausted"]:
        report["status"] = "budget_decision_required"
        add("budget", "现有结论仅覆盖已测前缀；先决定是否值得增加预算，不推断全域不可行。",
            "run.search_audit / task.limits")
        return report, None
    if run.adapter != "virtuoso-bridge-subprocess":
        report["status"] = "software_only"
        add("evidence_boundary", "离线记录只验证软件逻辑，不据此给出实际器件尺寸优化或电路性能结论。", "run.adapter")
        return report, None
    if small_signal_policy_path is not None:
        small_signal_policy = OperatingPointSmallSignalPolicy.model_validate(read_json(small_signal_policy_path))
        if small_signal_policy.expected_pdk_profile != task.pdk_profile:
            raise ValueError("small-signal policy PDK differs from reviewed task")
        result = analyze_operating_point_small_signal_run(small_signal_policy, run_path)
        report["sources"]["small_signal_policy"] = file_reference(small_signal_policy_path)
        report["small_signal"] = {
            "status": result.status.value, "scope": result.analysis_scope,
            "covered_metrics": result.covered_metrics, "uncovered_metrics": result.uncovered_metrics,
            "comparisons": {key: value.model_dump(mode="json") for key, value in result.eda_comparison.items()},
            "missing_quantities": result.missing_quantities_by_instance,
            "assumptions": result.assumptions, "warnings": result.warnings,
            "source_action_sha256": result.source_action_sha256,
            "source": "software_inference",
        }
        add("op_small_signal", "已用原 OP 与声明边界重算小信号网络；先查看模型与 EDA 的差异和缺失量，"
            "不能把单个工作点的增益解释当作尺寸灵敏度。", "small_signal.comparisons / small_signal.missing_quantities")
    if model_path is None:
        if review["selected_candidate_index"] is not None:
            report["status"] = "review_or_stop"
            add("review_or_stop", "先检查已声明规格和 winner 质量是否满足用途；已达标不强制继续调参。", "selected candidate constraints")
        add("theory_before_new_points", "需要继续优化时，复用 OP 小信号/MNA 或带独立留出的局部响应模型；"
            "缺少相应导数或受控扰动记录时明确缺口，不随机生成尺寸。", "existing small-signal-from-run / op-relinearize")
        return report, None

    assert policy_path is not None and template_path is not None
    policy = OperatingPointRelinearizationPolicy.model_validate(read_json(policy_path))
    saved = OperatingPointRelinearizationResult.model_validate(read_json(model_path))
    recomputed = relinearize_operating_point(policy, run_path)
    if saved != recomputed:
        raise ValueError("saved model differs from deterministic source/policy recomputation")
    for label, path in (("model", model_path), ("policy", policy_path), ("template", template_path)):
        report["sources"][label] = file_reference(path)
    if any(c not in task.constraints for c in policy.constraints) or policy.objective != task.objective:
        raise ValueError("next-step model must retain the reviewed constraints and objective")
    report["model"] = {
        "gate_passed": recomputed.gate_passed, "anchor_candidate_index": policy.anchor_candidate_index,
        "scope": "local first-order EDA response; not a global physical law",
        "evidence_source": "software_inference",
        "parameter_bindings": {name: binding.model_dump(mode="json")
                               for name, binding in recomputed.parameter_bindings.items()},
        "constraints_without_prediction": [c.metric for c in task.constraints
                                            if c.metric not in {m.metric for m in recomputed.models}],
        "sensitivities": [{"metric": model.metric, "normalized_sensitivities": model.normalized_sensitivities,
                           "holdout_error_percent": model.maximum_holdout_error_percent,
                           "holdout_gate_passed": model.holdout_gate_passed} for model in recomputed.models],
    }
    if recomputed.fixed_testbench_overrides is not None:
        report["model"]["fixed_testbench_overrides"] = recomputed.fixed_testbench_overrides.model_dump(mode="json")
    if not recomputed.gate_passed or recomputed.candidate_set is None:
        report["status"] = "model_not_validated"
        add("model_gap", "留出或模型门未通过；保留误差，不生成可执行尺寸候选。", "recomputed model gates")
        return report, None
    if review["selected_candidate_index"] != policy.anchor_candidate_index:
        report["status"] = "anchor_not_selected"
        add("anchor", "模型锚点不是本轮已选设计；先明确起点，不能把旧锚点预测当当前局部优化。", "selected candidate / model anchor")
        return report, None
    anchor = next(c for c in run.candidates if c.index == policy.anchor_candidate_index)
    anchor_objective = anchor.metrics[policy.objective.metric]
    feasible = [p for p in recomputed.proposals if p.predicted_feasible and not p.previously_evaluated]
    improving = [p for p in feasible if not math.isclose(p.predicted_objective_value, anchor_objective, rel_tol=1e-12, abs_tol=0.0)
                 and ((p.predicted_objective_value > anchor_objective) if policy.objective.goal.value == "maximize"
                      else (p.predicted_objective_value < anchor_objective))]
    report["model"]["objective_improvement"] = {
        "metric": policy.objective.metric, "goal": policy.objective.goal.value,
        "recorded_anchor_value": anchor_objective, "prospective_feasible_count": len(feasible),
        "predicted_improving_count": len(improving), "source": "software_inference",
    }
    if not improving:
        report["status"] = "no_predicted_improvement"
        add("stop_or_revisit_model", "当前受控范围内没有预测更优且满足模型约束的新点；停止生成下一轮任务。"
            "这不是全局最优证明；确需诊断时先说明物理假设或缺量，不为模型验收重复计算。",
            "model.objective_improvement / source-bound local proposals")
        return report, None
    draft = build_task_from_relinearization(model_path, template_path)
    # The existing compiler owns numerical proposal generation. This layer adds
    # source-task continuity and strips execution authority, not another solver.
    for field in ("target", "circuit", "pdk_profile", "analysis", "analysis_stages",
                  "analysis_stage_execution", "operating_conditions", "generic_simulation",
                  "design_context", "constraints", "objective", "ac_sweep", "linearity_sweep", "noise_sweep",
                  "stability_sweep", "measurement_plan"):
        if getattr(draft, field) != getattr(task, field):
            raise ValueError(f"next-step template changes reviewed context: {field}")
    if draft.winner_verification != task.winner_verification:
        raise ValueError("next-step template changes reviewed context: winner_verification")
    if draft.id == task.id or draft.operation.value != "design.tune" or draft.create_if_missing:
        raise ValueError("next task must be a newly named, non-creating design.tune task")
    raw = draft.model_dump(mode="json")
    raw["safety"] = task.safety.model_dump(mode="json")
    raw["safety"].update(allow_remote_compute=False, allow_remote_write=False,
                         replace_existing=False)
    draft = TaskSpec.model_validate(raw)
    comparisons = []
    for candidate in recomputed.candidate_set.candidates:
        comparisons.append({
            "id": candidate.id, "changes": {name: {"before": anchor.parameters.get(name), "proposed": value}
                for name, value in candidate.parameters.items() if anchor.parameters.get(name) != value},
            "instance_changes": {instance: {name: {"before": anchor.instance_parameters.get(instance, {}).get(name),
                                                       "proposed": value}
                                             for name, value in fields.items()
                                             if anchor.instance_parameters.get(instance, {}).get(name) != value}
                                 for instance, fields in candidate.instance_parameters().items()},
            "predicted_metric_changes": {name: {"anchor": anchor.metrics.get(name), "predicted": value,
                "delta": value - anchor.metrics[name] if name in anchor.metrics else None}
                for name, value in candidate.predicted_metrics.items()},
            "evidence_source": "software_inference",
        })
    report["status"] = "local_model_draft_ready"
    report["model"]["proposals"] = comparisons
    report["task_draft"] = {"id": draft.id, "plan_token": build_plan(draft).confirmation_token,
                            "remote_compute": False, "remote_write": False,
                            "user_confirmation_required": True,
                            "candidate_count": len(comparisons)}
    add("bounded_local_refinement", "根据已复算并通过留出门的局部灵敏度生成普通调优草案；"
        "同时展示预测收益与代价，最终选择仍服从新的同源 EDA。", "model.sensitivities / model.proposals")
    return report, draft


def render_design_next(report: dict) -> str:
    lines = [f"# 下一步建议：{report['task_id']}", "", f"状态：`{report['status']}`。仅建议，未执行。", ""]
    for step in report["recommendations"]:
        lines.extend([f"- {step['reason']}（依据：{step['evidence']}）"])
    if report.get("iteration"):
        from .design_iteration import render_design_iteration
        lines.extend(["", render_design_iteration(report["iteration"])])
    if report["model"]:
        if report["model"].get("fixed_testbench_overrides"):
            lines.extend(["", "## 模型使用的固定仿真条件", "",
                          "以下原记录的源/负载 override 已逐候选保留，不改写基础 setup 或 OA；"
                          "不证明实际 simulator input 已独立核对。", "",
                          f"`{report['model']['fixed_testbench_overrides']}`", ""])
        if report["model"].get("parameter_bindings"):
            lines.extend(["## 显式 CDF 数值映射", "",
                          "字段单位/含义为声明，不是新的 PDK 回读；只接受显式 Spectre SI 字面量。", ""])
            for name, binding in report["model"]["parameter_bindings"].items():
                lines.append(f"- {name} → {binding['instance']}.{binding['parameter']}；"
                             f"模型单位 {binding['unit']}；{binding['meaning']}")
        if report["model"].get("objective_improvement"):
            improvement = report["model"]["objective_improvement"]
            lines.extend(["", f"目标 {improvement['metric']} ({improvement['goal']})："
                          f"记录锚点 {improvement['recorded_anchor_value']}；"
                          f"预测更优的新点 {improvement['predicted_improving_count']} 个。不是实测收益。", ""])
        lines.extend(["", "## 局部模型依据", "",
                      "仅适用于当前 PDK、拓扑、条件和锚点附近；预测是 software_inference。", "",
                      "| 指标 | 归一化局部灵敏度 | 留出误差 % |",
                      "| --- | --- | ---: |"])
        for model in report["model"]["sensitivities"]:
            lines.append(f"| {model['metric']} | {model['normalized_sensitivities']} | {model['holdout_error_percent']} |")
        for proposal in report["model"].get("proposals", []):
            lines.extend(["", f"### {proposal['id']}", "", f"修改：`{proposal['changes']}`", "",
                          f"CDF 修改：`{proposal.get('instance_changes', {})}`", "",
                          "| 指标 | 锚点 | 预测 | 变化 |", "| --- | ---: | ---: | ---: |"])
            for name, values in proposal["predicted_metric_changes"].items():
                lines.append(f"| {name} | {values['anchor']} | {values['predicted']} | {values['delta']} |")
    if report["small_signal"]:
        result = report["small_signal"]
        lines.extend(["", "## OP 小信号复算", "", f"范围：{result['scope']}；状态：{result['status']}",
                      "", f"未覆盖指标：{result['uncovered_metrics']}；缺失量：{result['missing_quantities']}",
                      "", "| 指标 | 理论预测 | EDA | 相对误差 |", "| --- | ---: | ---: | ---: |"])
        for name, values in result["comparisons"].items():
            lines.append(f"| {name} | {values['predicted']} | {values['actual']} | {values['relative_error']} |")
    lines.extend(["", "不得沿用历史结果替代新设计最终验证；草案仍需审查、重新授权和 plan。", ""])
    return "\n".join(lines)
