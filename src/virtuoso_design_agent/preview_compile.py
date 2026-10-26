"""Deterministically bind finite candidates into structured netlist previews."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import (
    AtomicCandidateSet,
    CircuitKind,
    Operation,
    TaskSpec,
    TheorySeedCandidateSet,
)
from .netlist_preview import NetlistPreviewVariant


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_METRIC_ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_TARGET_FIELDS = {
    "mosfet": {"width_um", "length_um", "fingers", "multiplicity"},
    "voltage_source": {"dc_v", "ac_magnitude_v"},
    "resistor": {"resistance_ohm"},
    "capacitor": {"capacitance_f"},
}
_TARGET_COLLECTIONS = {
    "mosfet": "mosfets",
    "voltage_source": "voltage_sources",
    "resistor": "resistors",
    "capacitor": "capacitors",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PreviewParameterBinding(_StrictModel):
    """Map one semantic candidate parameter to one typed preview field."""

    parameter: StrictStr = Field(min_length=1, max_length=128)
    element_kind: Literal["mosfet", "voltage_source", "resistor", "capacitor"]
    element: StrictStr = Field(min_length=1, max_length=128)
    field: StrictStr = Field(min_length=1, max_length=64)
    scale: float = Field(default=1.0, gt=0.0)

    @model_validator(mode="after")
    def validate_target_field(self) -> "PreviewParameterBinding":
        if self.field not in _TARGET_FIELDS[self.element_kind]:
            allowed = ", ".join(sorted(_TARGET_FIELDS[self.element_kind]))
            raise ValueError(
                f"{self.element_kind} binding field must be one of: {allowed}"
            )
        return self


class PreviewCandidateCompilePolicy(_StrictModel):
    """Explicit, bounded handoff from an existing candidate domain to a graph."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(pattern=_ID_PATTERN)
    expected_candidate_generator: StrictStr = Field(pattern=_ID_PATTERN)
    expected_candidate_source_id: StrictStr = Field(pattern=_ID_PATTERN)
    expected_pdk_profile: StrictStr | None = Field(default=None, min_length=1)
    candidate_ids: list[StrictStr] = Field(min_length=1, max_length=16)
    template_variant_id: StrictStr = Field(pattern=_METRIC_ID_PATTERN)
    variant_id_prefix: StrictStr = Field(pattern=_METRIC_ID_PATTERN)
    fixed_parameters: dict[StrictStr, float] = Field(
        default_factory=dict,
        max_length=64,
    )
    bindings: list[PreviewParameterBinding] = Field(min_length=1, max_length=64)

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("preview candidate ids must be unique")
        return value

    @field_validator("fixed_parameters")
    @classmethod
    def validate_fixed_parameters(
        cls, value: dict[str, float]
    ) -> dict[str, float]:
        for name, number in value.items():
            if not name or not math.isfinite(number):
                raise ValueError("fixed candidate parameters must be finite")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def validate_mapping(self) -> "PreviewCandidateCompilePolicy":
        targets = [
            (binding.element_kind, binding.element, binding.field)
            for binding in self.bindings
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("preview bindings cannot write one target field twice")
        mapped = {binding.parameter for binding in self.bindings}
        overlap = sorted(mapped & set(self.fixed_parameters))
        if overlap:
            raise ValueError(
                "candidate parameters cannot be both fixed and mapped: "
                + ", ".join(overlap)
            )
        return self


@dataclass(frozen=True)
class _CompileCandidate:
    id: str
    parameters: dict[str, float]
    has_raw_updates: bool


@dataclass(frozen=True)
class _CandidateDomain:
    generator: str
    source_id: str
    pdk_profile: str | None
    candidates: tuple[_CompileCandidate, ...]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json_object(path: Path, *, label: str) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return raw


def _load_candidate_domain(path: Path) -> _CandidateDomain:
    raw = _load_json_object(path, label="candidate source")
    has_atomic = raw.get("candidate_set") is not None
    has_theory = raw.get("theory_seed") is not None
    if has_atomic and has_theory:
        raise ValueError("candidate source cannot contain both candidate_set and theory_seed")
    candidate_raw = raw.get("candidate_set") if has_atomic else raw.get("theory_seed")
    if candidate_raw is None:
        candidate_raw = raw

    atomic: AtomicCandidateSet | None = None
    theory: TheorySeedCandidateSet | None = None
    if has_theory:
        theory = TheorySeedCandidateSet.model_validate(candidate_raw)
    elif has_atomic:
        atomic = AtomicCandidateSet.model_validate(candidate_raw)
    else:
        try:
            atomic = AtomicCandidateSet.model_validate(candidate_raw)
        except ValidationError:
            try:
                theory = TheorySeedCandidateSet.model_validate(candidate_raw)
            except ValidationError as theory_error:
                raise ValueError(
                    "candidate source is neither an AtomicCandidateSet nor a "
                    "TheorySeedCandidateSet"
                ) from theory_error

    pdk_profile = raw.get("pdk_profile")
    if pdk_profile is not None and (
        not isinstance(pdk_profile, str) or not pdk_profile
    ):
        raise ValueError("candidate source pdk_profile must be a nonempty string")

    if atomic is not None:
        return _CandidateDomain(
            generator=atomic.source.generator,
            source_id=atomic.source.id,
            pdk_profile=pdk_profile,
            candidates=tuple(
                _CompileCandidate(
                    id=candidate.id,
                    parameters=dict(candidate.parameters),
                    has_raw_updates=bool(candidate.instance_parameter_updates),
                )
                for candidate in atomic.candidates
            ),
        )
    assert theory is not None
    return _CandidateDomain(
        generator=theory.source.generator,
        source_id=theory.source.policy_id,
        pdk_profile=pdk_profile,
        candidates=tuple(
            _CompileCandidate(
                id=candidate.id,
                parameters=dict(candidate.parameters),
                has_raw_updates=False,
            )
            for candidate in theory.candidates
        ),
    )


def _validate_candidate_contract(
    policy: PreviewCandidateCompilePolicy,
    domain: _CandidateDomain,
) -> list[_CompileCandidate]:
    if domain.generator != policy.expected_candidate_generator:
        raise ValueError(
            "candidate generator mismatch: "
            f"expected {policy.expected_candidate_generator!r}, got {domain.generator!r}"
        )
    if domain.source_id != policy.expected_candidate_source_id:
        raise ValueError(
            "candidate source id mismatch: "
            f"expected {policy.expected_candidate_source_id!r}, got {domain.source_id!r}"
        )
    candidates_by_id = {candidate.id: candidate for candidate in domain.candidates}
    missing = [item for item in policy.candidate_ids if item not in candidates_by_id]
    if missing:
        raise ValueError("candidate source is missing selected ids: " + ", ".join(missing))
    selected = [candidates_by_id[item] for item in policy.candidate_ids]
    if any(candidate.has_raw_updates for candidate in selected):
        raise ValueError(
            "preview compilation rejects raw instance_parameter_updates; use semantic "
            "bindings to typed graph fields"
        )

    parameter_names = set(selected[0].parameters)
    mapped = {binding.parameter for binding in policy.bindings}
    fixed = set(policy.fixed_parameters)
    unknown = sorted((mapped | fixed) - parameter_names)
    if unknown:
        raise ValueError(
            "preview policy names parameters absent from the candidate domain: "
            + ", ".join(unknown)
        )
    unaccounted = sorted(parameter_names - mapped - fixed)
    if unaccounted:
        raise ValueError(
            "preview policy leaves candidate parameters unaccounted: "
            + ", ".join(unaccounted)
        )
    for candidate in selected:
        for name, expected in policy.fixed_parameters.items():
            actual = candidate.parameters[name]
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15):
                raise ValueError(
                    f"fixed candidate parameter {name!r} drifted for {candidate.id!r}: "
                    f"expected {expected}, got {actual}"
                )
    return selected


