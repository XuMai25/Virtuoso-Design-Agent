"""Reuse local response fixtures to diagnose sampling, not to invent measurements."""

import copy
import json

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, RelinearizationDataRequest, relinearize_operating_point,
)
from virtuoso_design_agent.relinearization_data import inspect_relinearization_data

from test_joint_testbench_relinearization import joint_source
from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha


def test_readiness_is_not_a_fit_and_cannot_bypass_strict_policy(joint_source, tmp_path):
    request = RelinearizationDataRequest.model_validate(joint_source[2])
    report = inspect_relinearization_data(request, joint_source[1])
    assert report["strict_fit_inputs_ready"] and report["joint_training_independence"]
    assert not report["fit_performed"] and not report["task_produced"]
    with pytest.raises(ValueError, match="strict OperatingPoint"):
        relinearize_operating_point(request, joint_source[1])
    path = write(tmp_path / "data-policy.json", joint_source[2])
    next_report, task = suggest_design_next(joint_source[0], joint_source[1], data_policy_path=path)
    assert task is None and next_report["status"] == "data_ready_for_fitting"
    assert "未拟合" in render_design_next(next_report)


def test_insufficient_count_fixed_load_and_useful_slices_are_separate(joint_source, tmp_path, capsys):
    run = json.loads(joint_source[1].read_text())
    run["candidates"] = [run["candidates"][i] for i in (0, 1, 2)]
    write(joint_source[1], run)
    policy = copy.deepcopy(joint_source[2])
    policy.update(expected_source_run_sha256=sha(joint_source[1]),
                  training_candidate_indices=[1, 2], holdout_candidate_indices=[3])
    request = RelinearizationDataRequest.model_validate(policy)
    result = inspect_relinearization_data(request, joint_source[1])
    kinds = {row["kind"] for row in result["issues"]}
    assert {"insufficient_training_count", "training_axis_missing", "holdout_axis_missing"} <= kinds
    assert result["fixed_axis_options"][0]["name"] == "load_ff"
    slices = {s["varying_parameter"]: s for s in result["historical_one_axis_slices"]}
    assert slices["device_width_um"]["candidate_indices"] == [1, 2]
    assert slices["bias_mv"]["candidate_indices"] == [1, 3]
    assert all(not s["fit_or_holdout_validated"] for s in slices.values())
    path = write(tmp_path / "incomplete-policy.json", policy)
    output = tmp_path / "data-check.json"
    assert main(["op-relinearize", str(path), str(joint_source[1]), "--check-only", "--output", str(output)]) == 2
    assert json.loads(output.read_text())["status"] == "model_data_insufficient"
    with pytest.raises(ValueError, match="independent point"):
        OperatingPointRelinearizationPolicy.model_validate(policy)


@pytest.mark.parametrize("case", ["collinear", "repeated_holdout", "wrong_fixed_context", "non_eda_metric", "no_holdout"])
def test_diagnostic_explains_existing_data_gates_without_relaxing_them(joint_source, case):
    run = json.loads(joint_source[1].read_text())
    policy = copy.deepcopy(joint_source[2])
    if case == "collinear":
        for candidate in run["candidates"][:4]:
            candidate["testbench_overrides"]["sources"]["VIN_SRC"]["dc_value"] = .4
    elif case == "repeated_holdout":
        repeated = copy.deepcopy(run["candidates"][4])
        repeated["index"] = 6
        run["candidates"].append(repeated)
        policy.update(training_candidate_indices=[1, 2, 3, 4, 5], holdout_candidate_indices=[6])
    elif case == "wrong_fixed_context":
        run["candidates"][4]["instance_parameters"]["MN0"]["l"] = "40n"
    elif case == "non_eda_metric":
        run["candidates"][4]["metric_sources"]["output_dc_v"] = "software_inference"
    else:
        policy["holdout_candidate_indices"] = []
    write(joint_source[1], run)
    policy["expected_source_run_sha256"] = sha(joint_source[1])
    result = inspect_relinearization_data(RelinearizationDataRequest.model_validate(policy), joint_source[1])
    assert not result["strict_fit_inputs_ready"] and result["issues"]
    assert not result["fit_performed"] and not result["execution_authorized"]
    if case == "repeated_holdout":
        assert any(row["kind"] == "holdout_repeats_training" for row in result["issues"])
    elif case in {"wrong_fixed_context", "non_eda_metric"}:
        assert result["ignored_points"][0]["candidate_index"] == 5


def test_diagnostic_retains_byte_binding_and_does_not_change_legacy_policy_serialization(joint_source):
    policy = OperatingPointRelinearizationPolicy.model_validate(joint_source[2])
    request = RelinearizationDataRequest.model_validate(joint_source[2])
    assert policy.model_dump(mode="json") == request.model_dump(mode="json")
    wrong = request.model_copy(update={"expected_source_run_sha256": "0" * 64})
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_relinearization_data(wrong, joint_source[1])
