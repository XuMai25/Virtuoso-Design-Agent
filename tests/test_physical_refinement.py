"""Local calculator/compiler tests, using explicitly synthetic importer fixtures."""

import json
from pathlib import Path

import pytest

from virtuoso_design_agent.characterization import MosCharacterizationArtifact, lookup_mos_by_gm_over_id
from virtuoso_design_agent.design_intake import build_design_intake
from virtuoso_design_agent.design_review import file_reference
from virtuoso_design_agent.design_next import suggest_design_next
from virtuoso_design_agent.measurements import canonical_sha256
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.physical_refinement import compile_physical_refinement
from virtuoso_design_agent.profiles import load_pdk_profile

from test_generic_simulation import _task
from test_measurement_iteration import measurement, round_record, write


def _artifact():
    # Numerical fixture, not measured TSMC data. The importer-record shape is
    # used only to test promotion gates; nothing here is circuit evidence.
    return {"id": "local-fixture", "source": "pdk_characterization", "source_artifact_sha256": "a" * 64,
            "pdk_profile": "nics4304_tsmc28", "process_corner": "top_tt", "temperature_c": 27,
            "characterized_width_um": 1, "model_parameters_by_polarity": {"nmos": {"nf": "2"}},
            "raw_data_evidence_source": "eda_result", "normalized_point_evidence_source": "software_inference",
            "points": [{"id": f"point-{i}", "model": "fixture_nmos", "polarity": "nmos", "length_um": .03,
                        "vgs_magnitude_v": vgs, "vds_magnitude_v": .45, "vsb_magnitude_v": 0,
                        "vdsat_magnitude_v": .1, "drain_current_density_a_per_um": density,
                        "gm_over_id_per_v": gmid, "gds_over_id_per_v": .1}
                       for i, (vgs, density, gmid) in enumerate(((.3, 1e-5, 20), (.5, 3e-5, 10)))]}


def _binding(parameter):
    return {"instance": "MN0", "parameter": parameter, "unit": "um", "literal_format": "spectre_si",
            "meaning": "Explicit local test convention, not an inferred PDK mapping"}


@pytest.fixture
def physical(tmp_path):
    raw = _task().model_dump(mode="json")
    raw["generic_simulation"]["temperature_c"] = 27
    raw["objective"] = {"metric": "low_frequency_gain_db", "goal": "maximize"}
    task = TaskSpec.model_validate(raw)
    task_path = write(tmp_path / "task.json", task.model_dump(mode="json"))
    now = "2026-10-26T00:00:00Z"
    profile = load_pdk_profile(task.pdk_profile)
    input_path = tmp_path / "mos_characterization.scs"
    input_path.write_text(f'// Synthetic importer fixture only\ninclude "{profile.model_include}" section={profile.model_section}\n', encoding="utf-8")
    entries = [{"path": "mos_characterization.scs", "size_bytes": input_path.stat().st_size,
                "sha256": file_reference(input_path)["sha256"]}]
    entries.extend({"path": path, "size_bytes": 1, "sha256": "a" * 64} for path in (
        "mos_characterization.raw/dcOp.dc", "mos_characterization.raw/dcOpInfo.info", "spectre.out"))
    manifest_hash = canonical_sha256(sorted(entries, key=lambda entry: entry["path"]))
    artifact = _artifact()
    artifact["source_artifact_sha256"] = manifest_hash
    characterization = write(tmp_path / "characterization.json", {
        "task_id": "local-fixture-characterization", "plan_token": "fixture-only", "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded", "started_at": now, "finished_at": now,
        "notes": ["Synthetic importer fixture; not a real PDK result"],
        "actions": [{"action": "device.characterize.validate", "status": "succeeded",
                     "started_at": now, "finished_at": now, "evidence_source": "software_inference",
                     "details": {"artifact": artifact, "point_counts": {"holdout": 1},
                                 "holdout_audit": {"passed": True}}},
                    {"action": "device.characterize", "status": "succeeded", "started_at": now, "finished_at": now,
                     "evidence_source": "eda_result", "details": {"evidence": {"artifact_manifest": entries,
                         "artifact_manifest_complete": True, "manifest_sha256": manifest_hash,
                         "remote_run_root": "/data/xum/local-fixture", "remote_simulation_dir": "/data/xum/local-fixture/sim"}}}],
    })
    plan = {"id": "physical-local-draft", "task_sha256": file_reference(task_path)["sha256"],
            "pdk_profile_sha256": canonical_sha256(load_pdk_profile(task.pdk_profile).model_dump(mode="json")),
            "process_corner": "top_tt", "temperature_c": 27,
            "axes": [{"name": "branch_current", "unit": "A", "minimum": 1e-5, "maximum": 4e-5},
                     {"name": "gmid", "unit": "1/V", "minimum": 10, "maximum": 20},
                     {"name": "load", "unit": "fF", "minimum": 1, "maximum": 3}],
            "devices": [{"id": "gain-device", "characterization": characterization.name,
                         "characterization_sha256": file_reference(characterization)["sha256"],
                         "characterization_input": input_path.name,
                         "model": "fixture_nmos", "polarity": "nmos", "model_parameters": {"nf": "2"},
                         "length_um": {"value": .03}, "gm_over_id_per_v": {"axis": "gmid"},
                         "current_a": {"axis": "branch_current"}, "vds_magnitude_v": .45, "vsb_magnitude_v": 0,
                         "minimum_headroom_v": .1, "width_binding": _binding("w"), "length_binding": _binding("l"),
                         "total_width_per_cdf_width": 2, "width_convention": "Two fingers; total effective width is twice w",
                         "characterization_total_width_per_normalized_width": 1,
                         "minimum_cdf_width_um": .1, "maximum_cdf_width_um": 3, "width_grid_um": .01}],
            "direct": [{"value": {"axis": "load"}, "testbench": {
                "target": "load", "name": "CL0", "unit": "fF", "meaning": "Explicit output capacitance"}}],
            "points": [{"id": "physical-point", "values": {"branch_current": 2e-5, "gmid": 15, "load": 2}}]}
    plan_path = write(tmp_path / "physical.json", plan)
    return task_path, task, plan_path, characterization


