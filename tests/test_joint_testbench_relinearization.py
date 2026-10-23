"""Synthetic joint-coordinate regressions; no real circuit or EDA execution."""

from __future__ import annotations

import copy
import json

import pytest

from virtuoso_design_agent.design_next import render_design_next, suggest_design_next
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import MetricConstraint, RunRecord, TaskSpec
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy, build_task_from_relinearization,
    relinearize_operating_point, validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan

from test_measurement_iteration import write
from test_numeric_cdf_relinearization import raw_source, sha
from test_op_relinearization import _write_atomic_eda_run


@pytest.fixture
def joint_source(raw_source):
    task_path, run_path, policy, template_path = raw_source
    task = json.loads(task_path.read_text())
    template = json.loads(template_path.read_text())
    run = json.loads(run_path.read_text())
    task.pop("instance_parameter_space")
    task["limits"]["max_iterations"] = template["limits"]["max_iterations"] = 6
    for payload in (task, template):
        payload["generic_simulation"]["loads"].append({
            "name": "CFIX", "kind": "capacitor", "positive_node": "OUT", "value": 2e-15,
        })
    candidates, atomic = [], []
    constraints = [MetricConstraint.model_validate(raw) for raw in task["constraints"]]
    for index, (width, bias, load) in enumerate([(0, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 1, 1)], 1):
        candidate = copy.deepcopy(run["candidates"][0])
        metrics = {"output_dc_v": .5 + .02 * width + .03 * bias - .01 * load,
                   "low_frequency_gain_db": 5 + .5 * width - .2 * bias - .1 * load}
        overrides = {"sources": {"VIN_SRC": {"dc_value": .4 + .02 * bias, "ac_magnitude": .8, "ac_phase_deg": 15},
                                  "VDD_SRC": {"dc_value": .9}},
                     "loads": {"CL0": (5 + load) * 1e-15, "CFIX": 2e-15}}
        candidate.update(index=index, instance_parameters={"MN0": {"w": f"{1 + .1 * width:g}u", "l": "30n"}},
                         testbench_overrides=overrides, metrics=metrics,
                         metric_sources={metric: "eda_result" for metric in metrics},
                         objective_value=metrics["low_frequency_gain_db"],
                         constraints=[c.model_dump(mode="json") for c in evaluate_constraints(metrics, constraints)],
                         atomic_candidate_id=f"synthetic-joint-{index}", atomic_candidate_predicted_metrics={},
                         atomic_candidate_evidence_source="user_input")
        candidates.append(candidate)
        atomic.append({"id": candidate["atomic_candidate_id"], "testbench_overrides": overrides,
                       "instance_parameter_updates": [{"instance": "MN0", "parameters": {"w": candidate["instance_parameters"]["MN0"]["w"]}}]})
    task["candidate_set"] = {"source": {"id": "synthetic-joint-source", "generator": "fixture"}, "candidates": atomic}
    run.update(candidates=candidates, plan_token=build_plan(TaskSpec.model_validate(task)).confirmation_token,
               selected_instance_parameters=copy.deepcopy(candidates[0]["instance_parameters"]),
               selected_testbench_overrides=copy.deepcopy(candidates[0]["testbench_overrides"]),
               selected_testbench_override_evidence_source="user_input", selected_metrics=candidates[0]["metrics"],
               notes=["Synthetic three-coordinate regression, not actual EDA or physical calibration."])
    write(task_path, task)
    write(template_path, template)
    write(run_path, run)
    policy.update(expected_source_run_sha256=sha(run_path), anchor_candidate_index=1,
                  training_candidate_indices=[1, 2, 3, 4], holdout_candidate_indices=[5], maximum_candidates=6)
    policy["parameters"].extend([
        {"name": "bias_mv", "minimum": 350, "maximum": 450, "proposal_step": 10,
         "testbench_parameter": {"target": "source_dc", "name": "VIN_SRC", "unit": "mV", "meaning": "Declared synthetic bias."}},
        {"name": "load_ff", "minimum": 3, "maximum": 7, "proposal_step": .5,
         "testbench_parameter": {"target": "load", "name": "CL0", "unit": "fF", "meaning": "Declared synthetic capacitance."}},
    ])
    return task_path, run_path, policy, template_path


