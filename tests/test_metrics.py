from __future__ import annotations

import math

import pytest

from virtuoso_design_agent.metrics import (
    MetricExtractionError,
    aggregate_common_source_linearity_metrics,
    aggregate_differential_pair_linearity_metrics,
    extract_common_source_ac_metrics,
    extract_common_source_dc_metrics,
    extract_common_source_linearity_point_metrics,
    extract_common_source_noise_metrics,
    extract_dc_supply_metrics,
    extract_differential_pair_ac_metrics,
    extract_differential_pair_cmrr_response_metrics,
    extract_differential_pair_common_mode_ac_metrics,
    extract_differential_pair_dc_metrics,
    extract_differential_pair_psrr_metrics,
    extract_inverter_metrics,
    extract_low_frequency_cmrr_metrics,
    extract_supply_metrics,
)


def _linear(value0: float, value1: float, x: float) -> float:
    return value0 + (value1 - value0) * x


def test_extract_inverter_metrics_from_waveforms() -> None:
    time_ps = list(range(0, 201))
    vin = []
    vout = []
    for t in time_ps:
        if t < 20:
            vin.append(0.0)
        elif t <= 24:
            vin.append(_linear(0.0, 0.9, (t - 20) / 4))
        elif t < 100:
            vin.append(0.9)
        elif t <= 104:
            vin.append(_linear(0.9, 0.0, (t - 100) / 4))
        else:
            vin.append(0.0)

        if t < 30:
            vout.append(0.9)
        elif t <= 40:
            vout.append(_linear(0.9, 0.0, (t - 30) / 10))
        elif t < 112:
            vout.append(0.0)
        elif t <= 124:
            vout.append(_linear(0.0, 0.9, (t - 112) / 12))
        else:
            vout.append(0.9)

    metrics = extract_inverter_metrics(
        [value * 1e-12 for value in time_ps], vin, vout, vdd_v=0.9
    )
    assert metrics["tphl_ps"] == pytest.approx(13.0, abs=0.2)
    assert metrics["tplh_ps"] == pytest.approx(16.0, abs=0.2)
    assert metrics["delay_ps"] == pytest.approx(14.5, abs=0.2)
    assert metrics["fall_ps"] == pytest.approx(8.0, abs=0.2)
    assert metrics["rise_ps"] == pytest.approx(9.6, abs=0.2)
    assert metrics["voh_v"] == pytest.approx(0.9)
    assert metrics["vol_v"] == pytest.approx(0.0)
    assert metrics["overshoot_v"] == pytest.approx(0.0)
    assert metrics["undershoot_v"] == pytest.approx(0.0)


def test_missing_crossing_is_not_silently_accepted() -> None:
    with pytest.raises(MetricExtractionError, match="both rising and falling"):
        extract_inverter_metrics([0, 1, 2, 3], [0, 0, 0, 0], [1, 1, 1, 1], vdd_v=1)


def test_extract_supply_energy_over_one_input_cycle() -> None:
    metrics = extract_supply_metrics(
        [value * 1e-9 for value in range(5)],
        [0.0, 1.0, 1.0, 0.0, 1.0],
        [-2e-6] * 5,
        vdd_v=1.0,
    )
    assert metrics["supply_cycle_period_ps"] == pytest.approx(3000.0)
    assert metrics["supply_energy_per_cycle_fj"] == pytest.approx(6.0)
    assert metrics["average_supply_power_uw"] == pytest.approx(2.0)


def test_supply_metrics_require_a_complete_cycle() -> None:
    with pytest.raises(MetricExtractionError, match="two rising crossings"):
        extract_supply_metrics(
            [0.0, 1e-9, 2e-9, 3e-9],
            [0.0, 1.0, 1.0, 0.0],
            [-1e-6] * 4,
            vdd_v=1.0,
        )


def test_supply_metrics_reject_unexpected_current_polarity() -> None:
    with pytest.raises(MetricExtractionError, match="unexpected polarity"):
        extract_supply_metrics(
            [value * 1e-9 for value in range(5)],
            [0.0, 1.0, 1.0, 0.0, 1.0],
            [2e-6] * 5,
            vdd_v=1.0,
        )


