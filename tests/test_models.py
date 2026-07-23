from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.catalog import UnsupportedCapability
from virtuoso_design_agent.models import (
    DEFAULT_PDK_PROFILE,
    AnalysisKind,
    TaskSpec,
)
from virtuoso_design_agent.planner import build_plan


def _base_task() -> dict:
    return {
        "id": "test-task",
        "operation": "design.tune",
        "circuit": "inverter",
        "target": {"library": "vda_test", "cell": "vda_inv"},
        "parameter_space": {"nmos_width_um": [0.4, 0.5]},
        "constraints": [{"metric": "delay_ps", "relation": "<=", "value": 50}],
    }


def test_default_pdk_is_the_verified_tsmc_n28_foundry_profile() -> None:
    task = TaskSpec.model_validate(_base_task())

    assert DEFAULT_PDK_PROFILE == "nics4304_tsmc28"
    assert task.pdk_profile == DEFAULT_PDK_PROFILE


def test_common_source_simulation_accepts_explicit_finite_pvt_conditions() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-pvt-quality",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "quality",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05],
            },
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
            "parameters": {"bias_v": 0.35, "load_ff": 1.0},
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

    assert [condition.name for condition in task.operating_conditions] == [
        "tt_25c_0p90v",
        "ss_125c_0p81v",
    ]
    assert task.operating_conditions[1].temperature_c == 125.0


@pytest.mark.parametrize(
    ("conditions", "message"),
    [
        (
            [
                {
                    "name": "same",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                    "vdd_v": 0.9,
                },
                {
                    "name": "same",
                    "process_corner": "ss",
                    "temperature_c": 125.0,
                    "vdd_v": 0.81,
                },
            ],
            "unique names",
        ),
        (
            [
                {
                    "name": "tt",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                    "vdd_v": 0.9,
                },
                {
                    "name": "ss",
                    "process_corner": "ss",
                    "temperature_c": 125.0,
                },
            ],
            "all provide vdd_v or all inherit",
        ),
    ],
)
def test_pvt_conditions_reject_ambiguous_grids(conditions, message) -> None:
    data = {
        "id": "cs-invalid-pvt",
        "operation": "simulation.run",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "parameters": {"bias_v": 0.35},
        "operating_conditions": conditions,
    }

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_pvt_conditions_can_tune_but_cannot_conflict_with_condition_vdd() -> None:
    base = {
        "id": "cs-invalid-pvt-scope",
        "operation": "simulation.run",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
        "operating_conditions": [
            {
                "name": "tt",
                "process_corner": "tt",
                "temperature_c": 25.0,
                "vdd_v": 0.9,
            }
        ],
    }
    with pytest.raises(ValidationError, match="must not also declare vdd_v"):
        TaskSpec.model_validate(base)

    base["operation"] = "design.tune"
    base["parameters"] = {"bias_v": 0.35}
    base["parameter_space"] = {"length_um": [0.03, 0.04]}
    base["constraints"] = [
        {"metric": "saturation_region", "relation": ">=", "value": 1.0}
    ]
    task = TaskSpec.model_validate(base)

    assert task.operation.value == "design.tune"
    assert len(task.operating_conditions) == 1

    base["operation"] = "design.close_loop"
    assert TaskSpec.model_validate(base).operation.value == "design.close_loop"

    base["parameter_space"] = {
        "length_um": [0.03, 0.04],
        "vdd_v": [0.8, 0.9],
    }
    with pytest.raises(ValidationError, match="must not also tune vdd_v"):
        TaskSpec.model_validate(base)


def test_pvt_conditions_without_vdd_can_inherit_each_candidate_vdd() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-pvt-vdd-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
            "parameter_space": {"vdd_v": [0.8, 0.9]},
            "operating_conditions": [
                {
                    "name": "tt_25c",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                },
                {
                    "name": "ss_125c",
                    "process_corner": "ss",
                    "temperature_c": 125.0,
                },
            ],
            "constraints": [
                {"metric": "saturation_region", "relation": ">=", "value": 1.0}
            ],
        }
    )

    assert all(condition.vdd_v is None for condition in task.operating_conditions)
    assert task.parameter_space["vdd_v"] == [0.8, 0.9]


def test_plan_rejects_process_corner_absent_from_selected_profile() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-unknown-corner",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
            "operating_conditions": [
                {
                    "name": "unknown",
                    "process_corner": "not_a_real_corner",
                    "temperature_c": 25.0,
                }
            ],
        }
    )

    with pytest.raises(UnsupportedCapability, match="not_a_real_corner"):
        build_plan(task)


def test_tuning_requires_parameter_space() -> None:
    data = _base_task()
    data["parameter_space"] = {}
    with pytest.raises(ValidationError, match="requires parameter_space"):
        TaskSpec.model_validate(data)


def test_parameters_must_be_positive() -> None:
    data = _base_task()
    data["parameter_space"] = {"nmos_width_um": [0.0]}
    with pytest.raises(ValidationError, match="positive"):
        TaskSpec.model_validate(data)


def test_unknown_semantic_parameter_is_rejected_by_catalog() -> None:
    data = _base_task()
    data["parameter_space"] = {"magic_knob": [1.0]}
    task = TaskSpec.model_validate(data)
    with pytest.raises(UnsupportedCapability, match="magic_knob"):
        build_plan(task)


def test_differential_pair_dc_gate_accepts_only_declared_parameters() -> None:
    data = _base_task()
    data["circuit"] = "differential_pair"
    data["parameters"] = {
        "length_um": 0.03,
        "load_resistance_ohm": 20_000.0,
        "tail_current_ua": 50.0,
        "common_mode_v": 0.45,
        "vdd_v": 0.9,
    }
    data["parameter_space"] = {"input_width_um": [0.5, 1.0]}
    data["constraints"] = [
        {"metric": "both_saturation_region", "relation": ">=", "value": 1.0}
    ]
    task = TaskSpec.model_validate(data)

    assert build_plan(task).circuit.value == "differential_pair"

    invalid = task.model_copy(
        update={"parameters": dict(task.parameters) | {"load_ff": 2.0}}
    )
    with pytest.raises(UnsupportedCapability, match="load_ff"):
        build_plan(invalid)


def test_differential_pair_tuning_requires_explicit_testbench_defaults() -> None:
    data = _base_task()
    data["circuit"] = "differential_pair"
    data["analysis"] = "dc"
    data["parameters"] = {
        "length_um": 0.03,
        "tail_current_ua": 50.0,
        "vdd_v": 0.9,
    }
    data["parameter_space"] = {"input_width_um": [0.5, 1.0]}

    with pytest.raises(ValidationError, match="common_mode_v"):
        TaskSpec.model_validate(data)


def test_differential_pair_accepts_ac_with_an_explicit_sweep() -> None:
    data = _base_task()
    data.update(
        {
            "circuit": "differential_pair",
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
            "parameters": {
                "tail_current_ua": 50.0,
                "tail_output_resistance_ohm": 1_000_000.0,
                "common_mode_v": 0.45,
                "vdd_v": 0.9,
            },
            "parameter_space": {"input_width_um": [0.5, 1.0]},
        }
    )

    task = TaskSpec.model_validate(data)

    assert task.resolved_analysis().value == "ac"
    plan = build_plan(task)
    assert plan.circuit.value == "differential_pair"
    assert any("差模/共模" in step.description for step in plan.steps)

    data.pop("ac_sweep")
    with pytest.raises(ValidationError, match="requires ac_sweep"):
        TaskSpec.model_validate(data)


