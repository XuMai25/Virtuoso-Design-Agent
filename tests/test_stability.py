"""Analytical/software fixtures only: none of these tests invokes EDA."""
from __future__ import annotations

import cmath
import hashlib
import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.generic_simulation import GenericOaSimulationSpec, render_generic_oa_testbench
from virtuoso_design_agent.models import CandidateEvaluation, TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.stability import StabilitySpec, extract_stability_metrics
from test_generic_simulation import _generic_spec, _task, _tune_payload


def contract(**changes):
    return StabilitySpec.model_validate({
        "probe_source": "VLOOP", "feedback_configuration": "software fixture: OUT to FB",
        "loop_scope": "single_loop", "raw_loop_gain_convention": "return_ratio",
        "open_loop_rhp_poles": 0, "assumptions_source": "software_inference", **changes,
    })


def stability_task():
    raw = _task().model_dump(mode="json", exclude_none=True)
    raw.update(id="software-stb-fixture", analysis="stability")
    raw.pop("ac_sweep")
    raw["stability_sweep"] = {"start_hz": 1, "stop_hz": 1e8, "points_per_decade": 40}
    raw["generic_simulation"]["sources"].append({
        "name": "VLOOP", "kind": "voltage", "positive_node": "OUT",
        "negative_node": "IN", "dc_value": 0,
    })
    raw["generic_simulation"]["stability"] = contract().model_dump(mode="json")
    raw["design_context"]["required_analyses"].append("stability")
    raw["design_context"]["metrics"].append("phase_margin_deg")
    raw["constraints"] = [{"metric": "phase_margin_deg", "relation": ">=", "value": 60}]
    return TaskSpec.model_validate(raw)


def response():
    frequencies = [10 ** (i / 100) for i in range(801)]
    return frequencies, [100 / ((1 + 1j * f / 1e3) * (1 + 1j * f / 1e6)) for f in frequencies]


def test_margin_matches_independent_two_pole_solution_and_sign_convention():
    f, gain = response()
    # Solve (1+(f/p1)^2)*(1+(f/p2)^2) = A0^2 analytically.
    b, c = 1e6 + 1e12, 1e6 * 1e12 * (1 - 100 ** 2)
    unity = math.sqrt((-b + math.sqrt(b * b - 4 * c)) / 2)
    expected_pm = 180 - math.degrees(math.atan(unity / 1e3) + math.atan(unity / 1e6))
    metrics, diagnostics = extract_stability_metrics(f, gain, contract())
    assert metrics["loop_unity_gain_frequency_hz"] == pytest.approx(unity, rel=1e-4)
    assert metrics["phase_margin_deg"] == pytest.approx(expected_pm, abs=.01)
    assert "gain_margin_db" not in metrics  # never invent infinite GM from a truncated sweep
    assert diagnostics["analysis_complete"] and not diagnostics["global_stability_proven"]
    assert diagnostics["assumptions_source"] == "software_inference"
    normalized, _ = extract_stability_metrics(f, [-x for x in gain], contract(raw_loop_gain_convention="feedback_transfer"))
    assert normalized == metrics
    wrong, issue = extract_stability_metrics(f, [-x for x in gain], contract())
    assert "phase_margin_deg" not in wrong and not issue["analysis_complete"]


def test_negative_margin_is_a_measured_failure_not_missing_evidence():
    f, _ = response()
    gain = [1000 / (1 + 1j * x / 1e3) ** 3 for x in f]
    metrics, details = extract_stability_metrics(f, gain, contract())
    assert details["analysis_complete"]
    assert metrics["phase_margin_deg"] < 0
    assert metrics["gain_margin_db"] == pytest.approx(-20 * math.log10(125), abs=.01)


@pytest.mark.parametrize("magnitudes", [[10, .5, 2, .1], [10, 1, 10, .1], [10, 1, 1, .1], [10, 8, 5, 2]])
def test_multiple_touching_plateau_or_uncovered_gain_crossing_is_incomplete(magnitudes):
    f = [1, 10, 100, 1000]
    gain = [m * cmath.exp(-1j * i * .4) for i, m in enumerate(magnitudes)]
    metrics, diagnostics = extract_stability_metrics(f, gain, contract())
    assert not diagnostics["analysis_complete"] and "phase_margin_deg" not in metrics


def test_exact_through_crossing_is_counted_once():
    values = [10 + 0j, -1j, .1 * cmath.exp(-2j)]
    metrics, details = extract_stability_metrics([1, 10, 100], values, contract())
    assert details["analysis_complete"] and len(details["gain_crossings"]) == 1
    assert metrics["phase_margin_deg"] == pytest.approx(90)


@pytest.mark.parametrize("frequencies,values", [([], []), ([1, 1, 3], [3, 2, 1]),
    ([1, 2, float('inf')], [3, 2, 1]), ([1, 2, 3], [3, 0, 1]),
    ([1, 2, 3], [3, complex(float('nan')), 1]), ([1, 2, 3], [3, 2])])
def test_invalid_or_empty_waveforms_are_not_accepted(frequencies, values):
    with pytest.raises(ValueError):
        extract_stability_metrics(frequencies, values, contract())


