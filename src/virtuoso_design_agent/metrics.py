"""Metric extraction and constraint evaluation with no EDA dependency."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from .models import ConstraintEvaluation, MetricConstraint, Relation


class MetricExtractionError(ValueError):
    pass


def _crossings(
    time_s: Sequence[float],
    values: Sequence[float],
    threshold: float,
    *,
    rising: bool,
) -> list[float]:
    result: list[float] = []
    for index in range(1, len(values)):
        before = float(values[index - 1])
        after = float(values[index])
        crossed = before < threshold <= after if rising else before > threshold >= after
        if not crossed:
            continue
        t0 = float(time_s[index - 1])
        t1 = float(time_s[index])
        if after == before:
            result.append(t1)
            continue
        fraction = (threshold - before) / (after - before)
        result.append(t0 + fraction * (t1 - t0))
    return result


def _first_after(values: Sequence[float], after_s: float) -> float:
    for value in values:
        if value >= after_s:
            return float(value)
    raise MetricExtractionError(f"no crossing found after {after_s:.3e}s")


def _sample_window(
    time_s: Sequence[float],
    values: Sequence[float],
    start_s: float,
    end_s: float,
) -> float:
    if end_s <= start_s:
        raise MetricExtractionError("invalid steady-state sampling window")
    samples = [
        float(value)
        for time, value in zip(time_s, values, strict=True)
        if start_s <= float(time) <= end_s
    ]
    if not samples:
        raise MetricExtractionError("steady-state sampling window contains no points")
    return float(median(samples))


def extract_inverter_metrics(
    time_s: Sequence[float],
    vin_v: Sequence[float],
    vout_v: Sequence[float],
    *,
    vdd_v: float,
) -> dict[str, float]:
    """Extract first-cycle inverter timing metrics using linear interpolation."""
    if vdd_v <= 0:
        raise MetricExtractionError("vdd_v must be positive")
    if len(time_s) < 4 or len(time_s) != len(vin_v) or len(time_s) != len(vout_v):
        raise MetricExtractionError("time, VIN, and VOUT must have equal non-trivial length")
    if any(float(time_s[i]) <= float(time_s[i - 1]) for i in range(1, len(time_s))):
        raise MetricExtractionError("time values must be strictly increasing")

    mid = 0.5 * vdd_v
    input_rises = _crossings(time_s, vin_v, mid, rising=True)
    input_falls = _crossings(time_s, vin_v, mid, rising=False)
    output_rises = _crossings(time_s, vout_v, mid, rising=True)
    output_falls = _crossings(time_s, vout_v, mid, rising=False)
    if not input_rises or not input_falls:
        raise MetricExtractionError("VIN does not contain both rising and falling crossings")

    input_rise = input_rises[0]
    input_fall = _first_after(input_falls, input_rise)
    output_fall = _first_after(output_falls, input_rise)
    output_rise = _first_after(output_rises, input_fall)
    tphl_s = output_fall - input_rise
    tplh_s = output_rise - input_fall
    if tphl_s < 0 or tplh_s < 0:
        raise MetricExtractionError("negative propagation delay")

    out_90_falls = _crossings(time_s, vout_v, 0.9 * vdd_v, rising=False)
    out_10_falls = _crossings(time_s, vout_v, 0.1 * vdd_v, rising=False)
    out_10_rises = _crossings(time_s, vout_v, 0.1 * vdd_v, rising=True)
    out_90_rises = _crossings(time_s, vout_v, 0.9 * vdd_v, rising=True)
    fall_start = _first_after(out_90_falls, input_rise)
    fall_end = _first_after(out_10_falls, fall_start)
    rise_start = _first_after(out_10_rises, input_fall)
    rise_end = _first_after(out_90_rises, rise_start)

    tphl_ps = tphl_s * 1e12
    tplh_ps = tplh_s * 1e12
    rise_ps = (rise_end - rise_start) * 1e12
    fall_ps = (fall_end - fall_start) * 1e12
    low_span = input_fall - output_fall
    low_start = output_fall + 0.65 * low_span
    low_end = output_fall + 0.9 * low_span
    next_input_rises = [value for value in input_rises if value > input_fall]
    high_limit = next_input_rises[0] if next_input_rises else float(time_s[-1])
    high_span = high_limit - output_rise
    high_start = output_rise + 0.65 * high_span
    high_end = output_rise + 0.9 * high_span
    voh_v = _sample_window(time_s, vout_v, high_start, high_end)
    vol_v = _sample_window(time_s, vout_v, low_start, low_end)
    peak = max(float(value) for value in vout_v)
    trough = min(float(value) for value in vout_v)
    return {
        "tphl_ps": tphl_ps,
        "tplh_ps": tplh_ps,
        "delay_ps": 0.5 * (tphl_ps + tplh_ps),
        "rise_ps": rise_ps,
        "fall_ps": fall_ps,
        "rise_fall_skew_ps": abs(rise_ps - fall_ps),
        "voh_v": voh_v,
        "vol_v": vol_v,
        "overshoot_v": max(peak - vdd_v, 0.0),
        "undershoot_v": max(-trough, 0.0),
    }


def evaluate_constraints(
    metrics: dict[str, float], constraints: Sequence[MetricConstraint]
) -> list[ConstraintEvaluation]:
    evaluations: list[ConstraintEvaluation] = []
    for constraint in constraints:
        actual = metrics.get(constraint.metric)
        if actual is None:
            evaluations.append(
                ConstraintEvaluation(
                    metric=constraint.metric,
                    relation=constraint.relation,
                    expected=constraint.value,
                    actual=None,
                    tolerance=constraint.tolerance,
                    passed=False,
                    normalized_violation=1_000_000.0,
                    reason="metric missing",
                )
            )
            continue

        scale = max(abs(constraint.value), constraint.tolerance, 1e-12)
        if constraint.relation is Relation.LESS_OR_EQUAL:
            excess = max(actual - (constraint.value + constraint.tolerance), 0.0)
        elif constraint.relation is Relation.GREATER_OR_EQUAL:
            excess = max((constraint.value - constraint.tolerance) - actual, 0.0)
        else:
            excess = max(abs(actual - constraint.value) - constraint.tolerance, 0.0)
        evaluations.append(
            ConstraintEvaluation(
                metric=constraint.metric,
                relation=constraint.relation,
                expected=constraint.value,
                actual=actual,
                tolerance=constraint.tolerance,
                passed=excess == 0.0,
                normalized_violation=excess / scale,
            )
        )
    return evaluations