def test_extract_dc_supply_power_from_the_actual_source_current() -> None:
    metrics = extract_dc_supply_metrics(
        vdd_v=0.9, supply_source_current_a=-20e-6
    )

    assert metrics["supply_current_ua"] == pytest.approx(20.0)
    assert metrics["dc_supply_power_uw"] == pytest.approx(18.0)

    with pytest.raises(MetricExtractionError, match="unexpected polarity"):
        extract_dc_supply_metrics(vdd_v=0.9, supply_source_current_a=20e-6)


def test_extract_common_source_coherent_linearity_and_supply_power() -> None:
    frequency_hz = 1e6
    points_per_cycle = 128
    total_cycles = 6
    time_s = [
        index / (frequency_hz * points_per_cycle)
        for index in range(total_cycles * points_per_cycle + 1)
    ]
    vin = [0.35 + 0.01 * math.sin(2.0 * math.pi * frequency_hz * time) for time in time_s]
    vout = [
        0.5
        - 0.1 * math.sin(2.0 * math.pi * frequency_hz * time)
        + 0.005 * math.sin(4.0 * math.pi * frequency_hz * time)
        for time in time_s
    ]
    supply_current = [-20e-6] * len(time_s)

    metrics, diagnostics = extract_common_source_linearity_point_metrics(
        time_s,
        vin,
        vout,
        supply_current,
        vdd_v=0.9,
        frequency_hz=frequency_hz,
        settling_cycles=2,
        measurement_cycles=4,
        max_harmonic=5,
    )

    assert metrics["input_fundamental_v_peak"] == pytest.approx(0.01, rel=1e-4)
    assert metrics["output_fundamental_v_peak"] == pytest.approx(0.1, rel=1e-4)
    assert metrics["large_signal_gain_v_per_v"] == pytest.approx(10.0, rel=1e-4)
    assert metrics["hd2_dbc"] == pytest.approx(20.0 * math.log10(0.05), abs=0.02)
    assert metrics["thd_percent"] == pytest.approx(5.0, abs=0.02)
    assert metrics["average_supply_power_uw"] == pytest.approx(18.0)
    assert diagnostics["analysis_complete"] is True


def test_linearity_aggregation_resolves_p1db_only_when_bracketed() -> None:
    amplitudes = [0.005, 0.02, 0.05]
    points = [
        {
            "large_signal_gain_v_per_v": gain,
            "output_fundamental_v_peak": amplitude * gain,
            "thd_percent": thd,
            "average_supply_power_uw": power,
            "output_peak_to_peak_v": 2.0 * amplitude * gain,
            "hd2_dbc": -50.0 + index * 5.0,
            "hd3_dbc": -60.0 + index * 5.0,
        }
        for index, (amplitude, gain, thd, power) in enumerate(
            zip(amplitudes, [10.0, 9.5, 8.5], [0.1, 0.5, 2.0], [18.0, 18.2, 19.0])
        )
    ]

    metrics, diagnostics = aggregate_common_source_linearity_metrics(
        amplitudes, points
    )

    assert 0.02 < metrics["input_1db_compression_v_peak"] < 0.05
    assert metrics["max_thd_percent"] == pytest.approx(2.0)
    assert metrics["max_average_supply_power_uw"] == pytest.approx(19.0)
    assert diagnostics["p1db"]["status"] == "resolved"

    uncompressed = [dict(point, large_signal_gain_v_per_v=10.0) for point in points]
    metrics, diagnostics = aggregate_common_source_linearity_metrics(
        amplitudes, uncompressed
    )
    assert "input_1db_compression_v_peak" not in metrics
    assert diagnostics["p1db"]["status"] == "unresolved"


