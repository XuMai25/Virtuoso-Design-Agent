from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from virtuoso_design_agent.models import (
    CandidateEvaluation,
    EvidenceSource,
    RunRecord,
    RunStatus,
    SearchAudit,
    SelectionScope,
    TaskSpec,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.theory_seed_validation import (
    TheorySeedValidationPolicy,
    validate_theory_seed_run,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "theory-seed-validation-test",
            "operation": "design.tune",
            "circuit": "differential_pair",
            "target": {
                "library": "vda_test",
                "cell": "vda_theory_seed_validation",
            },
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1.0e3, "stop_hz": 1.0e9},
            "parameters": {
                "tail_bias_v": 0.32,
                "common_mode_v": 0.55,
                "vdd_v": 0.9,
                "load_ff": 0.5,
            },
            "theory_seed": {
                "source": {
                    "policy_id": "seed-policy",
                    "policy_sha256": "1" * 64,
                    "theory_request_id": "request",
                    "theory_request_sha256": "2" * 64,
                    "theory_result_sha256": "3" * 64,
                    "device_data_source": "pdk_characterization",
                    "device_data_artifact_id": "device-data",
                    "device_data_artifact_sha256": "4" * 64,
                    "optimality_classification": "bounded_discrete",
                    "declared_theory_combinations": 2,
                    "evaluated_theory_combinations": 2,
                    "theory_domain_exhausted": True,
                    "selection_strategy": "ranked_then_log_maximin",
                },
                "candidates": [
                    {
                        "id": "seed-001",
                        "source_candidate_id": "source-001",
                        "parameters": {
                            "input_width_um": 1.0,
                            "pmos_load_width_um": 1.2,
                            "tail_width_um": 0.6,
                            "length_um": 0.03,
                            "pmos_load_length_um": 0.03,
                            "tail_length_um": 0.03,
                        },
                        "predicted_metrics": {"estimated_power_w": 1.0e-5},
                    },
                    {
                        "id": "seed-002",
                        "source_candidate_id": "source-002",
                        "parameters": {
                            "input_width_um": 1.5,
                            "pmos_load_width_um": 1.5,
                            "tail_width_um": 0.8,
                            "length_um": 0.03,
                            "pmos_load_length_um": 0.03,
                            "tail_length_um": 0.03,
                        },
                        "predicted_metrics": {"estimated_power_w": 1.2e-5},
                    },
                ],
            },
            "constraints": [
                {
                    "metric": "dc_supply_power_uw",
                    "relation": "<=",
                    "value": 20.0,
                }
            ],
            "objective": {"metric": "dc_supply_power_uw", "goal": "minimize"},
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
                "replace_existing": False,
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )


def _run(task: TaskSpec, *, first_power_uw: float = 10.0) -> RunRecord:
    assert task.theory_seed is not None
    candidates = []
    for index, (seed, measured) in enumerate(
        zip(task.theory_seed.candidates, (first_power_uw, 12.0), strict=True),
        start=1,
    ):
        candidates.append(
            CandidateEvaluation(
                index=index,
                parameters=dict(task.parameters) | dict(seed.parameters),
                oa_parameters=dict(seed.parameters),
                metrics={"dc_supply_power_uw": measured},
                constraints=[],
                feasible=True,
                total_violation=0.0,
                objective_value=measured,
                evidence_source=EvidenceSource.EDA_RESULT,
                metric_sources={
                    "dc_supply_power_uw": EvidenceSource.EDA_RESULT
                },
                theory_seed_candidate_id=seed.id,
                theory_seed_source_candidate_id=seed.source_candidate_id,
                theory_seed_predicted_metrics=dict(seed.predicted_metrics),
                theory_seed_evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        )
    started = datetime.now(UTC)
    return RunRecord(
        task_id=task.id,
        plan_token=build_plan(task).confirmation_token,
        adapter="virtuoso-bridge-subprocess",
        status=RunStatus.SUCCEEDED,
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        actions=[],
        candidates=candidates,
        selected_parameters=dict(candidates[0].parameters),
        selected_metrics=dict(candidates[0].metrics),
        search_audit=SearchAudit(
            declared_candidate_count=2,
            attempted_candidate_count=2,
            completed_candidate_count=2,
            domain_exhausted=True,
            selection_scope=SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN,
            statement="test exhausted the exact discrete domain",
            theory_seed_source=task.theory_seed.source,
        ),
    )


def _write_inputs(
    tmp_path: Path,
    *,
    first_power_uw: float = 10.0,
) -> tuple[Path, Path, TaskSpec]:
    task = _task()
    task_path = tmp_path / "task.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    run_path = tmp_path / "run.json"
    run_path.write_text(
        _run(task, first_power_uw=first_power_uw).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return task_path, run_path, task


def _policy(task_path: Path, run_path: Path, task: TaskSpec):
    return TheorySeedValidationPolicy.model_validate(
        {
            "id": "validation-policy",
            "expected_task_id": task.id,
            "expected_task_sha256": _sha256(task_path),
            "expected_run_sha256": _sha256(run_path),
            "minimum_eda_feasible_fraction": 0.5,
            "metric_rules": [
                {
                    "predicted_metric": "estimated_power_w",
                    "measured_metric": "dc_supply_power_uw",
                    "predicted_scale": 1.0e6,
                    "maximum_absolute_error_percent": 10.0,
                }
            ],
        }
    )


def test_theory_seed_validation_passes_exact_bound_metrics(tmp_path: Path) -> None:
    task_path, run_path, task = _write_inputs(tmp_path)

    result = validate_theory_seed_run(
        _policy(task_path, run_path, task),
        task_path,
        run_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.candidate_generation_gate_passed is True
    assert result.prediction_accuracy_gate_passed is True
    assert result.eda_feasible_fraction == 1.0
    assert result.recommendation_agreement is True


def test_theory_seed_validation_keeps_shortlist_pass_separate_from_error_gate(
    tmp_path: Path,
) -> None:
    task_path, run_path, task = _write_inputs(tmp_path, first_power_uw=20.0)

    result = validate_theory_seed_run(
        _policy(task_path, run_path, task),
        task_path,
        run_path,
    )

    assert result.status is RunStatus.PARTIAL
    assert result.candidate_generation_gate_passed is True
    assert result.prediction_accuracy_gate_passed is False
    assert result.candidates[0].comparisons[0].absolute_error_percent == 100.0


def test_theory_seed_validation_rejects_task_hash_drift(tmp_path: Path) -> None:
    task_path, run_path, task = _write_inputs(tmp_path)
    policy = _policy(task_path, run_path, task).model_copy(
        update={"expected_task_sha256": "0" * 64}
    )

    with pytest.raises(ValueError, match="task SHA-256 mismatch"):
        validate_theory_seed_run(policy, task_path, run_path)
