"""Derive a bounded standalone MOS characterization task from real si evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .models import (
    DeviceCharacterizationSourceBinding,
    EvidenceSource,
    Operation,
    RunRecord,
    RunStatus,
    StrictModel,
    TaskSpec,
)
from .profiles import load_pdk_profile


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CharacterizationHoldoutBias(_FiniteStrictModel):
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(gt=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)


class DeviceCharacterizationGridTemplate(_FiniteStrictModel):
    """User-owned safe bias domain; geometry and si signature are filled later."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    vgs_magnitudes_v: list[float] = Field(min_length=1, max_length=16)
    vds_magnitudes_v: list[float] = Field(min_length=1, max_length=16)
    vsb_magnitudes_v: list[float] = Field(min_length=1, max_length=8)
    holdout_biases: list[CharacterizationHoldoutBias] = Field(
        default_factory=list,
        max_length=64,
    )
    temperature_c: float = Field(default=27.0, ge=-273.15, le=300.0)
    maximum_holdout_normalized_error: float = Field(
        default=0.25,
        gt=0.0,
        le=1.0,
    )
    timeout_seconds: int = Field(default=900, ge=1, le=7200)

    @model_validator(mode="after")
    def validate_grid(self) -> "DeviceCharacterizationGridTemplate":
        axes = {
            "vgs_magnitudes_v": self.vgs_magnitudes_v,
            "vds_magnitudes_v": self.vds_magnitudes_v,
            "vsb_magnitudes_v": self.vsb_magnitudes_v,
        }
        for name, values in axes.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{name} cannot contain duplicates")
        if any(value < 0.0 for value in self.vgs_magnitudes_v):
            raise ValueError("vgs_magnitudes_v cannot contain negative values")
        if any(value <= 0.0 for value in self.vds_magnitudes_v):
            raise ValueError("vds_magnitudes_v must contain positive values")
        if any(value < 0.0 for value in self.vsb_magnitudes_v):
            raise ValueError("vsb_magnitudes_v cannot contain negative values")
        if (
            len(self.vgs_magnitudes_v)
            * len(self.vds_magnitudes_v)
            * len(self.vsb_magnitudes_v)
            > 256
        ):
            raise ValueError("characterization grid exceeds 256 training points")
        for holdout in self.holdout_biases:
            key = (
                holdout.vgs_magnitude_v,
                holdout.vds_magnitude_v,
                holdout.vsb_magnitude_v,
            )
            if key in {
                (vgs, vds, vsb)
                for vgs in self.vgs_magnitudes_v
                for vds in self.vds_magnitudes_v
                for vsb in self.vsb_magnitudes_v
            }:
                raise ValueError("holdout bias duplicates a training point")
        return self


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _selected_result(
    details: dict[str, Any], operating_condition_name: str | None
) -> dict[str, Any]:
    rows = details.get("operating_condition_results")
    if rows is None:
        if operating_condition_name is not None:
            raise ValueError("source action has no operating-condition results")
        return details
    if not isinstance(rows, list):
        raise ValueError("source operating-condition results must be a list")
    if operating_condition_name is None:
        if len(rows) != 1:
            raise ValueError(
                "source run has multiple operating conditions; select one explicitly"
            )
        selected = rows[0]
    else:
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("condition"), dict)
            and row["condition"].get("name") == operating_condition_name
        ]
        if len(matches) != 1:
            raise ValueError("source run does not contain exactly one selected condition")
        selected = matches[0]
    if not isinstance(selected, dict) or not isinstance(selected.get("result"), dict):
        raise ValueError("selected operating condition has no result")
    return selected["result"]


