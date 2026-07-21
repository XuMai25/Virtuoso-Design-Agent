from __future__ import annotations

import pytest

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.adapters.subprocess_bridge import BridgeWorkerError
from virtuoso_design_agent.executor import (
    TaskExecutor,
    load_execution_checkpoint,
)
from virtuoso_design_agent.models import AnalysisKind, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan


def _close_loop(delay_limit: float = 45.0) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "loop",
            "operation": "design.close_loop",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"length_um": 0.03, "load_ff": 2.0, "vdd_v": 0.9},
            "parameter_space": {
                "nmos_width_um": [0.4, 0.5, 0.6],
                "pmos_width_um": [0.8, 1.0, 1.2],
            },
            "constraints": [
                {"metric": "delay_ps", "relation": "<=", "value": delay_limit},
                {"metric": "rise_fall_skew_ps", "relation": "<=", "value": 8},
            ],
            "objective": {"metric": "delay_ps", "goal": "minimize"},
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
            },
            "limits": {"max_iterations": 9, "timeout_seconds": 600},
        }
    )


def _explicit_parameter_apply() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "explicit-parameter-apply",
            "operation": "parameters.apply",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "instance_parameter_updates": [
                {
                    "instance": "MN0",
                    "parameters": {"fingers": "2", "m": "1"},
                },
                {"instance": "RD0", "parameters": {"r": "22k"}},
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def test_explicit_instance_parameter_apply_is_independently_read_back() -> None:
    task = _explicit_parameter_apply()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters is None
    applied = next(
        action for action in record.actions if action.action == "parameters.apply"
    )
    assert applied.details["requested_evidence_source"] == "user_input"
    assert applied.details["confirmed_evidence_source"] == "software_inference"
    assert applied.details["confirmed_instance_parameters"] == {
        "MN0": {"fingers": "2", "m": "1"},
        "RD0": {"r": "22k"},
    }
    readback = adapter.inspect_schematic(task).data["instance_parameters"]
    assert readback["MN0"]["fingers"] == "2"
    assert readback["RD0"]["r"] == "22k"


def test_explicit_parameter_apply_fails_on_untrusted_adapter_confirmation() -> None:
    class MismatchedConfirmationAdapter(DeterministicDemoAdapter):
        def apply_parameters(self, task, parameters):
            result = super().apply_parameters(task, parameters)
            data = dict(result.data)
            data["confirmed_instance_parameters"] = {
                "MN0": {"fingers": "3", "m": "1"},
                "RD0": {"r": "22k"},
            }
            return AdapterResult(data=data, evidence_source=result.evidence_source)

    task = _explicit_parameter_apply()
    adapter = MismatchedConfirmationAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("confirmation mismatch" in note for note in record.notes)


def test_semantic_and_explicit_instance_parameters_are_both_applied() -> None:
    task = _explicit_parameter_apply().model_copy(
        update={"parameters": {"device_width_um": 0.5}}
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task.model_copy(update={"parameters": {}}))
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters == {"device_width_um": 0.5}
    readback = adapter.inspect_schematic(task).data
    assert readback["semantic_parameters"]["device_width_um"] == pytest.approx(0.5)
    assert readback["instance_parameters"]["MN0"]["fingers"] == "2"


def test_existing_schematic_parameter_surface_preserves_empty_and_long_values() -> None:
    long_value = "x" * 256
    task = TaskSpec.model_validate(
        {
            "id": "existing-schematic-params",
            "operation": "parameters.apply",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_existing"},
            "instance_parameter_updates": [
                {
                    "instance": "I0<3>",
                    "parameters": {"empty": "", "long": long_value},
                }
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    confirmed = next(
        action.details["confirmed_instance_parameters"]
        for action in record.actions
        if action.action == "schematic.inspect.after"
    )
    assert confirmed == {"I0<3>": {"empty": "", "long": long_value}}


def test_explicit_parameters_preserve_bridge_wf_and_nf_shorthands() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "bridge-parameter-shorthands",
            "operation": "parameters.apply",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"wf": "0.6u", "nf": "2"}}
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    applied = next(
        action.details
        for action in record.actions
        if action.action == "parameters.apply"
    )
    assert applied["requested_instance_parameters"] == {
        "MN0": {"wf": "0.6u", "nf": "2"}
    }
    assert applied["applied_instance_parameters"] == {
        "MN0": {"Wfg": "0.6u", "fingers": "2"}
    }


def test_demo_close_loop_selects_and_applies_feasible_candidate() -> None:
    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 9
    assert record.selected_parameters is not None
    assert record.selected_parameters["nmos_width_um"] == 0.6
    assert record.selected_parameters["pmos_width_um"] == 1.2
    assert any(action.action == "parameters.apply.best" for action in record.actions)
    assert all(
        candidate.evidence_source.value == "software_inference"
        for candidate in record.candidates
    )
    assert all(
        candidate.metric_sources["gate_area_proxy_um2"].value
        == "software_inference"
        for candidate in record.candidates
    )


def test_simulation_record_uses_actual_schematic_parameters_when_omitted() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "simulate-current-oa",
            "operation": "simulation.run",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"load_ff": 2.0, "vdd_v": 0.9},
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.candidates[0].parameters["nmos_width_um"] == 0.5
    assert record.candidates[0].parameters["pmos_width_um"] == 1.0
    assert record.candidates[0].parameters["length_um"] == 0.03


def test_infeasible_search_does_not_write_best_attempt_to_oa() -> None:
    task = _close_loop(delay_limit=1.0)
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert record.selected_parameters is None
    assert record.selected_metrics is None
    assert not any(action.action == "parameters.apply.best" for action in record.actions)
    assert any(action.action == "parameters.restore" for action in record.actions)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }
    assert any("no feasible candidate was committed" in note for note in record.notes)


