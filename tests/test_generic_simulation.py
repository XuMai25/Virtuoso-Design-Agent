from __future__ import annotations

import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.base import AdapterInterrupted, AdapterResult
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.executor import TaskExecutor, load_execution_checkpoint
from virtuoso_design_agent.generic_simulation import (
    GenericOaSimulationSpec,
    GenericVoltageExpression,
    render_generic_oa_testbench,
)
from virtuoso_design_agent.models import (
    EvidenceSource,
    RunStatus,
    SelectionScope,
    TaskSpec,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.profiles import load_pdk_profile
from virtuoso_design_agent.topology_delta import derive_topology_delta


def _raw_schematic() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "lib": "tsmcN28",
                "cell": "nch_lvt_mac",
                "view": "symbol",
                "params": {"model": "nch_lvt_mac", "w": "1u", "l": "30n"},
                "terms": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
            }
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }


def _pin_geometry() -> dict[str, dict]:
    return {
        name: {
            "direction": None,
            "numBits": 1,
            "master": "ipin",
            "xy": [float(index), 0.0],
            "orient": "R0",
        }
        for index, name in enumerate(("IN", "OUT", "VDD", "VSS"))
    }


def _inspection() -> dict:
    raw = _raw_schematic()
    return {
        "instances": [
            {
                "name": "MN0",
                "library": "tsmcN28",
                "cell": "nch_lvt_mac",
                "view": "symbol",
                "terminals": dict(raw["instances"][0]["terms"]),
            }
        ],
        "nets": list(raw["nets"]),
        "pins": list(raw["pins"]),
        "instance_parameters": {
            "MN0": {"model": "nch_lvt_mac", "w": "1u", "l": "30n"}
        },
        "bridge_schematic": raw,
    }


def _context() -> dict:
    return {
        "id": "user-single-ended-stage",
        "roles": [
            {"role": "signal.input", "nets": ["IN"]},
            {"role": "signal.output", "nets": ["OUT"]},
            {"role": "device.input", "instances": ["MN0"]},
            {
                "role": "device.input_gate",
                "terminals": [
                    {"instance": "MN0", "terminal": "G", "net": "IN"}
                ],
            },
        ],
        "instance_parameter_permissions": [
            {"instance": "MN0", "parameters": ["w", "l"]}
        ],
        "required_analyses": ["dc", "ac"],
        "metrics": [
            "output_dc_v",
            "supply_current_a",
            "device_ids_a",
            "low_frequency_gain_db",
            "bandwidth_3db_hz",
            "gain_bandwidth_product_hz",
        ],
    }


def _generic_spec() -> dict:
    return {
        "sources": [
            {
                "name": "VDD_SRC",
                "kind": "voltage",
                "positive_node": "VDD",
                "dc_value": 0.9,
            },
            {
                "name": "VSS_SRC",
                "kind": "voltage",
                "positive_node": "VSS",
                "dc_value": 0.0,
            },
            {
                "name": "VIN_SRC",
                "kind": "voltage",
                "positive_node": "IN",
                "dc_value": 0.45,
                "ac_magnitude": 1.0,
            },
        ],
        "loads": [
            {
                "name": "CL0",
                "kind": "capacitor",
                "positive_node": "OUT",
                "value": 1e-15,
            }
        ],
        "dc_voltage_metrics": [
            {
                "metric": "output_dc_v",
                "expression": {"positive_node": "OUT"},
            }
        ],
        "dc_current_metrics": [
            {"metric": "supply_current_a", "source": "VDD_SRC"}
        ],
        "operating_point_metrics": [
            {"metric": "device_ids_a", "instance": "MN0", "quantity": "ids"}
        ],
        "transfer": {
            "input": {"positive_node": "IN"},
            "output": {"positive_node": "OUT"},
        },
        "netlist_parameter_bindings": [
            {
                "instance": "MN0",
                "oa_parameter": "w",
                "netlist_parameter": "w",
            },
            {
                "instance": "MN0",
                "oa_parameter": "l",
                "netlist_parameter": "l",
            },
        ],
    }


def _task(*, analysis: str = "ac") -> TaskSpec:
    payload = {
        "id": f"generic-{analysis}",
        "operation": "simulation.run",
        "circuit": "existing_schematic",
        "target": {"library": "vda_test", "cell": "vda_user_stage"},
        "analysis": analysis,
        "design_context": _context(),
        "generic_simulation": _generic_spec(),
        "constraints": [
            {
                "metric": (
                    "low_frequency_gain_db" if analysis == "ac" else "output_dc_v"
                ),
                "relation": ">=",
                "value": 0.1,
            }
        ],
        "safety": {"allow_remote_compute": True},
    }
    if analysis == "ac":
        payload["ac_sweep"] = {
            "start_hz": 1e2,
            "stop_hz": 1e8,
            "points_per_decade": 10,
        }
    return TaskSpec.model_validate(payload)


def _tune_payload(*, constraint_db: float = 12.0, max_iterations: int = 3) -> dict:
    return {
        "id": "generic-ac-tune",
        "operation": "design.tune",
        "circuit": "existing_schematic",
        "target": {"library": "vda_test", "cell": "vda_user_stage"},
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e2,
            "stop_hz": 1e8,
            "points_per_decade": 10,
        },
        "design_context": _context(),
        "generic_simulation": _generic_spec(),
        "instance_parameter_space": [
            {
                "instance": "MN0",
                "parameter": "w",
                "values": ["1u", "2u", "3u"],
            }
        ],
        "constraints": [
            {
                "metric": "low_frequency_gain_db",
                "relation": ">=",
                "value": constraint_db,
            }
        ],
        "objective": {
            "metric": "low_frequency_gain_db",
            "goal": "maximize",
        },
        "limits": {"max_iterations": max_iterations},
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
    }


def _tune_task(*, constraint_db: float = 12.0, max_iterations: int = 3) -> TaskSpec:
    return TaskSpec.model_validate(
        _tune_payload(
            constraint_db=constraint_db,
            max_iterations=max_iterations,
        )
    )


def _staged_tune_payload() -> dict:
    payload = _tune_payload()
    payload.pop("analysis")
    payload["analysis_stages"] = [
        {
            "id": "bias",
            "analysis": "dc",
            "constraint_metrics": ["output_dc_v"],
        },
        {
            "id": "gain-bandwidth",
            "analysis": "ac",
            "constraint_metrics": ["low_frequency_gain_db"],
        },
    ]
    payload["constraints"] = [
        {"metric": "output_dc_v", "relation": ">=", "value": 0.4},
        {
            "metric": "low_frequency_gain_db",
            "relation": ">=",
            "value": 12.0,
        },
    ]
    return payload


def _winner_verification_payload() -> dict:
    payload = _tune_payload()
    payload["generic_simulation"][
        "operating_condition_supply_source"
    ] = "VDD_SRC"
    payload["winner_verification"] = {
        "analysis_stages": [
            {
                "id": "winner-bias",
                "analysis": "dc",
                "constraint_metrics": ["output_dc_v"],
            },
            {
                "id": "winner-ac",
                "analysis": "ac",
                "constraint_metrics": ["low_frequency_gain_db"],
            },
        ],
        "constraints": [
            {"metric": "output_dc_v", "relation": ">=", "value": 0.4},
            {
                "metric": "low_frequency_gain_db",
                "relation": ">=",
                "value": 18.0,
            },
        ],
        "operating_conditions": [
            {
                "name": "tt_27c_0p90v",
                "process_corner": "tt",
                "temperature_c": 27.0,
                "vdd_v": 0.9,
            },
            {
                "name": "ss_125c_0p81v",
                "process_corner": "ss",
                "temperature_c": 125.0,
                "vdd_v": 0.81,
            },
        ],
        "ac_sweep": {
            "start_hz": 1e2,
            "stop_hz": 1e8,
            "points_per_decade": 10,
        },
    }
    return payload


def _topology_refinement_payload(
    *, constraint_db: float = 12.0, max_iterations: int = 4
) -> dict:
    before = _inspection()
    after = {
        **before,
        "instances": [dict(item) for item in before["instances"]],
    }
    after["instances"][0] = {
        **after["instances"][0],
        "cell": "nch_rvt_mac",
    }
    contract = derive_topology_delta("try-rvt-input", before, after)
    baseline_context = _context()
    baseline_context.update(
        {
            "expected_topology_sha256": contract.expected_before_sha256,
            "topology_edits": {
                "allowed_operations": ["replace_master"],
                "mutable_instances": ["MN0"],
            },
        }
    )
    alternative_context = {
        **baseline_context,
        "id": "user-single-ended-stage-rvt",
        "expected_topology_sha256": contract.expected_after_sha256,
    }
    payload = _tune_payload(
        constraint_db=constraint_db,
        max_iterations=max_iterations,
    )
    payload.update(
        {
            "id": "generic-ac-topology-close-loop",
            "operation": "design.close_loop",
            "design_context": baseline_context,
            "instance_parameter_space": [
                {
                    "instance": "MN0",
                    "parameter": "w",
                    "values": ["1u", "2u"],
                }
            ],
            "topology_refinement": {
                "baseline_id": "lvt",
                "alternative_id": "rvt",
                "topology_delta": {
                    "direction": "forward",
                    "contract": contract.model_dump(mode="json"),
                },
                "alternative_design_context": alternative_context,
                "alternative_generic_simulation": _generic_spec(),
            },
        }
    )
    return payload


def _staged_topology_refinement_payload() -> dict:
    payload = _topology_refinement_payload()
    payload.pop("analysis")
    payload["analysis_stages"] = [
        {
            "id": "bias",
            "analysis": "dc",
            "constraint_metrics": ["output_dc_v"],
        },
        {
            "id": "gain-bandwidth",
            "analysis": "ac",
            "constraint_metrics": ["low_frequency_gain_db"],
        },
    ]
    payload["constraints"] = [
        {"metric": "output_dc_v", "relation": ">=", "value": 0.4},
        {
            "metric": "low_frequency_gain_db",
            "relation": ">=",
            "value": 12.0,
        },
    ]
    return payload


def _multi_topology_refinement_payload() -> dict:
    before = _inspection()
    alternatives = []
    baseline_context = _context()
    for variant, cell in (("rvt", "nch_rvt_mac"), ("hvt", "nch_hvt_mac")):
        after = {
            **before,
            "instances": [dict(item) for item in before["instances"]],
        }
        after["instances"][0] = {
            **after["instances"][0],
            "cell": cell,
        }
        contract = derive_topology_delta(f"try-{variant}-input", before, after)
        if "expected_topology_sha256" not in baseline_context:
            baseline_context.update(
                {
                    "expected_topology_sha256": contract.expected_before_sha256,
                    "topology_edits": {
                        "allowed_operations": ["replace_master"],
                        "mutable_instances": ["MN0"],
                    },
                }
            )
        alternatives.append(
            {
                "id": variant,
                "topology_delta": {
                    "direction": "forward",
                    "contract": contract.model_dump(mode="json"),
                },
                "design_context": {
                    **baseline_context,
                    "id": f"user-single-ended-stage-{variant}",
                    "expected_topology_sha256": contract.expected_after_sha256,
                },
                "generic_simulation": _generic_spec(),
            }
        )
    payload = _tune_payload(max_iterations=6)
    payload.update(
        {
            "id": "generic-ac-multi-topology-close-loop",
            "operation": "design.close_loop",
            "design_context": baseline_context,
            "instance_parameter_space": [
                {
                    "instance": "MN0",
                    "parameter": "w",
                    "values": ["1u", "2u"],
                }
            ],
            "topology_refinement": {
                "baseline_id": "lvt",
                "alternatives": alternatives,
            },
        }
    )
    return payload


