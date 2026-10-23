"""Local context-transfer regressions; synthetic records, never Bridge/EDA."""

from __future__ import annotations

import copy
import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.generic_simulation import GenericTestbenchOverrides
from virtuoso_design_agent.models import RunRecord, TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, build_task_from_relinearization,
    relinearize_operating_point, validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha
from test_op_relinearization import _write_atomic_eda_run


FIXED_CONTEXT = {
    "sources": {"VIN_SRC": {"dc_value": .41, "ac_magnitude": .75, "ac_phase_deg": 30}},
    "loads": {"CL0": 7e-15},
}


def model_bundle(source, tmp_path, *, with_overrides=True):
    task_path, run_path, raw_policy, template_path = source
    expected = GenericTestbenchOverrides.model_validate(FIXED_CONTEXT) if with_overrides else None
    if with_overrides:
        task = json.loads(task_path.read_text())
        run = json.loads(run_path.read_text())
        task.pop("instance_parameter_space")
        atomic = []
        for candidate in run["candidates"]:
            candidate_id = f"synthetic-source-{candidate['index']}"
            candidate.update(testbench_overrides=copy.deepcopy(FIXED_CONTEXT),
                             atomic_candidate_id=candidate_id, atomic_candidate_predicted_metrics={},
                             atomic_candidate_evidence_source="user_input")
            atomic.append({"id": candidate_id,
                           "instance_parameter_updates": [{"instance": "MN0", "parameters": {
                               "w": candidate["instance_parameters"]["MN0"]["w"]}}],
                           "testbench_overrides": copy.deepcopy(FIXED_CONTEXT)})
        task["candidate_set"] = {"source": {"id": "synthetic-fixed-bias", "generator": "fixture"}, "candidates": atomic}
        run["plan_token"] = build_plan(TaskSpec.model_validate(task)).confirmation_token
        run["selected_testbench_overrides"] = copy.deepcopy(FIXED_CONTEXT)
        run["selected_testbench_override_evidence_source"] = "user_input"
        write(task_path, task)
        write(run_path, run)
        raw_policy["expected_source_run_sha256"] = sha(run_path)
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    model = relinearize_operating_point(policy, run_path)
    model_path = write(tmp_path / "context-model.json", model.model_dump(mode="json"))
    policy_path = write(tmp_path / "context-policy.json", policy.model_dump(mode="json"))
    report, draft = suggest_design_next(task_path, run_path, model_path=model_path,
                                        policy_path=policy_path, template_path=template_path)
    assert draft is not None, report
    draft_path = write(tmp_path / "context-next-task.json", draft.model_dump(mode="json"))
    return model, model_path, policy_path, draft, draft_path, report, expected


def matching_audit_run(tmp_path, task):
    path = _write_atomic_eda_run(tmp_path, task)
    run = RunRecord.model_validate_json(path.read_text())
    run.notes.append("Synthetic context-transfer audit, not actual EDA.")
    for candidate, atomic in zip(run.candidates, task.candidate_set.candidates, strict=True):
        candidate.testbench_overrides = atomic.testbench_overrides
    selected = next(c for c in run.candidates if c.instance_parameters == run.selected_instance_parameters)
    run.selected_testbench_overrides = selected.testbench_overrides
    write(path, run.model_dump(mode="json"))
    return path, run


def test_model_and_task_preserve_fixed_source_load_context(raw_source, tmp_path):
    model, model_path, policy_path, draft, path, report, expected = model_bundle(raw_source, tmp_path)
    assert all(c.testbench_overrides == expected for c in model.candidate_set.candidates)
    assert model.fixed_testbench_overrides == expected
    assert report["model"]["fixed_testbench_overrides"] == expected.model_dump(mode="json")
    assert "固定仿真条件" in render_design_next(report)
    original = TaskSpec.model_validate_json(raw_source[0].read_text())
    assert draft.generic_simulation == original.generic_simulation  # Do not rewrite the base setup.
    assert not any((draft.safety.allow_remote_write, draft.safety.allow_remote_compute, draft.create_if_missing))
    for candidate in draft.candidate_set.candidates:
        effective = candidate.testbench_overrides.apply_to(draft.generic_simulation)
        source = next(s for s in effective.sources if s.name == "VIN_SRC")
        assert (source.dc_value, source.ac_magnitude, source.ac_phase_deg) == (.41, .75, 30)
        assert next(load.value for load in effective.loads if load.name == "CL0") == 7e-15
    run_path, _ = matching_audit_run(tmp_path, draft)
    audit = validate_relinearization_run(model_path, path, run_path, policy_path)
    assert audit.gate_passed and audit.fixed_testbench_overrides == expected


@pytest.mark.parametrize("with_overrides", [False, True])
@pytest.mark.parametrize("target", ["candidate", "selected"])
def test_audit_refuses_wrong_context_even_when_metrics_and_cdf_match(raw_source, tmp_path, with_overrides, target):
    _, model_path, policy_path, draft, path, _, _ = model_bundle(raw_source, tmp_path, with_overrides=with_overrides)
    run_path, run = matching_audit_run(tmp_path, draft)
    assert validate_relinearization_run(model_path, path, run_path, policy_path).gate_passed
    wrong = GenericTestbenchOverrides.model_validate({"sources": {"VIN_SRC": {"dc_value": .42}}})
    if target == "candidate":
        run.candidates[0].testbench_overrides = wrong
    else:
        run.selected_testbench_overrides = wrong
    write(run_path, run.model_dump(mode="json"))
    with pytest.raises(ValueError, match="provenance|selection"):
        validate_relinearization_run(model_path, path, run_path, policy_path)


def test_compiler_and_recomputation_reject_lost_context(raw_source, tmp_path):
    model, model_path, policy_path, _, _, _, _ = model_bundle(raw_source, tmp_path)
    for candidate in model.candidate_set.candidates:
        candidate.testbench_overrides = None
    write(model_path, model.model_dump(mode="json"))
    with pytest.raises(ValueError, match="fixed testbench"):
        build_task_from_relinearization(model_path, raw_source[3])
    with pytest.raises(ValueError, match="deterministic source/policy recomputation"):
        suggest_design_next(raw_source[0], raw_source[1], model_path=model_path,
                            policy_path=policy_path, template_path=raw_source[3])


def test_missing_template_source_cannot_silently_drop_fixed_context(raw_source, tmp_path):
    _, model_path, _, _, _, _, _ = model_bundle(raw_source, tmp_path)
    template = json.loads(raw_source[3].read_text())
    for source in template["generic_simulation"]["sources"]:
        if source["name"] == "VIN_SRC":
            source["name"] = "VIN_RENAMED"
    write(raw_source[3], template)
    with pytest.raises(ValueError, match="unknown sources"):
        build_task_from_relinearization(model_path, raw_source[3])
