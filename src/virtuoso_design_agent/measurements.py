"""Reviewable measurement definitions compiled onto the existing task contract."""

from __future__ import annotations

import hashlib
import json
import math
from itertools import product
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


_KNOWN_MEASUREMENTS = {
    "low_frequency_gain_db": ("gain", "dB"),
    "low_frequency_gain_v_per_v": ("gain", "V/V"),
    "bandwidth_3db_hz": ("bandwidth", "Hz"),
    "unity_gain_frequency_hz": ("unity_gain_frequency", "Hz"),
    "gain_bandwidth_product_hz": ("gain_bandwidth_product", "Hz"),
    "loop_unity_gain_frequency_hz": ("unity_gain_frequency", "Hz"),
    "phase_margin_deg": ("phase_margin", "deg"),
    "gain_margin_db": ("gain_margin", "dB"),
}


class MeasurementDefinition(_Contract):
    metric: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    analysis: Literal["dc", "ac", "transient", "noise", "stability"]
    quantity: Literal["gain", "bandwidth", "unity_gain_frequency", "gain_bandwidth_product",
                      "phase_margin", "gain_margin", "voltage", "current", "power", "time",
                      "noise", "operating_point", "other"]
    unit: StrictStr = Field(min_length=1, max_length=48)
    definition: StrictStr = Field(min_length=1, max_length=2000)
    signals: list[StrictStr] = Field(min_length=1, max_length=64)
    required: bool = True
    source: Literal["user_input", "software_inference"]

    @model_validator(mode="after")
    def check_semantics(self) -> "MeasurementDefinition":
        known = _KNOWN_MEASUREMENTS.get(self.metric)
        if known is not None and (self.quantity, self.unit) != known:
            raise ValueError(f"{self.metric} requires quantity/unit {known}; UGB and GBW are distinct")
        if self.quantity in {"phase_margin", "gain_margin"} and self.analysis != "stability":
            raise ValueError("margin measurements require the stability analysis, not transfer phase")
        if len(self.signals) != len(set(self.signals)) or any(not x.strip() for x in self.signals):
            raise ValueError("measurement signals must be nonempty and unique")
        return self


class MeasurementPlan(_Contract):
    schema_version: Literal[1] = 1
    context_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    measurements: list[MeasurementDefinition] = Field(min_length=1, max_length=128)


class SamplingAxis(_Contract):
    minimum: float
    nominal: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> "SamplingAxis":
        if not self.minimum <= self.nominal <= self.maximum:
            raise ValueError("sampling axis requires minimum <= nominal <= maximum")
        return self

    def values(self) -> list[float]:
        return list(dict.fromkeys((self.minimum, self.nominal, self.maximum)))


class OperatingRange(_Contract):
    process_corner: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    temperature_c: SamplingAxis | None = None
    supply_v: SamplingAxis | None = None
    # Required for the unchanged axis in a single-axis check. Never assume
    # 27 C / 1.8 V across PDKs.
    nominal_temperature_c: float | None = Field(default=None, ge=-273.15, le=300)
    nominal_supply_v: float | None = Field(default=None, gt=0, le=10)

    def compile(self) -> list[dict]:
        if (self.temperature_c and self.nominal_temperature_c is not None
                and self.nominal_temperature_c != self.temperature_c.nominal):
            raise ValueError("conflicting nominal temperature declarations")
        if (self.supply_v and self.nominal_supply_v is not None
                and self.nominal_supply_v != self.supply_v.nominal):
            raise ValueError("conflicting nominal supply declarations")
        temperatures = self.temperature_c.values() if self.temperature_c else [self.nominal_temperature_c]
        supplies = self.supply_v.values() if self.supply_v else [self.nominal_supply_v]
        if self.temperature_c is None and self.supply_v is None:
            raise ValueError("operating range requires at least one explicit range")
        if None in temperatures or None in supplies:
            raise ValueError("single-axis verification requires an explicit nominal value for the other axis")
        from .models import OperatingCondition
        return [OperatingCondition(name=f"tv-{i:02d}", process_corner=self.process_corner,
                                   temperature_c=t, vdd_v=v).model_dump(mode="json")
                for i, (t, v) in enumerate(product(temperatures, supplies), 1)]


