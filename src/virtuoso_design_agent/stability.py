"""Explicit single-loop STB contracts and conservative margin extraction.

The sign convention is an input, never inferred from a waveform.  Margins
describe the declared loop under its assumptions, not a Nyquist proof.
"""

from __future__ import annotations

import cmath
import math
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class NativeMarginTolerances(BaseModel):
    """Extraction-consistency bounds, not circuit performance specifications."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    phase_margin_abs_deg: float = Field(default=0.1, gt=0, le=5)
    frequency_relative: float = Field(default=0.005, gt=0, le=0.1)
    gain_margin_abs_db: float = Field(default=0.1, gt=0, le=3)


class StabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    # A declared, zero-volt testbench voltage source is a supported Spectre
    # loop probe.  This does not insert or reconnect anything inside OA.
    probe_source: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    feedback_configuration: StrictStr = Field(min_length=1, max_length=1000)
    loop_scope: Literal["single_loop"]
    raw_loop_gain_convention: Literal["return_ratio", "feedback_transfer"]
    open_loop_rhp_poles: Literal[0]
    assumptions_source: Literal["user_input", "software_inference"]
    reference_phase_tolerance_deg: float = Field(default=30.0, gt=0, le=60)
    native_margin_tolerances: NativeMarginTolerances = Field(default_factory=NativeMarginTolerances)


class StabilitySweep(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    points_per_decade: int = Field(default=40, ge=4, le=1000)

    @model_validator(mode="after")
    def ordered(self) -> "StabilitySweep":
        if self.stop_hz <= self.start_hz:
            raise ValueError("stability sweep stop_hz must exceed start_hz")
        return self


STABILITY_METRICS = frozenset({
    "loop_low_frequency_gain_db", "loop_unity_gain_frequency_hz",
    "phase_margin_deg", "gain_margin_db", "phase_crossover_frequency_hz",
})


def extract_stability_metrics(
    frequency_hz: Sequence[float], raw_loop_gain: Sequence[complex],
    spec: StabilitySpec,
) -> tuple[dict[str, float], dict]:
    """Extract one unambiguous gain crossing with phase unwrapped from DC.

    Normalize to T in 1+T.  ``feedback_transfer`` explicitly requests -raw;
    ``return_ratio`` requests raw.  Multiple gain crossings are incomplete,
    including upward crossings; selecting the first can hide an unsafe loop.
    """
    if len(frequency_hz) != len(raw_loop_gain) or len(frequency_hz) < 3:
        raise ValueError("stability requires matching, nonempty frequency/loop-gain arrays")
    frequencies = [float(x) for x in frequency_hz]
    if any(not math.isfinite(x) or x <= 0 for x in frequencies) or any(
        b <= a for a, b in zip(frequencies, frequencies[1:])
    ):
        raise ValueError("stability frequencies must be finite, positive and strictly increasing")
    sign = 1 if spec.raw_loop_gain_convention == "return_ratio" else -1
    values = [sign * complex(x) for x in raw_loop_gain]
    if any(not math.isfinite(x.real) or not math.isfinite(x.imag) or abs(x) == 0 for x in values):
        raise ValueError("stability loop gain must be finite and nonzero")
    gain_db = [20 * math.log10(abs(x)) for x in values]
    phases: list[float] = []
    for value in values:
        phase = math.degrees(cmath.phase(value))
        if phases:
            while phase - phases[-1] > 180:
                phase -= 360
            while phase - phases[-1] < -180:
                phase += 360
        phases.append(phase)

    def crossing_samples(samples: list[float], level: float) -> list[dict]:
        found: list[dict] = []
        for i in range(1, len(samples)):
            left, right = samples[i - 1] - level, samples[i] - level
            if left * right >= 0:
                continue
            fraction = left / (left - right)
            frequency = math.exp(math.log(frequencies[i - 1]) + fraction *
                                 math.log(frequencies[i] / frequencies[i - 1]))
            found.append({"frequency_hz": frequency, "index": i,
                          "fraction": fraction, "direction": "down" if right < left else "up"})
        for i in range(1, len(samples) - 1):
            if samples[i] != level:
                continue
            left, right = samples[i - 1] - level, samples[i + 1] - level
            # A touch/plateau is not a unique through-crossing. Preserve it as
            # an ambiguity rather than choosing a plausible-looking margin.
            direction = ("down" if right < left else "up") if left * right < 0 else "touch_or_plateau"
            found.append({"frequency_hz": frequencies[i], "index": i,
                          "fraction": 1.0, "direction": direction})
        return sorted(found, key=lambda crossing: crossing["frequency_hz"])

    crossings = crossing_samples(gain_db, 0.0)
    diagnostics = {
        "method": "single-loop STB; normalize to T in 1+T; log-frequency interpolation",
        "contract": spec.model_dump(mode="json"),
        "assumptions_source": spec.assumptions_source, "assumptions_independently_verified": False,
        "global_stability_proven": False, "gain_crossings": crossings,
        "frequency_hz": frequencies, "gain_db": gain_db, "phase_deg": phases,
        "issues": [], "warnings": [], "analysis_complete": False,
    }
    issues = diagnostics["issues"]
    metrics = {"loop_low_frequency_gain_db": gain_db[0]}
    if abs(phases[0]) > spec.reference_phase_tolerance_deg:
        issues.append("low-frequency phase does not match the declared return-ratio convention")
    if gain_db[0] <= 0:
        issues.append("sweep does not start above unity loop gain")
    if gain_db[-1] >= 0:
        issues.append("sweep does not end below unity loop gain")
    if len(crossings) != 1 or (crossings and crossings[0]["direction"] != "down"):
        issues.append("a single downward unity-gain crossing is required")
    if not issues:
        crossing = crossings[0]
        i, fraction = crossing["index"], crossing["fraction"]
        phase = phases[i - 1] + fraction * (phases[i] - phases[i - 1])
        if not -360 < phase <= 0:
            issues.append("unity crossing is outside the supported lagging phase branch")
        else:
            metrics["loop_unity_gain_frequency_hz"] = crossing["frequency_hz"]
            metrics["phase_margin_deg"] = 180 + phase
    phase_crossings = crossing_samples(phases, -180)
    diagnostics["phase_crossings"] = phase_crossings
    if not issues and len(phase_crossings) == 1 and phase_crossings[0]["direction"] == "down":
        crossing = phase_crossings[0]
        i, fraction = crossing["index"], crossing["fraction"]
        metrics["gain_margin_db"] = -(gain_db[i - 1] + fraction * (gain_db[i] - gain_db[i - 1]))
        metrics["phase_crossover_frequency_hz"] = crossing["frequency_hz"]
    else:
        diagnostics["warnings"].append("gain margin unresolved: no single downward -180-degree crossing")
    diagnostics["analysis_complete"] = not issues
    return metrics, diagnostics