def test_gmid_inversion_uses_existing_interpolator_and_refuses_ambiguous_or_missing_domain():
    artifact = MosCharacterizationArtifact.model_validate(_artifact())
    kwargs = {"point_id": "inverse", "model": "fixture_nmos", "polarity": "nmos", "length_um": .03,
              "gm_over_id_per_v": 15, "vds_magnitude_v": .45, "vsb_magnitude_v": 0}
    result = lookup_mos_by_gm_over_id(artifact, **kwargs)
    assert result.point.vgs_magnitude_v == pytest.approx(.4)
    assert result.point.drain_current_density_a_per_um == pytest.approx(2e-5)
    for changes in ({"length_um": .04}, {"gm_over_id_per_v": 25}, {"vds_magnitude_v": .8}, {"vsb_magnitude_v": .1}):
        with pytest.raises((ValueError, RuntimeError)):
            lookup_mos_by_gm_over_id(artifact, **(kwargs | changes))
    artifact.points[1].gm_over_id_per_v = 20
    with pytest.raises(ValueError, match="ambiguous"):
        lookup_mos_by_gm_over_id(artifact, **kwargs)
    artifact.points[1].gm_over_id_per_v = 10
    artifact.points.append(artifact.points[0].model_copy(update={"id": "duplicate-coordinate"}))
    with pytest.raises(ValueError, match="duplicate bias"):
        lookup_mos_by_gm_over_id(artifact, **kwargs)


def test_physical_coordinates_compile_complete_raw_and_testbench_tuple_without_execution(physical):
    task_path, task, request, characterization = physical
    unchanged = {path: path.read_bytes() for path in (task_path, request, characterization)}
    report, draft = compile_physical_refinement(request, task_path, task)
    assert draft is not None, report
    point = draft.candidate_set.candidates[0]
    assert point.instance_parameters() == {"MN0": {"w": "0.5u", "l": "0.03u"}}
    assert not draft.instance_parameter_updates  # Candidate fields must not overlap global fixed fields.
    assert point.testbench_overrides.loads["CL0"] == pytest.approx(2e-15)
    assert report["points"][0]["calculations"][0]["estimated_gm_after_grid_s"] == pytest.approx(3e-4)
    assert draft.design_context == task.design_context and draft.constraints == task.constraints
    assert draft.objective == task.objective and draft.generic_simulation == task.generic_simulation
    assert not any((draft.safety.allow_remote_compute, draft.safety.allow_remote_write, draft.create_if_missing,
                    draft.safety.replace_existing))
    assert point.predicted_metrics == {}  # Device estimates are not module performance predictions.
    assert draft.candidate_set.source.bindings["request"] == file_reference(request)["sha256"]
    assert all(path.read_bytes() == data for path, data in unchanged.items())