def test_missing_objective_metric_blocks_writeback() -> None:
    data = _close_loop().model_dump(mode="json")
    data["objective"] = {"metric": "missing_energy_fj", "goal": "minimize"}
    task = TaskSpec.model_validate(data)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert not any(action.action == "parameters.apply.best" for action in record.actions)


def test_partial_candidate_failure_downgrades_run_status() -> None:
    class FlakyAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            if parameters["nmos_width_um"] == 0.4:
                raise RuntimeError("injected candidate failure")
            return super().simulate(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(FlakyAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert any("candidate evaluation(s) failed" in note for note in record.notes)
    assert any(action.action == "parameters.apply.best" for action in record.actions)


def test_search_budget_exhaustion_is_explicit() -> None:
    data = _close_loop().model_dump(mode="json")
    data["limits"]["max_iterations"] = 2
    task = TaskSpec.model_validate(data)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert len(record.candidates) == 2
    assert record.status is RunStatus.PARTIAL
    assert any("search budget exhausted" in note for note in record.notes)


def test_interrupted_search_restores_initial_oa_parameters() -> None:
    class InterruptingAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.simulations = 0

        def simulate(self, task, parameters):
            self.simulations += 1
            if self.simulations == 2:
                raise KeyboardInterrupt
            return super().simulate(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    adapter = InterruptingAdapter()
    executor = TaskExecutor(adapter)
    with pytest.raises(KeyboardInterrupt):
        executor.execute(task, plan, token=plan.confirmation_token)

    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }
    assert any(
        action.action == "parameters.restore.interrupted"
        for action in executor.actions
    )
    assert any(
        action.action == "simulation.candidate.2" and action.status == "failed"
        for action in executor.actions
    )


class _RecoveringBridgeAdapter(DeterministicDemoAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.simulations: list[tuple[float, float]] = []
        self.interrupted = False

    def simulate(self, task, parameters):
        point = (
            parameters["nmos_width_um"],
            parameters["pmos_width_um"],
        )
        self.simulations.append(point)
        if point == (0.4, 1.0) and not self.interrupted:
            self.interrupted = True
            raise BridgeWorkerError("injected SSH reset")
        return super().simulate(task, parameters)


def _incomplete_checkpoint(tmp_path):
    task = _close_loop()
    plan = build_plan(task)
    adapter = _RecoveringBridgeAdapter()
    checkpoint_path = tmp_path / "loop.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    return task, plan, adapter, checkpoint_path, first


def test_bridge_interruption_resumes_without_repeating_completed_prefix(
    tmp_path,
) -> None:
    task, plan, adapter, checkpoint_path, first = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.complete is False
    assert checkpoint.next_candidate_index == 2
    assert [candidate.index for candidate in checkpoint.candidates] == [1]
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )
    completed = load_execution_checkpoint(checkpoint_path)

    assert resumed.status is RunStatus.SUCCEEDED
    assert completed.complete is True
    assert completed.next_candidate_index == 10
    assert len(resumed.candidates) == 9
    assert adapter.simulations.count((0.4, 0.8)) == 1
    assert adapter.simulations.count((0.4, 1.0)) == 2
    assert any("resumed candidate search at index 2" in note for note in resumed.notes)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.6,
        "pmos_width_um": 1.2,
        "length_um": 0.03,
    }


def test_resume_rejects_changed_task_plan_before_adapter_actions(tmp_path) -> None:
    task, _, adapter, checkpoint_path, _ = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)
    changed = task.model_copy(
        update={
            "constraints": [
                task.constraints[0].model_copy(update={"value": 44.0}),
                *task.constraints[1:],
            ]
        }
    )
    changed_plan = build_plan(changed)
    executor = TaskExecutor(adapter)

    with pytest.raises(ValueError, match="plan token"):
        executor.execute(
            changed,
            changed_plan,
            token=changed_plan.confirmation_token,
            checkpoint_path=checkpoint_path,
            resume_checkpoint=checkpoint,
        )
    assert executor.actions == []


