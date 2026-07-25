from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import (
    CandidateEvaluation,
    EvidenceSource,
    RunRecord,
    RunStatus,
    SearchAudit,
    SelectionScope,
    TaskSpec,
)
from virtuoso_design_agent.op_relinearization import (
    OperatingPointRelinearizationPolicy,
    build_task_from_relinearization,
    relinearize_operating_point,
    validate_relinearization_run,
)
from virtuoso_design_agent.planner import build_plan


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source_run(
    tmp_path: Path,
    *,
    points: list[tuple[float, float]] | None = None,
    holdout_gain_offset: float = 0.0,
    gain_offsets: dict[int, float] | None = None,
    vary_unmodeled_holdout: bool = False,
    adapter: str = "virtuoso-bridge-subprocess",
    metric_evidence_source: EvidenceSource = EvidenceSource.EDA_RESULT,
) -> Path:
    normalized_points = points or [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (-1.0, -1.0)]
    candidates: list[CandidateEvaluation] = []
    for index, (width_step, bias_step) in enumerate(normalized_points, start=1):
        gain = 5.0 + 0.5 * width_step - 0.2 * bias_step
        if index == 4:
            gain += holdout_gain_offset
        gain += (gain_offsets or {}).get(index, 0.0)
        parameters = {
            "device_width_um": 1.0 + 0.1 * width_step,
            "bias_v": 0.30 + 0.05 * bias_step,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "vdd_v": 0.9,
            "load_ff": 2.0 if vary_unmodeled_holdout and index == 4 else 1.0,
        }
        metrics = {
            "gm_us": 100.0 + 10.0 * width_step + 20.0 * bias_step,
            "low_frequency_gain_v_per_v": gain,
        }
        candidates.append(
            CandidateEvaluation(
                index=index,
                parameters=parameters,
                metrics=metrics,
                constraints=[],
                feasible=True,
                total_violation=0.0,
                objective_value=gain,
                evidence_source=EvidenceSource.EDA_RESULT,
                metric_sources={
                    name: metric_evidence_source for name in metrics
                },
                analysis_complete=True,
            )
        )
    now = datetime.now(UTC)
    run = RunRecord(
        task_id="synthetic-real-local-response",
        plan_token="test-plan-token",
        adapter=adapter,
        status=RunStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        actions=[],
        candidates=candidates,
    )
    path = tmp_path / "source-run.json"
    path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _policy(source_run: Path) -> OperatingPointRelinearizationPolicy:
    return OperatingPointRelinearizationPolicy.model_validate(
        {
            "id": "synthetic-local-response",
            "expected_source_task_id": "synthetic-real-local-response",
            "expected_source_run_sha256": _sha256(source_run),
            "anchor_candidate_index": 1,
            "training_candidate_indices": [1, 2, 3],
            "holdout_candidate_indices": [4],
            "parameters": [
                {
                    "name": "device_width_um",
                    "minimum": 0.8,
                    "maximum": 1.2,
                    "proposal_step": 0.1,
                },
                {
                    "name": "bias_v",
                    "minimum": 0.2,
                    "maximum": 0.4,
                    "proposal_step": 0.05,
                },
            ],
            "metrics": [
                {
                    "metric": "gm_us",
                    "role": "operating_point",
                    "maximum_holdout_error_percent": 0.001,
                },
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "role": "performance",
                    "maximum_holdout_error_percent": 0.001,
                },
            ],
            "constraints": [
                {"metric": "gm_us", "relation": ">=", "value": 70.0},
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 4.0,
                },
            ],
            "objective": {
                "metric": "low_frequency_gain_v_per_v",
                "goal": "maximize",
            },
            "maximum_candidates": 4,
        }
    )


