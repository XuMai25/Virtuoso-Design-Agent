from __future__ import annotations

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.catalog import UnsupportedCapability
from virtuoso_design_agent.models import TaskSpec
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
