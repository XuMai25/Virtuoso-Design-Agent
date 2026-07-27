from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.preview_oa_handoff import (
    build_oa_task_from_preview_shortlist,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _candidate(source_id: str, index: int) -> dict:
    return {
        "index": index,
        "preview_variant_id": f"variant_{index:03d}",
        "source_candidate_id": source_id,
        "preview_feasible": True,
        "reference_feasible": True,
        "preview_objective_value": float(10 - index),
        "reference_objective_value": float(10 - index),
        "preview_rank": float(index),
        "reference_rank": float(index),
        "constraints": [],
        "comparisons": [],
    }


def _selection_payload() -> dict:
    ids = ["seed-a", "seed-b", "seed-c"]
    return {
        "schema_version": 1,
        "policy_id": "preview-selection-1",
        "policy_sha256": "1" * 64,
        "assessment_mode": "prospective_validation",
        "preview_task_id": "preview-task-1",
        "preview_task_sha256": "2" * 64,
        "preview_run_sha256": "3" * 64,
        "reference_task_id": "reference-task-1",
        "reference_run_sha256": "4" * 64,
        "plan_token": "preview-plan-token",
        "status": "succeeded",
        "pdk_profile": "nics4304_tsmc28",
        "analysis": "ac",
        "candidate_generator": "vda.cascode-seed",
        "candidate_source_id": "cascode-source-1",
        "candidate_source_sha256": "a" * 64,
        "declared_candidate_count": 3,
        "evaluated_candidate_count": 3,
        "shortlist_size": 2,
        "shortlist_candidate_ids": ["seed-c", "seed-a"],
        "shortlist_variant_ids": ["variant_003", "variant_001"],
        "preview_winner_candidate_id": "seed-c",
        "reference_winner_candidate_id": "seed-c",
        "winner_agreement": True,
        "reference_winner_in_shortlist": True,
        "spearman_rank_correlation": 1.0,
        "feasibility_agreement_fraction": 1.0,
        "reference_feasible_recall": 1.0,
        "artifact_integrity_gate_passed": True,
        "feasibility_gate_passed": True,
        "rank_correlation_gate_passed": True,
        "winner_retention_gate_passed": True,
        "selection_utility_gate_passed": True,
        "selection_scope": "best_in_declared_discrete_domain",
        "continuous_optimum_claim": False,
        "global_optimum_claim": False,
        "candidates": [
            _candidate(source_id, index)
            for index, source_id in enumerate(ids, start=1)
        ],
        "metric_error_summaries": [],
        "preview_measurement_evidence_source": "eda_result",
        "reference_measurement_evidence_source": "eda_result",
        "selection_evidence_source": "software_inference",
        "policy_evidence_source": "user_input",
        "notes": [],
    }


def _candidate_task_payload() -> dict:
    candidates = [
        {
            "id": "seed-a",
            "parameters": {
                "cascode_width_um": 0.75,
                "cascode_length_um": 0.03,
                "cascode_bias_v": 0.52,
            },
        },
        {
            "id": "seed-b",
            "parameters": {
                "cascode_width_um": 1.0,
                "cascode_length_um": 0.03,
                "cascode_bias_v": 0.53,
            },
        },
        {
            "id": "seed-c",
            "parameters": {
                "cascode_width_um": 1.25,
                "cascode_length_um": 0.03,
                "cascode_bias_v": 0.54,
            },
        },
    ]
    return {
        "schema_version": 1,
        "id": "full-oa-candidate-task",
        "operation": "design.tune",
        "circuit": "common_source",
        "target": {
            "library": "vb_pdk_smoke",
            "cell": "vda_preview_handoff_001",
            "view": "schematic",
        },
        "pdk_profile": "nics4304_tsmc28",
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e12,
            "points_per_decade": 30,
        },
        "parameters": {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 2.0,
        },
        "candidate_set": {
            "source": {
                "generator": "vda.cascode-seed",
                "id": "cascode-source-1",
                "bindings": {
                    "cascode_seed_result_sha256": "a" * 64,
                    "source_run_sha256": "b" * 64,
                },
                "evidence_source": "software_inference",
            },
            "candidates": candidates,
        },
        "constraints": [
            {"metric": "saturation_region", "relation": ">=", "value": 1.0},
            {
                "metric": "bandwidth_3db_hz",
                "relation": ">=",
                "value": 1e6,
            },
        ],
        "objective": {
            "metric": "gain_bandwidth_product_hz",
            "goal": "maximize",
        },
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vb_pdk_smoke",
            "required_cell_prefix": "vda_",
            "replace_existing": False,
        },
        "limits": {"max_iterations": 3, "timeout_seconds": 1800},
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    selection = tmp_path / "selection.json"
    candidate_task = tmp_path / "candidate-task.json"
    _write_json(selection, _selection_payload())
    _write_json(candidate_task, _candidate_task_payload())
    return selection, candidate_task