def _write_template(tmp_path: Path, policy: OperatingPointRelinearizationPolicy) -> Path:
    template = {
        "schema_version": 1,
        "id": "synthetic-local-response-next-eda",
        "operation": "design.tune",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "analysis": "ac",
        "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e11},
        "parameters": {
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "constraints": [
            *[item.model_dump(mode="json") for item in policy.constraints],
            {"metric": "saturation_region", "relation": ">=", "value": 1.0},
        ],
        "objective": policy.objective.model_dump(mode="json"),
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
        "limits": {"max_iterations": 4, "timeout_seconds": 600},
    }
    path = tmp_path / "task-template.json"
    path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return path


def _write_compiled_relinearization(
    tmp_path: Path,
) -> tuple[Path, Path, TaskSpec]:
    source_run = _write_source_run(tmp_path)
    policy = _policy(source_run)
    result = relinearize_operating_point(policy, source_run)
    result_path = tmp_path / "relinearization-result.json"
    result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    task = build_task_from_relinearization(
        result_path,
        _write_template(tmp_path, policy),
    )
    task_path = tmp_path / "relinearization-task.json"
    task_path.write_text(
        task.model_dump_json(indent=2, exclude_none=True, exclude_unset=True) + "\n",
        encoding="utf-8",
    )
    return result_path, task_path, task


def _write_atomic_eda_run(
    tmp_path: Path,
    task: TaskSpec,
    *,
    metric_offsets: dict[tuple[int, str], float] | None = None,
    adapter: str = "virtuoso-bridge-subprocess",
) -> Path:
    assert task.candidate_set is not None
    assert task.objective is not None
    evaluations: list[CandidateEvaluation] = []
    for index, atomic in enumerate(task.candidate_set.candidates, start=1):
        parameters = dict(task.parameters)
        parameters.update(atomic.parameters)
        instance_parameters = {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }
        for update in atomic.instance_parameter_updates:
            instance_parameters.setdefault(update.instance, {}).update(
                update.parameters
            )
        metrics = dict(atomic.predicted_metrics)
        metrics["saturation_region"] = 1.0
        for (candidate_index, metric), offset in (metric_offsets or {}).items():
            if candidate_index == index:
                metrics[metric] += offset
        constraints = evaluate_constraints(metrics, task.constraints)
        feasible = all(item.passed for item in constraints)
        evaluations.append(
            CandidateEvaluation(
                index=index,
                parameters=parameters,
                instance_parameters=instance_parameters,
                metrics=metrics,
                constraints=constraints,
                feasible=feasible,
                total_violation=sum(
                    item.normalized_violation for item in constraints
                ),
                objective_value=metrics[task.objective.metric],
                evidence_source=EvidenceSource.EDA_RESULT,
                metric_sources={
                    name: (
                        EvidenceSource.SOFTWARE_INFERENCE
                        if name == "saturation_region"
                        else EvidenceSource.EDA_RESULT
                    )
                    for name in metrics
                },
                analysis_complete=True,
                atomic_candidate_id=atomic.id,
                atomic_candidate_predicted_metrics=dict(atomic.predicted_metrics),
                atomic_candidate_evidence_source=(
                    EvidenceSource.SOFTWARE_INFERENCE
                ),
            )
        )
    selected = max(
        (candidate for candidate in evaluations if candidate.feasible),
        key=lambda candidate: candidate.objective_value,
    )
    now = datetime.now(UTC)
    run = RunRecord(
        task_id=task.id,
        plan_token=build_plan(task).confirmation_token,
        adapter=adapter,
        status=RunStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        actions=[],
        candidates=evaluations,
        selected_parameters=dict(selected.parameters),
        selected_instance_parameters=(
            dict(selected.instance_parameters)
            if selected.instance_parameters
            else None
        ),
        selected_metrics=dict(selected.metrics),
        search_audit=SearchAudit(
            declared_candidate_count=len(evaluations),
            attempted_candidate_count=len(evaluations),
            completed_candidate_count=len(evaluations),
            domain_exhausted=True,
            selection_scope=SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN,
            statement="test exhausted atomic domain",
            candidate_set_source=task.candidate_set.source,
        ),
    )
    path = tmp_path / "atomic-eda-run.json"
    path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def test_heldout_validated_local_model_emits_hash_bound_atomic_task(
    tmp_path: Path,
) -> None:
    source_run = _write_source_run(tmp_path)
    policy = _policy(source_run)

    result = relinearize_operating_point(policy, source_run)

    assert result.status is RunStatus.SUCCEEDED
    assert result.gate_passed
    assert result.candidate_set is not None
    assert result.candidate_set.source.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    assert result.candidate_set.candidates[0].parameters == {
        "device_width_um": pytest.approx(1.0),
        "bias_v": pytest.approx(0.30),
    }
    assert all(
        model.training_gate_passed and model.holdout_gate_passed
        for model in result.models
    )
    assert result.holdout_parameter_coverage == {
        "device_width_um": True,
        "bias_v": True,
    }
    assert all(
        comparison.actual_evidence_source is EvidenceSource.EDA_RESULT
        and comparison.predicted_evidence_source is EvidenceSource.SOFTWARE_INFERENCE
        for comparison in result.holdout_comparisons
    )

    result_path = tmp_path / "relinearization.json"
    result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    template_path = _write_template(tmp_path, policy)
    task = build_task_from_relinearization(result_path, template_path)

    assert isinstance(task, TaskSpec)
    assert task.candidate_set is not None
    assert len(task.candidate_set.candidates) == 4
    assert task.parameter_space == {}
    assert task.instance_parameter_space == []
    assert "relinearization_result_sha256" in task.candidate_set.source.bindings
    assert "task_template_sha256" in task.candidate_set.source.bindings
    assert {item.metric for item in task.constraints} == {
        "gm_us",
        "low_frequency_gain_v_per_v",
        "saturation_region",
    }

    incomplete_template = json.loads(template_path.read_text(encoding="utf-8"))
    incomplete_template["constraints"] = incomplete_template["constraints"][1:]
    incomplete_path = tmp_path / "incomplete-template.json"
    incomplete_path.write_text(
        json.dumps(incomplete_template, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="omits or changes"):
        build_task_from_relinearization(result_path, incomplete_path)


def test_holdout_failure_is_partial_and_cannot_compile_a_task(tmp_path: Path) -> None:
    source_run = _write_source_run(tmp_path, holdout_gain_offset=10.0)
    result = relinearize_operating_point(_policy(source_run), source_run)

    assert result.status is RunStatus.PARTIAL
    assert not result.gate_passed
    assert result.candidate_set is None
    assert any("held-out error gate failed" in note for note in result.notes)

    result_path = tmp_path / "failed-result.json"
    result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="only a passed"):
        build_task_from_relinearization(
            result_path,
            _write_template(tmp_path, _policy(source_run)),
        )


def test_relinearization_rejects_non_real_or_unmodeled_changing_inputs(
    tmp_path: Path,
) -> None:
    demo = _write_source_run(tmp_path, adapter="deterministic-demo")
    with pytest.raises(ValueError, match="real-Bridge"):
        relinearize_operating_point(_policy(demo), demo)

    changing = _write_source_run(tmp_path, vary_unmodeled_holdout=True)
    with pytest.raises(ValueError, match="unmodeled parameter 'load_ff' varies"):
        relinearize_operating_point(_policy(changing), changing)

    inferred_metric = _write_source_run(
        tmp_path,
        metric_evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
    )
    with pytest.raises(ValueError, match="metric gm_us is not eda_result"):
        relinearize_operating_point(_policy(inferred_metric), inferred_metric)


def test_relinearization_refuses_singular_training_perturbations(
    tmp_path: Path,
) -> None:
    source_run = _write_source_run(
        tmp_path,
        points=[(0.0, 0.0), (1.0, 1.0), (-1.0, -1.0), (1.0, -1.0)],
    )

    with pytest.raises(ValueError, match="independently span"):
        relinearize_operating_point(_policy(source_run), source_run)


def test_relinearization_rejects_uncovered_holdout_parameter_direction(
    tmp_path: Path,
) -> None:
    source_run = _write_source_run(
        tmp_path,
        points=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (2.0, 0.0)],
    )

    with pytest.raises(
        ValueError,
        match="held-out points do not perturb declared parameter directions: bias_v",
    ):
        relinearize_operating_point(_policy(source_run), source_run)


