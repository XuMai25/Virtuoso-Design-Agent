from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import RunRecord, RunStatus, TaskSpec
from virtuoso_design_agent.netlist_preview import render_spectre_preview_deck
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.preview_selection import (
    ProspectivePreviewPolicy,
    PreviewSelectionPolicy,
    audit_frozen_preview_shortlist,
    freeze_preview_shortlist,
    validate_preview_selection,
)
from virtuoso_design_agent.profiles import load_pdk_profile


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _manifest_sha256(value: list[dict]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(encoded)


def _task() -> TaskSpec:
    variants = [
        {
            "id": "baseline",
            "output_positive": "OUT",
            "mosfets": [
                {
                    "name": "MN0",
                    "polarity": "nmos",
                    "drain": "OUT",
                    "gate": "IN",
                    "source": "0",
                    "bulk": "0",
                    "width_um": 1.0,
                    "length_um": 0.03,
                }
            ],
        }
    ]
    for index, width in enumerate((0.75, 1.0, 1.25), start=1):
        variants.append(
            {
                "id": f"candidate_{index}",
                "output_positive": "OUT",
                "mosfets": [
                    {
                        "name": "MN0",
                        "polarity": "nmos",
                        "drain": "OUT",
                        "gate": "IN",
                        "source": "0",
                        "bulk": "0",
                        "width_um": width,
                        "length_um": 0.03,
                    }
                ],
            }
        )
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "id": "preview-selection-test",
            "operation": "simulation.run",
            "circuit": "netlist_preview",
            "pdk_profile": "nics4304_tsmc28",
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e9,
                "points_per_decade": 10,
            },
            "netlist_preview": {
                "schema_version": 1,
                "id": "preview-selection-test",
                "input_source": "VIN_SRC",
                "supply_sources": ["VDD_SRC"],
                "voltage_sources": [
                    {
                        "name": "VDD_SRC",
                        "positive": "VDD",
                        "negative": "0",
                        "dc_v": 0.9,
                    },
                    {
                        "name": "VIN_SRC",
                        "positive": "IN",
                        "negative": "0",
                        "dc_v": 0.35,
                        "ac_magnitude_v": 1.0,
                    },
                ],
                "resistors": [
                    {
                        "name": "RLOAD",
                        "positive": "VDD",
                        "negative": "OUT",
                        "resistance_ohm": 20000.0,
                    }
                ],
                "variants": variants,
                "source_bindings": {"candidate_source_sha256": "a" * 64},
                "source_bindings_evidence_source": "software_inference",
                "variant_source_ids": {
                    "candidate_1": "seed-1",
                    "candidate_2": "seed-2",
                    "candidate_3": "seed-3",
                },
                "variant_source_ids_evidence_source": "software_inference",
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": False,
                "replace_existing": False,
            },
        }
    )