def test_stb_uses_typed_probe_and_keeps_existing_oa_include_and_authority():
    task = stability_task()
    deck = render_generic_oa_testbench(task.generic_simulation, analysis="stability",
        remote_netlist_path="/data/xum/fixture/netlist", model_configuration="// fixture",
        stability_sweep=task.stability_sweep.model_dump(mode="json"))
    assert 'include "/data/xum/fixture/netlist"' in deck
    assert "stb stb start=1 stop=100000000 dec=40 probe=VLOOP annotate=status" in deck
    assert "VLOOP (OUT IN) vsource dc=0" in deck
    assert "ac ac" not in deck
    assert SubprocessBridgeAdapter._task_payload(task)["stability_sweep"]["stop_hz"] == 1e8
    assert build_plan(task).requires_remote_compute and not build_plan(task).requires_remote_write
    assert "STB" in " ".join(step.description for step in build_plan(task).steps)


@pytest.mark.parametrize("change", [{"probe_source": "missing"}, {"raw_loop_gain_convention": "guess"},
    {"loop_scope": "arbitrary"}, {"open_loop_rhp_poles": 1}, {"assumptions_source": "guess"}])
def test_probe_and_assumptions_cannot_be_guessed(change):
    raw = stability_task().model_dump(mode="json", exclude_none=True)
    raw["generic_simulation"]["stability"].update(change)
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(raw)


@pytest.mark.parametrize("field,value", [("dc_value", .1), ("negative_node", "0"), ("kind", "current")])
def test_stability_rejects_nonzero_grounded_or_current_probe(field, value):
    raw = stability_task().model_dump(mode="json", exclude_none=True)
    raw["generic_simulation"]["sources"][-1][field] = value
    with pytest.raises(ValidationError, match="probe"):
        TaskSpec.model_validate(raw)


def test_stage_and_winner_verification_keep_only_matching_sweeps():
    raw = stability_task().model_dump(mode="json", exclude_none=True)
    raw.pop("analysis")
    raw["analysis_stages"] = [{"id": "bias", "analysis": "dc", "constraint_metrics": ["output_dc_v"]},
        {"id": "loop", "analysis": "stability", "constraint_metrics": ["phase_margin_deg"]}]
    raw["constraints"].append({"metric": "output_dc_v", "relation": ">=", "value": .1})
    task = TaskSpec.model_validate(raw)
    dc = TaskExecutor._analysis_stage_task(task, task.analysis_stages[0], {})
    stb = TaskExecutor._analysis_stage_task(task, task.analysis_stages[1], {})
    assert dc.stability_sweep is None and stb.stability_sweep == task.stability_sweep
    # A winner-only expensive check must not leak into the nominal analysis.
    raw = _tune_payload()
    raw["generic_simulation"] = task.generic_simulation.model_dump(mode="json", exclude_none=True)
    raw["design_context"] = task.design_context.model_dump(mode="json")
    raw["winner_verification"] = {"analysis_stages": [{"id": "loop", "analysis": "stability",
        "constraint_metrics": ["phase_margin_deg"]}], "constraints": [{"metric": "phase_margin_deg",
        "relation": ">=", "value": 60}], "stability_sweep": task.stability_sweep.model_dump(mode="json")}
    winner_task = TaskSpec.model_validate(raw)
    selected = CandidateEvaluation(index=1, parameters={}, metrics={}, constraints=[], feasible=True,
                                   total_violation=0, evidence_source="software_inference")
    verified = TaskExecutor._winner_verification_task(winner_task, winner_task, selected)
    assert verified.stability_sweep == task.stability_sweep and verified.ac_sweep is None


def test_stb_psf_selection_binds_raw_bytes_and_rejects_ambiguous_or_wrong_sweep(tmp_path, monkeypatch):
    task = stability_task()
    raw_file = tmp_path / "stb.stb"
    raw_file.write_text("software fixture, not actual Spectre data", encoding="utf-8")
    f, gain = response()
    monkeypatch.setattr(bridge_worker, "_read_psf_ascii_data", lambda *a, **kw: {"freq": f, "loopGain": gain})
    result = SimpleNamespace(metadata={"output_dir": str(tmp_path)})
    metrics, details = bridge_worker._generic_stability_metrics_from_result(
        result, task.generic_simulation, task.stability_sweep.model_dump())
    assert details["raw_file"]["sha256"] == hashlib.sha256(raw_file.read_bytes()).hexdigest()
    assert metrics["phase_margin_deg"] > 60
    with pytest.raises(RuntimeError, match="endpoints"):
        bridge_worker._generic_stability_metrics_from_result(result, task.generic_simulation,
            {"start_hz": 10, "stop_hz": 1e8})
    raw_file.unlink()
    for name in ("other", "second"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "stb.stb").write_text("software fixture", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ambiguous"):
        bridge_worker._generic_stability_metrics_from_result(result, task.generic_simulation,
            task.stability_sweep.model_dump())


def test_legacy_dc_ac_still_has_no_implicit_stability():
    spec = GenericOaSimulationSpec.model_validate(_generic_spec())
    assert spec.stability is None
    with pytest.raises(ValueError, match="explicit stability"):
        spec.validate_analysis("stability")
    assert "phase_margin_deg" not in spec.metric_names_for_analysis("ac")
