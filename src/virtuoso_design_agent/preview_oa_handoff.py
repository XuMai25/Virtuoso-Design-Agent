"""Compile a passed preview shortlist into a normal OA candidate task."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import CircuitKind, Operation, RunStatus, TaskSpec
from .preview_selection import PreviewSelectionResult


_TUNING_OPERATIONS = {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}
_CASCODE_CANDIDATE_PARAMETERS = frozenset(
    {"cascode_width_um", "cascode_length_um", "cascode_bias_v"}
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merge_binding(bindings: dict[str, str], name: str, digest: str) -> None:
    existing = bindings.get(name)
    if existing is not None and existing != digest:
        raise ValueError(f"preview OA handoff binding {name!r} conflicts")
    bindings[name] = digest


def _expected_target_topology(template: TaskSpec) -> str | None:
    declared = template.expected_target_topology_variant
    if template.circuit is not CircuitKind.COMMON_SOURCE:
        return declared
    assert template.candidate_set is not None
    cascode_candidates = [
        candidate
        for candidate in template.candidate_set.candidates
        if _CASCODE_CANDIDATE_PARAMETERS.intersection(candidate.parameters)
    ]
    if not cascode_candidates:
        return declared
    if len(cascode_candidates) != len(template.candidate_set.candidates) or any(
        not _CASCODE_CANDIDATE_PARAMETERS.issubset(candidate.parameters)
        for candidate in cascode_candidates
    ):
        raise ValueError(
            "preview OA handoff cascode candidates require width, length, and bias"
        )
    inferred = "cascode_common_source"
    if declared is not None and declared != inferred:
        raise ValueError(
            "preview OA handoff declared target topology conflicts with candidates"
        )
    return inferred


def build_oa_task_from_preview_shortlist(
    selection_path: Path,
    candidate_task_path: Path,
    *,
    task_id: str,
) -> TaskSpec:
    """Subset a full atomic OA task in the exact passed-preview rank order.

    Both inputs are bound byte-for-byte into the returned candidate source. The
    normal planner/executor remains responsible for authorization, OA readback,
    same-source netlisting, EDA metrics, checkpointing, and final selection.
    """

    selection = PreviewSelectionResult.model_validate_json(
        selection_path.read_text(encoding="utf-8")
    )
    template = TaskSpec.model_validate_json(
        candidate_task_path.read_text(encoding="utf-8")
    )

    if (
        selection.status is not RunStatus.SUCCEEDED
        or not selection.selection_utility_gate_passed
    ):
        raise ValueError("preview selection utility Gate did not pass")
    shortlist_ids = list(selection.shortlist_candidate_ids)
    if not shortlist_ids:
        raise ValueError("preview selection produced an empty shortlist")
    if (
        selection.shortlist_size != len(shortlist_ids)
        or len(selection.shortlist_variant_ids) != len(shortlist_ids)
        or len(shortlist_ids) != len(set(shortlist_ids))
    ):
        raise ValueError("preview shortlist ids, variants, and size are inconsistent")

    if template.operation not in _TUNING_OPERATIONS:
        raise ValueError("preview OA handoff requires a normal tuning candidate task")
    if template.circuit is CircuitKind.NETLIST_PREVIEW or template.target is None:
        raise ValueError("preview OA handoff requires a concrete non-preview OA target")
    if template.candidate_set is None or template.theory_seed is not None:
        raise ValueError(
            "preview OA handoff currently requires one compiled atomic candidate_set"
        )
    if task_id == template.id:
        raise ValueError("preview OA handoff task id must differ from the full task id")
    if selection.pdk_profile != template.pdk_profile:
        raise ValueError("preview selection PDK profile mismatch")
    if template.analysis is None or selection.analysis != template.analysis.value:
        raise ValueError("preview selection analysis mismatch")

    source = template.candidate_set.source
    if source.generator != selection.candidate_generator:
        raise ValueError("preview selection candidate generator mismatch")
    if source.id != selection.candidate_source_id:
        raise ValueError("preview selection candidate source id mismatch")
    if selection.candidate_source_sha256 not in source.bindings.values():
        raise ValueError(
            "preview selection candidate source SHA-256 is not bound by the OA task"
        )

    domain_ids = [candidate.id for candidate in template.candidate_set.candidates]
    selection_domain = sorted(selection.candidates, key=lambda item: item.index)
    selection_indices = [item.index for item in selection_domain]
    if selection_indices != list(range(1, len(selection_domain) + 1)):
        raise ValueError("preview selection candidate indices are not contiguous")
    selection_domain_ids = [item.source_candidate_id for item in selection_domain]
    if (
        selection.declared_candidate_count != len(domain_ids)
        or selection.evaluated_candidate_count != len(domain_ids)
        or selection_domain_ids != domain_ids
    ):
        raise ValueError("preview selection candidate order differs from the OA task")
    expected_variants = {
        item.source_candidate_id: item.preview_variant_id
        for item in selection_domain
    }
    if selection.shortlist_variant_ids != [
        expected_variants[candidate_id] for candidate_id in shortlist_ids
    ]:
        raise ValueError("preview shortlist variant identity drifted")

    candidates_by_id = {
        candidate.id: candidate for candidate in template.candidate_set.candidates
    }
    missing = [item for item in shortlist_ids if item not in candidates_by_id]
    if missing:
        raise ValueError(
            "preview shortlist names candidates absent from the OA task: "
            + ", ".join(missing)
        )
    if template.limits.max_iterations < len(domain_ids):
        raise ValueError("full OA candidate task budget does not cover its domain")

    bindings = dict(source.bindings)
    _merge_binding(
        bindings,
        "preview_candidate_source_sha256",
        selection.candidate_source_sha256,
    )
    _merge_binding(
        bindings,
        "preview_selection_result_sha256",
        _file_sha256(selection_path),
    )
    _merge_binding(
        bindings,
        "preview_oa_candidate_task_sha256",
        _file_sha256(candidate_task_path),
    )

    raw = template.model_dump(mode="json", exclude_none=True)
    raw["id"] = task_id
    expected_topology = _expected_target_topology(template)
    if expected_topology is not None:
        raw["expected_target_topology_variant"] = expected_topology
    raw["candidate_set"] = {
        "source": {
            "generator": "vda.preview-shortlist",
            "id": selection.policy_id,
            "bindings": bindings,
            "evidence_source": "software_inference",
        },
        "candidates": [
            candidates_by_id[item].model_dump(mode="json") for item in shortlist_ids
        ],
    }
    raw["limits"]["max_iterations"] = len(shortlist_ids)
    return TaskSpec.model_validate(raw)


__all__ = ["build_oa_task_from_preview_shortlist"]
