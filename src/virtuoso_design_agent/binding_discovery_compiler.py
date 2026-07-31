"""Compile fresh Bridge inspection evidence into a safe binding probe task."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .design_context import DesignContext
from .generic_simulation import (
    GenericHierarchyBinding,
    ParameterBindingDiscoverySpec,
)
from .instance_path import INSTANCE_PATH_PATTERN, split_instance_path
from .models import DesignTarget, TaskSpec
from .onboarding import build_onboarding_draft
from .parameter_binding import canonical_parameter_table_sha256


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ParameterBindingDiscoveryIntent(_StrictModel):
    """Human/agent intent layered on one hash-bound read-only inspection."""

    schema_version: Literal[1] = 1
    task_id: StrictStr = Field(min_length=1, max_length=128)
    context_id: StrictStr = Field(min_length=1, max_length=128)
    instance: StrictStr = Field(pattern=INSTANCE_PATH_PATTERN)
    oa_parameter: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    probe_value: StrictStr = Field(min_length=1, max_length=1024)
    hierarchy_bindings: list[GenericHierarchyBinding] = Field(
        default_factory=list,
        max_length=32,
    )
    timeout_seconds: int = Field(default=600, ge=30, le=3600)
    evidence_source: Literal["user_input"] = "user_input"


class ParameterBindingDiscoveryCompilation(_StrictModel):
    """Hash handoff showing exactly which readback produced the probe task."""

    schema_version: Literal[1] = 1
    source_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    intent_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_task_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    oa_target: DesignTarget
    parameter_target: DesignTarget
    topology_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    placement_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    instance: StrictStr
    oa_parameter: StrictStr
    original_value: StrictStr
    probe_value: StrictStr
    complete_cdf_parameter_count: int = Field(ge=1)
    complete_cdf_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    execution_enabled: Literal[False] = False
    evidence_sources: dict[
        str,
        Literal["bridge_readback", "software_inference", "user_input"],
    ]


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def compile_parameter_binding_discovery_task(
    inspect_task: Path,
    inspect_run: Path,
    intent: ParameterBindingDiscoveryIntent,
    *,
    child_inspections: Sequence[tuple[str, Path, Path]] = (),
) -> tuple[TaskSpec, ParameterBindingDiscoveryCompilation]:
    """Build a non-executable task without guessing or dropping CDF fields."""

    draft = build_onboarding_draft(
        inspect_task,
        inspect_run,
        draft_id=f"{intent.task_id}-source",
        child_inspections=child_inspections,
    )
    inventory_matches = [
        item
        for item in draft.parameter_inventory
        if item.instance_path == intent.instance
    ]
    if len(inventory_matches) != 1:
        raise ValueError(
            "binding discovery target must appear exactly once in the fresh "
            f"parameter inventory: {intent.instance!r}"
        )
    inventory = inventory_matches[0]
    expected_parameters = {
        name: field.raw_value
        for name, field in sorted(inventory.fields.items())
    }
    if intent.oa_parameter not in expected_parameters:
        raise ValueError(
            "binding discovery field is absent from the complete fresh CDF "
            f"inventory: {intent.instance}.{intent.oa_parameter}"
        )

    discovery = ParameterBindingDiscoverySpec(
        instance=intent.instance,
        oa_parameter=intent.oa_parameter,
        probe_value=intent.probe_value,
        expected_instance_parameters=expected_parameters,
        hierarchy_bindings=intent.hierarchy_bindings,
    )
    top_instance = split_instance_path(intent.instance)[0] or intent.instance
    context_payload = draft.design_context_draft.model_dump(mode="json")
    context_payload.update(
        {
            "id": intent.context_id,
            "topology_origin": "existing_oa",
            "expected_topology_sha256": draft.topology_sha256,
            "roles": [
                {
                    "role": "device.binding_probe",
                    "instances": [top_instance],
                    "evidence_source": "user_input",
                }
            ],
            "instance_parameter_permissions": [
                {
                    "instance": intent.instance,
                    "parameters": [intent.oa_parameter],
                    "modes": ["fixed"],
                }
            ],
            "semantic_parameter_permissions": [],
            "required_analyses": [],
            "optional_analyses": [],
            "metrics": [],
        }
    )
    context = DesignContext.model_validate(context_payload)
    task = TaskSpec.model_validate(
        {
            "schema_version": 1,
            "id": intent.task_id,
            "operation": "parameters.binding.discover",
            "circuit": "existing_schematic",
            "target": draft.source.target.model_dump(mode="json"),
            "pdk_profile": draft.source.pdk_profile,
            "design_context": context.model_dump(mode="json"),
            "parameter_binding_discovery": discovery.model_dump(mode="json"),
            "limits": {
                "max_iterations": 1,
                "timeout_seconds": intent.timeout_seconds,
            },
            "safety": {
                "allow_remote_compute": False,
                "allow_remote_write": False,
                "allowed_library": draft.source.target.library,
                "required_cell_prefix": "vda_",
                "replace_existing": False,
            },
        }
    )
    intent_sha256 = _canonical_sha256(intent.model_dump(mode="json"))
    task_sha256 = _canonical_sha256(
        task.model_dump(mode="json", exclude_none=True)
    )
    compilation = ParameterBindingDiscoveryCompilation(
        source_task_sha256=draft.source.task_sha256,
        source_run_sha256=draft.source.run_sha256,
        intent_sha256=intent_sha256,
        compiled_task_sha256=task_sha256,
        oa_target=draft.source.target,
        parameter_target=inventory.target,
        topology_sha256=draft.topology_sha256,
        placement_sha256=draft.placement_sha256,
        instance=intent.instance,
        oa_parameter=intent.oa_parameter,
        original_value=expected_parameters[intent.oa_parameter],
        probe_value=intent.probe_value,
        complete_cdf_parameter_count=len(expected_parameters),
        complete_cdf_sha256=canonical_parameter_table_sha256(
            expected_parameters
        ),
        evidence_sources={
            "inspect_task": "user_input",
            "inspect_run": "bridge_readback",
            "field_selection_and_probe": "user_input",
            "compilation_and_hashes": "software_inference",
        },
    )
    return task, compilation