def test_checkpoint_candidate_allows_only_matching_oa_derived_extras() -> None:
    declared = {
        "vdd_v": 0.9,
        "bias_v": 0.35,
        "source_resistance_ohm": 500.0,
    }
    initial_oa = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 22_000.0,
        "source_resistance_ohm": 1_000.0,
    }
    actual = {
        **declared,
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 22_000.0,
    }

    assert TaskExecutor._checkpoint_candidate_matches(
        declared, actual, initial_oa
    )
    assert not TaskExecutor._checkpoint_candidate_matches(
        declared, actual | {"untrusted_extra": 1.0}, initial_oa
    )
    assert not TaskExecutor._checkpoint_candidate_matches(
        declared, actual | {"device_width_um": 1.1}, initial_oa
    )


def test_resume_refuses_unrecognized_current_oa_parameters(tmp_path) -> None:
    task, plan, adapter, checkpoint_path, _ = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)
    adapter.apply_parameters(
        task,
        {
            "nmos_width_um": 0.7,
            "pmos_width_um": 1.4,
            "length_um": 0.03,
        },
    )

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.FAILED
    assert any("refusing automatic resume" in note for note in resumed.notes)
    assert not any(
        action.action.startswith("parameters.stage.")
        for action in resumed.actions[len(checkpoint.actions) :]
    )
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.7,
        "pmos_width_um": 1.4,
        "length_um": 0.03,
    }


def test_resume_after_final_write_interruption_does_not_repeat_simulations(
    tmp_path,
) -> None:
    class FinalizeInterruptingAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.simulations = 0
            self.interrupted = False

        def simulate(self, task, parameters):
            self.simulations += 1
            return super().simulate(task, parameters)

        def apply_parameters(self, task, parameters):
            final_best = (
                parameters.get("nmos_width_um") == 0.6
                and parameters.get("pmos_width_um") == 1.2
                and self.simulations == 9
            )
            if final_best and not self.interrupted:
                self.interrupted = True
                raise BridgeWorkerError("injected final write reset")
            return super().apply_parameters(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    adapter = FinalizeInterruptingAdapter()
    checkpoint_path = tmp_path / "finalize.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 10
    assert len(checkpoint.candidates) == 9
    assert adapter.simulations == 9

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.simulations == 9
    assert load_execution_checkpoint(checkpoint_path).complete is True
    assert any(
        action.action == "parameters.apply.best" and action.status == "succeeded"
        for action in resumed.actions
    )


def _common_source_loop() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "cs-loop",
            "operation": "design.close_loop",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "vdd_v": 0.9,
            },
            "parameter_space": {
                "device_width_um": [0.5, 1.0, 1.5],
                "bias_v": [0.45],
            },
            "constraints": [
                {
                    "metric": "drain_current_ua",
                    "relation": "target",
                    "value": 20.0,
                    "tolerance": 1.0,
                },
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
            ],
            "objective": {
                "metric": "output_swing_margin_v",
                "goal": "maximize",
            },
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 3, "timeout_seconds": 600},
        }
    )


def test_common_source_demo_closes_dc_operating_point_with_oa_readback() -> None:
    task = _common_source_loop()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.0)
    assert record.selected_parameters["bias_v"] == pytest.approx(0.45)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(20_000.0),
    }
    assert all(
        candidate.evidence_source.value == "software_inference"
        for candidate in record.candidates
    )