def _apply_candidate(
    template: NetlistPreviewVariant,
    candidate: _CompileCandidate,
    bindings: list[PreviewParameterBinding],
    *,
    variant_id: str,
) -> NetlistPreviewVariant:
    raw = template.model_dump(mode="json")
    raw["id"] = variant_id
    for binding in bindings:
        collection = raw[_TARGET_COLLECTIONS[binding.element_kind]]
        matches = [item for item in collection if item["name"] == binding.element]
        if len(matches) != 1:
            raise ValueError(
                f"template variant {template.id!r} does not contain exactly one "
                f"{binding.element_kind} named {binding.element!r}"
            )
        value = candidate.parameters[binding.parameter] * binding.scale
        if not math.isfinite(value):
            raise ValueError(
                f"binding {binding.parameter!r} produced a non-finite value"
            )
        if binding.field == "fingers":
            if not value.is_integer():
                raise ValueError("MOS fingers bindings must produce an integer")
            matches[0][binding.field] = int(value)
        else:
            matches[0][binding.field] = value
    return NetlistPreviewVariant.model_validate(raw)


def _merge_binding(
    bindings: dict[str, str],
    name: str,
    digest: str,
) -> None:
    existing = bindings.get(name)
    if existing is not None and existing != digest:
        raise ValueError(f"preview source binding {name!r} conflicts with the template")
    bindings[name] = digest