def _added_instance_refinement_payload() -> dict:
    before = _inspection()
    after = {
        **before,
        "instances": [dict(item) for item in before["instances"]],
        "nets": [*before["nets"], "NSRC"],
    }
    after["instances"][0] = {
        **after["instances"][0],
        "terminals": {
            **after["instances"][0]["terminals"],
            "S": "NSRC",
        },
    }
    after["instances"].append(
        {
            "name": "RS0",
            "library": "analogLib",
            "cell": "res",
            "view": "symbol",
            "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
        }
    )
    contract = derive_topology_delta("add-source-degeneration", before, after)
    baseline_context = _context()
    baseline_context.update(
        {
            "expected_topology_sha256": contract.expected_before_sha256,
            "topology_edits": {
                "allowed_operations": [
                    "add_net",
                    "reconnect_terminal",
                    "add_instance",
                ],
                "mutable_instances": ["MN0", "RS0"],
                "mutable_nets": ["NSRC"],
            },
        }
    )
    alternative_context = {
        **baseline_context,
        "id": "user-single-ended-stage-degenerated",
        "expected_topology_sha256": contract.expected_after_sha256,
        "roles": [
            *baseline_context["roles"],
            {"role": "device.source_degeneration", "instances": ["RS0"]},
        ],
        "instance_parameter_permissions": [
            *baseline_context["instance_parameter_permissions"],
            {"instance": "RS0", "parameters": ["r"], "modes": ["fixed"]},
        ],
        "topology_edits": {
            "allowed_operations": [
                "remove_instance",
                "reconnect_terminal",
                "remove_net",
            ],
            "mutable_instances": ["MN0", "RS0"],
            "mutable_nets": ["NSRC"],
        },
    }
    alternative_simulation = _generic_spec()
    alternative_simulation["netlist_parameter_bindings"].append(
        {
            "instance": "RS0",
            "oa_parameter": "r",
            "netlist_parameter": "r",
        }
    )
    payload = _tune_payload(max_iterations=4)
    payload.update(
        {
            "operation": "design.close_loop",
            "design_context": baseline_context,
            "instance_parameter_space": [
                {
                    "instance": "MN0",
                    "parameter": "w",
                    "values": ["1u", "2u"],
                }
            ],
            "topology_refinement": {
                "alternative_id": "source-degenerated",
                "topology_delta": {
                    "direction": "forward",
                    "contract": contract.model_dump(mode="json"),
                },
                "alternative_design_context": alternative_context,
                "alternative_generic_simulation": alternative_simulation,
                "alternative_instance_parameter_updates": [
                    {"instance": "RS0", "parameters": {"r": "1k"}}
                ],
            },
        }
    )
    return payload


def test_generic_existing_schematic_task_plans_read_before_compute() -> None:
    task = _task()
    plan = build_plan(task)

    assert task.parameters == {}
    assert [step.capability for step in plan.steps[:5]] == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
        "netlist.generate",
        "simulation.run",
    ]
    assert plan.requires_remote_write is False
    assert plan.requires_remote_compute is True
    assert "instance/model/node" in plan.steps[3].description


def test_generic_task_rejects_missing_context_bad_binding_and_dynamic_analysis() -> None:
    valid = _task().model_dump(mode="json")
    valid["design_context"] = None
    with pytest.raises(ValidationError, match="requires design_context"):
        TaskSpec.model_validate(valid)

    bad_binding = _task().model_dump(mode="json")
    bad_binding["generic_simulation"]["netlist_parameter_bindings"][0][
        "oa_parameter"
    ] = "m"
    with pytest.raises(ValidationError, match="outside design_context"):
        TaskSpec.model_validate(bad_binding)

    unsupported = _task().model_dump(mode="json")
    unsupported["analysis"] = "psrr"
    unsupported["ac_sweep"] = None
    with pytest.raises(ValidationError, match="dc, ac, transient, or noise"):
        TaskSpec.model_validate(unsupported)


def test_read_only_generic_simulation_allows_no_parameter_binding_but_tuning_does_not() -> None:
    read_only = _task().model_dump(mode="json")
    read_only["generic_simulation"]["netlist_parameter_bindings"] = []
    task = TaskSpec.model_validate(read_only)

    assert task.generic_simulation is not None
    assert task.generic_simulation.netlist_parameter_bindings == []

    tuning = _tune_payload()
    tuning["generic_simulation"]["netlist_parameter_bindings"] = []
    with pytest.raises(ValidationError, match="OA-to-si netlist parameter bindings"):
        TaskSpec.model_validate(tuning)


def test_generic_tuning_requires_bound_raw_instance_fields() -> None:
    task = _tune_task()
    plan = build_plan(task)

    assert plan.requires_remote_write is True
    assert plan.requires_remote_compute is True
    assert [step.capability for step in plan.steps[:5]] == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
        "parameters.stage",
        "netlist.generate",
    ]
    assert "schematic.ensure" not in {step.capability for step in plan.steps}

    unbound = _tune_payload()
    unbound["design_context"]["instance_parameter_permissions"][0][
        "parameters"
    ].append("m")
    unbound["instance_parameter_space"][0]["parameter"] = "m"
    with pytest.raises(ValidationError, match="OA-to-si netlist parameter bindings"):
        TaskSpec.model_validate(unbound)

    semantic = _tune_payload()
    semantic["parameter_space"] = {"device_width_um": [1.0, 2.0]}
    semantic["instance_parameter_space"] = []
    with pytest.raises(ValidationError, match="raw instance parameters"):
        TaskSpec.model_validate(semantic)


def test_staged_generic_task_validates_single_constraint_ownership_and_plan() -> None:
    task = TaskSpec.model_validate(_staged_tune_payload())
    plan = build_plan(task)

    assert task.analysis is None
    assert [analysis.value for analysis in task.resolved_analyses()] == [
        "dc",
        "ac",
    ]
    assert "bias(DC)" in next(
        step.description
        for step in plan.steps
        if step.capability == "simulation.sweep"
    )

    duplicate = _staged_tune_payload()
    duplicate["analysis_stages"][1]["constraint_metrics"].append(
        "output_dc_v"
    )
    with pytest.raises(ValidationError, match="assigned more than once"):
        TaskSpec.model_validate(duplicate)

    unassigned = _staged_tune_payload()
    unassigned["analysis_stages"][0]["constraint_metrics"] = []
    with pytest.raises(ValidationError, match="must belong to one analysis stage"):
        TaskSpec.model_validate(unassigned)


def test_shared_netlist_stage_mode_is_explicit_and_planned() -> None:
    payload = _staged_tune_payload()
    payload["analysis_stage_execution"] = "shared_netlist"
    task = TaskSpec.model_validate(payload)
    plan = build_plan(task)

    assert task.analysis_stage_execution.value == "shared_netlist"
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.sweep"
    )
    assert "只生成并核对一次 si 网表" in simulation.description
    evaluation = next(
        step for step in plan.steps if step.capability == "results.select"
    )
    assert "checkpoint 粒度为候选边界" in evaluation.description

    invalid = _tune_payload()
    invalid["analysis_stage_execution"] = "shared_netlist"
    with pytest.raises(ValidationError, match="requires analysis_stages"):
        TaskSpec.model_validate(invalid)

    wrong_circuit = _staged_tune_payload()
    wrong_circuit["circuit"] = "common_source"
    wrong_circuit["parameters"] = {"bias_v": 0.35}
    wrong_circuit["design_context"] = None
    wrong_circuit["generic_simulation"] = None
    with pytest.raises(ValidationError, match="require circuit='existing_schematic'"):
        TaskSpec.model_validate(wrong_circuit)


def test_staged_generic_tuning_rejects_early_and_commits_best() -> None:
    task = TaskSpec.model_validate(_staged_tune_payload())
    plan = build_plan(task)
    adapter = _StagedGenericTuningAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.width == "2u"
    assert adapter.simulated_stages == [
        ("1u", "dc"),
        ("2u", "dc"),
        ("2u", "ac"),
        ("3u", "dc"),
        ("3u", "ac"),
    ]
    rejected = record.candidates[0]
    assert rejected.terminated_after_stage == "bias"
    assert rejected.analysis_complete is True
    assert rejected.feasible is False
    assert [stage.stage_id for stage in rejected.analysis_stages] == ["bias"]
    assert record.search_audit is not None
    assert record.search_audit.domain_exhausted is True


def test_shared_netlist_stages_batch_once_per_candidate_and_keep_gate_truth() -> None:
    payload = _staged_tune_payload()
    payload["analysis_stage_execution"] = "shared_netlist"
    task = TaskSpec.model_validate(payload)
    plan = build_plan(task)
    adapter = _SharedStagedGenericTuningAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert adapter.batch_calls == ["1u", "2u", "3u"]
    assert adapter.scalar_simulation_calls == 0
    assert record.candidates[0].terminated_after_stage == "bias"
    assert [
        stage.stage_id for stage in record.candidates[0].analysis_stages
    ] == ["bias"]
    assert [
        stage.stage_id for stage in record.candidates[1].analysis_stages
    ] == ["bias", "gain-bandwidth"]
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert any(
        action.action == "simulation.candidate.1.stages.shared-netlist"
        for action in record.actions
    )


def test_shared_netlist_checkpoint_restarts_only_interrupted_candidate(
    tmp_path,
) -> None:
    payload = _staged_tune_payload()
    payload["analysis_stage_execution"] = "shared_netlist"
    task = TaskSpec.model_validate(payload)
    plan = build_plan(task)
    adapter = _SharedStagedGenericTuningAdapter()
    adapter.interrupt_width = "2u"
    checkpoint_path = tmp_path / "shared-stage-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.active_candidate_index == 2
    assert checkpoint.next_analysis_stage_index == 1
    assert checkpoint.active_candidate_stages == []

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.batch_calls == ["1u", "2u", "2u", "3u"]


def test_winner_verification_contract_requires_explicit_supply_binding() -> None:
    payload = _winner_verification_payload()
    payload["generic_simulation"].pop(
        "operating_condition_supply_source"
    )

    with pytest.raises(ValidationError, match="supply_source"):
        TaskSpec.model_validate(payload)


def test_winner_only_pvt_verification_commits_only_verified_candidate() -> None:
    task = TaskSpec.model_validate(_winner_verification_payload())
    plan = build_plan(task)
    adapter = _WinnerVerificationAdapter(verification_passes=True)

    verify_step = next(
        step
        for step in plan.steps
        if step.capability == "simulation.verify-winner"
    )
    assert "runner-up" in verify_step.description

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.width == "2u"
    assert adapter.winner_batch_widths == ["2u"]
    assert record.winner_verification is not None
    assert record.winner_verification.feasible is True
    assert [
        item.name for item in record.winner_verification.operating_conditions
    ] == ["tt_27c_0p90v", "ss_125c_0p81v"]
    assert math.isclose(
        record.winner_verification.metrics["low_frequency_gain_db"],
        18.5,
    )


