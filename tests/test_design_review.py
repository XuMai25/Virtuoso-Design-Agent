from __future__ import annotations

import json
from pathlib import Path

import pytest

from virtuoso_design_agent.adapters import DeterministicDemoAdapter
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_review import build_design_review, render_design_review
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import TaskSpec, ExecutionCheckpoint
from virtuoso_design_agent.planner import build_plan

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def source(tmp_path):
    task = TaskSpec.model_validate_json((ROOT / "examples/tasks/inverter-close-loop.demo.json").read_text())
    task_path = write(tmp_path / "task.json", task.model_dump(mode="json", exclude_unset=True))
    run = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, build_plan(task), token=build_plan(task).confirmation_token,
    )
    run_path = write(tmp_path / "run.json", run.model_dump(mode="json"))
    return task_path, run_path


def test_review_reuses_constraints_and_never_promotes_demo(source):
    report = build_design_review(*source)
    assert report["read_only"]
    assert len(report["candidates"]) == 9
    assert report["selected_candidate_index"]
    assert report["writeback"]["current_remote_state_verified"] is False
    assert all(c["source"] == "software_inference" for c in report["candidates"])
    assert "不是连续或全局最优" in render_design_review(report)
    assert report["execution"]["agent_thinking_seconds"] is None
    suggestion, draft = suggest_design_next(*source)
    assert suggestion["status"] == "software_only"
    assert draft is None


@pytest.mark.parametrize("field,value", [("task_id", "other"), ("plan_token", "wrong")])
def test_review_rejects_source_mismatch(source, field, value):
    raw = json.loads(source[1].read_text())
    raw[field] = value
    write(source[1], raw)
    with pytest.raises(ValueError, match="identity"):
        build_design_review(*source)


def test_missing_metrics_are_not_electrical_infeasibility(source):
    raw = json.loads(source[1].read_text())
    raw.update(selected_parameters=None, selected_instance_parameters=None, selected_metrics=None)
    raw["candidates"][0]["metrics"].pop("delay_ps")
    write(source[1], raw)
    report = build_design_review(*source)
    assert report["candidates"][0]["status"] == "incomplete"
    assert report["candidates"][0]["constraints"][0]["margin"] is None
    assert suggest_design_next(*source)[0]["status"] == "reconcile_execution_first"


def test_finite_constraint_failure_is_distinct(source):
    raw = json.loads(source[1].read_text())
    raw.update(selected_parameters=None, selected_instance_parameters=None, selected_metrics=None)
    raw["candidates"][0]["metrics"]["delay_ps"] = 200
    write(source[1], raw)
    report = build_design_review(*source)
    assert report["candidates"][0]["status"] == "infeasible"
    assert report["candidates"][0]["constraints"][0]["margin"] == -155


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_rejected(source, literal):
    text = source[1].read_text()
    text = text.replace('"schema_version": 1', '"schema_version": ' + literal, 1)
    source[1].write_text(text)
    with pytest.raises(ValueError, match="non-finite"):
        build_design_review(*source)


def test_failed_run_never_proposes_new_write(source):
    raw = json.loads(source[1].read_text())
    raw["status"] = "failed"
    write(source[1], raw)
    report, task = suggest_design_next(*source)
    assert report["status"] == "reconcile_execution_first"
    assert task is None


def test_selection_mismatch_rejected(source):
    raw = json.loads(source[1].read_text())
    raw["selected_metrics"]["delay_ps"] += 10
    write(source[1], raw)
    with pytest.raises(ValueError, match="selected metrics"):
        build_design_review(*source)


def test_cli_writes_new_outputs_and_refuses_overwrite(source, tmp_path, capsys):
    output, markdown = tmp_path / "review.json", tmp_path / "review.md"
    args = ["design-review", *map(str, source), "--output", str(output), "--markdown", str(markdown)]
    assert main(args) == 0
    original = output.read_bytes()
    assert "规格与余量" in markdown.read_text(encoding="utf-8")
    assert main(args) == 2
    assert output.read_bytes() == original
    assert "never overwritten" in capsys.readouterr().err


def test_output_paths_must_be_distinct(source, tmp_path):
    output = tmp_path / "same.json"
    assert main(["design-review", *map(str, source), "--output", str(output), "--markdown", str(output)]) == 2
    assert not output.exists()


def test_model_requires_complete_inputs(source, tmp_path):
    with pytest.raises(ValueError, match="supplied together"):
        suggest_design_next(*source, model_path=tmp_path / "model.json")


