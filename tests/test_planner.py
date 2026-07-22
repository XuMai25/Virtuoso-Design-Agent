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


def test_common_source_pvt_plan_discloses_finite_worst_case_gate() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-pvt-plan",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35},
            "operating_conditions": [
                {
                    "name": "tt_25c_0p90v",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                    "vdd_v": 0.9,
                },
                {
                    "name": "ss_125c_0p81v",
                    "process_corner": "ss",
                    "temperature_c": 125.0,
                    "vdd_v": 0.81,
                },
            ],
            "safety": {"allow_remote_compute": True},
        }
    )

    plan = build_plan(task)
    simulate = next(step for step in plan.steps if step.capability == "simulation.run")
    evaluate = next(step for step in plan.steps if step.capability == "results.evaluate")

    assert "2 个显式 PVT 条件" in simulate.description
    assert "tt_25c_0p90v" in simulate.description
    assert "ss_125c_0p81v" in simulate.description
    assert "全部满足约束" in evaluate.description
    assert "最坏值" in evaluate.description


def test_common_source_pvt_tuning_plan_is_explicit_and_optional() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-pvt-tune-plan",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35},
            "parameter_space": {"length_um": [0.03, 0.04]},
            "operating_conditions": [
                {
                    "name": "tt_25c_0p90v",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                    "vdd_v": 0.9,
                },
                {
                    "name": "ss_125c_0p81v",
                    "process_corner": "ss",
                    "temperature_c": 125.0,
                    "vdd_v": 0.81,
                },
            ],
            "constraints": [
                {"metric": "saturation_region", "relation": ">=", "value": 1.0}
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )

    plan = build_plan(task)
    stage = next(step for step in plan.steps if step.capability == "parameters.stage")
    sweep = next(step for step in plan.steps if step.capability == "simulation.sweep")
    select = next(step for step in plan.steps if step.capability == "results.select")

    assert stage.side_effect is SideEffect.REMOTE_WRITE
    assert "每个候选只暂存一次 OA" in stage.description
    assert "每个候选" in sweep.description
    assert "2 个显式 PVT 条件" in sweep.description
    assert "tt_25c_0p90v" in sweep.description
    assert "全部条件" in select.description
    assert "最坏值" in select.description


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