def saved_joint_model(source, tmp_path):
    policy = OperatingPointRelinearizationPolicy.model_validate(source[2])
    model = relinearize_operating_point(policy, source[1])
    model_path = write(tmp_path / "joint-model.json", model.model_dump(mode="json"))
    policy_path = write(tmp_path / "joint-policy.json", policy.model_dump(mode="json"))
    return model, model_path, policy_path


def test_joint_model_compiles_typed_values_and_audits_the_exact_tuple(joint_source, tmp_path):
    task_path, run_path, _, template_path = joint_source
    model, model_path, policy_path = saved_joint_model(joint_source, tmp_path)
    assert model.gate_passed and set(model.testbench_parameter_bindings) == {"bias_mv", "load_ff"}
    fixed = model.fixed_testbench_overrides
    assert "CL0" not in fixed.loads and fixed.sources["VIN_SRC"].dc_value is None
    assert fixed.loads["CFIX"] == 2e-15 and fixed.sources["VIN_SRC"].ac_magnitude == .8
    report, task = suggest_design_next(task_path, run_path, model_path=model_path,
                                       policy_path=policy_path, template_path=template_path)
    assert task is not None and report["status"] == "local_model_draft_ready"
    assert "VIN_SRC" in render_design_next(report) and "源/负载数值映射" in render_design_next(report)
    assert not any((task.safety.allow_remote_write, task.safety.allow_remote_compute,
                    task.safety.replace_existing, task.create_if_missing))
    assert task.generic_simulation == TaskSpec.model_validate_json(task_path.read_text()).generic_simulation
    for atomic in task.candidate_set.candidates:
        assert not atomic.parameters  # Model aliases never become semantic executor parameters.
        effective = atomic.testbench_overrides.apply_to(task.generic_simulation)
        source = next(s for s in effective.sources if s.name == "VIN_SRC")
        assert any(source.dc_value * 1000 == pytest.approx(value) for value in [390, 400, 410])
        assert any(atomic.testbench_overrides.loads["CL0"] * 1e15 == pytest.approx(value) for value in [4.5, 5, 5.5])
        assert (source.ac_magnitude, source.ac_phase_deg) == (.8, 15)
        assert atomic.testbench_overrides.loads["CFIX"] == 2e-15
    assert any(p["testbench_changes"] for p in report["model"]["proposals"])
    path = write(tmp_path / "joint-next.json", task.model_dump(mode="json"))
    measured_path = _write_atomic_eda_run(tmp_path, task)
    measured = RunRecord.model_validate_json(measured_path.read_text())
    for candidate, atomic in zip(measured.candidates, task.candidate_set.candidates, strict=True):
        candidate.testbench_overrides = atomic.testbench_overrides
    winner = next(c for c in measured.candidates if c.metrics == measured.selected_metrics
                  and c.instance_parameters == measured.selected_instance_parameters)
    measured.selected_testbench_overrides = winner.testbench_overrides
    write(measured_path, measured.model_dump(mode="json"))
    assert validate_relinearization_run(model_path, path, measured_path, policy_path).gate_passed
    measured.candidates[0].testbench_overrides.sources["VIN_SRC"].dc_value += .01
    write(measured_path, measured.model_dump(mode="json"))
    with pytest.raises(ValueError, match="atomic provenance"):
        validate_relinearization_run(model_path, path, measured_path, policy_path)
    model.candidate_set.candidates = model.candidate_set.candidates[:2]  # Keep tuples distinct after deleting a coordinate.
    for atomic in model.candidate_set.candidates:
        atomic.testbench_overrides.loads.pop("CL0")
    missing = write(tmp_path / "joint-missing-coordinate.json", model.model_dump(mode="json"))
    with pytest.raises(ValueError, match="explicit testbench"):
        build_task_from_relinearization(missing, template_path)