def test_integrate_common_source_input_and_output_noise_density() -> None:
    frequencies = [1e3, 101e3, 1.001e6]
    metrics, diagnostics = extract_common_source_noise_metrics(
        frequencies,
        [10e-9] * len(frequencies),
        [2e-9] * len(frequencies),
    )

    assert metrics["integrated_output_noise_uv_rms"] == pytest.approx(10.0)
    assert metrics["integrated_input_referred_noise_uv_rms"] == pytest.approx(2.0)
    assert diagnostics["integration_band_hz"] == [1e3, 1.001e6]

    with pytest.raises(MetricExtractionError, match="strictly increasing"):
        extract_common_source_noise_metrics(
            [1e3, 1e3], [1e-9, 1e-9], [1e-9, 1e-9]
        )


def test_extract_common_source_dc_metrics_from_operating_point() -> None:
    metrics = extract_common_source_dc_metrics(
        vdd_v=0.9,
        vin_v=0.45,
        vout_v=0.5,
        vss_v=0.0,
        drain_current_a=20e-6,
        vdsat_v=0.12,
        gm_s=200e-6,
        gds_s=10e-6,
        load_resistance_ohm=20_000.0,
    )

    assert metrics["drain_current_ua"] == pytest.approx(20.0)
    assert metrics["vgs_v"] == pytest.approx(0.45)
    assert metrics["vds_v"] == pytest.approx(0.5)
    assert metrics["saturation_margin_v"] == pytest.approx(0.38)
    assert metrics["output_swing_margin_v"] == pytest.approx(0.38)
    assert metrics["intrinsic_gain_v_per_v"] == pytest.approx(20.0)
    assert metrics["resistor_current_ua"] == pytest.approx(20.0)
    assert metrics["current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["saturation_region"] == pytest.approx(1.0)


def test_common_source_dc_metrics_reject_nonfinite_or_zero_gds() -> None:
    with pytest.raises(MetricExtractionError, match="finite"):
        extract_common_source_dc_metrics(
            vdd_v=0.9,
            vin_v=float("nan"),
            vout_v=0.5,
            vss_v=0.0,
            drain_current_a=20e-6,
            vdsat_v=0.12,
            gm_s=200e-6,
            gds_s=10e-6,
            load_resistance_ohm=20_000.0,
        )

    with pytest.raises(MetricExtractionError, match="gds"):
        extract_common_source_dc_metrics(
            vdd_v=0.9,
            vin_v=0.45,
            vout_v=0.5,
            vss_v=0.0,
            drain_current_a=20e-6,
            vdsat_v=0.12,
            gm_s=200e-6,
            gds_s=0.0,
            load_resistance_ohm=20_000.0,
        )


def test_extract_balanced_differential_pair_dc_metrics() -> None:
    metrics = extract_differential_pair_dc_metrics(
        vdd_v=0.9,
        common_mode_v=0.45,
        outp_v=0.4,
        outn_v=0.4,
        tail_v=0.08,
        branch_p_current_a=25e-6,
        branch_n_current_a=25e-6,
        tail_source_current_a=50e-6,
        supply_source_current_a=-50e-6,
        branch_p_vdsat_v=0.12,
        branch_n_vdsat_v=0.12,
        branch_p_gm_s=400e-6,
        branch_n_gm_s=400e-6,
        branch_p_gds_s=20e-6,
        branch_n_gds_s=20e-6,
        load_resistance_ohm=20_000.0,
    )

    assert metrics["branch_p_current_ua"] == pytest.approx(25.0)
    assert metrics["branch_n_current_ua"] == pytest.approx(25.0)
    assert metrics["tail_current_ua"] == pytest.approx(50.0)
    assert metrics["branch_current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["tail_current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["supply_current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["max_load_current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["output_offset_abs_mv"] == pytest.approx(0.0)
    assert metrics["minimum_saturation_margin_v"] == pytest.approx(0.2)
    assert metrics["both_saturation_region"] == pytest.approx(1.0)
    assert metrics["minimum_intrinsic_gain_v_per_v"] == pytest.approx(20.0)
    assert metrics["dc_supply_power_uw"] == pytest.approx(45.0)


def test_differential_pair_dc_metrics_expose_imbalance_without_hiding_kcl() -> None:
    metrics = extract_differential_pair_dc_metrics(
        vdd_v=0.9,
        common_mode_v=0.45,
        outp_v=0.42,
        outn_v=0.38,
        tail_v=0.08,
        branch_p_current_a=24e-6,
        branch_n_current_a=26e-6,
        tail_source_current_a=50e-6,
        supply_source_current_a=-50e-6,
        branch_p_vdsat_v=0.12,
        branch_n_vdsat_v=0.12,
        branch_p_gm_s=390e-6,
        branch_n_gm_s=410e-6,
        branch_p_gds_s=20e-6,
        branch_n_gds_s=21e-6,
        load_resistance_ohm=20_000.0,
    )

    assert metrics["branch_current_mismatch_percent"] == pytest.approx(
        2.0 / 26.0 * 100.0
    )
    assert metrics["output_offset_abs_mv"] == pytest.approx(40.0)
    assert metrics["tail_current_mismatch_percent"] == pytest.approx(0.0)
    assert metrics["max_load_current_mismatch_percent"] > 0.0


def _first_order_ac_response(
    *, start_exponent: int, stop_exponent: int, points_per_decade: int = 10
) -> tuple[list[float], list[complex], list[complex]]:
    frequency_hz = [
        10.0 ** (start_exponent + index / points_per_decade)
        for index in range((stop_exponent - start_exponent) * points_per_decade + 1)
    ]
    vin = [1.0 + 0.0j for _ in frequency_hz]
    vout = [
        -10.0 / (1.0 + 1j * frequency / 1e6) for frequency in frequency_hz
    ]
    return frequency_hz, vin, vout


def test_extract_common_source_ac_gain_bandwidth_gbw_and_unity_gain() -> None:
    frequency_hz, vin, vout = _first_order_ac_response(
        start_exponent=2, stop_exponent=8
    )

    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        vin,
        vout,
        reference_points=5,
        max_reference_variation_db=0.5,
    )

    assert metrics["low_frequency_gain_v_per_v"] == pytest.approx(10.0, rel=1e-5)
    assert metrics["low_frequency_gain_db"] == pytest.approx(20.0, abs=1e-4)
    assert metrics["bandwidth_3db_hz"] == pytest.approx(1e6, rel=0.01)
    assert metrics["gain_bandwidth_product_hz"] == pytest.approx(1e7, rel=0.01)
    assert metrics["unity_gain_frequency_hz"] == pytest.approx(
        (10.0**2 - 1.0) ** 0.5 * 1e6,
        rel=0.02,
    )
    assert metrics["phase_at_bandwidth_deg"] == pytest.approx(135.0, abs=0.5)
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["bandwidth"]["status"] == "resolved"
    assert diagnostics["gbw"]["status"] == "resolved"
    assert diagnostics["unity_gain"]["status"] == "resolved"


def test_extract_differential_pair_ac_uses_differential_nodes() -> None:
    frequency_hz, _, transfer = _first_order_ac_response(
        start_exponent=2, stop_exponent=8
    )

    metrics, diagnostics = extract_differential_pair_ac_metrics(
        frequency_hz,
        [0.5 + 0.0j] * len(frequency_hz),
        [-0.5 + 0.0j] * len(frequency_hz),
        [0.5 * value for value in transfer],
        [-0.5 * value for value in transfer],
    )

    assert metrics["differential_low_frequency_gain_v_per_v"] == pytest.approx(
        10.0, rel=1e-5
    )
    assert metrics["differential_bandwidth_3db_hz"] == pytest.approx(
        1e6, rel=0.01
    )
    assert metrics["differential_gain_bandwidth_product_hz"] == pytest.approx(
        1e7, rel=0.01
    )
    assert metrics["differential_unity_gain_frequency_hz"] == pytest.approx(
        (10.0**2 - 1.0) ** 0.5 * 1e6,
        rel=0.02,
    )
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["transfer"] == "(OUTP-OUTN)/(INP-INN) complex ratio"


def test_extract_differential_pair_common_mode_ac_and_cmrr() -> None:
    frequency_hz = [10.0 ** (2.0 + index / 10.0) for index in range(71)]
    differential_transfer = [
        -10.0 / (1.0 + 1j * frequency / 1e6)
        for frequency in frequency_hz
    ]
    common_mode_transfer = [
        -0.01 / (1.0 + 1j * frequency / 2e5)
        for frequency in frequency_hz
    ]
    differential_metrics, _ = extract_differential_pair_ac_metrics(
        frequency_hz,
        [0.5 + 0.0j] * len(frequency_hz),
        [-0.5 + 0.0j] * len(frequency_hz),
        [0.5 * value for value in differential_transfer],
        [-0.5 * value for value in differential_transfer],
    )
    common_mode_metrics, diagnostics = (
        extract_differential_pair_common_mode_ac_metrics(
            frequency_hz,
            [1.0 + 0.0j] * len(frequency_hz),
            [1.0 + 0.0j] * len(frequency_hz),
            common_mode_transfer,
            common_mode_transfer,
        )
    )
    cmrr = extract_low_frequency_cmrr_metrics(
        differential_metrics, common_mode_metrics
    )

    assert common_mode_metrics[
        "common_mode_low_frequency_gain_v_per_v"
    ] == pytest.approx(0.01, rel=1e-5)
    assert common_mode_metrics["common_mode_bandwidth_3db_hz"] == pytest.approx(
        2e5, rel=0.01
    )
    assert cmrr["low_frequency_cmrr_v_per_v"] == pytest.approx(1000.0)
    assert cmrr["low_frequency_cmrr_db"] == pytest.approx(60.0)
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["transfer"] == (
        "((OUTP+OUTN)/2)/((INP+INN)/2) complex ratio"
    )


def test_extract_frequency_dependent_cmrr_bandwidth_from_paired_runs() -> None:
    frequency_hz = [10.0 ** (2.0 + index / 10.0) for index in range(71)]
    differential_transfer = [
        -10.0 / (1.0 + 1j * frequency / 1e6)
        for frequency in frequency_hz
    ]
    common_mode_transfer = [
        -0.01 / (1.0 + 1j * frequency / 1e7)
        for frequency in frequency_hz
    ]

    metrics, diagnostics = extract_differential_pair_cmrr_response_metrics(
        frequency_hz,
        [0.5 + 0.0j] * len(frequency_hz),
        [-0.5 + 0.0j] * len(frequency_hz),
        [0.5 * value for value in differential_transfer],
        [-0.5 * value for value in differential_transfer],
        frequency_hz,
        [1.0 + 0.0j] * len(frequency_hz),
        [1.0 + 0.0j] * len(frequency_hz),
        common_mode_transfer,
        common_mode_transfer,
    )

    assert metrics["low_frequency_cmrr_db"] == pytest.approx(60.0, abs=1e-4)
    assert metrics["cmrr_bandwidth_3db_hz"] == pytest.approx(1.01e6, rel=0.03)
    assert metrics["cmrr_at_sweep_stop_db"] < metrics["low_frequency_cmrr_db"]
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["frequency_grid_consistency"] == "matched"


def test_active_load_differential_pair_ac_uses_outn_single_ended_output() -> None:
    frequency_hz = [1e3, 1e4, 1e5, 1e6, 1e7, 1e8]
    transfer = [-10.0 / (1.0 + 1j * frequency / 1e6) for frequency in frequency_hz]
    metrics, diagnostics = extract_differential_pair_ac_metrics(
        frequency_hz,
        [0.5 + 0.0j] * len(frequency_hz),
        [-0.5 + 0.0j] * len(frequency_hz),
        [100.0 + 0.0j] * len(frequency_hz),
        transfer,
        reference_points=2,
        output_mode="single_ended_outn",
    )

    assert metrics["differential_low_frequency_gain_v_per_v"] == pytest.approx(
        10.0, rel=0.01
    )
    assert diagnostics["transfer"] == "OUTN/(INP-INN) complex ratio"
    assert diagnostics["output_mode"] == "single_ended_outn"


def test_extract_active_load_psrr_from_three_matched_ac_runs() -> None:
    frequency_hz = [10.0 ** (2.0 + index / 10.0) for index in range(71)]
    differential_transfer = [
        -10.0 / (1.0 + 1j * frequency / 1e6)
        for frequency in frequency_hz
    ]
    positive_supply_transfer = [
        -0.1 / (1.0 + 1j * frequency / 1e7)
        for frequency in frequency_hz
    ]
    negative_supply_transfer = [
        0.01 / (1.0 + 1j * frequency / 1e7)
        for frequency in frequency_hz
    ]

    metrics, diagnostics = extract_differential_pair_psrr_metrics(
        frequency_hz,
        [0.5 + 0.0j] * len(frequency_hz),
        [-0.5 + 0.0j] * len(frequency_hz),
        [100.0 + 0.0j] * len(frequency_hz),
        differential_transfer,
        frequency_hz,
        [1.0 + 0.0j] * len(frequency_hz),
        [100.0 + 0.0j] * len(frequency_hz),
        positive_supply_transfer,
        frequency_hz,
        [1.0 + 0.0j] * len(frequency_hz),
        [100.0 + 0.0j] * len(frequency_hz),
        negative_supply_transfer,
        output_mode="single_ended_outn",
    )

    assert metrics["positive_low_frequency_psrr_db"] == pytest.approx(40.0, abs=0.01)
    assert metrics["negative_low_frequency_psrr_db"] == pytest.approx(60.0, abs=0.01)
    assert metrics["positive_supply_low_frequency_gain_v_per_v"] == pytest.approx(
        0.1, rel=1e-4
    )
    assert metrics["minimum_low_frequency_psrr_db"] == pytest.approx(40.0, abs=0.01)
    assert metrics["positive_psrr_bandwidth_3db_hz"] == pytest.approx(
        1.01e6, rel=0.03
    )
    assert metrics["minimum_psrr_db_over_sweep"] < 30.0
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["frequency_grid_consistency"] == "matched"
    assert diagnostics["output_mode"] == "single_ended_outn"


def test_psrr_rejects_mismatched_grids_and_zero_supply_stimulus() -> None:
    frequency_hz = [1e3, 1e4, 1e5, 1e6, 1e7, 1e8]
    point_count = len(frequency_hz)
    arguments = [
        frequency_hz,
        [0.5 + 0.0j] * point_count,
        [-0.5 + 0.0j] * point_count,
        [0.0j] * point_count,
        [-10.0 + 0.0j] * point_count,
        frequency_hz,
        [1.0 + 0.0j] * point_count,
        [0.0j] * point_count,
        [-0.1 + 0.0j] * point_count,
        frequency_hz,
        [1.0 + 0.0j] * point_count,
        [0.0j] * point_count,
        [0.01 + 0.0j] * point_count,
    ]

    mismatched_grid = list(arguments)
    mismatched_grid[5] = [1e3, 1e4, 2e5, 1e6, 1e7, 1e8]
    with pytest.raises(MetricExtractionError, match="frequency grids differ"):
        extract_differential_pair_psrr_metrics(
            *mismatched_grid, output_mode="single_ended_outn"
        )

    nonfinite_grid = list(arguments)
    nonfinite_grid[9] = [1e3, 1e4, math.nan, 1e6, 1e7, 1e8]
    with pytest.raises(MetricExtractionError, match="finite and positive"):
        extract_differential_pair_psrr_metrics(
            *nonfinite_grid, output_mode="single_ended_outn"
        )

    zero_positive_supply = list(arguments)
    zero_positive_supply[6] = [0.0j] * point_count
    with pytest.raises(MetricExtractionError, match="zero supply input"):
        extract_differential_pair_psrr_metrics(
            *zero_positive_supply, output_mode="single_ended_outn"
        )


def test_namespace_differential_pair_linearity_metrics() -> None:
    metrics, diagnostics = aggregate_differential_pair_linearity_metrics(
        [0.01, 0.1],
        [
            {
                "large_signal_gain_v_per_v": 3.0,
                "output_fundamental_v_peak": 0.03,
                "thd_percent": 0.1,
                "average_supply_power_uw": 45.0,
                "output_peak_to_peak_v": 0.06,
                "hd2_dbc": -80.0,
                "hd3_dbc": -60.0,
            },
            {
                "large_signal_gain_v_per_v": 2.5,
                "output_fundamental_v_peak": 0.25,
                "thd_percent": 2.0,
                "average_supply_power_uw": 46.0,
                "output_peak_to_peak_v": 0.5,
                "hd2_dbc": -60.0,
                "hd3_dbc": -35.0,
            },
        ],
    )

    assert metrics["differential_small_signal_gain_v_per_v"] == pytest.approx(3.0)
    assert metrics["differential_input_1db_compression_v_peak"] > 0.01
    assert metrics["differential_thd_at_max_amplitude_percent"] == pytest.approx(
        2.0
    )
    assert metrics["transient_max_average_supply_power_uw"] == pytest.approx(46.0)
    assert diagnostics["input_definition"] == (
        "VINP-VINN differential peak amplitude"
    )


def test_common_source_ac_does_not_invent_bandwidth_beyond_sweep() -> None:
    frequency_hz, vin, vout = _first_order_ac_response(
        start_exponent=2, stop_exponent=5
    )

    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz, vin, vout
    )

    assert "bandwidth_3db_hz" not in metrics
    assert "gain_bandwidth_product_hz" not in metrics
    assert diagnostics["analysis_complete"] is False
    assert diagnostics["bandwidth"]["reason"] == (
        "sweep_stop_below_first_minus_3db_crossing"
    )
    assert diagnostics["issues"] == [
        "bandwidth_3db_hz unresolved: "
        "sweep_stop_below_first_minus_3db_crossing"
    ]


def test_common_source_ac_rejects_a_nonflat_reference_window() -> None:
    frequency_hz, vin, vout = _first_order_ac_response(
        start_exponent=6, stop_exponent=8
    )

    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz, vin, vout
    )

    assert "bandwidth_3db_hz" not in metrics
    assert diagnostics["reference"]["status"] == "not_flat"
    assert diagnostics["bandwidth"]["reason"] == "low_frequency_reference_not_flat"


