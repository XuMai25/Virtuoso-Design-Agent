"""Decision regressions over retained/synthetic local models; no EDA jobs."""

import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.op_relinearization import OperatingPointRelinearizationPolicy, relinearize_operating_point
from virtuoso_design_agent.refinement_decision import RefinementDecisionPolicy, assess_refinement_utility

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha


def model_bundle(source, tmp_path):
    policy = OperatingPointRelinearizationPolicy.model_validate(source[2])
    model = relinearize_operating_point(policy, source[1])
    return model, write(tmp_path / "model.json", model.model_dump(mode="json")), write(tmp_path / "policy.json", policy.model_dump(mode="json"))


def test_small_gain_inside_observed_error_keeps_diagnostics_not_a_task(raw_source, tmp_path):
    run = json.loads(raw_source[1].read_text())
    run["candidates"][3]["metrics"]["low_frequency_gain_db"] += .01
    write(raw_source[1], run)
    raw_source[2]["expected_source_run_sha256"] = sha(raw_source[1])
    raw_source[2]["metrics"][1]["maximum_holdout_error_percent"] = 1
    raw_source[2]["parameters"][0].update(proposal_step=.0001, quantization_grid=.0001)
    model, model_path, policy_path = model_bundle(raw_source, tmp_path)
    assert model.gate_passed
    report, draft = suggest_design_next(raw_source[0], raw_source[1], model_path=model_path,
                                       policy_path=policy_path, template_path=raw_source[3])
    assert draft is None and report["status"] == "predicted_gain_not_resolved"
    utility = report["model"]["utility"]
    assert 0 < utility["best_predicted_improvement"] < utility["maximum_observed_holdout_absolute_error"]
    assert utility["policy"]["evidence_source"] == "software_inference"
    assert "不是置信区间" in render_design_next(report)


@pytest.mark.parametrize("setting,status", [
    ({"minimum_objective_improvement": 1}, "predicted_gain_not_resolved"),
    ({"minimum_relative_improvement_percent": 10}, "predicted_gain_not_resolved"),
    ({"minimum_predicted_slack": {"output_dc_v": 1}}, "predicted_headroom_insufficient"),
    ({"maximum_estimated_action_seconds": 1}, "action_cost_budget_exceeded"),
])
def test_declared_benefit_headroom_and_cost_are_not_silently_ignored(raw_source, tmp_path, setting, status):
    model, _, _ = model_bundle(raw_source, tmp_path)
    result = assess_refinement_utility(model, model.models[1].anchor_value,
                                      RefinementDecisionPolicy.model_validate(setting),
                                      historical_action_seconds=40, historical_candidate_count=4)
    assert result["status"] == status and not result["task_draft_allowed"]
    assert result["cost"]["estimated_action_seconds"] == 30


def test_missing_cost_is_unknown_not_free_and_unknown_slack_is_rejected(raw_source, tmp_path):
    model, model_path, policy_path = model_bundle(raw_source, tmp_path)
    decision = write(tmp_path / "decision.json", {"maximum_estimated_action_seconds": 100})
    report, draft = suggest_design_next(raw_source[0], raw_source[1], model_path=model_path,
                                       policy_path=policy_path, template_path=raw_source[3], decision_policy_path=decision)
    assert draft is None and report["status"] == "action_cost_unknown"
    assert report["model"]["utility"]["cost"]["agent_thinking_seconds"] is None
    with pytest.raises(ValueError, match="modeled constraints"):
        assess_refinement_utility(model, 5.5, RefinementDecisionPolicy(minimum_predicted_slack={"unknown": 1}),
                                  historical_action_seconds=0, historical_candidate_count=0)


def test_explicit_policy_is_local_and_does_not_grant_execution(raw_source, tmp_path):
    _, model_path, policy_path = model_bundle(raw_source, tmp_path)
    decision = write(tmp_path / "decision.json", {"minimum_objective_improvement": .1})
    report, draft = suggest_design_next(raw_source[0], raw_source[1], model_path=model_path,
                                       policy_path=policy_path, template_path=raw_source[3], decision_policy_path=decision)
    assert report["status"] == "local_model_draft_ready" and draft is not None
    assert report["sources"]["decision_policy"]["sha256"] == sha(decision)
    assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write,
                    draft.safety.replace_existing, draft.create_if_missing))


def test_cli_keeps_stop_diagnostics_without_empty_or_stale_task_replacement(raw_source, tmp_path, capsys):
    from virtuoso_design_agent.cli import main
    _, model, policy = model_bundle(raw_source, tmp_path)
    decision = write(tmp_path / "stop-policy.json", {"minimum_objective_improvement": 10})
    output, markdown, task = tmp_path / "decision.json", tmp_path / "decision.md", tmp_path / "unused-task.json"
    args = ["design-next", str(raw_source[0]), str(raw_source[1]), "--model", str(model), "--policy", str(policy),
            "--task-template", str(raw_source[3]), "--decision-policy", str(decision), "--output", str(output),
            "--markdown", str(markdown), "--task-output", str(task)]
    assert main(args) == 2
    assert json.loads(output.read_text())["status"] == "predicted_gain_not_resolved"
    assert markdown.is_file() and not task.exists()
    assert "task output was not written" in capsys.readouterr().err
    write(task, {"existing_unrelated_draft": True})
    before = task.read_bytes()
    args[args.index("--output") + 1] = str(tmp_path / "second-decision.json")
    args[args.index("--markdown") + 1] = str(tmp_path / "second-decision.md")
    assert main(args) == 2 and task.read_bytes() == before
