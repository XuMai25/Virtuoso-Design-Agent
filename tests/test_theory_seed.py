from __future__ import annotations

import json
from pathlib import Path

import pytest

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TheorySeedSource
from virtuoso_design_agent.theory import (
    DifferentialPairTheoryRequest,
    size_differential_pair,
)
from virtuoso_design_agent.theory_seed import (
    DifferentialPairTheorySeedPolicy,
    build_theory_seeded_task,
)


ROOT = Path(__file__).resolve().parents[1]
THEORY_REQUEST = (
    ROOT / "examples" / "theory" / "differential-pair-gmid.synthetic.json"
)
TASK_EXAMPLE = (
    ROOT
    / "examples"
    / "tasks"
    / "differential-pair-current-mirror-ac-geometry-tune.bridge.json"
)


def _write_theory_result(
    tmp_path: Path,
    *,
    broaden_feasibility: bool = False,
) -> tuple[Path, object]:
    payload = json.loads(THEORY_REQUEST.read_text(encoding="utf-8"))
    if broaden_feasibility:
        for constraint in payload["constraints"]:
            if constraint["metric"] == "estimated_differential_gain_v_per_v":
                constraint["value"] = 50.0
            if constraint["metric"] == "estimated_power_w":
                constraint["value"] = 1e-3
    result = size_differential_pair(
        DifferentialPairTheoryRequest.model_validate(payload)
    )
    path = tmp_path / "theory-result.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path, result


def _write_template(tmp_path: Path, *, max_iterations: int = 6) -> Path:
    payload = json.loads(TASK_EXAMPLE.read_text(encoding="utf-8"))
    payload.pop("parameter_space")
    payload["id"] = "differential-pair-theory-seed-test"
    payload["limits"]["max_iterations"] = max_iterations
    path = tmp_path / "task-template.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _policy(result: object, *, maximum: int = 3, ranked: int = 1):
    return DifferentialPairTheorySeedPolicy.model_validate(
        {
            "id": "theory-seed-test-policy",
            "expected_theory_request_id": result.request_id,
            "expected_theory_request_sha256": result.request_sha256,
            "maximum_candidates": maximum,
            "ranked_candidate_count": ranked,
        }
    )


def test_theory_seed_preserves_atomic_tuples_and_hash_provenance(
    tmp_path: Path,
) -> None:
    result_path, result = _write_theory_result(
        tmp_path, broaden_feasibility=True
    )
    template = _write_template(tmp_path)
    policy = _policy(result)

    first = build_theory_seeded_task(policy, result_path, template)
    second = build_theory_seeded_task(policy, result_path, template)

    assert first == second
    assert first.parameter_space == {}
    assert first.instance_parameter_space == []
    assert first.theory_seed is not None
    assert len(first.theory_seed.candidates) == 3
    assert first.theory_seed.candidates[0].source_candidate_id == (
        result.recommended_candidate_id
    )
    assert len(first.theory_seed.source.theory_result_sha256) == 64
    assert first.theory_seed.source.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    assert first.theory_seed.source.width_quantization == "decimal_places"
    assert first.theory_seed.source.width_grid_um is None
    expected = [
        dict(first.parameters) | dict(candidate.parameters)
        for candidate in first.theory_seed.candidates
    ]
    actual = [candidate.parameters for candidate in TaskExecutor._candidate_inputs(first)]
    assert actual == expected
    assert TaskExecutor._candidate_space_size(first) == 3


def test_theory_seed_snaps_continuous_widths_to_declared_oa_grid(
    tmp_path: Path,
) -> None:
    result_path, result = _write_theory_result(
        tmp_path, broaden_feasibility=True
    )
    policy = _policy(result, maximum=1).model_copy(
        update={"width_grid_um": 0.005}
    )

    task = build_theory_seeded_task(
        policy,
        result_path,
        _write_template(tmp_path),
    )

    assert task.theory_seed is not None
    candidate = task.theory_seed.candidates[0]
    for name in (
        "input_width_um",
        "pmos_load_width_um",
        "tail_width_um",
    ):
        assert candidate.parameters[name] * 200 == pytest.approx(
            round(candidate.parameters[name] * 200)
        )
    assert task.theory_seed.source.width_quantization == "nearest_grid_half_up"
    assert task.theory_seed.source.width_grid_um == pytest.approx(0.005)