def test_common_source_ac_nonmonotonic_response_uses_first_crossing_and_warns() -> None:
    frequency_hz = [10.0**index for index in range(8)]
    gain_db = [10.0, 10.0, 8.0, 6.0, 8.0, 6.0, 4.0, 2.0]
    vout = [10.0 ** (value / 20.0) + 0.0j for value in gain_db]

    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        [1.0 + 0.0j] * len(frequency_hz),
        vout,
        reference_points=2,
    )

    assert 1e2 < metrics["bandwidth_3db_hz"] < 1e3
    assert diagnostics["bandwidth"]["crossing_count"] == 2
    assert diagnostics["analysis_complete"] is True
    assert "multiple downward half-power crossings; first crossing used" in diagnostics[
        "warnings"
    ]
    assert (
        "response re-crosses the half-power threshold after bandwidth"
        in diagnostics["warnings"]
    )


@pytest.mark.parametrize(
    ("frequency_hz", "vin", "vout", "message"),
    [
        ([1.0, 10.0, "bad", 1000.0, 10_000.0, 100_000.0], [1.0] * 6, [1.0] * 6, "must be numeric"),
        ([1.0, 10.0, 10.0, 100.0, 1000.0, 10_000.0], [1.0] * 6, [1.0] * 6, "strictly increasing"),
        ([1.0, 10.0, 100.0, 1000.0, 10_000.0, 100_000.0], [1.0, 1.0, 0.0, 1.0, 1.0, 1.0], [1.0] * 6, "zero small-signal"),
        ([1.0, 10.0, 100.0, 1000.0, 10_000.0, 100_000.0], [1.0] * 6, [1.0, 1.0, complex(float("nan"), 0.0), 1.0, 1.0, 1.0], "must be finite"),
    ],
)
def test_common_source_ac_rejects_invalid_complex_waveforms(
    frequency_hz, vin, vout, message
) -> None:
    with pytest.raises(MetricExtractionError, match=message):
        extract_common_source_ac_metrics(frequency_hz, vin, vout)