def test_training_error_gate_is_independent_of_the_holdout_gate(
    tmp_path: Path,
) -> None:
    source_run = _write_source_run(
        tmp_path,
        points=[
            (0.0, 0.0),
            (1.0, 0.0),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, -1.0),
        ],
        gain_offsets={4: 10.0},
    )
    policy = _policy(source_run).model_copy(
        update={
            "training_candidate_indices": [1, 2, 3, 4],
            "holdout_candidate_indices": [5],
        }
    )

    result = relinearize_operating_point(policy, source_run)
    gain = next(
        model
        for model in result.models
        if model.metric == "low_frequency_gain_v_per_v"
    )

    assert result.status is RunStatus.PARTIAL
    assert not gain.training_gate_passed
    assert gain.holdout_gate_passed
    assert result.candidate_set is None
    assert any("training error gate failed" in note for note in result.notes)


def test_relinearization_cli_round_trip(tmp_path: Path, capsys) -> None:
    source_run = _write_source_run(tmp_path)
    policy = _policy(source_run)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(policy.model_dump_json(indent=2) + "\n", encoding="utf-8")
    result_path = tmp_path / "result.json"

    assert main(
        [
            "op-relinearize",
            str(policy_path),
            str(source_run),
            "--output",
            str(result_path),
        ]
    ) == 0
    capsys.readouterr()

    task_path = tmp_path / "task.json"
    assert main(
        [
            "candidate-task-from-relinearization",
            str(result_path),
            str(_write_template(tmp_path, policy)),
            "--output",
            str(task_path),
        ]
    ) == 0
    capsys.readouterr()
    task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
    assert task.candidate_set is not None


