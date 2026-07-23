"""Metric extraction and constraint evaluation with no EDA dependency."""

from __future__ import annotations

import cmath
import math
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


def _interpolate_at(
    time_s: Sequence[float], values: Sequence[float], target_s: float
) -> float:
    if target_s < float(time_s[0]) or target_s > float(time_s[-1]):
        raise MetricExtractionError("integration boundary is outside waveform range")
    for index in range(1, len(time_s)):
        t0 = float(time_s[index - 1])
        t1 = float(time_s[index])
        if target_s == t0:
            return float(values[index - 1])
        if target_s <= t1:
            fraction = (target_s - t0) / (t1 - t0)
            return float(values[index - 1]) + fraction * (
                float(values[index]) - float(values[index - 1])
            )
    if target_s == float(time_s[-1]):
        return float(values[-1])
    raise MetricExtractionError("integration boundary is outside waveform range")


def _integrate_window(
    time_s: Sequence[float], values: Sequence[float], start_s: float, end_s: float
) -> float:
    if end_s <= start_s:
        raise MetricExtractionError("invalid integration window")
    points = [(start_s, _interpolate_at(time_s, values, start_s))]
    points.extend(
        (float(time), float(value))
        for time, value in zip(time_s, values, strict=True)
        if start_s < float(time) < end_s
    )
    points.append((end_s, _interpolate_at(time_s, values, end_s)))
    return sum(
        0.5 * (before_value + after_value) * (after_time - before_time)
        for (before_time, before_value), (after_time, after_value) in zip(
            points, points[1:]
        )
    )


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


def extract_supply_metrics(
    time_s: Sequence[float],
    vin_v: Sequence[float],
    supply_current_a: Sequence[float],
    *,
    vdd_v: float,
) -> dict[str, float]:
    """Integrate total supply energy between two consecutive VIN rising edges.

    The result includes leakage during the cycle; it is not pure switching energy.
    """
    if vdd_v <= 0:
        raise MetricExtractionError("vdd_v must be positive")
    if (
        len(time_s) < 4
        or len(time_s) != len(vin_v)
        or len(time_s) != len(supply_current_a)
    ):
        raise MetricExtractionError(
            "time, VIN, and supply current must have equal non-trivial length"
        )
    if any(float(time_s[i]) <= float(time_s[i - 1]) for i in range(1, len(time_s))):
        raise MetricExtractionError("time values must be strictly increasing")

    input_rises = _crossings(time_s, vin_v, 0.5 * vdd_v, rising=True)
    if len(input_rises) < 2:
        raise MetricExtractionError(
            "VIN does not contain two rising crossings for a complete supply cycle"
        )
    start_s, end_s = input_rises[:2]
    period_s = end_s - start_s
    source_charge_c = -_integrate_window(
        time_s, supply_current_a, start_s, end_s
    )
    if source_charge_c <= 0:
        raise MetricExtractionError(
            "integrated supply current has unexpected polarity or zero energy"
        )
    energy_j = source_charge_c * vdd_v
    return {
        "supply_cycle_period_ps": period_s * 1e12,
        "supply_energy_per_cycle_fj": energy_j * 1e15,
        "average_supply_power_uw": energy_j / period_s * 1e6,
    }


def extract_dc_supply_metrics(
    *, vdd_v: float, supply_source_current_a: float
) -> dict[str, float]:
    """Convert the actual VDD source current into consumed DC power.

    ``VDD_SRC`` is oriented from VDD to ground in the generated testbench, so a
    circuit drawing power produces a negative source-terminal current.
    """
    if not math.isfinite(float(vdd_v)) or vdd_v <= 0:
        raise MetricExtractionError("DC supply voltage must be finite and positive")
    if not math.isfinite(float(supply_source_current_a)):
        raise MetricExtractionError("DC supply source current must be finite")
    consumed_current_a = -float(supply_source_current_a)
    if consumed_current_a <= 0:
        raise MetricExtractionError(
            "DC supply source current has unexpected polarity or zero power"
        )
    return {
        "supply_current_ua": consumed_current_a * 1e6,
        "dc_supply_power_uw": consumed_current_a * float(vdd_v) * 1e6,
    }


def _window_with_boundaries(
    time_s: Sequence[float],
    values: Sequence[float],
    start_s: float,
    end_s: float,
) -> tuple[list[float], list[float]]:
    points_time = [float(start_s)]
    points_value = [_interpolate_at(time_s, values, start_s)]
    for time, value in zip(time_s, values, strict=True):
        numeric_time = float(time)
        if start_s < numeric_time < end_s:
            points_time.append(numeric_time)
            points_value.append(float(value))
    points_time.append(float(end_s))
    points_value.append(_interpolate_at(time_s, values, end_s))
    return points_time, points_value


def _fourier_peak_amplitude(
    time_s: Sequence[float],
    values: Sequence[float],
    *,
    frequency_hz: float,
    harmonic: int,
    start_s: float,
    end_s: float,
) -> float:
    window_time, window_values = _window_with_boundaries(
        time_s, values, start_s, end_s
    )
    angular_frequency = 2.0 * math.pi * frequency_hz * harmonic
    samples = [
        value * cmath.exp(-1j * angular_frequency * time)
        for time, value in zip(window_time, window_values, strict=True)
    ]
    integral = sum(
        0.5 * (before_value + after_value) * (after_time - before_time)
        for before_time, after_time, before_value, after_value in zip(
            window_time[:-1],
            window_time[1:],
            samples[:-1],
            samples[1:],
            strict=True,
        )
    )
    return 2.0 * abs(integral / (end_s - start_s))