def test_target_gm_and_mirror_ratio_are_explicit_not_guessed(physical):
    task_path, task, path, _ = physical
    raw = json.loads(path.read_text())
    raw["axes"][0] = {"name": "target_gm", "unit": "S", "minimum": 1e-4, "maximum": 1e-3}
    raw["points"][0]["values"].pop("branch_current")
    raw["points"][0]["values"]["target_gm"] = 3e-4
    raw["devices"][0].pop("current_a")
    raw["devices"][0]["gm_s"] = {"axis": "target_gm", "scale": 2}
    write(path, raw)
    report, draft = compile_physical_refinement(path, task_path, task)
    assert draft.candidate_set.candidates[0].instance_parameters()["MN0"]["w"] == "1u"
    assert report["points"][0]["calculations"][0]["requested_current_a"] == pytest.approx(4e-5)


def test_table_normalization_and_target_fingers_are_not_counted_twice(physical):
    task_path, task, path, _ = physical
    raw = json.loads(path.read_text())
    # If the table normalized by per-finger w, its J already incorporates
    # source nf=2. The target also has two fingers: do not divide by nf again.
    raw["devices"][0]["characterization_total_width_per_normalized_width"] = 2
    write(path, raw)
    report, draft = compile_physical_refinement(path, task_path, task)
    assert draft.candidate_set.candidates[0].instance_parameters()["MN0"]["w"] == "1u"
    calculation = report["points"][0]["calculations"][0]
    assert calculation["total_width_um_before_grid"] == pytest.approx(2)
    assert calculation["estimated_current_after_grid_a"] == pytest.approx(2e-5)


@pytest.mark.parametrize("failure", ["hash", "pdk", "corner", "width_convention", "unit", "permission", "overlap", "bounds", "unused_axis", "existing_domain"])
def test_invalid_domains_mappings_and_provenance_never_emit_a_task(physical, failure):
    task_path, task, path, _ = physical
    raw = json.loads(path.read_text())
    if failure == "hash":
        raw["devices"][0]["characterization_sha256"] = "0" * 64
    elif failure == "pdk":
        raw["pdk_profile_sha256"] = "0" * 64
    elif failure == "corner":
        raw["process_corner"] = "ss"
    elif failure == "width_convention":
        raw["devices"][0].pop("total_width_per_cdf_width")
    elif failure == "unit":
        raw["axes"][0]["unit"] = "V"
    elif failure == "permission":
        raw["devices"][0]["width_binding"]["parameter"] = "unknown"
    elif failure == "overlap":
        raw["direct"].append({"value": {"value": .1}, "cdf": _binding("w")})
    elif failure == "bounds":
        raw["points"][0]["values"]["load"] = 4
    elif failure == "unused_axis":
        raw["axes"].append({"name": "unused", "unit": "V", "minimum": 0, "maximum": 1})
        raw["points"][0]["values"]["unused"] = .5
    else:
        task = task.model_copy(update={"instance_parameter_space": [{"instance": "MN0", "parameter": "w", "values": ["1u"]}]})
    write(path, raw)
    with pytest.raises(ValueError):
        compile_physical_refinement(path, task_path, task)


@pytest.mark.parametrize("failure", ["extrapolation", "grid_overflow", "headroom", "no_holdout", "standalone", "uncertainty"])
def test_missing_data_and_physical_failures_are_retained_not_fitted_or_silently_skipped(physical, failure):
    task_path, task, path, characterization = physical
    raw = json.loads(path.read_text())
    if failure == "extrapolation":
        raw["devices"][0]["length_um"]["value"] = .04
    elif failure == "grid_overflow":
        raw["devices"][0].update(width_grid_um=.6, maximum_cdf_width_um=.55)
    elif failure == "headroom":
        raw["devices"][0]["minimum_headroom_v"] = .4
    elif failure == "uncertainty":
        raw["uncertainties"] = ["Unknown bulk bias"]
    else:
        data = json.loads(characterization.read_text())
        if failure == "standalone":
            data = data["actions"][0]["details"]["artifact"]
        else:
            data["actions"][0]["details"]["point_counts"]["holdout"] = 0
        write(characterization, data)
        raw["devices"][0]["characterization_sha256"] = file_reference(characterization)["sha256"]
    write(path, raw)
    report, draft = compile_physical_refinement(path, task_path, task)
    assert draft is None and report["issues"] and report["status"] == "needs_evidence"


def test_intake_reuses_measurement_compiler_and_the_same_physical_compiler(physical, tmp_path):
    task_path, _, plan_path, _ = physical
    measures = write(tmp_path / "measurements.json", {"measurements": [measurement()]})
    request = write(tmp_path / "intake.json", {"source": {"kind": "task", "task": task_path.name},
        "physical_refinement": plan_path.name, "measurements": measures.name,
        "rationale": [{"text": "Use declared physical axes and table bounds, not a claimed optimum.", "source": "software_inference"}]})
    report, draft = build_design_intake(request)
    assert draft is not None, report["issues"]
    assert draft.measurement_plan and report["physical_refinement"]["status"] == "draft_ready"
    assert not report["execution_authorized"]