def test_ade_prepare_plan_refuses_existing_state_and_leaves_manual_configuration() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "prepare-manual-ade",
            "operation": "ade.prepare",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_prepare": {
                "test_name": "VDA_AC",
                "design": {
                    "library": "source_lib",
                    "cell": "legacy_tb",
                    "view": "schematic",
                },
            },
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.prepare.preflight",
        "ade.prepare",
        "ade.prepare.readback",
        "evidence.persist",
    ]
    assert plan.steps[1].side_effect is SideEffect.READ_ONLY
    assert plan.steps[2].side_effect is SideEffect.REMOTE_WRITE
    assert "已有 Maestro 状态一律拒绝" in plan.steps[1].description
    assert "source_lib/legacy_tb/schematic" in plan.steps[1].description
    assert "不设置 analysis" in plan.steps[2].description
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_capture_plan_preserves_manual_session_and_discloses_local_artifacts() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "capture-manual-ade",
            "operation": "ade.capture",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_capture": {
                "history": "Interactive.7",
                "require_structured_outputs": True,
            },
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.focus.verify",
        "ade.capture",
        "evidence.persist",
    ]
    assert plan.steps[1].side_effect is SideEffect.READ_ONLY
    assert plan.steps[2].side_effect is SideEffect.LOCAL_WRITE
    assert "不打开、保存、关闭或运行" in plan.steps[1].description
    assert "Interactive.7" in plan.steps[2].description
    assert not plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_run_plan_uses_background_compute_without_setup_write() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {"require_structured_outputs": True},
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.run.preflight",
        "ade.run",
        "ade.results.read",
        "evidence.persist",
    ]
    assert plan.steps[2].side_effect is SideEffect.REMOTE_COMPUTE
    assert plan.steps[3].side_effect is SideEffect.REMOTE_COMPUTE
    assert "不要求或改变 GUI 焦点" in plan.steps[1].description
    assert "原生 analysis/parametric sweep" in plan.steps[2].description
    assert "不能证明名称唯一" in plan.steps[2].description
    assert "project/scratch" in plan.steps[3].description
    assert "SHA-256" in plan.steps[3].description
    assert "保留小型 TSV" in plan.steps[3].description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_ade_run_resume_plan_skips_new_simulation_and_discloses_exact_paths() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "recover-saved-maestro",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {
                "resume_history": "Interactive.0",
                "resume_runtime_scratch_root": "/data/xum/vda_runs/run-0",
            },
        }
    )

    plan = build_plan(task)

    assert plan.steps[2].capability == "ade.run.resume"
    assert "不再次调用 run_and_wait" in plan.steps[2].description
    assert "Interactive.0" in plan.steps[1].description
    assert "/data/xum/vda_runs/run-0" in plan.steps[1].description
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_ade_native_sweep_plan_discloses_point_input_and_result_binding() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "run-native-cl-sweep",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": {
                    "expected_tests": ["VDA"],
                    "variables": [
                        {"name": "CL", "expected_value": "1f,2f,4f"}
                    ],
                    "points": [
                        {"point": 1, "values": {"CL": "1f"}},
                        {"point": 2, "values": {"CL": "2f"}},
                        {"point": 3, "values": {"CL": "4f"}},
                    ],
                    "input_bindings": [
                        {
                            "test": "VDA",
                            "variable": "CL",
                            "instance": "CL0",
                            "oa_parameter": "c",
                        }
                    ],
                },
            },
        }
    )

    plan = build_plan(task)

    assert "3 个声明 point" in plan.steps[2].description
    assert "exact-history RDB" in plan.steps[2].description
    assert "唯一 runtime 符号输入束" in plan.steps[2].description
    assert "RDB/Detail" in plan.steps[3].description
    assert "逐点参数" in plan.steps[3].description
    assert "仍不等于已满足 VDA constraints" in plan.steps[4].description


def test_ade_variable_patch_plan_discloses_cas_and_single_setup_save() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-variables",
            "operation": "ade.variables.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": "0.35",
                        "value": "0.30,0.35,0.40",
                    }
                ],
            },
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.variables.preflight",
        "ade.variables.apply",
        "ade.variables.readback",
        "evidence.persist",
    ]
    assert "任何已配置的开放" in plan.steps[1].description
    assert "逐项匹配任务前置条件" in plan.steps[1].description
    assert "bias_v" in plan.steps[2].description
    assert "只保存一次 setup" in plan.steps[2].description
    assert "不证明未声明 scope" in plan.steps[2].description