def _common_source_ac_run(*, stop_hz: float = 1e11) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": f"cs-ac-{int(stop_hz)}",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": stop_hz,
                "points_per_decade": 20,
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e9,
                },
                {
                    "metric": "gain_bandwidth_product_hz",
                    "relation": ">=",
                    "value": 5e9,
                },
            ],
            "safety": {"allow_remote_compute": True},
        }
    )


def test_common_source_ac_is_a_formal_simulation_run_capability() -> None:
    task = _common_source_ac_run()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 1
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.analysis_issues == []
    assert candidate.metrics["low_frequency_gain_v_per_v"] >= 2.0
    assert candidate.metrics["bandwidth_3db_hz"] >= 1e9
    assert candidate.metrics["gain_bandwidth_product_hz"] >= 5e9
    assert candidate.metrics["unity_gain_frequency_hz"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def test_common_source_linearity_is_a_formal_simulation_run_capability() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-linearity-run",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_linearity"},
            "analysis": "transient",
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 1.0,
            },
            "constraints": [
                {
                    "metric": "max_thd_percent",
                    "relation": "<=",
                    "value": 10.0,
                },
                {
                    "metric": "max_average_supply_power_uw",
                    "relation": "<=",
                    "value": 100.0,
                },
            ],
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.metrics["small_signal_gain_v_per_v"] > 0
    assert candidate.metrics["input_1db_compression_v_peak"] > 0
    assert candidate.metrics["max_thd_percent"] <= 10.0
    assert candidate.metrics["dc_supply_power_uw"] > 0
    assert candidate.metrics["max_average_supply_power_uw"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def test_common_source_noise_is_a_formal_simulation_run_capability() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-noise-run",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_noise"},
            "analysis": "noise",
            "noise_sweep": {
                "start_hz": 1e3,
                "stop_hz": 1e9,
                "points_per_decade": 20,
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 1.0,
            },
            "constraints": [
                {
                    "metric": "integrated_input_referred_noise_uv_rms",
                    "relation": "<=",
                    "value": 1e6,
                }
            ],
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.metrics["integrated_output_noise_uv_rms"] > 0
    assert candidate.metrics["integrated_input_referred_noise_uv_rms"] > 0
    assert candidate.metrics["dc_supply_power_uw"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def _common_source_quality_task(**updates) -> TaskSpec:
    data = {
        "id": "cs-quality-run",
        "operation": "simulation.run",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs_quality"},
        "analysis": "quality",
        "ac_sweep": {
            "start_hz": 1e4,
            "stop_hz": 1e11,
            "points_per_decade": 20,
        },
        "linearity_sweep": {
            "frequency_hz": 100e6,
            "amplitudes_v": [0.005, 0.05, 0.15],
        },
        "noise_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "points_per_decade": 20,
        },
        "parameters": {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "constraints": [
            {"metric": "saturation_margin_v", "relation": ">=", "value": 0.01},
            {
                "metric": "gain_bandwidth_product_hz",
                "relation": ">=",
                "value": 1e9,
            },
            {"metric": "input_1db_compression_v_peak", "relation": ">=", "value": 0.01},
            {"metric": "max_thd_percent", "relation": "<=", "value": 100.0},
            {
                "metric": "integrated_input_referred_noise_uv_rms",
                "relation": "<=",
                "value": 1e6,
            },
        ],
        "safety": {"allow_remote_compute": True},
    }
    data.update(updates)
    return TaskSpec.model_validate(data)


def test_common_source_quality_combines_all_required_metrics() -> None:
    task = _common_source_quality_task()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.analysis_issues == []
    for metric in (
        "gain_bandwidth_product_hz",
        "input_1db_compression_v_peak",
        "max_thd_percent",
        "integrated_input_referred_noise_uv_rms",
        "dc_supply_power_uw",
    ):
        assert metric in candidate.metrics
    action = next(
        item for item in record.actions if item.action == "simulation.candidate.1"
    )
    assert action.details["analysis_bundle"]["analyses"] == [
        "ac",
        "transient",
        "noise",
    ]


def test_common_source_quality_rejects_one_incomplete_member() -> None:
    class MissingNoiseAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            result = super().simulate(task, parameters)
            if task.resolved_analysis() is AnalysisKind.NOISE:
                data = dict(result.data)
                data["analysis_complete"] = False
                data["analysis_issues"] = ["noise waveform is empty"]
                return AdapterResult(data=data, evidence_source=result.evidence_source)
            return result

    task = _common_source_quality_task(constraints=[])
    adapter = MissingNoiseAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert record.candidates[0].analysis_complete is False
    assert record.candidates[0].feasible is False
    assert "noise: noise waveform is empty" in record.candidates[0].analysis_issues


def test_common_source_quality_testbench_tuning_is_bounded_and_does_not_write_oa() -> None:
    task = _common_source_quality_task(
        id="cs-quality-tune",
        operation="design.tune",
        parameters={"vdd_v": 0.9},
        parameter_space={"bias_v": [0.32, 0.35], "load_ff": [1.0, 4.0]},
        objective={
            "metric": "integrated_input_referred_noise_uv_rms",
            "goal": "minimize",
        },
        limits={"max_iterations": 2, "timeout_seconds": 600},
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    before = adapter.inspect_schematic(task).data["semantic_parameters"]
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.PARTIAL
    assert len(record.candidates) == 2
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert before == after
    assert not any(
        action.action.startswith("parameters.stage")
        or action.action in {"parameters.finalize", "parameters.restore"}
        for action in record.actions
    )
    assert any("budget exhausted" in note for note in record.notes)


def test_common_source_quality_design_tuning_writes_and_reads_back_best_oa() -> None:
    task = _common_source_quality_task(
        id="cs-quality-design-tune",
        operation="design.tune",
        parameters={
            "length_um": 0.03,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        parameter_space={
            "device_width_um": [0.5, 1.0],
            "load_resistance_ohm": [20_000.0, 22_000.0],
            "source_resistance_ohm": [1_000.0, 2_000.0],
        },
        objective={"metric": "gain_bandwidth_product_hz", "goal": "maximize"},
        safety={
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
        limits={"max_iterations": 8, "timeout_seconds": 600},
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    adapter.transform_schematic(
        TaskSpec.model_validate(
            {
                "id": "cs-quality-design-tune-transform",
                "operation": "schematic.transform",
                "circuit": "common_source",
                "target": task.target.model_dump(),
                "parameters": {"source_resistance_ohm": 1_000.0},
                "safety": {
                    "allow_remote_write": True,
                    "allowed_library": "vda_test",
                },
            }
        )
    )
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.SUCCEEDED
    assert plan.requires_remote_write is True
    assert len(record.candidates) == 8
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert record.selected_parameters is not None
    for name in (
        "device_width_um",
        "load_resistance_ohm",
        "source_resistance_ohm",
    ):
        assert after[name] == pytest.approx(record.selected_parameters[name])
    assert sum(
        action.action.startswith("parameters.stage.") for action in record.actions
    ) == 8
    assert any(
        action.action == "parameters.apply.best" for action in record.actions
    )


def test_common_source_ac_short_sweep_is_partial_not_a_fake_bandwidth() -> None:
    task = _common_source_ac_run(stop_hz=1e8).model_copy(
        update={"constraints": []}
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    candidate = record.candidates[0]
    assert candidate.analysis_complete is False
    assert "bandwidth_3db_hz" not in candidate.metrics
    assert any("sweep_stop" in issue for issue in candidate.analysis_issues)
    assert any("required analysis metrics" in note for note in record.notes)


def test_common_source_ac_metrics_participate_in_bounded_design_tuning() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_tune"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
            },
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "parameter_space": {"device_width_um": [0.5, 1.0, 1.5]},
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 1.5,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 2e9,
                },
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
            "limits": {"max_iterations": 3, "timeout_seconds": 600},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 3
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.5)
    assert adapter.inspect_schematic(task).data["semantic_parameters"][
        "device_width_um"
    ] == pytest.approx(1.5)


def test_common_source_ac_can_tune_testbench_conditions_without_oa_write() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-testbench-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_conditions"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
            },
            "parameters": {"vdd_v": 0.9},
            "parameter_space": {
                "bias_v": [0.3, 0.35],
                "load_ff": [1.0, 4.0],
            },
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e9,
                },
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {"allow_remote_compute": True},
            "limits": {"max_iterations": 4, "timeout_seconds": 600},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    before = adapter.inspect_schematic(task).data["semantic_parameters"]
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.SUCCEEDED
    assert plan.requires_remote_write is False
    assert len(record.candidates) == 4
    assert record.selected_parameters is not None
    assert record.selected_parameters["load_ff"] == pytest.approx(1.0)
    assert before == after
    assert not any(
        action.action.startswith("parameters.stage") for action in record.actions
    )
    assert not any(
        action.action == "parameters.apply.best" for action in record.actions
    )
    assert any("without changing OA" in note for note in record.notes)


def test_ac_tuning_marks_mixed_complete_and_incomplete_candidates_partial() -> None:
    class IncompleteFirstCandidateAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            result = super().simulate(task, parameters)
            if float(parameters["device_width_um"]) != 0.5:
                return result
            data = dict(result.data)
            data["analysis_complete"] = False
            data["analysis_issues"] = [
                "bandwidth_3db_hz unresolved: injected_short_sweep"
            ]
            metrics = dict(data["metrics"])
            metrics.pop("bandwidth_3db_hz", None)
            metrics.pop("gain_bandwidth_product_hz", None)
            data["metrics"] = metrics
            return AdapterResult(data=data, evidence_source=result.evidence_source)

    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-mixed-completeness",
            "operation": "design.close_loop",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_mixed"},
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "parameter_space": {"device_width_um": [0.5, 1.0]},
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 1.5,
                }
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )
    adapter = IncompleteFirstCandidateAdapter()
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert record.candidates[0].analysis_complete is False
    assert record.candidates[1].analysis_complete is True
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.0)
    assert any("lacked required core metrics" in note for note in record.notes)