def test_reasoned_physical_draft_requires_explaining_all_new_fields(physical, tmp_path):
    task_path, _, plan_path, _ = physical
    reason = {"task": task_path.name, "task_sha256": file_reference(task_path)["sha256"],
              "hypotheses": [{"id": "physical-recipe", "observation": "Explicit local fixture only",
                  "mechanism": "Derive total width from current density at the selected gm/ID, then account for fingers.",
                  "evidence": [{"kind": "task", "selector": "/design_context/roles/0"}],
                  "controls": [{"kind": "cdf", "name": "MN0", "parameter": "w"},
                               {"kind": "cdf", "name": "MN0", "parameter": "l"},
                               {"kind": "load", "name": "CL0"}],
                  "falsification": "A future measured OP may not agree with the assumed biases."}]}
    reason_path = write(tmp_path / "reason.json", reason)
    measures = write(tmp_path / "measurements.json", {"measurements": [measurement()]})
    intake_path = write(tmp_path / "intake-reasoned.json", {"source": {"kind": "task", "task": task_path.name},
        "physical_refinement": plan_path.name, "measurements": measures.name, "reasoning": reason_path.name})
    report, draft = build_design_intake(intake_path)
    assert draft is not None, report["issues"]
    reason["hypotheses"][0]["controls"].pop(1)
    write(reason_path, reason)
    report, draft = build_design_intake(intake_path)
    assert draft is None and any("未被设计假设解释" in issue for issue in report["issues"])


@pytest.mark.parametrize("mode", ["changed_bytes", "wrong_model_include"])
def test_characterization_input_must_match_manifest_and_selected_model_not_just_profile_name(physical, mode):
    task_path, task, path, char_path = physical
    plan = json.loads(path.read_text())
    input_path = path.parent / plan["devices"][0]["characterization_input"]
    input_path.write_text('include "/different/metal-stack/toplevel.scs" section=top_tt\n', encoding="utf-8")
    if mode == "wrong_model_include":
        raw = json.loads(char_path.read_text())
        evidence = raw["actions"][1]["details"]["evidence"]
        entry = next(e for e in evidence["artifact_manifest"] if e["path"] == "mos_characterization.scs")
        entry.update(sha256=file_reference(input_path)["sha256"], size_bytes=input_path.stat().st_size)
        digest = canonical_sha256(sorted(evidence["artifact_manifest"], key=lambda entry: entry["path"]))
        evidence["manifest_sha256"] = raw["actions"][0]["details"]["artifact"]["source_artifact_sha256"] = digest
        write(char_path, raw)
        plan["devices"][0]["characterization_sha256"] = file_reference(char_path)["sha256"]
        write(path, plan)
    report, draft = compile_physical_refinement(path, task_path, task)
    assert draft is None
    assert any("model include/section" in issue for issue in report["issues"])
    if mode == "changed_bytes":
        assert any("recorded manifest" in issue for issue in report["issues"])


def test_candidate_budget_is_not_automatically_expanded(physical):
    task_path, task, path, _ = physical
    limited = task.model_dump(mode="json")
    limited["limits"]["max_iterations"] = 1
    task = TaskSpec.model_validate(limited)
    write(task_path, limited)
    raw = json.loads(path.read_text())
    raw["task_sha256"] = file_reference(task_path)["sha256"]
    raw["points"].append({"id": "second", "values": {"branch_current": 3e-5, "gmid": 15, "load": 2}})
    write(path, raw)
    report, draft = compile_physical_refinement(path, task_path, task)
    assert draft is None and any("budget" in issue for issue in report["issues"])


def test_design_next_reuses_physical_compiler_but_failed_execution_does_not_reach_it(physical, tmp_path):
    task_path, task, plan_path, _ = physical
    source = round_record(tmp_path, 1)
    raw = json.loads(Path(source["run"]).read_text(encoding="utf-8"))
    from virtuoso_design_agent.planner import build_plan
    raw.update(task_id=task.id, plan_token=build_plan(task).confirmation_token)
    raw["candidates"][0]["analysis_stages"] = []
    run_path = write(tmp_path / "physical-source-run.json", raw)
    report, draft = suggest_design_next(task_path, run_path, physical_refinement_path=plan_path)
    assert draft is not None and report["status"] == "physical_design_draft_ready"
    raw["status"] = "failed"
    write(run_path, raw)
    report, draft = suggest_design_next(task_path, run_path, physical_refinement_path=tmp_path / "must-not-be-read.json")
    assert draft is None and report["status"] == "reconcile_execution_first"