def test_live_relinearization_validation_binds_and_accepts_exact_eda_run(
    tmp_path: Path,
) -> None:
    result_path, task_path, task = _write_compiled_relinearization(tmp_path)
    run_path = _write_atomic_eda_run(tmp_path, task)

    validation = validate_relinearization_run(result_path, task_path, run_path)

    assert validation.status is RunStatus.SUCCEEDED
    assert validation.gate_passed
    assert validation.candidate_execution_gate_passed
    assert validation.prediction_accuracy_gate_passed
    assert validation.recommendation_agreement
    assert validation.declared_candidate_count == len(task.candidate_set.candidates)
    assert validation.eda_feasible_fraction == pytest.approx(1.0)
    assert all(
        comparison.measurement_evidence_source is EvidenceSource.EDA_RESULT
        and comparison.prediction_evidence_source
        is EvidenceSource.SOFTWARE_INFERENCE
        and comparison.passed
        for candidate in validation.candidates
        for comparison in candidate.comparisons
    )


def test_live_relinearization_validation_reports_prediction_partial(
    tmp_path: Path,
    capsys,
) -> None:
    result_path, task_path, task = _write_compiled_relinearization(tmp_path)
    run_path = _write_atomic_eda_run(
        tmp_path,
        task,
        metric_offsets={(2, "gm_us"): 1.0},
    )

    validation = validate_relinearization_run(result_path, task_path, run_path)

    assert validation.status is RunStatus.PARTIAL
    assert not validation.gate_passed
    assert validation.candidate_execution_gate_passed
    assert not validation.prediction_accuracy_gate_passed
    assert validation.recommendation_agreement
    assert any("gm_us" in note for note in validation.notes)
    failed = [
        comparison
        for candidate in validation.candidates
        for comparison in candidate.comparisons
        if not comparison.passed
    ]
    assert {comparison.metric for comparison in failed} == {"gm_us"}

    output = tmp_path / "live-validation.json"
    assert main(
        [
            "op-relinearization-validate",
            str(result_path),
            str(task_path),
            str(run_path),
            "--output",
            str(output),
        ]
    ) == 1
    capsys.readouterr()
    assert output.is_file()


def test_live_relinearization_validation_rejects_provenance_drift(
    tmp_path: Path,
) -> None:
    result_path, task_path, task = _write_compiled_relinearization(tmp_path)
    run_path = _write_atomic_eda_run(tmp_path, task)

    raw_task = json.loads(task_path.read_text(encoding="utf-8"))
    raw_task["candidate_set"]["source"]["bindings"][
        "relinearization_result_sha256"
    ] = "0" * 64
    drifted_task = tmp_path / "drifted-task.json"
    drifted_task.write_text(json.dumps(raw_task, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind the exact"):
        validate_relinearization_run(result_path, drifted_task, run_path)

    raw_run = json.loads(run_path.read_text(encoding="utf-8"))
    raw_run["adapter"] = "deterministic-demo"
    drifted_run = tmp_path / "drifted-run.json"
    drifted_run.write_text(json.dumps(raw_run, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="successful real-Bridge"):
        validate_relinearization_run(result_path, task_path, drifted_run)