def test_differential_pair_psrr_requires_only_an_ac_sweep() -> None:
    data = {
        "id": "diffpair-psrr",
        "operation": "simulation.run",
        "circuit": "differential_pair",
        "target": {"library": "vda_test", "cell": "vda_diffpair_active"},
        "analysis": "psrr",
        "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
        "parameters": {
            "tail_bias_v": 0.32,
            "common_mode_v": 0.55,
            "vdd_v": 0.9,
            "load_ff": 0.5,
        },
    }

    task = TaskSpec.model_validate(data)

    assert task.resolved_analysis() is AnalysisKind.PSRR
    missing = dict(data)
    missing.pop("ac_sweep")
    with pytest.raises(ValidationError, match="PSRR analysis requires ac_sweep"):
        TaskSpec.model_validate(missing)

    unexpected = dict(data)
    unexpected["noise_sweep"] = {"start_hz": 1e3, "stop_hz": 1e9}
    with pytest.raises(ValidationError, match="PSRR accepts only ac_sweep"):
        TaskSpec.model_validate(unexpected)

    common_source = dict(data)
    common_source["circuit"] = "common_source"
    with pytest.raises(ValidationError, match="common_source supports"):
        TaskSpec.model_validate(common_source)


def test_psrr_ac_sweep_accepts_only_an_in_sweep_evaluation_stop() -> None:
    data = {
        "id": "diffpair-psrr-band",
        "operation": "simulation.run",
        "circuit": "differential_pair",
        "target": {"library": "vda_test", "cell": "vda_diffpair_active"},
        "analysis": "psrr",
        "ac_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "evaluation_stop_hz": 1e8,
        },
        "parameters": {
            "tail_bias_v": 0.32,
            "common_mode_v": 0.55,
            "vdd_v": 0.9,
        },
    }

    task = TaskSpec.model_validate(data)

    assert task.ac_sweep is not None
    assert task.ac_sweep.evaluation_stop_hz == 1e8
    for invalid_stop in (1e3, 1e11):
        invalid = json.loads(json.dumps(data))
        invalid["ac_sweep"]["evaluation_stop_hz"] = invalid_stop
        with pytest.raises(ValidationError, match="evaluation_stop_hz"):
            TaskSpec.model_validate(invalid)

    non_psrr = json.loads(json.dumps(data))
    non_psrr["analysis"] = "ac"
    with pytest.raises(ValidationError, match="supported only.*PSRR"):
        TaskSpec.model_validate(non_psrr)


def test_differential_pair_accepts_balanced_transient_linearity_sweep() -> None:
    data = {
        "id": "diffpair-linearity",
        "operation": "simulation.run",
        "circuit": "differential_pair",
        "target": {"library": "vda_test", "cell": "vda_diffpair"},
        "analysis": "transient",
        "linearity_sweep": {
            "frequency_hz": 100e6,
            "amplitudes_v": [0.005, 0.05, 0.15],
        },
        "parameters": {
            "tail_current_ua": 50.0,
            "common_mode_v": 0.45,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "safety": {"allow_remote_compute": True},
    }

    task = TaskSpec.model_validate(data)
    plan = build_plan(task)

    assert task.resolved_analysis().value == "transient"
    assert any("差分正弦" in step.description for step in plan.steps)

    data.pop("linearity_sweep")
    with pytest.raises(ValidationError, match="requires linearity_sweep"):
        TaskSpec.model_validate(data)


def test_differential_pair_create_rejects_testbench_only_parameters() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "diffpair-create-testbench-leak",
            "operation": "schematic.create",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair"},
            "parameters": {
                "input_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 10_000.0,
                "tail_current_ua": 50.0,
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )

    with pytest.raises(UnsupportedCapability, match="tail_current_ua"):
        build_plan(task)

    finite_tail = task.model_copy(
        update={
            "parameters": {
                "input_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 10_000.0,
                "tail_output_resistance_ohm": 1_000_000.0,
            }
        }
    )
    with pytest.raises(
        UnsupportedCapability, match="tail_output_resistance_ohm"
    ):
        build_plan(finite_tail)


def test_differential_pair_real_tail_transform_and_noise_are_explicit() -> None:
    transform = TaskSpec.model_validate(
        {
            "id": "diffpair-add-tail",
            "operation": "schematic.transform",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair_tail"},
            "schematic_transform": {"action": "add_tail_device"},
            "parameters": {"tail_width_um": 2.0, "tail_length_um": 0.03},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    plan = build_plan(transform)

    assert transform.resolved_schematic_transform_action().value == "add_tail_device"
    assert any(
        step.capability == "schematic.transform.differential-pair-tail-device"
        for step in plan.steps
    )

    missing_action = transform.model_dump(mode="json", exclude_none=True)
    missing_action.pop("schematic_transform")
    with pytest.raises(ValidationError, match="explicit schematic_transform"):
        TaskSpec.model_validate(missing_action)

    noise = TaskSpec.model_validate(
        {
            "id": "diffpair-tail-noise",
            "operation": "simulation.run",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair_tail"},
            "analysis": "noise",
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
            "parameters": {
                "tail_bias_v": 0.35,
                "common_mode_v": 0.55,
                "vdd_v": 0.9,
            },
        }
    )
    noise_plan = build_plan(noise)
    assert any("单一差模输入源" in step.description for step in noise_plan.steps)

    conflict = noise.model_copy(
        update={
            "parameters": dict(noise.parameters) | {"tail_current_ua": 50.0}
        }
    )
    with pytest.raises(UnsupportedCapability, match="mutually exclusive"):
        build_plan(conflict)


def test_differential_pair_source_degeneration_transform_is_explicit_and_reversible() -> None:
    add = TaskSpec.model_validate(
        {
            "id": "diffpair-add-source-degeneration",
            "operation": "schematic.transform",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair_deg"},
            "schematic_transform": {"action": "add_source_degeneration"},
            "parameters": {"source_resistance_ohm": 500.0},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    add_plan = build_plan(add)

    assert any(
        step.capability
        == "schematic.transform.differential-pair-source-degeneration"
        for step in add_plan.steps
    )
    assert any("RS0(NSP,TAIL)/RS1(NSN,TAIL)" in step.description for step in add_plan.steps)

    remove = TaskSpec.model_validate(
        {
            "id": "diffpair-remove-source-degeneration",
            "operation": "schematic.transform",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair_deg"},
            "schematic_transform": {
                "action": "remove_source_degeneration",
                "expected_restored_placement_sha256": "a" * 64,
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    remove_plan = build_plan(remove)
    assert any(
        step.capability
        == "schematic.transform.differential-pair-source-degeneration.remove"
        for step in remove_plan.steps
    )
    assert any("placement SHA-256" in step.description for step in remove_plan.steps)

    wrong_add = add.model_dump(mode="json", exclude_none=True)
    wrong_add["parameters"] = {"source_resistance_ohm": 500.0, "tail_width_um": 1.0}
    with pytest.raises(UnsupportedCapability, match="exactly source_resistance_ohm"):
        build_plan(TaskSpec.model_validate(wrong_add))

    wrong_remove = remove.model_dump(mode="json", exclude_none=True)
    wrong_remove["parameters"] = {"source_resistance_ohm": 500.0}
    with pytest.raises(ValidationError, match="does not accept parameters"):
        TaskSpec.model_validate(wrong_remove)


def test_common_source_gate_accepts_only_implemented_dc_parameters() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-dc",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "vdd_v": 0.9,
            },
            "parameter_space": {
                "device_width_um": [0.5, 1.0],
                "bias_v": [0.4, 0.45],
            },
            "constraints": [
                {"metric": "saturation_margin_v", "relation": ">=", "value": 0.05}
            ],
        }
    )
    assert build_plan(task).circuit.value == "common_source"

    invalid = task.model_copy(
        update={"parameters": dict(task.parameters) | {"load_ff": 2.0}}
    )
    with pytest.raises(UnsupportedCapability, match="load_ff"):
        build_plan(invalid)


def test_common_source_ac_requires_an_explicit_valid_sweep() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e3,
                "stop_hz": 1e11,
                "points_per_decade": 20,
            },
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9, "load_ff": 2.0},
        }
    )

    assert task.resolved_analysis() is AnalysisKind.AC
    assert task.ac_sweep is not None
    assert task.ac_sweep.reference_points == 5
    assert build_plan(task).requires_remote_compute

    missing_sweep = task.model_dump(mode="json", exclude={"ac_sweep"})
    with pytest.raises(ValidationError, match="requires ac_sweep"):
        TaskSpec.model_validate(missing_sweep)

    invalid_range = task.model_dump(mode="json")
    invalid_range["ac_sweep"]["stop_hz"] = 1e3
    with pytest.raises(ValidationError, match="greater than start_hz"):
        TaskSpec.model_validate(invalid_range)


def test_common_source_linearity_requires_a_bounded_coherent_sweep() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-linearity",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "transient",
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.02, 0.05],
                "max_harmonic": 5,
            },
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
        }
    )

    assert task.resolved_analysis() is AnalysisKind.TRANSIENT
    assert task.linearity_sweep is not None
    assert task.linearity_sweep.measurement_cycles == 8
    assert build_plan(task).requires_remote_compute

    missing = task.model_dump(mode="json", exclude={"linearity_sweep"})
    with pytest.raises(ValidationError, match="requires linearity_sweep"):
        TaskSpec.model_validate(missing)

    invalid = task.model_dump(mode="json")
    invalid["linearity_sweep"]["amplitudes_v"] = [0.02, 0.01]
    with pytest.raises(ValidationError, match="strictly increasing"):
        TaskSpec.model_validate(invalid)

    invalid = task.model_dump(mode="json")
    invalid["linearity_sweep"]["amplitudes_v"] = [0.005, float("nan")]
    with pytest.raises(ValidationError, match="finite positive"):
        TaskSpec.model_validate(invalid)