def test_checkpoint_is_bound_to_execution_and_never_authorizes_resume(source, tmp_path):
    raw = json.loads(source[1].read_text())
    cp = ExecutionCheckpoint.model_validate({
        "task_id": raw["task_id"], "plan_token": raw["plan_token"], "adapter": raw["adapter"],
        "started_at": raw["started_at"], "initial_parameters": {}, "expected_oa_parameters": {},
        "pending_oa_parameters": {"nmos_width_um": 0.5}, "next_candidate_index": 2,
        "candidates": raw["candidates"][:1],
    })
    path = write(tmp_path / "checkpoint.json", cp.model_dump(mode="json"))
    report = build_design_review(*source, path)
    assert report["checkpoint"]["pending_write"]
    assert not report["checkpoint"]["resume_authorized"]
    raw = json.loads(path.read_text())
    raw["plan_token"] = "different"
    write(path, raw)
    with pytest.raises(ValueError, match="different task or execution"):
        build_design_review(*source, path)


def test_budget_exhaustion_does_not_mean_no_feasible_design(source):
    task_raw = json.loads(source[0].read_text())
    task_raw["limits"]["max_iterations"] = 1
    task = TaskSpec.model_validate(task_raw)
    write(source[0], task_raw)
    run = TaskExecutor(DeterministicDemoAdapter()).execute(task, build_plan(task), token=build_plan(task).confirmation_token)
    write(source[1], run.model_dump(mode="json"))
    review = build_design_review(*source)
    assert review["execution"]["budget_exhausted"]
    assert review["search_audit"]["domain_exhausted"] is False
    assert suggest_design_next(*source)[0]["status"] == "budget_decision_required"


def test_target_margin_respects_tolerance(source):
    task_raw = json.loads(source[0].read_text())
    task_raw["constraints"] = [{"metric": "delay_ps", "relation": "target", "value": 40, "tolerance": 5}]
    task = TaskSpec.model_validate(task_raw)
    write(source[0], task_raw)
    raw = json.loads(source[1].read_text())
    raw.update(plan_token=build_plan(task).confirmation_token, selected_parameters=None,
               selected_instance_parameters=None, selected_metrics=None)
    raw["candidates"][0]["metrics"]["delay_ps"] = 42
    write(source[1], raw)
    assert build_design_review(*source)["candidates"][0]["constraints"][0]["margin"] == 3


def test_duplicate_candidates_rejected(source):
    raw = json.loads(source[1].read_text())
    raw["candidates"][1]["index"] = 1
    write(source[1], raw)
    with pytest.raises(ValueError, match="contiguous prefix"):
        build_design_review(*source)


@pytest.fixture
def modeled_source(tmp_path):
    # Reuse the existing solver's synthetic four-point, independent-holdout fixture.
    from test_op_relinearization import _write_source_run, _policy, _write_template
    from virtuoso_design_agent.op_relinearization import relinearize_operating_point

    run_path = _write_source_run(tmp_path)
    policy = _policy(run_path)
    template_path = _write_template(tmp_path, policy)
    template = json.loads(template_path.read_text())
    template["constraints"] = [c.model_dump(mode="json") for c in policy.constraints]
    write(template_path, template)
    task_raw = dict(template)
    task_raw.update(id="synthetic-real-local-response", parameter_space={
        "device_width_um": [0.9, 1.0, 1.1], "bias_v": [0.25, 0.30, 0.35]})
    task_raw["limits"] = {"max_iterations": 9, "timeout_seconds": 600}
    task = TaskSpec.model_validate(task_raw)
    task_path = write(tmp_path / "review-source-task.json", task_raw)
    run = json.loads(run_path.read_text())
    run["plan_token"] = build_plan(task).confirmation_token
    run["selected_parameters"] = run["candidates"][1]["parameters"]
    run["selected_metrics"] = run["candidates"][1]["metrics"]
    write(run_path, run)
    policy = _policy(run_path).model_copy(update={"anchor_candidate_index": 2})
    result = relinearize_operating_point(policy, run_path)
    policy_path = write(tmp_path / "policy.json", policy.model_dump(mode="json"))
    model_path = write(tmp_path / "model.json", result.model_dump(mode="json"))
    return task_path, run_path, model_path, policy_path, template_path


def test_next_recomputes_existing_model_and_strips_authority(modeled_source):
    task, run, model, policy, template = modeled_source
    report, draft = suggest_design_next(task, run, model_path=model, policy_path=policy, template_path=template)
    assert report["status"] == "local_model_draft_ready"
    assert draft is not None and draft.candidate_set
    assert not draft.safety.allow_remote_compute and not draft.safety.allow_remote_write
    assert not draft.safety.replace_existing and report["task_draft"]["user_confirmation_required"]
    assert report["model"]["sensitivities"] and report["model"]["proposals"]
    assert build_plan(draft)


def test_next_rejects_modified_model(modeled_source):
    task, run, model, policy, template = modeled_source
    raw = json.loads(model.read_text())
    raw["models"][0]["normalized_sensitivities"]["device_width_um"] += 100
    write(model, raw)
    with pytest.raises(ValueError, match="recomputation"):
        suggest_design_next(task, run, model_path=model, policy_path=policy, template_path=template)


