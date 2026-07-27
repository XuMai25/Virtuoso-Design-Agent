"""Structured, OA-free Spectre previews for cheap topology screening."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_METRIC_ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_NODE_PATTERN = r"^(?:0|[A-Za-z_][A-Za-z0-9_$]*)$"
_MODEL_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]*$"
_PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_NUMERIC_LITERAL_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    r"(?:[A-Za-z][A-Za-z0-9_]*)?$"
)
_RESERVED_MOS_PARAMETERS = {"w", "l", "nf", "m", "multi"}
_MOS_SAVE_QUANTITIES = (
    "ids",
    "vgs",
    "vds",
    "vbs",
    "vdsat",
    "gm",
    "gds",
    "gmb",
    "cgg",
    "cgd",
    "cgs",
    "cgb",
    "cdg",
    "cdd",
    "cds",
    "cdb",
    "csg",
    "csd",
    "css",
    "csb",
    "cbg",
    "cbd",
    "cbs",
    "cbb",
    "cjd",
    "cjs",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PreviewMosInstance(_StrictModel):
    name: StrictStr = Field(pattern=r"^M[A-Za-z0-9_]*$")
    polarity: Literal["nmos", "pmos"]
    drain: StrictStr = Field(pattern=_NODE_PATTERN)
    gate: StrictStr = Field(pattern=_NODE_PATTERN)
    source: StrictStr = Field(pattern=_NODE_PATTERN)
    bulk: StrictStr = Field(pattern=_NODE_PATTERN)
    width_um: float = Field(gt=0.0)
    length_um: float = Field(gt=0.0)
    fingers: int = Field(default=1, ge=1, le=1024)
    multiplicity: float = Field(default=1.0, gt=0.0)
    model: StrictStr | None = Field(default=None, pattern=_MODEL_PATTERN)
    model_parameters: dict[StrictStr, StrictStr] = Field(
        default_factory=dict,
        max_length=64,
    )

    @field_validator("model_parameters")
    @classmethod
    def validate_model_parameters(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        for name, literal in value.items():
            if _PARAMETER_PATTERN.fullmatch(name) is None:
                raise ValueError(f"invalid MOS model parameter name: {name!r}")
            if name.lower() in _RESERVED_MOS_PARAMETERS:
                raise ValueError(
                    f"MOS parameter {name!r} is controlled by the preview contract"
                )
            if len(literal) > 64 or _NUMERIC_LITERAL_PATTERN.fullmatch(literal) is None:
                raise ValueError(
                    f"MOS model parameter {name!r} must be a numeric Spectre literal"
                )
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_terminals(self) -> "PreviewMosInstance":
        if self.drain == self.source:
            raise ValueError(f"MOS {self.name} drain and source must differ")
        return self


class PreviewResistor(_StrictModel):
    name: StrictStr = Field(pattern=r"^R[A-Za-z0-9_]*$")
    positive: StrictStr = Field(pattern=_NODE_PATTERN)
    negative: StrictStr = Field(pattern=_NODE_PATTERN)
    resistance_ohm: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_terminals(self) -> "PreviewResistor":
        if self.positive == self.negative:
            raise ValueError(f"resistor {self.name} connects one node twice")
        return self


class PreviewCapacitor(_StrictModel):
    name: StrictStr = Field(pattern=r"^C[A-Za-z0-9_]*$")
    positive: StrictStr = Field(pattern=_NODE_PATTERN)
    negative: StrictStr = Field(pattern=_NODE_PATTERN)
    capacitance_f: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_terminals(self) -> "PreviewCapacitor":
        if self.positive == self.negative:
            raise ValueError(f"capacitor {self.name} connects one node twice")
        return self


class PreviewVoltageSource(_StrictModel):
    name: StrictStr = Field(pattern=r"^V[A-Za-z0-9_]*$")
    positive: StrictStr = Field(pattern=_NODE_PATTERN)
    negative: StrictStr = Field(pattern=_NODE_PATTERN)
    dc_v: float
    ac_magnitude_v: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_terminals(self) -> "PreviewVoltageSource":
        if self.positive == self.negative:
            raise ValueError(f"voltage source {self.name} connects one node twice")
        return self


class NetlistPreviewVariant(_StrictModel):
    id: StrictStr = Field(pattern=_METRIC_ID_PATTERN)
    output_positive: StrictStr = Field(pattern=_NODE_PATTERN)
    output_negative: StrictStr = Field(default="0", pattern=_NODE_PATTERN)
    mosfets: list[PreviewMosInstance] = Field(min_length=1, max_length=256)
    voltage_sources: list[PreviewVoltageSource] = Field(
        default_factory=list,
        max_length=64,
    )
    resistors: list[PreviewResistor] = Field(default_factory=list, max_length=512)
    capacitors: list[PreviewCapacitor] = Field(default_factory=list, max_length=512)

    @model_validator(mode="after")
    def validate_variant(self) -> "NetlistPreviewVariant":
        if self.output_positive == self.output_negative:
            raise ValueError("preview output nodes must differ")
        names = [item.name for item in self.mosfets]
        names.extend(item.name for item in self.voltage_sources)
        names.extend(item.name for item in self.resistors)
        names.extend(item.name for item in self.capacitors)
        _require_unique(names, f"variant {self.id} element")
        return self


class NetlistPreviewSpec(_StrictModel):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=_ID_PATTERN)
    temperature_c: float = Field(default=27.0, ge=-273.15, le=300.0)
    input_source: StrictStr = Field(pattern=r"^V[A-Za-z0-9_]*$")
    supply_sources: list[StrictStr] = Field(min_length=1, max_length=16)
    voltage_sources: list[PreviewVoltageSource] = Field(min_length=1, max_length=64)
    resistors: list[PreviewResistor] = Field(default_factory=list, max_length=512)
    capacitors: list[PreviewCapacitor] = Field(default_factory=list, max_length=512)
    variants: list[NetlistPreviewVariant] = Field(min_length=1, max_length=8)
    source_bindings: dict[StrictStr, StrictStr] = Field(
        default_factory=dict,
        max_length=16,
    )

    @field_validator("source_bindings")
    @classmethod
    def validate_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            if not name or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("preview source bindings require SHA-256 values")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_preview(self) -> "NetlistPreviewSpec":
        _require_unique((variant.id for variant in self.variants), "variant")
        _require_unique(self.supply_sources, "supply source")

        shared_names = [item.name for item in self.voltage_sources]
        shared_names.extend(item.name for item in self.resistors)
        shared_names.extend(item.name for item in self.capacitors)
        _require_unique(shared_names, "shared element")
        source_by_name = {item.name: item for item in self.voltage_sources}
        if self.input_source not in source_by_name:
            raise ValueError("input_source must name one shared voltage source")
        missing_supply = sorted(set(self.supply_sources) - set(source_by_name))
        if missing_supply:
            raise ValueError(
                "supply_sources must name shared voltage sources: "
                + ", ".join(missing_supply)
            )

        for variant in self.variants:
            variant_names = [item.name for item in variant.mosfets]
            variant_names.extend(item.name for item in variant.voltage_sources)
            variant_names.extend(item.name for item in variant.resistors)
            variant_names.extend(item.name for item in variant.capacitors)
            overlap = sorted(set(shared_names) & set(variant_names))
            if overlap:
                raise ValueError(
                    f"variant {variant.id} repeats shared element names: {overlap}"
                )
            referenced_nodes = _variant_nodes(self, variant)
            for node in (variant.output_positive, variant.output_negative):
                if node != "0" and node not in referenced_nodes:
                    raise ValueError(
                        f"variant {variant.id} output node {node!r} is not connected"
                    )
        return self

    def canonical_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def variant(self, variant_id: str) -> NetlistPreviewVariant:
        matches = [variant for variant in self.variants if variant.id == variant_id]
        if len(matches) != 1:
            raise ValueError(f"unknown preview variant: {variant_id}")
        return matches[0]


def _require_unique(values, label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        raise ValueError(f"duplicate {label} names: {duplicates}")


def _variant_nodes(
    spec: NetlistPreviewSpec,
    variant: NetlistPreviewVariant,
) -> set[str]:
    nodes: set[str] = set()
    for source in [*spec.voltage_sources, *variant.voltage_sources]:
        nodes.update((source.positive, source.negative))
    for resistor in [*spec.resistors, *variant.resistors]:
        nodes.update((resistor.positive, resistor.negative))
    for capacitor in [*spec.capacitors, *variant.capacitors]:
        nodes.update((capacitor.positive, capacitor.negative))
    for mosfet in variant.mosfets:
        nodes.update((mosfet.drain, mosfet.gate, mosfet.source, mosfet.bulk))
    return nodes


def _render_voltage_source(source: PreviewVoltageSource) -> str:
    ac = (
        f" mag={source.ac_magnitude_v:.12g} type=dc"
        if source.ac_magnitude_v > 0.0
        else ""
    )
    return (
        f"{source.name} ({source.positive} {source.negative}) "
        f"vsource dc={source.dc_v:.12g}{ac}"
    )


def render_spectre_preview_deck(
    spec: NetlistPreviewSpec,
    variant_id: str,
    profile: Mapping[str, object],
    *,
    analysis: Literal["dc", "ac"],
    ac_sweep: Mapping[str, object] | None = None,
) -> str:
    """Render one safe, deterministic Spectre deck from the structured graph."""

    variant = spec.variant(variant_id)
    model_path = str(profile.get("model_include", ""))
    model_section = str(profile.get("model_section", ""))
    if not model_path or '"' in model_path:
        raise ValueError("invalid preview model include path")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", model_section) is None:
        raise ValueError("invalid preview model section")
    default_models = {
        "nmos": str(profile.get("nmos_cell", "")),
        "pmos": str(profile.get("pmos_cell", "")),
    }
    if any(re.fullmatch(_MODEL_PATTERN, model) is None for model in default_models.values()):
        raise ValueError("invalid preview MOS model name in PDK profile")

    input_source = next(
        source for source in spec.voltage_sources if source.name == spec.input_source
    )
    analysis_statement = ""
    if analysis == "ac":
        if input_source.ac_magnitude_v <= 0.0:
            raise ValueError("AC preview requires a nonzero input-source magnitude")
        if ac_sweep is None:
            raise ValueError("AC preview requires ac_sweep")
        analysis_statement = (
            f'ac ac start={float(ac_sweep["start_hz"]):.12g} '
            f'stop={float(ac_sweep["stop_hz"]):.12g} '
            f'dec={int(ac_sweep.get("points_per_decade", 20))} annotate=status'
        )
    elif analysis != "dc":
        raise ValueError(f"unsupported netlist preview analysis: {analysis}")

    elements: list[str] = []
    elements.extend(
        _render_voltage_source(source)
        for source in [*spec.voltage_sources, *variant.voltage_sources]
    )
    elements.extend(
        f"{resistor.name} ({resistor.positive} {resistor.negative}) "
        f"resistor r={resistor.resistance_ohm:.12g}"
        for resistor in [*spec.resistors, *variant.resistors]
    )
    elements.extend(
        f"{capacitor.name} ({capacitor.positive} {capacitor.negative}) "
        f"capacitor c={capacitor.capacitance_f:.12g}"
        for capacitor in [*spec.capacitors, *variant.capacitors]
    )
    for mosfet in variant.mosfets:
        model = mosfet.model or default_models[mosfet.polarity]
        if re.fullmatch(_MODEL_PATTERN, model) is None:
            raise ValueError(f"invalid preview MOS model: {model!r}")
        extra = "".join(
            f" {name}={literal}"
            for name, literal in mosfet.model_parameters.items()
        )
        elements.append(
            f"{mosfet.name} ({mosfet.drain} {mosfet.gate} "
            f"{mosfet.source} {mosfet.bulk}) {model} "
            f"w={mosfet.width_um:.12g}u l={mosfet.length_um:.12g}u "
            f"nf={mosfet.fingers} multi={mosfet.multiplicity:.12g}{extra}"
        )

    nodes = sorted(node for node in _variant_nodes(spec, variant) if node != "0")
    sources = [*spec.voltage_sources, *variant.voltage_sources]
    node_save = "save " + " ".join(nodes)
    source_save = "save " + " ".join(f"{source.name}:p" for source in sources)
    mos_saves = [
        "save "
        + " ".join(
            f"{mosfet.name}:{quantity}" for quantity in _MOS_SAVE_QUANTITIES
        )
        for mosfet in variant.mosfets
    ]
    lines = [
        "simulator lang=spectre",
        f'include "{model_path}" section={model_section}',
        "",
        *elements,
        "",
        (
            'simulatorOptions options psfversion="1.4.0" '
            f"temp={spec.temperature_c:.12g} reltol=1e-4 "
            "vabstol=1e-6 iabstol=1e-12"
        ),
        'dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status',
        "dcOpInfo info what=oppoint where=rawfile",
    ]
    if analysis_statement:
        lines.append(analysis_statement)
    lines.extend((node_save, source_save, *mos_saves, "saveOptions options save=selected", ""))
    deck = "\n".join(lines)
    if not deck.endswith("\n"):
        deck += "\n"
    if not math.isfinite(spec.temperature_c):
        raise ValueError("preview temperature must be finite")
    return deck