def build_preview_task_from_candidates(
    policy_path: Path,
    candidate_source_path: Path,
    task_template_path: Path,
) -> TaskSpec:
    """Compile selected candidate tuples into one validated preview task."""

    policy = PreviewCandidateCompilePolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    domain = _load_candidate_domain(candidate_source_path)
    selected = _validate_candidate_contract(policy, domain)
    template = TaskSpec.model_validate(
        _load_json_object(task_template_path, label="preview task template")
    )
    if template.operation is not Operation.SIMULATION_RUN:
        raise ValueError("preview task template requires operation='simulation.run'")
    if template.circuit is not CircuitKind.NETLIST_PREVIEW:
        raise ValueError("preview task template requires circuit='netlist_preview'")
    if template.netlist_preview is None:
        raise ValueError("preview task template is missing netlist_preview settings")
    if policy.expected_pdk_profile is not None and (
        template.pdk_profile != policy.expected_pdk_profile
    ):
        raise ValueError("preview task template PDK profile does not match policy")
    if domain.pdk_profile is not None and domain.pdk_profile != template.pdk_profile:
        raise ValueError("candidate source PDK profile does not match task template")

    spec = template.netlist_preview
    placeholder = spec.variant(policy.template_variant_id)
    if policy.template_variant_id in spec.variant_source_ids:
        raise ValueError("template placeholder cannot predeclare a variant source id")
    objective_prefix = f"{policy.template_variant_id}__"
    if template.objective is not None and template.objective.metric.startswith(
        objective_prefix
    ):
        raise ValueError(
            "a placeholder-specific objective is ambiguous after candidate expansion"
        )

    generated: list[NetlistPreviewVariant] = []
    variant_source_ids = dict(spec.variant_source_ids)
    for index, candidate in enumerate(selected, start=1):
        variant_id = f"{policy.variant_id_prefix}_{index:03d}"
        generated.append(
            _apply_candidate(
                placeholder,
                candidate,
                policy.bindings,
                variant_id=variant_id,
            )
        )
        variant_source_ids[variant_id] = candidate.id

    generated_ids = {variant.id for variant in generated}
    fixed_ids = {
        variant.id
        for variant in spec.variants
        if variant.id != policy.template_variant_id
    }
    collisions = sorted(generated_ids & fixed_ids)
    if collisions:
        raise ValueError(
            "generated preview variant ids collide with fixed variants: "
            + ", ".join(collisions)
        )
    variants: list[NetlistPreviewVariant] = []
    for variant in spec.variants:
        if variant.id == policy.template_variant_id:
            variants.extend(generated)
        else:
            variants.append(variant)

    source_bindings = dict(spec.source_bindings)
    _merge_binding(
        source_bindings,
        "candidate_source_sha256",
        _file_sha256(candidate_source_path),
    )
    _merge_binding(
        source_bindings,
        "preview_compile_policy_sha256",
        _file_sha256(policy_path),
    )
    _merge_binding(
        source_bindings,
        "preview_task_template_sha256",
        _file_sha256(task_template_path),
    )

    constraints = []
    for constraint in template.constraints:
        if constraint.metric.startswith(objective_prefix):
            suffix = constraint.metric[len(policy.template_variant_id) :]
            constraints.extend(
                constraint.model_copy(update={"metric": variant.id + suffix})
                for variant in generated
            )
        else:
            constraints.append(constraint)

    raw_task = template.model_dump(mode="json", exclude_none=True)
    raw_task["constraints"] = [
        constraint.model_dump(mode="json") for constraint in constraints
    ]
    raw_task["netlist_preview"] = spec.model_copy(
        update={
            "variants": variants,
            "source_bindings": source_bindings,
            "source_bindings_evidence_source": "software_inference",
            "variant_source_ids": variant_source_ids,
            "variant_source_ids_evidence_source": "software_inference",
        }
    ).model_dump(mode="json")
    return TaskSpec.model_validate(raw_task)


__all__ = [
    "PreviewCandidateCompilePolicy",
    "PreviewParameterBinding",
    "build_preview_task_from_candidates",
]