def _preview_run(
    task: TaskSpec,
    preview_objectives: tuple[float, float, float],
) -> dict:
    assert task.netlist_preview is not None
    profile = load_pdk_profile(task.pdk_profile).model_dump(mode="json")
    ac_sweep = task.ac_sweep.model_dump(mode="json") if task.ac_sweep else None
    root = "/data/xum/virtuoso_bridge_smoke/vda_preview_selection_test"
    variants: dict[str, dict] = {}
    metrics: dict[str, float] = {}
    sources: dict[str, str] = {}
    for variant_index, variant in enumerate(task.netlist_preview.variants):
        deck = render_spectre_preview_deck(
            task.netlist_preview,
            variant.id,
            profile,
            analysis="ac",
            ac_sweep=ac_sweep,
        )
        deck_hash = _sha256_bytes(deck.encode())
        manifest = [
            {
                "path": f"preview_{variant.id}.scs",
                "size_bytes": len(deck.encode()),
                "sha256": deck_hash,
            }
        ]
        for artifact_index in range(7):
            payload = f"{variant.id}-{artifact_index}".encode()
            manifest.append(
                {
                    "path": f"preview_{variant.id}.raw/file_{artifact_index}",
                    "size_bytes": len(payload),
                    "sha256": _sha256_bytes(payload),
                }
            )
        variants[variant.id] = {
            "analysis_complete": True,
            "analysis_issues": [],
            "analysis_warnings": [],
            "metrics": {},
            "operating_point": {},
            "ac_response": {
                "analysis_complete": True,
                "issues": [],
                "warnings": [],
                "sample_count": 51,
            },
            "deck_sha256": deck_hash,
            "artifact_manifest": manifest,
            "manifest_sha256": _manifest_sha256(manifest),
            "remote_run_root": f"{root}/{variant.id}",
            "remote_simulation_dir": f"{root}/{variant.id}/run-1",
            "process_lifecycle": {"bounded_remote_process": True},
        }
        if variant_index == 0:
            continue
        values = {
            "all_mos_saturation_region": 1.0,
            "low_frequency_gain_v_per_v": 4.0 + variant_index,
            "gain_bandwidth_product_hz": preview_objectives[variant_index - 1],
        }
        for name, value in values.items():
            full_name = f"{variant.id}__{name}"
            metrics[full_name] = value
            sources[full_name] = (
                "software_inference"
                if name == "all_mos_saturation_region"
                else "eda_result"
            )
    evidence = {
        "source": "eda_result",
        "preview_spec_sha256": task.netlist_preview.canonical_sha256(),
        "preview_spec_source": "user_input",
        "source_bindings": task.netlist_preview.source_bindings,
        "source_bindings_evidence_source": "software_inference",
        "variant_source_ids": task.netlist_preview.variant_source_ids,
        "variant_source_ids_evidence_source": "software_inference",
        "analysis": "ac",
        "analysis_source": "user_input",
        "pdk_profile": task.pdk_profile,
        "non_overwrite_preflight": "absent",
        "remote_run_root": root,
        "variants": variants,
    }
    return {
        "schema_version": 1,
        "task_id": task.id,
        "plan_token": build_plan(task).confirmation_token,
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": "2026-07-27T00:00:00Z",
        "finished_at": "2026-07-27T00:01:00Z",
        "actions": [
            {
                "action": "bridge.spectre.probe",
                "status": "succeeded",
                "started_at": "2026-07-27T00:00:00Z",
                "finished_at": "2026-07-27T00:00:01Z",
                "evidence_source": "bridge_readback",
                "details": {
                    "connected": True,
                    "profile": task.pdk_profile,
                    "virtuoso_started": False,
                    "oa_access_performed": False,
                    "oa_write_performed": False,
                },
            },
            {
                "action": "simulation.candidate.1",
                "status": "succeeded",
                "started_at": "2026-07-27T00:00:01Z",
                "finished_at": "2026-07-27T00:01:00Z",
                "evidence_source": "eda_result",
                "details": {
                    "parameters": {},
                    "metrics": metrics,
                    "metric_sources": sources,
                    "analysis_complete": True,
                    "analysis_issues": [],
                    "analysis_warnings": [],
                    "evidence": evidence,
                },
            },
        ],
        "candidates": [
            {
                "index": 1,
                "parameters": {},
                "metrics": metrics,
                "constraints": [],
                "feasible": True,
                "total_violation": 0.0,
                "evidence_source": "eda_result",
                "metric_sources": sources,
                "analysis_complete": True,
                "analysis_issues": [],
                "analysis_warnings": [],
            }
        ],
        "notes": [],
    }