def test_common_source_infeasible_search_restores_oa_parameters() -> None:
    task = _common_source_loop().model_copy(
        update={
            "constraints": [
                _common_source_loop().constraints[0].model_copy(
                    update={"value": 1.0, "tolerance": 0.1}
                )
            ]
        }
    )
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert any(action.action == "parameters.restore" for action in record.actions)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(20_000.0),
    }


def test_common_source_budget_exhaustion_is_explicit() -> None:
    base = _common_source_loop()
    task = base.model_copy(
        update={
            "parameter_space": {
                "device_width_um": [0.5, 1.0, 1.5],
                "bias_v": [0.35, 0.45],
            },
            "limits": base.limits.model_copy(update={"max_iterations": 2}),
        }
    )
    plan = build_plan(task)
    executor = TaskExecutor(DeterministicDemoAdapter())
    record = executor.execute(
        task, plan, token=plan.confirmation_token
    )

    assert len(record.candidates) == 2
    assert record.status is RunStatus.PARTIAL
    assert any("search budget exhausted" in note for note in record.notes)
    notes = list(record.notes)
    executor._note_budget_exhaustion(task, record.status, notes)
    assert sum(note.startswith("search budget exhausted") for note in notes) == 1


def test_common_source_checkpoint_resumes_completed_prefix(tmp_path) -> None:
    class InterruptingCommonSourceAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.widths: list[float] = []
            self.interrupted = False

        def simulate(self, task, parameters):
            width = float(parameters["device_width_um"])
            self.widths.append(width)
            if width == 1.0 and not self.interrupted:
                self.interrupted = True
                raise BridgeWorkerError("injected common-source transport reset")
            return super().simulate(task, parameters)

    base = _common_source_loop()
    task = base.model_copy(
        update={
            "parameter_space": {
                "device_width_um": [0.5, 1.0],
                "bias_v": [0.45],
            },
            "limits": base.limits.model_copy(update={"max_iterations": 2}),
        }
    )
    plan = build_plan(task)
    adapter = InterruptingCommonSourceAdapter()
    checkpoint_path = tmp_path / "common-source.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert [candidate.index for candidate in checkpoint.candidates] == [1]

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.widths.count(0.5) == 1
    assert adapter.widths.count(1.0) == 2
    assert load_execution_checkpoint(checkpoint_path).complete is True


