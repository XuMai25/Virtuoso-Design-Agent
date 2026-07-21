"""Small Spectre scalar normalization helpers shared by evidence gates."""

from __future__ import annotations

import re
from typing import Any

_SPECTRE_SCALAR = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$"
)

_SPECTRE_SCALES = {
    "": 1.0,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "G": 1e9,
    "t": 1e12,
    "T": 1e12,
    "P": 1e15,
}


def spectre_scalar(value: Any) -> float | None:
    """Parse one plain Spectre engineering scalar without evaluating expressions."""

    match = _SPECTRE_SCALAR.fullmatch(str(value).strip().strip('"'))
    if match is None:
        return None
    suffix = match.group(2)
    if suffix not in _SPECTRE_SCALES:
        return None
    return float(match.group(1)) * _SPECTRE_SCALES[suffix]


def spectre_values_equal(left: Any, right: Any) -> bool:
    """Compare literal or engineering-scaled Spectre values conservatively."""

    if str(left).strip().strip('"') == str(right).strip().strip('"'):
        return True
    left_value = spectre_scalar(left)
    right_value = spectre_scalar(right)
    if left_value is None or right_value is None:
        return False
    tolerance = max(abs(left_value), abs(right_value), 1e-30) * 1e-9
    return abs(left_value - right_value) <= tolerance
