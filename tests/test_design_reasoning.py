"""Offline hypothesis/provenance regressions; importer fixtures are not EDA."""

import json
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_intake import build_design_intake
from virtuoso_design_agent.design_iteration import build_design_iteration
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.design_reasoning import build_design_reasoning, render_design_reasoning
from virtuoso_design_agent.design_review import file_reference, load_bound_run

from test_measurement_iteration import campaign, round_record, write


def reasoning_request(tmp_path, baseline):
    return write(tmp_path / "reasoning.json", {
        "task": baseline["task"], "task_sha256": file_reference(Path(baseline["task"]))["sha256"],
        "run": baseline["run"], "run_sha256": file_reference(Path(baseline["run"]))["sha256"],
        "candidate_index": 1,
        "hypotheses": [{
            "id": "width-gain", "observation": "The recorded gain is below the intended target.",
            "mechanism": "A width change may affect gm and capacitance; check both rather than assume a gain improvement.",
            "evidence": [{"kind": "metric", "selector": "low_frequency_gain_db"},
                         {"kind": "task", "selector": "/design_context/roles/0"}],
            "controls": [{"kind": "cdf", "name": "MN0", "parameter": "w"}],
            "expectations": [{"metric": "low_frequency_gain_db", "direction": "increase", "absolute_tolerance": .1}],
            "tradeoffs": ["Device capacitance can increase."], "falsification": "No gain improvement at the same conditions.",
        }],
    })


