"""Reusable complex linear and nodal-analysis primitives.

The strict VDA task contracts live above this module.  Circuit-specific scripts may
use these primitives to add a local stamp without copying the numerical solver.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re


_NODE_PATTERN = re.compile(r"^\S+$")


def _as_finite_complex(value: complex | float, *, label: str) -> complex:
    converted = complex(value)
    if not math.isfinite(converted.real) or not math.isfinite(converted.imag):
        raise ValueError(f"{label} must be finite")
    return converted


def solve_complex_linear_system(
    coefficient_matrix: Sequence[Sequence[complex]],
    right_hand_side: Sequence[complex],
    *,
    relative_pivot_tolerance: float = 1e-12,
) -> list[complex]:
    """Solve a finite square complex system without mutating caller data.

    This low-level entry point also permits a circuit script to assemble modified
    nodal-analysis rows with auxiliary branch-current unknowns when plain nodal
    stamping is insufficient.
    """

    if not math.isfinite(relative_pivot_tolerance) or relative_pivot_tolerance <= 0:
        raise ValueError("relative_pivot_tolerance must be finite and positive")
    size = len(right_hand_side)
    if size == 0 or len(coefficient_matrix) != size:
        raise ValueError("coefficient matrix must be nonempty and square")
    matrix: list[list[complex]] = []
    for row_index, row in enumerate(coefficient_matrix):
        if len(row) != size:
            raise ValueError("coefficient matrix must be nonempty and square")
        matrix.append(
            [
                _as_finite_complex(value, label=f"matrix[{row_index}][{column}]")
                for column, value in enumerate(row)
            ]
        )
    right = [
        _as_finite_complex(value, label=f"right_hand_side[{index}]")
        for index, value in enumerate(right_hand_side)
    ]

    augmented = [row[:] + [right[index]] for index, row in enumerate(matrix)]
    scale = max((abs(value) for row in matrix for value in row), default=0.0)
    threshold = max(scale, 1e-30) * relative_pivot_tolerance
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= threshold:
            raise ValueError("linear system matrix is singular or ill-conditioned")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]

    result = [0j] * size
    for row in range(size - 1, -1, -1):
        remainder = augmented[row][size] - sum(
            augmented[row][column] * result[column]
            for column in range(row + 1, size)
        )
        result[row] = remainder / augmented[row][row]
        _as_finite_complex(result[row], label=f"solution[{row}]")
    return result


class ComplexNodalSystem:
    """Mutable complex nodal system for one frequency point.

    Ground is always the implicit node ``0``.  ``add_coefficient`` and ``add_rhs``
    are the deliberate extension seam for circuit-specific stamps.  The convenience
    methods cover the common passive and current-injection cases.
    """

    def __init__(self, nodes: Iterable[str]) -> None:
        declared = list(nodes)
        for node in declared:
            if (
                not isinstance(node, str)
                or not node
                or not _NODE_PATTERN.fullmatch(node)
            ):
                raise ValueError(f"invalid node name {node!r}")
        normalized = tuple(sorted(set(declared) - {"0"}))
        if not normalized:
            raise ValueError("nodal system must contain at least one non-ground node")
        self._nodes = normalized
        self._indexes = {node: index for index, node in enumerate(normalized)}
        self._matrix = [[0j for _ in normalized] for _ in normalized]
        self._right_hand_side = [0j for _ in normalized]

    @property
    def nodes(self) -> tuple[str, ...]:
        return self._nodes

    def _require_node(self, node: str) -> None:
        if node != "0" and node not in self._indexes:
            raise ValueError(f"node {node!r} is not declared in this nodal system")

    def add_coefficient(
        self, row_node: str, column_node: str, value: complex | float
    ) -> None:
        """Add one coefficient to a KCL row for a circuit-specific stamp."""

        self._require_node(row_node)
        self._require_node(column_node)
        converted = _as_finite_complex(value, label="matrix coefficient")
        if row_node != "0" and column_node != "0":
            self._matrix[self._indexes[row_node]][self._indexes[column_node]] += (
                converted
            )

    def add_rhs(self, node: str, value: complex | float) -> None:
        """Add current injected into ``node`` to the KCL right-hand side."""

        self._require_node(node)
        converted = _as_finite_complex(value, label="right-hand-side value")
        if node != "0":
            self._right_hand_side[self._indexes[node]] += converted

    def stamp_admittance(
        self, positive: str, negative: str, admittance: complex | float
    ) -> None:
        """Stamp a two-terminal admittance between two declared nodes."""

        converted = _as_finite_complex(admittance, label="admittance")
        self.add_coefficient(positive, positive, converted)
        self.add_coefficient(negative, negative, converted)
        self.add_coefficient(positive, negative, -converted)
        self.add_coefficient(negative, positive, -converted)

    def stamp_current_injection(
        self, positive: str, negative: str, current: complex | float
    ) -> None:
        """Inject current into ``positive`` and withdraw it from ``negative``."""

        converted = _as_finite_complex(current, label="current injection")
        self.add_rhs(positive, converted)
        self.add_rhs(negative, -converted)

    def solve(
        self,
        fixed_voltages: Mapping[str, complex | float] | None = None,
        *,
        relative_pivot_tolerance: float = 1e-12,
    ) -> dict[str, complex]:
        """Solve unknown node voltages with ground fixed to zero."""

        fixed: dict[str, complex] = {"0": 0j}
        for node, value in (fixed_voltages or {}).items():
            self._require_node(node)
            converted = _as_finite_complex(value, label=f"fixed voltage {node}")
            if node == "0" and converted != 0j:
                raise ValueError("ground node 0 must remain at zero volts")
            fixed[node] = converted

        unknown_nodes = [node for node in self._nodes if node not in fixed]
        if not unknown_nodes:
            raise ValueError("nodal system has no unknown nodes")
        unknown_indexes = [self._indexes[node] for node in unknown_nodes]
        fixed_nodes = [node for node in self._nodes if node in fixed]
        fixed_indexes = [self._indexes[node] for node in fixed_nodes]
        unknown_matrix = [
            [self._matrix[row][column] for column in unknown_indexes]
            for row in unknown_indexes
        ]
        right_hand_side = [
            self._right_hand_side[row]
            - sum(
                self._matrix[row][column] * fixed[node]
                for node, column in zip(fixed_nodes, fixed_indexes)
            )
            for row in unknown_indexes
        ]
        solution = solve_complex_linear_system(
            unknown_matrix,
            right_hand_side,
            relative_pivot_tolerance=relative_pivot_tolerance,
        )
        voltages = dict(fixed)
        voltages.update(dict(zip(unknown_nodes, solution)))
        return voltages
