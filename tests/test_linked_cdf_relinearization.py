"""Explicit linked-field local fixtures; no circuits, Bridge or EDA are run."""

from __future__ import annotations

import copy
import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, build_task_from_relinearization,
    relinearize_operating_point, validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.spectre_values import spectre_scalar

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha
from test_relinearization_testbench_context import matching_audit_run


@pytest.fixture
def linked_source(raw_source):
    task_path, run_path, policy, template_path = raw_source
    task = json.loads(task_path.read_text())
    run = json.loads(run_path.read_text())
    template = json.loads(template_path.read_text())
    for payload in (task, template):
        payload["instance_parameter_updates"].append({"instance": "MN1", "parameters": {"l": "30n"}})
        payload["design_context"]["instance_parameter_permissions"].append({"instance": "MN1", "parameters": ["w", "l"]})
        payload["generic_simulation"]["netlist_parameter_bindings"].extend([
            {"instance": "MN1", "oa_parameter": field, "netlist_parameter": field} for field in ("w", "l")
        ])
    task.pop("instance_parameter_space")
    atomic = []
    for candidate in run["candidates"]:
        literal = candidate["instance_parameters"]["MN0"]["w"]
        # Equivalent SI spelling must not lose each member's exact anchor text.
        candidate["instance_parameters"]["MN1"] = {"w": f"{spectre_scalar(literal):.12g}", "l": "30n"}
        candidate_id = f"synthetic-linked-{candidate['index']}"
        candidate.update(atomic_candidate_id=candidate_id, atomic_candidate_predicted_metrics={},
                         atomic_candidate_evidence_source="user_input")
        atomic.append({"id": candidate_id, "instance_parameter_updates": [
            {"instance": instance, "parameters": {"w": fields["w"]}}
            for instance, fields in candidate["instance_parameters"].items()
        ]})
    task["candidate_set"] = {"source": {"id": "synthetic-linked-width", "generator": "fixture"}, "candidates": atomic}
    run["plan_token"] = build_plan(TaskSpec.model_validate(task)).confirmation_token
    run["selected_instance_parameters"] = copy.deepcopy(run["candidates"][1]["instance_parameters"])
    run["notes"] = ["Synthetic linked-field regression, not a real matched-device experiment."]
    write(task_path, task)
    write(run_path, run)
    write(template_path, template)
    policy["expected_source_run_sha256"] = sha(run_path)
    return task_path, run_path, policy, template_path


def linked_policy(raw):
    raw = copy.deepcopy(raw)
    raw["parameters"][0]["instance_parameter"]["linked_parameters"] = [{"instance": "MN1", "parameter": "w"}]
    return OperatingPointRelinearizationPolicy.model_validate(raw)


def saved_linked_model(linked_source, tmp_path):
    policy = linked_policy(linked_source[2])
    model = relinearize_operating_point(policy, linked_source[1])
    model_path = write(tmp_path / "linked-model.json", model.model_dump(mode="json"))
    policy_path = write(tmp_path / "linked-policy.json", policy.model_dump(mode="json"))
    return model, model_path, policy_path