def extract_common_source_linearity_point_metrics(
    time_s: Sequence[float],
    vin_v: Sequence[float],
    vout_v: Sequence[float],
    supply_current_a: Sequence[float],
    *,
    vdd_v: float,
    frequency_hz: float,
    settling_cycles: int,
    measurement_cycles: int,
    max_harmonic: int,
) -> tuple[dict[str, float], dict[str, object]]:
    """Measure coherent sine gain, harmonics, THD, and VDD power for one amplitude."""
    lengths = {len(time_s), len(vin_v), len(vout_v), len(supply_current_a)}
    if len(lengths) != 1 or len(time_s) < 8:
        raise MetricExtractionError(
            "linearity time, VIN, VOUT, and supply current must have equal non-trivial length"
        )
    if frequency_hz <= 0 or not math.isfinite(frequency_hz):
        raise MetricExtractionError("linearity frequency must be finite and positive")
    if settling_cycles < 1 or measurement_cycles < 2 or max_harmonic < 2:
        raise MetricExtractionError("invalid linearity cycle or harmonic settings")

    times = [float(value) for value in time_s]
    inputs = [float(value) for value in vin_v]
    outputs = [float(value) for value in vout_v]
    currents = [float(value) for value in supply_current_a]
    if any(
        not math.isfinite(value)
        for value in times + inputs + outputs + currents
    ):
        raise MetricExtractionError("linearity waveforms must be finite")
    if any(times[index] <= times[index - 1] for index in range(1, len(times))):
        raise MetricExtractionError("linearity time values must be strictly increasing")

    period_s = 1.0 / frequency_hz
    start_s = settling_cycles * period_s
    end_s = (settling_cycles + measurement_cycles) * period_s
    if times[0] > start_s or times[-1] < end_s:
        raise MetricExtractionError(
            "linearity waveform does not cover the declared coherent measurement window"
        )

    input_fundamental = _fourier_peak_amplitude(
        times,
        inputs,
        frequency_hz=frequency_hz,
        harmonic=1,
        start_s=start_s,
        end_s=end_s,
    )
    output_harmonics = {
        harmonic: _fourier_peak_amplitude(
            times,
            outputs,
            frequency_hz=frequency_hz,
            harmonic=harmonic,
            start_s=start_s,
            end_s=end_s,
        )
        for harmonic in range(1, max_harmonic + 1)
    }
    output_fundamental = output_harmonics[1]
    if input_fundamental <= 1e-15 or output_fundamental <= 1e-15:
        raise MetricExtractionError(
            "linearity fundamental amplitude is missing or below the numeric floor"
        )

    harmonic_floor = output_fundamental * 1e-15
    harmonic_power = sum(
        amplitude * amplitude
        for harmonic, amplitude in output_harmonics.items()
        if harmonic >= 2
    )
    source_charge_c = -_integrate_window(times, currents, start_s, end_s)
    if source_charge_c <= 0:
        raise MetricExtractionError(
            "linearity supply current has unexpected polarity or zero energy"
        )
    duration_s = end_s - start_s
    energy_j = source_charge_c * vdd_v
    window_time, window_output = _window_with_boundaries(
        times, outputs, start_s, end_s
    )
    metrics: dict[str, float] = {
        "input_fundamental_v_peak": input_fundamental,
        "output_fundamental_v_peak": output_fundamental,
        "large_signal_gain_v_per_v": output_fundamental / input_fundamental,
        "thd_percent": math.sqrt(harmonic_power) / output_fundamental * 100.0,
        "average_supply_power_uw": energy_j / duration_s * 1e6,
        "supply_energy_per_cycle_fj": energy_j / measurement_cycles * 1e15,
        "output_dc_v": _integrate_window(
            times, outputs, start_s, end_s
        )
        / duration_s,
        "output_min_v": min(window_output),
        "output_max_v": max(window_output),
        "output_peak_to_peak_v": max(window_output) - min(window_output),
    }
    for harmonic, amplitude in output_harmonics.items():
        metrics[f"output_harmonic_{harmonic}_v_peak"] = amplitude
        if harmonic >= 2:
            metrics[f"hd{harmonic}_dbc"] = 20.0 * math.log10(
                max(amplitude, harmonic_floor) / output_fundamental
            )
    return metrics, {
        "analysis_complete": True,
        "frequency_hz": frequency_hz,
        "measurement_window_s": [start_s, end_s],
        "settling_cycles": settling_cycles,
        "measurement_cycles": measurement_cycles,
        "sample_count": len(window_time),
        "max_harmonic": max_harmonic,
        "harmonic_numeric_floor_dbc": -300.0,
        "fourier_method": "trapezoidal coherent-window projection",
        "supply_power_method": "-integral(VDD_SRC:p) * VDD / measurement_time",
    }