class MeasurementRequest(_Contract):
    schema_version: Literal[1] = 1
    measurements: list[MeasurementDefinition] = Field(min_length=1, max_length=128)
    operating_range: OperatingRange | None = None
    uncertainties: list[StrictStr] = Field(default_factory=list, max_length=64)


def measurement_signals(simulation: Any, metric: str, analysis: str) -> set[str]:
    """Exact machine-readable bindings, not free-text descriptions of a node."""
    def voltage(expression: Any) -> str:
        return f"V({expression.positive_node},{expression.negative_node})"

    for item in simulation.dc_voltage_metrics:
        if item.metric == metric:
            return {voltage(item.expression)}
    for item in simulation.dc_current_metrics:
        if item.metric == metric:
            return {f"I({item.source})"}
    for item in simulation.operating_point_metrics:
        if item.metric == metric:
            return {f"OP({item.instance},{item.quantity})"}
    if analysis == "stability" and simulation.stability is not None:
        return {f"loopGain({simulation.stability.probe_source})"}
    if simulation.transfer is None:
        return set()
    signals = {voltage(simulation.transfer.input), voltage(simulation.transfer.output)}
    if analysis == "transient" and simulation.dynamic_analysis is not None:
        signals.add(f"I({simulation.dynamic_analysis.power_source})")
    return signals


def measurement_context(task: Any) -> dict:
    from .profiles import load_pdk_profile
    profile = load_pdk_profile(task.pdk_profile)
    return {
        name: value.model_dump(mode="json", exclude_none=True) if hasattr(value, "model_dump") else value
        for name, value in {
            "pdk_profile": task.pdk_profile,
            "pdk_configuration_sha256": canonical_sha256(profile.model_dump(mode="json")),
            "target": task.target,
            "generic_simulation": task.generic_simulation,
            "topology_refinement": task.topology_refinement,
            "ac_sweep": task.ac_sweep, "linearity_sweep": task.linearity_sweep,
            "noise_sweep": task.noise_sweep, "stability_sweep": task.stability_sweep,
            "operating_conditions": [c.model_dump(mode="json", exclude_none=True)
                                     for c in task.operating_conditions],
        }.items()
    }


def validate_measurement_plan(task: Any) -> None:
    plan = task.measurement_plan
    if plan is None:
        return
    if task.circuit.value != "existing_schematic" or task.operation.value not in {
        "simulation.run", "design.tune", "design.close_loop",
    } or task.generic_simulation is None:
        raise ValueError("measurement_plan requires a generic existing-schematic simulation/tuning task")
    if plan.context_sha256 != canonical_sha256(measurement_context(task)):
        raise ValueError("measurement context changed; recompile and review the measurement plan")
    definitions = {item.metric: item for item in plan.measurements}
    if len(definitions) != len(plan.measurements):
        raise ValueError("measurement definitions repeat a metric")
    requested = {a.value for a in task.resolved_analyses()}
    for item in plan.measurements:
        if item.analysis not in requested or item.metric not in task.generic_simulation.metric_names_for_analysis(item.analysis):
            raise ValueError(f"measurement {item.metric!r} cannot be produced by its declared analysis")
        expected = measurement_signals(task.generic_simulation, item.metric, item.analysis)
        if set(item.signals) != expected:
            raise ValueError(f"measurement {item.metric!r} signal binding must be {sorted(expected)!r}")
        for alternative in task.topology_refinement.resolved_alternatives() if task.topology_refinement else []:
            other = alternative.alternative_generic_simulation or task.generic_simulation
            if (item.metric not in other.metric_names_for_analysis(item.analysis)
                    or measurement_signals(other, item.metric, item.analysis) != expected
                    or (item.analysis == "stability" and other.stability != task.generic_simulation.stability)):
                raise ValueError("topology alternatives must retain the reviewed measurement/probe bindings")
    required = {c.metric for c in task.constraints}
    if task.objective:
        required.add(task.objective.metric)
    if any(name not in definitions or not definitions[name].required for name in required):
        raise ValueError("every constraint and objective requires a required measurement definition")
    for stage in task.analysis_stages:
        for name in stage.constraint_metrics:
            if definitions[name].analysis != stage.analysis.value:
                raise ValueError("measurement definition disagrees with the analysis-stage assignment")