def test_linked_width_reuses_one_dimension_and_original_task_audit(linked_source, tmp_path):
    task_path, run_path, raw_policy, template_path = linked_source
    with pytest.raises(ValueError, match="raw instance parameters vary"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    independent = copy.deepcopy(raw_policy)
    second = copy.deepcopy(independent["parameters"][0])
    second.update(name="other_width_um", instance_parameter={**second["instance_parameter"], "instance": "MN1"})
    independent["parameters"].append(second)
    with pytest.raises(ValueError, match="independently span"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(independent), run_path)
    model, model_path, policy_path = saved_linked_model(linked_source, tmp_path)
    assert model.gate_passed
    assert model.fixed_instance_parameters == {"MN0": {"l": "30n"}, "MN1": {"l": "30n"}}
    anchor = model.candidate_set.candidates[0].instance_parameters()
    assert anchor == {"MN0": {"w": "1.1u"}, "MN1": {"w": "1.1e-06"}}
    for candidate in model.candidate_set.candidates:
        values = candidate.instance_parameters()
        assert spectre_scalar(values["MN0"]["w"]) == spectre_scalar(values["MN1"]["w"])
        assert not candidate.parameters  # One modeled degree of freedom, no new executor knob.
    report, task = suggest_design_next(task_path, run_path, model_path=model_path,
                                       policy_path=policy_path, template_path=template_path)
    assert task is not None and report["status"] == "local_model_draft_ready"
    assert "MN0.w = MN1.w" in render_design_next(report)
    assert not any((task.safety.allow_remote_write, task.safety.allow_remote_compute,
                    task.safety.replace_existing, task.create_if_missing))
    assert report["model"]["proposals"][1]["instance_changes"]["MN1"]["w"]["before"] == "1.1e-06"
    path = write(tmp_path / "linked-next.json", task.model_dump(mode="json"))
    run_path, run = matching_audit_run(tmp_path, task)
    assert validate_relinearization_run(model_path, path, run_path, policy_path).gate_passed
    run.candidates[0].instance_parameters["MN1"]["w"] = "1.3u"
    write(run_path, run.model_dump(mode="json"))
    with pytest.raises(ValueError, match="atomic provenance"):
        validate_relinearization_run(model_path, path, run_path, policy_path)


@pytest.mark.parametrize("value", [None, "1.2u"])
def test_linked_group_must_be_complete_and_equal_in_every_source_point(linked_source, value):
    _, run_path, raw_policy, _ = linked_source
    run = json.loads(run_path.read_text())
    if value is None:
        run["candidates"][3]["instance_parameters"]["MN1"].pop("w")
    else:
        run["candidates"][0]["instance_parameters"]["MN1"]["w"] = value
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="positive literal|linked CDF"):
        relinearize_operating_point(linked_policy(raw_policy), run_path)


def test_linked_field_cannot_belong_to_two_model_variables(linked_source):
    policy = linked_policy(linked_source[2]).model_dump(mode="json")
    other = copy.deepcopy(policy["parameters"][0])
    other.update(name="aliased_width", instance_parameter={
        **other["instance_parameter"], "instance": "MN1", "linked_parameters": [],
    })
    policy["parameters"].append(other)
    with pytest.raises(ValueError, match="must not alias"):
        OperatingPointRelinearizationPolicy.model_validate(policy)


def test_linked_group_cannot_hide_unmodeled_length_drift(linked_source):
    _, run_path, raw_policy, _ = linked_source
    run = json.loads(run_path.read_text())
    run["candidates"][3]["instance_parameters"]["MN1"]["l"] = "40n"
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="raw instance parameters vary"):
        relinearize_operating_point(linked_policy(raw_policy), run_path)


def test_linked_compiler_cannot_drop_one_member_or_bypass_its_permission(linked_source, tmp_path):
    model, model_path, _ = saved_linked_model(linked_source, tmp_path)
    raw = json.loads(linked_source[3].read_text())
    raw["design_context"]["instance_parameter_permissions"][1]["modes"] = ["fixed"]
    write(linked_source[3], raw)
    with pytest.raises(ValueError, match="permission|search"):
        build_plan(build_task_from_relinearization(model_path, linked_source[3]))
    for candidate in model.candidate_set.candidates:
        candidate.instance_parameter_updates = [u for u in candidate.instance_parameter_updates if u.instance != "MN1"]
    write(model_path, model.model_dump(mode="json"))
    with pytest.raises(ValueError, match="positive literal|linked CDF"):
        build_task_from_relinearization(model_path, linked_source[3])


def test_single_binding_does_not_change_old_policy_bytes(raw_source):
    raw_policy = raw_source[2]
    policy = OperatingPointRelinearizationPolicy.model_validate(raw_policy)
    serialized = policy.model_dump(mode="json")["parameters"][0]["instance_parameter"]
    assert serialized == raw_policy["parameters"][0]["instance_parameter"]
    assert "linked_parameters" not in serialized
