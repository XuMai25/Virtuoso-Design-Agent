from __future__ import annotations

import pytest

from virtuoso_design_agent.metrics import (
    MetricExtractionError,
    extract_common_source_dc_metrics,
    extract_inverter_metrics,
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