def derive_device_characterization_task(
    template: DeviceCharacterizationGridTemplate,
    circuit_run_path: Path,
    *,
    task_id: str,
    instance_name: str,
    polarity: Literal["nmos", "pmos"],
    source_action: str = "simulation.candidate.1",
    operating_condition_name: str | None = None,
) -> TaskSpec:
    """Build one safe characterization task without hand-copying si parameters."""

    try:
        run_bytes = circuit_run_path.read_bytes()
        record = RunRecord.model_validate_json(run_bytes)
    except OSError as exc:
        raise ValueError(f"cannot read source circuit run {circuit_run_path}") from exc
    if record.adapter != "virtuoso-bridge-subprocess":
        raise ValueError("characterization task derivation requires a real Bridge run")
    if record.status is not RunStatus.SUCCEEDED:
        raise ValueError("source circuit run did not succeed")
    forbidden_write_actions = {
        "schematic.create",
        "schematic.transform",
        "parameters.apply",
        "parameters.restore",
    }
    if any(item.action in forbidden_write_actions for item in record.actions):
        raise ValueError("source circuit run contains an OA write action")
    allowed_actions = {"bridge.probe", "schematic.inspect.before", source_action}
    unexpected_actions = [
        item.action for item in record.actions if item.action not in allowed_actions
    ]
    if unexpected_actions:
        raise ValueError(
            "source circuit run is not a dedicated read-only simulation: "
            f"unexpected actions {unexpected_actions}"
        )
    actions = [item for item in record.actions if item.action == source_action]
    if len(actions) != 1:
        raise ValueError("source run must contain exactly one selected simulation action")
    action = actions[0]
    if (
        action.status != "succeeded"
        or action.evidence_source is not EvidenceSource.EDA_RESULT
    ):
        raise ValueError("source simulation action is not successful eda_result evidence")
    details = _selected_result(action.details, operating_condition_name)
    if details.get("analysis_complete") is not True or details.get("analysis_issues"):
        raise ValueError("source simulation analysis is incomplete")
    if not str(details.get("tool_version", "")).strip():
        raise ValueError("source simulation has no Spectre tool version")
    evidence = _mapping(details.get("evidence"), "source simulation evidence")
    side_effects = _mapping(evidence.get("side_effects"), "source side effects")
    if side_effects != {
        "oa_access_performed": True,
        "oa_write_performed": False,
        "remote_compute_performed": True,
    }:
        raise ValueError("source simulation evidence is not read-only OA + compute")
    netlist = _mapping(evidence.get("netlist"), "source si netlist")
    if (
        netlist.get("source") != "eda_result"
        or netlist.get("parameter_consistency") != "matched"
    ):
        raise ValueError("source si netlist is not matched eda_result evidence")
    instances = _mapping(netlist.get("instances"), "source si instances")
    instance = _mapping(instances.get(instance_name), f"source instance {instance_name}")
    if instance.get("model") == "resistor":
        raise ValueError("source instance must be a MOS device")
    unparsed = instance.get("unparsed_model_parameter_tokens", [])
    if not isinstance(unparsed, list) or any(not isinstance(item, str) for item in unparsed):
        raise ValueError("source instance has invalid unparsed-parameter evidence")
    if unparsed:
        raise ValueError("source instance model parameter signature is not fully parsed")
    model_parameters = _mapping(
        instance.get("model_parameters"),
        "source instance model parameters",
    )
    if any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in model_parameters.items()
    ):
        raise ValueError("source instance model parameters must be string literals")
    width_um = _finite(
        instance.get("netlist_width_um", instance.get("width_um")),
        "source instance width",
    )
    length_um = _finite(instance.get("length_um"), "source instance length")
    fingers = _finite(instance.get("fingers", 1.0), "source instance fingers")
    multiplicity = _finite(
        instance.get("multiplicity", 1.0),
        "source instance multiplicity",
    )
    if not math.isclose(fingers, 1.0) or not math.isclose(multiplicity, 1.0):
        raise ValueError(
            "automatic characterization task derivation currently requires "
            "fingers=1 and multiplicity=1"
        )
    testbench = _mapping(evidence.get("testbench"), "source testbench")
    model_configuration = _mapping(
        testbench.get("model_configuration"),
        "source model configuration",
    )
    profile = load_pdk_profile(template.pdk_profile)
    if model_configuration.get("profile") != profile.name:
        raise ValueError("source PDK profile does not match the grid template")
    if model_configuration.get("process_corner") != profile.model_section:
        raise ValueError("source process corner does not match the profile default")
    temperature_c = _finite(
        model_configuration.get("temperature_c"),
        "source temperature",
    )
    if not math.isclose(
        temperature_c,
        template.temperature_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("source temperature does not match the grid template")
    model = str(instance.get("model", ""))
    expected_model = profile.nmos_cell if polarity == "nmos" else profile.pmos_cell
    if model != expected_model:
        raise ValueError(
            f"source model {model!r} does not match profile {polarity} model "
            f"{expected_model!r}"
        )
    netlist_hash = str(netlist.get("sha256", ""))
    if len(netlist_hash) != 64 or any(ch not in "0123456789abcdef" for ch in netlist_hash):
        raise ValueError("source si netlist has no valid SHA-256")
    topology_variant = str(netlist.get("topology_variant", ""))
    if not topology_variant:
        raise ValueError("source si netlist has no topology variant")
    binding = DeviceCharacterizationSourceBinding(
        source_run_sha256=hashlib.sha256(run_bytes).hexdigest(),
        source_task_id=record.task_id,
        source_action=source_action,
        source_instance=instance_name,
        source_netlist_sha256=netlist_hash,
        source_pdk_profile=profile.name,
        source_process_corner=profile.model_section,
        source_temperature_c=temperature_c,
        source_topology_variant=topology_variant,
        source_model=model,
        source_width_um=width_um,
        source_length_um=length_um,
        source_model_parameter_count=len(model_parameters),
        source_model_parameters_sha256=_canonical_sha256(model_parameters),
    )
    task = TaskSpec.model_validate(
        {
            "schema_version": 1,
            "id": task_id,
            "operation": Operation.DEVICE_CHARACTERIZE.value,
            "circuit": "mos_device",
            "pdk_profile": profile.name,
            "device_characterization": {
                "polarities": [polarity],
                "width_um": width_um,
                "model_parameters_by_polarity": {polarity: model_parameters},
                "source_instance_binding": binding.model_dump(mode="json"),
                "lengths_um": [length_um],
                "vgs_magnitudes_v": template.vgs_magnitudes_v,
                "vds_magnitudes_v": template.vds_magnitudes_v,
                "vsb_magnitudes_v": template.vsb_magnitudes_v,
                "holdout_points": [
                    {
                        "polarity": polarity,
                        "length_um": length_um,
                        **holdout.model_dump(mode="json"),
                    }
                    for holdout in template.holdout_biases
                ],
                "temperature_c": template.temperature_c,
                "maximum_holdout_normalized_error": (
                    template.maximum_holdout_normalized_error
                ),
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": False,
                "replace_existing": False,
            },
            "limits": {
                "max_iterations": 1,
                "timeout_seconds": template.timeout_seconds,
            },
        }
    )
    return task
