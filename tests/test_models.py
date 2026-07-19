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