def test_common_source_noise_requires_an_explicit_valid_sweep() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-noise",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "noise",
            "noise_sweep": {
                "start_hz": 1e3,
                "stop_hz": 10e9,
                "points_per_decade": 20,
            },
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
        }
    )

    assert task.resolved_analysis() is AnalysisKind.NOISE
    assert task.noise_sweep is not None
    assert build_plan(task).requires_remote_compute

    missing = task.model_dump(mode="json", exclude={"noise_sweep"})
    with pytest.raises(ValidationError, match="requires noise_sweep"):
        TaskSpec.model_validate(missing)

    mismatch = task.model_dump(mode="json")
    mismatch["analysis"] = "ac"
    with pytest.raises(ValidationError, match="noise_sweep requires"):
        TaskSpec.model_validate(mismatch)


def test_common_source_quality_requires_all_three_bounded_sweeps() -> None:
    data = {
        "id": "cs-quality",
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

    task = TaskSpec.model_validate(data)

    assert task.resolved_analysis() is AnalysisKind.QUALITY
    assert task.resolved_analyses() == (
        AnalysisKind.AC,
        AnalysisKind.TRANSIENT,
        AnalysisKind.NOISE,
    )
    assert build_plan(task).requires_remote_compute

    missing_noise = dict(data)
    missing_noise.pop("noise_sweep")
    with pytest.raises(ValidationError, match="quality analysis requires noise_sweep"):
        TaskSpec.model_validate(missing_noise)

    missing_all = dict(data)
    for field in ("ac_sweep", "linearity_sweep", "noise_sweep"):
        missing_all.pop(field)
    with pytest.raises(
        ValidationError,
        match="requires ac_sweep, linearity_sweep, noise_sweep",
    ):
        TaskSpec.model_validate(missing_all)


def test_ac_settings_cannot_leak_into_unrelated_operations_or_analyses() -> None:
    with pytest.raises(ValidationError, match="only by simulation"):
        TaskSpec.model_validate(
            {
                "id": "cs-create-with-analysis",
                "operation": "schematic.create",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "analysis": "ac",
                "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
                "parameters": {"device_width_um": 1.0},
            }
        )

    with pytest.raises(ValidationError, match="ac_sweep requires"):
        TaskSpec.model_validate(
            {
                "id": "cs-dc-with-ac-sweep",
                "operation": "simulation.run",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "analysis": "dc",
                "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
                "parameters": {"bias_v": 0.45},
            }
        )

    with pytest.raises(ValidationError, match="only transient"):
        TaskSpec.model_validate(
            {
                "id": "inverter-ac",
                "operation": "simulation.run",
                "circuit": "inverter",
                "target": {"library": "vda_test", "cell": "vda_inv"},
                "analysis": "ac",
                "ac_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
                "parameters": {"vdd_v": 0.9},
            }
        )


def test_parameters_apply_accepts_exact_instance_parameter_strings() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "raw-params",
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
        }
    )

    assert task.instance_parameter_updates[0].parameters["fingers"] == "2"
    assert build_plan(task).requires_remote_write


def test_instance_parameter_updates_preserve_bridge_string_values() -> None:
    long_value = "x" * 256
    task = TaskSpec.model_validate(
        {
            "id": "raw-params-pass-through",
            "operation": "parameters.apply",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_existing"},
            "instance_parameter_updates": [
                {
                    "instance": "I0<3>",
                    "parameters": {
                        "empty_value": "",
                        "long_value": long_value,
                        "display-mode": "layout dependent",
                    },
                }
            ],
        }
    )

    assert task.instance_parameter_updates[0].parameters == {
        "empty_value": "",
        "long_value": long_value,
        "display-mode": "layout dependent",
    }
    assert build_plan(task).requires_remote_write


def test_instance_parameter_updates_still_require_string_values() -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {
                "id": "raw-params-invalid",
                "operation": "parameters.apply",
                "circuit": "inverter",
                "target": {"library": "vda_test", "cell": "vda_inv"},
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"m": 2}}
                ],
            }
        )


