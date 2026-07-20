from __future__ import annotations

from virtuoso_design_agent.models import SideEffect, TaskSpec
from virtuoso_design_agent.planner import build_plan


def _task(operation: str, **extra) -> TaskSpec:
    data = {
        "id": operation.replace(".", "-"),
        "operation": operation,
        "circuit": "inverter",
        "target": {"library": "vda_test", "cell": "vda_inv"},
        "parameters": {"nmos_width_um": 0.5, "pmos_width_um": 1.0},
    }
    data.update(extra)
    return TaskSpec.model_validate(data)


def test_plan_token_is_stable() -> None:
    task = _task("schematic.create")
    assert build_plan(task).confirmation_token == build_plan(task).confirmation_token


def test_create_plan_discloses_replace_existing() -> None:
    task = _task(
        "schematic.create",
        safety={
            "allow_remote_write": True,
            "allowed_library": "vda_test",
            "required_cell_prefix": "vda_",
            "replace_existing": True,
        },
    )
    create = next(
        step for step in build_plan(task).steps if step.capability == "schematic.create"
    )

    assert "显式删除" in create.description
    assert "替换已有" in create.description


def test_inspect_plan_is_remote_read_only() -> None:
    plan = build_plan(_task("schematic.inspect"))
    assert all(step.side_effect is not SideEffect.REMOTE_WRITE for step in plan.steps)
    assert all(step.side_effect is not SideEffect.REMOTE_COMPUTE for step in plan.steps)


def test_simulation_plan_does_not_write_oa() -> None:
    plan = build_plan(_task("simulation.run"))
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert "netlist.generate" in [step.capability for step in plan.steps]


def test_apply_plan_has_before_and_after_readback() -> None:
    plan = build_plan(_task("parameters.apply"))
    capabilities = [step.capability for step in plan.steps]
    assert capabilities.count("schematic.inspect") == 2
    assert "parameters.apply" in capabilities


def test_explicit_instance_parameter_plan_discloses_raw_cdf_readback() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "raw-cdf-apply",
            "operation": "parameters.apply",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"fingers": "2"}}
            ],
        }
    )

    apply = next(
        step
        for step in build_plan(task).steps
        if step.capability == "parameters.apply"
    )

    assert apply.side_effect is SideEffect.REMOTE_WRITE
    assert "CDF/OA" in apply.description
    assert "定向回读" in apply.description
    assert "至多" in apply.description


def test_tuning_plan_discloses_candidate_oa_staging() -> None:
    plan = build_plan(
        _task(
            "design.tune",
            parameter_space={"nmos_width_um": [0.4, 0.5]},
            constraints=[{"metric": "delay_ps", "relation": "<=", "value": 50}],
        )
    )
    stage = next(step for step in plan.steps if step.capability == "parameters.stage")
    persist = next(step for step in plan.steps if step.capability == "evidence.persist")
    assert stage.side_effect is SideEffect.REMOTE_WRITE
    assert "checkpoint" in persist.description


def test_testbench_only_tuning_does_not_request_oa_write() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-testbench-only",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "parameters": {"vdd_v": 0.9},
            "parameter_space": {
                "bias_v": [0.3, 0.35],
                "load_ff": [1.0, 4.0],
            },
            "constraints": [
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e9,
                }
            ],
        }
    )
    plan = build_plan(task)
    stage = next(step for step in plan.steps if step.capability == "parameters.stage")
    finalize = next(
        step for step in plan.steps if step.capability == "parameters.finalize"
    )

    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert stage.side_effect is SideEffect.READ_ONLY
    assert finalize.side_effect is SideEffect.READ_ONLY
    assert "不写 OA" in stage.description


def test_common_source_simulation_plan_is_dc_op_and_read_only_to_oa() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-dc",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9},
        }
    )
    plan = build_plan(task)
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.run"
    )
    assert "DC operating point" in simulation.description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_common_source_ac_plan_discloses_dc_precheck_and_complex_metrics() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-ac",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e11},
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9, "load_ff": 2.0},
        }
    )
    plan = build_plan(task)
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.run"
    )
    evaluation = next(
        step for step in plan.steps if step.capability == "results.evaluate"
    )

    assert "DC operating point" in simulation.description
    assert "复数 AC" in simulation.description
    assert "-3 dB" in evaluation.description
    assert "GBW" in evaluation.description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_common_source_linearity_plan_discloses_sweep_distortion_and_power() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-linearity",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "transient",
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.02, 0.05],
            },
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
        }
    )
    plan = build_plan(task)
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.run"
    )
    evaluation = next(
        step for step in plan.steps if step.capability == "results.evaluate"
    )

    assert "transient" in simulation.description
    assert "幅度扫描" in simulation.description
    assert "THD" in evaluation.description
    assert "P1dB" in evaluation.description
    assert "VDD" in evaluation.description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_common_source_noise_plan_discloses_density_integration() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-noise",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "noise",
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
        }
    )
    plan = build_plan(task)
    simulation = next(
        step for step in plan.steps if step.capability == "simulation.run"
    )
    evaluation = next(
        step for step in plan.steps if step.capability == "results.evaluate"
    )

    assert "noise sweep" in simulation.description
    assert "输入参考噪声" in evaluation.description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_source_degeneration_plan_discloses_minimal_in_place_delta() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "source-degeneration-transform",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": 1_000.0},
        }
    )
    plan = build_plan(task)
    transform = next(
        step
        for step in plan.steps
        if step.capability == "schematic.transform.source-degeneration"
    )
    assert transform.side_effect is SideEffect.REMOTE_WRITE
    assert "同一 cellview" in transform.description
    assert "MN0.S" in transform.description
    assert "新增 RS0" in transform.description
    assert "不新建或替换" in transform.description
    assert [step.capability for step in plan.steps].count("schematic.inspect") == 2