def aggregate_common_source_linearity_metrics(
    amplitudes_v: Sequence[float],
    point_metrics: Sequence[dict[str, float]],
    *,
    compression_db: float = 1.0,
) -> tuple[dict[str, float], dict[str, object]]:
    """Aggregate an ordered amplitude sweep without inventing a missing P1dB."""
    if len(amplitudes_v) < 2 or len(amplitudes_v) != len(point_metrics):
        raise MetricExtractionError(
            "linearity amplitudes and point metrics must have equal length of at least two"
        )
    amplitudes = [float(value) for value in amplitudes_v]
    if any(
        not math.isfinite(value) or value <= 0 for value in amplitudes
    ) or any(
        amplitudes[index] <= amplitudes[index - 1]
        for index in range(1, len(amplitudes))
    ):
        raise MetricExtractionError(
            "linearity amplitudes must be finite, positive, and strictly increasing"
        )
    if compression_db <= 0 or not math.isfinite(compression_db):
        raise MetricExtractionError("compression_db must be finite and positive")

    required = {
        "large_signal_gain_v_per_v",
        "output_fundamental_v_peak",
        "thd_percent",
        "average_supply_power_uw",
        "output_peak_to_peak_v",
    }
    if any(not required <= metrics.keys() for metrics in point_metrics):
        raise MetricExtractionError("linearity point metrics are incomplete")
    gains = [float(metrics["large_signal_gain_v_per_v"]) for metrics in point_metrics]
    if any(not math.isfinite(value) or value <= 0 for value in gains):
        raise MetricExtractionError("linearity gains must be finite and positive")
    gain_db = [20.0 * math.log10(value) for value in gains]
    compression = [gain_db[0] - value for value in gain_db]
    last = point_metrics[-1]
    metrics = {
        "small_signal_gain_v_per_v": gains[0],
        "small_signal_gain_db": gain_db[0],
        "gain_at_max_amplitude_v_per_v": gains[-1],
        "gain_compression_at_max_db": compression[-1],
        "input_amplitude_max_v_peak": amplitudes[-1],
        "output_at_max_amplitude_v_peak": float(
            last["output_fundamental_v_peak"]
        ),
        "output_peak_to_peak_at_max_amplitude_v": float(
            last["output_peak_to_peak_v"]
        ),
        "thd_at_max_amplitude_percent": float(last["thd_percent"]),
        "max_thd_percent": max(
            float(item["thd_percent"]) for item in point_metrics
        ),
        "small_signal_supply_power_uw": float(
            point_metrics[0]["average_supply_power_uw"]
        ),
        "large_signal_supply_power_uw": float(last["average_supply_power_uw"]),
        "max_average_supply_power_uw": max(
            float(item["average_supply_power_uw"]) for item in point_metrics
        ),
    }
    for name in ("hd2_dbc", "hd3_dbc"):
        if name in last:
            metrics[f"{name[:-4]}_at_max_amplitude_dbc"] = float(last[name])

    crossing: dict[str, object] = {
        "status": "unresolved",
        "reason": "declared amplitude sweep does not reach gain compression",
        "target_compression_db": compression_db,
    }
    for index in range(1, len(amplitudes)):
        if compression[index] < compression_db:
            continue
        before = compression[index - 1]
        after = compression[index]
        fraction = 1.0 if after == before else (compression_db - before) / (after - before)
        fraction = min(max(fraction, 0.0), 1.0)
        input_compression = amplitudes[index - 1] + fraction * (
            amplitudes[index] - amplitudes[index - 1]
        )
        before_output = float(point_metrics[index - 1]["output_fundamental_v_peak"])
        after_output = float(point_metrics[index]["output_fundamental_v_peak"])
        output_compression = before_output + fraction * (
            after_output - before_output
        )
        metrics["input_1db_compression_v_peak"] = input_compression
        metrics["output_1db_compression_v_peak"] = output_compression
        crossing = {
            "status": "resolved",
            "target_compression_db": compression_db,
            "bracket_amplitudes_v_peak": [
                amplitudes[index - 1],
                amplitudes[index],
            ],
            "interpolation_fraction": fraction,
            "interpolation": "linear input amplitude versus gain compression dB",
        }
        break

    warnings: list[str] = []
    if crossing["status"] != "resolved":
        warnings.append(
            "input_1db_compression_v_peak unresolved within declared amplitudes"
        )
    if any(value < -1e-6 for value in compression[1:]):
        warnings.append("gain expansion appears before compression")
    return metrics, {
        "analysis_complete": True,
        "reference": "first declared amplitude",
        "p1db": crossing,
        "warnings": warnings,
        "points": [
            {
                "input_amplitude_v_peak": amplitude,
                "gain_v_per_v": gain,
                "gain_compression_db": compression_value,
                "thd_percent": float(point["thd_percent"]),
                "average_supply_power_uw": float(
                    point["average_supply_power_uw"]
                ),
            }
            for amplitude, gain, compression_value, point in zip(
                amplitudes, gains, compression, point_metrics, strict=True
            )
        ],
    }


def aggregate_differential_pair_linearity_metrics(
    amplitudes_v: Sequence[float],
    point_metrics: Sequence[dict[str, float]],
    *,
    compression_db: float = 1.0,
) -> tuple[dict[str, float], dict[str, object]]:
    """Namespace a differential-input/differential-output linearity sweep."""
    raw_metrics, diagnostics = aggregate_common_source_linearity_metrics(
        amplitudes_v,
        point_metrics,
        compression_db=compression_db,
    )
    metrics = {
        (
            f"transient_{name}"
            if "supply_power" in name
            else f"differential_{name}"
        ): value
        for name, value in raw_metrics.items()
    }
    diagnostics.update(
        {
            "input_definition": "VINP-VINN differential peak amplitude",
            "output_definition": "VOUTP-VOUTN differential waveform",
            "metric_namespace": (
                "differential_* for transfer/linearity and transient_* for "
                "whole-circuit supply power"
            ),
        }
    )
    return metrics, diagnostics


def extract_common_source_noise_metrics(
    frequency_hz: Sequence[float],
    output_noise_v_per_sqrt_hz: Sequence[float],
    input_noise_v_per_sqrt_hz: Sequence[float],
) -> tuple[dict[str, float], dict[str, object]]:
    """Integrate output and input-referred voltage-noise densities over the sweep."""
    if (
        len(frequency_hz) < 2
        or len(frequency_hz) != len(output_noise_v_per_sqrt_hz)
        or len(frequency_hz) != len(input_noise_v_per_sqrt_hz)
    ):
        raise MetricExtractionError(
            "noise frequency, output density, and input density must have equal non-trivial length"
        )
    frequencies = [float(value) for value in frequency_hz]
    output_density = [float(value) for value in output_noise_v_per_sqrt_hz]
    input_density = [float(value) for value in input_noise_v_per_sqrt_hz]
    if any(
        not math.isfinite(value) or value <= 0 for value in frequencies
    ):
        raise MetricExtractionError("noise frequencies must be finite and positive")
    if any(
        frequencies[index] <= frequencies[index - 1]
        for index in range(1, len(frequencies))
    ):
        raise MetricExtractionError("noise frequencies must be strictly increasing")
    if any(
        not math.isfinite(value) or value < 0
        for value in output_density + input_density
    ):
        raise MetricExtractionError("noise densities must be finite and non-negative")
    start_hz, stop_hz = frequencies[0], frequencies[-1]
    output_variance = _integrate_window(
        frequencies,
        [value * value for value in output_density],
        start_hz,
        stop_hz,
    )
    input_variance = _integrate_window(
        frequencies,
        [value * value for value in input_density],
        start_hz,
        stop_hz,
    )
    return {
        "integrated_output_noise_v_rms": math.sqrt(output_variance),
        "integrated_output_noise_uv_rms": math.sqrt(output_variance) * 1e6,
        "integrated_input_referred_noise_v_rms": math.sqrt(input_variance),
        "integrated_input_referred_noise_uv_rms": math.sqrt(input_variance) * 1e6,
        "output_noise_density_start_nv_per_sqrt_hz": output_density[0] * 1e9,
        "output_noise_density_stop_nv_per_sqrt_hz": output_density[-1] * 1e9,
        "input_noise_density_start_nv_per_sqrt_hz": input_density[0] * 1e9,
        "input_noise_density_stop_nv_per_sqrt_hz": input_density[-1] * 1e9,
    }, {
        "analysis_complete": True,
        "sample_count": len(frequencies),
        "integration_band_hz": [start_hz, stop_hz],
        "integration_method": "trapezoidal integral of voltage-noise density squared",
        "density_units": "V/sqrt(Hz)",
    }


