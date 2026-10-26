"""Local contracts and replay guards; synthetic records are not EDA evidence."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_iteration import build_design_iteration
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.measurements import (
    MeasurementDefinition, OperatingRange, compile_measurement_task,
)
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import CandidateEvaluation, RunRecord, TaskSpec
from virtuoso_design_agent.planner import build_plan
from test_generic_simulation import _task

ROOT = Path(__file__).resolve().parents[1]
STAGES = [{"id": "bias", "analysis": "dc", "constraint_metrics": ["output_dc_v"]},
          {"id": "gain", "analysis": "ac", "constraint_metrics": ["low_frequency_gain_db"]}]


def write(path, raw):
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return path


def measurement(metric="low_frequency_gain_db", **changes):
    return {"metric": metric, "analysis": "ac", "quantity": "gain", "unit": "dB",
            "definition": "Magnitude ratio of V(OUT,0) to V(IN,0), low-frequency reference window",
            "signals": ["V(IN,0)", "V(OUT,0)"], "source": "user_input", **changes}


def operating_range(**changes):
    return {"process_corner": "tt",
            "temperature_c": {"minimum": -40, "nominal": 25, "maximum": 125},
            "supply_v": {"minimum": .81, "nominal": .9, "maximum": .99}, **changes}


@pytest.fixture
def compile_inputs(tmp_path):
    raw = _task().model_dump(mode="json", exclude_none=True)
    raw["generic_simulation"]["operating_condition_supply_source"] = "VDD_SRC"
    template = write(tmp_path / "template.json", raw)
    request = write(tmp_path / "measurements.json", {"measurements": [measurement()]})
    return template, request


def test_compile_binds_actual_signals_and_context_and_strips_execution(compile_inputs):
    report, task = compile_measurement_task(*compile_inputs)
    assert report["status"] == "draft_ready" and report["maximum_candidate_analysis_runs"] == 1
    assert task.measurement_plan and not task.safety.allow_remote_compute
    assert not task.safety.allow_remote_write and not task.safety.replace_existing
    for field, change in [("generic_simulation", lambda x: x["loads"][0].update(value=2e-15)),
                          ("ac_sweep", lambda x: x.update(stop_hz=1e9))]:
        raw = task.model_dump(mode="json", exclude_none=True)
        change(raw[field])
        with pytest.raises(ValidationError, match="context changed"):
            TaskSpec.model_validate(raw)


def test_local_pdk_configuration_drift_invalidates_compiled_measurement(compile_inputs, monkeypatch):
    from virtuoso_design_agent import profiles
    _, task = compile_measurement_task(*compile_inputs)
    changed = profiles.load_pdk_profile(task.pdk_profile).model_copy(update={"model_section": "changed-section"})
    monkeypatch.setattr(profiles, "load_pdk_profile", lambda name: changed)
    with pytest.raises(ValidationError, match="context changed"):
        TaskSpec.model_validate(task.model_dump(mode="json", exclude_none=True))


@pytest.mark.parametrize("mode", ["single", "stage", "pvt-single", "pvt-stage"])
def test_required_measurements_are_enforced_even_without_a_numeric_constraint(compile_inputs, mode):
    from virtuoso_design_agent.adapters.base import AdapterResult
    from virtuoso_design_agent.executor import TaskExecutor
    from virtuoso_design_agent.models import AnalysisStageSpec
    template, request = compile_inputs
    raw = {"measurements": [measurement(), measurement("peak_gain_db", definition="Peak magnitude gain") ]}
    if mode.startswith("pvt"):
        raw["operating_range"] = operating_range()
    write(request, raw)
    report, task = compile_measurement_task(template, request)
    assert task is not None, report

    def evaluate(include_peak):
        metrics = {"low_frequency_gain_db": 20, **({"peak_gain_db": 21} if include_peak else {})}
        data = {"metrics": metrics, "analysis_complete": True}
        if task.operating_conditions:
            data = {"operating_condition_results": [{"condition": c.model_dump(mode="json"),
                "result": {"metrics": metrics, "analysis_complete": True, "parameters": {"vdd_v": c.vdd_v}}}
                for c in task.operating_conditions]}
        result = AdapterResult(data=data, evidence_source="software_inference")
        if mode.endswith("stage"):
            stage = AnalysisStageSpec(id="gain", analysis="ac", constraint_metrics=["low_frequency_gain_db"])
            return TaskExecutor._evaluate_analysis_stage(task, stage, result)
        return TaskExecutor._evaluate_candidate(task, 1, {}, result)

    missing, complete = evaluate(False), evaluate(True)
    assert not missing.analysis_complete and complete.analysis_complete
    assert "required measurements" in " ".join(missing.analysis_issues)


@pytest.mark.parametrize("metric,quantity,unit", [
    ("unity_gain_frequency_hz", "gain_bandwidth_product", "Hz"),
    ("gain_bandwidth_product_hz", "unity_gain_frequency", "Hz"),
    ("low_frequency_gain_db", "gain", "V/V"),
    ("phase_margin_deg", "phase_margin", "deg"),
])
def test_ugb_gbw_units_and_loop_phase_are_not_interchangeable(metric, quantity, unit):
    with pytest.raises(ValidationError):
        MeasurementDefinition.model_validate(measurement(metric, quantity=quantity, unit=unit))


def test_uncertainties_and_wrong_signal_bindings_never_emit_task(compile_inputs):
    template, request = compile_inputs
    write(request, {"measurements": [measurement()], "uncertainties": ["Which output polarity?"]})
    report, task = compile_measurement_task(template, request)
    assert report["status"] == "needs_confirmation" and task is None
    write(request, {"measurements": [measurement(signals=["V(OUT,0)"])]})
    report, task = compile_measurement_task(template, request)
    assert report["status"] == "needs_definition" and task is None
    assert "signal binding" in report["issues"][0]


def test_missing_required_metric_and_duplicate_definitions_are_rejected(compile_inputs):
    template, request = compile_inputs
    write(request, {"measurements": [measurement(required=False)]})
    assert compile_measurement_task(template, request)[1] is None
    write(request, {"measurements": [measurement(), measurement()]})
    assert "repeat" in compile_measurement_task(template, request)[0]["issues"][0]


def test_temperature_supply_default_compiles_nine_finite_points(compile_inputs):
    template, request = compile_inputs
    write(request, {"measurements": [measurement()], "operating_range": operating_range()})
    report, task = compile_measurement_task(template, request)
    assert task is not None, report
    assert report["condition_count"] == report["maximum_candidate_analysis_runs"] == 9
    assert {(c.temperature_c, c.vdd_v) for c in task.operating_conditions} == {
        (t, v) for t in (-40, 25, 125) for v in (.81, .9, .99)}
    assert "no continuous-range guarantee" in report["coverage_statement"]
    raw = json.loads(template.read_text())
    raw["operating_conditions"] = [task.operating_conditions[0].model_dump(mode="json")]
    write(template, raw)
    with pytest.raises(ValueError, match="refuses to replace"):
        compile_measurement_task(template, request)


def test_unknown_corner_is_rejected_before_any_remote_execution(compile_inputs):
    template, request = compile_inputs
    write(request, {"measurements": [measurement()], "operating_range": operating_range(process_corner="guessed")})
    report, task = compile_measurement_task(template, request)
    assert task is None and "unconfigured PDK corners" in report["issues"][0]


def test_single_axis_requires_other_nominal_and_collapsed_axes_are_deduplicated():
    raw = operating_range(supply_v=None)
    with pytest.raises(ValueError, match="explicit nominal"):
        OperatingRange.model_validate(raw).compile()
    raw["nominal_supply_v"] = .9
    assert len(OperatingRange.model_validate(raw).compile()) == 3
    raw["temperature_c"] = {"minimum": 25, "nominal": 25, "maximum": 25}
    assert len(OperatingRange.model_validate(raw).compile()) == 1
    with pytest.raises(ValidationError):
        OperatingRange.model_validate(operating_range(supply_v={"minimum": 1, "nominal": .9, "maximum": 2}))
    with pytest.raises(ValueError, match="conflicting nominal"):
        OperatingRange.model_validate(operating_range(nominal_supply_v=1.8)).compile()


def test_nine_point_winner_gate_and_measurement_cli_are_local_and_nonoverwriting(compile_inputs, tmp_path):
    template, request = compile_inputs
    write(request, {"measurements": [measurement()], "operating_range": operating_range()})
    output, draft = tmp_path / "report.json", tmp_path / "draft.json"
    args = ["measurement-compile", str(template), str(request), "--output", str(output), "--task-output", str(draft)]
    assert main(args) == 0
    saved = draft.read_bytes()
    assert main(args) == 2 and draft.read_bytes() == saved
    from test_generic_simulation import _tune_payload
    raw = _tune_payload()
    raw["generic_simulation"]["operating_condition_supply_source"] = "VDD_SRC"
    raw["winner_verification"] = {"analysis_stages": [{"id": "check", "analysis": "ac",
        "constraint_metrics": ["low_frequency_gain_db"]}], "constraints": raw["constraints"],
        "ac_sweep": raw["ac_sweep"], "operating_conditions": OperatingRange.model_validate(operating_range()).compile()}
    assert len(TaskSpec.model_validate(raw).winner_verification.operating_conditions) == 9


def round_record(tmp_path, number, *, output=.5, gain=20, adapter="virtuoso-bridge-subprocess",
                 complete=True, width="1u", pdk="nics4304_tsmc28", conditions=None,
                 omit_ac=False, run_status="succeeded"):
    raw = _task().model_dump(mode="json", exclude_none=True)
    raw.update(id=f"software-round-{number}", analysis=None, analysis_stages=STAGES, pdk_profile=pdk)
    raw["safety"].update(allow_remote_compute=False, allow_remote_write=False,
                         allowed_library="vda_test", required_cell_prefix="vda_")
    raw["constraints"] = [{"metric": "output_dc_v", "relation": ">=", "value": .1},
                          {"metric": "low_frequency_gain_db", "relation": ">=", "value": 10}]
    raw["objective"] = {"metric": "low_frequency_gain_db", "goal": "maximize"}
    if conditions:
        raw["operating_conditions"] = conditions
        raw["generic_simulation"]["operating_condition_supply_source"] = "VDD_SRC"
    task = TaskSpec.model_validate(raw)
    values = {"output_dc_v": output, **({"low_frequency_gain_db": gain} if not omit_ac else {})}
    stages = []
    for stage in task.analysis_stages:
        if omit_ac and stage.analysis.value == "ac":
            continue
        stage_values = {name: values[name] for name in stage.constraint_metrics}
        constraints = [c for c in task.constraints if c.metric in stage_values]
        evaluations = evaluate_constraints(stage_values, constraints)
        stages.append({"stage_id": stage.id, "analysis": stage.analysis.value,
                       "metrics": stage_values, "constraints": [e.model_dump(mode="json") for e in evaluations],
                       "passed": all(e.passed for e in evaluations), "evidence_source": "eda_result"})
    evaluations = evaluate_constraints(values, task.constraints)
    candidate = CandidateEvaluation(index=1, parameters={}, instance_parameters={"MN0": {"w": width}},
        metrics=values, constraints=evaluations, feasible=complete and all(e.passed for e in evaluations),
        total_violation=sum(e.normalized_violation for e in evaluations), analysis_complete=complete,
        evidence_source="eda_result", analysis_stages=stages)
    started = datetime(2026, 10, 22, tzinfo=UTC) + timedelta(minutes=number)
    run = RunRecord(task_id=task.id, plan_token=build_plan(task).confirmation_token,
        adapter=adapter, status=run_status, started_at=started, finished_at=started + timedelta(seconds=2),
        actions=[], candidates=[candidate], notes=["Synthetic importer fixture only, not real EDA"])
    task_path = write(tmp_path / f"task-{number}.json", raw)
    run_path = write(tmp_path / f"run-{number}.json", run.model_dump(mode="json"))
    return {"task": str(task_path), "run": str(run_path)}


def campaign(tmp_path, rounds, **changes):
    return write(tmp_path / "campaign.json", {"stages": STAGES, "rounds": rounds, "max_rounds": 8, **changes})


def test_parameter_refinement_cannot_sacrifice_a_previously_passed_stage(tmp_path):
    first = round_record(tmp_path, 1, gain=5)
    second = round_record(tmp_path, 2, output=.05, gain=25, width="2u")
    path = campaign(tmp_path, [first, second])
    report, draft = build_design_iteration(path)
    assert report["status"] == "regressed" and report["history"][-1]["regressed_stages"] == ["bias"]
    assert report["history"][-1]["changed_inputs"] == ["parameters"]
    assert draft.operation.value == "schematic.inspect" and not draft.safety.allow_remote_write
    assert report["best_feasible_anchor"] is None and not report["current_remote_state_verified"]
    next_report, next_task = suggest_design_next(Path(second["task"]), Path(second["run"]), iteration_path=path)
    assert next_report["status"] == "regressed" and next_task.operation.value == "schematic.inspect"


def test_best_feasible_anchor_is_retained_without_automatic_restore(tmp_path):
    sources = [round_record(tmp_path, 1, gain=20), round_record(tmp_path, 2, gain=25, width="2u"),
               round_record(tmp_path, 3, output=.05, gain=30, width="3u")]
    report, draft = build_design_iteration(campaign(tmp_path, sources, continue_after_feasible=True))
    assert report["best_feasible_anchor"]["round"] == 2
    assert report["best_feasible_anchor"]["instance_parameters"]["MN0"]["w"] == "2u"
    assert draft.operation.value == "schematic.inspect"
    assert not report["task_draft"]["restoration_executed"]


def test_missing_stage_cannot_be_completed_with_old_metrics(tmp_path):
    sources = [round_record(tmp_path, 1), round_record(tmp_path, 2, omit_ac=True, width="2u")]
    report, draft = build_design_iteration(campaign(tmp_path, sources, continue_after_feasible=True))
    assert report["status"] == "revalidation_required" and draft is None
    assert report["history"][-1]["stages"][1]["status"] == "missing"
    assert report["history"][-1]["previous_metrics_invalidated"]
    assert not report["history"][-1]["full_specification_feasible"]


@pytest.mark.parametrize("change", [{"complete": False}, {"run_status": "failed"}])
def test_interrupted_execution_only_generates_inspection(tmp_path, change):
    source = round_record(tmp_path, 1, **change)
    report, draft = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "reconcile_execution" and draft.operation.value == "schematic.inspect"
    assert report["best_feasible_anchor"] is None


def test_finite_budget_stagnation_and_success_stop_are_distinct(tmp_path):
    first = round_record(tmp_path, 1, gain=5)
    assert build_design_iteration(campaign(tmp_path, [first], max_rounds=1))[0]["status"] == "budget_exhausted"
    second = round_record(tmp_path, 2, gain=5, width="2u")
    report, _ = build_design_iteration(campaign(tmp_path, [first, second], stagnation_rounds=2))
    assert report["status"] == "stagnated" and "improvement" in report["history"][-1]["stop_reason"]
    third = round_record(tmp_path, 3, gain=20)
    report, draft = build_design_iteration(campaign(tmp_path, [first, second, third]))
    assert report["status"] == "specifications_satisfied" and draft is None
    assert report["best_feasible_anchor"]["round"] == 3


def test_pdk_or_testbench_context_change_invalidates_old_anchor(tmp_path):
    sources = [round_record(tmp_path, 1), round_record(tmp_path, 2, gain=5, pdk="nics4304_tsmc18")]
    report, _ = build_design_iteration(campaign(tmp_path, sources, continue_after_feasible=True))
    assert report["best_feasible_anchor"] is None and report["history"][-1]["qualification_context_changed"]
    assert "pdk" in report["history"][-1]["changed_inputs"]


def test_changed_operating_conditions_need_a_new_anchor_not_old_performance(tmp_path):
    first = round_record(tmp_path, 1)
    second = round_record(tmp_path, 2, gain=5, conditions=OperatingRange.model_validate(operating_range()).compile())
    report, _ = build_design_iteration(campaign(tmp_path, [first, second], continue_after_feasible=True))
    assert report["best_feasible_anchor"] is None
    assert "conditions" in report["history"][-1]["changed_inputs"]


def test_pending_write_checkpoint_requires_inspection_even_with_good_metrics(tmp_path):
    from virtuoso_design_agent.models import ExecutionCheckpoint
    source = round_record(tmp_path, 1)
    run = RunRecord.model_validate_json(Path(source["run"]).read_text())
    checkpoint = ExecutionCheckpoint(task_id=run.task_id, plan_token=run.plan_token,
        adapter=run.adapter, started_at=run.started_at, initial_parameters={}, expected_oa_parameters={},
        pending_oa_instance_parameters={"MN0": {"w": "2u"}}, next_candidate_index=1)
    source["checkpoint"] = str(write(tmp_path / "checkpoint.json", checkpoint.model_dump(mode="json")))
    report, draft = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "reconcile_execution" and draft.operation.value == "schematic.inspect"


def test_software_prediction_inside_bridge_record_is_not_measured_closure(tmp_path):
    source = round_record(tmp_path, 1)
    run = json.loads(Path(source["run"]).read_text())
    run["candidates"][0]["metric_sources"] = {"low_frequency_gain_db": "software_inference"}
    write(Path(source["run"]), run)
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "evidence_source_unverified" and report["best_feasible_anchor"] is None
    assert report["history"][-1]["required_metrics_without_eda_source"] == ["low_frequency_gain_db"]


def test_nominal_metrics_cannot_stand_in_for_nine_requested_points(tmp_path):
    conditions = OperatingRange.model_validate(operating_range()).compile()
    source = round_record(tmp_path, 1, conditions=conditions)
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "revalidation_required" and report["best_feasible_anchor"] is None
    assert report["history"][0]["stages"][0]["condition_issues"]


@pytest.mark.parametrize("failed_condition", [False, True])
def test_worst_condition_inference_keeps_real_lineage_and_cannot_hide_a_failure(tmp_path, failed_condition):
    conditions = OperatingRange.model_validate(operating_range()).compile()
    source = round_record(tmp_path, 1, conditions=conditions)
    run = json.loads(Path(source["run"]).read_text())
    candidate = run["candidates"][0]
    candidate["metric_sources"] = {name: "software_inference" for name in candidate["metrics"]}
    for stage in candidate["analysis_stages"]:
        for number, condition in enumerate(conditions):
            metrics = dict(stage["metrics"])
            if failed_condition and number == 8 and stage["analysis"] == "dc":
                metrics["output_dc_v"] = .01
            stage["operating_conditions"].append({**condition, "parameters": {}, "metrics": metrics,
                "constraints": [], "feasible": True, "total_violation": 0, "evidence_source": "eda_result"})
    write(Path(source["run"]), run)
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == ("refine_stage" if failed_condition else "specifications_satisfied")
    assert bool(report["best_feasible_anchor"]) is not failed_condition
    assert report["history"][0]["stages"][0]["status"] == ("fail" if failed_condition else "pass")
    assert "low_frequency_gain_db" in report["history"][0]["derived_condition_metrics_with_eda_lineage"]


def test_missing_objective_is_not_a_complete_feasible_anchor(tmp_path):
    source = round_record(tmp_path, 1)
    raw = json.loads(Path(source["task"]).read_text())
    raw["objective"]["metric"] = "bandwidth_3db_hz"
    task = TaskSpec.model_validate(raw)
    write(Path(source["task"]), raw)
    run = json.loads(Path(source["run"]).read_text())
    run["plan_token"] = build_plan(task).confirmation_token
    write(Path(source["run"]), run)
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "revalidation_required" and report["best_feasible_anchor"] is None
    assert report["history"][0]["missing_required_metrics"] == ["bandwidth_3db_hz"]


@pytest.mark.parametrize("mode,expected", [("missing", "winner_verification_required"),
    ("false_pass", "winner_verification_required"), ("valid", "specifications_satisfied")])
def test_winner_verification_is_recomputed_not_trusted_from_pass_flag(tmp_path, mode, expected):
    source = round_record(tmp_path, 1)
    task_raw = json.loads(Path(source["task"]).read_text())
    task_raw.update(operation="design.tune", instance_parameter_space=[{
        "instance": "MN0", "parameter": "w", "values": ["1u"]}])
    task_raw["winner_verification"] = {"analysis_stages": [{"id": "quality", "analysis": "ac",
        "constraint_metrics": ["low_frequency_gain_db"]}], "constraints": [{
        "metric": "low_frequency_gain_db", "relation": ">=", "value": 15}], "ac_sweep": task_raw["ac_sweep"]}
    task = TaskSpec.model_validate(task_raw)
    write(Path(source["task"]), task_raw)
    run = json.loads(Path(source["run"]).read_text())
    run["plan_token"] = build_plan(task).confirmation_token
    if mode != "missing":
        winner = dict(run["candidates"][0])
        stage = dict(winner["analysis_stages"][1], stage_id="quality")
        if mode == "false_pass":
            stage["metrics"] = {"low_frequency_gain_db": 14}
        winner["analysis_stages"] = [stage]
        run["winner_verification"] = winner
    write(Path(source["run"]), run)
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == expected
    assert bool(report["best_feasible_anchor"]) == (mode == "valid")
    # A nominal refinement draft may not silently drop a declared winner gate.
    if mode == "valid":
        template = dict(task_raw, winner_verification=None)
        path = write(tmp_path / "weakened.json", template)
        report, draft = build_design_iteration(campaign(tmp_path, [source], continue_after_feasible=True), path)
        assert draft is None and "protected winner" in report["draft_issues"][0]


def test_demo_and_duplicate_or_reordered_sources_cannot_advance_a_campaign(tmp_path):
    source = round_record(tmp_path, 1, adapter="demo")
    report, _ = build_design_iteration(campaign(tmp_path, [source]))
    assert report["status"] == "software_only" and report["best_feasible_anchor"] is None
    copied = dict(source, run=str(tmp_path / "copied.json"))
    Path(copied["run"]).write_bytes(Path(source["run"]).read_bytes())
    with pytest.raises(ValueError, match="repeats"):
        build_design_iteration(campaign(tmp_path, [source, copied]))
    second = round_record(tmp_path, 2)
    with pytest.raises(ValueError, match="start order"):
        build_design_iteration(campaign(tmp_path, [second, source]))


def test_next_template_preserves_specs_objective_stage_gates_and_safety(tmp_path):
    source = round_record(tmp_path, 1, gain=5)
    path = campaign(tmp_path, [source])
    template = json.loads(Path(source["task"]).read_text())
    template["target"].pop("view", None)  # equivalent default is allowed
    template["safety"].update(allowed_library=None, required_cell_prefix="", allow_remote_write=True)
    template_path = write(tmp_path / "template.json", template)
    report, draft = build_design_iteration(path, template_path)
    assert report["status"] == "refine_stage" and draft is not None
    assert draft.safety.allowed_library == "vda_test" and draft.safety.required_cell_prefix == "vda_"
    assert not draft.safety.allow_remote_compute and not draft.safety.allow_remote_write
    assert [s.model_dump(mode="json", exclude_defaults=True) for s in draft.analysis_stages]
    template["constraints"][0]["value"] = 0
    write(template_path, template)
    with pytest.raises(ValueError, match="protected specification"):
        build_design_iteration(path, template_path)
    template["constraints"][0]["value"] = .1
    template["objective"]["goal"] = "minimize"
    write(template_path, template)
    with pytest.raises(ValueError, match="objective"):
        build_design_iteration(path, template_path)


def test_task_draft_cannot_expand_topology_or_parameter_edit_authority(tmp_path):
    source = round_record(tmp_path, 1, gain=5)
    template = json.loads(Path(source["task"]).read_text())
    template["design_context"]["instance_parameter_permissions"].append({"instance": "OTHER", "parameters": ["w"]})
    path = write(tmp_path / "expanded.json", template)
    report, draft = build_design_iteration(campaign(tmp_path, [source]), path)
    assert draft is None and "edit envelope" in report["draft_issues"][0]


def test_cli_and_design_next_require_exact_latest_task_run_and_never_overwrite(tmp_path):
    first, second = round_record(tmp_path, 1, gain=5), round_record(tmp_path, 2, gain=5)
    path = campaign(tmp_path, [first, second])
    with pytest.raises(ValueError, match="latest round"):
        suggest_design_next(Path(first["task"]), Path(first["run"]), iteration_path=path)
    output = tmp_path / "iteration.json"
    args = ["design-iterate", str(path), "--output", str(output)]
    assert main(args) == 0
    saved = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == saved


def test_historical_generic_campaign_replays_without_new_simulation():
    path = ROOT / "examples/iteration/pmos-loaded-cs-history.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for source in raw["rounds"]:
        if not (path.parent / source["run"]).exists():
            pytest.skip("optional historical artifacts not included in checkout")
    report, draft = build_design_iteration(path)
    assert report["status"] == "specifications_satisfied"
    assert report["best_feasible_anchor"] is not None and draft is None
    assert not report["execution_authorized"]