def _reference_run() -> dict:
    values = (29.0, 22.0, 42.0)
    candidates = []
    for index, value in enumerate(values, start=1):
        candidates.append(
            {
                "index": index,
                "parameters": {"device_width_um": float(index)},
                "metrics": {
                    "low_frequency_gain_v_per_v": 4.0 + index,
                    "gain_bandwidth_product_hz": value,
                },
                "constraints": [],
                "feasible": True,
                "total_violation": 0.0,
                "objective_value": value,
                "evidence_source": "eda_result",
                "metric_sources": {
                    "low_frequency_gain_v_per_v": "eda_result",
                    "gain_bandwidth_product_hz": "eda_result",
                },
                "analysis_complete": True,
                "analysis_issues": [],
                "analysis_warnings": [],
                "atomic_candidate_id": f"seed-{index}",
                "atomic_candidate_evidence_source": "software_inference",
            }
        )
    return {
        "schema_version": 1,
        "task_id": "reference-selection-test",
        "plan_token": "reference-token",
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": "2026-07-26T00:00:00Z",
        "finished_at": "2026-07-26T00:03:00Z",
        "actions": [],
        "candidates": candidates,
        "selected_parameters": {"device_width_um": 3.0},
        "selected_metrics": candidates[2]["metrics"],
        "search_audit": {
            "declared_candidate_count": 3,
            "attempted_candidate_count": 3,
            "completed_candidate_count": 3,
            "domain_exhausted": True,
            "selection_scope": "best_in_declared_discrete_domain",
            "continuous_optimum_claim": False,
            "global_optimum_claim": False,
            "statement": "Best feasible candidate in the declared domain.",
            "candidate_set_source": {
                "generator": "vda.test-seed",
                "id": "test-source-1",
                "bindings": {"candidate_source_sha256": "a" * 64},
                "evidence_source": "software_inference",
            },
        },
        "notes": [],
    }


def _reference_task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "id": "reference-selection-test",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {
                "library": "vda_test",
                "cell": "vda_preview_reference",
                "view": "schematic",
            },
            "pdk_profile": "nics4304_tsmc28",
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e9,
                "points_per_decade": 10,
            },
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 1.0,
            },
            "candidate_set": {
                "source": {
                    "generator": "vda.test-seed",
                    "id": "test-source-1",
                    "bindings": {"candidate_source_sha256": "a" * 64},
                    "evidence_source": "software_inference",
                },
                "candidates": [
                    {
                        "id": f"seed-{index}",
                        "parameters": {"device_width_um": float(index)},
                    }
                    for index in range(1, 4)
                ],
            },
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                }
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
                "replace_existing": False,
            },
            "limits": {"max_iterations": 3, "timeout_seconds": 1800},
        }
    )


def _write_model(path: Path, model) -> None:
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _fixture(
    tmp_path: Path,
    *,
    preview_objectives: tuple[float, float, float] = (30.0, 20.0, 40.0),
    mutate_preview: Callable[[dict], None] | None = None,
    mutate_reference: Callable[[dict], None] | None = None,
) -> tuple[PreviewSelectionPolicy, Path, Path, Path, Path]:
    task = _task()
    task_path = tmp_path / "task.json"
    _write_model(task_path, task)

    preview_payload = _preview_run(task, preview_objectives)
    reference_payload = _reference_run()
    if mutate_preview is not None:
        mutate_preview(preview_payload)
    if mutate_reference is not None:
        mutate_reference(reference_payload)
    preview_path = tmp_path / "preview.json"
    reference_path = tmp_path / "reference.json"
    _write_model(preview_path, RunRecord.model_validate(preview_payload))
    _write_model(reference_path, RunRecord.model_validate(reference_payload))

    policy_payload = {
        "schema_version": 1,
        "id": "preview-selection-test",
        "assessment_mode": "prospective_validation",
        "expected_preview_task_id": task.id,
        "expected_preview_task_sha256": _file_sha256(task_path),
        "expected_preview_run_sha256": _file_sha256(preview_path),
        "expected_reference_task_id": "reference-selection-test",
        "expected_reference_run_sha256": _file_sha256(reference_path),
        "expected_analysis": "ac",
        "expected_pdk_profile": task.pdk_profile,
        "expected_candidate_generator": "vda.test-seed",
        "expected_candidate_source_id": "test-source-1",
        "candidate_source_sha256": "a" * 64,
        "reference_candidate_source_binding": "candidate_source_sha256",
        "constraints": [
            {
                "metric": "all_mos_saturation_region",
                "relation": ">=",
                "value": 1.0,
                "expected_evidence_source": "software_inference",
            },
            {
                "metric": "low_frequency_gain_v_per_v",
                "relation": ">=",
                "value": 2.0,
                "expected_evidence_source": "eda_result",
            },
        ],
        "objective": {
            "preview_metric": "gain_bandwidth_product_hz",
            "reference_metric": "gain_bandwidth_product_hz",
            "goal": "maximize",
        },
        "comparison_metrics": [
            {
                "preview_metric": "gain_bandwidth_product_hz",
                "reference_metric": "gain_bandwidth_product_hz",
            }
        ],
        "shortlist_size": 2,
        "minimum_artifact_count_per_variant": 8,
        "minimum_ac_sample_count": 51,
        "minimum_feasibility_agreement": 1.0,
        "minimum_reference_feasible_recall": 1.0,
        "minimum_spearman_rank_correlation": 0.7,
        "require_reference_winner_in_shortlist": True,
        "require_preview_winner_match": False,
        "evidence_source": "user_input",
    }
    policy = PreviewSelectionPolicy.model_validate(policy_payload)
    policy_path = tmp_path / "policy.json"
    _write_model(policy_path, policy)
    return policy, policy_path, task_path, preview_path, reference_path