def extract_common_source_dc_metrics(
    *,
    vdd_v: float,
    vin_v: float,
    vout_v: float,
    vss_v: float,
    drain_current_a: float,
    vdsat_v: float,
    gm_s: float,
    gds_s: float,
    load_resistance_ohm: float,
) -> dict[str, float]:
    """Derive common-source DC metrics from a completed Spectre OP result."""
    values = {
        "vdd_v": vdd_v,
        "vin_v": vin_v,
        "vout_v": vout_v,
        "vss_v": vss_v,
        "drain_current_a": drain_current_a,
        "vdsat_v": vdsat_v,
        "gm_s": gm_s,
        "gds_s": gds_s,
        "load_resistance_ohm": load_resistance_ohm,
    }
    if any(not math.isfinite(float(value)) for value in values.values()):
        raise MetricExtractionError("common-source operating-point values must be finite")
    if vdd_v <= vss_v:
        raise MetricExtractionError("vdd_v must be greater than vss_v")
    if load_resistance_ohm <= 0:
        raise MetricExtractionError("load resistance must be positive")
    if gds_s <= 0:
        raise MetricExtractionError("common-source gds must be positive")

    drain_current_a = abs(float(drain_current_a))
    vdsat_v = abs(float(vdsat_v))
    gm_s = abs(float(gm_s))
    gds_s = float(gds_s)
    vgs_v = float(vin_v) - float(vss_v)
    vds_v = float(vout_v) - float(vss_v)
    upper_headroom_v = float(vdd_v) - float(vout_v)
    saturation_margin_v = vds_v - vdsat_v
    resistor_current_a = upper_headroom_v / float(load_resistance_ohm)
    current_scale_a = max(drain_current_a, abs(resistor_current_a), 1e-18)
    current_mismatch_percent = (
        abs(drain_current_a - abs(resistor_current_a)) / current_scale_a * 100.0
    )

    return {
        "drain_current_ua": drain_current_a * 1e6,
        "vgs_v": vgs_v,
        "vds_v": vds_v,
        "vdsat_v": vdsat_v,
        "saturation_margin_v": saturation_margin_v,
        "upper_output_headroom_v": upper_headroom_v,
        "lower_saturation_headroom_v": saturation_margin_v,
        "output_swing_margin_v": min(upper_headroom_v, saturation_margin_v),
        "gm_us": gm_s * 1e6,
        "gds_us": gds_s * 1e6,
        "intrinsic_gain_v_per_v": gm_s / gds_s,
        "resistor_current_ua": abs(resistor_current_a) * 1e6,
        "current_mismatch_percent": current_mismatch_percent,
        "saturation_region": float(
            drain_current_a > 0.0 and saturation_margin_v >= 0.0
        ),
    }