def test_instance_parameter_updates_are_apply_only_and_can_mix_semantics() -> None:
    base = {
        "id": "raw-params-invalid-scope",
        "circuit": "inverter",
        "target": {"library": "vda_test", "cell": "vda_inv"},
        "instance_parameter_updates": [
            {"instance": "MN0", "parameters": {"m": "2"}}
        ],
    }
    with pytest.raises(ValidationError, match="only by parameters.apply"):
        TaskSpec.model_validate(
            base
            | {
                "operation": "simulation.run",
                "parameters": {"vdd_v": 0.9},
            }
        )
    combined = TaskSpec.model_validate(
        base
        | {
            "operation": "parameters.apply",
            "parameters": {"nmos_width_um": 0.5},
        }
    )
    assert combined.parameters == {"nmos_width_um": 0.5}
    assert combined.instance_parameter_updates[0].parameters == {"m": "2"}


def test_raw_instance_parameter_space_supports_bounded_tuning() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "raw-instance-search",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"m": "1"}}
            ],
            "instance_parameter_space": [
                {
                    "instance": "MN0",
                    "parameter": "fingers",
                    "values": ["1", "2"],
                }
            ],
            "constraints": [
                {"metric": "drain_current_ua", "relation": ">=", "value": 1.0}
            ],
        }
    )

    assert task.parameter_space == {}
    assert task.instance_parameter_updates[0].parameters == {"m": "1"}
    assert task.instance_parameter_space[0].values == ["1", "2"]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            {
                "instance_parameter_space": [
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": ["1", "1"],
                    }
                ]
            },
            "contain duplicates",
        ),
        (
            {
                "instance_parameter_space": [
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": ["1", "2"],
                    },
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": ["3"],
                    },
                ]
            },
            "cannot repeat an instance/parameter",
        ),
        (
            {
                "instance_parameter_updates": [
                    {"instance": "MN0", "parameters": {"fingers": "1"}}
                ],
                "instance_parameter_space": [
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": ["1", "2"],
                    }
                ],
            },
            "overlap swept dimensions",
        ),
    ],
)
def test_raw_instance_parameter_space_rejects_ambiguous_dimensions(
    patch: dict, message: str
) -> None:
    payload = {
        "id": "invalid-raw-instance-search",
        "operation": "design.tune",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
        "instance_parameter_space": [
            {
                "instance": "MN0",
                "parameter": "fingers",
                "values": ["1", "2"],
            }
        ],
        "constraints": [
            {"metric": "drain_current_ua", "relation": ">=", "value": 1.0}
        ],
    }
    payload.update(patch)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(payload)


def test_raw_instance_parameter_space_is_tuning_only_and_string_strict() -> None:
    with pytest.raises(ValidationError, match="requires design.tune"):
        TaskSpec.model_validate(
            {
                "id": "raw-space-on-apply",
                "operation": "parameters.apply",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "instance_parameter_space": [
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": ["1", "2"],
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="string"):
        TaskSpec.model_validate(
            {
                "id": "numeric-raw-space",
                "operation": "design.tune",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
                "instance_parameter_space": [
                    {
                        "instance": "MN0",
                        "parameter": "fingers",
                        "values": [1, 2],
                    }
                ],
                "constraints": [
                    {
                        "metric": "drain_current_ua",
                        "relation": ">=",
                        "value": 1.0,
                    }
                ],
            }
        )


def test_existing_schematic_exposes_only_read_and_manual_parameter_write() -> None:
    inspect = TaskSpec.model_validate(
        {
            "id": "inspect-existing",
            "operation": "schematic.inspect",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_existing"},
        }
    )
    assert build_plan(inspect).operation.value == "schematic.inspect"

    invalid = TaskSpec.model_validate(
        inspect.model_dump(mode="json")
        | {"operation": "simulation.run", "parameters": {"vdd_v": 0.9}}
    )
    with pytest.raises(UnsupportedCapability, match="manual OA surface"):
        build_plan(invalid)


@pytest.mark.parametrize(
    ("action", "parameters", "message"),
    [
        (
            "replace_resistive_load_with_current_mirror",
            {"pmos_load_width_um": 2.0},
            "requires exactly pmos_load_width_um and pmos_load_length_um",
        ),
        (
            "restore_resistive_load",
            {"load_resistance_ohm": 10_000.0, "pmos_load_width_um": 2.0},
            "requires exactly load_resistance_ohm",
        ),
    ],
)
def test_current_mirror_load_transform_requires_exact_parameters(
    action: str, parameters: dict[str, float], message: str
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "invalid-active-load-transform",
            "operation": "schematic.transform",
            "circuit": "differential_pair",
            "target": {"library": "vda_test", "cell": "vda_diffpair"},
            "schematic_transform": {"action": action},
            "parameters": parameters,
        }
    )
    with pytest.raises(UnsupportedCapability, match=message):
        build_plan(task)


def test_ade_prepare_is_a_non_overwrite_persistent_handoff_contract() -> None:
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
                "backend": "maestro",
                "test_name": "VDA_AC",
                "design": {
                    "library": "source_lib",
                    "cell": "legacy_tb",
                    "view": "schematic",
                },
                "simulator": "spectre",
            },
        }
    )

    plan = build_plan(task)

    assert task.ade_prepare is not None
    assert task.ade_prepare.test_name == "VDA_AC"
    assert task.ade_prepare.design is not None
    assert task.ade_prepare.design.cell == "legacy_tb"
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"ade_prepare": None}, "requires ade_prepare settings"),
        (
            {"target": {"library": "vda_test", "cell": "vda_manual_tb"}},
            "target.view='maestro'",
        ),
        ({"parameters": {"vdd_v": 0.9}}, "only creates a new persistent ADE setup"),
        (
            {"safety": {"replace_existing": True}},
            "never replaces an existing Maestro view",
        ),
    ],
)
def test_ade_prepare_rejects_automation_or_overwrite_requests(
    update: dict, message: str
) -> None:
    data = {
        "id": "prepare-manual-ade",
        "operation": "ade.prepare",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_manual_tb",
            "view": "maestro",
        },
        "ade_prepare": {"backend": "maestro"},
    }
    data.update(update)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_ade_prepare_settings_cannot_leak_into_other_operations() -> None:
    with pytest.raises(ValidationError, match="require operation='ade.prepare'"):
        TaskSpec.model_validate(
            {
                "id": "wrong-prepare-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_prepare": {"backend": "maestro"},
            }
        )


def test_ade_capture_is_a_read_only_human_handoff_contract() -> None:
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
                "backend": "maestro",
                "history": "Interactive.7",
                "require_structured_outputs": True,
            },
        }
    )

    plan = build_plan(task)

    assert task.ade_capture is not None
    assert task.ade_capture.history == "Interactive.7"
    assert not plan.requires_remote_write
    assert not plan.requires_remote_compute


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"ade_capture": None}, "requires ade_capture settings"),
        (
            {"target": {"library": "vda_test", "cell": "vda_manual_tb"}},
            "target.view='maestro'",
        ),
        ({"parameters": {"vdd_v": 0.9}}, "only reads the focused ADE state"),
        (
            {"constraints": [{"metric": "gain", "relation": ">=", "value": 2}]},
            "only reads the focused ADE state",
        ),
    ],
)
def test_ade_capture_rejects_automation_or_ambiguous_targets(
    update: dict, message: str
) -> None:
    data = {
        "id": "capture-manual-ade",
        "operation": "ade.capture",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_manual_tb",
            "view": "maestro",
        },
        "ade_capture": {"backend": "maestro"},
    }
    data.update(update)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_ade_capture_settings_cannot_leak_into_other_operations() -> None:
    with pytest.raises(ValidationError, match="require operation='ade.capture'"):
        TaskSpec.model_validate(
            {
                "id": "wrong-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_capture": {"backend": "maestro"},
            }
        )