def _prospective_fixture(
    tmp_path: Path,
) -> tuple[
    ProspectivePreviewPolicy,
    Path,
    Path,
    Path,
    Path,
    Path,
]:
    bound, _, preview_task, preview_run, reference_run = _fixture(tmp_path)
    reference_task = tmp_path / "reference-task.json"
    reference_task_model = _reference_task()
    _write_model(reference_task, reference_task_model)

    reference_payload = json.loads(reference_run.read_text(encoding="utf-8"))
    reference_payload["plan_token"] = build_plan(
        reference_task_model
    ).confirmation_token
    reference_payload["started_at"] = "2099-01-01T00:00:00Z"
    reference_payload["finished_at"] = "2099-01-01T00:03:00Z"
    _write_model(reference_run, RunRecord.model_validate(reference_payload))

    policy_payload = bound.model_dump(mode="json")
    policy_payload.pop("expected_preview_run_sha256")
    policy_payload.pop("expected_reference_run_sha256")
    policy_payload["expected_reference_task_sha256"] = _file_sha256(
        reference_task
    )
    policy = ProspectivePreviewPolicy.model_validate(policy_payload)
    policy_path = tmp_path / "prospective-policy.json"
    _write_model(policy_path, policy)
    return (
        policy,
        policy_path,
        preview_task,
        preview_run,
        reference_task,
        reference_run,
    )


def test_preview_selection_verifies_integrity_and_retains_reference_winner(
    tmp_path: Path,
) -> None:
    policy, _, task, preview, reference = _fixture(tmp_path)

    result = validate_preview_selection(policy, task, preview, reference)

    assert result.status is RunStatus.SUCCEEDED
    assert result.shortlist_candidate_ids == ["seed-3", "seed-1"]
    assert result.preview_winner_candidate_id == "seed-3"
    assert result.reference_winner_candidate_id == "seed-3"
    assert result.reference_winner_in_shortlist is True
    assert result.spearman_rank_correlation == pytest.approx(1.0)
    assert result.artifact_integrity_gate_passed is True
    assert result.selection_scope.value == "best_in_declared_discrete_domain"
    assert result.global_optimum_claim is False


