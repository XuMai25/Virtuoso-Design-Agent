"""Grouped-coordinate software fixtures, not new circuits or EDA measurements."""

from __future__ import annotations

import copy
import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.models import RunRecord, TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, build_task_from_relinearization,
    relinearize_operating_point, validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan

from test_joint_testbench_relinearization import joint_source, saved_joint_model
from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha
from test_op_relinearization import _write_atomic_eda_run


@pytest.fixture
def grouped_source(joint_source):
    task_path, run_path, policy, template_path = joint_source
    task = json.loads(task_path.read_text())
    template = json.loads(template_path.read_text())
    run = json.loads(run_path.read_text())
    # Extend only the old synthetic mapping fixture; no OA or physical model is made.
    for payload in (task, template):
        payload["generic_simulation"]["sources"].append({
            "name": "VIN_PEER", "kind": "voltage", "positive_node": "IN_PEER",
            "dc_value": .4, "ac_magnitude": .6, "ac_phase_deg": 195,
        })
        payload["generic_simulation"]["loads"].append({
            "name": "CL_PEER", "kind": "capacitor", "positive_node": "OUT_PEER", "value": 5e-15,
        })
    for candidate, atomic in zip(run["candidates"], task["candidate_set"]["candidates"], strict=True):
        override = candidate["testbench_overrides"]
        override["sources"]["VIN_PEER"] = {
            "dc_value": override["sources"]["VIN_SRC"]["dc_value"],
            "ac_magnitude": .6, "ac_phase_deg": 195,
        }
        override["loads"]["CL_PEER"] = override["loads"]["CL0"]
        atomic["testbench_overrides"] = copy.deepcopy(override)
    run["selected_testbench_overrides"] = copy.deepcopy(run["candidates"][0]["testbench_overrides"])
    run["plan_token"] = build_plan(TaskSpec.model_validate(task)).confirmation_token
    write(task_path, task)
    write(template_path, template)
    write(run_path, run)
    policy["expected_source_run_sha256"] = sha(run_path)
    return joint_source


def group_policy(source):
    policy = copy.deepcopy(source[2])
    policy["parameters"][1]["testbench_parameter"]["linked_names"] = ["VIN_PEER"]
    policy["parameters"][2]["testbench_parameter"]["linked_names"] = ["CL_PEER"]
    return policy


def saved_group_model(source, tmp_path):
    grouped = (*source[:2], group_policy(source), source[3])
    return saved_joint_model(grouped, tmp_path)


def test_explicit_groups_share_axes_keep_member_ac_and_use_original_safe_task(grouped_source, tmp_path):
    with pytest.raises(ValueError, match="unmodeled topology, testbench"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(grouped_source[2]), grouped_source[1])
    model, model_path, policy_path = saved_group_model(grouped_source, tmp_path)
    assert model.gate_passed and len(model.models[0].normalized_sensitivities) == 3
    assert model.holdout_parameter_coverage == {"device_width_um": True, "bias_mv": True, "load_ff": True}
    fixed = model.fixed_testbench_overrides
    assert fixed.loads == {"CFIX": 2e-15}
    assert fixed.sources["VIN_SRC"].dc_value is None and fixed.sources["VIN_PEER"].dc_value is None
    report, draft = suggest_design_next(grouped_source[0], grouped_source[1], model_path=model_path,
                                       policy_path=policy_path, template_path=grouped_source[3])
    assert draft is not None and report["status"] == "local_model_draft_ready"
    assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write,
                    draft.safety.replace_existing, draft.create_if_missing))
    assert "VIN_SRC.dc_value = VIN_PEER.dc_value" in render_design_next(report)
    assert "CL0.value = CL_PEER.value" in render_design_next(report)
    assert any(p["testbench_changes"].get("bias_mv", {}).get("targets") == ["VIN_SRC", "VIN_PEER"]
               for p in report["model"]["proposals"])
    for atomic in draft.candidate_set.candidates:
        effective = atomic.testbench_overrides.apply_to(draft.generic_simulation)
        sources = {s.name: s for s in effective.sources}
        loads = {load.name: load.value for load in effective.loads}
        assert sources["VIN_SRC"].dc_value == sources["VIN_PEER"].dc_value
        assert (sources["VIN_SRC"].ac_magnitude, sources["VIN_SRC"].ac_phase_deg) == (.8, 15)
        assert (sources["VIN_PEER"].ac_magnitude, sources["VIN_PEER"].ac_phase_deg) == (.6, 195)
        assert loads["CL0"] == loads["CL_PEER"] and loads["CFIX"] == 2e-15
    task_path = write(tmp_path / "grouped-next.json", draft.model_dump(mode="json"))
    run_path = _write_atomic_eda_run(tmp_path, draft)
    run = RunRecord.model_validate_json(run_path.read_text())
    for candidate, atomic in zip(run.candidates, draft.candidate_set.candidates, strict=True):
        candidate.testbench_overrides = atomic.testbench_overrides.model_copy(deep=True)
    selected = next(c for c in run.candidates if c.metrics == run.selected_metrics
                    and c.instance_parameters == run.selected_instance_parameters)
    run.selected_testbench_overrides = selected.testbench_overrides.model_copy(deep=True)
    write(run_path, run.model_dump(mode="json"))
    assert validate_relinearization_run(model_path, task_path, run_path, policy_path).gate_passed
    # A matching primary field must never stand in for a changed peer's evidence.
    run.candidates[0].testbench_overrides.loads["CL_PEER"] *= 1.1
    write(run_path, run.model_dump(mode="json"))
    with pytest.raises(ValueError, match="atomic provenance"):
        validate_relinearization_run(model_path, task_path, run_path, policy_path)