def test_ade_run_is_background_compute_without_oa_write() -> None:
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

    assert task.ade_run is not None
    assert task.ade_run.require_structured_outputs is True
    assert task.ade_run.require_artifact_manifest is True
    assert task.ade_run.require_simulator_input_consistency is False
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


def test_ade_run_resume_requires_an_exact_history_and_data_xum_runtime_pair() -> None:
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
                "resume_runtime_scratch_root": (
                    "/data/xum/vda_runs/vda_ade_run_saved_1234"
                ),
            },
        }
    )

    assert task.ade_run is not None
    assert task.ade_run.resume_history == "Interactive.0"
    assert build_plan(task).steps[2].capability == "ade.run.resume"

    for ade_run in (
        {"resume_history": "Interactive.0"},
        {"resume_runtime_scratch_root": "/data/xum/vda_runs/run"},
        {
            "resume_history": "Interactive.0",
            "resume_runtime_scratch_root": "/home/xum/simulation/run",
        },
    ):
        with pytest.raises(ValidationError, match="resume|/data/xum"):
            TaskSpec.model_validate(
                task.model_dump(mode="json") | {"ade_run": ade_run}
            )


def test_ade_input_consistency_requires_artifact_manifest() -> None:
    with pytest.raises(ValidationError, match="requires the artifact manifest"):
        TaskSpec.model_validate(
            {
                "id": "run-without-input-manifest",
                "operation": "ade.run",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_run": {
                    "require_artifact_manifest": False,
                    "require_simulator_input_consistency": True,
                },
            }
        )


def _sweep_verification() -> dict:
    return {
        "expected_tests": ["VDA"],
        "variables": [
            {
                "name": "CL",
                "scope": "global",
                "expected_value": "1f,2f,4f",
            }
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
    }


def _result_mapping() -> dict:
    return {
        "parameters": [
            {
                "source": "CL",
                "parameter": "load_ff",
                "scale": 1e15,
                "unit": "fF",
            }
        ],
        "metrics": [
            {
                "test": "VDA",
                "output": "Delay",
                "expected_expression": "average(VT(\"/OUT\"))",
                "metric": "delay_ps",
                "scale": 1e12,
                "unit": "ps",
            },
            {
                "test": "VDA",
                "output": "Energy",
                "expected_expression": "integ(IT(\"/VDD0/PLUS\"))",
                "metric": "supply_energy_per_cycle_fj",
                "scale": 1e15,
                "unit": "fJ",
            },
        ],
    }


def test_ade_run_accepts_an_exact_native_sweep_verification_contract() -> None:
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
                "sweep_verification": _sweep_verification(),
            },
        }
    )

    assert task.ade_run is not None
    sweep = task.ade_run.sweep_verification
    assert sweep is not None
    assert sweep.variables[0].evidence_key() == "CL"
    assert sweep.variables[0].declared_values() == ["1f", "2f", "4f"]
    assert [point.point for point in sweep.points] == [1, 2, 3]


def test_ade_run_accepts_orthogonal_test_sweep_and_corner_overrides() -> None:
    corners = ["Nominal", "VDA_LOW_VDD", "VDA_NOMINAL_VDD"]
    points = []
    point = 1
    for maestro_point, load in ((1, "1f"), (2, "4f")):
        for corner, vdd in zip(corners, ("0.9", "0.8", "0.9"), strict=True):
            points.append(
                {
                    "point": point,
                    "maestro_point": maestro_point,
                    "corner": corner,
                    "values": {"CL": load, "VDD": vdd},
                }
            )
            point += 1
    verification = {
        "expected_tests": ["VDA"],
        "expected_corners": corners,
        "expected_global_variable_selections": {"CL": False, "VDD": True},
        "variables": [
            {
                "name": "CL",
                "scope": "test",
                "scope_name": "VDA",
                "expected_value": "1f,4f",
            },
            {
                "name": "VDD",
                "scope": "global",
                "expected_value": "0.9",
                "sweep": False,
            },
            {
                "name": "VDD",
                "scope": "corner",
                "scope_name": "VDA_LOW_VDD",
                "expected_value": "0.8",
                "sweep": False,
            },
            {
                "name": "VDD",
                "scope": "corner",
                "scope_name": "VDA_NOMINAL_VDD",
                "expected_value": "0.9",
                "sweep": False,
            },
        ],
        "points": points,
        "input_bindings": [
            {
                "test": "VDA",
                "variable": "CL",
                "instance": "CL0",
                "oa_parameter": "c",
            },
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VDD0",
                "oa_parameter": "vdc",
            },
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VIN0",
                "oa_parameter": "v2",
            },
        ],
    }
    task = TaskSpec.model_validate(
        {
            "id": "run-native-corner-grid",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": verification,
            },
        }
    )

    assert task.ade_run is not None
    sweep = task.ade_run.sweep_verification
    assert sweep is not None
    assert sweep.corner_mode() is True
    assert sweep.maestro_point_count() == 2
    assert sweep.effective_variable_names() == ["CL", "VDD"]
    assert len(sweep.points) == 6

    missing_selection = json.loads(json.dumps(verification))
    missing_selection["expected_global_variable_selections"].pop("VDD")
    with pytest.raises(ValidationError, match="cover every effective"):
        TaskSpec.model_validate(
            task.model_dump(mode="json")
            | {
                "ade_run": {
                    "require_simulator_input_consistency": True,
                    "sweep_verification": missing_selection,
                }
            }
        )


def test_ade_sweep_allows_one_variable_to_bind_multiple_oa_parameters() -> None:
    verification = _sweep_verification()
    verification["variables"].append(
        {
            "name": "VDD",
            "scope": "global",
            "expected_value": "0.8,0.9",
        }
    )
    verification["points"] = [
        {"point": 1, "values": {"CL": "1f", "VDD": "0.8"}},
        {"point": 2, "values": {"CL": "2f", "VDD": "0.8"}},
        {"point": 3, "values": {"CL": "4f", "VDD": "0.8"}},
        {"point": 4, "values": {"CL": "1f", "VDD": "0.9"}},
        {"point": 5, "values": {"CL": "2f", "VDD": "0.9"}},
        {"point": 6, "values": {"CL": "4f", "VDD": "0.9"}},
    ]
    verification["input_bindings"].extend(
        [
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VDD0",
                "oa_parameter": "vdc",
            },
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VIN0",
                "oa_parameter": "v2",
            },
        ]
    )
    task = TaskSpec.model_validate(
        {
            "id": "run-native-vdd-cl-sweep",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": verification,
            },
        }
    )

    assert task.ade_run is not None
    sweep = task.ade_run.sweep_verification
    assert sweep is not None
    assert len(sweep.points) == 6
    assert [
        binding.instance
        for binding in sweep.input_bindings
        if binding.variable == "VDD"
    ] == ["VDD0", "VIN0"]