def test_preview_selection_accepts_scoped_parallel_batch_evidence(
    tmp_path: Path,
) -> None:
    policy, _, task_path, preview_path, reference_path = _fixture(tmp_path)
    raw = json.loads(preview_path.read_text(encoding="utf-8"))
    evidence = raw["actions"][1]["details"]["evidence"]
    variants = evidence["variants"]
    remote_root = evidence["remote_run_root"]
    evidence["batch_execution"] = {
        "source": "software_inference",
        "api": "SpectreSimulator.run_parallel",
        "submission_count": len(variants),
        "max_workers": 4,
        "result_binding": "submission_order_plus_remote_deck_inventory",
        "simulator_instance_count": 1,
        "guard_installation_count": 1,
        "remote_inventory_command_count": 1,
        "executor_lifecycle": "scoped_context_manager",
    }
    for index, (variant_id, variant) in enumerate(variants.items(), start=1):
        simulation_dir = f"{remote_root}/{index:08x}"
        variant["remote_run_root"] = remote_root
        variant["remote_simulation_dir"] = simulation_dir
        variant["remote_deck_path"] = f"{simulation_dir}/preview_{variant_id}.scs"
    _write_model(preview_path, RunRecord.model_validate(raw))
    rebound_policy = policy.model_copy(
        update={"expected_preview_run_sha256": _file_sha256(preview_path)}
    )

    result = validate_preview_selection(
        rebound_policy,
        task_path,
        preview_path,
        reference_path,
    )

    assert result.status is RunStatus.SUCCEEDED

    invalid_count = json.loads(preview_path.read_text(encoding="utf-8"))
    invalid_count["actions"][1]["details"]["evidence"]["batch_execution"][
        "simulator_instance_count"
    ] = True
    _write_model(preview_path, RunRecord.model_validate(invalid_count))
    invalid_count_policy = policy.model_copy(
        update={"expected_preview_run_sha256": _file_sha256(preview_path)}
    )
    with pytest.raises(ValueError, match="batch execution evidence is invalid"):
        validate_preview_selection(
            invalid_count_policy,
            task_path,
            preview_path,
            reference_path,
        )

    _write_model(preview_path, RunRecord.model_validate(raw))
    tampered = json.loads(preview_path.read_text(encoding="utf-8"))
    tampered_variants = tampered["actions"][1]["details"]["evidence"]["variants"]
    first_id, second_id = list(tampered_variants)[:2]
    duplicate_dir = tampered_variants[first_id]["remote_simulation_dir"]
    tampered_variants[second_id]["remote_simulation_dir"] = duplicate_dir
    tampered_variants[second_id]["remote_deck_path"] = (
        f"{duplicate_dir}/preview_{second_id}.scs"
    )
    _write_model(preview_path, RunRecord.model_validate(tampered))
    tampered_policy = policy.model_copy(
        update={"expected_preview_run_sha256": _file_sha256(preview_path)}
    )
    with pytest.raises(ValueError, match="remote path mismatch"):
        validate_preview_selection(
            tampered_policy,
            task_path,
            preview_path,
            reference_path,
        )


@pytest.mark.parametrize(
    ("mutate_preview", "mutate_reference", "message"),
    [
        (
            lambda run: run["actions"][1]["details"]["evidence"]["variants"][
                "candidate_1"
            ].update({"deck_sha256": "f" * 64}),
            None,
            "rendered deck hash mismatch",
        ),
        (
            lambda run: run["actions"][1]["details"]["metric_sources"].update(
                {"candidate_1__gain_bandwidth_product_hz": "bridge_readback"}
            )
            or run["candidates"][0]["metric_sources"].update(
                {"candidate_1__gain_bandwidth_product_hz": "bridge_readback"}
            ),
            None,
            "evidence source mismatch",
        ),
        (
            lambda run: run["actions"][1]["details"]["evidence"]["variants"][
                "candidate_1"
            ]["ac_response"].update({"sample_count": 1}),
            None,
            "incomplete AC waveform",
        ),
        (
            None,
            lambda run: run["search_audit"].update({"domain_exhausted": False}),
            "did not exhaust",
        ),
        (
            None,
            lambda run: run["search_audit"]["candidate_set_source"][
                "bindings"
            ].update({"candidate_source_sha256": "b" * 64}),
            "SHA-256 binding mismatch",
        ),
    ],
)
def test_preview_selection_rejects_broken_evidence_boundaries(
    tmp_path: Path,
    mutate_preview,
    mutate_reference,
    message: str,
) -> None:
    policy, _, task, preview, reference = _fixture(
        tmp_path,
        mutate_preview=mutate_preview,
        mutate_reference=mutate_reference,
    )

    with pytest.raises(ValueError, match=message):
        validate_preview_selection(policy, task, preview, reference)