def test_failed_winner_only_pvt_restores_initial_and_withholds_runner_up() -> None:
    task = TaskSpec.model_validate(_winner_verification_payload())
    plan = build_plan(task)
    adapter = _WinnerVerificationAdapter(verification_passes=False)

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    assert adapter.width == "1u"
    assert adapter.winner_batch_widths == ["2u"]
    assert record.selected_instance_parameters is None
    assert record.selected_metrics is None
    assert record.winner_verification is not None
    assert record.winner_verification.feasible is False
    assert record.search_audit is not None
    assert record.search_audit.selection_scope is (
        SelectionScope.NO_RECOMMENDATION_FROM_EVALUATED_POINTS
    )
    assert "unverified nominal runners-up were not silently promoted" in (
        record.search_audit.statement
    )


def test_staged_generic_checkpoint_resumes_at_next_analysis_stage(tmp_path) -> None:
    task = TaskSpec.model_validate(_staged_tune_payload())
    plan = build_plan(task)
    adapter = _StagedGenericTuningAdapter()
    adapter.interrupt_stage = ("2u", "ac")
    checkpoint_path = tmp_path / "staged-analysis-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert checkpoint.active_candidate_index == 2
    assert checkpoint.next_analysis_stage_index == 2
    assert [stage.stage_id for stage in checkpoint.active_candidate_stages] == [
        "bias"
    ]

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.simulated_stages.count(("2u", "dc")) == 1
    assert adapter.simulated_stages.count(("2u", "ac")) == 2
    final_checkpoint = load_execution_checkpoint(checkpoint_path)
    assert final_checkpoint.complete is True
    assert final_checkpoint.active_candidate_index is None
    assert final_checkpoint.active_candidate_stages == []


@pytest.mark.parametrize(
    ("output_dc_v", "expected_analyses", "expected_termination"),
    [
        (0.2, ["dc"], "bias"),
        (0.5, ["dc", "ac"], None),
    ],
)
def test_worker_shared_stage_bundle_reuses_one_cache_and_honors_gate(
    monkeypatch,
    output_dc_v,
    expected_analyses,
    expected_termination,
) -> None:
    calls: list[tuple[str, int]] = []

    def fake_simulate(payload, *, _bundle_cache=None):
        assert _bundle_cache is not None
        calls.append((payload["analysis"], id(_bundle_cache)))
        _bundle_cache.setdefault("netlist_generation_count", 1)
        _bundle_cache.setdefault("schematic_readback_count", 1)
        netlist = {
            "source": "eda_result",
            "remote_path": "/data/xum/shared/input.scs",
            "sha256": "c" * 64,
        }
        metrics = {"output_dc_v": output_dc_v}
        if payload["analysis"] == "ac":
            metrics["low_frequency_gain_db"] = 20.0
        return {
            "metrics": metrics,
            "metric_sources": {name: "eda_result" for name in metrics},
            "analysis_complete": True,
            "evidence": {
                "schematic_readback": {
                    "source": "bridge_readback",
                    "topology_sha256": "d" * 64,
                },
                "netlist": netlist,
            },
        }

    monkeypatch.setattr(
        bridge_worker,
        "simulate_existing_schematic",
        fake_simulate,
    )
    result = bridge_worker.simulate_existing_schematic_stages(
        {
            "analysis_stages": _staged_tune_payload()["analysis_stages"],
            "constraints": [
                *_staged_tune_payload()["constraints"],
                {
                    "metric": "output_dc_v",
                    "relation": "<=",
                    "value": 0.8,
                },
            ],
        }
    )

    assert [analysis for analysis, _ in calls] == expected_analyses
    assert len({cache_id for _, cache_id in calls}) == 1
    assert result["terminated_after_stage"] == expected_termination
    assert result["shared_netlist"]["netlist_generation_count"] == 1
    assert [item["analysis"] for item in result["stage_results"]] == expected_analyses


def test_generic_close_loop_accepts_one_exact_topology_alternative() -> None:
    task = TaskSpec.model_validate(_topology_refinement_payload())

    assert task.topology_refinement is not None
    assert task.topology_refinement.baseline_id == "lvt"
    assert task.topology_refinement.alternative_id == "rvt"
    assert task.topology_refinement.topology_delta.direction == "forward"
    assert task.limits.max_iterations == 4


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"objective": None}, "requires an objective"),
        ({"limits": {"max_iterations": 3}}, "complete topology-parameter domain"),
    ],
)
def test_generic_close_loop_rejects_ambiguous_or_biased_selection(
    patch: dict, message: str
) -> None:
    payload = _topology_refinement_payload()
    payload.update(patch)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(payload)


def test_generic_close_loop_plan_discloses_two_phase_commit_or_restore() -> None:
    task = TaskSpec.model_validate(_topology_refinement_payload())
    plan = build_plan(task)
    capabilities = [step.capability for step in plan.steps]

    assert plan.requires_remote_compute is True
    assert plan.requires_remote_write is True
    assert capabilities == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
        "parameters.stage.baseline",
        "simulation.sweep.baseline",
        "parameters.restore.before-topology",
        "schematic.transform.topology-delta.forward",
        "schematic.inspect.alternative",
        "design.context.bind.alternative",
        "parameters.stage.alternative",
        "simulation.sweep.alternative",
        "results.select.topology-and-parameters",
        "design.finalize.topology-and-parameters",
        "schematic.inspect.final",
        "evidence.persist",
    ]


def test_generic_close_loop_accepts_explicit_fixed_parameters_on_added_instance() -> None:
    task = TaskSpec.model_validate(_added_instance_refinement_payload())

    assert task.topology_refinement is not None
    assert task.topology_refinement.alternative_instance_parameter_updates[
        0
    ].parameters == {"r": "1k"}

    payload = _added_instance_refinement_payload()
    payload["topology_refinement"]["alternative_instance_parameter_updates"] = [
        {"instance": "MN0", "parameters": {"l": "40n"}}
    ]
    with pytest.raises(ValidationError, match="instances added by the topology_delta"):
        TaskSpec.model_validate(payload)


def test_generic_atomic_candidate_set_is_instance_only_and_ordered() -> None:
    payload = _tune_payload()
    payload["instance_parameter_space"] = []
    payload["candidate_set"] = {
        "source": {"id": "manual-shortlist"},
        "candidates": [
            {
                "id": "wide",
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"w": "3u"}}
                ],
            },
            {
                "id": "narrow",
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"w": "1u"}}
                ],
            },
        ],
    }
    task = TaskSpec.model_validate(payload)

    assert [
        candidate.atomic_candidate_id
        for candidate in TaskExecutor._candidate_inputs(task)
    ] == ["wide", "narrow"]

    payload["candidate_set"]["candidates"][0]["parameters"] = {
        "device_width_um": 3.0
    }
    payload["candidate_set"]["candidates"][1]["parameters"] = {
        "device_width_um": 1.0
    }
    with pytest.raises(
        ValidationError,
        match="only instance_parameter_updates and typed testbench_overrides",
    ):
        TaskSpec.model_validate(payload)


def _joint_candidate_payload() -> dict:
    payload = _tune_payload(max_iterations=2)
    payload["instance_parameter_space"] = []
    payload["candidate_set"] = {
        "source": {"id": "joint-oa-testbench-shortlist"},
        "candidates": [
            {
                "id": "low-bias-light-load",
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"w": "1u"}}
                ],
                "testbench_overrides": {
                    "sources": {
                        "VIN_SRC": {"dc_value": 0.40, "ac_magnitude": 1.0}
                    },
                    "loads": {"CL0": 1e-15},
                },
            },
            {
                "id": "high-bias-heavy-load",
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"w": "2u"}}
                ],
                "testbench_overrides": {
                    "sources": {
                        "VIN_SRC": {"dc_value": 0.50, "ac_magnitude": 1.0}
                    },
                    "loads": {"CL0": 2e-15},
                },
            },
        ],
    }
    return payload


def test_generic_atomic_candidate_accepts_joint_oa_and_testbench_values() -> None:
    task = TaskSpec.model_validate(_joint_candidate_payload())
    inputs = TaskExecutor._candidate_inputs(task)

    assert [item.atomic_candidate_id for item in inputs] == [
        "low-bias-light-load",
        "high-bias-heavy-load",
    ]
    assert inputs[0].instance_parameters == {"MN0": {"w": "1u"}}
    assert inputs[0].testbench_overrides is not None
    assert inputs[0].testbench_overrides.loads == {"CL0": pytest.approx(1e-15)}
    assert (
        inputs[0].testbench_override_evidence_source
        is EvidenceSource.USER_INPUT
    )


def _add_unknown_source_to_all_candidates(payload: dict) -> None:
    for candidate in payload["candidate_set"]["candidates"]:
        candidate["testbench_overrides"]["sources"]["UNKNOWN"] = {
            "dc_value": 0.4
        }


def _add_unknown_load_to_all_candidates(payload: dict) -> None:
    for candidate in payload["candidate_set"]["candidates"]:
        candidate["testbench_overrides"]["loads"]["UNKNOWN"] = 1e-15


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            _add_unknown_source_to_all_candidates,
            "unknown sources",
        ),
        (
            _add_unknown_load_to_all_candidates,
            "unknown loads",
        ),
        (
            lambda payload: payload["candidate_set"]["candidates"][1][
                "testbench_overrides"
            ]["sources"]["VIN_SRC"].pop("ac_magnitude"),
            "same semantic, raw, and testbench fields",
        ),
        (
            lambda payload: payload["candidate_set"]["candidates"][0][
                "testbench_overrides"
            ]["sources"]["VIN_SRC"].update({"ac_magnitude": 0.0}),
            "nonzero source ac_magnitude",
        ),
    ],
)
def test_generic_atomic_candidate_rejects_invalid_testbench_overrides(
    mutate, message: str
) -> None:
    payload = _joint_candidate_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(payload)


def test_candidate_testbench_overrides_are_existing_schematic_only() -> None:
    payload = {
        "id": "unsupported-testbench-candidate",
        "operation": "design.tune",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "analysis": "ac",
        "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
        "candidate_set": {
            "candidates": [
                {
                    "id": "candidate-1",
                    "parameters": {"device_width_um": 1.0},
                    "testbench_overrides": {
                        "sources": {"VIN": {"dc_value": 0.4}}
                    },
                }
            ]
        },
        "constraints": [
            {"metric": "gain_db", "relation": ">=", "value": 1.0}
        ],
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
    }

    with pytest.raises(
        ValidationError,
        match="testbench_overrides require existing_schematic tuning",
    ):
        TaskSpec.model_validate(payload)