def test_ade_run_accepts_strict_result_mapping_constraints_and_objective() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "evaluate-native-cl-sweep",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": _sweep_verification(),
                "result_mapping": _result_mapping(),
            },
            "constraints": [
                {"metric": "delay_ps", "relation": "<=", "value": 5.0}
            ],
            "objective": {
                "metric": "supply_energy_per_cycle_fj",
                "goal": "minimize",
            },
        }
    )

    assert task.ade_run is not None
    assert task.ade_run.result_mapping is not None
    assert task.ade_run.result_mapping.parameters[0].parameter == "load_ff"
    assert {
        binding.metric for binding in task.ade_run.result_mapping.metrics
    } == {"delay_ps", "supply_energy_per_cycle_fj"}
    descriptions = "\n".join(step.description for step in build_plan(task).steps)
    assert "显式 scale 映射" in descriptions
    assert "constraint 判定" in descriptions
    assert "software_inference" in descriptions


def test_ade_run_allows_only_exact_unmapped_output_evaluation_errors() -> None:
    sweep = _sweep_verification()
    sweep["expected_output_evaluation_errors"] = [
        {
            "test": "VDA",
            "output": "LegacyRise",
            "point_values": {"CL": "1f"},
        }
    ]
    task = TaskSpec.model_validate(
        {
            "id": "evaluate-native-cl-sweep-with-legacy-output-error",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": sweep,
                "result_mapping": _result_mapping(),
            },
            "constraints": [
                {"metric": "delay_ps", "relation": "<=", "value": 5.0}
            ],
        }
    )

    assert task.ade_run is not None
    verification = task.ade_run.sweep_verification
    assert verification is not None
    assert verification.expected_output_evaluation_errors[0].output == "LegacyRise"
    descriptions = "\n".join(step.description for step in build_plan(task).steps)
    assert "1 个显式声明" in descriptions
    assert "零未解释错误" in descriptions


def test_ade_run_rejects_a_mapped_output_as_an_expected_evaluation_error() -> None:
    sweep = _sweep_verification()
    sweep["expected_output_evaluation_errors"] = [
        {
            "test": "VDA",
            "output": "Delay",
            "point_values": {"CL": "1f"},
        }
    ]
    with pytest.raises(ValidationError, match="mapped metric outputs"):
        TaskSpec.model_validate(
            {
                "id": "invalid-mapped-output-error",
                "operation": "ade.run",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_sweep_tb",
                    "view": "maestro",
                },
                "ade_run": {
                    "require_simulator_input_consistency": True,
                    "sweep_verification": sweep,
                    "result_mapping": _result_mapping(),
                },
                "constraints": [
                    {"metric": "delay_ps", "relation": "<=", "value": 5.0}
                ],
            }
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["ade_run"].pop("sweep_verification"),
            "requires strict native sweep verification",
        ),
        (
            lambda data: data["ade_run"]["result_mapping"].update(
                {"parameters": []}
            ),
            "at least 1 item",
        ),
        (
            lambda data: data["ade_run"]["result_mapping"]["parameters"][0].pop(
                "unit"
            ),
            "Field required",
        ),
        (
            lambda data: data["ade_run"]["result_mapping"]["metrics"][0].pop(
                "unit"
            ),
            "Field required",
        ),
        (
            lambda data: data["ade_run"]["result_mapping"]["metrics"][0].update(
                {"test": "OTHER"}
            ),
            "sole expected test",
        ),
        (
            lambda data: data["constraints"][0].update(
                {"metric": "unmapped_delay_ps"}
            ),
            "missing constraint/objective metrics",
        ),
    ],
)
def test_ade_result_mapping_rejects_ambiguous_or_unmapped_contracts(
    mutate, message: str
) -> None:
    data = {
        "id": "invalid-result-mapping",
        "operation": "ade.run",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "maestro",
        },
        "ade_run": {
            "require_simulator_input_consistency": True,
            "sweep_verification": _sweep_verification(),
            "result_mapping": _result_mapping(),
        },
        "constraints": [
            {"metric": "delay_ps", "relation": "<=", "value": 5.0}
        ],
    }
    mutate(data)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_ade_run_constraints_require_an_explicit_result_mapping() -> None:
    with pytest.raises(ValidationError, match="require an explicit result_mapping"):
        TaskSpec.model_validate(
            {
                "id": "unbound-ade-constraints",
                "operation": "ade.run",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_sweep_tb",
                    "view": "maestro",
                },
                "ade_run": {},
                "constraints": [
                    {"metric": "delay_ps", "relation": "<=", "value": 5.0}
                ],
            }
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["points"].__setitem__(
                1, {"point": 3, "values": {"CL": "2f"}}
            ),
            "ordered and contiguous",
        ),
        (
            lambda data: data["points"][1].update(
                {"values": {"UNBOUND": "2f"}}
            ),
            "declare exactly",
        ),
        (
            lambda data: data.update({"input_bindings": []}),
            "at least 1 item",
        ),
        (
            lambda data: data["variables"][0].update(
                {"scope": "test", "scope_name": "OTHER"}
            ),
            "must target one of expected_tests",
        ),
        (
            lambda data: data["variables"][0].update(
                {"expected_value": "1f"}
            ),
            "at least two comma-separated",
        ),
    ],
)
def test_ade_sweep_verification_rejects_ambiguous_point_or_binding_contracts(
    mutate, message: str
) -> None:
    sweep = _sweep_verification()
    mutate(sweep)
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(
            {
                "id": "invalid-native-sweep",
                "operation": "ade.run",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_sweep_tb",
                    "view": "maestro",
                },
                "ade_run": {
                    "require_simulator_input_consistency": True,
                    "sweep_verification": sweep,
                },
            }
        )


def test_ade_sweep_verification_requires_every_result_and_input_gate() -> None:
    for disabled in (
        "require_structured_outputs",
        "require_artifact_manifest",
        "require_simulator_input_consistency",
    ):
        settings = {
            "require_structured_outputs": True,
            "require_artifact_manifest": True,
            "require_simulator_input_consistency": True,
            "sweep_verification": _sweep_verification(),
        }
        settings[disabled] = False
        with pytest.raises(ValidationError, match="requires"):
            TaskSpec.model_validate(
                {
                    "id": f"invalid-native-sweep-{disabled}",
                    "operation": "ade.run",
                    "circuit": "existing_schematic",
                    "target": {
                        "library": "vda_test",
                        "cell": "vda_sweep_tb",
                        "view": "maestro",
                    },
                    "ade_run": settings,
                }
            )


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"ade_run": None}, "requires ade_run settings"),
        (
            {"target": {"library": "vda_test", "cell": "vda_manual_tb"}},
            "target.view='maestro'",
        ),
        ({"parameters": {"vdd_v": 0.9}}, "executes the saved Maestro setup"),
        ({"safety": {"replace_existing": True}}, "never replaces"),
    ],
)
def test_ade_run_rejects_configuration_or_overwrite_requests(
    update: dict, message: str
) -> None:
    data = {
        "id": "run-saved-maestro",
        "operation": "ade.run",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_manual_tb",
            "view": "maestro",
        },
        "ade_run": {},
    }
    data.update(update)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_ade_run_settings_cannot_leak_into_other_operations() -> None:
    with pytest.raises(ValidationError, match="require operation='ade.run'"):
        TaskSpec.model_validate(
            {
                "id": "wrong-run-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_run": {},
            }
        )