def test_preview_shortlist_compiles_to_exact_normal_oa_candidate_task(
    tmp_path: Path,
) -> None:
    selection, candidate_task = _write_inputs(tmp_path)

    task = build_oa_task_from_preview_shortlist(
        selection,
        candidate_task,
        task_id="shortlist-oa-task",
    )
    repeated = build_oa_task_from_preview_shortlist(
        selection,
        candidate_task,
        task_id="shortlist-oa-task",
    )
    plan = build_plan(task)

    assert task.model_dump(mode="json") == repeated.model_dump(mode="json")
    assert task.id == "shortlist-oa-task"
    assert task.target is not None
    assert task.target.cell == "vda_preview_handoff_001"
    assert task.expected_target_topology_variant == "cascode_common_source"
    assert "cascode_common_source" in plan.steps[1].description
    assert "最终 topology_variant" in plan.steps[-2].description
    assert plan.requires_remote_compute is True
    assert plan.requires_remote_write is True
    assert task.safety.replace_existing is False
    assert task.candidate_set is not None
    assert task.candidate_set.source.generator == "vda.preview-shortlist"
    assert task.candidate_set.source.id == "preview-selection-1"
    assert [candidate.id for candidate in task.candidate_set.candidates] == [
        "seed-c",
        "seed-a",
    ]
    assert task.limits.max_iterations == 2
    assert task.candidate_set.source.bindings == {
        "cascode_seed_result_sha256": "a" * 64,
        "source_run_sha256": "b" * 64,
        "preview_candidate_source_sha256": "a" * 64,
        "preview_selection_result_sha256": hashlib.sha256(
            selection.read_bytes()
        ).hexdigest(),
        "preview_oa_candidate_task_sha256": hashlib.sha256(
            candidate_task.read_bytes()
        ).hexdigest(),
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda selection, task: selection.update(
                {"selection_utility_gate_passed": False, "status": "partial"}
            ),
            "utility Gate",
        ),
        (
            lambda selection, task: selection.update(
                {
                    "shortlist_size": 0,
                    "shortlist_candidate_ids": [],
                    "shortlist_variant_ids": [],
                    "preview_winner_candidate_id": None,
                }
            ),
            "empty shortlist",
        ),
        (
            lambda selection, task: task["candidate_set"]["source"][
                "bindings"
            ].update({"cascode_seed_result_sha256": "c" * 64}),
            "candidate source SHA-256",
        ),
        (
            lambda selection, task: task["candidate_set"]["candidates"].reverse(),
            "candidate order",
        ),
        (
            lambda selection, task: selection.update({"analysis": "dc"}),
            "analysis mismatch",
        ),
        (
            lambda selection, task: [
                candidate["parameters"].pop("cascode_bias_v")
                for candidate in task["candidate_set"]["candidates"]
            ],
            "require width, length, and bias",
        ),
    ],
)
def test_preview_shortlist_handoff_rejects_drift(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    selection_payload = _selection_payload()
    task_payload = _candidate_task_payload()
    mutate(selection_payload, task_payload)
    selection = tmp_path / "selection.json"
    candidate_task = tmp_path / "candidate-task.json"
    _write_json(selection, selection_payload)
    _write_json(candidate_task, task_payload)

    with pytest.raises(ValueError, match=message):
        build_oa_task_from_preview_shortlist(
            selection,
            candidate_task,
            task_id="shortlist-oa-task",
        )


def test_preview_shortlist_cli_writes_a_runnable_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selection, candidate_task = _write_inputs(tmp_path)
    output = tmp_path / "shortlist-task.json"

    assert (
        main(
            [
                "oa-task-from-preview-shortlist",
                str(selection),
                str(candidate_task),
                "--id",
                "shortlist-oa-task",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    task = TaskSpec.model_validate_json(output.read_text(encoding="utf-8"))
    assert task.candidate_set is not None
    assert [item.id for item in task.candidate_set.candidates] == [
        "seed-c",
        "seed-a",
    ]
    assert "shortlist-oa-task" in capsys.readouterr().out
