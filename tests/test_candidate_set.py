from __future__ import annotations

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.catalog import task_requests_oa_parameter_write
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan


def _atomic_task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "mixed-atomic-candidates",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e11},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "vdd_v": 0.9,
            },
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"m": "1"}}
            ],
            "candidate_set": {
                "source": {
                    "generator": "test.local-model",
                    "id": "mixed-atomic-test",
                    "bindings": {"model_sha256": "a" * 64},
                    "evidence_source": "software_inference",
                },
                "candidates": [
                    {
                        "id": "tuple-a",
                        "parameters": {
                            "device_width_um": 0.5,
                            "bias_v": 0.30,
                            "load_ff": 1.0,
                        },
                        "instance_parameter_updates": [
                            {
                                "instance": "MN0",
                                "parameters": {"fingers": "1"},
                            }
                        ],
                        "predicted_metrics": {"mixed_score": 100.0},
                    },
                    {
                        "id": "tuple-b",
                        "parameters": {
                            "device_width_um": 1.0,
                            "bias_v": 0.35,
                            "load_ff": 2.0,
                        },
                        "instance_parameter_updates": [
                            {
                                "instance": "MN0",
                                "parameters": {"fingers": "2"},
                            }
                        ],
                        "predicted_metrics": {"mixed_score": 1.0},
                    },
                ],
            },
            "constraints": [
                {"metric": "mixed_score", "relation": ">=", "value": 1.0}
            ],
            "objective": {"metric": "mixed_score", "goal": "maximize"},
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )


class _MixedMetricAdapter(DeterministicDemoAdapter):
    def simulate(self, task, parameters):
        schematic = self._schematics[self._key(task)]
        fingers = float(schematic["instance_parameters"]["MN0"]["fingers"])
        score = (
            float(parameters["device_width_um"])
            + float(parameters["bias_v"])
            + float(parameters["load_ff"])
            + fingers
        )
        return AdapterResult(
            data={
                "parameters": dict(parameters),
                "metrics": {"mixed_score": score},
                "metric_sources": {"mixed_score": "software_inference"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )


def test_atomic_candidates_keep_mixed_fields_together_and_record_provenance() -> None:
    task = _atomic_task()
    inputs = TaskExecutor._candidate_inputs(task)

    assert TaskExecutor._candidate_space_size(task) == 2
    assert [item.parameters for item in inputs] == [
        {
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "vdd_v": 0.9,
            "device_width_um": 0.5,
            "bias_v": 0.30,
            "load_ff": 1.0,
        },
        {
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "vdd_v": 0.9,
            "device_width_um": 1.0,
            "bias_v": 0.35,
            "load_ff": 2.0,
        },
    ]
    assert [item.instance_parameters for item in inputs] == [
        {"MN0": {"m": "1", "fingers": "1"}},
        {"MN0": {"m": "1", "fingers": "2"}},
    ]
    assert [item.atomic_candidate_id for item in inputs] == ["tuple-a", "tuple-b"]
    assert task_requests_oa_parameter_write(task)

    adapter = _MixedMetricAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 2
    assert record.candidates[1].atomic_candidate_id == "tuple-b"
    assert record.candidates[1].atomic_candidate_predicted_metrics == {
        "mixed_score": 1.0
    }
    assert (
        record.candidates[1].atomic_candidate_evidence_source
        is EvidenceSource.SOFTWARE_INFERENCE
    )
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.0)
    assert record.search_audit is not None
    assert record.search_audit.declared_candidate_count == 2
    assert record.search_audit.candidate_set_source is not None
    assert (
        record.search_audit.candidate_set_source.evidence_source
        is EvidenceSource.SOFTWARE_INFERENCE
    )
    assert "原子 tuple" in next(
        step.description
        for step in plan.steps
        if step.capability == "simulation.sweep"
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda task: task["candidate_set"]["source"].update({"bindings": {}}),
            "require at least one hash binding",
        ),
        (
            lambda task: task.update(
                {"parameter_space": {"device_width_um": [0.5, 1.0]}}
            ),
            "cannot be combined",
        ),
        (
            lambda task: task["parameters"].update({"bias_v": 0.3}),
            "fixed parameters overlap",
        ),
        (
            lambda task: task["candidate_set"]["candidates"][1][
                "parameters"
            ].pop("load_ff"),
            "same semantic, raw, and testbench fields",
        ),
        (
            lambda task: task["candidate_set"]["candidates"][1].update(
                {
                    "parameters": dict(
                        task["candidate_set"]["candidates"][0]["parameters"]
                    ),
                    "instance_parameter_updates": [
                        {
                            "instance": "MN0",
                            "parameters": {"fingers": "1"},
                        }
                    ],
                }
            ),
            "parameter tuples must be unique",
        ),
    ],
)
def test_atomic_candidate_contract_rejects_ambiguous_domains(
    mutate, message: str
) -> None:
    payload = _atomic_task().model_dump(mode="json")
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(payload)