def test_theory_seed_source_rejects_inconsistent_width_quantization() -> None:
    common = {
        "policy_id": "policy",
        "policy_sha256": "1" * 64,
        "theory_request_id": "request",
        "theory_request_sha256": "2" * 64,
        "theory_result_sha256": "3" * 64,
        "device_data_source": "pdk_characterization",
        "optimality_classification": "best_in_declared_discrete_domain",
        "declared_theory_combinations": 4,
        "evaluated_theory_combinations": 4,
        "theory_domain_exhausted": True,
        "selection_strategy": "ranked_then_log_maximin",
    }

    with pytest.raises(ValueError, match="requires width_grid_um"):
        TheorySeedSource.model_validate(
            common | {"width_quantization": "nearest_grid_half_up"}
        )
    with pytest.raises(ValueError, match="cannot declare width_grid_um"):
        TheorySeedSource.model_validate(common | {"width_grid_um": 0.005})
    with pytest.raises(ValueError, match="combination counts to match"):
        TheorySeedSource.model_validate(
            common | {"evaluated_theory_combinations": 3}
        )


def test_theory_seed_rejects_request_hash_drift(tmp_path: Path) -> None:
    result_path, result = _write_theory_result(tmp_path)
    template = _write_template(tmp_path)
    policy = _policy(result).model_copy(
        update={"expected_theory_request_sha256": "0" * 64}
    )

    with pytest.raises(ValueError, match="SHA-256"):
        build_theory_seeded_task(policy, result_path, template)


def test_theory_seed_refuses_a_no_recommendation_result(tmp_path: Path) -> None:
    payload = json.loads(THEORY_REQUEST.read_text(encoding="utf-8"))
    payload["constraints"][0]["value"] = 1_000.0
    result = size_differential_pair(
        DifferentialPairTheoryRequest.model_validate(payload)
    )
    assert result.status is RunStatus.PARTIAL
    path = tmp_path / "infeasible-theory.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="successful theory result"):
        build_theory_seeded_task(
            _policy(result),
            path,
            _write_template(tmp_path),
        )


def test_theory_seed_budget_counts_full_atomic_domain(tmp_path: Path) -> None:
    result_path, result = _write_theory_result(
        tmp_path, broaden_feasibility=True
    )
    task = build_theory_seeded_task(
        _policy(result),
        result_path,
        _write_template(tmp_path, max_iterations=2),
    )
    notes: list[str] = []

    status = TaskExecutor._note_budget_exhaustion(
        TaskExecutor.__new__(TaskExecutor), task, RunStatus.SUCCEEDED, notes
    )

    assert status is RunStatus.PARTIAL
    assert len(TaskExecutor._candidate_inputs(task)) == 2
    assert "2 of 3 declared candidates" in notes[0]


def test_eda_metrics_not_theory_prediction_control_final_rank(tmp_path: Path) -> None:
    result_path, result = _write_theory_result(tmp_path)
    task = build_theory_seeded_task(
        _policy(result, maximum=2, ranked=2),
        result_path,
        _write_template(tmp_path),
    )
    inputs = TaskExecutor._candidate_inputs(task)
    evaluated = []
    for index, (candidate, measured_gbw) in enumerate(
        zip(inputs, (1.0e9, 2.0e9), strict=True), start=1
    ):
        metrics = {
            "all_signal_devices_saturation_region": 1.0,
            "max_load_current_mismatch_percent": 0.0,
            "minimum_output_swing_margin_v": 0.2,
            "dc_supply_power_uw": 10.0,
            "differential_low_frequency_gain_v_per_v": 4.0,
            "differential_bandwidth_3db_hz": 2.0e9,
            "differential_gain_peaking_db": 0.0,
            "low_frequency_cmrr_db": 40.0,
            "differential_gain_bandwidth_product_hz": measured_gbw,
        }
        evaluated.append(
            TaskExecutor._evaluate_candidate(
                task,
                index,
                candidate.parameters,
                AdapterResult(
                    data={
                        "parameters": candidate.parameters,
                        "metrics": metrics,
                        "metric_sources": {
                            name: "eda_result" for name in metrics
                        },
                    },
                    evidence_source=EvidenceSource.EDA_RESULT,
                ),
                theory_seed_candidate_id=candidate.theory_seed_candidate_id,
                theory_seed_source_candidate_id=(
                    candidate.theory_seed_source_candidate_id
                ),
                theory_seed_predicted_metrics=(
                    candidate.theory_seed_predicted_metrics
                ),
            )
        )

    selected = min(evaluated, key=lambda item: TaskExecutor._rank(task, item))

    assert selected.index == 2
    assert selected.evidence_source is EvidenceSource.EDA_RESULT
    assert selected.theory_seed_evidence_source is EvidenceSource.SOFTWARE_INFERENCE


def test_theory_seed_cli_writes_the_same_task_it_prints(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result_path, result = _write_theory_result(tmp_path)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(_policy(result, maximum=2, ranked=2).model_dump_json(indent=2))
    output = tmp_path / "task.json"

    assert (
        main(
            [
                "theory-seed-task",
                str(policy_path),
                str(result_path),
                str(_write_template(tmp_path)),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == json.loads(
        output.read_text(encoding="utf-8")
    )