@pytest.mark.parametrize("field,value", [("pdk_profile", "nics4304_tsmc18"), ("target", {"library": "vda_test", "cell": "vda_other"})])
def test_next_rejects_context_drift(modeled_source, field, value):
    task, run, model, policy, template = modeled_source
    raw = json.loads(template.read_text())
    raw[field] = value
    write(template, raw)
    with pytest.raises(ValueError, match="changes reviewed context"):
        suggest_design_next(task, run, model_path=model, policy_path=policy, template_path=template)


def test_old_plan_requires_existing_exact_byte_audit(modeled_source, tmp_path):
    from virtuoso_design_agent.design_review import file_reference
    task, run, *_ = modeled_source
    raw = json.loads(run.read_text())
    raw["plan_token"] = "historic-plan-token"
    write(run, raw)
    with pytest.raises(ValueError, match="plan token mismatch"):
        build_design_review(task, run)
    audit = {
        "schema_version": 1, "task_id": raw["task_id"], "task_sha256": file_reference(task)["sha256"],
        "run_sha256": file_reference(run)["sha256"], "plan_token": raw["plan_token"],
        "candidate_execution_gate_passed": True, "evaluated_candidate_count": len(raw["candidates"]),
        "validation_evidence_source": "software_inference", "candidates": [
            {"index": c["index"], "parameters": c["parameters"], "comparisons": [
                {"metric": name, "measured_value": value} for name, value in c["metrics"].items()
            ]} for c in raw["candidates"]
        ],
    }
    path = write(tmp_path / "synthetic-audit.json", audit)
    report = build_design_review(task, run, binding_audit_path=path)
    assert report["binding"]["mode"] == "historical_exact_byte_audit"
    assert not report["binding"]["execution_authorized"]
    audit["run_sha256"] = "0" * 64
    write(path, audit)
    with pytest.raises(ValueError, match="exact task/run bytes"):
        build_design_review(task, run, binding_audit_path=path)


def test_next_cli_generates_safe_draft(modeled_source, tmp_path):
    task, run, model, policy, template = modeled_source
    output = tmp_path / "next-task.json"
    assert main(["design-next", str(task), str(run), "--model", str(model), "--policy", str(policy),
                 "--task-template", str(template), "--task-output", str(output)]) == 0
    draft = TaskSpec.model_validate_json(output.read_text())
    assert not draft.safety.allow_remote_write and not draft.safety.allow_remote_compute


def test_next_rejects_uncovered_model_instead_of_random_values(modeled_source):
    from virtuoso_design_agent.op_relinearization import OperatingPointRelinearizationPolicy, relinearize_operating_point
    task, run, model, policy, template = modeled_source
    raw = json.loads(run.read_text())
    raw["candidates"][3]["metrics"]["low_frequency_gain_v_per_v"] += 10
    write(run, raw)
    from virtuoso_design_agent.design_review import file_reference
    p = json.loads(policy.read_text())
    p["expected_source_run_sha256"] = file_reference(run)["sha256"]
    write(policy, p)
    recomputed = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(p), run)
    write(model, recomputed.model_dump(mode="json"))
    report, draft = suggest_design_next(task, run, model_path=model, policy_path=policy, template_path=template)
    assert report["status"] == "model_not_validated" and draft is None


def test_optional_small_signal_dispatch_reuses_solver(modeled_source, monkeypatch):
    from types import SimpleNamespace
    from virtuoso_design_agent import design_next
    from virtuoso_design_agent.models import RunStatus
    task, run, *_ = modeled_source
    called = []

    def analyze(policy, path):
        called.append((policy.expected_pdk_profile, path))
        return SimpleNamespace(status=RunStatus.PARTIAL, analysis_scope="low_frequency_conductance",
                               covered_metrics=[], uncovered_metrics=["bandwidth_3db_hz"], eda_comparison={},
                               missing_quantities_by_instance={"MN0": ["gmb_s"]}, assumptions=[], warnings=[],
                               source_action_sha256="a" * 64)

    monkeypatch.setattr(design_next, "analyze_operating_point_small_signal_run", analyze)
    report, draft = suggest_design_next(task, run, small_signal_policy_path=(
        ROOT / "examples/theory/common-source-op-small-signal-policy.json"))
    assert called == [("nics4304_tsmc28", run)]
    assert report["small_signal"]["status"] == "partial"
    assert report["small_signal"]["uncovered_metrics"] == ["bandwidth_3db_hz"]
    assert draft is None


def test_draft_cannot_relax_original_library_or_prefix(modeled_source):
    task, run, model, policy, template = modeled_source
    original = TaskSpec.model_validate_json(task.read_text())
    raw = json.loads(template.read_text())
    raw["safety"].update(allowed_library=None, required_cell_prefix="")
    write(template, raw)
    _, draft = suggest_design_next(task, run, model_path=model, policy_path=policy, template_path=template)
    assert draft is not None
    assert draft.safety.allowed_library == original.safety.allowed_library
    assert draft.safety.required_cell_prefix == original.safety.required_cell_prefix
