from __future__ import annotations

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.catalog import UnsupportedCapability
from virtuoso_design_agent.models import AnalysisKind, TaskSpec
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


def test_unimplemented_circuit_is_explicit() -> None:
    data = _base_task()
    data["circuit"] = "differential_pair"
    data["parameter_space"] = {"input_width_um": [1.0]}
    task = TaskSpec.model_validate(data)
    with pytest.raises(UnsupportedCapability, match="Gate 3"):
        build_plan(task)


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
                "design_view": "schematic",
                "simulator": "spectre",
            },
        }
    )

    plan = build_plan(task)

    assert task.ade_prepare is not None
    assert task.ade_prepare.test_name == "VDA_AC"
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
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write


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


def test_transform_requires_parameters_and_is_not_exposed_to_inverter() -> None:
    with pytest.raises(ValidationError, match="schematic.transform requires parameters"):
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
            "id": "invalid-inverter-transform",
            "operation": "schematic.transform",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"source_resistance_ohm": 1_000.0},
        }
    )
    with pytest.raises(UnsupportedCapability, match="not executable yet"):
        build_plan(inverter)