def test_ade_variable_patch_requires_exact_old_values_and_remote_write() -> None:
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
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    }
                ],
            },
        }
    )

    plan = build_plan(task)

    assert task.ade_variables is not None
    assert task.ade_variables.updates[0].expected_value is None
    assert task.ade_variables.updates[0].evidence_key() == "bias_v"
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"ade_variables": None}, "requires ade_variables settings"),
        (
            {"target": {"library": "vda_test", "cell": "vda_manual_tb"}},
            "target.view='maestro'",
        ),
        ({"parameters": {"vdd_v": 0.9}}, "only patches declared global"),
        ({"safety": {"replace_existing": True}}, "never replaces"),
    ],
)
def test_ade_variable_patch_rejects_other_configuration_or_overwrite(
    update: dict, message: str
) -> None:
    data = {
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
                {"name": "bias_v", "expected_value": None, "value": "0.35"}
            ],
        },
    }
    data.update(update)

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


@pytest.mark.parametrize(
    ("ade_variables", "message"),
    [
        (
            {
                "expected_tests": ['bad"test'],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": "0.35",
                    }
                ],
            },
            "invalid Maestro test name",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [{"name": "bias_v", "value": "0.35"}],
            },
            "Field required",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": '0.35\" system("bad")',
                    }
                ],
            },
            "quotes, backslashes",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": "0.35\t0.40",
                    }
                ],
            },
            "control characters",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {"name": "bias_v", "expected_value": None, "value": "0.35"},
                    {"name": "bias_v", "expected_value": None, "value": "0.40"},
                ],
            },
            "cannot repeat",
        ),
        (
            {
                "expected_tests": ["VDA", "VDA"],
                "updates": [
                    {"name": "bias_v", "expected_value": None, "value": "0.35"}
                ],
            },
            "expected_tests cannot contain duplicates",
        ),
    ],
)
def test_ade_variable_patch_rejects_unsafe_or_ambiguous_updates(
    ade_variables: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(
            {
                "id": "invalid-maestro-variables",
                "operation": "ade.variables.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": ade_variables,
            }
        )


def test_ade_variable_settings_cannot_leak_into_other_operations() -> None:
    with pytest.raises(
        ValidationError, match="require operation='ade.variables.apply'"
    ):
        TaskSpec.model_validate(
            {
                "id": "wrong-variable-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_variables": {
                    "expected_tests": ["VDA"],
                    "updates": [
                        {
                            "name": "bias_v",
                            "expected_value": None,
                            "value": "0.35",
                        }
                    ],
                },
            }
        )


def test_ade_setup_patch_accepts_analysis_cas_and_named_output_additions() -> None:
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
                        "expected": {
                            "enabled": True,
                            "options": {
                                "anaName": "ac",
                                "start": "1",
                                "stop": "1G",
                                "dec": "10",
                            },
                        },
                        "enabled": True,
                        "options": {"stop": "10G", "dec": "20"},
                    }
                ],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "Vout",
                        "output_type": "net",
                        "signal_name": "/OUT",
                    },
                    {
                        "test": "AC",
                        "name": "BW",
                        "output_type": "point",
                        "expression": "bandwidth(mag(VF(\"/OUT\")) 3 \"low\")",
                        "spec": {"relation": "gt", "value": "1G"},
                    },
                ],
            },
        }
    )

    assert task.ade_setup is not None
    assert task.ade_setup.analyses[0].label() == "AC/ac"
    assert [output.label() for output in task.ade_setup.outputs] == [
        "AC/Vout",
        "AC/BW",
    ]
    plan = build_plan(task)
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


def test_ade_corner_patch_accepts_add_only_empty_membership() -> None:
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

    assert task.ade_corners is not None
    assert task.ade_corners.expected_corners == []
    assert [addition.name for addition in task.ade_corners.additions] == [
        "VDA_LOW",
        "VDA_NOMINAL",
    ]
    plan = build_plan(task)
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute


@pytest.mark.parametrize(
    ("ade_corners", "message"),
    [
        (
            {
                "expected_tests": ["VDA"],
                "expected_corners": ["VDA_LOW"],
                "additions": [{"name": "VDA_LOW"}],
            },
            "must be absent",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "additions": [{"name": "VDA_LOW"}, {"name": "VDA_LOW"}],
            },
            "cannot repeat",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "additions": [{"name": "bad\ncorner"}],
            },
            "control characters",
        ),
    ],
)
def test_ade_corner_patch_rejects_conflicts_and_unsafe_names(
    ade_corners: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(
            {
                "id": "invalid-maestro-corners",
                "operation": "ade.corners.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_corners": ade_corners,
            }
        )


def test_ade_corner_settings_cannot_leak_or_request_overwrite() -> None:
    settings = {
        "expected_tests": ["VDA"],
        "additions": [{"name": "VDA_LOW"}],
    }
    with pytest.raises(ValidationError, match="require operation='ade.corners.apply'"):
        TaskSpec.model_validate(
            {
                "id": "wrong-corner-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_corners": settings,
            }
        )
    with pytest.raises(ValidationError, match="never replaces"):
        TaskSpec.model_validate(
            {
                "id": "overwrite-corner-setup",
                "operation": "ade.corners.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_corners": settings,
                "safety": {"replace_existing": True},
            }
        )


@pytest.mark.parametrize(
    ("ade_setup", "message"),
    [
        (
            {
                "expected_tests": ["AC"],
                "analyses": [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "expected": None,
                        "enabled": False,
                    }
                ],
            },
            "new Maestro analysis must be enabled",
        ),
        (
            {
                "expected_tests": ["AC"],
                "analyses": [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "expected": {"enabled": True, "options": {"stop": "1G"}},
                        "enabled": True,
                    }
                ],
            },
            "must change enable or options",
        ),
        (
            {
                "expected_tests": ["AC"],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "bad",
                        "output_type": "net",
                        "expression": "VF(\"/OUT\")",
                    }
                ],
            },
            "require signal_name",
        ),
        (
            {
                "expected_tests": ["AC"],
                "outputs": [
                    {
                        "test": "OTHER",
                        "name": "BW",
                        "output_type": "point",
                        "expression": "1",
                    }
                ],
            },
            "must target expected_tests",
        ),
        (
            {
                "expected_tests": ["AC"],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "BW",
                        "output_type": "point",
                        "expression": "1",
                    },
                    {
                        "test": "AC",
                        "name": "BW",
                        "output_type": "point",
                        "expression": "2",
                    },
                ],
            },
            "cannot repeat a test/name",
        ),
    ],
)
def test_ade_setup_patch_rejects_ambiguous_or_unsafe_changes(
    ade_setup: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(
            {
                "id": "invalid-maestro-setup",
                "operation": "ade.setup.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_setup": ade_setup,
            }
        )


def test_ade_setup_settings_cannot_leak_or_request_overwrite() -> None:
    setup = {
        "expected_tests": ["AC"],
        "outputs": [
            {
                "test": "AC",
                "name": "Vout",
                "output_type": "net",
                "signal_name": "/OUT",
            }
        ],
    }
    with pytest.raises(ValidationError, match="require operation='ade.setup.apply'"):
        TaskSpec.model_validate(
            {
                "id": "wrong-setup-operation",
                "operation": "schematic.inspect",
                "circuit": "existing_schematic",
                "target": {"library": "vda_test", "cell": "vda_manual_tb"},
                "ade_setup": setup,
            }
        )
    with pytest.raises(ValidationError, match="never replaces"):
        TaskSpec.model_validate(
            {
                "id": "overwrite-maestro-setup",
                "operation": "ade.setup.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_setup": setup,
                "safety": {"replace_existing": True},
            }
        )


def test_ade_variable_patch_accepts_distinct_global_test_and_corner_scopes() -> None:
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
                "expected_tests": ["AC Sweep:1"],
                "expected_corners": ["nominal", "TT 25C"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope": "global",
                        "expected_value": "0.35",
                        "value": "0.40",
                    },
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "AC Sweep:1",
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    },
                    {
                        "name": "bias_v",
                        "scope": "corner",
                        "scope_name": "TT 25C",
                        "expected_value": "0.9",
                        "value": "0.95",
                    },
                ],
            },
        }
    )

    assert task.ade_variables is not None
    assert [update.evidence_key() for update in task.ade_variables.updates] == [
        "bias_v",
        "test:AC Sweep:1:bias_v",
        "corner:TT 25C:bias_v",
    ]


