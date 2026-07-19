from __future__ import annotations

import pytest

from virtuoso_design_agent.metrics import (
    MetricExtractionError,
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