def _source_degeneration_transform(resistance_ohm: float = 1_000.0) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": f"source-degeneration-{resistance_ohm:g}",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": resistance_ohm},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def test_source_degeneration_is_an_in_place_audited_delta() -> None:
    task = _source_degeneration_transform()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    adapter.apply_parameters(_explicit_parameter_apply(), {})
    before = adapter.inspect_schematic(task).data
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters == {"source_resistance_ohm": 1_000.0}
    assert before["pins"] == after["pins"]
    assert set(after["nets"]) == set(before["nets"]) | {"NSRC"}
    assert set(after["instance_parameters"]) == {"MN0", "RD0", "RS0"}
    assert after["instance_parameters"]["MN0"] == before["instance_parameters"]["MN0"]
    assert after["instance_parameters"]["RD0"] == before["instance_parameters"]["RD0"]
    assert after["instance_parameters"]["MN0"]["fingers"] == "2"
    assert after["instance_parameters"]["RD0"]["r"] == "22k"
    assert after["instance_parameters"]["RS0"]["r"] == "1000"
    assert after["topology_variant"] == "source_degenerated_common_source"
    assert next(
        item for item in after["instances"] if item["name"] == "MN0"
    )["terminals"]["S"] == "NSRC"


def test_source_degeneration_transform_is_idempotent_and_can_retarget_only_rs0() -> None:
    adapter = DeterministicDemoAdapter()
    first = _source_degeneration_transform()
    adapter.create_schematic(first)
    first_plan = build_plan(first)
    TaskExecutor(adapter).execute(
        first, first_plan, token=first_plan.confirmation_token
    )
    baseline = adapter.inspect_schematic(first).data

    repeated = _source_degeneration_transform()
    repeated_plan = build_plan(repeated)
    repeated_record = TaskExecutor(adapter).execute(
        repeated, repeated_plan, token=repeated_plan.confirmation_token
    )
    assert repeated_record.status is RunStatus.SUCCEEDED
    repeated_action = next(
        action
        for action in repeated_record.actions
        if action.action == "schematic.transform.source-degeneration"
    )
    assert repeated_action.details["already_transformed"] is True
    assert repeated_action.details["resistance_changed"] is False

    retarget = _source_degeneration_transform(2_000.0)
    retarget_plan = build_plan(retarget)
    retarget_record = TaskExecutor(adapter).execute(
        retarget, retarget_plan, token=retarget_plan.confirmation_token
    )
    after = adapter.inspect_schematic(retarget).data
    assert retarget_record.status is RunStatus.SUCCEEDED
    assert after["instance_parameters"]["MN0"] == baseline["instance_parameters"]["MN0"]
    assert after["instance_parameters"]["RD0"] == baseline["instance_parameters"]["RD0"]
    assert after["instance_parameters"]["RS0"]["r"] == "2000"