def extract_differential_pair_dc_metrics(
    *,
    vdd_v: float,
    common_mode_v: float,
    outp_v: float,
    outn_v: float,
    tail_v: float,
    branch_p_current_a: float,
    branch_n_current_a: float,
    tail_source_current_a: float,
    supply_source_current_a: float,
    branch_p_vdsat_v: float,
    branch_n_vdsat_v: float,
    branch_p_gm_s: float,
    branch_n_gm_s: float,
    branch_p_gds_s: float,
    branch_n_gds_s: float,
    load_resistance_ohm: float,
    branch_p_source_v: float | None = None,
    branch_n_source_v: float | None = None,
) -> dict[str, float]:
    """Derive symmetric resistive-load NMOS differential-pair DC metrics.

    Branch ``p`` is MN0/INP/OUTP and branch ``n`` is MN1/INN/OUTN.  Current
    arguments retain their raw simulator polarity at the worker boundary; this
    pure metric layer compares magnitudes and reports every independent KCL
    residual instead of assuming an ideal split.
    """
    values = {
        "vdd_v": vdd_v,
        "common_mode_v": common_mode_v,
        "outp_v": outp_v,
        "outn_v": outn_v,
        "tail_v": tail_v,
        "branch_p_current_a": branch_p_current_a,
        "branch_n_current_a": branch_n_current_a,
        "tail_source_current_a": tail_source_current_a,
        "supply_source_current_a": supply_source_current_a,
        "branch_p_vdsat_v": branch_p_vdsat_v,
        "branch_n_vdsat_v": branch_n_vdsat_v,
        "branch_p_gm_s": branch_p_gm_s,
        "branch_n_gm_s": branch_n_gm_s,
        "branch_p_gds_s": branch_p_gds_s,
        "branch_n_gds_s": branch_n_gds_s,
        "load_resistance_ohm": load_resistance_ohm,
    }
    if branch_p_source_v is not None:
        values["branch_p_source_v"] = branch_p_source_v
    if branch_n_source_v is not None:
        values["branch_n_source_v"] = branch_n_source_v
    if any(not math.isfinite(float(value)) for value in values.values()):
        raise MetricExtractionError(
            "differential-pair operating-point values must be finite"
        )
    if vdd_v <= 0 or load_resistance_ohm <= 0:
        raise MetricExtractionError(
            "differential-pair supply and load resistance must be positive"
        )
    if branch_p_gds_s <= 0 or branch_n_gds_s <= 0:
        raise MetricExtractionError(
            "differential-pair branch gds values must be positive"
        )

    branch_p_current = abs(float(branch_p_current_a))
    branch_n_current = abs(float(branch_n_current_a))
    tail_current = abs(float(tail_source_current_a))
    supply_current = abs(float(supply_source_current_a))
    branch_sum = branch_p_current + branch_n_current
    branch_scale = max(branch_p_current, branch_n_current, 1e-18)
    total_scale = max(branch_sum, tail_current, supply_current, 1e-18)

    branch_p_vdsat = abs(float(branch_p_vdsat_v))
    branch_n_vdsat = abs(float(branch_n_vdsat_v))
    source_p_v = (
        float(tail_v)
        if branch_p_source_v is None
        else float(branch_p_source_v)
    )
    source_n_v = (
        float(tail_v)
        if branch_n_source_v is None
        else float(branch_n_source_v)
    )
    branch_p_vds = float(outp_v) - source_p_v
    branch_n_vds = float(outn_v) - source_n_v
    branch_p_margin = branch_p_vds - branch_p_vdsat
    branch_n_margin = branch_n_vds - branch_n_vdsat
    upper_p_headroom = float(vdd_v) - float(outp_v)
    upper_n_headroom = float(vdd_v) - float(outn_v)
    resistor_p_current = abs(upper_p_headroom / float(load_resistance_ohm))
    resistor_n_current = abs(upper_n_headroom / float(load_resistance_ohm))
    load_p_scale = max(branch_p_current, resistor_p_current, 1e-18)
    load_n_scale = max(branch_n_current, resistor_n_current, 1e-18)
    load_p_mismatch = (
        abs(branch_p_current - resistor_p_current) / load_p_scale * 100.0
    )
    load_n_mismatch = (
        abs(branch_n_current - resistor_n_current) / load_n_scale * 100.0
    )

    gm_p = abs(float(branch_p_gm_s))
    gm_n = abs(float(branch_n_gm_s))
    gds_p = float(branch_p_gds_s)
    gds_n = float(branch_n_gds_s)
    output_offset = float(outp_v) - float(outn_v)
    minimum_saturation_margin = min(branch_p_margin, branch_n_margin)
    minimum_upper_headroom = min(upper_p_headroom, upper_n_headroom)
    return {
        "branch_p_current_ua": branch_p_current * 1e6,
        "branch_n_current_ua": branch_n_current * 1e6,
        "branch_current_sum_ua": branch_sum * 1e6,
        "tail_current_ua": tail_current * 1e6,
        "supply_current_ua": supply_current * 1e6,
        "branch_current_mismatch_percent": (
            abs(branch_p_current - branch_n_current) / branch_scale * 100.0
        ),
        "tail_current_mismatch_percent": (
            abs(branch_sum - tail_current) / total_scale * 100.0
        ),
        "supply_current_mismatch_percent": (
            abs(branch_sum - supply_current) / total_scale * 100.0
        ),
        "load_p_current_ua": resistor_p_current * 1e6,
        "load_n_current_ua": resistor_n_current * 1e6,
        "load_p_current_mismatch_percent": load_p_mismatch,
        "load_n_current_mismatch_percent": load_n_mismatch,
        "max_load_current_mismatch_percent": max(
            load_p_mismatch, load_n_mismatch
        ),
        "common_mode_input_v": float(common_mode_v),
        "tail_voltage_v": float(tail_v),
        "branch_p_source_v": source_p_v,
        "branch_n_source_v": source_n_v,
        "branch_p_vgs_v": float(common_mode_v) - source_p_v,
        "branch_n_vgs_v": float(common_mode_v) - source_n_v,
        "branch_p_vds_v": branch_p_vds,
        "branch_n_vds_v": branch_n_vds,
        "branch_p_vdsat_v": branch_p_vdsat,
        "branch_n_vdsat_v": branch_n_vdsat,
        "branch_p_saturation_margin_v": branch_p_margin,
        "branch_n_saturation_margin_v": branch_n_margin,
        "minimum_saturation_margin_v": minimum_saturation_margin,
        "both_saturation_region": float(
            branch_p_current > 0.0
            and branch_n_current > 0.0
            and minimum_saturation_margin >= 0.0
        ),
        "output_common_mode_v": 0.5 * (float(outp_v) + float(outn_v)),
        "output_differential_v": output_offset,
        "output_offset_abs_mv": abs(output_offset) * 1e3,
        "minimum_upper_output_headroom_v": minimum_upper_headroom,
        "minimum_output_swing_margin_v": min(
            minimum_upper_headroom, minimum_saturation_margin
        ),
        "branch_p_gm_us": gm_p * 1e6,
        "branch_n_gm_us": gm_n * 1e6,
        "minimum_branch_gm_us": min(gm_p, gm_n) * 1e6,
        "branch_p_gds_us": gds_p * 1e6,
        "branch_n_gds_us": gds_n * 1e6,
        "minimum_intrinsic_gain_v_per_v": min(gm_p / gds_p, gm_n / gds_n),
        "dc_supply_power_uw": supply_current * float(vdd_v) * 1e6,
    }


def _log_frequency_crossing(
    frequency_before_hz: float,
    frequency_after_hz: float,
    value_before: float,
    value_after: float,
    target: float,
) -> tuple[float, float]:
    if value_after == value_before:
        return frequency_after_hz, 1.0
    fraction = (target - value_before) / (value_after - value_before)
    fraction = min(max(fraction, 0.0), 1.0)
    log_frequency = math.log10(frequency_before_hz) + fraction * (
        math.log10(frequency_after_hz) - math.log10(frequency_before_hz)
    )
    return 10.0**log_frequency, fraction


def _unwrapped_phase_degrees(values: Sequence[complex]) -> list[float]:
    phases = [cmath.phase(value) for value in values]
    for index in range(1, len(phases)):
        delta = phases[index] - phases[index - 1]
        while delta > math.pi:
            phases[index] -= 2.0 * math.pi
            delta -= 2.0 * math.pi
        while delta < -math.pi:
            phases[index] += 2.0 * math.pi
            delta += 2.0 * math.pi
    return [math.degrees(value) for value in phases]


