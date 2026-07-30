"""Structured testbenches for a user-provided OA schematic.

The contract intentionally accepts only typed primitives and identifiers.  It
never accepts raw Spectre statements, SKILL, shell text, or an alternate
netlist, so the simulated design remains the netlist generated from the target
OA schematic by the existing Bridge path.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$]*$"
_NODE_PATTERN = r"^(?:0|[A-Za-z_][A-Za-z0-9_$!.]*)$"
_METRIC_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"

GENERIC_AC_METRICS = frozenset(
    {
        "low_frequency_gain_v_per_v",
        "low_frequency_gain_db",
        "low_frequency_phase_deg",
        "peak_gain_db",
        "peak_gain_frequency_hz",
        "gain_peaking_db",
        "bandwidth_3db_hz",
        "phase_at_bandwidth_deg",
        "gain_bandwidth_product_hz",
        "unity_gain_frequency_hz",
        "phase_at_unity_gain_deg",
    }
)

GENERIC_TRANSIENT_METRICS = frozenset(
    {
        "small_signal_gain_v_per_v",
        "small_signal_gain_db",
        "gain_at_max_amplitude_v_per_v",
        "gain_compression_at_max_db",
        "input_amplitude_max_v_peak",
        "output_at_max_amplitude_v_peak",
        "output_peak_to_peak_at_max_amplitude_v",
        "thd_at_max_amplitude_percent",
        "max_thd_percent",
        "small_signal_supply_power_uw",
        "large_signal_supply_power_uw",
        "max_average_supply_power_uw",
        "hd2_at_max_amplitude_dbc",
        "hd3_at_max_amplitude_dbc",
        "input_1db_compression_v_peak",
        "output_1db_compression_v_peak",
    }
)

GENERIC_NOISE_METRICS = frozenset(
    {
        "integrated_output_noise_v_rms",
        "integrated_output_noise_uv_rms",
        "integrated_input_referred_noise_v_rms",
        "integrated_input_referred_noise_uv_rms",
        "output_noise_density_start_nv_per_sqrt_hz",
        "output_noise_density_stop_nv_per_sqrt_hz",
        "input_noise_density_start_nv_per_sqrt_hz",
        "input_noise_density_stop_nv_per_sqrt_hz",
    }
)

_GENERIC_DERIVED_METRICS = (
    GENERIC_AC_METRICS | GENERIC_TRANSIENT_METRICS | GENERIC_NOISE_METRICS
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GenericVoltageExpression(_StrictModel):
    positive_node: StrictStr = Field(pattern=_NODE_PATTERN)
    negative_node: StrictStr = Field(default="0", pattern=_NODE_PATTERN)

    @model_validator(mode="after")
    def require_distinct_nodes(self) -> "GenericVoltageExpression":
        if self.positive_node == self.negative_node:
            raise ValueError("voltage expression nodes must differ")
        return self


class GenericTransferSpec(_StrictModel):
    input: GenericVoltageExpression
    output: GenericVoltageExpression


class GenericDynamicAnalysisSpec(_StrictModel):
    """Explicit stimulus and supply-current probes for transient/noise."""

    stimulus_source: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    power_source: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)


class GenericIndependentSource(_StrictModel):
    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    kind: Literal["voltage", "current"]
    positive_node: StrictStr = Field(pattern=_NODE_PATTERN)
    negative_node: StrictStr = Field(default="0", pattern=_NODE_PATTERN)
    dc_value: float = 0.0
    ac_magnitude: float = Field(default=0.0, ge=0.0)
    ac_phase_deg: float = Field(default=0.0, ge=-360.0, le=360.0)

    @model_validator(mode="after")
    def require_distinct_nodes(self) -> "GenericIndependentSource":
        if self.positive_node == self.negative_node:
            raise ValueError(f"source {self.name!r} nodes must differ")
        return self


class GenericPassiveLoad(_StrictModel):
    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    kind: Literal["resistor", "capacitor"]
    positive_node: StrictStr = Field(pattern=_NODE_PATTERN)
    negative_node: StrictStr = Field(default="0", pattern=_NODE_PATTERN)
    value: float = Field(gt=0.0)

    @model_validator(mode="after")
    def require_distinct_nodes(self) -> "GenericPassiveLoad":
        if self.positive_node == self.negative_node:
            raise ValueError(f"load {self.name!r} nodes must differ")
        return self


class GenericVoltageMetric(_StrictModel):
    metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    expression: GenericVoltageExpression


class GenericSourceCurrentMetric(_StrictModel):
    metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    source: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)


class GenericOperatingPointMetric(_StrictModel):
    metric: StrictStr = Field(pattern=_METRIC_PATTERN)
    instance: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    quantity: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)


class GenericNetlistParameterBinding(_StrictModel):
    instance: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    oa_parameter: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    netlist_parameter: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)


class GenericHierarchyBinding(_StrictModel):
    """One explicitly named top-level subcell and its si subcircuit contract."""

    instance: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    library: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    cell: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    view: Literal["schematic"] = "schematic"
    subcircuit: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    terminal_order: list[StrictStr] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_hierarchy_binding(self) -> "GenericHierarchyBinding":
        if len(self.terminal_order) != len(set(self.terminal_order)):
            raise ValueError("hierarchy binding terminal_order contains duplicates")
        return self


class GenericOaSimulationSpec(_StrictModel):
    """One explicit OA-to-si verification surface with optional one-level hierarchy."""

    schema_version: Literal[1] = 1
    sources: list[GenericIndependentSource] = Field(min_length=1, max_length=32)
    loads: list[GenericPassiveLoad] = Field(default_factory=list, max_length=32)
    dc_voltage_metrics: list[GenericVoltageMetric] = Field(
        default_factory=list,
        max_length=64,
    )
    dc_current_metrics: list[GenericSourceCurrentMetric] = Field(
        default_factory=list,
        max_length=32,
    )
    operating_point_metrics: list[GenericOperatingPointMetric] = Field(
        default_factory=list,
        max_length=128,
    )
    transfer: GenericTransferSpec | None = None
    dynamic_analysis: GenericDynamicAnalysisSpec | None = None
    netlist_parameter_bindings: list[GenericNetlistParameterBinding] = Field(
        default_factory=list,
        max_length=128,
    )
    hierarchy_bindings: list[GenericHierarchyBinding] = Field(
        default_factory=list,
        max_length=32,
    )
    operating_condition_supply_source: StrictStr | None = Field(
        default=None,
        pattern=_IDENTIFIER_PATTERN,
    )
    temperature_c: float | None = Field(default=None, ge=-273.15, le=300.0)

    @model_validator(mode="after")
    def validate_contract(self) -> "GenericOaSimulationSpec":
        source_names = [item.name for item in self.sources]
        load_names = [item.name for item in self.loads]
        if len(source_names) != len(set(source_names)):
            raise ValueError("generic simulation source names must be unique")
        if len(load_names) != len(set(load_names)):
            raise ValueError("generic simulation load names must be unique")
        collisions = sorted(set(source_names) & set(load_names))
        if collisions:
            raise ValueError(
                "generic simulation element names collide: " + ", ".join(collisions)
            )

        unknown_sources = sorted(
            {item.source for item in self.dc_current_metrics} - set(source_names)
        )
        if unknown_sources:
            raise ValueError(
                "current metrics reference unknown sources: "
                + ", ".join(unknown_sources)
            )

        metric_names = [
            item.metric
            for collection in (
                self.dc_voltage_metrics,
                self.dc_current_metrics,
                self.operating_point_metrics,
            )
            for item in collection
        ]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("generic simulation metric names must be unique")
        reserved = sorted(set(metric_names) & _GENERIC_DERIVED_METRICS)
        if reserved:
            raise ValueError(
                "generic simulation custom metrics use reserved derived names: "
                + ", ".join(reserved)
            )
        if not metric_names and self.transfer is None:
            raise ValueError(
                "generic simulation requires a DC/OP metric or an AC transfer"
            )

        oa_bindings = [
            (item.instance, item.oa_parameter)
            for item in self.netlist_parameter_bindings
        ]
        netlist_bindings = [
            (item.instance, item.netlist_parameter)
            for item in self.netlist_parameter_bindings
        ]
        if len(oa_bindings) != len(set(oa_bindings)):
            raise ValueError("generic simulation repeats an OA parameter binding")
        if len(netlist_bindings) != len(set(netlist_bindings)):
            raise ValueError("generic simulation repeats a netlist parameter binding")

        hierarchy_instances = [item.instance for item in self.hierarchy_bindings]
        if len(hierarchy_instances) != len(set(hierarchy_instances)):
            raise ValueError("generic simulation repeats a hierarchy instance binding")

        if self.operating_condition_supply_source is not None:
            source = next(
                (
                    item
                    for item in self.sources
                    if item.name == self.operating_condition_supply_source
                ),
                None,
            )
            if source is None:
                raise ValueError(
                    "generic operating-condition supply source is unknown"
                )
            if source.kind != "voltage" or abs(source.dc_value) <= 0.0:
                raise ValueError(
                    "generic operating-condition supply source must be a nonzero "
                    "voltage source"
                )

        if self.dynamic_analysis is not None:
            sources = {item.name: item for item in self.sources}
            stimulus = sources.get(self.dynamic_analysis.stimulus_source)
            power = sources.get(self.dynamic_analysis.power_source)
            if stimulus is None:
                raise ValueError(
                    "generic dynamic analysis stimulus_source is unknown"
                )
            if power is None:
                raise ValueError("generic dynamic analysis power_source is unknown")
            if stimulus.kind != "voltage" or power.kind != "voltage":
                raise ValueError(
                    "generic dynamic analysis stimulus and power sources must be voltage sources"
                )
            if stimulus.name == power.name:
                raise ValueError(
                    "generic dynamic analysis stimulus and power sources must differ"
                )
            if self.transfer is None:
                raise ValueError(
                    "generic dynamic analysis requires an input/output transfer"
                )
            if (
                stimulus.positive_node != self.transfer.input.positive_node
                or stimulus.negative_node != self.transfer.input.negative_node
            ):
                raise ValueError(
                    "generic dynamic stimulus source nodes must exactly match the transfer input"
                )
            if abs(power.dc_value) <= 0.0:
                raise ValueError(
                    "generic dynamic power source requires a nonzero DC voltage"
                )
        return self

    def validate_analysis(self, analysis: str) -> None:
        if analysis not in {"dc", "ac", "transient", "noise"}:
            raise ValueError(
                "generic OA simulation supports dc, ac, transient, or noise"
            )
        if analysis == "ac":
            if self.transfer is None:
                raise ValueError("generic OA AC simulation requires transfer")
            if not any(source.ac_magnitude > 0.0 for source in self.sources):
                raise ValueError(
                    "generic OA AC simulation requires a nonzero source ac_magnitude"
                )
        if analysis in {"transient", "noise"}:
            if self.transfer is None or self.dynamic_analysis is None:
                raise ValueError(
                    f"generic OA {analysis} simulation requires transfer and dynamic_analysis"
                )

    def metric_names_for_analysis(self, analysis: str) -> set[str]:
        self.validate_analysis(analysis)
        names = {
            item.metric
            for collection in (
                self.dc_voltage_metrics,
                self.dc_current_metrics,
                self.operating_point_metrics,
            )
            for item in collection
        }
        if analysis == "ac":
            names.update(GENERIC_AC_METRICS)
        elif analysis == "transient":
            names.update(GENERIC_TRANSIENT_METRICS)
        elif analysis == "noise":
            names.update(GENERIC_NOISE_METRICS)
        return names

    def referenced_nodes(self) -> set[str]:
        nodes: set[str] = set()
        for item in (*self.sources, *self.loads):
            nodes.update((item.positive_node, item.negative_node))
        for item in self.dc_voltage_metrics:
            nodes.update(
                (item.expression.positive_node, item.expression.negative_node)
            )
        if self.transfer is not None:
            nodes.update(
                (
                    self.transfer.input.positive_node,
                    self.transfer.input.negative_node,
                    self.transfer.output.positive_node,
                    self.transfer.output.negative_node,
                )
            )
        return nodes


def _number(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("generic simulation numeric value must be finite")
    return f"{number:.12g}"


def render_generic_oa_testbench(
    spec: GenericOaSimulationSpec,
    *,
    analysis: Literal["dc", "ac", "transient", "noise"],
    remote_netlist_path: str,
    model_configuration: str,
    ac_sweep: dict[str, Any] | None = None,
    linearity_sweep: dict[str, Any] | None = None,
    noise_sweep: dict[str, Any] | None = None,
) -> str:
    """Render a deterministic wrapper around one Bridge-generated OA netlist."""

    spec.validate_analysis(analysis)
    if '"' in remote_netlist_path:
        raise ValueError("netlist path contains an unsupported quote")
    if analysis == "ac" and ac_sweep is None:
        raise ValueError("generic OA AC simulation requires ac_sweep")
    if analysis == "transient" and linearity_sweep is None:
        raise ValueError("generic OA transient simulation requires linearity_sweep")
    if analysis == "noise" and noise_sweep is None:
        raise ValueError("generic OA noise simulation requires noise_sweep")
    provided_sweeps = {
        "ac": ac_sweep,
        "transient": linearity_sweep,
        "noise": noise_sweep,
    }
    unexpected = [
        name
        for name, value in provided_sweeps.items()
        if value is not None and name != analysis
    ]
    if unexpected:
        raise ValueError(
            f"generic OA {analysis} simulation received unrelated sweep settings: "
            + ", ".join(unexpected)
        )

    source_lines: list[str] = []
    for source in spec.sources:
        primitive = "vsource" if source.kind == "voltage" else "isource"
        ac_clause = ""
        if analysis == "ac":
            ac_clause = (
                f" mag={_number(source.ac_magnitude)}"
                f" phase={_number(source.ac_phase_deg)} type=dc"
            )
        elif (
            analysis == "noise"
            and spec.dynamic_analysis is not None
            and source.name == spec.dynamic_analysis.stimulus_source
        ):
            ac_clause = " mag=1 phase=0 type=dc"
        elif (
            analysis == "transient"
            and spec.dynamic_analysis is not None
            and source.name == spec.dynamic_analysis.stimulus_source
        ):
            ac_clause = (
                " type=sine"
                f" sinedc={_number(source.dc_value)}"
                " ampl=vda_stimulus_amplitude"
                " freq=vda_stimulus_frequency"
            )
        source_lines.append(
            f"{source.name} ({source.positive_node} {source.negative_node}) "
            f"{primitive} dc={_number(source.dc_value)}{ac_clause}"
        )

    load_lines = [
        (
            f"{load.name} ({load.positive_node} {load.negative_node}) "
            + (
                f"resistor r={_number(load.value)}"
                if load.kind == "resistor"
                else f"capacitor c={_number(load.value)}"
            )
        )
        for load in spec.loads
    ]

    analysis_line = ""
    if analysis == "ac":
        assert ac_sweep is not None
        analysis_line = (
            f"ac ac start={_number(float(ac_sweep['start_hz']))} "
            f"stop={_number(float(ac_sweep['stop_hz']))} "
            f"dec={int(ac_sweep.get('points_per_decade', 20))} annotate=status"
        )
    elif analysis == "transient":
        assert linearity_sweep is not None
        amplitudes = [float(value) for value in linearity_sweep["amplitudes_v"]]
        frequency_hz = float(linearity_sweep["frequency_hz"])
        total_cycles = int(linearity_sweep.get("settling_cycles", 4)) + int(
            linearity_sweep.get("measurement_cycles", 8)
        )
        points_per_cycle = int(linearity_sweep.get("points_per_cycle", 128))
        sample_step_s = 1.0 / (frequency_hz * points_per_cycle)
        stop_s = total_cycles / frequency_hz + sample_step_s
        values = " ".join(_number(value) for value in amplitudes)
        analysis_line = (
            f"sw1 sweep param=vda_stimulus_amplitude values=[{values}] {{\n"
            f"  tran tran stop={_number(stop_s)} maxstep={_number(sample_step_s)} "
            f"strobeperiod={_number(sample_step_s)} strobeoutput=all annotate=status\n"
            "}"
        )
    elif analysis == "noise":
        assert noise_sweep is not None
        assert spec.transfer is not None
        assert spec.dynamic_analysis is not None
        output = spec.transfer.output
        analysis_line = (
            f"noise ({output.positive_node} {output.negative_node}) noise "
            f"start={_number(float(noise_sweep['start_hz']))} "
            f"stop={_number(float(noise_sweep['stop_hz']))} "
            f"dec={int(noise_sweep.get('points_per_decade', 20))} "
            f"iprobe={spec.dynamic_analysis.stimulus_source} annotate=status"
        )

    node_saves = sorted(spec.referenced_nodes() - {"0"})
    current_saves = sorted(
        {f"{item.source}:p" for item in spec.dc_current_metrics}
    )
    if analysis == "transient" and spec.dynamic_analysis is not None:
        current_saves = sorted(
            set(current_saves) | {f"{spec.dynamic_analysis.power_source}:p"}
        )
    op_saves = sorted(
        {
            f"{item.instance}:{item.quantity}"
            for item in spec.operating_point_metrics
        }
    )
    save_tokens = node_saves + current_saves + op_saves
    temperature = (
        "" if spec.temperature_c is None else f" temp={_number(spec.temperature_c)}"
    )
    lines = [
        "simulator lang=spectre",
        model_configuration,
        # Foundry model include trees may leave Spectre in a SPICE language
        # section.  Reassert the language before consuming the OA ``si``
        # netlist; flat primitives can otherwise mask this hierarchy-only bug.
        "simulator lang=spectre",
        f'include "{remote_netlist_path}"',
        "",
    ]
    if analysis == "transient":
        assert linearity_sweep is not None
        lines.append(
            "parameters "
            f"vda_stimulus_amplitude={_number(float(linearity_sweep['amplitudes_v'][0]))} "
            f"vda_stimulus_frequency={_number(float(linearity_sweep['frequency_hz']))}"
        )
    lines.extend([
        *source_lines,
        *load_lines,
        "",
        (
            "simulatorOptions options"
            f"{temperature} psfversion=\"1.4.0\" reltol=1e-4 "
            "vabstol=1e-6 iabstol=1e-12"
        ),
        'dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status',
        "dcOpInfo info what=oppoint where=rawfile",
    ])
    if analysis_line:
        lines.append(analysis_line)
    lines.extend(("save " + " ".join(save_tokens), "saveOptions options save=allpub", ""))
    return "\n".join(lines)
