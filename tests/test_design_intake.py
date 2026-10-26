"""Local composition/failure fixtures, not new OA or circuit-performance evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_intake import build_design_intake, render_design_intake
from virtuoso_design_agent.measurements import (
    MeasurementRequest, compile_measurement_request, compile_measurement_task,
)
from virtuoso_design_agent.models import ExecutionCheckpoint, RunRecord, TaskSpec
from virtuoso_design_agent.planner import build_plan

from test_generic_simulation import _tune_payload
from test_measurement_iteration import measurement, round_record, write
from test_onboarding import _flat_resolution, _write_onboarding_draft


@pytest.fixture
def intake(tmp_path):
    source = write(tmp_path / "task.json", _tune_payload())
    measures = write(tmp_path / "measurements.json", {"measurements": [measurement()]})
    request = write(tmp_path / "intake.json", {
        "source": {"kind": "task", "task": source.name},
        "measurements": measures.name,
        "rationale": [{"text": "复用已配置的局部尺寸域；不把有限点最佳当全局最优。", "source": "software_inference",
                       "instance": "MN0", "parameter": "w"}],
    })
    return request, source, measures


def test_composes_existing_measurement_compiler_without_changing_inputs(intake):
    request, source, measures = intake
    original = {path: path.read_bytes() for path in intake}
    expected_report, expected = compile_measurement_task(source, measures)
    report, draft = build_design_intake(request)
    assert draft == expected
    assert draft is not None
    assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write,
                    draft.safety.replace_existing, draft.create_if_missing))
    assert report["context"]["design_context"] == draft.design_context.model_dump(mode="json", exclude_none=True)
    assert report["context"]["instance_parameter_space"] == [{"instance": "MN0", "parameter": "w", "values": ["1u", "2u", "3u"]}]
    assert report["task_draft"]["plan_token"] == expected_report["plan_token"] == build_plan(draft).confirmation_token
    assert not report["current_input_fingerprints"]["current_remote_state_verified"]
    assert report["sources"]["task"]["path"] == str(source.resolve())
    assert report["rationale"][0]["source"] == "software_inference"
    assert all(path.read_bytes() == data for path, data in original.items())
    assert "不是当前设计结果" not in render_design_intake(report)  # No fabricated history section.
    raw = json.loads(source.read_text())
    raw["safety"]["replace_existing"] = True
    write(source, raw)
    with pytest.raises(ValueError, match="never replaces the target"):
        build_design_intake(request)  # Do not normalize an invalid source past the original contract.


def test_measurement_composition_does_not_mutate_in_memory_payload(intake):
    _, source, measures = intake
    raw = json.loads(source.read_text())
    before = json.dumps(raw, sort_keys=True)
    compile_measurement_request(raw, MeasurementRequest.model_validate_json(measures.read_text()), sources={})
    assert json.dumps(raw, sort_keys=True) == before


def test_source_paths_resolve_against_manifest_and_cli_never_overwrites(intake, monkeypatch, tmp_path, capsys):
    request, source, _ = intake
    monkeypatch.chdir(tmp_path.parent)
    output, markdown, draft = (tmp_path / name for name in ("report.json", "report.md", "draft.json"))
    args = ["design-intake", str(request), "--output", str(output), "--markdown", str(markdown), "--task-output", str(draft)]
    assert main(args) == 0
    saved = {path: path.read_bytes() for path in (source, output, markdown, draft)}
    assert TaskSpec.model_validate_json(draft.read_text()).measurement_plan is not None
    assert "角色与允许改动" in markdown.read_text(encoding="utf-8")
    assert main(args) == 2
    assert main(["design-intake", str(request), "--output", str(tmp_path / "same.json"),
                 "--task-output", str(tmp_path / "same.json")]) == 2
    assert not (tmp_path / "same.json").exists()
    assert main(["design-intake", str(request), "--output", str(source)]) == 2
    assert all(path.read_bytes() == data for path, data in saved.items())
    capsys.readouterr()


def test_intake_does_not_construct_remote_adapters(intake, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("local intake attempted remote execution")
    monkeypatch.setattr("virtuoso_design_agent.cli.SubprocessBridgeAdapter.__init__", forbidden)
    monkeypatch.setattr("virtuoso_design_agent.cli.run_bridge_lifecycle", forbidden)
    assert build_design_intake(intake[0])[1] is not None


@pytest.mark.parametrize("missing", ["rationale", "measurements", "measurement_definition", "uncertainty", "measurement_uncertainty"])
def test_unresolved_decisions_cannot_emit_task_or_partial_output(intake, missing, tmp_path, capsys):
    request, _, measures = intake
    raw = json.loads(request.read_text())
    if missing in {"rationale", "measurements"}:
        raw.pop(missing)
    elif missing == "uncertainty":
        raw["uncertainties"] = ["反馈分支是否接入尚未确认。"]
    else:
        write(measures, {"measurements": [measurement(signals=["V(UNKNOWN,0)"])]} if missing == "measurement_definition"
              else {"measurements": [measurement()], "uncertainties": ["负载未确认。"]})
    write(request, raw)
    report, draft = build_design_intake(request)
    assert draft is None and report["task_draft"] is None
    assert report["status"] != "draft_ready" and (report["uncertainties"] or report["issues"])
    output, task_output = tmp_path / "summary.json", tmp_path / "draft.json"
    assert main(["design-intake", str(request), "--output", str(output), "--task-output", str(task_output)]) == 2
    assert not output.exists() and not task_output.exists()
    assert main(["design-intake", str(request), "--output", str(output), "--json"]) == 0
    assert output.exists()
    capsys.readouterr()


@pytest.mark.parametrize("rationale", [
    {"role": "invented.role"}, {"instance": "MN0", "parameter": "invented"},
    {"metric": "invented_metric"}, {"semantic_parameter": "invented"},
    {"source": "eda_result"}, {"source": "bridge_readback"},
])
def test_rationale_cannot_invent_bindings_or_promote_authored_reasoning(intake, rationale):
    request, _, _ = intake
    raw = json.loads(request.read_text())
    raw["rationale"] = [{"text": "Authored local rationale", "source": "software_inference", **rationale}]
    write(request, raw)
    with pytest.raises(ValueError):
        build_design_intake(request)


def test_onboarding_source_reuses_exact_draft_binding_and_original_resolution(tmp_path):
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = write(tmp_path / "resolution.json", _flat_resolution(draft_path))
    request = write(tmp_path / "intake.json", {
        "source": {"kind": "onboarding", "draft": draft_path.name, "resolution": resolution.name},
        "rationale": [{"text": "使用已审阅的连接、角色和单次 AC 配置，不补造参数。", "source": "user_input"}],
    })
    report, task = build_design_intake(request)
    assert task is None  # Original intent has no explicit measurement definitions.
    assert report["context"]["target"]["cell"] == "vda_flat"
    assert report["context"]["design_context"]["frozen_instances"] == ["MN0", "RD0"]
    measures = write(tmp_path / "measurements.json", {"measurements": [
        measurement(metric="output_dc_v", analysis="dc", quantity="voltage", unit="V", signals=["V(OUT,0)"]),
        measurement(metric="low_frequency_gain_v_per_v", quantity="gain", unit="V/V"),
        measurement(metric="bandwidth_3db_hz", quantity="bandwidth", unit="Hz"),
    ]})
    raw = json.loads(request.read_text())
    raw["measurements"] = measures.name
    write(request, raw)
    report, task = build_design_intake(request)
    assert task is None and any("does not declare requested analyses: dc" in issue for issue in report["issues"])
    intent = json.loads(resolution.read_text())
    intent["required_analyses"].append("dc")  # Explicit intent; the compiler must not infer this permission.
    write(resolution, intent)
    report, task = build_design_intake(request)
    assert task is not None, report["issues"]
    assert task.design_context.frozen_instances == ["MN0", "RD0"]
    raw = json.loads(resolution.read_text())
    raw["draft_sha256"] = "0" * 64
    write(resolution, raw)
    with pytest.raises(ValueError, match="SHA-256"):
        build_design_intake(request)


def _history_intake(tmp_path, *, complete=True):
    history = round_record(tmp_path, 1, complete=complete)
    raw = json.loads(Path(history["task"]).read_text())
    source = write(tmp_path / "current.json", raw)
    # Keep scope explicit; do not fill missing historical metrics with a newer round.
    measures = write(tmp_path / "measurements.json", {"measurements": [
        measurement(), measurement(metric="output_dc_v", analysis="dc", quantity="voltage", unit="V", signals=["V(OUT,0)"]),
    ]})
    request = write(tmp_path / "intake.json", {
        "source": {"kind": "task", "task": source.name}, "measurements": measures.name,
        "history": history,
        "rationale": [{"text": "Use the bound saved run only as comparison, not current OA.", "source": "software_inference"}],
    })
    return request, source, history


def test_history_preserves_sources_and_flags_changed_measurements_and_search_scope(tmp_path):
    request, source, history = _history_intake(tmp_path)
    report, draft = build_design_intake(request)
    assert draft is not None
    bound = report["history"]
    assert bound["candidate_count"] == 1 and bound["selected_candidate"] is None
    assert bound["last_candidate_diagnostic"]["metric_sources"]["low_frequency_gain_db"] == "eda_result"
    assert "analysis" in bound["changed_recorded_inputs"]  # Explicit measurement context added.
    assert not bound["current_metrics_reused"] and not bound["current_remote_state_verified"]
    raw = json.loads(source.read_text())
    raw["limits"]["max_iterations"] += 1
    write(source, raw)
    assert "decision_scope" in build_design_intake(request)[0]["history"]["changed_recorded_inputs"]
    raw["pdk_profile"] = "nics4304_tsmc28_svt"
    write(source, raw)
    with pytest.raises(ValueError, match="target/PDK"):
        build_design_intake(request)
    raw["pdk_profile"] = "nics4304_tsmc28"
    raw["target"]["cell"] = "vda_other"
    write(source, raw)
    with pytest.raises(ValueError, match="target/PDK"):
        build_design_intake(request)


def test_historical_identity_and_interrupted_run_block_replay(tmp_path):
    request, _, history = _history_intake(tmp_path, complete=False)
    report, draft = build_design_intake(request)
    assert draft is None and report["history"]["next_status_for_historical_run"] == "reconcile_execution_first"
    run_path = Path(history["run"])
    run = json.loads(run_path.read_text())
    run["plan_token"] = "0" * 16
    write(run_path, run)
    with pytest.raises(ValueError, match="plan token mismatch"):
        build_design_intake(request)


def test_demo_history_stays_software_only(tmp_path):
    request, _, history = _history_intake(tmp_path)
    run_path = Path(history["run"])
    raw = json.loads(run_path.read_text())
    raw["adapter"] = "deterministic-demo"
    for candidate in raw["candidates"]:
        candidate["evidence_source"] = "software_inference"
        for stage in candidate["analysis_stages"]:
            stage["evidence_source"] = "software_inference"
    write(run_path, raw)
    report, draft = build_design_intake(request)
    assert draft is not None  # A safe task is not a performance claim.
    assert report["history"]["next_status_for_historical_run"] == "software_only"
    candidate = report["history"]["last_candidate_diagnostic"]
    assert set(candidate["metric_sources"].values()) == {"software_inference"}
    assert "software_inference" in render_design_intake(report)


def test_changed_search_domain_is_not_mistaken_for_identical_recorded_inputs(intake, tmp_path):
    request, source, _ = intake
    raw = json.loads(source.read_text())
    # No run is fabricated here: compare the existing fingerprint helper's inputs.
    from virtuoso_design_agent.design_intake import _input_fingerprints
    before = _input_fingerprints(TaskSpec.model_validate(raw))
    raw["instance_parameter_space"][0]["values"].append("4u")
    after = _input_fingerprints(TaskSpec.model_validate(raw))
    assert before["sha256"]["search_domain"] != after["sha256"]["search_domain"]
    assert before["sha256"]["parameters"] == after["sha256"]["parameters"]


def test_existing_task_with_measurement_plan_needs_no_separate_measurement_file(intake):
    request, source, measures = intake
    _, compiled = compile_measurement_task(source, measures)
    write(source, compiled.model_dump(mode="json", exclude_none=True))
    raw = json.loads(request.read_text())
    raw.pop("measurements")
    write(request, raw)
    report, draft = build_design_intake(request)
    assert draft is not None and report["measurement_compile"] is None
    assert report["context"]["measurement_plan"] == compiled.measurement_plan.model_dump(mode="json")


def test_pending_checkpoint_requires_reconciliation_even_when_run_succeeded(tmp_path):
    request, _, history = _history_intake(tmp_path)
    run = RunRecord.model_validate_json(Path(history["run"]).read_text())
    checkpoint = ExecutionCheckpoint(
        task_id=run.task_id, plan_token=run.plan_token, adapter=run.adapter, started_at=run.started_at,
        initial_parameters={}, expected_oa_parameters={}, next_candidate_index=2,
        candidates=run.candidates, complete=True, pending_oa_instance_parameters={"MN0": {"w": "2u"}},
    )
    path = write(tmp_path / "checkpoint.json", checkpoint.model_dump(mode="json"))
    raw = json.loads(request.read_text())
    raw["history"]["checkpoint"] = path.name
    write(request, raw)
    report, draft = build_design_intake(request)
    assert draft is None and report["history"]["checkpoint"]["pending_write"]
    checkpoint.plan_token = "0" * 16
    write(path, checkpoint.model_dump(mode="json"))
    with pytest.raises(ValueError, match="different task or execution"):
        build_design_intake(request)


def test_sources_must_still_match_their_bound_bytes_at_end(intake, monkeypatch):
    import virtuoso_design_agent.design_intake as module
    request, source, _ = intake
    compile_request = module.compile_measurement_request

    def change_source(*args, **kwargs):
        result = compile_request(*args, **kwargs)
        source.write_bytes(source.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(module, "compile_measurement_request", change_source)
    with pytest.raises(ValueError, match="source changed"):
        build_design_intake(request)


def test_tracked_example_compiles_without_requiring_retained_local_artifacts():
    root = Path(__file__).resolve().parents[1]
    report, draft = build_design_intake(root / "examples/intake/pmos-loaded-cs.json")
    assert draft is not None and len(draft.candidate_set.candidates) == 3
    assert len(draft.measurement_plan.measurements) == 6
    assert draft.target.cell == "vda_pmos_loaded_cs_onboarding_001"
    assert report["history"] is None
    assert "theory_then_standalone_preview" in render_design_intake(report)