def test_ade_variable_selection_plan_discloses_exact_transition() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "select-test-local-cl",
            "operation": "ade.variables.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "global_selection_updates": [
                    {
                        "name": "CL",
                        "expected_enabled": True,
                        "enabled": False,
                    }
                ],
            },
        }
    )

    plan = build_plan(task)

    assert "CL:True->False" in plan.steps[2].description
    assert "保持其余集合" in plan.steps[2].description
    assert "不自动覆盖式重试" in plan.steps[3].description
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_setup_patch_plan_discloses_atomic_add_only_boundary() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-setup",
            "operation": "ade.setup.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_setup": {
                "expected_tests": ["AC"],
                "analyses": [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "expected": None,
                        "enabled": True,
                        "options": {"start": "1", "stop": "10G"},
                    }
                ],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "Vout",
                        "output_type": "net",
                        "signal_name": "/OUT",
                    }
                ],
            },
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.setup.preflight",
        "ade.setup.apply",
        "ade.setup.readback",
        "evidence.persist",
    ]
    assert "任一不符则零写入" in plan.steps[1].description
    assert "AC/ac" in plan.steps[2].description
    assert "AC/Vout" in plan.steps[2].description
    assert "不替换已有 output" in plan.steps[2].description
    assert "重新打开" in plan.steps[3].description
    assert plan.steps[2].side_effect is SideEffect.REMOTE_WRITE
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_corner_patch_plan_discloses_add_only_membership_boundary() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "add-maestro-corners",
            "operation": "ade.corners.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_corners": {
                "expected_tests": ["VDA"],
                "expected_corners": [],
                "additions": [{"name": "VDA_LOW"}, {"name": "VDA_NOMINAL"}],
            },
        }
    )

    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "ade.corners.preflight",
        "ade.corners.apply",
        "ade.corners.readback",
        "evidence.persist",
    ]
    assert "enabled/all corner" in plan.steps[1].description
    assert "VDA_LOW, VDA_NOMINAL" in plan.steps[2].description
    assert "public set_corner" in plan.steps[2].description
    assert "不配置 disabled tests" in plan.steps[2].description
    assert plan.steps[2].side_effect is SideEffect.REMOTE_WRITE
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_variable_patch_plan_discloses_scoped_readback_and_corner_guard() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "patch-scoped-maestro-variables",
            "operation": "ade.variables.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "expected_corners": ["nominal", "TT"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "VDA",
                        "expected_value": "0.35",
                        "value": "0.30,0.35,0.40",
                    },
                    {
                        "name": "vdd",
                        "scope": "corner",
                        "scope_name": "TT",
                        "expected_value": "0.9",
                        "value": "0.95",
                    },
                ],
            },
        }
    )

    plan = build_plan(task)

    assert "enabled corners" in plan.steps[1].description
    assert "test:VDA:bias_v" in plan.steps[2].description
    assert "corner:TT:vdd" in plan.steps[2].description
    assert "不证明仿真采用新值" in plan.steps[2].description
    assert "enabled corners" in plan.steps[3].description
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


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
    sweep = next(step for step in plan.steps if step.capability == "simulation.sweep")

    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert stage.side_effect is SideEffect.READ_ONLY
    assert finalize.side_effect is SideEffect.READ_ONLY
    assert "不写 OA" in stage.description
    assert "PVT" not in sweep.description


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


def test_common_source_quality_plan_discloses_atomic_evidence_gate() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-quality",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "quality",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
            },
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

    assert "同一次 OA/si" in simulation.description
    assert "三项均完整" in simulation.description
    assert "GBW" in evaluation.description
    assert "P1dB" in evaluation.description
    assert "输入参考噪声" in evaluation.description
    assert not plan.requires_remote_write


def test_quality_close_loop_does_not_claim_candidate_oa_writes_for_testbench_only_search() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "common-source-quality-close-loop",
            "operation": "design.close_loop",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "quality",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
            },
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
            "parameters": {"vdd_v": 0.9},
            "parameter_space": {"bias_v": [0.32, 0.35], "load_ff": [1.0, 4.0]},
            "constraints": [
                {"metric": "saturation_margin_v", "relation": ">=", "value": 0.05}
            ],
            "create_if_missing": True,
        }
    )
    plan = build_plan(task)
    stage = next(step for step in plan.steps if step.capability == "parameters.stage")
    finalize = next(
        step for step in plan.steps if step.capability == "parameters.finalize"
    )

    assert plan.requires_remote_write  # schematic.ensure may create the target
    assert stage.side_effect is SideEffect.READ_ONLY
    assert finalize.side_effect is SideEffect.READ_ONLY
    assert "不写 OA" in stage.description


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


def test_inverter_testbench_plan_discloses_fixed_minimal_delta() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "inverter-testbench-transform",
            "operation": "schematic.transform",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"vdd_v": 0.9, "load_ff": 2.0},
        }
    )

    plan = build_plan(task)
    transform = next(
        step
        for step in plan.steps
        if step.capability == "schematic.transform.inverter-testbench"
    )

    assert transform.side_effect is SideEffect.REMOTE_WRITE
    assert "VDD0/VIN0/CL0/GND0" in transform.description
    assert "同一 cellview" in transform.description
    assert "不替换" in transform.description