def extract_common_source_ac_metrics(
    frequency_hz: Sequence[float],
    vin_v: Sequence[complex | float],
    vout_v: Sequence[complex | float],
    *,
    reference_points: int = 5,
    max_reference_variation_db: float = 0.5,
) -> tuple[dict[str, float], dict[str, object]]:
    """Extract common-source small-signal gain and bandwidth from complex AC data.

    Bandwidth is the first downward half-power crossing relative to the complex
    mean of the first ``reference_points`` transfer samples. Crossing frequency
    is interpolated linearly in dB over log-frequency. GBW is deliberately kept
    distinct from unity-gain frequency.
    """
    if reference_points < 2:
        raise MetricExtractionError("AC reference_points must be at least 2")
    if max_reference_variation_db <= 0 or not math.isfinite(
        max_reference_variation_db
    ):
        raise MetricExtractionError(
            "AC max_reference_variation_db must be finite and positive"
        )
    if (
        len(frequency_hz) < max(3, reference_points + 1)
        or len(frequency_hz) != len(vin_v)
        or len(frequency_hz) != len(vout_v)
    ):
        raise MetricExtractionError(
            "frequency, VIN, and VOUT must have equal length beyond the reference window"
        )

    try:
        frequencies = [float(value) for value in frequency_hz]
    except (TypeError, ValueError) as exc:
        raise MetricExtractionError("AC frequencies must be numeric") from exc
    if any(not math.isfinite(value) or value <= 0 for value in frequencies):
        raise MetricExtractionError("AC frequencies must be finite and positive")
    if any(
        frequencies[index] <= frequencies[index - 1]
        for index in range(1, len(frequencies))
    ):
        raise MetricExtractionError("AC frequencies must be strictly increasing")

    try:
        inputs = [complex(value) for value in vin_v]
        outputs = [complex(value) for value in vout_v]
    except (TypeError, ValueError) as exc:
        raise MetricExtractionError("AC VIN and VOUT values must be numeric") from exc
    if any(
        not math.isfinite(value.real) or not math.isfinite(value.imag)
        for value in inputs + outputs
    ):
        raise MetricExtractionError("AC VIN and VOUT values must be finite")
    if any(abs(value) <= 1e-30 for value in inputs):
        raise MetricExtractionError("AC VIN contains a zero small-signal value")

    transfer = [output / input_value for input_value, output in zip(inputs, outputs)]
    reference_transfer = sum(transfer[:reference_points], 0j) / reference_points
    reference_gain = abs(reference_transfer)
    if not math.isfinite(reference_gain) or reference_gain <= 1e-30:
        raise MetricExtractionError("AC low-frequency reference gain is zero or invalid")

    floor = 1e-300
    gain_db = [20.0 * math.log10(max(abs(value), floor)) for value in transfer]
    phase_deg = _unwrapped_phase_degrees(transfer)
    reference_gain_db = 20.0 * math.log10(reference_gain)
    reference_phase_deg = math.degrees(cmath.phase(reference_transfer))
    reference_window_db = gain_db[:reference_points]
    reference_variation_db = max(reference_window_db) - min(reference_window_db)
    threshold_db = reference_gain_db - 10.0 * math.log10(2.0)
    peak_gain_db = max(gain_db)
    peak_index = gain_db.index(peak_gain_db)

    metrics: dict[str, float] = {
        "low_frequency_gain_v_per_v": reference_gain,
        "low_frequency_gain_db": reference_gain_db,
        "low_frequency_phase_deg": reference_phase_deg,
        "peak_gain_db": peak_gain_db,
        "peak_gain_frequency_hz": frequencies[peak_index],
        "gain_peaking_db": peak_gain_db - reference_gain_db,
    }
    diagnostics: dict[str, object] = {
        "analysis_complete": False,
        "issues": [],
        "warnings": [],
        "sample_count": len(frequencies),
        "sweep_start_hz": frequencies[0],
        "sweep_stop_hz": frequencies[-1],
        "reference": {
            "method": "complex_mean_of_first_points",
            "points": reference_points,
            "variation_db": reference_variation_db,
            "variation_limit_db": max_reference_variation_db,
            "status": (
                "flat"
                if reference_variation_db <= max_reference_variation_db
                else "not_flat"
            ),
        },
        "bandwidth": {
            "definition": "first downward half-power crossing from low-frequency reference",
            "threshold_db": threshold_db,
            "interpolation": "linear_dB_over_log10_frequency",
            "status": "unresolved",
        },
        "unity_gain": {
            "definition": "first downward |VOUT/VIN| = 1 crossing",
            "interpolation": "linear_dB_over_log10_frequency",
            "status": "unresolved",
        },
        "gbw": {
            "definition": "low_frequency_gain_v_per_v * bandwidth_3db_hz",
            "status": "unresolved",
        },
    }
    issues = diagnostics["issues"]
    warnings = diagnostics["warnings"]
    assert isinstance(issues, list)
    assert isinstance(warnings, list)
    bandwidth = diagnostics["bandwidth"]
    unity_gain = diagnostics["unity_gain"]
    gbw = diagnostics["gbw"]
    assert isinstance(bandwidth, dict)
    assert isinstance(unity_gain, dict)
    assert isinstance(gbw, dict)

    if reference_variation_db > max_reference_variation_db:
        reason = "low_frequency_reference_not_flat"
        bandwidth["reason"] = reason
        unity_gain["reason"] = reason
        gbw["reason"] = "bandwidth_unresolved"
        issues.append(f"bandwidth_3db_hz unresolved: {reason}")
        return metrics, diagnostics

    def downward_crossings(level_db: float) -> list[tuple[int, float, float]]:
        crossings: list[tuple[int, float, float]] = []
        for index in range(reference_points, len(gain_db)):
            before = gain_db[index - 1]
            after = gain_db[index]
            if not (
                before >= level_db
                and after <= level_db
                and (before > level_db or after < level_db)
            ):
                continue
            crossing_hz, fraction = _log_frequency_crossing(
                frequencies[index - 1],
                frequencies[index],
                before,
                after,
                level_db,
            )
            crossings.append((index, crossing_hz, fraction))
        return crossings

    bandwidth_crossings = downward_crossings(threshold_db)
    if bandwidth_crossings:
        index, crossing_hz, fraction = bandwidth_crossings[0]
        crossing_phase_deg = phase_deg[index - 1] + fraction * (
            phase_deg[index] - phase_deg[index - 1]
        )
        metrics["bandwidth_3db_hz"] = crossing_hz
        metrics["phase_at_bandwidth_deg"] = crossing_phase_deg
        metrics["gain_bandwidth_product_hz"] = reference_gain * crossing_hz
        bandwidth.update(
            {
                "status": "resolved",
                "frequency_hz": crossing_hz,
                "crossing_count": len(bandwidth_crossings),
                "bracket_hz": [frequencies[index - 1], frequencies[index]],
            }
        )
        gbw.update(
            {
                "status": "resolved",
                "value_hz": metrics["gain_bandwidth_product_hz"],
            }
        )
        diagnostics["analysis_complete"] = True
        if len(bandwidth_crossings) > 1:
            warnings.append(
                "multiple downward half-power crossings; first crossing used"
            )
        if any(
            gain_db[index - 1] < threshold_db <= gain_db[index]
            for index in range(bandwidth_crossings[0][0] + 1, len(gain_db))
        ):
            warnings.append(
                "response re-crosses the half-power threshold after bandwidth"
            )
    else:
        reason = (
            "sweep_stop_below_first_minus_3db_crossing"
            if gain_db[-1] > threshold_db
            else "no_downward_crossing_after_reference"
        )
        bandwidth["reason"] = reason
        gbw["reason"] = "bandwidth_unresolved"
        issues.append(f"bandwidth_3db_hz unresolved: {reason}")

    if reference_gain > 1.0:
        unity_crossings = downward_crossings(0.0)
        if unity_crossings:
            index, crossing_hz, fraction = unity_crossings[0]
            metrics["unity_gain_frequency_hz"] = crossing_hz
            metrics["phase_at_unity_gain_deg"] = phase_deg[index - 1] + fraction * (
                phase_deg[index] - phase_deg[index - 1]
            )
            unity_gain.update(
                {
                    "status": "resolved",
                    "frequency_hz": crossing_hz,
                    "crossing_count": len(unity_crossings),
                    "bracket_hz": [frequencies[index - 1], frequencies[index]],
                }
            )
        else:
            reason = "sweep_stop_below_unity_gain_crossing"
            unity_gain["reason"] = reason
            warnings.append(f"unity_gain_frequency_hz unresolved: {reason}")
    else:
        unity_gain.update(
            {
                "status": "not_applicable",
                "reason": "low_frequency_gain_at_or_below_unity",
            }
        )

    return metrics, diagnostics