def test_reasoning_is_evidence_bound_and_checks_expectations_not_causality(tmp_path):
    first = round_record(tmp_path, 1, gain=5)
    second = round_record(tmp_path, 2, gain=15, width="2u")
    request = reasoning_request(tmp_path, first)
    task, run = load_bound_run(Path(second["task"]), Path(second["run"]))
    report = build_design_reasoning(request, task, run=run)
    h = report["hypotheses"][0]
    assert h["comparisons"][0]["status"] == "consistent"
    assert h["comparisons"][0]["delta"] == 10
    assert [e["source"] for e in h["resolved_evidence"]] == ["eda_result", "user_input"]
    assert h["source"] == "software_inference" and not report["execution_authorized"]
    assert "因果" in render_design_reasoning(report)
    assert "retrospective" in " ".join(report["limitations"])
    # Source bytes cannot be replaced by a reinterpreted or newly rounded run.
    Path(first["run"]).write_text(Path(first["run"]).read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        build_design_reasoning(request, task, run=run)


@pytest.mark.parametrize("mode", ["changed_load", "changed_condition", "demo", "missing_metric", "incomplete", "same_point"])
def test_confounders_missing_evidence_and_demo_cannot_confirm_a_hypothesis(tmp_path, mode):
    first = round_record(tmp_path, 1, gain=5)
    second = round_record(tmp_path, 2, gain=15, width="2u")
    request = reasoning_request(tmp_path, first)
    task, run = load_bound_run(Path(second["task"]), Path(second["run"]))
    if mode == "changed_load":
        task.generic_simulation.loads[0].value *= 2
    elif mode == "changed_condition":
        task.generic_simulation.temperature_c = 100
    elif mode == "demo":
        run.adapter = "deterministic-demo"
    elif mode == "missing_metric":
        run.candidates[0].metrics.pop("low_frequency_gain_db")
    elif mode == "incomplete":
        run.candidates[0].analysis_complete = False
    else:
        task, run = load_bound_run(Path(first["task"]), Path(first["run"]))
    report = build_design_reasoning(request, task, run=run)
    assert report["hypotheses"][0]["comparisons"][0]["status"] == "not_evaluated"


def test_contradiction_is_preserved_and_unmapped_control_is_rejected(tmp_path):
    first = round_record(tmp_path, 1, gain=15)
    second = round_record(tmp_path, 2, gain=5, width="2u")
    request = reasoning_request(tmp_path, first)
    task, run = load_bound_run(Path(second["task"]), Path(second["run"]))
    assert build_design_reasoning(request, task, run=run)["hypotheses"][0]["comparisons"][0]["status"] == "contradicted"
    raw = json.loads(request.read_text())
    raw["hypotheses"][0]["controls"][0]["parameter"] = "unknown"
    write(request, raw)
    with pytest.raises(ValueError, match="not declared"):
        build_design_reasoning(request, task, run=run)


def test_independent_unchanged_point_can_check_repeatability_but_is_not_a_new_prediction(tmp_path):
    first = round_record(tmp_path, 1, gain=15)
    second = round_record(tmp_path, 2, gain=15)
    request = reasoning_request(tmp_path, first)
    raw = json.loads(request.read_text())
    raw["hypotheses"][0]["expectations"][0]["direction"] = "unchanged"
    write(request, raw)
    task, run = load_bound_run(Path(second["task"]), Path(second["run"]))
    report = build_design_reasoning(request, task, run=run)
    assert report["hypotheses"][0]["comparisons"][0]["status"] == "consistent"
    assert report["hypotheses"][0]["changed_controls"] == []
    assert "retrospective" in " ".join(report["limitations"])


def test_action_reference_retains_bridge_readback_and_recorded_status(tmp_path):
    first = round_record(tmp_path, 1)
    run_path = Path(first["run"])
    raw_run = json.loads(run_path.read_text())
    raw_run["actions"] = [{"action": "schematic.inspect.before", "status": "succeeded",
                           "started_at": raw_run["started_at"], "finished_at": raw_run["started_at"],
                           "evidence_source": "bridge_readback", "details": {"terminals": {"MN0": {"G": "IN"}}}}]
    write(run_path, raw_run)
    request = reasoning_request(tmp_path, first)
    raw = json.loads(request.read_text())
    raw["hypotheses"][0]["evidence"] = [{"kind": "action", "action": "schematic.inspect.before", "selector": "/terminals/MN0/G"}]
    write(request, raw)
    task, run = load_bound_run(Path(first["task"]), run_path)
    resolved = build_design_reasoning(request, task, run=run)["hypotheses"][0]["resolved_evidence"][0]
    assert (resolved["source"], resolved["value"], resolved["recorded_status"]) == ("bridge_readback", "IN", "succeeded")


@pytest.mark.parametrize("kind,selector", [("task", "/design_context/nonexistent"), ("metric", "invented_metric")])
def test_evidence_selectors_do_not_guess_missing_values(tmp_path, kind, selector):
    first = round_record(tmp_path, 1)
    request = reasoning_request(tmp_path, first)
    raw = json.loads(request.read_text())
    raw["hypotheses"][0]["evidence"] = [{"kind": kind, "selector": selector}]
    write(request, raw)
    task, run = load_bound_run(Path(first["task"]), Path(first["run"]))
    with pytest.raises(ValueError):
        build_design_reasoning(request, task, run=run)


def test_intake_next_and_iteration_share_the_same_reasoning_without_execution(tmp_path, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("reasoning attempted remote execution")
    monkeypatch.setattr("virtuoso_design_agent.cli.SubprocessBridgeAdapter.__init__", forbidden)
    first = round_record(tmp_path, 1, gain=5)
    second = round_record(tmp_path, 2, gain=15, width="2u")
    request = reasoning_request(tmp_path, first)
    intake = write(tmp_path / "intake.json", {"source": {"kind": "task", "task": second["task"]},
                                            "reasoning": request.name})
    intake_report, _ = build_design_intake(intake)
    assert intake_report["reasoning"]["hypotheses"][0]["comparisons"][0]["status"] == "not_evaluated"
    next_report, draft = suggest_design_next(Path(second["task"]), Path(second["run"]), reasoning_path=request)
    assert draft is None and next_report["reasoning"]["hypotheses"][0]["comparisons"][0]["status"] == "consistent"
    second["reasoning"] = str(request)
    iteration, _ = build_design_iteration(campaign(tmp_path, [first, second]))
    assert iteration["history"][-1]["reasoning"] == next_report["reasoning"]
    assert main(["design-next", second["task"], second["run"], "--reasoning", str(request),
                 "--output", str(tmp_path / "next.json")]) == 0
    capsys.readouterr()


def test_uncertainty_does_not_authorize_a_task_and_failed_execution_still_reconciles(tmp_path):
    first = round_record(tmp_path, 1, gain=5)
    request = reasoning_request(tmp_path, first)
    raw = json.loads(request.read_text())
    raw["uncertainties"] = ["Unconfirmed device mapping"]
    write(request, raw)
    report, draft = suggest_design_next(Path(first["task"]), Path(first["run"]), reasoning_path=request)
    assert report["status"] == "reasoning_requires_confirmation" and draft is None
    second = round_record(tmp_path, 2, complete=False)
    report, draft = suggest_design_next(Path(second["task"]), Path(second["run"]), reasoning_path=request)
    assert report["status"] == "reconcile_execution_first" and draft is None


def test_committed_examples_remain_local_and_synthetic_cannot_be_promoted():
    root = Path(__file__).resolve().parents[1]
    report, draft = build_design_intake(root / "examples/intake/pmos-loaded-cs-reasoning.json")
    assert draft is not None and report["reasoning"]["hypotheses"][0]["comparisons"][0]["status"] == "not_evaluated"
    report, draft = build_design_intake(root / "examples/intake/physical-refinement.synthetic.json")
    assert draft is None and any("Synthetic" in issue for issue in report["issues"])
    assert report["physical_refinement"]["points"][0]["calculations"][0]["cdf_width_um"] == 1


def test_outer_reasoning_also_guards_an_iteration_template(tmp_path):
    first = round_record(tmp_path, 1, gain=5)
    second = round_record(tmp_path, 2, gain=5, omit_ac=True, width="2u")
    request = reasoning_request(tmp_path, first)
    template = json.loads(Path(second["task"]).read_text())
    next(s for s in template["generic_simulation"]["sources"] if s["name"] == "VIN_SRC")["dc_value"] = .55
    template_path = write(tmp_path / "unexplained-bias-template.json", template)
    report, draft = suggest_design_next(Path(second["task"]), Path(second["run"]),
        reasoning_path=request, iteration_path=campaign(tmp_path, [first, second]), template_path=template_path,
        model_path=tmp_path / "unused-model.json", policy_path=tmp_path / "unused-policy.json")
    assert draft is None and report["task_draft"] is None
    assert any("未被设计假设解释" in issue for issue in report["reasoning"]["issues"])