def test_generic_contract_rejects_raw_fields_and_ac_without_excitation() -> None:
    raw = _generic_spec()
    raw["raw_spectre"] = "alter MN0 w=99u"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GenericOaSimulationSpec.model_validate(raw)

    no_ac = _generic_spec()
    for source in no_ac["sources"]:
        source["ac_magnitude"] = 0.0
    settings = GenericOaSimulationSpec.model_validate(no_ac)
    with pytest.raises(ValueError, match="nonzero source ac_magnitude"):
        settings.validate_analysis("ac")


@pytest.mark.parametrize(
    ("analysis", "sweep_name", "sweep"),
    [
        (
            "transient",
            "linearity_sweep",
            {
                "frequency_hz": 1e6,
                "amplitudes_v": [1e-3, 2e-3],
            },
        ),
        (
            "noise",
            "noise_sweep",
            {"start_hz": 1e3, "stop_hz": 1e8},
        ),
    ],
)
def test_generic_existing_schematic_accepts_single_dynamic_analysis(
    analysis: str,
    sweep_name: str,
    sweep: dict,
) -> None:
    payload = _task().model_dump(mode="json")
    payload["id"] = f"generic-{analysis}"
    payload["analysis"] = analysis
    payload["ac_sweep"] = None
    payload[sweep_name] = sweep
    payload["generic_simulation"]["dynamic_analysis"] = {
        "stimulus_source": "VIN_SRC",
        "power_source": "VDD_SRC",
    }
    payload["design_context"]["required_analyses"] = [analysis]
    payload["constraints"] = [
        {"metric": "output_dc_v", "relation": ">=", "value": 0.1}
    ]

    task = TaskSpec.model_validate(payload)

    assert task.resolved_analysis().value == analysis


def test_generic_testbench_renderer_emits_only_typed_dc_ac_surface() -> None:
    deck = render_generic_oa_testbench(
        GenericOaSimulationSpec.model_validate(_generic_spec()),
        analysis="ac",
        remote_netlist_path="/data/xum/run/netlist",
        model_configuration='include "/data/model.scs" section=top_tt',
        ac_sweep={"start_hz": 1e2, "stop_hz": 1e8, "points_per_decade": 10},
    )

    assert 'include "/data/xum/run/netlist"' in deck
    model_index = deck.index('include "/data/model.scs" section=top_tt')
    netlist_index = deck.index('include "/data/xum/run/netlist"')
    assert deck.index("simulator lang=spectre", model_index) < netlist_index
    assert deck.count("simulator lang=spectre") == 2
    assert "VIN_SRC (IN 0) vsource dc=0.45 mag=1 phase=0 type=dc" in deck
    assert "CL0 (OUT 0) capacitor c=1e-15" in deck
    assert "ac ac start=100 stop=100000000 dec=10" in deck
    assert "MN0:ids" in deck
    assert "alter" not in deck


def test_generic_testbench_renderer_supports_typed_transient_and_noise() -> None:
    raw = _generic_spec()
    raw["dynamic_analysis"] = {
        "stimulus_source": "VIN_SRC",
        "power_source": "VDD_SRC",
    }
    settings = GenericOaSimulationSpec.model_validate(raw)
    linearity = {
        "frequency_hz": 1e6,
        "amplitudes_v": [1e-3, 2e-3],
        "settling_cycles": 2,
        "measurement_cycles": 4,
        "points_per_cycle": 64,
        "max_harmonic": 5,
        "compression_db": 1.0,
    }
    transient = render_generic_oa_testbench(
        settings,
        analysis="transient",
        remote_netlist_path="/data/xum/run/netlist",
        model_configuration='include "/data/model.scs" section=top_tt',
        linearity_sweep=linearity,
    )
    noise = render_generic_oa_testbench(
        settings,
        analysis="noise",
        remote_netlist_path="/data/xum/run/netlist",
        model_configuration='include "/data/model.scs" section=top_tt',
        noise_sweep={
            "start_hz": 1e3,
            "stop_hz": 1e8,
            "points_per_decade": 10,
        },
    )

    assert "type=sine" in transient
    assert "sweep param=vda_stimulus_amplitude values=[0.001 0.002]" in transient
    assert "VDD_SRC:p" in transient
    assert "noise (OUT 0) noise start=1000 stop=100000000 dec=10" in noise
    assert "iprobe=VIN_SRC" in noise


def test_generic_linearity_extraction_uses_declared_differential_expressions() -> None:
    raw = _generic_spec()
    raw["dynamic_analysis"] = {
        "stimulus_source": "VIN_SRC",
        "power_source": "VDD_SRC",
    }
    settings = GenericOaSimulationSpec.model_validate(raw)
    sweep = {
        "frequency_hz": 1e3,
        "amplitudes_v": [0.01, 0.02],
        "settling_cycles": 1,
        "measurement_cycles": 2,
        "points_per_cycle": 64,
        "max_harmonic": 5,
        "compression_db": 1.0,
    }
    sample_count = 3 * 64 + 2
    time_s = [index / (1e3 * 64) for index in range(sample_count)]

    def point(amplitude: float) -> dict:
        input_signal = [
            0.45 + amplitude * math.sin(2 * math.pi * 1e3 * time)
            for time in time_s
        ]
        output_signal = [
            0.5 - 10.0 * (value - 0.45) for value in input_signal
        ]
        return {
            "time": time_s,
            "IN": input_signal,
            "OUT": output_signal,
            "VDD_SRC:p": [-100e-6] * sample_count,
        }

    metrics, diagnostics = bridge_worker._generic_linearity_metrics_from_result(
        {"sweep_points": {1: point(0.01), 2: point(0.02)}},
        settings,
        sweep,
    )

    assert metrics["small_signal_gain_v_per_v"] == pytest.approx(10.0, rel=1e-3)
    assert metrics["small_signal_supply_power_uw"] == pytest.approx(90.0)
    assert diagnostics["sweep_point_count"] == 2


def test_generic_si_parser_matches_topology_and_bound_parameters() -> None:
    settings = GenericOaSimulationSpec.model_validate(_generic_spec())
    text = "MN0 (OUT IN VSS VSS) nch_lvt_mac w=1u l=30n\n"

    parsed = bridge_worker._parse_existing_schematic_netlist(
        text,
        _raw_schematic(),
        settings,
    )

    assert parsed["topology_consistency"] == "matched"
    assert parsed["parameter_consistency"] == "matched"
    assert len(parsed["parameter_bindings"]) == 2

    with pytest.raises(RuntimeError, match="OA/si parameter mismatch"):
        bridge_worker._parse_existing_schematic_netlist(
            text.replace("w=1u", "w=2u"),
            _raw_schematic(),
            settings,
        )


def test_generic_si_parser_rechecks_discovered_derived_callbacks() -> None:
    raw_schematic = _raw_schematic()
    raw_schematic["instances"][0]["params"].update(
        {"Wfg": "1u", "ad": "5e-14", "as": "5e-14"}
    )
    raw_settings = _generic_spec()
    raw_settings["netlist_parameter_bindings"] = [
        {
            "instance": "MN0",
            "oa_parameter": "Wfg",
            "netlist_parameter": "w",
            "derived_callbacks": [
                {"oa_parameter": "ad", "netlist_parameter": "ad"},
                {"oa_parameter": "as", "netlist_parameter": "as"},
            ],
            "discovery_source": {
                "discovery_task_id": "binding-discovery",
                "discovery_plan_token": "0123456789abcdef",
                "discovery_task_sha256": "a" * 64,
                "discovery_run_sha256": "b" * 64,
                "classification": (
                    "direct_literal_binding_with_derived_callbacks"
                ),
                "topology_sha256": "c" * 64,
                "complete_cdf_sha256": "d" * 64,
            },
        }
    ]
    settings = GenericOaSimulationSpec.model_validate(raw_settings)
    netlist = (
        "MN0 (OUT IN VSS VSS) nch_lvt_mac "
        "w=1u l=30n ad=5e-14 as=5e-14\n"
    )

    parsed = bridge_worker._parse_existing_schematic_netlist(
        netlist,
        raw_schematic,
        settings,
    )

    assert parsed["derived_callback_consistency"] == "matched"
    assert [
        item["oa_parameter"] for item in parsed["derived_callback_bindings"]
    ] == ["ad", "as"]
    assert settings.netlist_parameter_bindings[0].discovery_source is not None

    with pytest.raises(RuntimeError, match="derived callback mismatch"):
        bridge_worker._parse_existing_schematic_netlist(
            netlist.replace("ad=5e-14", "ad=6e-14"),
            raw_schematic,
            settings,
        )

    with pytest.raises(RuntimeError, match="missing derived callback parameter"):
        bridge_worker._parse_existing_schematic_netlist(
            netlist.replace(" ad=5e-14", ""),
            raw_schematic,
            settings,
        )

    duplicate = _generic_spec()
    duplicate["netlist_parameter_bindings"] = [
        {
            "instance": "MN0",
            "oa_parameter": "w",
            "netlist_parameter": "w",
            "derived_callbacks": [
                {"oa_parameter": "ad", "netlist_parameter": "ad"},
                {"oa_parameter": "ad", "netlist_parameter": "as"},
            ],
        }
    ]
    with pytest.raises(ValidationError, match="repeats a derived OA callback"):
        GenericOaSimulationSpec.model_validate(duplicate)


def test_generic_si_parser_rejects_unknown_testbench_node() -> None:
    raw = _generic_spec()
    raw["sources"][2]["positive_node"] = "MISSING"
    with pytest.raises(RuntimeError, match="nodes absent from OA schematic"):
        bridge_worker._parse_existing_schematic_netlist(
            "MN0 (OUT IN VSS VSS) nch_lvt_mac w=1u l=30n\n",
            _raw_schematic(),
            GenericOaSimulationSpec.model_validate(raw),
        )


