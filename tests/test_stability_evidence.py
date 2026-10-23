"""Offline byte-integrity/native-margin fixtures, never new EDA evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from stability_fixtures import write_stb_pair
from test_stability import contract, response, stability_task
from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.design_iteration import build_design_iteration
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.design_review import build_design_review, render_design_review
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import CandidateEvaluation, RunRecord
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.stability import extract_stability_metrics
from virtuoso_design_agent.stability_evidence import (
    audit_stability_artifacts, canonical_sha256, compare_native_stb_margin,
    read_native_stb_margin,
)


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    return path


def bundle_fixture(root: Path):
    root.mkdir()
    task = stability_task()
    f, gain = response()
    metrics, details = extract_stability_metrics(f, gain, task.generic_simulation.stability)
    raw = root / "input_from_oa.raw"
    loop, margin = write_stb_pair(raw, metrics)
    for directory, names in ((root, ("si.env", "si_batch_stdout.log", "oa_netlist.scs",
                                    "oa_netlist_spectre.scs", "input_from_oa.scs", "spectre.out")),
                             (raw, ("dcOp.dc", "dcOpInfo.info"))):
        for name in names:
            (directory / name).write_text("software fixture only: " + name, encoding="utf-8")
    netlist = {"netlist_sha256": hashlib.sha256((root / "oa_netlist.scs").read_bytes()).hexdigest(),
               "spectre_netlist_sha256": hashlib.sha256((root / "oa_netlist_spectre.scs").read_bytes()).hexdigest()}
    payload = SubprocessBridgeAdapter._task_payload(task)
    details["_decoded_samples"] = {"frequency_hz": f, "raw_loop_gain": [[x.real, x.imag] for x in gain]}
    retained = bridge_worker._retain_stability_artifacts(root, SimpleNamespace(metadata={"output_dir": str(raw)}),
        payload, task.generic_simulation, task.stability_sweep.model_dump(mode="json"), netlist,
        details, root / "input_from_oa.scs")
    return task, metrics, Path(retained["evidence_file"]), retained


def test_native_scalar_nan_reason_and_typed_strings_are_preserved(tmp_path):
    metrics, _ = extract_stability_metrics(*response(), contract())
    loop, margin = write_stb_pair(tmp_path, metrics)
    native = read_native_stb_margin(loop, margin, contract())
    assert native["gain_margin_db"] is None
    assert native["gain_margin_reason"] == "No gain margin. Phase does not reach 0 Deg"
    assert native["state"] == "software fixture, no stability claim"
    assert native["phase_margin_deg"] == metrics["phase_margin_deg"]


@pytest.mark.parametrize("change", ["probe", "date", "version", "duplicate", "wrong_unit", "nan_without_reason", "infinite"])
def test_native_identity_and_scalar_corruption_never_pass(tmp_path, change):
    metrics, _ = extract_stability_metrics(*response(), contract())
    loop, margin = write_stb_pair(tmp_path, metrics)
    text = margin.read_text()
    if change in {"probe", "date", "version"}:
        old = {"probe": '"probe" "VLOOP"', "date": '"date" "software fixture only"',
               "version": '"version" "software-fixture"'}[change]
        text = text.replace(old, old[:-1] + '-wrong"')
    elif change == "duplicate":
        text = text.replace('"gainMarginFreq" "Hz" nan', '"gainMarginFreq" "Hz" nan\n"gainMarginFreq" "Hz" nan')
    elif change == "wrong_unit":
        text = text.replace('"phaseMargin" "Deg"', '"phaseMargin" "radians"')
    elif change == "nan_without_reason":
        text = text.replace('"gainMarginInfo" "No gain margin. Phase does not reach 0 Deg"\n', '')
    else:
        text = text.replace('"gainMargin" "dB" nan', '"gainMargin" "dB" inf')
    margin.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_native_stb_margin(loop, margin, contract())


def test_native_negative_pm_and_finite_negative_gm_are_not_parser_failures(tmp_path):
    f, _ = response()
    metrics, _ = extract_stability_metrics(f, [1000 / (1 + 1j * x / 1e3) ** 3 for x in f], contract())
    loop, margin = write_stb_pair(tmp_path, metrics)
    native = read_native_stb_margin(loop, margin, contract())
    comparison = compare_native_stb_margin(metrics, native, contract(), {"start_hz": f[0], "stop_hz": f[-1]})
    assert native["phase_margin_deg"] < 0 and native["gain_margin_db"] < 0
    assert comparison["passed"]  # Extraction matches; performance is separately judged.


@pytest.mark.parametrize("metric,change", [("phase_margin_deg", 1), ("loop_unity_gain_frequency_hz", 1e6),
                                           ("gain_margin_db", 3)])
def test_native_disagreement_is_incomplete_not_a_plausible_margin(tmp_path, metric, change):
    metrics, _ = extract_stability_metrics(*response(), contract())
    loop, margin = write_stb_pair(tmp_path, metrics)
    native = read_native_stb_margin(loop, margin, contract())
    native[metric] = (native[metric] or 0) + change
    result = compare_native_stb_margin(metrics, native, contract(), {"start_hz": 1, "stop_hz": 1e8})
    assert not result["passed"] and any(metric in s for s in result["issues"])


def test_persistent_bundle_replays_and_checks_exact_bytes_without_bridge(tmp_path, monkeypatch):
    task, metrics, path, retained = bundle_fixture(tmp_path / "bundle")
    monkeypatch.setattr(SubprocessBridgeAdapter, "_request", lambda *a, **kw: pytest.fail("offline audit called Bridge"))
    report = audit_stability_artifacts(path, expected_sha256=retained["evidence_sha256"])
    assert report["analysis_complete"] and report["metrics"] == metrics
    assert report["binding"]["stability"] == task.generic_simulation.stability.model_dump(mode="json")
    output = tmp_path / "audit.json"
    assert main(["stability-audit", str(path), "--output", str(output)]) == 0
    assert output.is_file()
    assert main(["stability-audit", str(path), "--output", str(output)]) == 2
    assert not report["remote_execution_performed"] and not report["current_OA_verified"]


@pytest.mark.parametrize("damage", ["raw", "missing_log", "empty_log", "manifest", "traversal", "roles", "snapshot", "wrong_hash"])
def test_damaged_or_partial_bundles_are_not_silently_reused(tmp_path, damage):
    _, _, path, retained = bundle_fixture(tmp_path / "bundle")
    bundle = json.loads(path.read_text())
    expected_hash = None
    if damage == "raw":
        (path.parent / bundle["loop_file"]).write_text("corrupted", encoding="utf-8")
    elif damage == "missing_log":
        (path.parent / bundle["log_file"]).unlink()
    elif damage == "empty_log":
        (path.parent / bundle["log_file"]).write_text("")
    elif damage == "manifest":
        bundle["artifact_manifest_sha256"] = "0" * 64
    elif damage == "traversal":
        bundle["artifact_manifest"][0]["path"] = "../outside"
        bundle["artifact_manifest_sha256"] = canonical_sha256(bundle["artifact_manifest"])
    elif damage == "roles":
        bundle["core_artifacts"].pop("margin_file")
    elif damage == "snapshot":
        snapshot_path = path.parent / bundle["response_snapshot"]
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["binding"]["target"]["cell"] = "different"
        save(snapshot_path, snapshot)
        for entry in bundle["artifact_manifest"]:
            if entry["path"] == bundle["response_snapshot"]:
                entry.update(size_bytes=snapshot_path.stat().st_size,
                             sha256=hashlib.sha256(snapshot_path.read_bytes()).hexdigest())
        bundle["artifact_manifest_sha256"] = canonical_sha256(bundle["artifact_manifest"])
    else:
        expected_hash = "0" * 64
    save(path, bundle)
    result = audit_stability_artifacts(path, expected_sha256=expected_hash)
    assert not result["analysis_complete"] and result["issues"]


@pytest.mark.parametrize("entry", ["../outside", "/outside", "C:\\outside", "\\\\host\\share\\file"])
def test_escaping_manifest_paths_are_rejected_before_filesystem_resolution(tmp_path, monkeypatch, entry):
    _, _, evidence, _ = bundle_fixture(tmp_path / "bundle")
    bundle = json.loads(evidence.read_text())
    bundle["artifact_manifest"][0]["path"] = entry
    bundle["artifact_manifest_sha256"] = canonical_sha256(bundle["artifact_manifest"])
    save(evidence, bundle)
    forbidden = evidence.parent / entry
    original = Path.resolve

    def guarded(path, *args, **kwargs):
        if path == forbidden:
            pytest.fail("unsafe manifest entry reached filesystem resolution")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded)
    report = audit_stability_artifacts(evidence)
    assert not report["analysis_complete"] and any("escaping" in issue for issue in report["issues"])


def test_interrupt_keeps_own_partial_evidence_and_parallel_calls_never_collide(tmp_path):
    payload = {"analysis": "stability", "simulation_output_root": str(tmp_path), "task_id": "fixture"}
    with bridge_worker._generic_simulation_work_directory(payload) as first:
        with bridge_worker._generic_simulation_work_directory(payload) as second:
            assert first != second
        with pytest.raises(KeyboardInterrupt):
            with bridge_worker._generic_simulation_work_directory(payload) as failed:
                (failed / "partial.psf").write_text("software partial fixture")
                raise KeyboardInterrupt()
    assert (failed / "partial.psf").is_file()
    assert json.loads((failed / "lifecycle-finish.json").read_text())["must_reconcile_before_retry"]
    assert json.loads((first / "lifecycle-finish.json").read_text())["status"] == "returned"


def test_adapter_provides_distinct_persistent_roots_before_stb_worker(tmp_path, monkeypatch):
    adapter = SubprocessBridgeAdapter(tmp_path / "not-invoked.exe", artifact_root=tmp_path / "artifacts")
    roots = []
    monkeypatch.setattr(adapter, "_request", lambda action, payload, **kw: roots.append(payload["simulation_output_root"]) or {})
    for _ in range(2):
        adapter.simulate(stability_task(), {})
    assert roots[0] != roots[1] and all(Path(p).is_absolute() for p in roots)
    monkeypatch.setattr(bridge_worker, "_client", lambda: pytest.fail("remote preflight must not start"))
    with pytest.raises(RuntimeError, match="persistent"):
        bridge_worker.simulate_existing_schematic(SubprocessBridgeAdapter._task_payload(stability_task()))


def recorded_bundle(tmp_path):
    task, metrics, bundle, retained = bundle_fixture(tmp_path / "bundle")
    evaluations = evaluate_constraints(metrics, task.constraints)
    candidate = CandidateEvaluation(index=1, parameters={}, metrics=metrics, constraints=evaluations,
        feasible=True, total_violation=0, evidence_source="eda_result")
    started = datetime(2026, 10, 23, tzinfo=UTC)
    binding = json.loads((bundle.parent / "stability-response.json").read_text())["binding"]
    run = RunRecord(task_id=task.id, plan_token=build_plan(task).confirmation_token,
        adapter="virtuoso-bridge-subprocess", status="succeeded", started_at=started,
        finished_at=started + timedelta(seconds=1), candidates=[candidate], actions=[{
            "action": "simulation.candidate.1", "status": "succeeded", "started_at": started,
            "finished_at": started + timedelta(seconds=1), "evidence_source": "eda_result", "details": {
                "metrics": metrics, "evidence": {"netlist": {"sha256": binding["netlist_sha256"]},
                    "testbench": {"sha256": binding["wrapper_sha256"]},
                    "simulation": {"stability_artifacts": retained}}}}])
    return task, save(tmp_path / "task.json", task.model_dump(mode="json")), save(tmp_path / "run.json", run.model_dump(mode="json")), bundle


def test_review_and_iteration_reject_stale_raw_evidence_without_resimulation(tmp_path):
    task, task_path, run_path, bundle = recorded_bundle(tmp_path)
    request = save(tmp_path / "iteration.json", {"max_rounds": 3,
        "stages": [{"id": "loop", "analysis": "stability", "constraint_metrics": ["phase_margin_deg"]}],
        "rounds": [{"task": str(task_path), "run": str(run_path)}]})
    review = build_design_review(task_path, run_path)
    assert not review["execution"]["incomplete_evidence"]
    assert "STB 原始证据与原生对照" in render_design_review(review)
    iteration, _ = build_design_iteration(request)
    assert iteration["status"] == "specifications_satisfied"
    record = json.loads(bundle.read_text())
    (bundle.parent / record["loop_file"]).write_text("changed after run")
    review = build_design_review(task_path, run_path)
    assert review["execution"]["incomplete_evidence"] and review["candidates"][0]["status"] == "incomplete"
    iteration, draft = build_design_iteration(request)
    assert iteration["status"] == "reconcile_execution" and not iteration["best_feasible_anchor"]
    assert draft.operation.value == "schematic.inspect" and not draft.safety.allow_remote_compute
    suggestion, _ = suggest_design_next(task_path, run_path)
    assert suggestion["status"] == "reconcile_execution_first"


def test_old_stb_record_without_bundle_is_unverified_not_recomputed(tmp_path):
    _, task_path, run_path, _ = recorded_bundle(tmp_path)
    raw = json.loads(run_path.read_text())
    raw["actions"] = []
    save(run_path, raw)
    report = build_design_review(task_path, run_path)
    assert report["execution"]["incomplete_evidence"]
    assert any("cover" in issue for row in report["stability_evidence"] for issue in row["issues"])


def test_one_nominal_bundle_cannot_cover_declared_finite_conditions(tmp_path):
    task, task_path, run_path, _ = recorded_bundle(tmp_path)
    raw = task.model_dump(mode="json", exclude_none=True)
    raw["operating_conditions"] = [
        {"name": "cold", "process_corner": "tt", "temperature_c": -40, "vdd_v": .81},
        {"name": "nominal", "process_corner": "tt", "temperature_c": 27, "vdd_v": .9},
    ]
    raw["generic_simulation"]["operating_condition_supply_source"] = "VDD_SRC"
    task = TaskSpec.model_validate(raw)
    save(task_path, task.model_dump(mode="json"))
    run = json.loads(run_path.read_text())
    run["plan_token"] = build_plan(task).confirmation_token
    save(run_path, run)
    review = build_design_review(task_path, run_path)
    assert review["execution"]["incomplete_evidence"]
    assert any("finite conditions exactly" in issue for row in review["stability_evidence"] for issue in row["issues"])


def test_winner_only_stb_uses_its_own_sweep_and_blocks_stale_winner_evidence(tmp_path):
    stb_task, task_path, run_path, bundle = recorded_bundle(tmp_path)
    from test_generic_simulation import _task
    raw = stb_task.model_dump(mode="json", exclude_none=True)
    raw.update(operation="design.tune", analysis="ac", ac_sweep=_task().ac_sweep.model_dump(mode="json"),
               instance_parameter_space=[{"instance": "MN0", "parameter": "w", "values": ["1u"]}],
               constraints=[{"metric": "low_frequency_gain_db", "relation": ">=", "value": 10}],
               winner_verification={"analysis_stages": [{"id": "loop", "analysis": "stability",
                   "constraint_metrics": ["phase_margin_deg"]}],
                   "constraints": [{"metric": "phase_margin_deg", "relation": ">=", "value": 60}],
                   "stability_sweep": raw.pop("stability_sweep")})
    task = TaskSpec.model_validate(raw)
    save(task_path, task.model_dump(mode="json"))
    run = json.loads(run_path.read_text())
    winner = dict(run["candidates"][0])
    winner["analysis_stages"] = [{"stage_id": "loop", "analysis": "stability", "metrics": winner["metrics"],
        "constraints": winner["constraints"], "passed": True, "evidence_source": "eda_result"}]
    candidate = run["candidates"][0]
    candidate["metrics"] = {"low_frequency_gain_db": 20}
    candidate["constraints"] = [v.model_dump(mode="json") for v in evaluate_constraints(candidate["metrics"], task.constraints)]
    run.update(plan_token=build_plan(task).confirmation_token, winner_verification=winner,
               selected_parameters={}, selected_instance_parameters={}, selected_metrics={**candidate["metrics"], **winner["metrics"]})
    run["actions"][0]["action"] = "simulation.winner.1.stages.shared-netlist"
    save(run_path, run)
    review = build_design_review(task_path, run_path)
    assert not review["execution"]["incomplete_evidence"]
    assert {row["role"] for row in review["stability_evidence"]} == {"winner"}
    request = save(tmp_path / "winner-iteration.json", {"max_rounds": 3,
        "stages": [{"id": "ac", "analysis": "ac", "constraint_metrics": ["low_frequency_gain_db"]}],
        "rounds": [{"task": str(task_path), "run": str(run_path)}]})
    assert build_design_iteration(request)[0]["status"] == "specifications_satisfied"
    (bundle.parent / "spectre.out").unlink()
    report, draft = build_design_iteration(request)
    assert report["status"] == "reconcile_execution" and draft.operation.value == "schematic.inspect"
    assert report["history"][-1]["stability_evidence_issues"]
