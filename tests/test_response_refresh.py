"""Original exact-domain audits feeding local refresh; never re-execute a run."""

import copy
import json
from types import SimpleNamespace

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.design_review import selected_candidate
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import RunRecord, TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, relinearize_operating_point, validate_relinearization_run,
)

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha
from test_op_relinearization import _write_atomic_eda_run, _write_compiled_relinearization


@pytest.mark.parametrize("bad_prediction", [False, True])
def test_prior_accuracy_and_completed_eda_selection_are_separate(tmp_path, bad_prediction):
    model_path, task_path, task = _write_compiled_relinearization(tmp_path)
    offsets = {(2, "gm_us"): 1.0} if bad_prediction else None
    run_path = _write_atomic_eda_run(tmp_path, task, metric_offsets=offsets)
    report, draft = suggest_design_next(task_path, run_path, prior_model_path=model_path)
    audit = report["response_audit"]
    assert audit["candidate_execution_gate_passed"] and audit["recommendation_agreement"]
    assert audit["prediction_accuracy_gate_passed"] is not bad_prediction
    assert report["status"] == ("local_model_refresh_required" if bad_prediction else "response_verified_review_or_stop")
    assert draft is None and not report["execution_authorized"]
    assert not audit["selected_recorded_inputs"]["current_remote_state_verified"]
    assert "不撤销真实 EDA 选优" in render_design_next(report)


def test_historical_identity_requires_exact_existing_audit_and_never_inherits_accuracy(tmp_path, monkeypatch):
    import virtuoso_design_agent.op_relinearization as op
    model_path, task_path, task = _write_compiled_relinearization(tmp_path)
    run_path = _write_atomic_eda_run(tmp_path, task, metric_offsets={(2, "gm_us"): 1.0})
    run = json.loads(run_path.read_text())
    run["plan_token"] = "synthetic-older-plan-format"
    write(run_path, run)
    # Simulate a saved audit produced under a former planner, not a production
    # token rewrite. The fixture remains explicitly synthetic and never executes.
    with monkeypatch.context() as patch:
        patch.setattr(op, "build_plan", lambda task: SimpleNamespace(confirmation_token=run["plan_token"]))
        saved_audit = validate_relinearization_run(model_path, task_path, run_path)
    path = write(tmp_path / "old-exact-audit.json", saved_audit.model_dump(mode="json"))
    with pytest.raises(ValueError, match="plan token"):
        validate_relinearization_run(model_path, task_path, run_path)
    report, draft = suggest_design_next(task_path, run_path, binding_audit_path=path, prior_model_path=model_path)
    assert draft is None and report["status"] == "local_model_refresh_required"
    assert report["response_audit"]["historical_identity_binding"] == "historical_exact_byte_audit"
    assert any("recomputed, not inherited" in note for note in report["response_audit"]["notes"])
    invalid = saved_audit.model_dump(mode="json")
    invalid["run_sha256"] = "0" * 64
    bad = write(tmp_path / "not-an-identity-audit.json", invalid)
    with pytest.raises(ValueError, match="exact task/run bytes"):
        suggest_design_next(task_path, run_path, binding_audit_path=bad, prior_model_path=model_path)


def test_legacy_policy_hash_recovers_only_the_known_omitted_option_and_never_changed_limits(tmp_path):
    from virtuoso_design_agent.op_relinearization import (
        OperatingPointRelinearizationResult, _canonical_sha256, _load_bound_validation_policy,
    )
    from test_op_relinearization import _policy, _write_source_run
    source = _write_source_run(tmp_path)
    policy = _policy(source)
    declaration = policy.model_dump(mode="json")
    declaration.pop("include_measured_anchor")
    old_result = relinearize_operating_point(policy, source).model_dump(mode="json")
    old_result["policy_sha256"] = _canonical_sha256(declaration)
    for metric in old_result["models"]:
        metric.pop("relative_error_floor")
    result = OperatingPointRelinearizationResult.model_validate(old_result)
    path = write(tmp_path / "original-policy.json", declaration)
    bound, digest = _load_bound_validation_policy(result, path)
    assert digest == old_result["policy_sha256"] and bound.metrics == policy.metrics
    changed = copy.deepcopy(declaration)
    changed["metrics"][0]["relative_error_floor"] *= 2
    with pytest.raises(ValueError, match="policy hash"):
        _load_bound_validation_policy(result, write(tmp_path / "changed-floor.json", changed))
    explicit = {**declaration, "include_measured_anchor": True}
    with pytest.raises(ValueError, match="policy hash"):
        _load_bound_validation_policy(result, write(tmp_path / "new-explicit-option.json", explicit))


def test_old_model_failure_can_refresh_locally_without_new_circuit_or_execution(raw_source, tmp_path):
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_source[2])
    old_model = relinearize_operating_point(policy, raw_source[1])
    old_model_path = write(tmp_path / "old-model.json", old_model.model_dump(mode="json"))
    old_policy = write(tmp_path / "old-policy.json", policy.model_dump(mode="json"))
    _, draft = suggest_design_next(raw_source[0], raw_source[1], model_path=old_model_path,
                                   policy_path=old_policy, template_path=raw_source[3])
    task_path = write(tmp_path / "completed-task.json", draft.model_dump(mode="json"))
    run_path = _write_atomic_eda_run(tmp_path, draft)
    run = RunRecord.model_validate_json(run_path.read_text())
    for candidate in run.candidates:
        width = float(candidate.instance_parameters["MN0"]["w"].rstrip("u"))
        candidate.metrics["low_frequency_gain_db"] += .1 * width
        candidate.objective_value = candidate.metrics["low_frequency_gain_db"]
        candidate.constraints = evaluate_constraints(candidate.metrics, draft.constraints)
    winner = max(run.candidates, key=lambda c: c.objective_value)
    run.selected_parameters = dict(winner.parameters)
    run.selected_instance_parameters = copy.deepcopy(winner.instance_parameters)
    run.selected_metrics = dict(winner.metrics)
    write(run_path, run.model_dump(mode="json"))
    selected = selected_candidate(run)
    indices = [c.index for c in run.candidates if c.index != selected.index]
    refreshed_policy = policy.model_dump(mode="json")
    refreshed_policy.update(id="explicit-local-refresh", expected_source_task_id=draft.id,
                            expected_source_run_sha256=sha(run_path), anchor_candidate_index=selected.index,
                            training_candidate_indices=[selected.index, indices[0]], holdout_candidate_indices=[indices[1]])
    refreshed = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(refreshed_policy), run_path)
    assert refreshed.gate_passed
    model_path = write(tmp_path / "refreshed-model.json", refreshed.model_dump(mode="json"))
    policy_path = write(tmp_path / "refreshed-policy.json", refreshed_policy)
    template = draft.model_dump(mode="json")
    template.pop("candidate_set")
    template["id"] = "refreshed-next-task"
    template_path = write(tmp_path / "refreshed-template.json", template)
    report, next_task = suggest_design_next(task_path, run_path, prior_model_path=old_model_path,
        model_path=model_path, policy_path=policy_path, template_path=template_path)
    assert report["status"] == "local_model_draft_ready" and next_task is not None
    assert not report["response_audit"]["prediction_accuracy_gate_passed"]
    assert report["model"]["gate_passed"] and report["model"]["anchor_candidate_index"] == selected.index
    assert not any((next_task.safety.allow_remote_write, next_task.safety.allow_remote_compute, next_task.create_if_missing))
