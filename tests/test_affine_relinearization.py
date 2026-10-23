"""Explicit affine-coordinate checks, reusing old synthetic fixtures only."""

import copy
import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.op_relinearization import OperatingPointRelinearizationPolicy, relinearize_operating_point
from virtuoso_design_agent.spectre_values import spectre_scalar

from test_joint_testbench_relinearization import joint_source, saved_joint_model
from test_linked_cdf_relinearization import linked_source
from test_linked_testbench_relinearization import grouped_source, group_policy
from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha


def save_bound_source(source, run):
    from virtuoso_design_agent.models import TaskSpec
    from virtuoso_design_agent.planner import build_plan
    task = json.loads(source[0].read_text())
    for atomic, candidate in zip(task["candidate_set"]["candidates"], run["candidates"], strict=True):
        for update in atomic["instance_parameter_updates"]:
            for field in update["parameters"]:
                update["parameters"][field] = candidate["instance_parameters"][update["instance"]][field]
        if candidate.get("testbench_overrides"):
            atomic["testbench_overrides"] = copy.deepcopy(candidate["testbench_overrides"])
    anchor = next(c for c in run["candidates"] if c["index"] == source[2]["anchor_candidate_index"])
    run["selected_instance_parameters"] = copy.deepcopy(anchor["instance_parameters"])
    run["selected_testbench_overrides"] = copy.deepcopy(anchor.get("testbench_overrides"))
    run["plan_token"] = build_plan(TaskSpec.model_validate(task)).confirmation_token
    write(source[0], task)
    write(source[1], run)


def test_cdf_ratio_offset_compiles_actual_member_values_not_equal_widths(linked_source, tmp_path):
    run = json.loads(linked_source[1].read_text())
    for candidate in run["candidates"]:
        width = spectre_scalar(candidate["instance_parameters"]["MN0"]["w"])
        candidate["instance_parameters"]["MN1"]["w"] = f"{2 * width + .1e-6:.12g}"
    save_bound_source(linked_source, run)
    policy = copy.deepcopy(linked_source[2])
    policy["expected_source_run_sha256"] = sha(linked_source[1])
    policy["parameters"][0]["instance_parameter"]["linked_parameters"] = [
        {"instance": "MN1", "parameter": "w", "scale": 2, "offset": .1, "quantization_grid": .005}]
    source = (*linked_source[:2], policy, linked_source[3])
    model, model_path, policy_path = saved_joint_model(source, tmp_path)
    assert model.gate_passed and len(model.models[0].normalized_sensitivities) == 1
    assert model.model_dump(mode="json", exclude_none=True)["parameter_bindings"][policy["parameters"][0]["name"]]["linked_parameters"] == [
        {"instance": "MN1", "parameter": "w", "scale": 2.0, "offset": .1, "quantization_grid": .005}]
    for candidate in model.candidate_set.candidates:
        values = candidate.instance_parameters()
        assert spectre_scalar(values["MN1"]["w"]) == pytest.approx(2 * spectre_scalar(values["MN0"]["w"]) + .1e-6)
    report, task = suggest_design_next(source[0], source[1], model_path=model_path, policy_path=policy_path, template_path=source[3])
    assert task is not None and "MN1.w = 2 × MN0.w +0.1" in render_design_next(report)
    assert not task.safety.allow_remote_compute and not task.safety.allow_remote_write
    assert task.model_dump(mode="json", exclude_none=True)["candidate_set"]["candidates"]


def test_signed_bias_offsets_and_load_ratios_keep_ac_and_one_axis_per_group(grouped_source, tmp_path):
    run = json.loads(grouped_source[1].read_text())
    for candidate in run["candidates"]:
        override = candidate["testbench_overrides"]
        override["sources"]["VIN_PEER"]["dc_value"] = -.5 * override["sources"]["VIN_SRC"]["dc_value"] + .2
        override["loads"]["CL_PEER"] = 2 * override["loads"]["CL0"] + 1e-15
    write(grouped_source[1], run)
    policy = group_policy(grouped_source)
    policy["expected_source_run_sha256"] = sha(grouped_source[1])
    policy["parameters"][1]["testbench_parameter"]["linked_names"] = [{"name": "VIN_PEER", "scale": -.5, "offset": 200}]
    policy["parameters"][2]["testbench_parameter"]["linked_names"] = [{"name": "CL_PEER", "scale": 2, "offset": 1}]
    save_bound_source(grouped_source, run)
    policy["expected_source_run_sha256"] = sha(grouped_source[1])
    source = (*grouped_source[:2], policy, grouped_source[3])
    model, model_path, policy_path = saved_joint_model(source, tmp_path)
    report, draft = suggest_design_next(source[0], source[1], model_path=model_path, policy_path=policy_path, template_path=source[3])
    assert draft is not None and len(model.models[0].normalized_sensitivities) == 3
    for candidate in draft.candidate_set.candidates:
        override = candidate.testbench_overrides
        assert override.sources["VIN_PEER"].dc_value == pytest.approx(-.5 * override.sources["VIN_SRC"].dc_value + .2, abs=1e-16)
        assert override.loads["CL_PEER"] == pytest.approx(2 * override.loads["CL0"] + 1e-15, abs=1e-28)
        assert (override.sources["VIN_PEER"].ac_magnitude, override.sources["VIN_PEER"].ac_phase_deg) == (.6, 195)
    assert "-0.5 × VIN_SRC.dc_value +200" in render_design_next(report)
    assert any(p.get("testbench_changes", {}).get("bias_mv", {}).get("member_values", {}).get("VIN_PEER")
               for p in report["model"]["proposals"])


def test_dependent_grid_excludes_points_instead_of_rounding_and_breaking_ratio(linked_source, tmp_path):
    run = json.loads(linked_source[1].read_text())
    for candidate in run["candidates"]:
        width = spectre_scalar(candidate["instance_parameters"]["MN0"]["w"])
        candidate["instance_parameters"]["MN1"]["w"] = f"{1.2 * width:.12g}"
    write(linked_source[1], run)
    policy = copy.deepcopy(linked_source[2])
    policy["expected_source_run_sha256"] = sha(linked_source[1])
    policy["parameters"][0].update(proposal_step=.005)
    policy["parameters"][0]["instance_parameter"]["linked_parameters"] = [
        {"instance": "MN1", "parameter": "w", "scale": 1.2, "quantization_grid": .005}]
    model = relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(policy), linked_source[1])
    assert len(model.proposals) == 1 and model.proposals[0].previously_evaluated
    assert any("2 proposal tuples excluded" in note for note in model.notes)


@pytest.mark.parametrize("relation", [{"scale": 0}, {"scale": float("inf")}, {"scale": 1, "offset": -2}])
def test_invalid_or_nonpositive_cdf_relation_cannot_enter_the_model(raw_source, relation):
    policy = copy.deepcopy(raw_source[2])
    policy["parameters"][0]["instance_parameter"]["linked_parameters"] = [{"instance": "MN1", "parameter": "w", **relation}]
    with pytest.raises(ValueError):
        OperatingPointRelinearizationPolicy.model_validate(policy)
