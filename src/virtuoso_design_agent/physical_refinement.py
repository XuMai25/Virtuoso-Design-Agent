"""Compile explicit physical design points into existing bounded atomic tasks.

This is a local calculator/compiler, not a circuit solver, optimizer or executor.
Bias assumptions and total-width/CDF conventions are deliberately explicit.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .characterization import DeviceDataSource, MosCharacterizationArtifact, lookup_mos_by_gm_over_id
from .design_reasoning import ReasoningControl, validate_reasoning_control
from .design_review import file_reference, read_json
from .generic_simulation import GenericTestbenchOverrides
from .models import AtomicCandidate, AtomicCandidateSet, CandidateSetSource, InstanceParameterUpdate, StrictModel, TaskSpec
from .op_relinearization import (NumericInstanceParameterBinding, NumericTestbenchParameterBinding,
                                 _NUMERIC_UNITS, _related_value)
from .profiles import load_pdk_profile
from .spectre_values import spectre_scalar


class _Contract(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PhysicalAxis(_Contract):
    name: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    unit: Literal["A", "S", "1/V", "um", "V", "F", "ohm", "1", "m", "nm", "mV", "uA", "pF", "fF", "kohm"]
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self):
        if self.maximum < self.minimum:
            raise ValueError("physical axis maximum must not be below minimum")
        return self


class PhysicalValue(_Contract):
    axis: StrictStr | None = None
    value: float | None = None
    scale: float = Field(default=1.0, gt=0.0)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.axis is None) == (self.value is None):
            raise ValueError("physical value requires exactly one explicit axis or constant")
        return self


class MosPhysicalMapping(_Contract):
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    characterization: Path
    characterization_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    characterization_input: Path | None = None
    model: StrictStr
    polarity: Literal["nmos", "pmos"]
    model_parameters: dict[StrictStr, StrictStr]
    length_um: PhysicalValue
    gm_over_id_per_v: PhysicalValue
    current_a: PhysicalValue | None = None
    gm_s: PhysicalValue | None = None
    vds_magnitude_v: float = Field(gt=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)
    minimum_headroom_v: float = Field(ge=0.0)
    width_binding: NumericInstanceParameterBinding
    length_binding: NumericInstanceParameterBinding
    total_width_per_cdf_width: float = Field(gt=0.0)
    characterization_total_width_per_normalized_width: float = Field(gt=0.0)
    width_convention: StrictStr = Field(min_length=1, max_length=2000)
    minimum_cdf_width_um: float = Field(gt=0.0)
    maximum_cdf_width_um: float = Field(gt=0.0)
    width_grid_um: float = Field(gt=0.0)

    @model_validator(mode="after")
    def explicit_geometry(self):
        if (self.current_a is None) == (self.gm_s is None):
            raise ValueError("MOS mapping requires exactly one drain current or target gm")
        if self.width_binding.unit != "um" or self.length_binding.unit != "um":
            raise ValueError("physical MOS width/length bindings must explicitly use um")
        if self.maximum_cdf_width_um < self.minimum_cdf_width_um or not self.width_convention.strip():
            raise ValueError("physical width requires ordered bounds and an explicit convention")
        return self


class DirectPhysicalMapping(_Contract):
    value: PhysicalValue
    cdf: NumericInstanceParameterBinding | None = None
    testbench: NumericTestbenchParameterBinding | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.cdf is None) == (self.testbench is None):
            raise ValueError("direct physical mapping requires exactly one CDF or testbench binding")
        return self


class PhysicalDesignPoint(_Contract):
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    values: dict[StrictStr, float]


class PhysicalRefinementRequest(_Contract):
    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=96)
    task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    pdk_profile_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    process_corner: StrictStr
    temperature_c: float = Field(ge=-273.15, le=300.0)
    axes: list[PhysicalAxis] = Field(min_length=1, max_length=16)
    devices: list[MosPhysicalMapping] = Field(default_factory=list, max_length=8)
    direct: list[DirectPhysicalMapping] = Field(default_factory=list, max_length=32)
    points: list[PhysicalDesignPoint] = Field(min_length=1, max_length=3)
    uncertainties: list[StrictStr] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def unique_names(self):
        for rows in (self.axes, self.devices, self.points):
            names = [getattr(row, "name", None) or row.id for row in rows]
            if len(names) != len(set(names)):
                raise ValueError("physical axes, device ids and point ids must be unique")
        if not self.devices and not self.direct:
            raise ValueError("physical refinement needs a device or direct mapping")
        return self


def compile_physical_refinement(request_path: Path, task_path: Path, task: TaskSpec) -> tuple[dict, TaskSpec | None]:
    from .measurements import canonical_sha256

    sources = {"request": file_reference(request_path), "task": file_reference(task_path)}
    request = PhysicalRefinementRequest.model_validate(read_json(request_path))
    issues = list(request.uncertainties)
    if len(request.points) > task.limits.max_iterations:
        issues.append("Physical point count exceeds the existing task candidate budget; no automatic budget expansion.")
    profile = load_pdk_profile(task.pdk_profile)
    if (request.task_sha256 != sources["task"]["sha256"] or
            request.pdk_profile_sha256 != canonical_sha256(profile.model_dump(mode="json"))):
        raise ValueError("physical refinement source task or PDK profile SHA-256 changed")
    if (task.circuit.value != "existing_schematic" or task.design_context is None or task.generic_simulation is None
            or task.operation.value not in {"simulation.run", "design.tune", "design.close_loop"}):
        raise ValueError("physical refinement requires the existing schematic simulation/tuning contract")
    if task.candidate_set or task.parameter_space or task.instance_parameter_space or task.theory_seed or task.topology_refinement:
        raise ValueError("physical refinement needs an explicit fixed baseline template; do not silently replace an existing search domain")
    if request.id == task.id:
        raise ValueError("physical refinement requires a new task id")
    if task.operating_conditions:
        conditions = {(c.process_corner, c.temperature_c) for c in task.operating_conditions}
        if (request.process_corner, request.temperature_c) not in conditions:
            raise ValueError("physical design point is not a declared operating condition")
    elif request.process_corner != profile.model_section:
        raise ValueError("physical refinement requires an explicit matching nominal corner and temperature")
    elif task.generic_simulation.temperature_c is None:
        issues.append("Nominal task temperature is unspecified; calculations use the declared table temperature but no task may be emitted.")
    elif task.generic_simulation.temperature_c != request.temperature_c:
        raise ValueError("physical refinement requires an explicit matching nominal corner and temperature")
    axes = {a.name: a for a in request.axes}
    used_axes = set()

    def value(ref: PhysicalValue, unit: str, point: PhysicalDesignPoint) -> float:
        if ref.axis is None:
            result = ref.value * ref.scale
        else:
            if ref.axis not in axes or axes[ref.axis].unit != unit:
                raise ValueError(f"physical axis is missing or has the wrong unit: {ref.axis}, expected {unit}")
            used_axes.add(ref.axis)
            result = point.values[ref.axis] * ref.scale
        if not math.isfinite(result):
            raise ValueError("physical value conversion is nonfinite")
        return result

    artifacts = {}
    for number, device in enumerate(request.devices):
        path = (request_path.resolve().parent / device.characterization).resolve()
        ref = file_reference(path)
        if ref["sha256"] != device.characterization_sha256:
            raise ValueError("physical characterization SHA-256 changed")
        payload = read_json(path)
        if "actions" in payload:
            # Use the same real-characterization loader as the existing theory
            # path. A standalone table's self-declared source is not promotion.
            from .theory_derivation import _load_artifact
            _, artifact = _load_artifact(path)
            validation = next(a for a in payload["actions"] if a["action"] == "device.characterize.validate")["details"]
            if (validation.get("holdout_audit", {}).get("passed") is not True or
                    validation.get("point_counts", {}).get("holdout", 0) < 1):
                issues.append(f"{device.id}: characterization has no passing independent holdout audit.")
            raw_actions = [a for a in payload["actions"] if a["action"] == "device.characterize"
                           and a["status"] == "succeeded" and a["evidence_source"] == "eda_result"]
            if device.characterization_input is None or len(raw_actions) != 1:
                issues.append(f"{device.id}: retained characterization input and one raw EDA action are required to bind the model include.")
            else:
                input_path = (request_path.resolve().parent / device.characterization_input).resolve()
                input_ref = file_reference(input_path)
                sources[f"characterization_input_{number}"] = input_ref
                evidence = raw_actions[0]["details"].get("evidence", {})
                from .characterization import _validate_manifest
                try:
                    _validate_manifest(raw_actions[0]["details"])
                except RuntimeError as exc:
                    issues.append(f"{device.id}: {exc}")
                entries = [e for e in evidence.get("artifact_manifest", []) if e.get("path") == "mos_characterization.scs"]
                if (len(entries) != 1 or entries[0].get("sha256") != input_ref["sha256"] or
                        entries[0].get("size_bytes") != input_path.stat().st_size or
                        evidence.get("manifest_sha256") != artifact.source_artifact_sha256 or
                        evidence.get("artifact_manifest_complete") is not True):
                    issues.append(f"{device.id}: characterization input does not match the recorded manifest.")
                include_lines = [line.strip() for line in input_path.read_text(encoding="utf-8").splitlines()
                                 if line.lstrip().startswith("include ")]
                parsed = [re.fullmatch(r'include\s+"([^"]+)"\s+section=([^\s]+)', line) for line in include_lines]
                includes = [(m[1], m[2]) for m in parsed if m]
                if any(m is None for m in parsed) or includes != [(profile.model_include, profile.model_section)]:
                    issues.append(f"{device.id}: characterization model include/section differs from the selected PDK profile.")
        else:
            artifact = MosCharacterizationArtifact.model_validate(payload)
            issues.append(f"{device.id}: standalone table is calculation-only; a validated characterization run is required for a task.")
        if (artifact.pdk_profile, artifact.process_corner, artifact.temperature_c) != (
                task.pdk_profile, request.process_corner, request.temperature_c):
            raise ValueError("physical characterization PDK/corner/temperature mismatch")
        if artifact.model_parameters_by_polarity.get(device.polarity, {}) != device.model_parameters:
            raise ValueError("physical characterization model parameter signature differs")
        if artifact.characterized_width_um is None:
            raise ValueError("physical sizing requires the explicitly characterized normalization width")
        artifacts[device.id] = artifact
        sources[f"characterization_{number}"] = ref
    if any(a.source == DeviceDataSource.SYNTHETIC_EXAMPLE for a in artifacts.values()):
        issues.append("Synthetic characterization is an example only; no OA task may be emitted.")
    rows, candidates = [], []
    for point in request.points:
        if set(point.values) != set(axes) or any(not a.minimum <= point.values[a.name] <= a.maximum for a in request.axes):
            raise ValueError("physical point must specify every axis within its declared bounds")
        fields = {}
        overrides = {"sources": {}, "loads": {}}
        assigned = set()

        def cdf(binding, number):
            for instance, parameter, scale, offset, grid in binding.members():
                control = ReasoningControl(kind="cdf", name=instance, parameter=parameter)
                validate_reasoning_control(task, control)
                if control.key() in assigned:
                    raise ValueError("physical mappings overlap a CDF target")
                assigned.add(control.key())
                related = _related_value(number, scale, offset, grid, f"{instance}.{parameter}")
                if related <= 0:
                    raise ValueError("physical CDF values must be positive")
                literal = f"{related:.12g}{_NUMERIC_UNITS[binding.unit][1]}"
                decoded = spectre_scalar(literal)
                if decoded is None or not math.isclose(decoded / _NUMERIC_UNITS[binding.unit][0], related, rel_tol=1e-12):
                    raise ValueError("physical CDF serialization loses precision")
                fields.setdefault(instance, {})[parameter] = literal

        calculations = []
        point_issues = []
        for device in request.devices:
            length = value(device.length_um, "um", point)
            gmid = value(device.gm_over_id_per_v, "1/V", point)
            current = (value(device.current_a, "A", point) if device.current_a else
                       value(device.gm_s, "S", point) / gmid if gmid > 0 else 0)
            if min(length, gmid, current) <= 0:
                raise ValueError("physical length, gm/ID and drain current magnitudes must be positive")
            try:
                lookup = lookup_mos_by_gm_over_id(
                    artifacts[device.id], point_id=f"physical-{device.id}", model=device.model, polarity=device.polarity,
                    length_um=length, gm_over_id_per_v=gmid, vds_magnitude_v=device.vds_magnitude_v,
                    vsb_magnitude_v=device.vsb_magnitude_v,
                )
            except (ValueError, RuntimeError) as exc:
                point_issues.append(f"{device.id}: {exc}")
                continue
            p = lookup.point
            normalized_width = current / p.drain_current_density_a_per_um
            total_width = normalized_width * device.characterization_total_width_per_normalized_width
            raw_width = total_width / device.total_width_per_cdf_width
            width = float((Decimal(str(raw_width)) / Decimal(str(device.width_grid_um))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(device.width_grid_um)))
            headroom = device.vds_magnitude_v - p.vdsat_magnitude_v
            if not device.minimum_cdf_width_um <= width <= device.maximum_cdf_width_um:
                point_issues.append(f"{device.id}: quantized width is outside the explicit CDF width bounds")
                continue
            if headroom < device.minimum_headroom_v:
                point_issues.append(f"{device.id}: assumed-bias saturation headroom is below the declared minimum")
                continue
            cdf(device.width_binding, width)
            cdf(device.length_binding, length)
            actual_current = (p.drain_current_density_a_per_um * width * device.total_width_per_cdf_width
                              / device.characterization_total_width_per_normalized_width)
            calculations.append({"device": device.id, "requested_current_a": current, "gm_over_id_per_v": gmid,
                "total_width_um_before_grid": total_width, "cdf_width_um": width, "length_um": length,
                "width_binding": device.width_binding.model_dump(mode="json"),
                "length_binding": device.length_binding.model_dump(mode="json"),
                "total_width_per_cdf_width": device.total_width_per_cdf_width,
                "characterization_total_width_per_normalized_width": device.characterization_total_width_per_normalized_width,
                "estimated_current_after_grid_a": actual_current, "estimated_gm_after_grid_s": actual_current * gmid,
                "assumed_vds_v": device.vds_magnitude_v, "assumed_vsb_v": device.vsb_magnitude_v,
                "lookup_vgs_magnitude_v": p.vgs_magnitude_v, "estimated_headroom_v": headroom,
                "width_convention": device.width_convention, "lookup": lookup.model_dump(mode="json"),
                "source": "software_inference"})
        for mapping in request.direct:
            binding = mapping.cdf or mapping.testbench
            number = value(mapping.value, binding.unit, point)
            if mapping.cdf:
                cdf(binding, number)
            else:
                simulation = task.generic_simulation
                objects = {o.name: o for o in (simulation.sources if binding.target == "source_dc" else simulation.loads)}
                expected_kind = {"V": "voltage", "mV": "voltage", "A": "current", "uA": "current",
                                 "F": "capacitor", "pF": "capacitor", "fF": "capacitor", "ohm": "resistor", "kohm": "resistor"}[binding.unit]
                for name, scale, offset, grid in binding.members():
                    key = (binding.target, name)
                    if name not in objects or objects[name].kind != expected_kind or key in assigned:
                        raise ValueError("physical testbench target is absent, has the wrong kind, or overlaps")
                    assigned.add(key)
                    if task.operating_conditions and binding.target == "source_dc" and name == simulation.operating_condition_supply_source:
                        raise ValueError("physical source coordinate would be overwritten by declared operating conditions")
                    related = _related_value(number, scale, offset, grid, name)
                    scaled = related * _NUMERIC_UNITS[binding.unit][0]
                    if not math.isfinite(scaled) or not math.isclose(scaled / _NUMERIC_UNITS[binding.unit][0], related, rel_tol=1e-12, abs_tol=0):
                        raise ValueError("physical testbench serialization loses precision")
                    if binding.target == "source_dc":
                        overrides["sources"][name] = {"dc_value": scaled}
                    else:
                        overrides["loads"][name] = scaled
        rows.append({"id": point.id, "physical_values": point.values, "calculations": calculations,
                     "issues": point_issues})
        issues.extend(f"{point.id}: {issue}" for issue in point_issues)
        if not point_issues:
            candidates.append(AtomicCandidate(id=point.id,
                instance_parameter_updates=[InstanceParameterUpdate(instance=n, parameters=p) for n, p in fields.items()],
                testbench_overrides=GenericTestbenchOverrides.model_validate(overrides) if any(overrides.values()) else None))
    if used_axes != set(axes):
        raise ValueError("physical axes include an unused degree of freedom")
    draft = None
    if not issues:
        candidate_set = AtomicCandidateSet(source=CandidateSetSource(generator="physical_refinement", id=request.id,
            bindings={"request": sources["request"]["sha256"], "task": sources["task"]["sha256"],
                      "pdk_profile": request.pdk_profile_sha256, "evidence": canonical_sha256(sources),
                      "input_contract": canonical_sha256(task.model_dump(mode="json"))},
            evidence_source="software_inference"),
            candidates=candidates)
        raw = task.model_dump(mode="json")
        raw.update(id=request.id, operation="design.tune", candidate_set=candidate_set.model_dump(mode="json"), create_if_missing=False)
        moved = {(u.instance, name) for u in candidates[0].instance_parameter_updates for name in u.parameters}
        raw["instance_parameter_updates"] = [
            {"instance": u.instance, "parameters": {name: v for name, v in u.parameters.items() if (u.instance, name) not in moved}}
            for u in task.instance_parameter_updates if any((u.instance, name) not in moved for name in u.parameters)
        ]
        raw["safety"].update(allow_remote_compute=False, allow_remote_write=False, replace_existing=False)
        draft = TaskSpec.model_validate(raw)
    for ref in sources.values():
        if file_reference(Path(ref["path"])) != ref:
            raise ValueError("physical refinement source changed during compilation")
    return {"schema_version": 1, "kind": "physical_refinement", "source": "software_inference", "sources": sources,
            "status": "draft_ready" if draft else "needs_evidence", "issues": issues, "points": rows,
            "direct_mappings": [m.model_dump(mode="json") for m in request.direct],
            "axes": [a.model_dump(mode="json") for a in request.axes], "execution_authorized": False,
            "limitations": ["Id=gm/(gm/Id); table-normalized width and target total/CDF width conventions are separately declared.",
                "Width scaling and grid-adjusted estimates are not a solved circuit operating point or performance evidence.",
                "Width/finger conventions and model-parameter mappings are authored declarations, not new OA readback.",
                "No absolute VGS bias is written automatically; source/bulk potentials must not be guessed.",
                "At most three explicit points; no automatic sweep, candidate ranking, fitting or extrapolation."]}, draft


def render_physical_refinement(report: dict) -> str:
    lines = ["## 物理变量到普通候选", "", f"状态：{report['status']}；仅 software_inference，未执行。",
             "不自动解电路偏置；器件量估算不等于模块性能或规格已通过。", "",
             "| 候选/器件 | 请求 Id A | gm/ID 1/V | CDF W µm | L µm | 估算 Id A（格点后） | 假设偏置下余量 V |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for point in report["points"]:
        for c in point["calculations"]:
            lines.append(f"| {point['id']}/{c['device']} | {c['requested_current_a']:.6g} | {c['gm_over_id_per_v']:.6g} | "
                         f"{c['cdf_width_um']:.6g} | {c['length_um']:.6g} | {c['estimated_current_after_grid_a']:.6g} | "
                         f"{c['estimated_headroom_v']:.6g} |")
        lines.extend(["", f"{point['id']} 的物理坐标：`{point['physical_values']}`"])
    lines.extend(["", *[f"- {issue}" for issue in report["issues"]], "",
                  "偏置、体电位、模型签名和宽度/指数量约定见 JSON；缺材料不生成任务。", ""])
    return "\n".join(lines)