def extract_differential_pair_ac_metrics(
    frequency_hz: Sequence[float],
    inp_v: Sequence[complex | float],
    inn_v: Sequence[complex | float],
    outp_v: Sequence[complex | float],
    outn_v: Sequence[complex | float],
    *,
    reference_points: int = 5,
    max_reference_variation_db: float = 0.5,
) -> tuple[dict[str, float], dict[str, object]]:
    """Extract differential gain and bandwidth from balanced complex AC data."""
    lengths = {
        len(frequency_hz),
        len(inp_v),
        len(inn_v),
        len(outp_v),
        len(outn_v),
    }
    if len(lengths) != 1:
        raise MetricExtractionError(
            "differential AC frequency and four node vectors must have equal length"
        )
    differential_input = [
        complex(inp) - complex(inn) for inp, inn in zip(inp_v, inn_v, strict=True)
    ]
    differential_output = [
        complex(outp) - complex(outn)
        for outp, outn in zip(outp_v, outn_v, strict=True)
    ]
    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        differential_input,
        differential_output,
        reference_points=reference_points,
        max_reference_variation_db=max_reference_variation_db,
    )
    return (
        {f"differential_{name}": value for name, value in metrics.items()},
        {
            **diagnostics,
            "signals": ["ac_freq", "ac_INP", "ac_INN", "ac_OUTP", "ac_OUTN"],
            "transfer": "(OUTP-OUTN)/(INP-INN) complex ratio",
            "stimulus": "balanced +0.5/-0.5 AC sources; differential input is 1 V",
        },
    )


def extract_differential_pair_common_mode_ac_metrics(
    frequency_hz: Sequence[float],
    inp_v: Sequence[complex | float],
    inn_v: Sequence[complex | float],
    outp_v: Sequence[complex | float],
    outn_v: Sequence[complex | float],
    *,
    reference_points: int = 5,
    max_reference_variation_db: float = 0.5,
) -> tuple[dict[str, float], dict[str, object]]:
    """Extract common-mode gain from a matched in-phase AC stimulus."""
    lengths = {
        len(frequency_hz),
        len(inp_v),
        len(inn_v),
        len(outp_v),
        len(outn_v),
    }
    if len(lengths) != 1:
        raise MetricExtractionError(
            "common-mode AC frequency and four node vectors must have equal length"
        )
    common_input = [
        0.5 * (complex(inp) + complex(inn))
        for inp, inn in zip(inp_v, inn_v, strict=True)
    ]
    common_output = [
        0.5 * (complex(outp) + complex(outn))
        for outp, outn in zip(outp_v, outn_v, strict=True)
    ]
    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        common_input,
        common_output,
        reference_points=reference_points,
        max_reference_variation_db=max_reference_variation_db,
    )
    return (
        {f"common_mode_{name}": value for name, value in metrics.items()},
        {
            **diagnostics,
            "signals": ["ac_freq", "ac_INP", "ac_INN", "ac_OUTP", "ac_OUTN"],
            "transfer": "((OUTP+OUTN)/2)/((INP+INN)/2) complex ratio",
            "stimulus": "matched 1 V in-phase AC sources",
        },
    )


def extract_low_frequency_cmrr_metrics(
    differential_metrics: dict[str, float],
    common_mode_metrics: dict[str, float],
) -> dict[str, float]:
    differential_gain = float(
        differential_metrics["differential_low_frequency_gain_v_per_v"]
    )
    common_mode_gain = float(
        common_mode_metrics["common_mode_low_frequency_gain_v_per_v"]
    )
    if (
        not math.isfinite(differential_gain)
        or not math.isfinite(common_mode_gain)
        or differential_gain <= 0.0
        or common_mode_gain <= 0.0
    ):
        raise MetricExtractionError(
            "CMRR requires finite positive differential and common-mode gains"
        )
    ratio = differential_gain / common_mode_gain
    return {
        "low_frequency_cmrr_v_per_v": ratio,
        "low_frequency_cmrr_db": 20.0 * math.log10(ratio),
    }