def test_preview_selection_reports_partial_when_ranking_drops_real_winner(
    tmp_path: Path,
) -> None:
    policy, _, task, preview, reference = _fixture(
        tmp_path,
        preview_objectives=(40.0, 30.0, 20.0),
    )

    result = validate_preview_selection(policy, task, preview, reference)

    assert result.status is RunStatus.PARTIAL
    assert result.shortlist_candidate_ids == ["seed-1", "seed-2"]
    assert result.reference_winner_candidate_id == "seed-3"
    assert result.reference_winner_in_shortlist is False
    assert result.rank_correlation_gate_passed is False
    assert result.winner_retention_gate_passed is False


def test_preview_selection_reports_partial_for_empty_coarse_shortlist(
    tmp_path: Path,
) -> None:
    def reject_all(run: dict) -> None:
        metrics = run["actions"][1]["details"]["metrics"]
        for index in range(1, 4):
            metrics[f"candidate_{index}__all_mos_saturation_region"] = 0.0

    policy, _, task, preview, reference = _fixture(
        tmp_path,
        mutate_preview=reject_all,
    )

    result = validate_preview_selection(policy, task, preview, reference)

    assert result.status is RunStatus.PARTIAL
    assert result.shortlist_candidate_ids == []
    assert result.preview_winner_candidate_id is None
    assert result.feasibility_gate_passed is False
    assert result.winner_retention_gate_passed is False


def test_preview_selection_rejects_file_drift_after_policy_binding(
    tmp_path: Path,
) -> None:
    policy, _, task, preview, reference = _fixture(tmp_path)
    preview.write_text(preview.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="run SHA-256 mismatch"):
        validate_preview_selection(policy, task, preview, reference)


