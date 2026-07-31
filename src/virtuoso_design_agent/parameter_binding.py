"""Evidence classification and validation for reversible OA-CDF probes."""

from __future__ import annotations

import hashlib
import json
import posixpath
from pathlib import Path
from typing import Any

from .generic_simulation import ParameterBindingDiscoverySpec
from .spectre_values import spectre_values_equal


def canonical_parameter_table_sha256(values: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            {str(name): str(value) for name, value in sorted(values.items())},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _changed_parameter_values(
    before: dict[str, str],
    after: dict[str, str],
) -> list[dict[str, str | None]]:
    changes: list[dict[str, str | None]] = []
    for name in sorted(set(before) | set(after)):
        before_value = before.get(name)
        after_value = after.get(name)
        if (
            before_value is not None
            and after_value is not None
            and spectre_values_equal(before_value, after_value)
        ):
            continue
        changes.append(
            {
                "parameter": name,
                "before": before_value,
                "after": after_value,
            }
        )
    return changes


def classify_parameter_binding_probe(
    discovery: ParameterBindingDiscoverySpec,
    *,
    baseline_oa_parameters: dict[str, str],
    probe_oa_parameters: dict[str, str],
    baseline_netlist_inventory: dict[str, dict[str, Any]],
    probe_netlist_inventory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Promote only one globally unique, direct literal parameter change."""

    oa_changes = _changed_parameter_values(
        baseline_oa_parameters,
        probe_oa_parameters,
    )
    netlist_changes: list[dict[str, Any]] = []
    netlist_structure_changes: list[dict[str, Any]] = []
    for instance in sorted(
        set(baseline_netlist_inventory) | set(probe_netlist_inventory)
    ):
        before_instance = baseline_netlist_inventory.get(instance)
        after_instance = probe_netlist_inventory.get(instance)
        if before_instance is None or after_instance is None:
            netlist_structure_changes.append(
                {
                    "instance": instance,
                    "before": before_instance,
                    "after": after_instance,
                    "reason": "instance_set_changed",
                }
            )
            continue
        for field in ("model", "nodes"):
            if before_instance.get(field) != after_instance.get(field):
                netlist_structure_changes.append(
                    {
                        "instance": instance,
                        "field": field,
                        "before": before_instance.get(field),
                        "after": after_instance.get(field),
                        "reason": "instance_structure_changed",
                    }
                )
        before_parameters = before_instance.get("parameters") or {}
        after_parameters = after_instance.get("parameters") or {}
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            netlist_structure_changes.append(
                {
                    "instance": instance,
                    "reason": "parameter_inventory_unstructured",
                }
            )
            continue
        for change in _changed_parameter_values(
            {
                str(name): str(value)
                for name, value in before_parameters.items()
            },
            {
                str(name): str(value)
                for name, value in after_parameters.items()
            },
        ):
            netlist_changes.append({"instance": instance, **change})

    changed_oa = [str(item["parameter"]) for item in oa_changes]
    changed_netlist = [
        f"{item['instance']}.{item['parameter']}" for item in netlist_changes
    ]
    promoted_binding: dict[str, str] | None = None
    literal_identity_verified = False
    if netlist_structure_changes:
        status = "netlist_structure_changed"
    elif not netlist_changes:
        status = "inert"
    elif (
        len(netlist_changes) > 1
        or netlist_changes[0]["instance"] != discovery.instance
    ):
        status = "ambiguous_netlist_change"
    elif changed_oa != [discovery.oa_parameter]:
        status = "callback_coupled"
    else:
        netlist_parameter = str(netlist_changes[0]["parameter"])
        before_value = netlist_changes[0].get("before")
        after_value = netlist_changes[0].get("after")
        original = discovery.expected_instance_parameters[discovery.oa_parameter]
        literal_identity_verified = (
            before_value is not None
            and after_value is not None
            and spectre_values_equal(before_value, original)
            and spectre_values_equal(after_value, discovery.probe_value)
        )
        if literal_identity_verified:
            status = "direct_literal_binding"
            promoted_binding = {
                "instance": discovery.instance,
                "oa_parameter": discovery.oa_parameter,
                "netlist_parameter": netlist_parameter,
            }
        else:
            status = "single_netlist_parameter_nonliteral"
    return {
        "status": status,
        "changed_oa_parameters": changed_oa,
        "changed_netlist_parameters": changed_netlist,
        "oa_changes": oa_changes,
        "netlist_changes": netlist_changes,
        "netlist_structure_changes": netlist_structure_changes,
        "literal_identity_verified": literal_identity_verified,
        "promoted_binding": promoted_binding,
        "same_name_assumption_used": False,
        "source": "software_inference",
    }


def validate_parameter_binding_eda_stage(
    stage_name: str,
    stage: dict[str, Any],
) -> None:
    """Independently bind one real ``si`` stage to local bytes and cleanup."""

    if stage.get("source") != "eda_result":
        raise RuntimeError(
            f"binding discovery {stage_name} stage is not raw EDA evidence"
        )
    signature = stage.get("canonical_netlist_signature_sha256")
    if (
        not isinstance(signature, str)
        or len(signature) != 64
        or any(character not in "0123456789abcdef" for character in signature)
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} has an invalid canonical si signature"
        )

    raw_netlist = stage.get("raw_netlist")
    if (
        not isinstance(raw_netlist, dict)
        or raw_netlist.get("source") != "eda_result"
        or raw_netlist.get("remote_retained") is not False
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} lacks immutable raw si evidence"
        )
    raw_sha256 = raw_netlist.get("sha256")
    remote_netlist_path = raw_netlist.get("remote_path")
    if (
        not isinstance(raw_sha256, str)
        or len(raw_sha256) != 64
        or any(character not in "0123456789abcdef" for character in raw_sha256)
        or not isinstance(remote_netlist_path, str)
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} raw si identity is incomplete"
        )

    cleanup = stage.get("remote_cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("removed") is not True
        or cleanup.get("source") != "system_event"
        or not isinstance(cleanup.get("remote_path"), str)
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} lacks verified remote cleanup"
        )
    cleanup_path = posixpath.normpath(str(cleanup["remote_path"]).rstrip("/"))
    if (
        cleanup_path != cleanup["remote_path"]
        or not cleanup_path.startswith("/data/xum/")
        or not posixpath.basename(cleanup_path).startswith("vda_")
        or posixpath.dirname(remote_netlist_path) != cleanup_path
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} cleanup is not bound to its exact "
            "VDA si run directory"
        )

    bundle = stage.get("artifact_bundle")
    if not isinstance(bundle, dict) or bundle.get("source") != "eda_result":
        raise RuntimeError(
            f"binding discovery {stage_name} lacks a local artifact bundle"
        )
    directory_value = bundle.get("directory")
    manifest_value = bundle.get("manifest_path")
    manifest_sha256 = bundle.get("manifest_sha256")
    files = bundle.get("files")
    if (
        not isinstance(directory_value, str)
        or not isinstance(manifest_value, str)
        or not isinstance(manifest_sha256, str)
        or not isinstance(files, list)
        or len(files) != 2
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} artifact bundle is incomplete"
        )
    directory = Path(directory_value).resolve()
    manifest_path = Path(manifest_value).resolve()
    if manifest_path.parent != directory or not manifest_path.is_file():
        raise RuntimeError(
            f"binding discovery {stage_name} manifest is outside its stage directory"
        )
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise RuntimeError(
            f"binding discovery {stage_name} manifest SHA-256 does not match"
        )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"binding discovery {stage_name} manifest is not valid UTF-8 JSON"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("stage") != stage_name
        or manifest.get("files") != files
    ):
        raise RuntimeError(
            f"binding discovery {stage_name} manifest content is inconsistent"
        )

    expected_relative_paths = {
        f"{stage_name}/oa_netlist.scs",
        f"{stage_name}/si_batch_stdout.log",
    }
    actual_relative_paths: set[str] = set()
    netlist_sha256: str | None = None
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError(
                f"binding discovery {stage_name} manifest has a malformed entry"
            )
        path_value = item.get("path")
        relative_path = item.get("relative_path")
        if not isinstance(path_value, str) or not isinstance(relative_path, str):
            raise RuntimeError(
                f"binding discovery {stage_name} manifest path is incomplete"
            )
        path = Path(path_value).resolve()
        if path.parent != directory or not path.is_file():
            raise RuntimeError(
                f"binding discovery {stage_name} artifact escaped its stage directory"
            )
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if item.get("size_bytes") != len(content) or item.get("sha256") != digest:
            raise RuntimeError(
                f"binding discovery {stage_name} artifact bytes do not match manifest"
            )
        actual_relative_paths.add(relative_path)
        if relative_path == f"{stage_name}/oa_netlist.scs":
            netlist_sha256 = digest
    if actual_relative_paths != expected_relative_paths:
        raise RuntimeError(
            f"binding discovery {stage_name} artifact set is incomplete"
        )
    if netlist_sha256 != raw_sha256:
        raise RuntimeError(
            f"binding discovery {stage_name} raw si hash is not bound to local bytes"
        )
