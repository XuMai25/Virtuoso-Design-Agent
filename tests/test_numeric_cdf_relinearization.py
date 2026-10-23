"""Synthetic local response fixtures; these tests never run Bridge or EDA."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import CandidateEvaluation, RunRecord, SearchAudit, TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, build_task_from_relinearization, relinearize_operating_point,
    validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.spectre_values import spectre_scalar

from test_generic_simulation import _tune_payload
from test_measurement_iteration import write
from test_op_relinearization import _policy, _write_source_run


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def raw_source(tmp_path):
    # Reuse the existing solver fixture, fixing bias and exposing width as literal CDF.
    run_path = _write_source_run(tmp_path, points=[(0, 0), (1, 0), (-1, 0), (2, 0)])
    run = json.loads(run_path.read_text())
    raw = _tune_payload(max_iterations=4)
    raw["id"] = run["task_id"]
    raw["instance_parameter_space"][0]["values"] = ["900n", "1u", "1.1u", "1.2u"]
    raw["instance_parameter_updates"] = [{"instance": "MN0", "parameters": {"l": "30n"}}]
    raw["constraints"] = [{"metric": "output_dc_v", "relation": ">=", "value": .1},
                          {"metric": "low_frequency_gain_db", "relation": ">=", "value": 4}]
    task = TaskSpec.model_validate(raw)
    task_path = write(tmp_path / "task.json", raw)
    run["plan_token"] = build_plan(task).confirmation_token
    for candidate in run["candidates"]:
        width = candidate["parameters"]["device_width_um"]
        candidate["parameters"] = {}
        candidate["instance_parameters"] = {"MN0": {"w": f"{width:g}u", "l": "30n"}}
        candidate["metrics"] = {"output_dc_v": candidate["metrics"]["gm_us"] / 200,
                                "low_frequency_gain_db": candidate["metrics"]["low_frequency_gain_v_per_v"]}
        candidate["metric_sources"] = {name: "eda_result" for name in candidate["metrics"]}
        candidate["constraints"] = [c.model_dump(mode="json")
                                    for c in evaluate_constraints(candidate["metrics"], task.constraints)]
        candidate["objective_value"] = candidate["metrics"]["low_frequency_gain_db"]
    run["selected_parameters"] = {}
    run["selected_instance_parameters"] = run["candidates"][1]["instance_parameters"]
    run["selected_metrics"] = run["candidates"][1]["metrics"]
    run["notes"] = ["Synthetic local unit/compilation fixture; not actual EDA."]
    write(run_path, run)
    policy = _policy(run_path).model_dump(mode="json")
    policy.update(anchor_candidate_index=2, parameters=[{
        "name": "device_width_um", "minimum": .8, "maximum": 1.3, "proposal_step": .05,
        "quantization_grid": .005,
        "instance_parameter": {"instance": "MN0", "parameter": "w", "unit": "um",
                               "literal_format": "spectre_si", "meaning": "Explicit fixture width in micrometres."},
    }], metrics=[{"metric": "output_dc_v", "role": "operating_point", "maximum_holdout_error_percent": .001},
                 {"metric": "low_frequency_gain_db", "role": "performance", "maximum_holdout_error_percent": .001}],
        constraints=raw["constraints"], objective=raw["objective"], evidence_source="software_inference")
    template = copy.deepcopy(raw)
    template["id"] = "numeric-cdf-next"
    template.pop("instance_parameter_space")
    template_path = write(tmp_path / "template.json", template)
    return task_path, run_path, policy, template_path


def test_raw_cdf_model_compiles_to_original_task_and_safe_next_with_units(raw_source, tmp_path):
    task_path, run_path, raw_policy, template_path = raw_source
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    result = relinearize_operating_point(policy, run_path)
    assert result.gate_passed and result.fixed_parameters == {}
    assert result.fixed_instance_parameters == {"MN0": {"l": "30n"}}
    assert result.parameter_bindings["device_width_um"].unit == "um"
    assert result.models[0].maximum_holdout_error_percent < 1e-10
    candidates = result.candidate_set.candidates
    assert all(not candidate.parameters for candidate in candidates)  # Alias is never an executable semantic knob.
    assert candidates[0].instance_parameters() == {"MN0": {"w": "1.1u"}}  # Exact anchor literal retained.
    assert {round(spectre_scalar(c.instance_parameters()["MN0"]["w"]) * 1e6, 6) for c in candidates} == {1.05, 1.1, 1.15}
    policy_path = write(tmp_path / "policy.json", policy.model_dump(mode="json"))
    result_path = write(tmp_path / "model.json", result.model_dump(mode="json"))
    compiled = build_task_from_relinearization(result_path, template_path)
    assert compiled.instance_parameter_space == [] and compiled.instance_parameter_updates[0].parameters == {"l": "30n"}
    report, draft = suggest_design_next(task_path, run_path, model_path=result_path,
                                        policy_path=policy_path, template_path=template_path)
    assert draft is not None and report["status"] == "local_model_draft_ready"
    assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write,
                    draft.safety.replace_existing, draft.create_if_missing))
    assert draft.generic_simulation == TaskSpec.model_validate_json(task_path.read_text()).generic_simulation
    assert report["model"]["proposals"][1]["instance_changes"]["MN0"]["w"]["before"] == "1.1u"
    assert "模型单位 um" in render_design_next(report)
    assert build_plan(draft).confirmation_token


def test_equivalent_numeric_cdf_spellings_give_same_model(raw_source):
    _, run_path, raw_policy, _ = raw_source
    baseline = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    run = json.loads(run_path.read_text())
    run["candidates"][2]["instance_parameters"]["MN0"]["w"] = "9e-7"
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    converted = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    assert converted.models == baseline.models
    assert converted.candidate_set.candidates == baseline.candidate_set.candidates


@pytest.mark.parametrize("literal", ["width*2", "1M", "0", "1e999"])
def test_nonliteral_ambiguous_or_nonpositive_cdf_cannot_be_modeled(raw_source, literal):
    _, run_path, raw_policy, _ = raw_source
    run = json.loads(run_path.read_text())
    run["candidates"][0]["instance_parameters"]["MN0"]["w"] = literal
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="positive literal parameter"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)


@pytest.mark.parametrize("field", ["testbench", "topology", "raw_parameter"])
def test_unmodeled_changes_cannot_be_attributed_to_width(raw_source, field):
    _, run_path, raw_policy, _ = raw_source
    run = json.loads(run_path.read_text())
    if field == "testbench":
        run["candidates"][3]["testbench_overrides"] = {"sources": {"VIN_SRC": {"dc_value": .4}}}
    elif field == "topology":
        run["candidates"][3]["topology_sha256"] = "f" * 64
    else:
        run["candidates"][3]["instance_parameters"]["MN0"]["l"] = "40n"
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="vary across source|vary across source points"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)


def test_failed_raw_holdout_does_not_compile_and_mapping_is_explicit(raw_source, tmp_path):
    _, run_path, raw_policy, template_path = raw_source
    implicit = copy.deepcopy(raw_policy)
    implicit["parameters"][0]["instance_parameter"].pop("literal_format")
    with pytest.raises(ValueError):
        OperatingPointRelinearizationPolicy.model_validate(implicit)
    run = json.loads(run_path.read_text())
    run["candidates"][3]["metrics"]["low_frequency_gain_db"] += 5
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    result = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    assert not result.gate_passed and result.candidate_set is None
    result_path = write(tmp_path / "failed.json", result.model_dump(mode="json"))
    with pytest.raises(ValueError, match="only a passed"):
        build_task_from_relinearization(result_path, template_path)


def test_raw_mapping_cannot_bypass_original_permissions_or_shadow_aliases(raw_source, tmp_path):
    _, run_path, raw_policy, template_path = raw_source
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    result = relinearize_operating_point(policy, run_path)
    result_path = write(tmp_path / "model.json", result.model_dump(mode="json"))
    raw = json.loads(template_path.read_text())
    raw["design_context"]["instance_parameter_permissions"][0]["modes"] = ["fixed"]
    write(template_path, raw)
    with pytest.raises(ValueError, match="permission|search"):
        build_plan(build_task_from_relinearization(result_path, template_path))
    duplicate = copy.deepcopy(raw_policy)
    duplicate["parameters"].append({**duplicate["parameters"][0], "name": "alias_again"})
    with pytest.raises(ValueError, match="must not alias"):
        OperatingPointRelinearizationPolicy.model_validate(duplicate)


def test_legacy_semantic_policy_serialization_does_not_gain_null_bindings(tmp_path):
    source = _write_source_run(tmp_path)
    policy = _policy(source)
    assert all("instance_parameter" not in p for p in policy.model_dump(mode="json")["parameters"])
    assert relinearize_operating_point(policy, source).gate_passed


def test_condition_results_can_change_but_declared_conditions_must_not(raw_source):
    _, run_path, raw_policy, _ = raw_source
    run = json.loads(run_path.read_text())
    for candidate in run["candidates"]:
        candidate["operating_conditions"] = [{
            "name": "tt", "process_corner": "tt", "temperature_c": 25, "vdd_v": .9,
            "parameters": {}, "metrics": candidate["metrics"], "constraints": candidate["constraints"],
            "feasible": True, "total_violation": 0, "evidence_source": "eda_result",
        }]
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    assert relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path).gate_passed
    run["candidates"][3]["operating_conditions"][0]["temperature_c"] = 125
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="operating conditions vary"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)


def test_no_predicted_improvement_stops_without_another_task(raw_source, tmp_path):
    task_path, run_path, raw_policy, template_path = raw_source
    raw_policy["parameters"][0]["maximum"] = 1.1  # Selected width is already at this explicit upper bound.
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    result = relinearize_operating_point(policy, run_path)
    assert result.gate_passed
    model_path = write(tmp_path / "model.json", result.model_dump(mode="json"))
    policy_path = write(tmp_path / "policy.json", policy.model_dump(mode="json"))
    report, draft = suggest_design_next(task_path, run_path, model_path=model_path,
                                        policy_path=policy_path, template_path=template_path)
    assert draft is None and report["status"] == "no_predicted_improvement"
    assert report["model"]["objective_improvement"]["predicted_improving_count"] == 0
    assert not report["execution_authorized"]
    assert "不是全局最优证明" in render_design_next(report)


def test_grid_rounding_never_expands_explicit_numeric_cdf_bounds(raw_source):
    _, run_path, raw_policy, _ = raw_source
    raw_policy["parameters"][0].update(maximum=1.26, proposal_step=.15, quantization_grid=.1)
    # 1.1 + .15 -> 1.25 -> nearest-grid 1.3, which must be excluded, not written.
    result = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    assert all(.8 <= p.parameters["device_width_um"] <= 1.26 for p in result.proposals)
    assert all(.8 <= spectre_scalar(c.instance_parameters()["MN0"]["w"]) * 1e6 <= 1.26
               for c in result.candidate_set.candidates)


def test_existing_post_run_audit_checks_raw_atomic_parameters(raw_source, tmp_path):
    task_path, run_path, raw_policy, template_path = raw_source
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    result = relinearize_operating_point(policy, run_path)
    model_path = write(tmp_path / "model.json", result.model_dump(mode="json"))
    policy_path = write(tmp_path / "policy.json", policy.model_dump(mode="json"))
    _, draft = suggest_design_next(task_path, run_path, model_path=model_path,
                                   policy_path=policy_path, template_path=template_path)
    path = write(tmp_path / "task-next.json", draft.model_dump(mode="json"))
    evaluations = []
    for index, atomic in enumerate(draft.candidate_set.candidates, 1):
        instance_parameters = {u.instance: dict(u.parameters) for u in draft.instance_parameter_updates}
        for instance, fields in atomic.instance_parameters().items():
            instance_parameters.setdefault(instance, {}).update(fields)
        constraints = evaluate_constraints(atomic.predicted_metrics, draft.constraints)
        evaluations.append(CandidateEvaluation(
            index=index, parameters={}, instance_parameters=instance_parameters, metrics=atomic.predicted_metrics,
            constraints=constraints, feasible=all(c.passed for c in constraints), total_violation=0,
            objective_value=atomic.predicted_metrics[draft.objective.metric], evidence_source="eda_result",
            metric_sources={name: "eda_result" for name in atomic.predicted_metrics},
            atomic_candidate_id=atomic.id, atomic_candidate_predicted_metrics=atomic.predicted_metrics,
            atomic_candidate_evidence_source="software_inference",
        ))
    source_run = RunRecord.model_validate_json(run_path.read_text())
    selected = max(evaluations, key=lambda c: c.objective_value)
    run = source_run.model_copy(update={
        "task_id": draft.id, "plan_token": build_plan(draft).confirmation_token, "candidates": evaluations,
        "selected_parameters": {}, "selected_instance_parameters": selected.instance_parameters,
        "selected_metrics": selected.metrics,
        "search_audit": SearchAudit(
            declared_candidate_count=len(evaluations), attempted_candidate_count=len(evaluations),
            completed_candidate_count=len(evaluations), domain_exhausted=True,
            selection_scope="best_in_declared_discrete_domain", statement="Synthetic audit fixture only",
            candidate_set_source=draft.candidate_set.source,
        ),
    })
    new_run_path = write(tmp_path / "synthetic-next-run.json", run.model_dump(mode="json"))
    assert validate_relinearization_run(model_path, path, new_run_path, policy_path).gate_passed
    run.candidates[0].instance_parameters["MN0"]["w"] = "2u"
    write(new_run_path, run.model_dump(mode="json"))
    with pytest.raises(ValueError, match="atomic provenance mismatch"):
        validate_relinearization_run(model_path, path, new_run_path, policy_path)
