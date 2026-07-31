"""Compile verified OA-to-si discoveries into normal onboarding tasks."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .generic_simulation import (
    GenericBindingDiscoverySource,
    GenericDerivedCallbackBinding,
    GenericNetlistParameterBinding,
    ParameterBindingDiscoverySpec,
)
from .models import (
    ActionRecord,
    CircuitKind,
    DesignTarget,
    EvidenceSource,
    Operation,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from .onboarding import ExistingSchematicOnboardingDraft
from .onboarding_resolution import (
    ExistingSchematicOnboardingResolution,
    resolve_onboarding_draft,
)
from .parameter_binding import (
    canonical_parameter_table_sha256,
    reclassify_parameter_binding_run,
)
from .planner import build_plan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OnboardingPromotedBindingEvidence(_StrictModel):
    """One real discovery run accepted by the onboarding compiler."""

    schema_version: Literal[1] = 1
    discovery_task_id: StrictStr
    discovery_plan_token: StrictStr
    discovery_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_run_status: Literal["partial", "succeeded"]
    target: DesignTarget
    pdk_profile: StrictStr
    topology_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    complete_cdf_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    complete_cdf_parameter_count: int = Field(ge=1)
    baseline_netlist_signature_sha256: StrictStr = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    probe_netlist_signature_sha256: StrictStr = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    restored_netlist_signature_sha256: StrictStr = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    binding: GenericNetlistParameterBinding
    evidence_sources: dict[
        StrictStr,
        Literal[
            "eda_result",
            "bridge_readback",
            "software_inference",
            "user_input",
            "system_event",
        ],
    ]


class OnboardingBindingPromotionCompilation(_StrictModel):
    """Hash handoff from discovery evidence to one disabled normal TaskSpec."""

    schema_version: Literal[1] = 1
    status: Literal["ready_for_plan"] = "ready_for_plan"
    onboarding_draft_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    onboarding_resolution_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    promoted_bindings: list[OnboardingPromotedBindingEvidence] = Field(
        min_length=1,
        max_length=128,
    )
    compiled_task_id: StrictStr
    compiled_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_plan_token: StrictStr
    execution_enabled: Literal[False] = False
    remote_execution_performed: Literal[False] = False
    oa_write_performed: Literal[False] = False
    evidence_sources: dict[
        StrictStr,
        Literal[
            "eda_result",
            "bridge_readback",
            "software_inference",
            "user_input",
            "system_event",
        ],
    ]


def _file_bytes(path: Path, *, kind: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read binding-promotion {kind} {path}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _model_bytes(model: BaseModel) -> bytes:
    return (model.model_dump_json(indent=2, exclude_none=True) + "\n").encode(
        "utf-8"
    )


def _draft_instance_parameters(
    draft: ExistingSchematicOnboardingDraft,
    instance: str,
) -> dict[str, str]:
    matches = [
        item for item in draft.parameter_inventory if item.instance_path == instance
    ]
    if len(matches) != 1:
        raise ValueError(
            "binding promotion target must appear exactly once in the onboarding "
            f"CDF inventory: {instance!r}"
        )
    return {
        name: field.raw_value for name, field in sorted(matches[0].fields.items())
    }


def _discovery_action(record: RunRecord) -> ActionRecord:
    actions = [
        action
        for action in record.actions
        if action.action == "parameters.binding.discover"
    ]
    if len(actions) != 1:
        raise ValueError(
            "binding promotion requires exactly one discovery action"
        )
    action = actions[0]
    if (
        action.status != "succeeded"
        or action.evidence_source is not EvidenceSource.EDA_RESULT
    ):
        raise ValueError(
            "binding promotion discovery action is not successful EDA evidence"
        )
    return action


def _load_promoted_binding(
    discovery_task_path: Path,
    discovery_run_path: Path,
    draft: ExistingSchematicOnboardingDraft,
) -> OnboardingPromotedBindingEvidence:
    task_bytes = _file_bytes(discovery_task_path, kind="discovery task")
    run_bytes = _file_bytes(discovery_run_path, kind="discovery run")
    try:
        task = TaskSpec.model_validate_json(task_bytes)
        record = RunRecord.model_validate_json(run_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "binding promotion discovery task or run is invalid"
        ) from exc
    if (
        task.operation is not Operation.PARAMETERS_BINDING_DISCOVER
        or task.circuit is not CircuitKind.EXISTING_SCHEMATIC
        or task.parameter_binding_discovery is None
        or task.design_context is None
        or task.target is None
    ):
        raise ValueError(
            "binding promotion source is not a complete existing-schematic "
            "binding-discovery task"
        )
    if (
        task.target != draft.source.target
        or task.pdk_profile != draft.source.pdk_profile
    ):
        raise ValueError(
            "binding promotion discovery target or PDK differs from onboarding"
        )
    if task.design_context.expected_topology_sha256 != draft.topology_sha256:
        raise ValueError(
            "binding promotion discovery topology differs from onboarding"
        )
    if (
        not task.safety.allow_remote_compute
        or not task.safety.allow_remote_write
        or task.safety.replace_existing
    ):
        raise ValueError(
            "binding promotion source task lacks explicit non-replacing "
            "compute/write authority"
        )
    if (
        record.task_id != task.id
        or record.plan_token != build_plan(task).confirmation_token
    ):
        raise ValueError(
            "binding promotion discovery run does not match its task plan"
        )
    if record.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("binding promotion requires a real Bridge discovery run")
    if record.status not in {RunStatus.PARTIAL, RunStatus.SUCCEEDED}:
        raise ValueError("binding promotion discovery run is not reusable")

    action = _discovery_action(record)
    details = action.details
    try:
        recorded_contract = ParameterBindingDiscoverySpec.model_validate(
            details.get("contract")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "binding promotion discovery action has an invalid contract"
        ) from exc
    if recorded_contract != task.parameter_binding_discovery:
        raise ValueError(
            "binding promotion task and run carry different discovery contracts"
        )
    expected_parameters = _draft_instance_parameters(
        draft,
        recorded_contract.instance,
    )
    if recorded_contract.expected_instance_parameters != expected_parameters:
        raise ValueError(
            "binding promotion discovery CDF baseline differs from onboarding"
        )
    baseline = details.get("baseline")
    probe = details.get("probe")
    restored = details.get("restored")
    if not all(isinstance(stage, dict) for stage in (baseline, probe, restored)):
        raise ValueError("binding promotion discovery stages are incomplete")
    assert isinstance(baseline, dict)
    assert isinstance(probe, dict)
    assert isinstance(restored, dict)
    if (
        baseline.get("oa_topology_sha256") != draft.topology_sha256
        or probe.get("oa_topology_sha256") != draft.topology_sha256
        or restored.get("oa_topology_sha256") != draft.topology_sha256
    ):
        raise ValueError(
            "binding promotion discovery OA topology drifted from onboarding"
        )

    reclassified = reclassify_parameter_binding_run(discovery_run_path)
    if (
        reclassified.get("source_run_sha256") != _sha256(run_bytes)
        or reclassified.get("source_task_id") != task.id
        or reclassified.get("source_plan_token") != record.plan_token
    ):
        raise ValueError(
            "binding promotion local reclassification is not bound to the source"
        )
    classification = reclassified.get("classification")
    if not isinstance(classification, dict):
        raise ValueError("binding promotion lacks a structured classification")
    status = classification.get("status")
    if status not in {
        "direct_literal_binding",
        "direct_literal_binding_with_derived_callbacks",
    } or reclassified.get("promotable") is not True:
        raise ValueError(
            "binding promotion source did not prove a promotable direct binding"
        )
    promoted = classification.get("promoted_binding")
    if (
        not isinstance(promoted, dict)
        or set(promoted) != {"instance", "oa_parameter", "netlist_parameter"}
        or promoted.get("instance") != recorded_contract.instance
        or promoted.get("oa_parameter") != recorded_contract.oa_parameter
    ):
        raise ValueError("binding promotion primary mapping is inconsistent")
    netlist_parameter = promoted.get("netlist_parameter")
    if not isinstance(netlist_parameter, str) or not netlist_parameter:
        raise ValueError("binding promotion primary netlist field is missing")

    callback_evidence = classification.get("derived_callback_evidence") or []
    if not isinstance(callback_evidence, list):
        raise ValueError("binding promotion derived callback evidence is malformed")
    callbacks: list[GenericDerivedCallbackBinding] = []
    for item in callback_evidence:
        if not isinstance(item, dict) or item.get("netlist_instance") != (
            recorded_contract.instance
        ):
            raise ValueError(
                "binding promotion derived callback escaped the target instance"
            )
        callbacks.append(
            GenericDerivedCallbackBinding(
                oa_parameter=str(item.get("oa_parameter", "")),
                netlist_parameter=str(item.get("netlist_parameter", "")),
            )
        )
    callbacks.sort(key=lambda item: (item.oa_parameter, item.netlist_parameter))
    if status == "direct_literal_binding" and callbacks:
        raise ValueError("direct binding unexpectedly carries callback evidence")
    if status == "direct_literal_binding_with_derived_callbacks" and (
        not callbacks or classification.get("callback_effects_verified") is not True
    ):
        raise ValueError(
            "derived-callback binding lacks complete verified callback evidence"
        )
    dependent_changes = classification.get("dependent_netlist_changes") or []
    dependent_pairs = {
        (str(item.get("instance")), str(item.get("parameter")))
        for item in dependent_changes
        if isinstance(item, dict)
    }
    callback_pairs = {
        (recorded_contract.instance, item.netlist_parameter) for item in callbacks
    }
    if dependent_pairs != callback_pairs:
        raise ValueError(
            "binding promotion callback list differs from the complete netlist delta"
        )

    stage_signatures: dict[str, str] = {}
    for stage_name, stage in (
        ("baseline", baseline),
        ("probe", probe),
        ("restored", restored),
    ):
        signature = stage.get("canonical_netlist_signature_sha256")
        if (
            not isinstance(signature, str)
            or len(signature) != 64
            or any(character not in "0123456789abcdef" for character in signature)
        ):
            raise ValueError(
                f"binding promotion {stage_name} netlist signature is invalid"
            )
        stage_signatures[stage_name] = signature

    complete_cdf_sha256 = canonical_parameter_table_sha256(expected_parameters)
    discovery_source = GenericBindingDiscoverySource(
        discovery_task_id=task.id,
        discovery_plan_token=record.plan_token,
        discovery_task_sha256=_sha256(task_bytes),
        discovery_run_sha256=_sha256(run_bytes),
        classification=status,
        topology_sha256=draft.topology_sha256,
        complete_cdf_sha256=complete_cdf_sha256,
    )
    binding = GenericNetlistParameterBinding(
        instance=recorded_contract.instance,
        oa_parameter=recorded_contract.oa_parameter,
        netlist_parameter=netlist_parameter,
        derived_callbacks=callbacks or None,
        discovery_source=discovery_source,
    )
    return OnboardingPromotedBindingEvidence(
        discovery_task_id=task.id,
        discovery_plan_token=record.plan_token,
        discovery_task_sha256=_sha256(task_bytes),
        discovery_run_sha256=_sha256(run_bytes),
        discovery_run_status=record.status.value,
        target=task.target,
        pdk_profile=task.pdk_profile,
        topology_sha256=draft.topology_sha256,
        complete_cdf_sha256=complete_cdf_sha256,
        complete_cdf_parameter_count=len(expected_parameters),
        baseline_netlist_signature_sha256=stage_signatures["baseline"],
        probe_netlist_signature_sha256=stage_signatures["probe"],
        restored_netlist_signature_sha256=stage_signatures["restored"],
        binding=binding,
        evidence_sources={
            "oa_cdf_and_topology": "bridge_readback",
            "si_netlist_deltas": "eda_result",
            "restoration_consistency": "software_inference",
            "remote_cleanup": "system_event",
            "classification_and_compilation": "software_inference",
            "target_field_and_probe": "user_input",
        },
    )


def _binding_identity(binding: GenericNetlistParameterBinding) -> tuple[str, str]:
    return binding.instance, binding.oa_parameter


def compile_onboarding_with_discovered_bindings(
    draft_path: Path,
    resolution_path: Path,
    binding_sources: Sequence[tuple[Path, Path]],
) -> tuple[TaskSpec, OnboardingBindingPromotionCompilation]:
    """Resolve onboarding intent after adding only locally revalidated bindings."""

    if not binding_sources:
        raise ValueError("binding promotion requires at least one discovery source")
    if len(binding_sources) > 128:
        raise ValueError("binding promotion accepts at most 128 discovery sources")
    draft_bytes = _file_bytes(draft_path, kind="onboarding draft")
    resolution_bytes = _file_bytes(resolution_path, kind="onboarding resolution")
    try:
        draft = ExistingSchematicOnboardingDraft.model_validate_json(draft_bytes)
        resolution = ExistingSchematicOnboardingResolution.model_validate_json(
            resolution_bytes
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "binding promotion onboarding draft or resolution is invalid"
        ) from exc
    if resolution.draft_sha256 != _sha256(draft_bytes):
        raise ValueError(
            "binding promotion resolution does not match the onboarding draft"
        )

    promoted = [
        _load_promoted_binding(task_path, run_path, draft)
        for task_path, run_path in binding_sources
    ]
    promoted.sort(key=lambda item: _binding_identity(item.binding))
    identities = [_binding_identity(item.binding) for item in promoted]
    if len(identities) != len(set(identities)):
        raise ValueError("binding promotion repeats an OA field")

    permissions = {
        (permission.instance, parameter)
        for permission in resolution.instance_parameter_permissions
        for parameter in permission.parameters
    }
    missing_permissions = sorted(set(identities) - permissions)
    if missing_permissions:
        raise ValueError(
            "binding promotion fields lack onboarding permission: "
            + ", ".join(
                f"{instance}.{parameter}"
                for instance, parameter in missing_permissions
            )
        )

    existing_bindings = resolution.generic_simulation.netlist_parameter_bindings
    existing_oa_fields = {
        (binding.instance, binding.oa_parameter)
        for binding in existing_bindings
    }
    collisions = sorted(set(identities) & existing_oa_fields)
    if collisions:
        raise ValueError(
            "binding promotion refuses a field already mapped by onboarding intent: "
            + ", ".join(
                f"{instance}.{parameter}" for instance, parameter in collisions
            )
        )

    resolution_payload = resolution.model_dump(mode="json", exclude_none=True)
    resolution_payload["generic_simulation"]["netlist_parameter_bindings"].extend(
        item.binding.model_dump(mode="json", exclude_none=True)
        for item in promoted
    )
    resolved_intent = ExistingSchematicOnboardingResolution.model_validate(
        resolution_payload
    )
    task = resolve_onboarding_draft(draft_path, resolved_intent)
    if (
        task.safety.allow_remote_compute
        or task.safety.allow_remote_write
        or task.safety.replace_existing
    ):
        raise ValueError(
            "binding promotion compiler output unexpectedly enables execution"
        )

    task_bytes = _model_bytes(task)
    compilation = OnboardingBindingPromotionCompilation(
        onboarding_draft_sha256=_sha256(draft_bytes),
        onboarding_resolution_sha256=_sha256(resolution_bytes),
        promoted_bindings=promoted,
        compiled_task_id=task.id,
        compiled_task_sha256=_sha256(task_bytes),
        compiled_plan_token=build_plan(task).confirmation_token,
        evidence_sources={
            "onboarding_draft_source_oa": "bridge_readback",
            "onboarding_draft_compilation": "software_inference",
            "onboarding_resolution": "user_input",
            "binding_discovery_oa_cdf": "bridge_readback",
            "binding_discovery_si_netlists": "eda_result",
            "binding_discovery_remote_cleanup": "system_event",
            "binding_classification_and_task_compilation": "software_inference",
        },
    )
    return task, compilation


__all__ = [
    "OnboardingBindingPromotionCompilation",
    "OnboardingPromotedBindingEvidence",
    "compile_onboarding_with_discovered_bindings",
]