def test_executor_rejects_transform_that_changes_an_existing_instance() -> None:
    class CorruptingTransformAdapter(DeterministicDemoAdapter):
        def transform_schematic(self, task):
            result = super().transform_schematic(task)
            self._schematics[self._key(task)]["instance_parameters"]["MN0"][
                "fingers"
            ] = "9"
            return result

    task = _source_degeneration_transform()
    adapter = CorruptingTransformAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("unexpectedly changed MN0 parameters" in note for note in record.notes)


def test_source_resistance_uses_the_existing_dc_tuning_path() -> None:
    adapter = DeterministicDemoAdapter()
    transform = _source_degeneration_transform()
    adapter.create_schematic(transform)
    adapter.apply_parameters(_explicit_parameter_apply(), {})
    transform_plan = build_plan(transform)
    TaskExecutor(adapter).execute(
        transform, transform_plan, token=transform_plan.confirmation_token
    )
    tune = TaskSpec.model_validate(
        {
            "id": "tune-source-resistance",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9},
            "parameter_space": {"source_resistance_ohm": [500.0, 1_000.0]},
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "source_current_mismatch_percent",
                    "relation": "<=",
                    "value": 1.0,
                },
            ],
            "objective": {"metric": "drain_current_ua", "goal": "maximize"},
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )
    tune_plan = build_plan(tune)
    record = TaskExecutor(adapter).execute(
        tune, tune_plan, token=tune_plan.confirmation_token
    )
    readback = adapter.inspect_schematic(tune).data

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 2
    assert record.selected_parameters is not None
    assert record.selected_parameters["source_resistance_ohm"] == pytest.approx(500.0)
    assert readback["semantic_parameters"]["source_resistance_ohm"] == pytest.approx(
        500.0
    )
    assert readback["instance_parameters"]["MN0"]["fingers"] == "2"
    assert all(
        "source_voltage_v" in candidate.metrics for candidate in record.candidates
    )
