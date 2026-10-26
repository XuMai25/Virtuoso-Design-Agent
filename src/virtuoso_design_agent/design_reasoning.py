"""Review authored design hypotheses against bound records, without executing them."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .design_review import file_reference, load_bound_run, read_json, selected_candidate
from .models import CandidateEvaluation, StrictModel, TaskSpec
from .spectre_values import spectre_values_equal


class _Contract(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ReasoningControl(_Contract):
    """An already permitted field, not permission to introduce a new control."""

    kind: Literal["cdf", "semantic", "source_dc", "load"]
    name: StrictStr = Field(min_length=1)
    parameter: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def cdf_field(self):
        if (self.kind == "cdf") != (self.parameter is not None):
            raise ValueError("only CDF controls require a parameter")
        return self

    def key(self) -> tuple[str, str, str | None]:
        return self.kind, self.name, self.parameter


class ReasoningEvidence(_Contract):
    kind: Literal["task", "metric", "action"]
    selector: StrictStr = Field(min_length=1)
    action: StrictStr | None = None

    @model_validator(mode="after")
    def action_name(self):
        if (self.kind == "action") != bool(self.action):
            raise ValueError("action evidence requires exactly one action name")
        return self


class MetricExpectation(_Contract):
    metric: StrictStr = Field(min_length=1)
    direction: Literal["increase", "decrease", "unchanged"]
    absolute_tolerance: float = Field(ge=0.0)


class DesignHypothesis(_Contract):
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    observation: StrictStr = Field(min_length=1, max_length=4000)
    mechanism: StrictStr = Field(min_length=1, max_length=4000)
    evidence: list[ReasoningEvidence] = Field(min_length=1, max_length=16)
    controls: list[ReasoningControl] = Field(default_factory=list, max_length=32)
    expectations: list[MetricExpectation] = Field(default_factory=list, max_length=32)
    tradeoffs: list[StrictStr] = Field(default_factory=list, max_length=32)
    falsification: StrictStr = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def distinct_fields(self):
        if any(not value.strip() for value in (self.observation, self.mechanism, self.falsification)):
            raise ValueError("hypothesis text cannot be blank")
        if len({c.key() for c in self.controls}) != len(self.controls):
            raise ValueError("hypothesis controls cannot repeat")
        if len({e.metric for e in self.expectations}) != len(self.expectations):
            raise ValueError("hypothesis expectations cannot repeat a metric")
        return self


class DesignReasoningRequest(_Contract):
    schema_version: Literal[1] = 1
    task: Path
    task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    run: Path | None = None
    run_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_index: int | None = Field(default=None, ge=1)
    binding_audit: Path | None = None
    hypotheses: list[DesignHypothesis] = Field(min_length=1, max_length=32)
    uncertainties: list[StrictStr] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def bound_baseline(self):
        if (self.run is None) != (self.run_sha256 is None):
            raise ValueError("reasoning run and its exact SHA-256 must be supplied together")
        if self.run is None and (self.candidate_index is not None or self.binding_audit is not None):
            raise ValueError("candidate/audit requires a baseline run")
        if len({h.id for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("hypothesis ids cannot repeat")
        return self


def validate_reasoning_control(task: TaskSpec, control: ReasoningControl) -> None:
    context = task.design_context
    if context is None:
        raise ValueError("reasoning requires an existing design_context")
    if control.kind == "cdf":
        allowed = any(p.instance == control.name and control.parameter in p.parameters
                      for p in context.instance_parameter_permissions)
    elif control.kind == "semantic":
        allowed = any(p.parameter == control.name for p in context.semantic_parameter_permissions)
    else:
        simulation = task.generic_simulation
        allowed = simulation is not None and control.name in {
            item.name for item in (simulation.sources if control.kind == "source_dc" else simulation.loads)
        }
    if not allowed:
        raise ValueError(f"reasoning control is not declared in the task: {control.key()}")


def _pointer(raw, selector: str):
    if not selector.startswith("/"):
        raise ValueError("task/action evidence requires a JSON pointer beginning with /")
    try:
        for token in selector[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            raw = raw[int(token)] if isinstance(raw, list) else raw[token]
        return raw
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(f"reasoning evidence pointer is absent: {selector}") from exc


def _point(run, index):
    if index is not None:
        matches = [c for c in run.candidates if c.index == index]
        if len(matches) != 1:
            raise ValueError("reasoning candidate is absent from the bound run")
        return matches[0]
    point = selected_candidate(run)
    if point is None and len(run.candidates) == 1:
        point = run.candidates[0]
    if point is None:
        raise ValueError("reasoning requires an explicit candidate for an unselected multi-point run")
    return point


def _state(task: TaskSpec, candidate: CandidateEvaluation | None) -> dict:
    """Compare effective recorded inputs; do not infer live OA or causality."""
    simulation = task.generic_simulation
    if simulation is not None and candidate is not None and candidate.testbench_overrides:
        simulation = candidate.testbench_overrides.apply_to(simulation)
    raw = task.model_dump(mode="json")
    instance = {u.instance: dict(u.parameters) for u in task.instance_parameter_updates}
    for name, fields in (candidate.instance_parameters if candidate else {}).items():
        instance.setdefault(name, {}).update(fields)
    fields = {("cdf", name, key): value for name, values in instance.items() for key, value in values.items()}
    fields.update({("semantic", name, None): value
                   for name, value in {**task.parameters, **(candidate.parameters if candidate else {})}.items()})
    if simulation:
        fields.update({("source_dc", s.name, None): s.dc_value for s in simulation.sources})
        fields.update({("load", s.name, None): s.value for s in simulation.loads})
    fixed = {key: raw[key] for key in (
        "target", "pdk_profile", "design_context", "analysis", "analysis_stages", "analysis_stage_execution",
        "ac_sweep", "linearity_sweep", "noise_sweep", "stability_sweep", "measurement_plan",
        "operating_conditions", "constraints", "objective", "winner_verification",
    )}
    fixed["topology"] = candidate.topology_sha256 if candidate else None
    fixed["simulation"] = simulation.model_dump(mode="json") if simulation else None
    if fixed["simulation"]:
        for source in fixed["simulation"]["sources"]:
            source.pop("dc_value")
        for load in fixed["simulation"]["loads"]:
            load.pop("value")
    return {"fields": fields, "fixed": fixed}


def build_design_reasoning(request_path: Path, task: TaskSpec, *, run=None,
                           candidate_index: int | None = None) -> dict:
    request_ref = file_reference(request_path)
    request = DesignReasoningRequest.model_validate(read_json(request_path))
    base = request_path.resolve().parent
    task_path = (base / request.task).resolve()
    sources = {"request": request_ref, "task": file_reference(task_path)}
    if sources["task"]["sha256"] != request.task_sha256:
        raise ValueError("reasoning source task SHA-256 changed")
    baseline_task = TaskSpec.model_validate(read_json(task_path))
    baseline_run = baseline = None
    if request.run is not None:
        run_path = (base / request.run).resolve()
        sources["run"] = file_reference(run_path)
        if sources["run"]["sha256"] != request.run_sha256:
            raise ValueError("reasoning source run SHA-256 changed")
        audit = (base / request.binding_audit).resolve() if request.binding_audit else None
        if audit:
            sources["binding_audit"] = file_reference(audit)
        baseline_task, baseline_run = load_bound_run(task_path, run_path, audit)
        baseline = _point(baseline_run, request.candidate_index)
    if (baseline_task.target, baseline_task.pdk_profile) != (task.target, task.pdk_profile):
        raise ValueError("reasoning target/PDK differs from the reviewed task")
    available_metrics = set(baseline_task.design_context.metrics if baseline_task.design_context else [])
    available_metrics.update(c.metric for c in baseline_task.constraints)
    if baseline_task.objective:
        available_metrics.add(baseline_task.objective.metric)
    current = None
    if run is not None and run.candidates and (candidate_index is not None or
            selected_candidate(run) is not None or len(run.candidates) == 1):
        current = _point(run, candidate_index)
    old_state, current_state = _state(baseline_task, baseline), _state(task, current)
    changed = {key for key in old_state["fields"].keys() | current_state["fields"].keys()
               if not (spectre_values_equal(old_state["fields"][key], current_state["fields"][key])
                       if key[0] == "cdf" and key in old_state["fields"] and key in current_state["fields"]
                       else old_state["fields"].get(key) == current_state["fields"].get(key))}
    rows = []
    for hypothesis in request.hypotheses:
        for control in hypothesis.controls:
            validate_reasoning_control(baseline_task, control)
            validate_reasoning_control(task, control)
        if any(e.metric not in available_metrics for e in hypothesis.expectations):
            raise ValueError("reasoning expectation names an undeclared metric")
        evidence = []
        for ref in hypothesis.evidence:
            if ref.kind == "task":
                value, source = _pointer(baseline_task.model_dump(mode="json"), ref.selector), "user_input"
            elif baseline_run is None:
                raise ValueError("metric/action evidence requires a bound baseline run")
            elif ref.kind == "metric":
                if ref.selector not in baseline.metrics:
                    raise ValueError("reasoning metric is absent from the baseline candidate")
                value = baseline.metrics[ref.selector]
                source = baseline.metric_sources.get(ref.selector, baseline.evidence_source).value
                if baseline_run.adapter != "virtuoso-bridge-subprocess":
                    source = "software_inference"
            else:
                actions = [a for a in baseline_run.actions if a.action == ref.action]
                if len(actions) != 1:
                    raise ValueError("reasoning action evidence is absent or ambiguous")
                value = _pointer(actions[0].details, ref.selector)
                source = actions[0].evidence_source.value
                if baseline_run.adapter != "virtuoso-bridge-subprocess":
                    source = "software_inference"
            evidence.append({**ref.model_dump(mode="json", exclude_none=True), "value": value,
                             "source": source, "scope": "saved source only; not a fresh observation",
                             "recorded_status": "declaration" if ref.kind == "task" else
                             baseline_run.status.value if ref.kind == "metric" else actions[0].status})
        comparable = bool(baseline is not None and current is not None and baseline_run is not None
                          and baseline_run.status.value == "succeeded" and run.status.value == "succeeded"
                          and baseline.analysis_complete and current.analysis_complete
                          and old_state["fixed"] == current_state["fixed"]
                          and changed <= {c.key() for c in hypothesis.controls})
        comparisons = []
        for expected in hypothesis.expectations:
            before = baseline.metrics.get(expected.metric) if baseline is not None else None
            actual = current.metrics.get(expected.metric) if current is not None else None
            real = bool(comparable and baseline_run.adapter == run.adapter == "virtuoso-bridge-subprocess"
                        and baseline.metric_sources.get(expected.metric, baseline.evidence_source).value == "eda_result"
                        and current.metric_sources.get(expected.metric, current.evidence_source).value == "eda_result")
            delta = actual - before if real and actual is not None and before is not None else None
            status = "not_evaluated"
            distinct_observation = baseline_run != run or (baseline is not None and current is not None and baseline.index != current.index)
            if delta is not None and math.isfinite(delta) and distinct_observation:
                passed = (delta > expected.absolute_tolerance if expected.direction == "increase" else
                          delta < -expected.absolute_tolerance if expected.direction == "decrease" else
                          abs(delta) <= expected.absolute_tolerance)
                status = "consistent" if passed else "contradicted"
            comparisons.append({**expected.model_dump(mode="json"), "baseline": before, "actual": actual,
                                "delta": delta, "status": status, "source": "software_inference"})
        rows.append({**hypothesis.model_dump(mode="json"), "resolved_evidence": evidence,
                     "comparisons": comparisons, "recorded_inputs_comparable": comparable,
                     "comparison_context": {
                         "baseline_observation_present": baseline is not None,
                         "current_observation_present": current is not None,
                         "baseline_complete": bool(baseline and baseline.analysis_complete and baseline_run.status.value == "succeeded"),
                         "current_complete": bool(current and current.analysis_complete and run.status.value == "succeeded"),
                         "fixed_context_differences": [key for key in old_state["fixed"]
                                                       if old_state["fixed"][key] != current_state["fixed"][key]],
                         "changes_outside_controls": [list(key) for key in sorted(
                             changed - {c.key() for c in hypothesis.controls}, key=str)],
                     },
                     "changed_controls": [list(c) for c in sorted(changed, key=str)],
                     "source": "software_inference"})
    for ref in sources.values():
        if file_reference(Path(ref["path"])) != ref:
            raise ValueError("reasoning source changed during review")
    return {"schema_version": 1, "kind": "design_reasoning", "source": "software_inference",
            "sources": sources, "hypotheses": rows, "issues": list(request.uncertainties),
            "baseline_fields": [{"control": list(key), "value": value}
                                for key, value in sorted(old_state["fields"].items(), key=lambda pair: str(pair[0]))],
            "status": "needs_confirmation" if request.uncertainties else "reviewed",
            "execution_authorized": False, "current_remote_state_verified": False,
            "limitations": ["Consistency is not proof of the proposed mechanism or separate parameter sensitivities.",
                            "No proof that hypotheses were frozen before seeing these results; replay is retrospective.",
                            "Only recorded input fields can be compared; absent fields are not proof that the physical inputs were unchanged.",
                            "Task declarations are user input, not measured OA; unknown/live inputs remain unknown."]}


def reasoning_draft_issues(report: dict, draft: TaskSpec) -> list[str]:
    """A reasoned proposal must not quietly change an unexplained coordinate."""
    allowed = {(c["kind"], c["name"], c.get("parameter"))
               for h in report["hypotheses"] for c in h["controls"]}
    baseline = {tuple(row["control"]): row["value"] for row in report["baseline_fields"]}
    points = draft.candidate_set.candidates if draft.candidate_set else [None]
    unexplained = set()
    for point in points:
        # _state only consumes these three shared value fields; an atomic
        # candidate does not pretend to be a measured CandidateEvaluation.
        fields = _state(draft, None)["fields"]
        if point:
            fields.update({("cdf", u.instance, name): value
                           for u in point.instance_parameter_updates for name, value in u.parameters.items()})
            fields.update({("semantic", name, None): value for name, value in point.parameters.items()})
            if point.testbench_overrides:
                fields.update({("source_dc", name, None): s.dc_value for name, s in point.testbench_overrides.sources.items()
                               if s.dc_value is not None})
                fields.update({("load", name, None): value for name, value in point.testbench_overrides.loads.items()})
        for key, value in fields.items():
            same = key in baseline and (spectre_values_equal(value, baseline[key]) if key[0] == "cdf" else value == baseline[key])
            if not same and key not in allowed:
                unexplained.add(str(key))
    return ["拟议草案含未被设计假设解释的字段：" + ", ".join(sorted(unexplained))] if unexplained else []


def render_design_reasoning(report: dict) -> str:
    lines = ["## 设计假设与结果对照", "", "仅核对保存记录；一致不等于因果验证，也不证明事前预测。", ""]
    for h in report["hypotheses"]:
        lines.extend([f"### {h['id']}", "", f"观察：{h['observation']}", f"物理解释（推断）：{h['mechanism']}",
                      f"可调整字段：{h['controls']}", f"代价：{h['tradeoffs']}", f"证伪条件：{h['falsification']}"])
        for e in h["resolved_evidence"]:
            value = e["value"]
            brief = (f"{len(value)} 项；完整值见 JSON" if isinstance(value, (dict, list)) else str(value))
            if len(brief) > 200:
                brief = brief[:200] + "…（完整值见 JSON）"
            lines.append(f"- 依据 {e['kind']} {e['selector']}（{e['source']}，{e['recorded_status']}）：`{brief}`")
        for e in h["comparisons"]:
            lines.append(f"- {e['metric']} {e['direction']}：{e['baseline']} → {e['actual']}；{e['status']}")
        if not h["recorded_inputs_comparable"]:
            lines.append(f"- 未作实测趋势确认的上下文：`{h['comparison_context']}`")
    lines.extend(["", *[f"- 待确认：{issue}" for issue in report["issues"]], ""])
    return "\n".join(lines)