def missing_required_measurements(plan: MeasurementPlan | None, metrics: dict[str, float], analysis: str) -> list[str]:
    if plan is None:
        return []
    return sorted(m.metric for m in plan.measurements if m.required
                  and m.analysis == analysis and not math.isfinite(metrics.get(m.metric, math.nan)))


def compile_measurement_task(template_path: Path, request_path: Path) -> tuple[dict, Any | None]:
    from .design_review import file_reference, read_json
    from .models import TaskSpec
    from .planner import build_plan

    request = MeasurementRequest.model_validate(read_json(request_path))
    report = {"schema_version": 1, "kind": "measurement_compile", "source": "software_inference",
              "sources": {"template": file_reference(template_path), "request": file_reference(request_path)},
              "uncertainties": list(request.uncertainties), "issues": [], "task_emitted": False,
              "remote_execution_performed": False, "execution_authorized": False}
    if request.uncertainties:
        report["status"] = "needs_confirmation"
        return report, None
    raw = read_json(template_path)
    raw.pop("measurement_plan", None)
    analyses = list(dict.fromkeys(item.analysis for item in request.measurements))
    if not raw.get("analysis_stages"):
        if len(analyses) == 1:
            raw["analysis"] = analyses[0]
        else:
            raw.pop("analysis", None)
            constraint_names = {c["metric"] for c in raw.get("constraints", [])}
            raw["analysis_stages"] = [
                {"id": f"measure-{i:02d}-{analysis}", "analysis": analysis,
                 "constraint_metrics": [m.metric for m in request.measurements
                                        if m.analysis == analysis and m.metric in constraint_names]}
                for i, analysis in enumerate(analyses, 1)
            ]
    if request.operating_range is not None:
        if raw.get("operating_conditions"):
            raise ValueError("range compilation refuses to replace existing explicit operating conditions")
        raw["operating_conditions"] = request.operating_range.compile()
    raw["safety"] = {**raw.get("safety", {}), "allow_remote_compute": False,
                     "allow_remote_write": False, "replace_existing": False}
    try:
        task = TaskSpec.model_validate(raw)
        from .profiles import load_pdk_profile
        profile = load_pdk_profile(task.pdk_profile)
        unknown_corners = {c.process_corner for c in task.operating_conditions} - set(profile.process_corners)
        if unknown_corners:
            raise ValueError(f"operating conditions name unconfigured PDK corners: {sorted(unknown_corners)}")
        raw["measurement_plan"] = MeasurementPlan(
            context_sha256=canonical_sha256(measurement_context(task)),
            measurements=request.measurements,
        ).model_dump(mode="json")
        task = TaskSpec.model_validate(raw)
        plan = build_plan(task)
    except ValueError as exc:
        report.update(status="needs_definition", issues=[str(exc)])
        return report, None
    from .executor import TaskExecutor
    candidates = TaskExecutor._candidate_space_size(task)
    alternatives = task.topology_refinement.resolved_alternatives() if task.topology_refinement else []
    candidates *= 1 + len(alternatives)
    if task.operation.value == "simulation.run":
        candidates = 1
    report.update(status="draft_ready", task_emitted=True, plan_token=plan.confirmation_token,
                  condition_count=len(task.operating_conditions) or 1,
                  analysis_count=len(task.resolved_analyses()),
                  maximum_candidate_analysis_runs=min(candidates, task.limits.max_iterations) *
                  (len(task.operating_conditions) or 1) * len(task.resolved_analyses()),
                  coverage_statement="finite sampled points only; no continuous-range guarantee",
                  cost_boundary="primary analyses only; early gates can reduce work; winner verification, "
                                "OA/readback, retries and agent thinking are not included",
                  measurements=[m.model_dump(mode="json") for m in request.measurements])
    return report, task