@pytest.mark.parametrize("field", ["ac_phase", "fixed_load"])
def test_joint_mapping_does_not_relax_other_conditions(joint_source, field):
    run_path = joint_source[1]
    run = json.loads(run_path.read_text())
    override = run["candidates"][4]["testbench_overrides"]
    if field == "ac_phase":
        override["sources"]["VIN_SRC"]["ac_phase_deg"] = 30
    else:
        override["loads"]["CFIX"] = 3e-15
    write(run_path, run)
    joint_source[2]["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="unmodeled topology, testbench"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(joint_source[2]), run_path)


@pytest.mark.parametrize("target", ["source_dc", "load"])
def test_missing_explicit_coordinate_never_falls_back_to_base_setup(joint_source, target):
    run_path = joint_source[1]
    run = json.loads(run_path.read_text())
    override = run["candidates"][4]["testbench_overrides"]
    if target == "source_dc":
        override["sources"]["VIN_SRC"].pop("dc_value")
    else:
        override["loads"].pop("CL0")
    write(run_path, run)
    joint_source[2]["expected_source_run_sha256"] = sha(run_path)
    with pytest.raises(ValueError, match="explicit testbench"):
        relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(joint_source[2]), run_path)


@pytest.mark.parametrize("case", ["voltage_kind", "capacitor_kind", "condition_supply"])
def test_compiler_refuses_wrong_physical_kind_or_condition_override(joint_source, tmp_path, case):
    _, model_path, _ = saved_joint_model(joint_source, tmp_path)
    template = json.loads(joint_source[3].read_text())
    if case == "voltage_kind":
        next(s for s in template["generic_simulation"]["sources"] if s["name"] == "VIN_SRC")["kind"] = "current"
    elif case == "capacitor_kind":
        template["generic_simulation"]["loads"][0]["kind"] = "resistor"
    else:
        template["generic_simulation"]["operating_condition_supply_source"] = "VIN_SRC"
        template["operating_conditions"] = [{"name": "tt", "process_corner": "top_tt", "temperature_c": 27, "vdd_v": .9}]
    write(joint_source[3], template)
    with pytest.raises(ValueError, match="testbench.*kind|operating-condition supply"):
        build_task_from_relinearization(model_path, joint_source[3])


@pytest.mark.parametrize("case", ["too_few", "collinear", "bad_holdout"])
def test_joint_data_gates_are_not_relaxed(joint_source, tmp_path, case):
    run_path, raw_policy = joint_source[1:3]
    if case == "too_few":
        raw_policy["training_candidate_indices"] = [1, 2, 3]
        with pytest.raises(ValueError, match="independent point"):
            OperatingPointRelinearizationPolicy.model_validate(raw_policy)
        return
    run = json.loads(run_path.read_text())
    if case == "collinear":
        for candidate in run["candidates"][:4]:
            width = candidate["instance_parameters"]["MN0"]["w"]
            step = -1 if width == "0.9u" else 0
            candidate["testbench_overrides"]["sources"]["VIN_SRC"]["dc_value"] = .4 + .02 * step
            candidate["testbench_overrides"]["loads"]["CL0"] = (5 + step) * 1e-15
    else:
        run["candidates"][4]["metrics"]["low_frequency_gain_db"] += 1
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    if case == "collinear":
        with pytest.raises(ValueError, match="independently span"):
            relinearize_operating_point(OperatingPointRelinearizationPolicy.model_validate(raw_policy), run_path)
    else:
        model, model_path, _ = saved_joint_model(joint_source, tmp_path)
        assert not model.gate_passed and model.candidate_set is None
        with pytest.raises(ValueError, match="only a passed"):
            build_task_from_relinearization(model_path, joint_source[3])