def test_preview_selection_cli_writes_result(tmp_path: Path, capsys) -> None:
    _, policy, task, preview, reference = _fixture(tmp_path)
    output = tmp_path / "selection.json"

    assert (
        main(
            [
                "preview-select",
                str(policy),
                str(task),
                str(preview),
                str(reference),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert '"selection_utility_gate_passed": true' in capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "succeeded"


def test_prospective_shortlist_is_frozen_before_reference_truth(
    tmp_path: Path,
) -> None:
    (
        policy,
        _,
        preview_task,
        preview_run,
        reference_task,
        reference_run,
    ) = _prospective_fixture(tmp_path)
    frozen_at = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)

    shortlist = freeze_preview_shortlist(
        policy,
        preview_task,
        preview_run,
        reference_task,
        _frozen_at=frozen_at,
    )
    shortlist_path = tmp_path / "shortlist.json"
    _write_model(shortlist_path, shortlist)
    result = audit_frozen_preview_shortlist(
        policy,
        shortlist_path,
        preview_task,
        preview_run,
        reference_task,
        reference_run,
    )

    assert shortlist.status is RunStatus.SUCCEEDED
    assert shortlist.shortlist_candidate_ids == ["seed-3", "seed-1"]
    assert shortlist.expected_reference_task_sha256 == _file_sha256(reference_task)
    assert not hasattr(shortlist, "reference_run_sha256")
    assert result.status is RunStatus.SUCCEEDED
    assert result.reference_winner_in_shortlist is True
    assert result.prospective_policy_sha256 == shortlist.policy_sha256
    assert result.frozen_shortlist_sha256 == _file_sha256(shortlist_path)
    assert result.reference_task_sha256 == _file_sha256(reference_task)


def test_prospective_audit_rejects_shortlist_changed_after_freeze(
    tmp_path: Path,
) -> None:
    policy, _, preview_task, preview_run, reference_task, reference_run = (
        _prospective_fixture(tmp_path)
    )
    shortlist = freeze_preview_shortlist(
        policy,
        preview_task,
        preview_run,
        reference_task,
        _frozen_at=datetime(2026, 7, 27, 1, 0, tzinfo=UTC),
    )
    raw = shortlist.model_dump(mode="json")
    raw["shortlist_candidate_ids"].reverse()
    shortlist_path = tmp_path / "shortlist-drifted.json"
    _write_model(shortlist_path, type(shortlist).model_validate(raw))

    with pytest.raises(ValueError, match="shortlist content drifted"):
        audit_frozen_preview_shortlist(
            policy,
            shortlist_path,
            preview_task,
            preview_run,
            reference_task,
            reference_run,
        )


def test_prospective_audit_rejects_reference_that_predates_shortlist(
    tmp_path: Path,
) -> None:
    policy, _, preview_task, preview_run, reference_task, reference_run = (
        _prospective_fixture(tmp_path)
    )
    shortlist = freeze_preview_shortlist(
        policy,
        preview_task,
        preview_run,
        reference_task,
        _frozen_at=datetime(2026, 7, 27, 1, 0, tzinfo=UTC),
    )
    shortlist_path = tmp_path / "shortlist.json"
    _write_model(shortlist_path, shortlist)
    raw = json.loads(reference_run.read_text(encoding="utf-8"))
    raw["started_at"] = "2026-07-27T00:30:00Z"
    raw["finished_at"] = "2026-07-27T00:45:00Z"
    _write_model(reference_run, RunRecord.model_validate(raw))

    with pytest.raises(ValueError, match="predates"):
        audit_frozen_preview_shortlist(
            policy,
            shortlist_path,
            preview_task,
            preview_run,
            reference_task,
            reference_run,
        )


def test_prospective_audit_requires_reference_to_start_after_shortlist(
    tmp_path: Path,
) -> None:
    policy, _, preview_task, preview_run, reference_task, reference_run = (
        _prospective_fixture(tmp_path)
    )
    frozen_at = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    shortlist = freeze_preview_shortlist(
        policy,
        preview_task,
        preview_run,
        reference_task,
        _frozen_at=frozen_at,
    )
    shortlist_path = tmp_path / "shortlist.json"
    _write_model(shortlist_path, shortlist)
    raw = json.loads(reference_run.read_text(encoding="utf-8"))
    raw["started_at"] = frozen_at.isoformat()
    raw["finished_at"] = "2026-07-27T01:30:00Z"
    _write_model(reference_run, RunRecord.model_validate(raw))

    with pytest.raises(ValueError, match="predates"):
        audit_frozen_preview_shortlist(
            policy,
            shortlist_path,
            preview_task,
            preview_run,
            reference_task,
            reference_run,
        )


def test_prospective_shortlist_cli_is_two_phase(tmp_path: Path, capsys) -> None:
    (
        _,
        policy,
        preview_task,
        preview_run,
        reference_task,
        reference_run,
    ) = _prospective_fixture(tmp_path)
    shortlist = tmp_path / "cli-shortlist.json"
    audit = tmp_path / "cli-audit.json"

    assert main(
        [
            "preview-shortlist",
            str(policy),
            str(preview_task),
            str(preview_run),
            str(reference_task),
            "--output",
            str(shortlist),
        ]
    ) == 0
    assert main(
        [
            "preview-shortlist-audit",
            str(policy),
            str(shortlist),
            str(preview_task),
            str(preview_run),
            str(reference_task),
            str(reference_run),
            "--output",
            str(audit),
        ]
    ) == 0
    assert json.loads(shortlist.read_text(encoding="utf-8"))[
        "shortlist_generation_gate_passed"
    ] is True
    assert json.loads(audit.read_text(encoding="utf-8"))[
        "reference_winner_in_shortlist"
    ] is True
    assert "OA reference run" in capsys.readouterr().out