def extract_differential_pair_cmrr_response_metrics(
    differential_frequency_hz: Sequence[float],
    differential_inp_v: Sequence[complex | float],
    differential_inn_v: Sequence[complex | float],
    differential_outp_v: Sequence[complex | float],
    differential_outn_v: Sequence[complex | float],
    common_mode_frequency_hz: Sequence[float],
    common_mode_inp_v: Sequence[complex | float],
    common_mode_inn_v: Sequence[complex | float],
    common_mode_outp_v: Sequence[complex | float],
    common_mode_outn_v: Sequence[complex | float],
    *,
    reference_points: int = 5,
    max_reference_variation_db: float = 0.5,
) -> tuple[dict[str, float], dict[str, object]]:
    """Extract frequency-dependent CMRR from paired differential/common AC runs."""
    differential_lengths = {
        len(differential_frequency_hz),
        len(differential_inp_v),
        len(differential_inn_v),
        len(differential_outp_v),
        len(differential_outn_v),
    }
    common_mode_lengths = {
        len(common_mode_frequency_hz),
        len(common_mode_inp_v),
        len(common_mode_inn_v),
        len(common_mode_outp_v),
        len(common_mode_outn_v),
    }
    if len(differential_lengths) != 1 or len(common_mode_lengths) != 1:
        raise MetricExtractionError(
            "paired CMRR frequency and node vectors must have equal lengths per run"
        )
    if len(differential_frequency_hz) != len(common_mode_frequency_hz):
        raise MetricExtractionError(
            "paired CMRR differential and common-mode sweeps have different lengths"
        )
    differential_frequencies = [float(value) for value in differential_frequency_hz]
    common_mode_frequencies = [float(value) for value in common_mode_frequency_hz]
    for differential_frequency, common_mode_frequency in zip(
        differential_frequencies, common_mode_frequencies, strict=True
    ):
        tolerance = max(abs(differential_frequency) * 1e-12, 1e-9)
        if abs(differential_frequency - common_mode_frequency) > tolerance:
            raise MetricExtractionError(
                "paired CMRR differential and common-mode frequency grids differ"
            )

    differential_transfer: list[complex] = []
    common_mode_transfer: list[complex] = []
    for inp, inn, outp, outn in zip(
        differential_inp_v,
        differential_inn_v,
        differential_outp_v,
        differential_outn_v,
        strict=True,
    ):
        differential_input = complex(inp) - complex(inn)
        if abs(differential_input) <= 1e-30:
            raise MetricExtractionError(
                "paired CMRR differential run contains zero differential input"
            )
        differential_transfer.append(
            (complex(outp) - complex(outn)) / differential_input
        )
    for inp, inn, outp, outn in zip(
        common_mode_inp_v,
        common_mode_inn_v,
        common_mode_outp_v,
        common_mode_outn_v,
        strict=True,
    ):
        common_mode_input = 0.5 * (complex(inp) + complex(inn))
        if abs(common_mode_input) <= 1e-30:
            raise MetricExtractionError(
                "paired CMRR common-mode run contains zero common-mode input"
            )
        common_mode_transfer.append(
            0.5 * (complex(outp) + complex(outn)) / common_mode_input
        )
    if any(abs(value) <= 1e-30 for value in common_mode_transfer):
        raise MetricExtractionError(
            "paired CMRR requires non-zero common-mode transfer; use an explicit "
            "finite tail-output model"
        )
    cmrr_transfer = [
        differential / common_mode
        for differential, common_mode in zip(
            differential_transfer, common_mode_transfer, strict=True
        )
    ]
    raw_metrics, raw_diagnostics = extract_common_source_ac_metrics(
        differential_frequencies,
        [1.0 + 0.0j] * len(differential_frequencies),
        cmrr_transfer,
        reference_points=reference_points,
        max_reference_variation_db=max_reference_variation_db,
    )
    metric_mapping = {
        "low_frequency_gain_v_per_v": "low_frequency_cmrr_v_per_v",
        "low_frequency_gain_db": "low_frequency_cmrr_db",
        "low_frequency_phase_deg": "low_frequency_cmrr_phase_deg",
        "peak_gain_db": "peak_cmrr_db",
        "peak_gain_frequency_hz": "peak_cmrr_frequency_hz",
        "gain_peaking_db": "cmrr_peaking_db",
        "bandwidth_3db_hz": "cmrr_bandwidth_3db_hz",
        "phase_at_bandwidth_deg": "cmrr_phase_at_bandwidth_deg",
    }
    metrics = {
        renamed: raw_metrics[name]
        for name, renamed in metric_mapping.items()
        if name in raw_metrics
    }
    cmrr_db = [20.0 * math.log10(max(abs(value), 1e-300)) for value in cmrr_transfer]
    metrics.update(
        {
            "minimum_cmrr_db_over_sweep": min(cmrr_db),
            "cmrr_at_sweep_stop_db": cmrr_db[-1],
        }
    )
    diagnostics = dict(raw_diagnostics)
    diagnostics["cmrr_bandwidth"] = diagnostics.pop("bandwidth")
    diagnostics.pop("gbw", None)
    diagnostics.pop("unity_gain", None)
    diagnostics["issues"] = [
        str(value).replace("bandwidth_3db_hz", "cmrr_bandwidth_3db_hz")
        for value in diagnostics.get("issues", [])
    ]
    diagnostics["warnings"] = [
        value
        for value in diagnostics.get("warnings", [])
        if not str(value).startswith("unity_gain_frequency_hz unresolved")
    ]
    diagnostics.update(
        {
            "differential_signals": [
                "ac_freq",
                "ac_INP",
                "ac_INN",
                "ac_OUTP",
                "ac_OUTN",
            ],
            "common_mode_signals": [
                "ac_freq",
                "ac_INP",
                "ac_INN",
                "ac_OUTP",
                "ac_OUTN",
            ],
            "transfer": (
                "((OUTP-OUTN)/(INP-INN)) divided by "
                "(((OUTP+OUTN)/2)/((INP+INN)/2))"
            ),
            "frequency_grid_consistency": "matched",
            "definition": (
                "CMRR bandwidth is the first 3 dB decline from the low-frequency "
                "CMRR reference, not a standalone common-mode low-pass bandwidth"
            ),
        }
    )
    return metrics, diagnostics


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