def test_zero_and_negative_dc_are_allowed_without_relaxing_cdf_or_load_bounds(joint_source, tmp_path):
    run_path, raw_policy = joint_source[1:3]
    run = json.loads(run_path.read_text())
    for candidate in run["candidates"]:
        candidate["testbench_overrides"]["sources"]["VIN_SRC"]["dc_value"] -= .4
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    raw_policy["parameters"][1].update(minimum=-50, maximum=50)
    model, model_path, _ = saved_joint_model(joint_source, tmp_path)
    task = build_task_from_relinearization(model_path, joint_source[3])
    assert model.gate_passed
    assert any(c.testbench_overrides.sources["VIN_SRC"].dc_value < 0 for c in task.candidate_set.candidates)
    invalid = copy.deepcopy(raw_policy)
    invalid["parameters"][0]["minimum"] = 0
    with pytest.raises(ValueError, match="positive"):
        OperatingPointRelinearizationPolicy.model_validate(invalid)
    invalid = copy.deepcopy(raw_policy)
    invalid["parameters"][2]["minimum"] = 0
    with pytest.raises(ValueError, match="positive"):
        OperatingPointRelinearizationPolicy.model_validate(invalid)


def test_binding_alias_and_unit_declarations_are_unambiguous(joint_source):
    raw = copy.deepcopy(joint_source[2])
    raw["parameters"][2]["testbench_parameter"] = copy.deepcopy(raw["parameters"][1]["testbench_parameter"])
    with pytest.raises(ValueError, match="must not alias"):
        OperatingPointRelinearizationPolicy.model_validate(raw)
    raw = copy.deepcopy(joint_source[2])
    raw["parameters"][1]["instance_parameter"] = copy.deepcopy(raw["parameters"][0]["instance_parameter"])
    with pytest.raises(ValueError, match="both"):
        OperatingPointRelinearizationPolicy.model_validate(raw)
    raw = copy.deepcopy(joint_source[2])
    raw["parameters"][2]["testbench_parameter"]["unit"] = "V"
    with pytest.raises(ValueError, match="unit"):
        OperatingPointRelinearizationPolicy.model_validate(raw)


def test_current_bias_and_resistive_load_use_declared_model_units(joint_source, tmp_path):
    run_path, raw_policy = joint_source[1:3]
    run = json.loads(run_path.read_text())
    for candidate in run["candidates"]:
        override = candidate["testbench_overrides"]
        override["sources"]["VIN_SRC"]["dc_value"] *= 1e-4  # 0.4 V fixture -> 40 uA coordinate.
        override["loads"]["CL0"] *= 1e18  # 5 fF fixture -> 5 kohm coordinate.
    write(run_path, run)
    raw_policy["expected_source_run_sha256"] = sha(run_path)
    raw_policy["parameters"][1].update(minimum=35, maximum=45, proposal_step=1)
    raw_policy["parameters"][1]["testbench_parameter"].update(unit="uA", meaning="Synthetic current coordinate.")
    raw_policy["parameters"][2]["testbench_parameter"].update(unit="kohm", meaning="Synthetic resistance coordinate.")
    template = json.loads(joint_source[3].read_text())
    next(s for s in template["generic_simulation"]["sources"] if s["name"] == "VIN_SRC")["kind"] = "current"
    template["generic_simulation"]["loads"][0]["kind"] = "resistor"
    write(joint_source[3], template)
    _, model_path, _ = saved_joint_model(joint_source, tmp_path)
    task = build_task_from_relinearization(model_path, joint_source[3])
    anchor = task.candidate_set.candidates[0].testbench_overrides.apply_to(task.generic_simulation)
    assert next(s.dc_value for s in anchor.sources if s.name == "VIN_SRC") == pytest.approx(40e-6)
    assert next(load.value for load in anchor.loads if load.name == "CL0") == pytest.approx(5000)
    assert any(c.testbench_overrides.sources["VIN_SRC"].dc_value == pytest.approx(39e-6)
               for c in task.candidate_set.candidates)
