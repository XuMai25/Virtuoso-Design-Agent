"""Retained-point eligibility, not new physical measurements."""

from __future__ import annotations

import copy
import json

import pytest

from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.op_relinearization import OperatingPointRelinearizationPolicy, relinearize_operating_point
from virtuoso_design_agent.planner import build_plan

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha


def extra_record(source, case):
    task_path, run_path, policy, _ = source
    run = json.loads(run_path.read_text())
    extra = copy.deepcopy(run["candidates"][0])
    extra.update(index=5, instance_parameters={"MN0": {"w": "1.15u", "l": "30n"}})
    if case == "other_length":
        extra["instance_parameters"]["MN0"]["l"] = "40n"
    elif case == "incomplete":
        extra["analysis_complete"] = False
    elif case == "non_eda_metric":
        extra["metric_sources"]["low_frequency_gain_db"] = "software_inference"
    elif case == "off_grid":
        extra["instance_parameters"]["MN0"]["w"] = "1.149u"
    run["candidates"].append(extra)
    task = json.loads(task_path.read_text())
    task["instance_parameter_space"][0]["values"].append(extra["instance_parameters"]["MN0"]["w"])
    if case == "other_length":
        task["instance_parameter_updates"] = []
        task["instance_parameter_space"].append({"instance": "MN0", "parameter": "l", "values": ["30n", "40n"]})
    task["limits"]["max_iterations"] = 10
    run["plan_token"] = build_plan(TaskSpec.model_validate(task)).confirmation_token
    write(task_path, task)
    write(run_path, run)
    policy["expected_source_run_sha256"] = sha(run_path)
    return relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(policy), run_path)


@pytest.mark.parametrize("case", ["other_length", "incomplete", "non_eda_metric", "off_grid"])
def test_a_different_or_unverified_tuple_cannot_hide_an_unmeasured_improving_point(raw_source, tmp_path, case):
    model = extra_record(raw_source, case)
    assert model.gate_passed
    assert any(p.parameters["device_width_um"] == pytest.approx(1.15) for p in model.proposals)
    assert any("source candidate 5" in note and "not reused" in note for note in model.notes)
    model_path = write(tmp_path / "model.json", model.model_dump(mode="json"))
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_source[2])
    policy_path = write(tmp_path / "policy.json", policy.model_dump(mode="json"))
    report, draft = suggest_design_next(raw_source[0], raw_source[1], model_path=model_path,
                                        policy_path=policy_path, template_path=raw_source[3])
    if case == "incomplete":
        assert report["status"] == "reconcile_execution_first" and draft is None
        return  # Eligibility is not permission to replay an uncertain operation.
    assert report["status"] == "local_model_draft_ready" and draft is not None
    assert report["model"]["notes"] == model.notes
    assert not any((draft.safety.allow_remote_write, draft.safety.allow_remote_compute, draft.create_if_missing))


def test_complete_same_context_exact_grid_point_is_still_not_proposed_again(raw_source):
    model = extra_record(raw_source, "exact_valid")
    assert model.gate_passed
    assert not any(p.parameters["device_width_um"] == pytest.approx(1.15) for p in model.proposals)