@pytest.mark.parametrize("case", ["missing_dc", "unequal_dc", "missing_load", "unequal_load", "peer_ac_drift"])
def test_grouped_source_points_are_complete_equal_and_keep_unmodeled_fields(grouped_source, case):
    run = json.loads(grouped_source[1].read_text())
    override = run["candidates"][4]["testbench_overrides"]
    if case == "missing_dc":
        override["sources"]["VIN_PEER"].pop("dc_value")
    elif case == "unequal_dc":
        override["sources"]["VIN_PEER"]["dc_value"] += .01
    elif case == "missing_load":
        override["loads"].pop("CL_PEER")
    elif case == "unequal_load":
        override["loads"]["CL_PEER"] *= 1.1  # Must not use a 1e-15 absolute tolerance.
    else:
        override["sources"]["VIN_PEER"]["ac_phase_deg"] = 180
    write(grouped_source[1], run)
    policy = group_policy(grouped_source)
    policy["expected_source_run_sha256"] = sha(grouped_source[1])
    with pytest.raises(ValueError, match="explicit testbench|linked testbench|unmodeled topology, testbench"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(policy), grouped_source[1])


@pytest.mark.parametrize("case", ["member_kind", "member_missing", "member_is_condition_supply", "unequal_candidate"])
def test_group_compiler_checks_every_member_not_just_primary(grouped_source, tmp_path, case):
    model, model_path, _ = saved_group_model(grouped_source, tmp_path)
    template = json.loads(grouped_source[3].read_text())
    if case == "member_kind":
        next(s for s in template["generic_simulation"]["sources"] if s["name"] == "VIN_PEER")["kind"] = "current"
    elif case == "member_missing":
        template["generic_simulation"]["loads"] = [load for load in template["generic_simulation"]["loads"]
                                                    if load["name"] != "CL_PEER"]
    elif case == "member_is_condition_supply":
        template["generic_simulation"]["operating_condition_supply_source"] = "VIN_PEER"
        template["operating_conditions"] = [{"name": "tt", "process_corner": "top_tt", "temperature_c": 27, "vdd_v": .9}]
    else:
        model.candidate_set.candidates[0].testbench_overrides.sources["VIN_PEER"].dc_value += .01
        write(model_path, model.model_dump(mode="json"))
    write(grouped_source[3], template)
    with pytest.raises(ValueError, match="incompatible kind|unknown loads|operating-condition supply|linked testbench"):
        build_task_from_relinearization(model_path, grouped_source[3])


@pytest.mark.parametrize("case", ["primary_repeated", "member_repeated", "shared_axis", "invalid_name"])
def test_group_declarations_cannot_double_control_or_guess_targets(grouped_source, case):
    policy = group_policy(grouped_source)
    binding = policy["parameters"][1]["testbench_parameter"]
    if case == "primary_repeated":
        binding["linked_names"].append("VIN_SRC")
    elif case == "member_repeated":
        binding["linked_names"].append("VIN_PEER")
    elif case == "shared_axis":
        other = copy.deepcopy(policy["parameters"][1])
        other.update(name="other_bias_mv")
        other["testbench_parameter"].update(name="VIN_PEER", linked_names=[])
        policy["parameters"].append(other)
        policy["training_candidate_indices"].append(6)  # Isolate the alias schema gate, not the count gate.
    else:
        binding["linked_names"] = ["VIN_PEER;other"]
    with pytest.raises(ValueError, match="unique|must not alias|pattern"):
        OperatingPointRelinearizationPolicy.model_validate(policy)


def test_single_testbench_binding_keeps_old_canonical_serialization(joint_source):
    policy = OperatingPointRelinearizationPolicy.model_validate(joint_source[2])
    for original, serialized in zip(joint_source[2]["parameters"], policy.model_dump(mode="json")["parameters"], strict=True):
        if "testbench_parameter" in original:
            assert serialized["testbench_parameter"] == original["testbench_parameter"]
            assert "linked_names" not in serialized["testbench_parameter"]
