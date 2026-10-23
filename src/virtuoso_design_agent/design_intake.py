"""Thin local intake: reuse saved intent and evidence, never execute a design."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from .design_iteration import design_input_fingerprints
from .design_next import suggest_design_next
from .design_review import build_design_review, file_reference, read_json
from .measurements import MeasurementRequest, canonical_sha256, compile_measurement_request
from .models import TaskSpec
from .onboarding_resolution import resolve_onboarding_draft
from .planner import build_plan


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _TaskSource(_Contract):
    kind: Literal["task"]
    task: Path


class _OnboardingSource(_Contract):
    kind: Literal["onboarding"]
    draft: Path
    resolution: Path


class _History(_Contract):
    task: Path
    run: Path
    checkpoint: Path | None = None
    binding_audit: Path | None = None
    completion: Path | None = None
    artifact_root: Path | None = None


class DesignRationale(_Contract):
    """Authored reasoning is not an EDA measurement or a Bridge readback."""

    text: StrictStr = Field(min_length=1, max_length=4000)
    source: Literal["user_input", "software_inference"]
    role: StrictStr | None = None
    instance: StrictStr | None = None
    parameter: StrictStr | None = None
    semantic_parameter: StrictStr | None = None
    metric: StrictStr | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "DesignRationale":
        if not self.text.strip():
            raise ValueError("rationale text cannot be blank")
        if (self.instance is None) != (self.parameter is None):
            raise ValueError("rationale instance and parameter must be supplied together")
        if sum(value is not None for value in (
            self.role, self.instance, self.semantic_parameter, self.metric,
        )) > 1:
            raise ValueError("rationale must name at most one target kind")
        return self


class DesignIntakeRequest(_Contract):
    schema_version: Literal[1] = 1
    source: Annotated[_TaskSource | _OnboardingSource, Field(discriminator="kind")]
    measurements: Path | None = None
    history: _History | None = None
    rationale: list[DesignRationale] = Field(default_factory=list, max_length=128)
    uncertainties: list[StrictStr] = Field(default_factory=list, max_length=128)


def _check_rationale(task: TaskSpec, items: list[DesignRationale]) -> None:
    context = task.design_context
    assert context is not None
    roles = {role.role for role in context.roles}
    permissions = {p.instance: set(p.parameters) for p in context.instance_parameter_permissions}
    semantic = {p.parameter for p in context.semantic_parameter_permissions}
    metrics = set(context.metrics) | {c.metric for c in task.constraints}
    if task.objective:
        metrics.add(task.objective.metric)
    for item in items:
        if item.role is not None and item.role not in roles:
            raise ValueError(f"rationale names unknown role: {item.role}")
        if item.instance is not None and item.parameter not in permissions.get(item.instance, set()):
            raise ValueError(f"rationale names undeclared parameter: {item.instance}.{item.parameter}")
        if item.semantic_parameter is not None and item.semantic_parameter not in semantic:
            raise ValueError(f"rationale names undeclared semantic parameter: {item.semantic_parameter}")
        if item.metric is not None and item.metric not in metrics:
            raise ValueError(f"rationale names unknown metric: {item.metric}")


def _context_summary(task: TaskSpec) -> dict:
    """Derive scope from the ordinary task; maintain only reasoning in the manifest."""
    context = task.design_context
    assert context is not None
    return {
        "task_id": task.id, "operation": task.operation.value,
        "target": task.target.model_dump(mode="json"), "pdk_profile": task.pdk_profile,
        "design_context": context.model_dump(mode="json", exclude_none=True),
        "parameters": {name: getattr(task, name) for name in ("parameters", "parameter_space")},
        "instance_parameter_updates": [u.model_dump(mode="json") for u in task.instance_parameter_updates],
        "instance_parameter_space": [s.model_dump(mode="json") for s in task.instance_parameter_space],
        "candidate_set": task.candidate_set.model_dump(mode="json") if task.candidate_set else None,
        "generic_simulation": task.generic_simulation.model_dump(mode="json", exclude_none=True),
        "analysis": [a.value for a in task.resolved_analyses()],
        "analysis_stages": [s.model_dump(mode="json") for s in task.analysis_stages],
        "sweeps": {name: getattr(task, name).model_dump(mode="json") if getattr(task, name) else None
                   for name in ("ac_sweep", "linearity_sweep", "noise_sweep", "stability_sweep")},
        "operating_conditions": [c.model_dump(mode="json") for c in task.operating_conditions],
        "measurement_plan": task.measurement_plan.model_dump(mode="json") if task.measurement_plan else None,
        "constraints": [c.model_dump(mode="json") for c in task.constraints],
        "objective": task.objective.model_dump(mode="json") if task.objective else None,
        "limits": task.limits.model_dump(mode="json"),
        "topology_refinement": task.topology_refinement.model_dump(mode="json") if task.topology_refinement else None,
        "winner_verification": task.winner_verification.model_dump(mode="json") if task.winner_verification else None,
    }


def _input_fingerprints(task: TaskSpec) -> dict:
    result = design_input_fingerprints(task, None)
    raw = task.model_dump(mode="json")
    for name, fields in {
        "search_domain": ("parameter_space", "instance_parameter_space", "candidate_set", "topology_refinement"),
        "decision_scope": ("design_context", "constraints", "objective", "limits", "analysis_stage_execution"),
    }.items():
        result["sha256"][name] = canonical_sha256({field: raw[field] for field in fields})
    return result


def build_design_intake(request_path: Path) -> tuple[dict, TaskSpec | None]:
    """Resolve saved sources, compile measurements, and emit only a safe normal task."""
    request = DesignIntakeRequest.model_validate(read_json(request_path))
    root = request_path.resolve().parent
    sources = {"request": file_reference(request_path)}

    def path_ref(label: str, value: Path) -> Path:
        path = (root / value).resolve()
        sources[label] = file_reference(path)
        return path

    if request.source.kind == "task":
        task = TaskSpec.model_validate(read_json(path_ref("task", request.source.task)))
    else:
        draft_path = path_ref("onboarding_draft", request.source.draft)
        resolution_path = path_ref("resolution", request.source.resolution)
        task = resolve_onboarding_draft(draft_path, read_json(resolution_path))
    if (task.circuit.value != "existing_schematic" or task.design_context is None
            or task.generic_simulation is None or task.target is None
            or task.operation.value not in {"simulation.run", "design.tune", "design.close_loop"}):
        raise ValueError("design-intake requires an existing_schematic simulation/tune/close_loop task with design_context")
    _check_rationale(task, request.rationale)
    raw = task.model_dump(mode="json", exclude_none=True)
    raw["create_if_missing"] = False
    raw["safety"].update(allow_remote_compute=False, allow_remote_write=False, replace_existing=False)
    task = TaskSpec.model_validate(raw)
    uncertainties = list(request.uncertainties)
    issues = []
    measurement = None
    if request.measurements is not None:
        measurement_path = path_ref("measurements", request.measurements)
        measurement_request = MeasurementRequest.model_validate(read_json(measurement_path))
        measurement, compiled = compile_measurement_request(
            raw, measurement_request, sources={name: value for name, value in sources.items()
                                              if name != "request"},
        )
        uncertainties.extend(measurement["uncertainties"])
        issues.extend(measurement["issues"])
        if compiled is not None:
            task = compiled
    if task.measurement_plan is None:
        issues.append("缺少明确的 measurement_plan；请绑定已有测量请求，不猜测单位、信号或提取定义。")
    if not request.rationale:
        issues.append("缺少设计理由；请记录已有理论依据、瓶颈、拟议改动理由或停止理由，不自动编造。")
    history = None
    if request.history is not None:
        paths = {name: path_ref(f"history_{name}", value)
                 for name, value in request.history.model_dump(exclude={"artifact_root"}).items() if value is not None}
        artifact_root = ((root / request.history.artifact_root).resolve()
                         if request.history.artifact_root is not None else None)
        review = build_design_review(paths["task"], paths["run"], paths.get("checkpoint"), paths.get("binding_audit"),
                                     completion_path=paths.get("completion"), artifact_root=artifact_root)
        for name, ref in review["sources"].items():
            key = f"history_{name}"
            if key in sources and sources[key] != ref:
                raise ValueError("intake history source changed during review")
            sources[key] = ref
        old_task = TaskSpec.model_validate(read_json(paths["task"]))
        if (old_task.target, old_task.pdk_profile) != (task.target, task.pdk_profile):
            raise ValueError("history target/PDK differs from intake; keep it in a separate review")
        old = _input_fingerprints(old_task)
        current = _input_fingerprints(task)
        changed = [name for name in current["sha256"] if current["sha256"][name] != old["sha256"][name]]
        next_report, _ = suggest_design_next(
            paths["task"], paths["run"], checkpoint_path=paths.get("checkpoint"),
            binding_audit_path=paths.get("binding_audit"),
            completion_path=paths.get("completion"), artifact_root=artifact_root,
        )
        selected = next((c for c in review["candidates"] if c["index"] == review["selected_candidate_index"]), None)
        history = {
            "task_id": old_task.id, "binding": review["binding"], "execution": review["execution"],
            "candidate_count": len(review["candidates"]), "selected_candidate": selected,
            "last_candidate_diagnostic": review["candidates"][-1] if selected is None and review["candidates"] else None,
            "checkpoint": review["checkpoint"], "writeback": review["writeback"],
            "input_evidence": review["input_evidence"], "input_artifact_root": review["input_artifact_root"],
            "completion": review["completion"],
            "winner_verification": review["winner_verification"],
            "missing_declared_stages": review["missing_declared_stages"],
            "winner_verification_missing": review["winner_verification_missing"],
            "changed_recorded_inputs": changed, "historical_input_fingerprints": old,
            "current_input_fingerprints": current,
            "current_metrics_reused": False, "current_remote_state_verified": False,
            "next_status_for_historical_run": next_report["status"],
            "recommendations_for_historical_run": next_report["recommendations"],
        }
        # Do not turn an interrupted/pending execution into another runnable task.
        if (next_report["status"] == "reconcile_execution_first"
                or review["checkpoint"] and (not review["checkpoint"]["complete"] or review["checkpoint"]["pending_write"])):
            issues.append("历史执行未对账或 checkpoint 未完成；先查询原作业/回读，不生成可重放任务。")
        elif (review["completion"]
              and review["completion"].get("selected_point_is_final_state") is not True):
            issues.append("历史选中点不是最后已确认的 OA/testbench 元组；请显式改用 baseline/final task/run "
                          "重新定锚，不沿已恢复的探针生成草案。")
    ready = not uncertainties and not issues
    report = {
        "schema_version": 1, "kind": "design_intake", "source": "software_inference", "read_only": True,
        "status": "draft_ready" if ready else "needs_confirmation" if uncertainties else "needs_definition",
        "sources": sources, "context": _context_summary(task),
        "rationale": [item.model_dump(mode="json", exclude_none=True) for item in request.rationale],
        "uncertainties": uncertainties, "issues": issues,
        "measurement_compile": {key: value for key, value in measurement.items()
                                if key not in {"sources", "measurements", "uncertainties", "issues"}} if measurement else None,
        "history": history, "current_input_fingerprints": _input_fingerprints(task),
        "task_draft": {"id": task.id, "semantic_sha256": canonical_sha256(task.model_dump(mode="json")),
                       "plan_token": build_plan(task).confirmation_token} if ready else None,
        "remote_execution_performed": False, "execution_authorized": False,
        "boundaries": [
            "接入摘要来自保存的任务/配置，不是新读出的 OA；理由保留 user_input/software_inference 来源。",
            "历史候选及建议只针对原运行；即使指纹相同也不证明当前 OA、PDK 文件或参数未变，不复用为当前指标。",
            "草案沿用原 TaskSpec/planner；远端计算、OA 写入、创建及覆盖关闭，token 不是授权。",
            "不自动识别功能角色、补全模型或随机提出数值；任意单项操作仍可使用原入口。",
        ],
    }
    for ref in sources.values():
        if file_reference(Path(ref["path"])) != ref:
            raise ValueError("intake source changed while it was being read; inspect it before retrying")
    return report, task if ready else None


def render_design_intake(report: dict) -> str:
    context = report["context"]
    lines = [f"# 设计接入与决策上下文：{context['task_id']}", "",
             f"状态：`{report['status']}`；仅本地整理，未执行。", "",
             f"目标：`{context['target']}`；PDK：`{context['pdk_profile']}`。", "",
             "## 角色与允许改动", ""]
    scope = context["design_context"]
    for role in scope["roles"]:
        targets = {key: role[key] for key in ("instances", "nets", "pins", "terminals") if role[key]}
        lines.append(f"- {role['role']} → `{targets}`（{role['evidence_source']}）")
    lines.extend(["", "范围从原任务导出；冻结拓扑不等于禁止明确授权的 CDF 参数修改。", "", "```json",
                  json.dumps({key: scope[key] for key in (
                      "instance_parameter_permissions", "semantic_parameter_permissions", "hierarchy_parameter_scopes",
                      "frozen_instances", "frozen_nets", "frozen_pins", "topology_edits",
                  )}, ensure_ascii=False, indent=2), "```", "", "## 设计理由", ""])
    for item in report["rationale"]:
        target = {key: value for key, value in item.items() if key not in {"text", "source"}}
        lines.append(f"- {target or '整体'}：{item['text']}（{item['source']}）")
    domain = {key: context[key] for key in ("parameters", "instance_parameter_updates", "instance_parameter_space")}
    if context["candidate_set"]:
        domain["candidate_set"] = context["candidate_set"]
    lines.extend(["", "## 变量与原候选域（不新增或应用）", "", "```json",
                  json.dumps(domain, ensure_ascii=False, indent=2), "```", "", "## 测量、规格与预算", "",
                  f"分析：`{context['analysis']}`；目标：`{context['objective']}`；预算：`{context['limits']}`。", "",
                  "| 测量 | 分析 | 单位 | 必需 | 信号 | 定义 | 来源 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
    for m in (context["measurement_plan"] or {}).get("measurements", []):
        cells = (m["metric"], m["analysis"], m["unit"], str(m["required"]),
                 ", ".join(m["signals"]), m["definition"], m["source"])
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in cells) + " |")
    lines.extend(["", "| 规格 | 关系 | 阈值 | 容差 |", "| --- | --- | ---: | ---: |"])
    for c in context["constraints"]:
        lines.append(f"| {c['metric']} | {c['relation']} | {c['value']} | {c['tolerance']} |")
    lines.extend(["", "扫描范围、条件、阶段门和后续 winner 复核见 JSON 的 context；此表不证明它们已运行。"])
    if report["history"]:
        history = report["history"]
        candidate = history["selected_candidate"] or history["last_candidate_diagnostic"]
        lines.extend(["", "## 历史依据（不是当前设计结果）", "",
                      f"- 原任务：{history['task_id']}；记录候选 {history['candidate_count']}。",
                      f"- 执行状态：{history['execution']['status']}；后续判断：{history['next_status_for_historical_run']}。",
                      f"- 与本次已记录输入不同的维度：`{history['changed_recorded_inputs']}`。",
                      f"- 参数写回记录：{history['writeback']['status']}；不证明当前 OA。"])
        for row in history.get("input_evidence", []):
            lines.append(f"- 输入证据（候选 {row['candidate_index']}）：{row['status']}；"
                         + ("；".join(row["issues"]) or "仅为保存记录的本地审计，不是新仿真。"))
        if history.get("completion"):
            completion = history["completion"]
            lines.append(f"- 已记录收尾：{completion['status']}；当前 OA 未重新读取。")
            if completion.get("observed_at"):
                lines.append(f"- 最后关联观察：{completion['observed_at']}；"
                             f"历史选中点仍为最终状态：{completion['selected_point_is_final_state']}。")
        if candidate:
            lines.extend(["", ("历史已选候选：" if history["selected_candidate"] else "最后候选，仅诊断：")
                          + f"{candidate['index']}，{candidate['status']}。", "",
                          "| 历史指标 | 值 | 来源 |", "| --- | ---: | --- |"])
            for name, value in candidate["metrics"].items():
                lines.append(f"| {name} | {value} | {candidate['metric_sources'][name]} |")
        lines.append("")
        lines.extend(f"- {step['reason']}" for step in history["recommendations_for_historical_run"])
    lines.extend(["", "## 待确认与边界", ""])
    lines.extend(f"- {item}" for item in [*report["uncertainties"], *report["issues"], *report["boundaries"]])
    lines.extend(["", "## 原始来源", ""])
    for label, ref in report["sources"].items():
        lines.append(f"- {label}: [{Path(ref['path']).name}](<{ref['path']}>)；SHA-256 `{ref['sha256']}`")
    return "\n".join(lines) + "\n"
