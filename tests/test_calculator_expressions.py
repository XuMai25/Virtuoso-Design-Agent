from __future__ import annotations

import pytest

from virtuoso_design_agent.calculator_expressions import calculator_expressions_equal


@pytest.mark.parametrize(
    ("requested", "readback"),
    [
        (
            'cross(clip(VT("/OUT") 80p 130p) 0.45 1 "falling" nil nil nil) '
            '- cross(clip(VT("/IN") 80p 130p) 0.45 1 "rising" nil nil nil)',
            '(cross(clip(VT("/OUT") 8e-11 1.3e-10) 0.45 1 "falling" nil '
            'nil nil) - cross(clip(VT("/IN") 8e-11 1.3e-10) 0.45 1 '
            '"rising" nil nil nil))',
        ),
        (
            '-0.9 * integ(IT("/VDD0/PLUS") cross(VT("/IN") 0.45 2 '
            '"rising" nil nil nil) cross(VT("/IN") 0.45 3 "rising" nil nil nil))',
            '(-0.9 * integ(IT("/VDD0/PLUS") cross(VT("/IN") 450m 2 '
            '"rising" nil nil nil) cross(VT("/IN") 0.45 3 "rising" nil nil nil)))',
        ),
        ('average(VT("/OUT"))', 'average(VT("/OUT"))'),
        ('bandwidth(mag(VF("/OUT")) 3 "low")', 'bandwidth(mag(VF("/OUT")) 3 "low")'),
        (
            'cross(VT("/OUT") (0.5 * VAR("VDD")) 1 "rising")',
            'cross(VT("/OUT") (500m * VAR("VDD")) 1 "rising")',
        ),
    ],
)
def test_calculator_expression_comparison_accepts_cadence_canonicalization(
    requested: str, readback: str
) -> None:
    assert calculator_expressions_equal(requested, readback)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ('cross(VT("/OUT") 0.45 1 "rising")', 'cross(VT("/OUT") 0.5 1 "rising")'),
        ('cross(VT("/OUT") 0.45 1 "rising")', 'cross(VT("/OUT") 0.45 1 "falling")'),
        ('VT("/A") - VT("/B")', 'VT("/B") - VT("/A")'),
        ('average(VT("/OUT"))', 'ymax(VT("/OUT"))'),
        (
            'cross(VT("/OUT") (0.5 * VAR("VDD")) 1 "rising")',
            'cross(VT("/OUT") (0.5 * VAR("VBIAS")) 1 "rising")',
        ),
        ('unsupported[0]', 'unsupported [ 0 ]'),
    ],
)
def test_calculator_expression_comparison_rejects_changed_or_unsupported_syntax(
    left: str, right: str
) -> None:
    assert not calculator_expressions_equal(left, right)
