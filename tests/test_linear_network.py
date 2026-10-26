from __future__ import annotations

import math

import pytest

from virtuoso_design_agent.linear_network import (
    ComplexNodalSystem,
    solve_complex_linear_system,
)


def _stamp_script_local_vccs(
    system: ComplexNodalSystem,
    output_positive: str,
    output_negative: str,
    control_positive: str,
    control_negative: str,
    transconductance_s: float,
) -> None:
    """Example stamp owned by a circuit script, not by the VDA core contract."""

    system.add_coefficient(
        output_positive, control_positive, transconductance_s
    )
    system.add_coefficient(
        output_positive, control_negative, -transconductance_s
    )
    system.add_coefficient(
        output_negative, control_positive, -transconductance_s
    )
    system.add_coefficient(
        output_negative, control_negative, transconductance_s
    )


def test_circuit_script_can_add_a_local_stamp_without_copying_the_solver() -> None:
    system = ComplexNodalSystem(["IN", "OUT", "0"])
    system.stamp_admittance("OUT", "0", 100e-6)
    _stamp_script_local_vccs(system, "OUT", "0", "IN", "0", 1e-3)

    voltages = system.solve({"IN": 1.0})

    assert voltages["OUT"] == pytest.approx(-10.0)
    assert voltages["0"] == 0j


def test_current_injection_supports_a_scripted_impedance_probe() -> None:
    system = ComplexNodalSystem(["PORT"])
    system.stamp_admittance("PORT", "0", 1.0 / 10_000.0)
    system.stamp_current_injection("PORT", "0", 1e-3)

    voltages = system.solve()

    assert voltages["PORT"] == pytest.approx(10.0)


def test_raw_linear_solver_supports_auxiliary_mna_unknowns() -> None:
    # Variables are V1, V2, and the branch current of a 1 V ideal source.
    conductance = 1.0 / 1_000.0
    solution = solve_complex_linear_system(
        [
            [conductance, 0.0, 1.0],
            [0.0, conductance, -1.0],
            [1.0, -1.0, 0.0],
        ],
        [0.0, 0.0, 1.0],
    )

    assert solution[0] == pytest.approx(0.5)
    assert solution[1] == pytest.approx(-0.5)
    assert solution[2] == pytest.approx(-0.5 * conductance)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda system: system.add_coefficient("OUT", "MISSING", 1.0), "not declared"),
        (lambda system: system.add_rhs("OUT", math.inf), "must be finite"),
        (lambda system: system.solve({"0": 1.0}), "must remain at zero"),
        (
            lambda system: system.solve(relative_pivot_tolerance=0.0),
            "must be finite and positive",
        ),
    ],
)
def test_nodal_extension_api_rejects_invalid_script_stamps(
    operation, message: str
) -> None:
    system = ComplexNodalSystem(["OUT"])

    with pytest.raises(ValueError, match=message):
        operation(system)