def test_generic_si_parser_proves_explicit_one_level_hierarchy() -> None:
    top = {
        "instances": [
            {
                "name": "XAMP",
                "lib": "vda_test",
                "cell": "vda_child",
                "view": "symbol",
                "params": {"scale": "2"},
                "terms": {
                    "IN": "IN",
                    "OUT": "OUT",
                    "VDD": "VDD",
                    "VSS": "VSS",
                },
            }
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }
    child = _raw_schematic()
    raw_settings = _generic_spec()
    raw_settings["operating_point_metrics"] = []
    raw_settings["netlist_parameter_bindings"] = [
        {
            "instance": "XAMP",
            "oa_parameter": "scale",
            "netlist_parameter": "scale",
        },
        {
            "instance": "XAMP/MN0",
            "oa_parameter": "w",
            "netlist_parameter": "w",
        },
    ]
    raw_settings["hierarchy_bindings"] = [
        {
            "instance": "XAMP",
            "library": "vda_test",
            "cell": "vda_child",
            "subcircuit": "vda_child",
            "terminal_order": ["IN", "OUT", "VDD", "VSS"],
        }
    ]
    netlist = """
subckt vda_child IN OUT VDD VSS
    MN0 (OUT IN VSS VSS) nch_lvt_mac w=1u l=30n \\
        multi=1 nf=1
ends vda_child
XAMP (IN OUT VDD VSS) vda_child scale=2
"""

    child_summary = {
        "topology": {
            "instances": [
                {
                    "name": "MN0",
                    "library": "tsmcN28",
                    "cell": "nch_lvt_mac",
                    "view": "symbol",
                    "terminals": {
                        "D": "OUT",
                        "G": "IN",
                        "S": "VSS",
                        "B": "VSS",
                    },
                }
            ],
            "nets": [
                {"name": name} for name in ("IN", "OUT", "VDD", "VSS")
            ],
            "pins": [
                {"name": name, "net": name}
                for name in ("IN", "OUT", "VDD", "VSS")
            ],
        },
        "placement": {"sha256": "1" * 64},
    }
    parsed = bridge_worker._parse_existing_schematic_netlist(
        netlist,
        top,
        GenericOaSimulationSpec.model_validate(raw_settings),
        {("vda_test", "vda_child"): child},
        {("vda_test", "vda_child"): child_summary},
    )

    assert parsed["flat_primitive_scope"] is False
    assert parsed["hierarchy_scope"] == "explicit_one_level_primitive_children"
    assert parsed["instances"][0]["kind"] == "subcircuit"
    assert parsed["hierarchy_bindings"][0]["subcircuit"] == "vda_child"
    assert parsed["hierarchy_bindings"][0]["child_topology_source"] == (
        "bridge_readback"
    )
    assert parsed["hierarchy_bindings"][0]["child_placement_sha256"] == "1" * 64
    assert parsed["parameter_bindings"][0]["oa_value"] == "2"
    assert parsed["parameter_bindings"][1] == {
        "instance": "XAMP/MN0",
        "oa_parameter": "w",
        "netlist_parameter": "w",
        "oa_value": "1u",
        "netlist_value": "1u",
    }

    with pytest.raises(RuntimeError, match="OA/si parameter mismatch"):
        bridge_worker._parse_existing_schematic_netlist(
            netlist.replace("w=1u", "w=2u"),
            top,
            GenericOaSimulationSpec.model_validate(raw_settings),
            {("vda_test", "vda_child"): child},
        )

    with pytest.raises(RuntimeError, match="node mismatch"):
        bridge_worker._parse_existing_schematic_netlist(
            netlist.replace(
                "MN0 (OUT IN VSS VSS)",
                "MN0 (OUT VDD VSS VSS)",
            ),
            top,
            GenericOaSimulationSpec.model_validate(raw_settings),
            {("vda_test", "vda_child"): child},
        )


def test_generic_si_parser_refuses_unbound_hierarchical_master() -> None:
    top = {
        "instances": [
            {
                "name": "XAMP",
                "lib": "vda_test",
                "cell": "vda_child",
                "view": "symbol",
                "params": {"scale": "2"},
                "terms": {"IN": "IN", "OUT": "OUT"},
            }
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }
    raw_settings = _generic_spec()
    raw_settings["operating_point_metrics"] = []
    raw_settings["netlist_parameter_bindings"] = [
        {
            "instance": "XAMP",
            "oa_parameter": "scale",
            "netlist_parameter": "scale",
        }
    ]

    with pytest.raises(RuntimeError, match="no primitive contract or explicit"):
        bridge_worker._parse_existing_schematic_netlist(
            "XAMP (IN OUT) vda_child scale=2\n",
            top,
            GenericOaSimulationSpec.model_validate(raw_settings),
        )


class _GenericAdapter:
    name = "generic-fixture"

    def probe(self, profile):
        return AdapterResult(
            data={"connected": True},
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def inspect_schematic(self, task):
        return AdapterResult(
            data=_inspection(),
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        assert parameters == {}
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {
                    "output_dc_v": 0.5,
                    "low_frequency_gain_db": 20.0,
                },
                "metric_sources": {
                    "output_dc_v": "eda_result",
                    "low_frequency_gain_db": "eda_result",
                },
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _GenericTuningAdapter(_GenericAdapter):
    def __init__(self) -> None:
        self.width = "1u"
        self.applied_widths: list[str] = []
        self.simulated_widths: list[str] = []
        self.interrupt_width: str | None = None

    def _inspection(self) -> dict:
        data = _inspection()
        data["instance_parameters"]["MN0"]["w"] = self.width
        data["bridge_schematic"]["instances"][0]["params"]["w"] = self.width
        return data

    def inspect_schematic(self, task):
        return AdapterResult(
            data=self._inspection(),
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def apply_parameters(self, task, parameters):
        assert parameters == {}
        requested = {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }
        assert set(requested) == {"MN0"}
        before = self.width
        self.width = requested["MN0"]["w"]
        self.applied_widths.append(self.width)
        return AdapterResult(
            data={
                "requested_instance_parameters": requested,
                "applied_instance_parameters": requested,
                "before_instance_parameters": {"MN0": {"w": before}},
                "confirmed_instance_parameters": requested,
                "readback": self._inspection(),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def verify_parameters(self, task, expected):
        if expected != {"MN0": {"w": self.width}}:
            raise RuntimeError("independent generic width readback mismatch")
        data = self._inspection()
        data["confirmed_instance_parameters"] = expected
        return AdapterResult(
            data=data,
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        assert parameters == {}
        self.simulated_widths.append(self.width)
        if self.interrupt_width == self.width:
            self.interrupt_width = None
            raise AdapterInterrupted("transport reset after generic OA staging")
        gain_db = {"1u": 10.0, "2u": 20.0, "3u": 15.0}[self.width]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": gain_db},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _JointOaTestbenchAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.simulated_conditions: list[tuple[str, float, float]] = []

    def simulate(self, task, parameters):
        assert parameters == {}
        assert task.generic_simulation is not None
        vin = next(
            source
            for source in task.generic_simulation.sources
            if source.name == "VIN_SRC"
        )
        load = next(
            item for item in task.generic_simulation.loads if item.name == "CL0"
        )
        condition = (self.width, vin.dc_value, load.value)
        self.simulated_conditions.append(condition)
        if self.interrupt_width == self.width:
            self.interrupt_width = None
            raise AdapterInterrupted("transport reset after joint candidate staging")
        gain_db = {
            ("1u", 0.40, 1e-15): 10.0,
            ("2u", 0.50, 2e-15): 25.0,
        }[condition]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": gain_db},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _StagedGenericTuningAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.simulated_stages: list[tuple[str, str]] = []
        self.interrupt_stage: tuple[str, str] | None = None

    def simulate(self, task, parameters):
        assert parameters == {}
        analysis = task.resolved_analysis().value
        point = (self.width, analysis)
        self.simulated_stages.append(point)
        if self.interrupt_stage == point:
            self.interrupt_stage = None
            raise AdapterInterrupted("injected staged-analysis interruption")
        metrics = (
            {"output_dc_v": {"1u": 0.2, "2u": 0.5, "3u": 0.6}[self.width]}
            if analysis == "dc"
            else {
                "output_dc_v": {"1u": 0.2, "2u": 0.5, "3u": 0.6}[
                    self.width
                ],
                "low_frequency_gain_db": {
                    "1u": 10.0,
                    "2u": 20.0,
                    "3u": 15.0,
                }[self.width],
            }
        )
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": metrics,
                "metric_sources": {name: "eda_result" for name in metrics},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _SharedStagedGenericTuningAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls: list[str] = []
        self.scalar_simulation_calls = 0
        self.interrupt_width: str | None = None

    def simulate(self, task, parameters):
        self.scalar_simulation_calls += 1
        raise AssertionError("shared-netlist mode must not call scalar simulate")

    def simulate_analysis_stages(self, task, parameters):
        assert parameters == {}
        self.batch_calls.append(self.width)
        if self.interrupt_width == self.width:
            self.interrupt_width = None
            raise AdapterInterrupted("injected shared-netlist batch interruption")
        netlist = {
            "source": "eda_result",
            "remote_path": "/data/xum/shared/input.scs",
            "sha256": "a" * 64,
        }
        output_dc_v = {"1u": 0.2, "2u": 0.5, "3u": 0.6}[self.width]
        stage_results = []
        terminated_after_stage = None
        for stage in task.analysis_stages:
            metrics = {"output_dc_v": output_dc_v}
            if stage.analysis.value == "ac":
                metrics["low_frequency_gain_db"] = {
                    "1u": 10.0,
                    "2u": 20.0,
                    "3u": 15.0,
                }[self.width]
            stage_results.append(
                {
                    "stage_id": stage.id,
                    "analysis": stage.analysis.value,
                    "result": {
                        "parameters": {},
                        "metrics": metrics,
                        "metric_sources": {
                            name: "eda_result" for name in metrics
                        },
                        "analysis_complete": True,
                        "evidence": {
                            "schematic_readback": {
                                "source": "bridge_readback",
                                "topology_sha256": "b" * 64,
                            },
                            "netlist": netlist,
                        },
                    },
                }
            )
            if stage.id == "bias" and output_dc_v < 0.4:
                terminated_after_stage = "bias"
                break
        return AdapterResult(
            data={
                "stage_results": stage_results,
                "terminated_after_stage": terminated_after_stage,
                "shared_netlist": {
                    "source": "software_inference",
                    "remote_path": netlist["remote_path"],
                    "sha256": netlist["sha256"],
                    "netlist_generation_count": 1,
                    "schematic_readback_count": 1,
                },
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _WinnerVerificationAdapter(_GenericTuningAdapter):
    def __init__(self, *, verification_passes: bool) -> None:
        super().__init__()
        self.verification_passes = verification_passes
        self.winner_batch_widths: list[str] = []

    def simulate_analysis_stages(self, task, parameters):
        assert parameters == {}
        assert self.width == "2u"
        self.winner_batch_widths.append(self.width)
        netlist = {
            "source": "eda_result",
            "remote_path": "/data/xum/shared/winner.scs",
            "sha256": "e" * 64,
        }
        stage_results = []
        for stage in task.analysis_stages:
            condition_results = []
            for condition in task.operating_conditions:
                effective_vdd = (
                    condition.vdd_v
                    if condition.vdd_v is not None
                    else 0.9
                )
                if stage.analysis.value == "dc":
                    metrics = {"output_dc_v": 0.5}
                else:
                    gain = (
                        19.0
                        if condition.name == "tt_27c_0p90v"
                        else (18.5 if self.verification_passes else 17.0)
                    )
                    metrics = {"low_frequency_gain_db": gain}
                condition_results.append(
                    {
                        "condition": condition.model_dump(mode="json"),
                        "result": {
                            "parameters": {"vdd_v": effective_vdd},
                            "metrics": metrics,
                            "metric_sources": {
                                name: "eda_result" for name in metrics
                            },
                            "analysis_complete": True,
                            "evidence": {
                                "schematic_readback": {
                                    "source": "bridge_readback",
                                    "topology_sha256": "f" * 64,
                                },
                                "netlist": netlist,
                            },
                        },
                    }
                )
            stage_results.append(
                {
                    "stage_id": stage.id,
                    "analysis": stage.analysis.value,
                    "result": {
                        "parameters": {},
                        "metrics": {},
                        "metric_sources": {},
                        "analysis_complete": True,
                        "operating_condition_results": condition_results,
                        "evidence": {
                            "schematic_readback": {
                                "source": "bridge_readback",
                                "topology_sha256": "f" * 64,
                            },
                            "netlist": netlist,
                        },
                    },
                }
            )
        return AdapterResult(
            data={
                "stage_results": stage_results,
                "terminated_after_stage": None,
                "shared_netlist": {
                    "source": "software_inference",
                    "remote_path": netlist["remote_path"],
                    "sha256": netlist["sha256"],
                    "netlist_generation_count": 1,
                    "schematic_readback_count": 1,
                },
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _TopologyRefinementAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.rvt = False
        self.interrupt_variant_width: tuple[str, str] | None = None
        self.simulated_points: list[tuple[str, str]] = []
        self.gain_db = {
            ("lvt", "1u"): 10.0,
            ("lvt", "2u"): 20.0,
            ("rvt", "1u"): 30.0,
            ("rvt", "2u"): 25.0,
        }

    def _inspection(self) -> dict:
        data = super()._inspection()
        cell = "nch_rvt_mac" if self.rvt else "nch_lvt_mac"
        data["instances"][0]["cell"] = cell
        data["bridge_schematic"]["instances"][0]["cell"] = cell
        return data

    def transform_schematic(self, task):
        assert task.topology_delta is not None
        expected_rvt = task.topology_delta.direction == "inverse"
        if self.rvt != expected_rvt:
            raise RuntimeError("topology transform input variant mismatch")
        self.rvt = not self.rvt
        return AdapterResult(
            data={
                "contract_id": task.topology_delta.contract.id,
                "direction": task.topology_delta.direction,
                "actual_output_topology_sha256": (
                    task.topology_delta.contract.expected_after_sha256
                    if self.rvt
                    else task.topology_delta.contract.expected_before_sha256
                ),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        variant = "rvt" if self.rvt else "lvt"
        point = (variant, self.width)
        self.simulated_points.append(point)
        if self.interrupt_variant_width == point:
            self.interrupt_variant_width = None
            raise AdapterInterrupted("injected topology refinement interruption")
        gain_db = self.gain_db[point]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": gain_db},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _MultiTopologyRefinementAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.variant = "lvt"
        self.transforms: list[tuple[str, str]] = []
        self.simulated_points: list[tuple[str, str]] = []
        self.interrupt_point: tuple[str, str] | None = None
        self.gain_db = {
            ("lvt", "1u"): 10.0,
            ("lvt", "2u"): 20.0,
            ("rvt", "1u"): 30.0,
            ("rvt", "2u"): 25.0,
            ("hvt", "1u"): 22.0,
            ("hvt", "2u"): 35.0,
        }

    def _inspection(self) -> dict:
        data = super()._inspection()
        cell = {
            "lvt": "nch_lvt_mac",
            "rvt": "nch_rvt_mac",
            "hvt": "nch_hvt_mac",
        }[self.variant]
        data["instances"][0]["cell"] = cell
        data["bridge_schematic"]["instances"][0]["cell"] = cell
        return data

    def transform_schematic(self, task):
        assert task.topology_delta is not None
        contract_id = task.topology_delta.contract.id
        target_variant = "rvt" if "rvt" in contract_id else "hvt"
        direction = task.topology_delta.direction
        if direction == "forward":
            assert self.variant == "lvt"
            self.variant = target_variant
        else:
            assert self.variant == target_variant
            self.variant = "lvt"
        self.transforms.append((target_variant, direction))
        return AdapterResult(
            data={
                "contract_id": contract_id,
                "direction": direction,
                "actual_output_topology_sha256": (
                    task.topology_delta.contract.expected_after_sha256
                    if direction == "forward"
                    else task.topology_delta.contract.expected_before_sha256
                ),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        point = (self.variant, self.width)
        self.simulated_points.append(point)
        if self.interrupt_point == point:
            self.interrupt_point = None
            raise AdapterInterrupted("injected multi-topology interruption")
        gain_db = self.gain_db[point]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": gain_db},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _StagedTopologyRefinementAdapter(_TopologyRefinementAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.simulated_stages: list[tuple[str, str, str]] = []
        self.interrupt_stage: tuple[str, str, str] | None = None

    def simulate(self, task, parameters):
        assert parameters == {}
        variant = "rvt" if self.rvt else "lvt"
        analysis = task.resolved_analysis().value
        point = (variant, self.width, analysis)
        self.simulated_stages.append(point)
        if self.interrupt_stage == point:
            self.interrupt_stage = None
            raise AdapterInterrupted("injected staged topology interruption")
        output_dc_v = {
            ("lvt", "1u"): 0.2,
            ("lvt", "2u"): 0.5,
            ("rvt", "1u"): 0.55,
            ("rvt", "2u"): 0.6,
        }[(variant, self.width)]
        metrics = {"output_dc_v": output_dc_v}
        if analysis == "ac":
            metrics["low_frequency_gain_db"] = self.gain_db[
                (variant, self.width)
            ]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": metrics,
                "metric_sources": {name: "eda_result" for name in metrics},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _SharedStagedTopologyRefinementAdapter(_TopologyRefinementAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls: list[tuple[str, str]] = []

    def simulate(self, task, parameters):
        raise AssertionError("shared-netlist topology mode must use one batch")

    def simulate_analysis_stages(self, task, parameters):
        assert parameters == {}
        variant = "rvt" if self.rvt else "lvt"
        point = (variant, self.width)
        self.batch_calls.append(point)
        output_dc_v = {
            ("lvt", "1u"): 0.2,
            ("lvt", "2u"): 0.5,
            ("rvt", "1u"): 0.55,
            ("rvt", "2u"): 0.6,
        }[point]
        netlist = {
            "source": "eda_result",
            "remote_path": f"/data/xum/shared/{variant}-{self.width}.scs",
            "sha256": ("a" if self.rvt else "b") * 64,
        }
        stage_results = []
        terminated_after_stage = None
        for stage in task.analysis_stages:
            metrics = {"output_dc_v": output_dc_v}
            if stage.analysis.value == "ac":
                metrics["low_frequency_gain_db"] = self.gain_db[point]
            stage_results.append(
                {
                    "stage_id": stage.id,
                    "analysis": stage.analysis.value,
                    "result": {
                        "metrics": metrics,
                        "metric_sources": {
                            name: "eda_result" for name in metrics
                        },
                        "analysis_complete": True,
                        "evidence": {
                            "schematic_readback": {
                                "source": "bridge_readback",
                                "topology_sha256": (
                                    "c" * 64 if self.rvt else "d" * 64
                                ),
                            },
                            "netlist": netlist,
                        },
                    },
                }
            )
            if stage.id == "bias" and output_dc_v < 0.4:
                terminated_after_stage = "bias"
                break
        return AdapterResult(
            data={
                "stage_results": stage_results,
                "terminated_after_stage": terminated_after_stage,
                "shared_netlist": {
                    "source": "software_inference",
                    "remote_path": netlist["remote_path"],
                    "sha256": netlist["sha256"],
                    "netlist_generation_count": 1,
                },
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _AddedInstanceTopologyRefinementAdapter(_GenericTuningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.degenerated = False
        self.resistance = "1K"
        self.simulated_points: list[tuple[str, str, str | None]] = []

    def _inspection(self) -> dict:
        data = super()._inspection()
        if not self.degenerated:
            return data
        data["instances"][0]["terminals"]["S"] = "NSRC"
        data["bridge_schematic"]["instances"][0]["terms"]["S"] = "NSRC"
        data["instances"].append(
            {
                "name": "RS0",
                "library": "analogLib",
                "cell": "res",
                "view": "symbol",
                "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
            }
        )
        data["instance_parameters"]["RS0"] = {"r": self.resistance}
        data["bridge_schematic"]["instances"].append(
            {
                "name": "RS0",
                "lib": "analogLib",
                "cell": "res",
                "view": "symbol",
                "params": {"r": self.resistance},
                "terms": {"PLUS": "NSRC", "MINUS": "VSS"},
            }
        )
        data["nets"].append("NSRC")
        data["bridge_schematic"]["nets"]["NSRC"] = {}
        return data

    def apply_parameters(self, task, parameters):
        assert parameters == {}
        requested = {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }
        if "RS0" in requested and not self.degenerated:
            raise RuntimeError("RS0 is absent from the baseline topology")
        before = {"MN0": {"w": self.width}}
        if self.degenerated:
            before["RS0"] = {"r": self.resistance}
        if "MN0" in requested:
            self.width = requested["MN0"]["w"]
        if "RS0" in requested:
            self.resistance = requested["RS0"]["r"]
        return AdapterResult(
            data={
                "requested_instance_parameters": requested,
                "applied_instance_parameters": requested,
                "before_instance_parameters": before,
                "confirmed_instance_parameters": requested,
                "readback": self._inspection(),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def verify_parameters(self, task, expected):
        actual: dict[str, dict[str, str]] = {}
        if "MN0" in expected:
            actual["MN0"] = {"w": self.width}
        if "RS0" in expected:
            if not self.degenerated:
                raise RuntimeError("RS0 is absent from the current topology")
            actual["RS0"] = {"r": self.resistance}
        if actual != expected:
            raise RuntimeError("added-instance parameter readback mismatch")
        data = self._inspection()
        data["confirmed_instance_parameters"] = actual
        return AdapterResult(
            data=data,
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def transform_schematic(self, task):
        assert task.topology_delta is not None
        expected_degenerated = task.topology_delta.direction == "inverse"
        if self.degenerated != expected_degenerated:
            raise RuntimeError("source-degeneration transform input mismatch")
        self.degenerated = not self.degenerated
        if self.degenerated:
            self.resistance = "1K"
        return AdapterResult(
            data={
                "contract_id": task.topology_delta.contract.id,
                "direction": task.topology_delta.direction,
                "actual_output_topology_sha256": (
                    task.topology_delta.contract.expected_after_sha256
                    if self.degenerated
                    else task.topology_delta.contract.expected_before_sha256
                ),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        assert parameters == {}
        variant = "degenerated" if self.degenerated else "baseline"
        resistance = self.resistance if self.degenerated else None
        self.simulated_points.append((variant, self.width, resistance))
        gain_db = {
            ("baseline", "1u"): 10.0,
            ("baseline", "2u"): 20.0,
            ("degenerated", "1u"): 30.0,
            ("degenerated", "2u"): 25.0,
        }[(variant, self.width)]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": gain_db},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


class _MultiFieldGenericAdapter(_GenericAdapter):
    def __init__(self) -> None:
        self.values = {"w": "1u", "l": "30n"}

    def _inspection(self) -> dict:
        data = _inspection()
        data["instance_parameters"]["MN0"].update(self.values)
        data["bridge_schematic"]["instances"][0]["params"].update(self.values)
        return data

    def inspect_schematic(self, task):
        return AdapterResult(
            data=self._inspection(),
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def apply_parameters(self, task, parameters):
        assert parameters == {}
        requested = {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }
        before = dict(self.values)
        self.values.update(requested["MN0"])
        confirmed = {"MN0": dict(requested["MN0"])}
        return AdapterResult(
            data={
                "requested_instance_parameters": confirmed,
                "applied_instance_parameters": confirmed,
                "before_instance_parameters": {"MN0": before},
                "confirmed_instance_parameters": confirmed,
                "readback": self._inspection(),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def verify_parameters(self, task, expected):
        actual = {
            "MN0": {
                name: self.values[name]
                for name in expected.get("MN0", {})
            }
        }
        if actual != expected:
            raise RuntimeError("multi-field readback mismatch")
        data = self._inspection()
        data["confirmed_instance_parameters"] = actual
        return AdapterResult(
            data=data,
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(self, task, parameters):
        score = {
            ("1u", "40n"): 15.0,
            ("2u", "30n"): 25.0,
        }[(self.values["w"], self.values["l"])]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_db": score},
                "metric_sources": {"low_frequency_gain_db": "eda_result"},
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


def test_executor_keeps_context_binding_before_generic_simulation() -> None:
    task = _task()
    plan = build_plan(task)
    record = TaskExecutor(_GenericAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    actions = [item.action for item in record.actions]
    assert actions.index("design.context.bind") < actions.index(
        "simulation.candidate.1"
    )
    candidate = record.candidates[0]
    assert candidate.metric_sources["low_frequency_gain_db"] is EvidenceSource.EDA_RESULT


def test_generic_tuning_commits_best_raw_instance_candidate(tmp_path) -> None:
    task = _tune_task()
    plan = build_plan(task)
    adapter = _GenericTuningAdapter()
    checkpoint_path = tmp_path / "generic-tune.checkpoint.json"

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.simulated_widths == ["1u", "2u", "3u"]
    assert adapter.width == "2u"
    assert adapter.applied_widths == ["1u", "2u", "3u", "2u"]
    assert record.search_audit is not None
    assert record.search_audit.selection_scope is (
        SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
    )
    assert load_execution_checkpoint(checkpoint_path).complete is True
    assert next(
        action
        for action in record.actions
        if action.action == "parameters.apply.best"
    ).evidence_source is EvidenceSource.BRIDGE_READBACK


def test_generic_tuning_keeps_joint_oa_and_testbench_candidate_atomic(
    tmp_path,
) -> None:
    task = TaskSpec.model_validate(_joint_candidate_payload())
    plan = build_plan(task)
    adapter = _JointOaTestbenchAdapter()
    checkpoint_path = tmp_path / "joint-oa-testbench.checkpoint.json"

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert adapter.simulated_conditions == [
        ("1u", 0.40, 1e-15),
        ("2u", 0.50, 2e-15),
    ]
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert record.selected_testbench_overrides is not None
    assert record.selected_testbench_overrides.sources[
        "VIN_SRC"
    ].dc_value == pytest.approx(0.50)
    assert record.selected_testbench_overrides.loads["CL0"] == pytest.approx(
        2e-15
    )
    assert record.selected_testbench_override_evidence_source is (
        EvidenceSource.USER_INPUT
    )
    assert record.candidates[1].testbench_override_evidence_source is (
        EvidenceSource.USER_INPUT
    )
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_joint_oa_testbench_checkpoint_resumes_exact_candidate(tmp_path) -> None:
    task = TaskSpec.model_validate(_joint_candidate_payload())
    plan = build_plan(task)
    adapter = _JointOaTestbenchAdapter()
    adapter.interrupt_width = "2u"
    checkpoint_path = tmp_path / "joint-oa-testbench-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert checkpoint.candidates[0].testbench_overrides is not None
    assert checkpoint.candidates[0].testbench_overrides.loads["CL0"] == pytest.approx(
        1e-15
    )

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.simulated_conditions == [
        ("1u", 0.40, 1e-15),
        ("2u", 0.50, 2e-15),
        ("2u", 0.50, 2e-15),
    ]
    assert resumed.selected_testbench_overrides is not None
    assert resumed.selected_testbench_overrides.loads["CL0"] == pytest.approx(
        2e-15
    )


def test_generic_tuning_objective_selects_one_atomic_multi_field_tuple() -> None:
    payload = _tune_payload(max_iterations=2)
    payload["instance_parameter_space"] = []
    payload["candidate_set"] = {
        "source": {"id": "two-field-user-shortlist"},
        "candidates": [
            {
                "id": "narrow-long",
                "instance_parameter_updates": [
                    {
                        "instance": "MN0",
                        "parameters": {"w": "1u", "l": "40n"},
                    }
                ],
            },
            {
                "id": "wide-short",
                "instance_parameter_updates": [
                    {
                        "instance": "MN0",
                        "parameters": {"w": "2u", "l": "30n"},
                    }
                ],
            },
        ],
    }
    task = TaskSpec.model_validate(payload)
    plan = build_plan(task)
    adapter = _MultiFieldGenericAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_instance_parameters == {
        "MN0": {"w": "2u", "l": "30n"}
    }
    assert adapter.values == {"w": "2u", "l": "30n"}
    assert [candidate.atomic_candidate_id for candidate in record.candidates] == [
        "narrow-long",
        "wide-short",
    ]
    assert [candidate.objective_value for candidate in record.candidates] == [
        15.0,
        25.0,
    ]


def test_generic_tuning_restores_initial_state_when_domain_is_infeasible() -> None:
    task = _tune_task(constraint_db=30.0)
    plan = build_plan(task)
    adapter = _GenericTuningAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    assert record.selected_instance_parameters is None
    assert adapter.width == "1u"
    assert adapter.applied_widths[-1] == "1u"
    assert record.search_audit is not None
    assert record.search_audit.selection_scope is (
        SelectionScope.NO_FEASIBLE_IN_DECLARED_DISCRETE_DOMAIN
    )
    restore = next(
        action for action in record.actions if action.action == "parameters.restore"
    )
    assert restore.details["confirmed_instance_parameters"] == {
        "MN0": {"w": "1u"}
    }


def test_generic_tuning_marks_budget_limited_winner_as_best_evaluated() -> None:
    task = _tune_task(max_iterations=2)
    plan = build_plan(task)
    adapter = _GenericTuningAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.simulated_widths == ["1u", "2u"]
    assert any("2 of 3 declared candidates" in note for note in record.notes)
    assert record.search_audit is not None
    assert record.search_audit.selection_scope is SelectionScope.BEST_EVALUATED


def test_generic_tuning_resumes_after_transport_interruption(tmp_path) -> None:
    task = _tune_task()
    plan = build_plan(task)
    adapter = _GenericTuningAdapter()
    adapter.interrupt_width = "2u"
    checkpoint_path = tmp_path / "generic-interrupted.checkpoint.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert adapter.width == "1u"
    assert checkpoint.next_candidate_index == 2
    assert [candidate.index for candidate in checkpoint.candidates] == [1]
    assert any(
        action.action == "simulation.candidate.2"
        and action.evidence_source is EvidenceSource.SYSTEM_EVENT
        for action in first.actions
    )

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.simulated_widths == ["1u", "2u", "2u", "3u"]
    assert adapter.width == "2u"
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_topology_close_loop_selects_and_commits_variant_and_parameters(
    tmp_path,
) -> None:
    task = TaskSpec.model_validate(_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _TopologyRefinementAdapter()
    checkpoint_path = tmp_path / "topology-close-loop.checkpoint.json"

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert record.status is RunStatus.SUCCEEDED
    assert [candidate.index for candidate in record.candidates] == [1, 2, 3, 4]
    assert [candidate.topology_variant_id for candidate in record.candidates] == [
        "lvt",
        "lvt",
        "rvt",
        "rvt",
    ]
    assert record.selected_topology_variant_id == "rvt"
    assert record.selected_topology_sha256 == (
        task.topology_refinement.topology_delta.contract.expected_after_sha256
    )
    assert record.selected_instance_parameters == {"MN0": {"w": "1u"}}
    assert adapter.rvt is True
    assert adapter.width == "1u"
    assert record.search_audit is not None
    assert record.search_audit.declared_candidate_count == 4
    assert record.search_audit.topology_variant_count == 2
    assert record.search_audit.selection_scope is (
        SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
    )
    assert checkpoint.expected_topology_variant_id == "rvt"
    assert checkpoint.pending_topology_sha256 is None
    assert checkpoint.complete is True


def test_multi_topology_close_loop_evaluates_independent_variants_and_commits() -> None:
    task = TaskSpec.model_validate(_multi_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _MultiTopologyRefinementAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert [candidate.topology_variant_id for candidate in record.candidates] == [
        "lvt",
        "lvt",
        "rvt",
        "rvt",
        "hvt",
        "hvt",
    ]
    assert adapter.simulated_points == [
        ("lvt", "1u"),
        ("lvt", "2u"),
        ("rvt", "1u"),
        ("rvt", "2u"),
        ("hvt", "1u"),
        ("hvt", "2u"),
    ]
    assert adapter.transforms == [
        ("rvt", "forward"),
        ("rvt", "inverse"),
        ("hvt", "forward"),
    ]
    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_topology_variant_id == "hvt"
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.variant == "hvt"
    assert adapter.width == "2u"
    assert record.search_audit is not None
    assert record.search_audit.topology_variant_count == 3
    assert record.search_audit.declared_candidate_count == 6
    plan_text = "\n".join(step.description for step in plan.steps)
    assert "rvt" in plan_text and "hvt" in plan_text


def test_multi_topology_can_return_from_last_variant_and_commit_earlier_one() -> None:
    task = TaskSpec.model_validate(_multi_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _MultiTopologyRefinementAdapter()
    adapter.gain_db[("rvt", "1u")] = 40.0

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_topology_variant_id == "rvt"
    assert record.selected_instance_parameters == {"MN0": {"w": "1u"}}
    assert adapter.variant == "rvt"
    assert adapter.width == "1u"
    assert adapter.transforms[-2:] == [
        ("hvt", "inverse"),
        ("rvt", "forward"),
    ]


def test_multi_topology_requires_all_alternatives_to_share_one_baseline() -> None:
    payload = _multi_topology_refinement_payload()
    payload["topology_refinement"]["alternatives"][1]["topology_delta"][
        "contract"
    ]["expected_before_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="anchored to one baseline"):
        TaskSpec.model_validate(payload)


def test_live_multi_alternative_example_plans_complete_three_topology_domain() -> None:
    task_path = (
        Path(__file__).parents[1]
        / "examples"
        / "tasks"
        / "existing-schematic-multi-alternative-close-loop.bridge.json"
    )
    task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
    plan = build_plan(task)

    assert task.target is not None
    assert task.target.cell == "vda_l5b_multi_alt_gate_001"
    assert task.limits.max_iterations == 6
    assert task.topology_refinement is not None
    assert [
        alternative.id
        for alternative in task.topology_refinement.resolved_alternatives()
    ] == ["source-degenerated", "cascode"]
    assert [step.capability for step in plan.steps if "topology-delta" in step.capability] == [
        "schematic.transform.topology-delta.forward",
        "schematic.transform.topology-delta.inverse",
        "schematic.transform.topology-delta.forward",
    ]
    assert any(
        step.capability == "results.select.topology-and-parameters"
        for step in plan.steps
    )


def test_multi_topology_checkpoint_restores_baseline_and_resumes_variant(
    tmp_path,
) -> None:
    task = TaskSpec.model_validate(_multi_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _MultiTopologyRefinementAdapter()
    adapter.interrupt_point = ("rvt", "2u")
    checkpoint_path = tmp_path / "multi-topology.checkpoint.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert adapter.variant == "lvt"
    assert checkpoint.next_candidate_index == 4
    assert [item.index for item in checkpoint.candidates] == [1, 2, 3]

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.selected_topology_variant_id == "hvt"
    assert adapter.variant == "hvt"
    assert adapter.simulated_points.count(("rvt", "2u")) == 2
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_topology_close_loop_commits_added_instance_and_its_fixed_parameter() -> None:
    task = TaskSpec.model_validate(_added_instance_refinement_payload())
    plan = build_plan(task)
    adapter = _AddedInstanceTopologyRefinementAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_topology_variant_id == "source-degenerated"
    assert record.selected_instance_parameters == {
        "MN0": {"w": "1u"},
        "RS0": {"r": "1k"},
    }
    assert adapter.degenerated is True
    assert adapter.width == "1u"
    assert adapter.resistance == "1k"
    assert adapter.simulated_points == [
        ("baseline", "1u", None),
        ("baseline", "2u", None),
        ("degenerated", "1u", "1k"),
        ("degenerated", "2u", "1k"),
    ]


def test_topology_close_loop_restores_exact_baseline_when_infeasible() -> None:
    task = TaskSpec.model_validate(
        _topology_refinement_payload(constraint_db=40.0)
    )
    plan = build_plan(task)
    adapter = _TopologyRefinementAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    assert record.selected_topology_variant_id is None
    assert record.selected_instance_parameters is None
    assert adapter.rvt is False
    assert adapter.width == "1u"
    assert record.search_audit is not None
    assert record.search_audit.selection_scope is (
        SelectionScope.NO_FEASIBLE_IN_DECLARED_DISCRETE_DOMAIN
    )
    assert any("exact initial baseline" in note for note in record.notes)


def test_topology_close_loop_inverts_delta_when_baseline_wins() -> None:
    task = TaskSpec.model_validate(_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _TopologyRefinementAdapter()
    adapter.gain_db[("lvt", "2u")] = 35.0

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_topology_variant_id == "lvt"
    assert record.selected_instance_parameters == {"MN0": {"w": "2u"}}
    assert adapter.rvt is False
    assert adapter.width == "2u"
    assert any(
        action.action == "schematic.transform.topology-delta.inverse"
        for action in record.actions
    )


def test_staged_topology_close_loop_uses_gate_and_commits_joint_winner() -> None:
    task = TaskSpec.model_validate(_staged_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _StagedTopologyRefinementAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_topology_variant_id == "rvt"
    assert record.selected_instance_parameters == {"MN0": {"w": "1u"}}
    assert adapter.rvt is True
    assert adapter.width == "1u"
    assert ("lvt", "1u", "ac") not in adapter.simulated_stages
    assert record.candidates[0].terminated_after_stage == "bias"
    assert record.search_audit is not None
    assert record.search_audit.domain_exhausted is True


def test_shared_netlist_stages_apply_across_topology_variants() -> None:
    payload = _staged_topology_refinement_payload()
    payload["analysis_stage_execution"] = "shared_netlist"
    task = TaskSpec.model_validate(payload)
    plan = build_plan(task)
    adapter = _SharedStagedTopologyRefinementAdapter()

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert adapter.batch_calls == [
        ("lvt", "1u"),
        ("lvt", "2u"),
        ("rvt", "1u"),
        ("rvt", "2u"),
    ]
    assert record.candidates[0].terminated_after_stage == "bias"
    assert record.selected_topology_variant_id == "rvt"
    assert record.selected_instance_parameters == {"MN0": {"w": "1u"}}
    assert adapter.rvt is True
    assert adapter.width == "1u"


def test_staged_topology_checkpoint_recovers_and_resumes_inside_candidate(
    tmp_path,
) -> None:
    task = TaskSpec.model_validate(_staged_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _StagedTopologyRefinementAdapter()
    adapter.interrupt_stage = ("rvt", "1u", "ac")
    checkpoint_path = tmp_path / "staged-topology-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert adapter.rvt is False
    assert adapter.width == "1u"
    assert checkpoint.next_candidate_index == 3
    assert checkpoint.active_candidate_index == 3
    assert checkpoint.next_analysis_stage_index == 2
    assert [stage.stage_id for stage in checkpoint.active_candidate_stages] == [
        "bias"
    ]

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.simulated_stages.count(("rvt", "1u", "dc")) == 1
    assert adapter.simulated_stages.count(("rvt", "1u", "ac")) == 2
    assert resumed.selected_topology_variant_id == "rvt"
    assert adapter.rvt is True
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_topology_close_loop_recovers_and_resumes_flattened_candidate_prefix(
    tmp_path,
) -> None:
    task = TaskSpec.model_validate(_topology_refinement_payload())
    plan = build_plan(task)
    adapter = _TopologyRefinementAdapter()
    adapter.interrupt_variant_width = ("rvt", "1u")
    checkpoint_path = tmp_path / "topology-close-loop-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert adapter.rvt is False
    assert adapter.width == "1u"
    assert checkpoint.next_candidate_index == 3
    assert [candidate.index for candidate in checkpoint.candidates] == [1, 2]
    assert checkpoint.expected_topology_variant_id == "lvt"
    assert checkpoint.pending_topology_sha256 is None
    assert checkpoint.complete is False

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert [candidate.index for candidate in resumed.candidates] == [1, 2, 3, 4]
    assert adapter.simulated_points == [
        ("lvt", "1u"),
        ("lvt", "2u"),
        ("rvt", "1u"),
        ("rvt", "1u"),
        ("rvt", "2u"),
    ]
    assert adapter.rvt is True
    assert adapter.width == "1u"
    assert resumed.selected_topology_variant_id == "rvt"
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_generic_transport_interruption_is_recorded_without_an_oa_write() -> None:
    class InterruptedAdapter(_GenericAdapter):
        def simulate(self, task, parameters):
            raise AdapterInterrupted("transport reset after si netlisting")

    task = _task()
    plan = build_plan(task)
    record = TaskExecutor(InterruptedAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.FAILED
    assert any("AdapterInterrupted" in note for note in record.notes)
    assert [item.action for item in record.actions][-1] == "simulation.candidate.1"
    assert record.actions[-1].evidence_source is EvidenceSource.SYSTEM_EVENT
    assert not any("parameters." in item.action for item in record.actions)


def test_subprocess_adapter_routes_generic_task_and_preserves_contract(
    tmp_path, monkeypatch
) -> None:
    task = _task()
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    observed = {}

    def fake_request(action, payload, *, timeout):
        observed.update(action=action, payload=payload, timeout=timeout)
        return {"metrics": {"low_frequency_gain_db": 20.0}}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.simulate(task, {})

    assert observed["action"] == "simulate_existing_schematic"
    assert observed["payload"]["generic_simulation"]["sources"][2][
        "name"
    ] == "VIN_SRC"
    assert observed["payload"]["design_context"]["id"] == (
        "user-single-ended-stage"
    )
    assert result.evidence_source is EvidenceSource.EDA_RESULT


def test_generic_worker_returns_oa_si_dc_ac_and_manifest_evidence(
    monkeypatch,
) -> None:
    frequency_hz = [10.0 ** (2.0 + index / 10.0) for index in range(61)]
    transfer = [
        -10.0 / (1.0 + 1j * frequency / 1e6) for frequency in frequency_hz
    ]
    runner_module = ModuleType("virtuoso_bridge.spectre.runner")

    class Simulator:
        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def run_simulation(self, netlist, parameters):
            return SimpleNamespace(
                ok=True,
                data={
                    "dc_OUT": 0.5,
                    "dc_VDD_SRC:p": -10e-6,
                    "dcOpInfo_MN0:ids": 10e-6,
                    "ac_freq": frequency_hz,
                    "ac_IN": [1.0 + 0.0j] * len(frequency_hz),
                    "ac_OUT": transfer,
                },
                metadata={},
                tool_version="test-spectre-generic",
                warnings=[],
            )

    runner_module.SpectreSimulator = Simulator
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setattr(
        bridge_worker, "_client", lambda: SimpleNamespace(ssh_runner=None)
    )
    monkeypatch.setattr(bridge_worker, "_read_schematic", lambda *args: _raw_schematic())
    geometry = _pin_geometry()
    monkeypatch.setattr(
        bridge_worker,
        "_schematic_geometry_bundle",
        lambda *args: (geometry, {"sha256": "c" * 64}),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_generate_oa_netlist",
        lambda *args, **kwargs: {
            "remote_run_dir": "/data/xum/virtuoso_bridge_smoke/vda_generic",
            "remote_netlist_path": (
                "/data/xum/virtuoso_bridge_smoke/vda_generic/netlist"
            ),
            "netlist_sha256": "a" * 64,
            "parsed": {
                "instances": [],
                "topology_consistency": "matched",
                "parameter_bindings": [],
                "parameter_consistency": "matched",
                "flat_primitive_scope": True,
            },
            "si_log_tail": ["End netlisting"],
        },
    )
    monkeypatch.setattr(bridge_worker, "_upload_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bridge_worker,
        "_install_remote_spectre_guard",
        lambda *args, **kwargs: (
            "spectre",
            {"zero_residual_processes": True},
        ),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_common_source_dc_data_from_result",
        lambda result: (result.data, {"selection": "fixture"}),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_spectre_ac_file_evidence_from_result",
        lambda result: {
            "selection": "fixture",
            "ac": {"relative_path": "ac.ac", "size_bytes": 1, "sha256": "b" * 64},
        },
    )

    task = _task()
    payload = SubprocessBridgeAdapter._task_payload(task)
    canonical_summary = bridge_worker._existing_schematic_summary(
        _raw_schematic(),
        pin_geometry=geometry,
        placement={"sha256": "c" * 64},
    )
    payload["design_context"]["expected_topology_sha256"] = (
        bridge_worker.topology_fingerprint(
            bridge_worker.snapshot_from_inspection(canonical_summary)
        )
    )
    result = bridge_worker.simulate_existing_schematic(payload)

    assert result["analysis_complete"] is True
    assert result["metrics"]["output_dc_v"] == pytest.approx(0.5)
    assert result["metrics"]["bandwidth_3db_hz"] == pytest.approx(1e6, rel=0.03)
    assert result["evidence"]["side_effects"]["oa_write_performed"] is False
    assert result["evidence"]["design_context_binding"]["source"] == (
        "software_inference"
    )
    assert result["evidence"]["simulation"]["artifact_manifest_complete"] is True
    assert result["evidence"]["process_lifecycle"][
        "zero_residual_processes"
    ] is True


def test_generic_ac_voltage_rejects_empty_waveform() -> None:
    with pytest.raises(RuntimeError, match="ac_OUT is empty"):
        bridge_worker._generic_ac_voltage(
            {"ac_OUT": []},
            GenericVoltageExpression(positive_node="OUT"),
            sample_count=2,
        )