@pytest.mark.parametrize(
    ("ade_variables", "message"),
    [
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope_name": "VDA",
                        "expected_value": None,
                        "value": "0.35",
                    }
                ],
            },
            "global Maestro variables cannot declare scope_name",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "expected_value": None,
                        "value": "0.35",
                    }
                ],
            },
            "test/corner Maestro variables require scope_name",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "OTHER",
                        "expected_value": None,
                        "value": "0.35",
                    }
                ],
            },
            "must target one of expected_tests",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "vdd",
                        "scope": "corner",
                        "scope_name": "TT",
                        "expected_value": "0.9",
                        "value": "0.95",
                    }
                ],
            },
            "require expected_corners",
        ),
        (
            {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "VDA",
                        "expected_value": None,
                        "value": "0.35",
                    },
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "VDA",
                        "expected_value": "0.35",
                        "value": "0.40",
                    },
                ],
            },
            "cannot repeat the same scoped variable",
        ),
    ],
)
def test_ade_variable_patch_rejects_invalid_scopes(
    ade_variables: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(
            {
                "id": "invalid-scoped-maestro-variables",
                "operation": "ade.variables.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": ade_variables,
            }
        )


@pytest.mark.parametrize("unsafe_name", ["VDA\t1", "TT\x7f"])
def test_ade_variable_patch_rejects_control_characters_in_scope_selectors(
    unsafe_name: str,
) -> None:
    with pytest.raises(ValidationError, match="control characters"):
        TaskSpec.model_validate(
            {
                "id": "invalid-maestro-selector",
                "operation": "ade.variables.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {
                    "expected_tests": [unsafe_name],
                    "updates": [
                        {
                            "name": "bias_v",
                            "scope": "test",
                            "scope_name": unsafe_name,
                            "expected_value": None,
                            "value": "0.35",
                        }
                    ],
                },
            }
        )


def test_ade_variable_patch_supports_selection_only_compare_and_swap() -> None:
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

    assert task.ade_variables is not None
    assert task.ade_variables.updates == []
    assert task.ade_variables.global_selection_updates[0].name == "CL"

    bad = task.model_dump(mode="json")
    bad["ade_variables"]["global_selection_updates"][0]["enabled"] = True
    with pytest.raises(ValidationError, match="must change state"):
        TaskSpec.model_validate(bad)


def test_ade_variable_patch_rejects_an_empty_change_set() -> None:
    with pytest.raises(ValidationError, match="requires value or global selection"):
        TaskSpec.model_validate(
            {
                "id": "empty-maestro-variable-patch",
                "operation": "ade.variables.apply",
                "circuit": "existing_schematic",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {"expected_tests": ["VDA"]},
            }
        )


def test_source_degeneration_is_an_exact_common_source_transform() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "add-source-degeneration",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": 1_000.0},
        }
    )
    plan = build_plan(task)
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute

    with pytest.raises(UnsupportedCapability, match="requires exactly"):
        build_plan(
            task.model_copy(
                update={
                    "parameters": {
                        "source_resistance_ohm": 1_000.0,
                        "device_width_um": 1.0,
                    }
                }
            )
        )


def test_source_degeneration_cannot_be_hidden_inside_schematic_create() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "create-degenerated",
            "operation": "schematic.create",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": 1_000.0},
        }
    )
    with pytest.raises(UnsupportedCapability, match="schematic.transform"):
        build_plan(task)


def test_transform_requires_parameters_and_inverter_testbench_is_exact() -> None:
    with pytest.raises(
        ValidationError, match="add_source_degeneration requires source_resistance_ohm"
    ):
        TaskSpec.model_validate(
            {
                "id": "empty-transform",
                "operation": "schematic.transform",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
            }
        )
    inverter = TaskSpec.model_validate(
        {
            "id": "inverter-testbench-transform",
            "operation": "schematic.transform",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"vdd_v": 0.9, "load_ff": 2.0},
        }
    )
    plan = build_plan(inverter)
    assert plan.requires_remote_write
    assert not plan.requires_remote_compute

    with pytest.raises(UnsupportedCapability, match="vdd_v and load_ff"):
        build_plan(
            inverter.model_copy(
                update={"parameters": {"vdd_v": 0.9}}
            )
        )


def test_source_degeneration_removal_is_explicit_and_parameter_free() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "remove-source-degeneration",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "schematic_transform": {"action": "remove_source_degeneration"},
        }
    )

    assert task.resolved_schematic_transform_action().value == (
        "remove_source_degeneration"
    )
    assert task.parameters == {}
    assert build_plan(task).requires_remote_write

    with pytest.raises(
        ValidationError, match="remove_source_degeneration does not accept parameters"
    ):
        TaskSpec.model_validate(
            {
                **task.model_dump(mode="json"),
                "parameters": {"source_resistance_ohm": 1_000.0},
            }
        )


def test_schematic_transform_spec_is_scoped_to_common_source_transform() -> None:
    with pytest.raises(
        ValidationError, match="currently support only common_source"
    ):
        TaskSpec.model_validate(
            {
                "id": "invalid-inverter-remove",
                "operation": "schematic.transform",
                "circuit": "inverter",
                "target": {"library": "vda_test", "cell": "vda_inv"},
                "schematic_transform": {"action": "remove_source_degeneration"},
                "parameters": {"vdd_v": 0.9, "load_ff": 2.0},
            }
        )

    with pytest.raises(
        ValidationError, match="valid only for remove_source_degeneration"
    ):
        TaskSpec.model_validate(
            {
                "id": "invalid-add-restoration-fingerprint",
                "operation": "schematic.transform",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "schematic_transform": {
                    "action": "add_source_degeneration",
                    "expected_restored_placement_sha256": "0" * 64,
                },
                "parameters": {"source_resistance_ohm": 1_000.0},
            }
        )

    with pytest.raises(
        ValidationError,
        match="schematic_transform settings require operation='schematic.transform'",
    ):
        TaskSpec.model_validate(
            {
                "id": "invalid-inspect-remove",
                "operation": "schematic.inspect",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_cs"},
                "schematic_transform": {"action": "remove_source_degeneration"},
            }
        )
