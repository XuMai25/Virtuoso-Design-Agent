"""Worker executed by the virtuoso-bridge Python environment.

The process reads one JSON request from stdin and emits one marker-prefixed JSON
result. It intentionally keeps Bridge imports out of the main VDA environment.
"""

from __future__ import annotations

import _thread
import base64
import csv
import hashlib
import ipaddress
import io
import json
import math
import os
import posixpath
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from virtuoso_design_agent.adapters.base import merge_analysis_bundle
from virtuoso_design_agent.characterization import (
    MOS_CHARGE_DERIVATIVE_NAMES,
    enumerate_mos_characterization_points,
)
from virtuoso_design_agent.design_context import (
    DesignContext,
    HierarchyParameterScope,
    audit_design_context,
)
from virtuoso_design_agent.generic_simulation import (
    GenericHierarchyBinding,
    GenericNetlistParameterBinding,
    GenericOaSimulationSpec,
    GenericVoltageExpression,
    ParameterBindingDiscoverySpec,
    render_generic_oa_testbench,
)
from virtuoso_design_agent.instance_path import split_instance_path
from virtuoso_design_agent.models import (
    AnalysisStageSpec,
    DeviceCharacterizationSpec,
    MetricConstraint,
)
from virtuoso_design_agent.netlist_preview import (
    NetlistPreviewSpec,
    NetlistPreviewVariant,
    render_spectre_preview_deck,
)
from virtuoso_design_agent.parameter_binding import (
    canonical_parameter_table_sha256,
    classify_parameter_binding_probe,
)
from virtuoso_design_agent.metrics import (
    aggregate_common_source_linearity_metrics,
    aggregate_differential_pair_linearity_metrics,
    extract_common_source_ac_metrics,
    extract_common_source_dc_metrics,
    extract_common_source_linearity_point_metrics,
    extract_common_source_noise_metrics,
    extract_dc_supply_metrics,
    extract_differential_pair_ac_metrics,
    extract_differential_pair_cmrr_response_metrics,
    extract_differential_pair_common_mode_ac_metrics,
    extract_differential_pair_dc_metrics,
    extract_differential_pair_psrr_metrics,
    extract_inverter_metrics,
    extract_supply_metrics,
    evaluate_constraints,
)
from virtuoso_design_agent.spectre_values import spectre_values_equal
from virtuoso_design_agent.calculator_expressions import calculator_expressions_equal
from virtuoso_design_agent.topology_delta import (
    AddInstanceOperation,
    AddNetOperation,
    AddPinOperation,
    MasterParameterMigration,
    ReconnectTerminalOperation,
    RemoveInstanceOperation,
    RemoveNetOperation,
    RemovePinOperation,
    ReplaceMasterOperation,
    TopologyDeltaExecutionSpec,
    TopologyInstance,
    TopologySnapshot,
    apply_topology_operations,
    apply_topology_delta_execution,
    invert_topology_operations,
    snapshot_from_inspection,
    topology_fingerprint,
    validate_topology_execution_readback,
)

_MARKER = "VDA_RESULT="
_WORKER_RESOURCES: list[Any] = []
_WORKER_RESOURCE_TRACKING = False
_SPECTRE_REMOTE_CLEANUP_GRACE_SECONDS = 15
_SPECTRE_REMOTE_KILL_AFTER_SECONDS = 10
_WORKER_PARENT_POLL_SECONDS = 0.2

_DIFFERENTIAL_PAIR_BASE_VARIANT = "resistive_load_nmos_differential_pair"
_DIFFERENTIAL_PAIR_TAIL_VARIANT = (
    "resistive_load_nmos_differential_pair_with_tail_device"
)
_DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT = (
    "resistive_load_nmos_differential_pair_with_tail_device_and_source_degeneration"
)
_DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT = (
    "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
)
_DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT = (
    "pmos_current_mirror_load_nmos_differential_pair_with_tail_device_"
    "and_source_degeneration"
)
_COMMON_SOURCE_CASCODE_VARIANT = "cascode_common_source"


def _differential_pair_has_real_tail(topology_variant: str) -> bool:
    return topology_variant in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT,
    }


def _differential_pair_has_source_degeneration(topology_variant: str) -> bool:
    return topology_variant in {
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT,
    }


def _differential_pair_has_current_mirror_load(topology_variant: str) -> bool:
    return topology_variant in {
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT,
    }


def _register_worker_resource(resource: Any) -> Any:
    if _WORKER_RESOURCE_TRACKING:
        _WORKER_RESOURCES.append(resource)
    return resource


def _close_worker_resources() -> list[str]:
    errors: list[str] = []
    while _WORKER_RESOURCES:
        resource = _WORKER_RESOURCES.pop()
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as exc:
            errors.append(f"{type(resource).__name__}: {type(exc).__name__}: {exc}")
    return errors


def _worker_parent_is_alive(parent_pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def _start_worker_parent_watchdog() -> tuple[threading.Event, threading.Thread] | None:
    """Interrupt the main thread if its caller cancels or disappears."""

    raw_parent_pid = os.getenv("VDA_WORKER_PARENT_PID")
    raw_cancel_file = os.getenv("VDA_WORKER_CANCEL_FILE")
    if not raw_parent_pid or not raw_cancel_file:
        return None
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError:
        return None
    cancel_file = Path(raw_cancel_file)
    stopped = threading.Event()

    def watch_parent() -> None:
        while not stopped.wait(_WORKER_PARENT_POLL_SECONDS):
            if cancel_file.is_file() or not _worker_parent_is_alive(parent_pid):
                _thread.interrupt_main()
                return

    watcher = threading.Thread(
        target=watch_parent,
        name="vda-worker-parent-watchdog",
        daemon=True,
    )
    watcher.start()
    return stopped, watcher


def _require_loopback_bridge_daemon(client: Any, *, timeout: int = 5) -> str:
    """Fail closed unless RAMIC reports an actual loopback bind address."""

    result = client.execute_skill(
        "if(boundp('RBLastBind) RBLastBind \"\")",
        timeout=timeout,
    )
    errors = [str(error) for error in (getattr(result, "errors", None) or [])]
    if errors:
        raise RuntimeError(
            "VDA could not verify the RAMIC daemon bind address: " + "; ".join(errors)
        )
    daemon_bind = str(getattr(result, "output", "") or "").strip()
    if daemon_bind.lower() == "nil":
        daemon_bind = ""
    if len(daemon_bind) >= 2 and daemon_bind[0] == '"' and daemon_bind[-1] == '"':
        daemon_bind = daemon_bind[1:-1]
    if not daemon_bind:
        raise RuntimeError(
            "VDA refuses remote OA/compute because the RAMIC daemon did not "
            "report its actual bind address"
        )
    host = daemon_bind.rsplit(":", 1)[0].strip("[]")
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise RuntimeError(
            "VDA refuses remote OA/compute because the RAMIC daemon is listening "
            f"on non-loopback address {daemon_bind!r}; stop it and reload the "
            "generated virtuoso_setup.il"
        )
    return daemon_bind


def _client():
    from virtuoso_bridge import VirtuosoClient

    client = _register_worker_resource(VirtuosoClient.from_env())
    ssh_runner = getattr(client, "ssh_runner", None)
    if ssh_runner is not None:
        # Required by the verified Windows + nics4304 setup.
        ssh_runner._persistent_shell_enabled = False
    daemon_bind = _require_loopback_bridge_daemon(client)
    setattr(client, "_vda_daemon_bind", daemon_bind)
    return client


def _target(payload: dict[str, Any]) -> tuple[str, str]:
    target = payload["target"]
    return str(target["library"]), str(target["cell"])


def _read_schematic(client, library: str, cell: str) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.reader import read_schematic

    return read_schematic(
        client,
        library,
        cell,
        include_positions=True,
        param_filters=None,
        timeout=90,
    )


def _placement_snapshot_from_readback(
    placement: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, list[Any]] = {}
    for field in ("instances", "pins", "labels", "wires"):
        values = placement.get(field, [])
        if not isinstance(values, list):
            raise RuntimeError(f"invalid schematic placement field: {field}")
        canonical[field] = sorted(
            values,
            key=lambda value: json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "counts": {field: len(values) for field, values in canonical.items()},
        "label_texts": sorted(
            str(item.get("text"))
            for item in canonical["labels"]
            if isinstance(item, dict)
        ),
        "canonical_geometry": canonical,
    }


def _schematic_placement_snapshot(
    client, library: str, cell: str
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.reader import read_placement

    return _placement_snapshot_from_readback(
        read_placement(client, library, cell)
    )


def _parse_skill_point(value: str, *, context: str) -> list[float]:
    match = re.fullmatch(
        r"\(\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)",
        value.strip(),
    )
    if match is None:
        raise RuntimeError(f"invalid {context} point: {value!r}")
    point = [float(match.group(1)), float(match.group(2))]
    if any(not math.isfinite(item) for item in point):
        raise RuntimeError(f"non-finite {context} point: {value!r}")
    return point


def _parse_schematic_pin_geometry(raw: str) -> dict[str, dict[str, Any]]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines or lines[0] != "PINS" or lines[-1] != "END":
        raise RuntimeError("pin geometry readback has invalid section framing")
    pins: dict[str, dict[str, Any]] = {}
    for line in lines[1:-1]:
        parts = line.split("|")
        if len(parts) != 9 or parts[0] != "PIN":
            raise RuntimeError(f"invalid pin geometry record: {line!r}")
        _, name, direction, raw_bits, library, cell, view, raw_xy, orient = parts
        if name in pins:
            raise RuntimeError(f"pin geometry readback repeated {name!r}")
        try:
            num_bits = int(raw_bits)
        except ValueError as exc:
            raise RuntimeError(
                f"pin geometry readback has invalid numBits for {name!r}"
            ) from exc
        pins[name] = {
            "direction": direction,
            "numBits": num_bits,
            "master": {"library": library, "cell": cell, "view": view},
            "xy": _parse_skill_point(raw_xy, context=f"pin {name!r}"),
            "orient": orient,
        }
    return pins


def _read_schematic_pin_geometry(
    client, library: str, cell: str
) -> dict[str, dict[str, Any]]:
    """Read each logical terminal's one physical pin figure without Bridge edits."""

    from virtuoso_bridge import decode_skill_output

    escaped_library = _skill_string(library)
    escaped_cell = _skill_string(cell)
    skill = " ".join(
        [
            "let((rbCv rbResult rbPin rbFig)",
            "rbCv = dbOpenCellViewByType("
            f'"{escaped_library}" "{escaped_cell}" '
            '"schematic" "schematic" "r")',
            'unless(rbCv error("target schematic missing during pin geometry readback"))',
            'rbResult = "PINS\\n"',
            "foreach(rbTerm rbCv~>terminals",
            'unless(length(rbTerm~>pins) == 1 error("logical terminal pin selection was not unique"))',
            "rbPin = car(rbTerm~>pins)",
            'unless(length(rbPin~>figs) == 1 error("logical terminal figure selection was not unique"))',
            "rbFig = car(rbPin~>figs)",
            "unless(rbFig~>objType == \"inst\" && rbFig~>purpose == \"pin\" "
            'error("logical terminal figure is not one schematic pin instance"))',
            'rbResult = strcat(rbResult sprintf(nil "PIN|%s|%s|%d|%s|%s|%s|%L|%s\\n" '
            'rbTerm~>name if(rbTerm~>direction rbTerm~>direction "inputOutput") '
            "if(rbTerm~>numBits rbTerm~>numBits 1) rbFig~>libName rbFig~>cellName "
            'if(rbFig~>viewName rbFig~>viewName "symbol") rbFig~>xy '
            'if(rbFig~>orient rbFig~>orient "R0")))',
            ")",
            'strcat(rbResult "END\\n"))',
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"pin geometry readback failed: {errors[0]}")
    return _parse_schematic_pin_geometry(
        decode_skill_output(str(getattr(result, "output", "")))
    )


def _assert_pin_geometry_matches_placement(
    pin_geometry: dict[str, dict[str, Any]],
    placement: dict[str, Any],
) -> None:
    instances = placement.get("canonical_geometry", {}).get("instances", [])
    if not isinstance(instances, list):
        raise RuntimeError("placement snapshot is missing canonical instances")
    expected_pin_cells = {"ipin", "opin", "iopin"}
    physical_pin_instances = [
        item
        for item in instances
        if isinstance(item, dict)
        and item.get("lib") == "basic"
        and item.get("cell") in expected_pin_cells
    ]
    if len(physical_pin_instances) != len(pin_geometry):
        raise RuntimeError(
            "logical pin geometry count does not match placement pin instances"
        )
    for name, geometry in pin_geometry.items():
        master = geometry["master"]
        matches = [
            item
            for item in physical_pin_instances
            if item.get("lib") == master["library"]
            and item.get("cell") == master["cell"]
            and item.get("orient") == geometry["orient"]
            and _parse_skill_point(
                str(item.get("xy")), context=f"placement pin {name!r}"
            )
            == geometry["xy"]
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"pin {name!r} geometry does not select one placement instance"
            )


def _placement_pin_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("lib"),
        item.get("cell"),
        item.get("orient"),
        tuple(
            _parse_skill_point(
                str(item.get("xy")), context="placement physical pin"
            )
        ),
    )


def _bind_logical_pin_names_to_placement(
    pin_geometry: dict[str, dict[str, Any]],
    placement: dict[str, Any],
) -> dict[str, Any]:
    """Add a stable placement hash that ignores OA's auto pin-figure names.

    Ordinary instance names remain part of the fingerprint.  Only physical
    basic/ipin, opin, and iopin figures are rebound to the independently read
    logical pin name after exact master/orientation/coordinate matching.
    """

    _assert_pin_geometry_matches_placement(pin_geometry, placement)
    canonical = placement.get("canonical_geometry", {})
    instances = canonical.get("instances", [])
    signature_to_pin: dict[tuple[Any, ...], str] = {}
    for name, geometry in sorted(pin_geometry.items()):
        signature = (
            geometry["master"]["library"],
            geometry["master"]["cell"],
            geometry["orient"],
            tuple(geometry["xy"]),
        )
        if signature in signature_to_pin:
            raise RuntimeError(
                "logical pins do not have unique physical placement signatures"
            )
        signature_to_pin[signature] = name

    bindings: list[dict[str, str]] = []
    rebound_instances: list[dict[str, Any]] = []
    for item in instances:
        if not isinstance(item, dict):
            raise RuntimeError("placement canonical instance is not an object")
        rebound = dict(item)
        if item.get("lib") == "basic" and item.get("cell") in {
            "ipin",
            "opin",
            "iopin",
        }:
            signature = _placement_pin_signature(item)
            logical_name = signature_to_pin.get(signature)
            if logical_name is None:
                raise RuntimeError(
                    "physical pin figure has no independently read logical pin binding"
                )
            physical_name = str(item.get("name"))
            rebound["name"] = f"logical-pin:{logical_name}"
            bindings.append(
                {
                    "logical_pin": logical_name,
                    "physical_oa_name": physical_name,
                }
            )
        rebound_instances.append(rebound)

    rebound_canonical = {
        field: sorted(
            rebound_instances if field == "instances" else list(canonical[field]),
            key=lambda value: json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
        for field in ("instances", "pins", "labels", "wires")
    }
    encoded = json.dumps(
        rebound_canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **placement,
        "logical_pin_bound_sha256": hashlib.sha256(encoded).hexdigest(),
        "logical_pin_bindings": sorted(
            bindings, key=lambda item: item["logical_pin"]
        ),
    }


def _placement_fingerprint_match_mode(
    expected_sha256: str,
    placement: dict[str, Any],
) -> str | None:
    if placement.get("sha256") == expected_sha256:
        return "exact_oa_names"
    if placement.get("logical_pin_bound_sha256") == expected_sha256:
        return "logical_pin_bound"
    return None


def _topology_pin_signature(pin: Any) -> tuple[Any, ...]:
    x, y, orient, library, cell, _view = _generic_pin_geometry(pin)
    return (library, cell, orient, (x, y))


def _unmatched_placement_pin_instances(
    pin_geometry: dict[str, dict[str, Any]],
    placement: dict[str, Any],
) -> list[dict[str, Any]]:
    instances = placement.get("canonical_geometry", {}).get("instances", [])
    if not isinstance(instances, list):
        raise RuntimeError("placement snapshot is missing canonical instances")
    physical = [
        item
        for item in instances
        if isinstance(item, dict)
        and item.get("lib") == "basic"
        and item.get("cell") in {"ipin", "opin", "iopin"}
    ]
    unmatched = list(physical)
    for name, geometry in sorted(pin_geometry.items()):
        expected = (
            geometry["master"]["library"],
            geometry["master"]["cell"],
            geometry["orient"],
            tuple(geometry["xy"]),
        )
        matches = [
            item for item in unmatched if _placement_pin_signature(item) == expected
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"logical pin {name!r} does not select one physical placement pin"
            )
        unmatched.remove(matches[0])
    return unmatched


def _strip_verified_orphan_pin_instances(
    schematic: dict[str, Any],
    orphan_instances: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exclude only placement-proven orphan pin figures from graph projection."""

    raw_instances = list(schematic.get("instances", []))
    stripped_names: set[str] = set()
    for orphan in orphan_instances:
        orphan_name = str(orphan.get("name"))
        orphan_signature = _placement_pin_signature(orphan)
        matches = [
            item
            for item in raw_instances
            if str(item.get("name")) == orphan_name
            and (
                item.get("lib"),
                item.get("cell"),
                item.get("orient"),
                tuple(float(value) for value in item.get("xy", [])),
            )
            == orphan_signature
            and item.get("view") == "symbol"
            and dict(item.get("terms", {})) == {}
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "orphan placement pin did not select one terminal-free raw "
                f"instance: {orphan_name!r}"
            )
        stripped_names.add(orphan_name)
    if len(stripped_names) != len(orphan_instances):
        raise RuntimeError("orphan placement pin instance names were not unique")
    return {
        **schematic,
        "instances": [
            item
            for item in raw_instances
            if str(item.get("name")) not in stripped_names
        ],
    }


def _schematic_geometry_bundle(
    client, library: str, cell: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    placement = _schematic_placement_snapshot(client, library, cell)
    pin_geometry = _read_schematic_pin_geometry(client, library, cell)
    placement = _bind_logical_pin_names_to_placement(pin_geometry, placement)
    return pin_geometry, placement


def _cellview_exists(client, library: str, cell: str, view: str) -> bool:
    result = client.execute_skill(
        f'let((v) v=ddGetObj("{library}" "{cell}" "{view}") if(v t nil))',
        timeout=15,
    )
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"{view} existence check failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output not in {"t", "nil"}:
        raise RuntimeError(f"unexpected {view} existence result: {output!r}")
    return output == "t"


def _focus_target_schematic(client, library: str, cell: str) -> None:
    opened = client.open_window(library, cell, view="schematic")
    open_errors = getattr(opened, "errors", None) or []
    if open_errors:
        raise RuntimeError(f"target schematic open failed: {open_errors[0]}")
    result = client.execute_skill(
        "let((cv) "
        "hiSetCurrentWindow(window) "
        "cv = geGetEditCellView() "
        f'if(cv && cv~>libName == "{library}" '
        f'&& cv~>cellName == "{cell}" '
        '&& cv~>viewName == "schematic" t nil))',
        timeout=15,
    )
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"target schematic focus failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            "Bridge active schematic does not match the requested parameter target"
        )


def _set_target_instance_params(
    client,
    library: str,
    cell: str,
    instance: str,
    **parameters: Any,
) -> dict[str, str]:
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params

    _focus_target_schematic(client, library, cell)
    return set_instance_params(client, instance, **parameters)


def _schematic_exists(client, library: str, cell: str) -> bool:
    return _cellview_exists(client, library, cell, "schematic")


def _try_read_schematic(client, library: str, cell: str) -> dict[str, Any] | None:
    if not _schematic_exists(client, library, cell):
        return None
    return _read_schematic(client, library, cell)


def _instance_parameters_from_schematic(
    data: dict[str, Any],
) -> dict[str, dict[str, str]]:
    return {
        str(item.get("name")): {
            str(name): str(value)
            for name, value in sorted(item.get("params", {}).items())
        }
        for item in sorted(data.get("instances", []), key=lambda item: str(item.get("name")))
    }


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    useful_params = {"Wfg", "w", "l", "fingers", "nf", "m", "model", "multi"}
    instances = []
    for item in data.get("instances", []):
        parameters = {
            key: value
            for key, value in item.get("params", {}).items()
            if key in useful_params
        }
        instances.append(
            {
                "name": item.get("name"),
                "library": item.get("lib"),
                "cell": item.get("cell"),
                "parameters": parameters,
                "terminals": item.get("terms", {}),
            }
        )
    return {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "semantic_parameters": _semantic_parameters_from_schematic(data),
        "bridge_schematic": data,
    }


def _existing_schematic_summary(
    data: dict[str, Any],
    *,
    pin_geometry: dict[str, dict[str, Any]] | None = None,
    placement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instances = [
        {
            "name": item.get("name"),
            "library": item.get("lib"),
            "cell": item.get("cell"),
            "parameters": dict(item.get("params", {})),
            "terminals": dict(item.get("terms", {})),
        }
        for item in data.get("instances", [])
    ]
    summary = {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "topology": _generic_topology_readback(data, pin_geometry=pin_geometry),
        "bridge_schematic": data,
    }
    if placement is not None:
        summary["placement"] = placement
    return summary


def _generic_topology_readback(
    data: dict[str, Any],
    *,
    pin_geometry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project Bridge readback onto the writable generic-topology contract."""

    instances = []
    for item in data.get("instances", []):
        instances.append(
            {
                "name": item.get("name"),
                "library": item.get("lib"),
                "cell": item.get("cell"),
                "view": item.get("view"),
                "terminals": dict(item.get("terms", {})),
                "xy": item.get("xy"),
                "orient": item.get("orient"),
                "numInst": item.get("numInst", 1),
            }
        )
    nets = []
    for name, item in data.get("nets", {}).items():
        attributes = {
            str(key): value
            for key, value in dict(item or {}).items()
            if key != "connections"
        }
        nets.append({"name": str(name), **attributes})
    logical_pin_names = {str(name) for name in data.get("pins", {})}
    if pin_geometry is not None and set(pin_geometry) != logical_pin_names:
        raise RuntimeError(
            "logical pin and physical pin geometry names do not match: "
            f"logical={sorted(logical_pin_names)}, "
            f"physical={sorted(pin_geometry)}"
        )
    pins = []
    for name, item in data.get("pins", {}).items():
        attributes = dict(item or {})
        direction = attributes.pop("direction", None)
        geometry = pin_geometry.get(str(name)) if pin_geometry is not None else None
        if geometry is not None:
            if geometry["direction"] != direction:
                raise RuntimeError(
                    f"logical and physical direction differ for pin {name!r}"
                )
            logical_num_bits = int(attributes.get("numBits", 1))
            if geometry["numBits"] != logical_num_bits:
                raise RuntimeError(
                    f"logical and physical numBits differ for pin {name!r}"
                )
            attributes.update(
                {
                    "master": geometry["master"],
                    "xy": geometry["xy"],
                    "orient": geometry["orient"],
                }
            )
        pins.append(
            {
                "name": str(name),
                "net": str(name),
                "direction": direction,
                **attributes,
            }
        )
    return {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(nets, key=lambda item: item["name"]),
        "pins": sorted(pins, key=lambda item: item["name"]),
    }


def _assert_inverter(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> str:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    names = set(by_name)
    core_names = {"MN0", "MP0"}
    testbench_names = core_names | {"VDD0", "VIN0", "CL0", "GND0"}
    if names == core_names:
        variant = "inverter_core"
    elif names == testbench_names:
        variant = "inverter_testbench"
    else:
        raise RuntimeError(
            f"existing schematic is not the VDA inverter: instances={sorted(names)}"
        )
    pins = set(data.get("pins", {}).keys())
    missing_pins = {"IN", "OUT", "VDD", "VSS"} - pins
    if missing_pins:
        raise RuntimeError(
            f"existing schematic is not the VDA inverter: missing pins={sorted(missing_pins)}"
        )
    missing_nets = {"IN", "OUT", "VDD", "VSS"} - set(data.get("nets", {}).keys())
    if missing_nets:
        raise RuntimeError(
            f"existing schematic is not the VDA inverter: missing nets={sorted(missing_nets)}"
        )
    expected_terminals = {
        "MP0": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
        "MN0": {
            "D": "OUT",
            "G": "IN",
            "S": "gnd!" if variant == "inverter_testbench" else "VSS",
            "B": "gnd!" if variant == "inverter_testbench" else "VSS",
        },
    }
    if variant == "inverter_testbench":
        expected_terminals.update(
            {
                "VDD0": {"PLUS": "VDD", "MINUS": "gnd!"},
                "VIN0": {"PLUS": "IN", "MINUS": "gnd!"},
                "CL0": {"PLUS": "OUT", "MINUS": "gnd!"},
                "GND0": {"gnd!": "gnd!"},
            }
        )
        expected_analog_masters = {
            "VDD0": ("analogLib", "vdc"),
            "VIN0": ("analogLib", "vpulse"),
            "CL0": ("analogLib", "cap"),
            "GND0": ("analogLib", "gnd"),
        }
        for name, expected in expected_analog_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    f"existing inverter testbench has {name} master {actual!r}, "
                    f"expected {expected!r}"
                )
    for name, expected in expected_terminals.items():
        if by_name[name].get("terms") != expected:
            raise RuntimeError(
                f"existing VDA inverter has unexpected {name} terminals: "
                f"{by_name[name].get('terms')!r}"
            )
    if profile is not None:
        expected_masters = {
            "MN0": (profile["tech_library"], profile["nmos_cell"]),
            "MP0": (profile["tech_library"], profile["pmos_cell"]),
        }
        for name, expected in expected_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    f"existing schematic is not the VDA inverter: {name} master "
                    f"is {actual!r}, expected {expected!r}"
                )
    return variant


def _assert_common_source(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> str:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    names = set(by_name)
    base_names = {"MN0", "RD0"}
    degenerated_names = base_names | {"RS0"}
    cascode_names = base_names | {"MNCAS"}
    if names == base_names:
        variant = "common_source"
    elif names == degenerated_names:
        variant = "source_degenerated_common_source"
    elif names == cascode_names:
        variant = _COMMON_SOURCE_CASCODE_VARIANT
    else:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"instances={sorted(names)}"
        )
    required_pins = {"IN", "OUT", "VDD", "VSS"}
    required_nets = set(required_pins)
    if variant == "source_degenerated_common_source":
        required_nets.add("NSRC")
    elif variant == _COMMON_SOURCE_CASCODE_VARIANT:
        required_pins.add("VCAS")
        required_nets.update({"VCAS", "NCAS"})
    missing_pins = required_pins - set(data.get("pins", {}).keys())
    if missing_pins:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"missing pins={sorted(missing_pins)}"
        )
    missing_nets = required_nets - set(data.get("nets", {}).keys())
    if missing_nets:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"missing nets={sorted(missing_nets)}"
        )
    expected_terminals = {
        "MN0": {
            "D": "NCAS" if variant == _COMMON_SOURCE_CASCODE_VARIANT else "OUT",
            "G": "IN",
            "S": (
                "NSRC"
                if variant == "source_degenerated_common_source"
                else "VSS"
            ),
            "B": "VSS",
        },
        "RD0": {"PLUS": "VDD", "MINUS": "OUT"},
    }
    if variant == "source_degenerated_common_source":
        expected_terminals["RS0"] = {"PLUS": "NSRC", "MINUS": "VSS"}
    elif variant == _COMMON_SOURCE_CASCODE_VARIANT:
        expected_terminals["MNCAS"] = {
            "D": "OUT",
            "G": "VCAS",
            "S": "NCAS",
            "B": "VSS",
        }
    for name, expected in expected_terminals.items():
        actual = by_name[name].get("terms", {})
        if actual != expected:
            raise RuntimeError(
                "existing schematic is not the VDA common-source stage: "
                f"{name} terminals={actual!r}, expected={expected!r}"
            )
    if profile is not None:
        expected_masters = {
            "MN0": (profile["tech_library"], profile["nmos_cell"]),
            "RD0": ("analogLib", "res"),
        }
        if variant == "source_degenerated_common_source":
            expected_masters["RS0"] = ("analogLib", "res")
        elif variant == _COMMON_SOURCE_CASCODE_VARIANT:
            expected_masters["MNCAS"] = (
                profile["tech_library"],
                profile["nmos_cell"],
            )
        for name, expected in expected_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    "existing schematic is not the VDA common-source stage: "
                    f"{name} master is {actual!r}, expected {expected!r}"
                )
    return variant


def _assert_differential_pair(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> str:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    core_names = {"MN0", "MN1", "RD0", "RD1"}
    tail_names = core_names | {"MNTAIL"}
    degenerated_tail_names = tail_names | {"RS0", "RS1"}
    current_mirror_names = {"MN0", "MN1", "MNTAIL", "MP0", "MP1"}
    current_mirror_degenerated_names = current_mirror_names | {"RS0", "RS1"}
    names = set(by_name)
    if frozenset(names) not in {
        frozenset(core_names),
        frozenset(tail_names),
        frozenset(degenerated_tail_names),
        frozenset(current_mirror_names),
        frozenset(current_mirror_degenerated_names),
    }:
        raise RuntimeError(
            "existing schematic is not the VDA differential pair: "
            f"instances={sorted(by_name)}"
        )
    if names == current_mirror_degenerated_names:
        variant = _DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT
    elif names == current_mirror_names:
        variant = _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT
    elif names == degenerated_tail_names:
        variant = _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT
    elif names == tail_names:
        variant = _DIFFERENTIAL_PAIR_TAIL_VARIANT
    else:
        variant = _DIFFERENTIAL_PAIR_BASE_VARIANT
    required_pins = {"INP", "INN", "OUTP", "OUTN", "TAIL", "VDD", "VSS"}
    required_nets = set(required_pins)
    real_tail_names = {
        frozenset(tail_names),
        frozenset(degenerated_tail_names),
        frozenset(current_mirror_names),
        frozenset(current_mirror_degenerated_names),
    }
    has_source_degeneration = frozenset(names) in {
        frozenset(degenerated_tail_names),
        frozenset(current_mirror_degenerated_names),
    }
    has_current_mirror_load = frozenset(names) in {
        frozenset(current_mirror_names),
        frozenset(current_mirror_degenerated_names),
    }
    if frozenset(names) in real_tail_names:
        required_pins.add("BIAS")
        required_nets.add("BIAS")
    if has_source_degeneration:
        required_nets.update({"NSP", "NSN"})
    missing_pins = required_pins - set(data.get("pins", {}).keys())
    if missing_pins:
        raise RuntimeError(
            "existing schematic is not the VDA differential pair: "
            f"missing pins={sorted(missing_pins)}"
        )
    missing_nets = required_nets - set(data.get("nets", {}).keys())
    if missing_nets:
        raise RuntimeError(
            "existing schematic is not the VDA differential pair: "
            f"missing nets={sorted(missing_nets)}"
        )
    expected_terminals = {
        "MN0": {
            "D": "OUTP",
            "G": "INP",
            "S": "NSP" if has_source_degeneration else "TAIL",
            "B": "VSS",
        },
        "MN1": {
            "D": "OUTN",
            "G": "INN",
            "S": "NSN" if has_source_degeneration else "TAIL",
            "B": "VSS",
        },
    }
    if has_current_mirror_load:
        expected_terminals.update(
            {
                "MP0": {"D": "OUTP", "G": "OUTP", "S": "VDD", "B": "VDD"},
                "MP1": {"D": "OUTN", "G": "OUTP", "S": "VDD", "B": "VDD"},
            }
        )
    else:
        expected_terminals.update(
            {
                "RD0": {"PLUS": "VDD", "MINUS": "OUTP"},
                "RD1": {"PLUS": "VDD", "MINUS": "OUTN"},
            }
        )
    if frozenset(names) in real_tail_names:
        expected_terminals["MNTAIL"] = {
            "D": "TAIL",
            "G": "BIAS",
            "S": "VSS",
            "B": "VSS",
        }
    if has_source_degeneration:
        expected_terminals.update(
            {
                "RS0": {"PLUS": "NSP", "MINUS": "TAIL"},
                "RS1": {"PLUS": "NSN", "MINUS": "TAIL"},
            }
        )
    for name, expected in expected_terminals.items():
        actual = by_name[name].get("terms", {})
        if actual != expected:
            raise RuntimeError(
                "existing schematic is not the VDA differential pair: "
                f"{name} terminals={actual!r}, expected={expected!r}"
            )
    if profile is not None:
        expected_masters: dict[str, tuple[str, str]] = {
            "MN0": (profile["tech_library"], profile["nmos_cell"]),
            "MN1": (profile["tech_library"], profile["nmos_cell"]),
        }
        if has_current_mirror_load:
            expected_masters.update(
                {
                    "MP0": (profile["tech_library"], profile["pmos_cell"]),
                    "MP1": (profile["tech_library"], profile["pmos_cell"]),
                }
            )
        else:
            expected_masters.update(
                {"RD0": ("analogLib", "res"), "RD1": ("analogLib", "res")}
            )
        if frozenset(names) in real_tail_names:
            expected_masters["MNTAIL"] = (
                profile["tech_library"],
                profile["nmos_cell"],
            )
        if has_source_degeneration:
            expected_masters.update(
                {"RS0": ("analogLib", "res"), "RS1": ("analogLib", "res")}
            )
        for name, expected in expected_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    "existing schematic is not the VDA differential pair: "
                    f"{name} master is {actual!r}, expected {expected!r}"
                )
    return variant


def _um(value: float) -> str:
    return f"{float(value):.12g}u"


def _ohm(value: float) -> str:
    return f"{float(value):.12g}"


_ENGINEERING_LENGTH = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([fpnum]?)$"
)


def _length_um(value: Any) -> float:
    text = str(value).strip().strip('"')
    match = _ENGINEERING_LENGTH.fullmatch(text)
    if match is None:
        raise RuntimeError(f"unsupported OA/netlist length value: {value!r}")
    number = float(match.group(1))
    suffix = match.group(2)
    scale_to_um = {
        "f": 1e-9,
        "p": 1e-6,
        "n": 1e-3,
        "u": 1.0,
        "m": 1e3,
        "": 1e6,
    }
    return number * scale_to_um[suffix]


_ENGINEERING_VALUE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$"
)


def _resistance_ohm(value: Any) -> float:
    text = str(value).strip().strip('"')
    match = _ENGINEERING_VALUE.fullmatch(text)
    if match is None:
        raise RuntimeError(f"unsupported OA/netlist resistance value: {value!r}")
    suffix = match.group(2).lower()
    scales = {
        "": 1.0,
        "m": 1e-3,
        "k": 1e3,
        "meg": 1e6,
        "g": 1e9,
        "t": 1e12,
    }
    if suffix not in scales:
        raise RuntimeError(f"unsupported OA/netlist resistance value: {value!r}")
    return float(match.group(1)) * scales[suffix]


def _semantic_parameters_from_schematic(data: dict[str, Any]) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    values: dict[str, float] = {}
    lengths: list[float] = []
    for instance_name, semantic_name in (
        ("MN0", "nmos_width_um"),
        ("MP0", "pmos_width_um"),
    ):
        if instance_name not in by_name:
            raise RuntimeError(f"schematic readback missing {instance_name}")
        params = by_name[instance_name].get("params", {})
        width = params.get("Wfg", params.get("w"))
        length = params.get("l")
        if width is None or length is None:
            raise RuntimeError(
                f"schematic readback missing width/length for {instance_name}"
            )
        values[semantic_name] = _length_um(width)
        lengths.append(_length_um(length))
    if abs(lengths[0] - lengths[1]) > 1e-9:
        raise RuntimeError(
            f"schematic NMOS/PMOS lengths differ: {lengths[0]}um vs {lengths[1]}um"
        )
    values["length_um"] = lengths[0]
    return values


def _common_source_semantic_parameters_from_schematic(
    data: dict[str, Any],
) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if "MN0" not in by_name or "RD0" not in by_name:
        raise RuntimeError("common-source readback is missing MN0 or RD0")
    mos_params = by_name["MN0"].get("params", {})
    width = mos_params.get("Wfg", mos_params.get("w"))
    length = mos_params.get("l")
    resistance = by_name["RD0"].get("params", {}).get("r")
    if width is None or length is None:
        raise RuntimeError("common-source readback is missing MN0 width/length")
    if resistance is None:
        raise RuntimeError("common-source readback is missing RD0 resistance")
    semantic_parameters = {
        "device_width_um": _length_um(width),
        "length_um": _length_um(length),
        "load_resistance_ohm": _resistance_ohm(resistance),
    }
    if "RS0" in by_name:
        source_resistance = by_name["RS0"].get("params", {}).get("r")
        if source_resistance is None:
            raise RuntimeError("common-source readback is missing RS0 resistance")
        semantic_parameters["source_resistance_ohm"] = _resistance_ohm(
            source_resistance
        )
    if "MNCAS" in by_name:
        cascode_params = by_name["MNCAS"].get("params", {})
        cascode_width = cascode_params.get("Wfg", cascode_params.get("w"))
        cascode_length = cascode_params.get("l")
        if cascode_width is None or cascode_length is None:
            raise RuntimeError(
                "common-source readback is missing MNCAS width/length"
            )
        semantic_parameters.update(
            {
                "cascode_width_um": _length_um(cascode_width),
                "cascode_length_um": _length_um(cascode_length),
            }
        )
    return semantic_parameters


def _positive_device_count(value: Any, label: str) -> float:
    try:
        count = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot parse {label}: {value!r}") from exc
    if count <= 0:
        raise RuntimeError(f"{label} must be positive, got {value!r}")
    return count


def _resolved_device_count_parameter(
    params: dict[str, Any],
    name: str,
    instance_name: str,
) -> float:
    """Resolve one exact CDF iPar indirection without evaluating SKILL."""

    value = params.get(name, 1)
    normalized = str(value).replace("\\", "")
    reference = re.fullmatch(
        r'iPar\("([A-Za-z_][A-Za-z0-9_]*)"\)', normalized
    )
    if reference is not None:
        referenced_name = reference.group(1)
        if referenced_name not in params:
            raise RuntimeError(
                f"cannot resolve {instance_name}.{name}: missing CDF parameter "
                f"{referenced_name!r}"
            )
        value = params[referenced_name]
        if re.fullmatch(
            r'iPar\("([A-Za-z_][A-Za-z0-9_]*)"\)',
            str(value).replace("\\", ""),
        ):
            raise RuntimeError(
                f"cannot resolve {instance_name}.{name}: nested CDF indirection"
            )
    return _positive_device_count(value, f"{instance_name}.{name}")


def _mos_geometry_from_schematic_instance(
    instance: dict[str, Any], instance_name: str
) -> dict[str, float]:
    params = instance.get("params", {})
    fingers = _resolved_device_count_parameter(
        params, "fingers", instance_name
    )
    multiplicity = _resolved_device_count_parameter(
        params, "m", instance_name
    )
    if params.get("Wfg") is not None:
        finger_width_um = _length_um(params["Wfg"])
    elif params.get("w") is not None:
        finger_width_um = _length_um(params["w"]) / fingers
    else:
        raise RuntimeError(
            f"common-source readback is missing {instance_name} Wfg/w"
        )
    return {
        "finger_width_um": finger_width_um,
        "fingers": fingers,
        "multiplicity": multiplicity,
        "total_width_um": finger_width_um * fingers * multiplicity,
    }


def _common_source_device_geometry_from_schematic(
    data: dict[str, Any],
) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if "MN0" not in by_name:
        raise RuntimeError("common-source readback is missing MN0")
    geometry = _mos_geometry_from_schematic_instance(by_name["MN0"], "MN0")
    if "MNCAS" in by_name:
        cascode = _mos_geometry_from_schematic_instance(
            by_name["MNCAS"], "MNCAS"
        )
        geometry.update(
            {f"cascode_{name}": value for name, value in cascode.items()}
        )
    return geometry


def _common_source_summary(data: dict[str, Any]) -> dict[str, Any]:
    useful_params = {
        "Wfg",
        "w",
        "l",
        "fingers",
        "nf",
        "m",
        "model",
        "multi",
        "r",
    }
    instances = []
    for item in data.get("instances", []):
        instances.append(
            {
                "name": item.get("name"),
                "library": item.get("lib"),
                "cell": item.get("cell"),
                "parameters": {
                    key: value
                    for key, value in item.get("params", {}).items()
                    if key in useful_params
                },
                "terminals": item.get("terms", {}),
                "xy": item.get("xy"),
                "orient": item.get("orient"),
                "bBox": item.get("bBox"),
                "numInst": item.get("numInst"),
                "view": item.get("view"),
            }
        )
    return {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "semantic_parameters": _common_source_semantic_parameters_from_schematic(data),
        "device_geometry": _common_source_device_geometry_from_schematic(data),
        "topology_variant": _assert_common_source(data),
        "bridge_schematic": data,
    }


def _differential_pair_semantic_parameters_from_schematic(
    data: dict[str, Any],
) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if not {"MN0", "MN1"} <= set(by_name):
        raise RuntimeError("differential-pair readback is missing an input device")
    has_resistive_load = {"RD0", "RD1"} <= set(by_name)
    has_current_mirror_load = {"MP0", "MP1"} <= set(by_name)
    if has_resistive_load == has_current_mirror_load:
        raise RuntimeError(
            "differential-pair readback must contain exactly one complete load pair"
        )
    widths: list[float] = []
    lengths: list[float] = []
    resistances: list[float] = []
    for instance in ("MN0", "MN1"):
        parameters = by_name[instance].get("params", {})
        width = parameters.get("Wfg", parameters.get("w"))
        length = parameters.get("l")
        if width is None or length is None:
            raise RuntimeError(
                f"differential-pair readback is missing {instance} width/length"
            )
        widths.append(_length_um(width))
        lengths.append(_length_um(length))
    if has_resistive_load:
        for instance in ("RD0", "RD1"):
            resistance = by_name[instance].get("params", {}).get("r")
            if resistance is None:
                raise RuntimeError(
                    f"differential-pair readback is missing {instance} resistance"
                )
            resistances.append(_resistance_ohm(resistance))
    comparisons = {
        "input widths": widths,
        "input lengths": lengths,
    }
    if resistances:
        comparisons["load resistances"] = resistances
    for label, values in comparisons.items():
        tolerance = max(abs(values[0]) * 1e-6, 1e-9)
        if abs(values[0] - values[1]) > tolerance:
            raise RuntimeError(
                f"differential-pair {label} differ: {values[0]:.12g} vs "
                f"{values[1]:.12g}"
            )
    semantic = {
        "input_width_um": widths[0],
        "length_um": lengths[0],
    }
    if has_resistive_load:
        semantic["load_resistance_ohm"] = resistances[0]
    else:
        pmos_widths: list[float] = []
        pmos_lengths: list[float] = []
        for instance in ("MP0", "MP1"):
            parameters = by_name[instance].get("params", {})
            width = parameters.get("Wfg", parameters.get("w"))
            length = parameters.get("l")
            if width is None or length is None:
                raise RuntimeError(
                    f"differential-pair readback is missing {instance} width/length"
                )
            pmos_widths.append(_length_um(width))
            pmos_lengths.append(_length_um(length))
        for label, values in (
            ("PMOS load widths", pmos_widths),
            ("PMOS load lengths", pmos_lengths),
        ):
            tolerance = max(abs(values[0]) * 1e-6, 1e-9)
            if abs(values[0] - values[1]) > tolerance:
                raise RuntimeError(
                    f"differential-pair {label} differ: {values[0]:.12g} vs "
                    f"{values[1]:.12g}"
                )
        semantic.update(
            {
                "pmos_load_width_um": pmos_widths[0],
                "pmos_load_length_um": pmos_lengths[0],
            }
        )
    if "MNTAIL" in by_name:
        tail_parameters = by_name["MNTAIL"].get("params", {})
        tail_width = tail_parameters.get("Wfg", tail_parameters.get("w"))
        tail_length = tail_parameters.get("l")
        if tail_width is None or tail_length is None:
            raise RuntimeError(
                "differential-pair readback is missing MNTAIL width/length"
            )
        semantic.update(
            {
                "tail_width_um": _length_um(tail_width),
                "tail_length_um": _length_um(tail_length),
            }
        )
    source_resistances: list[float] = []
    for instance in ("RS0", "RS1"):
        if instance in by_name:
            resistance = by_name[instance].get("params", {}).get("r")
            if resistance is None:
                raise RuntimeError(
                    f"differential-pair readback is missing {instance} resistance"
                )
            source_resistances.append(_resistance_ohm(resistance))
    if source_resistances:
        if len(source_resistances) != 2:
            raise RuntimeError(
                "differential-pair source degeneration requires both RS0 and RS1"
            )
        tolerance = max(abs(source_resistances[0]) * 1e-6, 1e-9)
        if abs(source_resistances[0] - source_resistances[1]) > tolerance:
            raise RuntimeError(
                "differential-pair source resistances differ: "
                f"{source_resistances[0]:.12g} vs {source_resistances[1]:.12g}"
            )
        semantic["source_resistance_ohm"] = source_resistances[0]
    return semantic


def _differential_pair_device_geometry_from_schematic(
    data: dict[str, Any],
) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    geometries: list[dict[str, float]] = []
    for instance in ("MN0", "MN1"):
        if instance not in by_name:
            raise RuntimeError(f"differential-pair readback is missing {instance}")
        parameters = by_name[instance].get("params", {})
        fingers = _positive_device_count(
            parameters.get("fingers", 1), f"{instance}.fingers"
        )
        multiplicity = _positive_device_count(
            parameters.get("m", 1), f"{instance}.m"
        )
        if parameters.get("Wfg") is not None:
            finger_width_um = _length_um(parameters["Wfg"])
        elif parameters.get("w") is not None:
            finger_width_um = _length_um(parameters["w"]) / fingers
        else:
            raise RuntimeError(
                f"differential-pair readback is missing {instance} Wfg/w"
            )
        geometries.append(
            {
                "finger_width_um": finger_width_um,
                "fingers": fingers,
                "multiplicity": multiplicity,
                "total_width_um": finger_width_um * fingers * multiplicity,
            }
        )
    _assert_parameter_consistency(
        geometries[0],
        geometries[1],
        expected_label="MN0 OA geometry",
        actual_label="MN1 OA geometry",
    )
    return geometries[0]


def _differential_pair_tail_device_geometry_from_schematic(
    data: dict[str, Any],
) -> dict[str, float] | None:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if "MNTAIL" not in by_name:
        return None
    parameters = by_name["MNTAIL"].get("params", {})
    fingers = _positive_device_count(
        parameters.get("fingers", 1), "MNTAIL.fingers"
    )
    multiplicity = _positive_device_count(parameters.get("m", 1), "MNTAIL.m")
    if parameters.get("Wfg") is not None:
        finger_width_um = _length_um(parameters["Wfg"])
    elif parameters.get("w") is not None:
        finger_width_um = _length_um(parameters["w"]) / fingers
    else:
        raise RuntimeError("differential-pair readback is missing MNTAIL Wfg/w")
    return {
        "finger_width_um": finger_width_um,
        "fingers": fingers,
        "multiplicity": multiplicity,
        "total_width_um": finger_width_um * fingers * multiplicity,
    }


def _differential_pair_current_mirror_geometry_from_schematic(
    data: dict[str, Any],
) -> dict[str, float] | None:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if "MP0" not in by_name and "MP1" not in by_name:
        return None
    if not {"MP0", "MP1"} <= set(by_name):
        raise RuntimeError("differential-pair current mirror requires MP0 and MP1")
    geometries: list[dict[str, float]] = []
    for instance in ("MP0", "MP1"):
        parameters = by_name[instance].get("params", {})
        fingers = _positive_device_count(
            parameters.get("fingers", 1), f"{instance}.fingers"
        )
        multiplicity = _positive_device_count(
            parameters.get("m", 1), f"{instance}.m"
        )
        if parameters.get("Wfg") is not None:
            finger_width_um = _length_um(parameters["Wfg"])
        elif parameters.get("w") is not None:
            finger_width_um = _length_um(parameters["w"]) / fingers
        else:
            raise RuntimeError(
                f"differential-pair readback is missing {instance} Wfg/w"
            )
        geometries.append(
            {
                "finger_width_um": finger_width_um,
                "fingers": fingers,
                "multiplicity": multiplicity,
                "total_width_um": finger_width_um * fingers * multiplicity,
            }
        )
    _assert_parameter_consistency(
        geometries[0],
        geometries[1],
        expected_label="MP0 OA geometry",
        actual_label="MP1 OA geometry",
    )
    return geometries[0]


def _differential_pair_summary(data: dict[str, Any]) -> dict[str, Any]:
    useful_params = {
        "Wfg",
        "w",
        "l",
        "fingers",
        "nf",
        "m",
        "model",
        "multi",
        "r",
    }
    instances = []
    for item in data.get("instances", []):
        instances.append(
            {
                "name": item.get("name"),
                "library": item.get("lib"),
                "cell": item.get("cell"),
                "parameters": {
                    key: value
                    for key, value in item.get("params", {}).items()
                    if key in useful_params
                },
                "terminals": item.get("terms", {}),
                "xy": item.get("xy"),
                "orient": item.get("orient"),
                "bBox": item.get("bBox"),
                "numInst": item.get("numInst"),
                "view": item.get("view"),
            }
        )
    summary = {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "semantic_parameters": (
            _differential_pair_semantic_parameters_from_schematic(data)
        ),
        "device_geometry": _differential_pair_device_geometry_from_schematic(data),
        "topology_variant": _assert_differential_pair(data),
        "bridge_schematic": data,
    }
    tail_geometry = _differential_pair_tail_device_geometry_from_schematic(data)
    if tail_geometry is not None:
        summary["tail_device_geometry"] = tail_geometry
    current_mirror_geometry = (
        _differential_pair_current_mirror_geometry_from_schematic(data)
    )
    if current_mirror_geometry is not None:
        summary["current_mirror_load_geometry"] = current_mirror_geometry
    return summary


def _assert_parameter_consistency(
    expected: dict[str, float],
    actual: dict[str, float],
    *,
    expected_label: str,
    actual_label: str,
) -> None:
    for name, expected_value in expected.items():
        if name not in actual:
            raise RuntimeError(
                f"parameter mismatch: {actual_label} is missing {name} from {expected_label}"
            )
        actual_value = float(actual[name])
        tolerance = max(abs(float(expected_value)) * 1e-6, 1e-9)
        if abs(float(expected_value) - actual_value) > tolerance:
            raise RuntimeError(
                f"parameter mismatch for {name}: {expected_label}={float(expected_value):.12g}, "
                f"{actual_label}={actual_value:.12g}"
            )


def _requested_instance_parameters(
    payload: dict[str, Any],
) -> dict[str, dict[str, str]]:
    raw_updates = payload.get("instance_parameter_updates", [])
    if not isinstance(raw_updates, list) or not raw_updates:
        raise RuntimeError("explicit instance parameter update list is empty")
    updates: dict[str, dict[str, str]] = {}
    for raw_update in raw_updates:
        if not isinstance(raw_update, dict):
            raise RuntimeError("invalid explicit instance parameter update")
        instance = raw_update.get("instance")
        parameters = raw_update.get("parameters")
        if not isinstance(instance, str) or not instance:
            raise RuntimeError(f"invalid instance name in parameter update: {instance!r}")
        if instance in updates:
            raise RuntimeError(f"duplicate instance parameter update: {instance}")
        if not isinstance(parameters, dict) or not parameters:
            raise RuntimeError(f"parameter update for {instance} is empty")
        normalized: dict[str, str] = {}
        for name, value in parameters.items():
            if not isinstance(name, str):
                raise RuntimeError(f"invalid CDF/OA parameter name: {name!r}")
            if not isinstance(value, str):
                raise RuntimeError(
                    f"invalid CDF/OA parameter value for {instance}.{name}"
                )
            normalized[name] = value
        updates[instance] = normalized
    return updates


def _expected_instance_parameters(
    payload: dict[str, Any],
) -> dict[str, dict[str, str]]:
    raw = payload.get("expected_instance_parameters")
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("expected instance parameter map is empty")
    expected: dict[str, dict[str, str]] = {}
    for instance, parameters in raw.items():
        if not isinstance(instance, str) or not instance:
            raise RuntimeError(f"invalid expected instance name: {instance!r}")
        if not isinstance(parameters, dict) or not parameters:
            raise RuntimeError(f"expected parameter map for {instance} is empty")
        expected[instance] = {}
        for name, value in parameters.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise RuntimeError(
                    f"invalid expected CDF/OA parameter for {instance}.{name}"
                )
            expected[instance][name] = value
    return expected


class ParameterReadbackMismatch(RuntimeError):
    """A CDF callback completed but the requested value did not persist."""


def _verify_instance_parameter_values(
    client,
    library: str,
    cell: str,
    expected: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Read target CDF values directly, including empty or long strings."""
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    checks: list[str] = []
    for instance, parameters in expected.items():
        escaped_instance = escape_skill_string(instance)
        checks.extend(
            [
                "inst = car(setof(x cv~>instances "
                f'x~>name == "{escaped_instance}"))',
                "unless(inst error("
                f'"instance not found during parameter verification: {escaped_instance}"))',
                "iCDF = cdfGetInstCDF(inst)",
                "unless(iCDF error("
                f'"instance has no CDF during parameter verification: {escaped_instance}"))',
            ]
        )
        for name, value in parameters.items():
            escaped_name = escape_skill_string(name)
            escaped_value = escape_skill_string(value)
            label = escape_skill_string(f"{instance}.{name}")
            checks.extend(
                [
                    f'p = get(iCDF "{escaped_name}")',
                    f'unless(p error("unknown CDF parameter: {label}"))',
                    "unless(p~>value == "
                    f'"{escaped_value}" error("CDF parameter readback mismatch: {label}"))',
                ]
            )

    skill = " ".join(
        [
            "let((cv inst iCDF p)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during parameter verification"))',
            *checks,
            "t)",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        message = f"targeted CDF readback failed: {errors[0]}"
        if "CDF parameter readback mismatch:" in str(errors[0]):
            raise ParameterReadbackMismatch(message)
        raise RuntimeError(message)
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(f"unexpected targeted CDF readback result: {output!r}")
    return {
        instance: dict(parameters) for instance, parameters in expected.items()
    }


def _resolved_parameters(
    payload: dict[str, Any],
    device_parameters: dict[str, float] | None = None,
) -> dict[str, float]:
    profile = payload["profile"]
    supplied = payload.get("parameters", {})
    device_parameters = device_parameters or {}
    nmos_width_um = float(
        supplied.get(
            "nmos_width_um",
            device_parameters.get(
                "nmos_width_um", profile["default_inverter_nmos_width_um"]
            ),
        )
    )
    pmos_width_um = float(
        supplied.get(
            "pmos_width_um",
            device_parameters.get(
                "pmos_width_um",
                nmos_width_um
                * profile["default_inverter_pmos_to_nmos_width_ratio"],
            ),
        )
    )
    return {
        "nmos_width_um": nmos_width_um,
        "pmos_width_um": pmos_width_um,
        "length_um": float(
            supplied.get(
                "length_um",
                device_parameters.get("length_um", profile["default_length_um"]),
            )
        ),
        "load_ff": float(supplied.get("load_ff", profile["default_load_ff"])),
        "vdd_v": float(supplied.get("vdd_v", profile["default_vdd_v"])),
    }


def _resolved_common_source_parameters(
    payload: dict[str, Any],
    oa_parameters: dict[str, float] | None = None,
    topology_variant: str | None = None,
) -> dict[str, float]:
    profile = payload["profile"]
    supplied = payload.get("parameters", {})
    oa_parameters = oa_parameters or {}
    parameters = {
        "device_width_um": float(
            supplied.get(
                "device_width_um",
                oa_parameters.get(
                    "device_width_um", profile["default_common_source_width_um"]
                ),
            )
        ),
        "length_um": float(
            supplied.get(
                "length_um",
                oa_parameters.get("length_um", profile["default_length_um"]),
            )
        ),
        "load_resistance_ohm": float(
            supplied.get(
                "load_resistance_ohm",
                oa_parameters.get(
                    "load_resistance_ohm",
                    profile["default_common_source_load_resistance_ohm"],
                ),
            )
        ),
        "bias_v": float(supplied.get("bias_v", profile["default_common_source_bias_v"])),
        "vdd_v": float(supplied.get("vdd_v", profile["default_vdd_v"])),
    }
    if "source_resistance_ohm" in supplied or "source_resistance_ohm" in oa_parameters:
        parameters["source_resistance_ohm"] = float(
            supplied["source_resistance_ohm"]
            if "source_resistance_ohm" in supplied
            else oa_parameters["source_resistance_ohm"]
        )
    is_cascode = topology_variant == _COMMON_SOURCE_CASCODE_VARIANT
    if is_cascode:
        if "cascode_width_um" not in oa_parameters or "cascode_length_um" not in oa_parameters:
            raise RuntimeError("cascode common-source OA readback is missing MNCAS geometry")
        if "cascode_bias_v" not in supplied:
            raise RuntimeError("cascode common-source simulation requires cascode_bias_v")
        parameters.update(
            {
                "cascode_width_um": float(
                    supplied.get("cascode_width_um", oa_parameters["cascode_width_um"])
                ),
                "cascode_length_um": float(
                    supplied.get("cascode_length_um", oa_parameters["cascode_length_um"])
                ),
                "cascode_bias_v": float(supplied["cascode_bias_v"]),
            }
        )
    else:
        conflicts = sorted(
            {"cascode_width_um", "cascode_length_um", "cascode_bias_v"}
            & supplied.keys()
        )
        if conflicts:
            raise RuntimeError(
                "non-cascode common-source topology rejects cascode parameters: "
                + ", ".join(conflicts)
            )
    if "load_ff" in supplied:
        parameters["load_ff"] = float(supplied["load_ff"])
    return parameters


def _resolved_differential_pair_parameters(
    payload: dict[str, Any],
    oa_parameters: dict[str, float] | None = None,
    topology_variant: str = "resistive_load_nmos_differential_pair",
) -> dict[str, float]:
    profile = payload["profile"]
    supplied = payload.get("parameters", {})
    oa_parameters = oa_parameters or {}
    parameters = {
        "input_width_um": float(
            supplied.get(
                "input_width_um",
                oa_parameters.get(
                    "input_width_um", profile["default_common_source_width_um"]
                ),
            )
        ),
        "length_um": float(
            supplied.get(
                "length_um",
                oa_parameters.get("length_um", profile["default_length_um"]),
            )
        ),
        "common_mode_v": float(
            supplied.get("common_mode_v", profile["default_common_source_bias_v"])
        ),
        "vdd_v": float(supplied.get("vdd_v", profile["default_vdd_v"])),
    }
    current_mirror_load = _differential_pair_has_current_mirror_load(
        topology_variant
    )
    if current_mirror_load:
        if "load_resistance_ohm" in supplied:
            raise RuntimeError(
                "current-mirror-load differential pair rejects load_resistance_ohm"
            )
        for name in ("pmos_load_width_um", "pmos_load_length_um"):
            if name not in supplied and name not in oa_parameters:
                raise RuntimeError(
                    f"current-mirror-load OA readback is missing {name}"
                )
            parameters[name] = float(
                supplied[name] if name in supplied else oa_parameters[name]
            )
    else:
        conflicts = sorted(
            {"pmos_load_width_um", "pmos_load_length_um"} & supplied.keys()
        )
        if conflicts:
            raise RuntimeError(
                "resistive-load differential pair rejects current-mirror "
                "parameters: " + ", ".join(conflicts)
            )
        parameters["load_resistance_ohm"] = float(
            supplied.get(
                "load_resistance_ohm",
                oa_parameters.get(
                    "load_resistance_ohm",
                    profile["default_common_source_load_resistance_ohm"],
                ),
            )
        )
    real_tail = _differential_pair_has_real_tail(topology_variant)
    if real_tail:
        conflicts = sorted(
            {"tail_current_ua", "tail_output_resistance_ohm"} & supplied.keys()
        )
        if conflicts:
            raise RuntimeError(
                "real-tail differential pair rejects ideal-tail parameters: "
                + ", ".join(conflicts)
            )
        if "tail_bias_v" not in supplied:
            raise RuntimeError("real-tail differential pair requires tail_bias_v")
        for name in ("tail_width_um", "tail_length_um"):
            if name not in oa_parameters:
                raise RuntimeError(f"real-tail OA readback is missing {name}")
            parameters[name] = float(oa_parameters[name])
        parameters["tail_bias_v"] = float(supplied["tail_bias_v"])
    else:
        if "tail_bias_v" in supplied:
            raise RuntimeError("ideal-tail differential pair rejects tail_bias_v")
        parameters["tail_current_ua"] = float(
            supplied.get("tail_current_ua", 50.0)
        )
        if "tail_output_resistance_ohm" in supplied:
            parameters["tail_output_resistance_ohm"] = float(
                supplied["tail_output_resistance_ohm"]
            )
    if _differential_pair_has_source_degeneration(topology_variant):
        if "source_resistance_ohm" not in oa_parameters:
            raise RuntimeError(
                "degenerated real-tail OA readback is missing source_resistance_ohm"
            )
        parameters["source_resistance_ohm"] = float(
            oa_parameters["source_resistance_ohm"]
        )
    elif "source_resistance_ohm" in supplied:
        raise RuntimeError(
            "source_resistance_ohm requires the degenerated real-tail topology"
        )
    if "load_ff" in supplied:
        parameters["load_ff"] = float(supplied["load_ff"])
    return parameters


def _apply_parameters(
    client,
    library: str,
    cell: str,
    parameters: dict[str, float],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _set_target_instance_params(
        client,
        library,
        cell,
        "MN0",
        wf=_um(parameters["nmos_width_um"]),
        l=_um(parameters["length_um"]),
        nf="1",
        m="1",
        param_filters=None,
    )
    _set_target_instance_params(
        client,
        library,
        cell,
        "MP0",
        wf=_um(parameters["pmos_width_um"]),
        l=_um(parameters["length_um"]),
        nf="1",
        m="1",
        param_filters=None,
    )
    data = _read_schematic(client, library, cell)
    _assert_inverter(data, profile)
    summary = _summary(data)
    _assert_parameter_consistency(
        {
            name: float(parameters[name])
            for name in ("nmos_width_um", "pmos_width_um", "length_um")
        },
        summary["semantic_parameters"],
        expected_label="requested write",
        actual_label="OA readback",
    )
    return summary


def _common_source_instance_parameter_updates(
    parameters: dict[str, float],
) -> dict[str, dict[str, str]]:
    updates: dict[str, dict[str, str]] = {}
    mos_updates: dict[str, str] = {}
    if "device_width_um" in parameters:
        mos_updates["wf"] = _um(parameters["device_width_um"])
    if "length_um" in parameters:
        mos_updates["l"] = _um(parameters["length_um"])
    if mos_updates:
        updates["MN0"] = mos_updates
    if "load_resistance_ohm" in parameters:
        updates["RD0"] = {"r": _ohm(parameters["load_resistance_ohm"])}
    if "source_resistance_ohm" in parameters:
        updates["RS0"] = {"r": _ohm(parameters["source_resistance_ohm"])}
    cascode_updates: dict[str, str] = {}
    if "cascode_width_um" in parameters:
        cascode_updates["wf"] = _um(parameters["cascode_width_um"])
    if "cascode_length_um" in parameters:
        cascode_updates["l"] = _um(parameters["cascode_length_um"])
    if cascode_updates:
        updates["MNCAS"] = cascode_updates
    return updates


def _apply_common_source_parameters(
    client,
    library: str,
    cell: str,
    parameters: dict[str, float],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persistable = {
        name: float(parameters[name])
        for name in (
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "cascode_width_um",
            "cascode_length_um",
        )
        if name in parameters
    }
    if not persistable:
        raise RuntimeError("common-source OA parameter write is empty")
    current = _read_schematic(client, library, cell)
    variant = _assert_common_source(current, profile)
    if (
        "source_resistance_ohm" in persistable
        and variant != "source_degenerated_common_source"
    ):
        raise RuntimeError(
            "source_resistance_ohm requires a source-degenerated common-source topology"
        )
    cascode_fields = {"cascode_width_um", "cascode_length_um"} & persistable.keys()
    if cascode_fields and variant != _COMMON_SOURCE_CASCODE_VARIANT:
        raise RuntimeError(
            "cascode geometry requires a cascode common-source topology"
        )

    instance_updates = _common_source_instance_parameter_updates(persistable)
    if "MN0" in instance_updates:
        _set_target_instance_params(
            client,
            library,
            cell,
            "MN0",
            param_filters=None,
            **instance_updates["MN0"],
        )
    if "RD0" in instance_updates:
        _set_target_instance_params(
            client,
            library,
            cell,
            "RD0",
            param_filters=None,
            **instance_updates["RD0"],
        )
    if "RS0" in instance_updates:
        _set_target_instance_params(
            client,
            library,
            cell,
            "RS0",
            param_filters=None,
            **instance_updates["RS0"],
        )
    if "MNCAS" in instance_updates:
        _set_target_instance_params(
            client,
            library,
            cell,
            "MNCAS",
            param_filters=None,
            **instance_updates["MNCAS"],
        )
    data = _read_schematic(client, library, cell)
    _assert_common_source(data, profile)
    summary = _common_source_summary(data)
    _assert_parameter_consistency(
        persistable,
        summary["semantic_parameters"],
        expected_label="requested write",
        actual_label="OA readback",
    )
    return summary


def _differential_pair_instance_parameter_updates(
    parameters: dict[str, float],
) -> dict[str, dict[str, str]]:
    updates: dict[str, dict[str, str]] = {}
    mos_updates: dict[str, str] = {}
    if "input_width_um" in parameters:
        mos_updates["wf"] = _um(parameters["input_width_um"])
    if "length_um" in parameters:
        mos_updates["l"] = _um(parameters["length_um"])
    if mos_updates:
        updates["MN0"] = dict(mos_updates)
        updates["MN1"] = dict(mos_updates)
    if "load_resistance_ohm" in parameters:
        load = {"r": _ohm(parameters["load_resistance_ohm"])}
        updates["RD0"] = dict(load)
        updates["RD1"] = dict(load)
    pmos_updates: dict[str, str] = {}
    if "pmos_load_width_um" in parameters:
        pmos_updates["wf"] = _um(parameters["pmos_load_width_um"])
    if "pmos_load_length_um" in parameters:
        pmos_updates["l"] = _um(parameters["pmos_load_length_um"])
    if pmos_updates:
        updates["MP0"] = dict(pmos_updates)
        updates["MP1"] = dict(pmos_updates)
    tail_updates: dict[str, str] = {}
    if "tail_width_um" in parameters:
        tail_updates["wf"] = _um(parameters["tail_width_um"])
    if "tail_length_um" in parameters:
        tail_updates["l"] = _um(parameters["tail_length_um"])
    if tail_updates:
        updates["MNTAIL"] = tail_updates
    if "source_resistance_ohm" in parameters:
        source_resistance = {"r": _ohm(parameters["source_resistance_ohm"])}
        updates["RS0"] = dict(source_resistance)
        updates["RS1"] = dict(source_resistance)
    return updates


def _apply_differential_pair_parameters(
    client,
    library: str,
    cell: str,
    parameters: dict[str, float],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persistable = {
        name: float(parameters[name])
        for name in (
            "input_width_um",
            "length_um",
            "load_resistance_ohm",
            "tail_width_um",
            "tail_length_um",
            "source_resistance_ohm",
            "pmos_load_width_um",
            "pmos_load_length_um",
        )
        if name in parameters
    }
    if not persistable:
        raise RuntimeError("differential-pair OA parameter write is empty")
    current = _read_schematic(client, library, cell)
    topology_variant = _assert_differential_pair(current, profile)
    current_mirror_load = _differential_pair_has_current_mirror_load(
        topology_variant
    )
    if current_mirror_load and "load_resistance_ohm" in persistable:
        raise RuntimeError(
            "load_resistance_ohm requires a resistive-load differential pair"
        )
    if not current_mirror_load and (
        {"pmos_load_width_um", "pmos_load_length_um"} & persistable.keys()
    ):
        raise RuntimeError(
            "PMOS load geometry requires the current-mirror-load topology"
        )
    if (
        {"tail_width_um", "tail_length_um"} & persistable.keys()
        and not _differential_pair_has_real_tail(topology_variant)
    ):
        raise RuntimeError(
            "tail_width_um/tail_length_um require the real-tail topology"
        )
    if (
        "source_resistance_ohm" in persistable
        and not _differential_pair_has_source_degeneration(topology_variant)
    ):
        raise RuntimeError(
            "source_resistance_ohm requires the degenerated real-tail topology"
        )
    for instance, update in _differential_pair_instance_parameter_updates(
        persistable
    ).items():
        _set_target_instance_params(
            client,
            library,
            cell,
            instance,
            param_filters=None,
            **update,
        )
    data = _read_schematic(client, library, cell)
    _assert_differential_pair(data, profile)
    summary = _differential_pair_summary(data)
    _assert_parameter_consistency(
        persistable,
        summary["semantic_parameters"],
        expected_label="requested write",
        actual_label="OA readback",
    )
    return summary


def _apply_explicit_instance_parameters(
    client,
    library: str,
    cell: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    requested = _requested_instance_parameters(payload)
    current = _read_schematic(client, library, cell)
    if payload["circuit"] == "inverter":
        _assert_inverter(current, payload["profile"])
        summarize = _summary
    elif payload["circuit"] == "common_source":
        _assert_common_source(current, payload["profile"])
        summarize = _common_source_summary
    elif payload["circuit"] == "differential_pair":
        _assert_differential_pair(current, payload["profile"])
        summarize = _differential_pair_summary
    elif payload["circuit"] == "existing_schematic":
        summarize = _existing_schematic_summary
    else:
        raise RuntimeError(
            f"explicit instance parameter writes are unsupported for {payload['circuit']}"
        )

    current_parameters = _instance_parameters_from_schematic(current)
    scoped_parameters, _scope_readbacks = _read_hierarchy_parameter_scope_state(
        client,
        library,
        cell,
        current,
        payload,
    )
    current_parameters.update(scoped_parameters)
    missing_instances = sorted(set(requested) - set(current_parameters))
    if missing_instances:
        raise RuntimeError(
            "explicit parameter write targets missing instances: "
            + ", ".join(missing_instances)
        )
    applied_parameters: dict[str, dict[str, str]] = {}
    for instance, parameters in requested.items():
        target_library, target_cell, target_instance = _parameter_target_location(
            payload,
            library,
            cell,
            instance,
        )
        applied = _set_target_instance_params(
            client,
            target_library,
            target_cell,
            target_instance,
            param_filters=None,
            **parameters,
        )
        if not isinstance(applied, dict) or not applied:
            raise RuntimeError(
                f"Bridge did not report applied CDF parameters for {instance}"
            )
        applied_parameters[instance] = {
            str(name): str(value) for name, value in applied.items()
        }

    before = {
        instance: {
            name: current_parameters[instance].get(name)
            for name in parameters
        }
        for instance, parameters in applied_parameters.items()
    }

    updated = _read_schematic(client, library, cell)
    if payload["circuit"] == "inverter":
        _assert_inverter(updated, payload["profile"])
    elif payload["circuit"] == "common_source":
        _assert_common_source(updated, payload["profile"])
    elif payload["circuit"] == "differential_pair":
        _assert_differential_pair(updated, payload["profile"])
    repair_applied_parameters: dict[str, dict[str, str]] = {}
    repair_reason: str | None = None
    try:
        confirmed = _verify_scoped_instance_parameter_values(
            client,
            library,
            cell,
            payload,
            applied_parameters,
        )
    except ParameterReadbackMismatch as error:
        repair_reason = str(error)
        for instance, parameters in requested.items():
            target_library, target_cell, target_instance = _parameter_target_location(
                payload,
                library,
                cell,
                instance,
            )
            repaired: dict[str, str] = {}
            for name, value in parameters.items():
                applied = _set_target_instance_params(
                    client,
                    target_library,
                    target_cell,
                    target_instance,
                    param_filters=None,
                    **{name: value},
                )
                if not isinstance(applied, dict) or not applied:
                    raise RuntimeError(
                        "Bridge did not report ordered repair CDF parameter for "
                        f"{instance}.{name}"
                    )
                repaired.update(
                    {str(actual): str(result) for actual, result in applied.items()}
                )
            repair_applied_parameters[instance] = repaired
        if repair_applied_parameters != applied_parameters:
            raise RuntimeError(
                "Bridge ordered repair changed the applied CDF parameter mapping"
            )
        try:
            confirmed = _verify_scoped_instance_parameter_values(
                client,
                library,
                cell,
                payload,
                applied_parameters,
            )
        except ParameterReadbackMismatch as replay_error:
            raise ParameterReadbackMismatch(
                "ordered replay was attempted once after an initial CDF "
                f"mismatch but final readback still failed: {replay_error}"
            ) from replay_error
        updated = _read_schematic(client, library, cell)
        if payload["circuit"] == "inverter":
            _assert_inverter(updated, payload["profile"])
        elif payload["circuit"] == "common_source":
            _assert_common_source(updated, payload["profile"])
        elif payload["circuit"] == "differential_pair":
            _assert_differential_pair(updated, payload["profile"])
    readback = summarize(updated)
    if payload["circuit"] == "existing_schematic":
        pin_geometry, placement = _schematic_geometry_bundle(client, library, cell)
        readback = _existing_schematic_summary(
            updated,
            pin_geometry=pin_geometry,
            placement=placement,
        )
        readback = _attach_hierarchy_parameter_scope_state(
            client,
            library,
            cell,
            updated,
            payload,
            readback,
        )
    return {
        "requested_instance_parameters": requested,
        "requested_evidence_source": "user_input",
        "applied_instance_parameters": applied_parameters,
        "before_instance_parameters": before,
        "confirmed_instance_parameters": confirmed,
        "confirmed_evidence_source": "bridge_readback",
        "confirmation_method": "independent_targeted_cdf_equality",
        "application_method": (
            "bridge_batch_then_ordered_replay"
            if repair_reason is not None
            else "bridge_batch"
        ),
        "ordered_replay_reason": repair_reason,
        "ordered_replay_applied_instance_parameters": repair_applied_parameters,
        "readback": readback,
    }


def _attach_targeted_parameter_verification(
    client,
    library: str,
    cell: str,
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    if not payload.get("verify_instance_parameters"):
        return summary
    expected = _expected_instance_parameters(payload)
    summary["confirmed_instance_parameters"] = (
        _verify_scoped_instance_parameter_values(
            client,
            library,
            cell,
            payload,
            expected,
        )
    )
    summary["confirmed_evidence_source"] = "bridge_readback"
    summary["confirmation_method"] = "independent_targeted_cdf_equality"
    return summary


_ADE_SETUP_FILENAMES = {
    "active.state",
    "maestro.sdb",
    "state_from_active_state.xml",
    "state_from_sdb.xml",
    "state_from_skill.txt",
}
_ADE_RUN_INPUT_FILENAMES = {
    "input.scs",
    "paramInfo.ils",
    "qpInformation.ils",
    "runObjFile",
    "variables_file",
}
_ADE_RUN_LOG_FILENAMES = {"logFile", "spectre.out"}
_ADE_RUN_RESULT_SUFFIXES = {
    ".ac",
    ".dc",
    ".noise",
    ".pac",
    ".pnoise",
    ".pss",
    ".pxf",
    ".sens",
    ".stb",
    ".tran",
    ".xf",
}
_ADE_ARTIFACT_LINE_PREFIX = "VDA_ARTIFACT\t"


def _maestro_tests_readback(client, session: str) -> list[str]:
    readback = client.execute_skill(
        f'maeGetSetup(?session "{session}")', timeout=30
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro setup readback failed: {errors[0]}")
    raw_tests = str(getattr(readback, "output", "") or "")
    return re.findall(r'"([^"\\]+)"', raw_tests)


def _maestro_test_design_readback(
    client, test: str, *, session: str
) -> dict[str, str]:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    escaped_test = escape_skill_string(test)
    escaped_session = escape_skill_string(session)
    expression = (
        "let((a d) "
        f'a=maeGetTestSession("{escaped_test}" ?session "{escaped_session}") '
        "d=a~>data~>designObj "
        "list(d~>libName d~>cellName d~>viewName))"
    )
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro design readback failed for test {test}: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if (
        not isinstance(parsed, list)
        or len(parsed) != 3
        or not all(isinstance(value, str) and value for value in parsed)
    ):
        raise RuntimeError(
            f"invalid Maestro design readback for test {test}: {parsed!r}"
        )
    return {"library": parsed[0], "cell": parsed[1], "view": parsed[2]}


def _maestro_corners_readback(client, session: str) -> list[str]:
    readback = client.execute_skill(
        f'maeGetSetup(?typeName "corners" ?enabled t ?session "{session}")',
        timeout=30,
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro corner readback failed: {errors[0]}")
    raw_corners = str(getattr(readback, "output", "") or "")
    return re.findall(r'"([^"\\]+)"', raw_corners)


def _maestro_all_corners_readback(client, session: str) -> list[str]:
    readback = client.execute_skill(
        f'maeGetSetup(?typeName "corners" ?session "{session}")',
        timeout=30,
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro all-corner readback failed: {errors[0]}")
    raw_corners = str(getattr(readback, "output", "") or "")
    return re.findall(r'"([^"\\]+)"', raw_corners)


def _normalized_maestro_variable_value(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if normalized in {"", "nil"}:
        return None
    if (
        len(normalized) >= 2
        and normalized.startswith('"')
        and normalized.endswith('"')
    ):
        normalized = normalized[1:-1]
    return normalized


def _validate_maestro_variable_update(update: dict[str, Any]) -> None:
    name = str(update.get("name") or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
        raise RuntimeError(f"invalid Maestro variable name: {name!r}")
    if "expected_value" not in update:
        raise RuntimeError(
            f"Maestro variable {name}.expected_value must be explicitly declared"
        )
    scope = str(update.get("scope") or "global")
    scope_name = update.get("scope_name")
    if scope not in {"global", "test", "corner"}:
        raise RuntimeError(f"invalid Maestro variable scope: {scope!r}")
    if scope == "global" and scope_name is not None:
        raise RuntimeError("global Maestro variables cannot declare scope_name")
    if scope != "global":
        if not isinstance(scope_name, str) or not scope_name:
            raise RuntimeError("test/corner Maestro variables require scope_name")
        if len(scope_name) > 128:
            raise RuntimeError("Maestro variable scope_name exceeds 128 characters")
        if any(
            character in ('"', "\\") or ord(character) < 32 or ord(character) == 127
            for character in scope_name
        ):
            raise RuntimeError("Maestro variable scope_name is unsafe")
    for field in ("expected_value", "value"):
        value = update.get(field)
        if value is None and field == "expected_value":
            continue
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"Maestro variable {name}.{field} must be a string")
        if any(
            character in ('"', "\\") or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise RuntimeError(
                f"Maestro variable {name}.{field} contains unsafe SKILL string "
                "characters"
            )


def _maestro_variable_identity(update: dict[str, Any]) -> str:
    scope = str(update.get("scope") or "global")
    name = str(update["name"])
    if scope == "global":
        return name
    return f"{scope}:{update['scope_name']}:{name}"


def _maestro_named_corner_handle(client, scope_name: str, *, session: str) -> str:
    """Return one real named-corner handle, rejecting nominal/unknown selectors.

    Maestro's built-in nominal row is included in ``maeGetSetup`` corner
    membership but is not a regular ``axlGetCorner`` object.  Keep global
    values for that row and use this helper only for explicitly named corners.
    Splitting handle lookup from variable lookup also prevents an invalid
    corner handle from being mistaken for an absent variable.
    """

    readback = client.execute_skill(
        "let((sdb corner) "
        f'sdb=axlGetMainSetupDB({json.dumps(session)}) '
        f'corner=axlGetCorner(sdb {json.dumps(scope_name)}) '
        "list(sdb corner))",
        timeout=30,
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro named-corner handle readback failed for {scope_name}: "
            f"{errors[0]}"
        )
    handles = _parse_skill_sexpr(getattr(readback, "output", ""))
    if (
        not isinstance(handles, list)
        or len(handles) != 2
        or handles[0] in {None, "0"}
        or handles[1] in {None, "0"}
        or not all(str(handle).isdigit() for handle in handles)
    ):
        raise RuntimeError(
            f"Maestro corner scope {scope_name!r} is not an addressable named "
            f"corner: {handles!r}; use the global scope for the built-in "
            "Nominal row"
        )
    return str(handles[1])


def _read_maestro_variable(
    client, get_var, update: dict[str, Any], *, session: str
) -> str | None:
    scope = str(update.get("scope") or "global")
    name = str(update["name"])
    if scope == "global":
        raw = get_var(client, name, session=session)
    elif scope == "test":
        scope_name = str(update["scope_name"])
        readback = client.execute_skill(
            f'maeGetVar("{name}" ?typeName "test" '
            f'?typeValue {json.dumps(scope_name)} ?session "{session}")',
            timeout=30,
        )
        errors = getattr(readback, "errors", None) or []
        if errors:
            raise RuntimeError(
                f"Maestro variable readback failed for "
                f"{_maestro_variable_identity(update)}: {errors[0]}"
            )
        raw = getattr(readback, "output", "")
    else:
        scope_name = str(update["scope_name"])
        corner_handle = _maestro_named_corner_handle(
            client, scope_name, session=session
        )
        variable_readback = client.execute_skill(
            f'axlGetVar({corner_handle} {json.dumps(name)})',
            timeout=30,
        )
        errors = getattr(variable_readback, "errors", None) or []
        if errors:
            raise RuntimeError(
                f"Maestro corner variable handle readback failed for "
                f"{_maestro_variable_identity(update)}: {errors[0]}"
            )
        variable_handle = _parse_skill_sexpr(
            getattr(variable_readback, "output", "")
        )
        if variable_handle in {None, "0"}:
            raw = "nil"
        elif not str(variable_handle).isdigit():
            raise RuntimeError(
                f"Maestro corner variable handle readback was malformed for "
                f"{_maestro_variable_identity(update)}: {variable_handle!r}"
            )
        else:
            value_readback = client.execute_skill(
                f"axlGetVarValue({variable_handle})",
                timeout=30,
            )
            errors = getattr(value_readback, "errors", None) or []
            if errors:
                raise RuntimeError(
                    f"Maestro variable value readback failed for "
                    f"{_maestro_variable_identity(update)}: {errors[0]}"
                )
            raw = getattr(value_readback, "output", "")
    return _normalized_maestro_variable_value(raw)


def _write_maestro_variable(
    client, set_var, update: dict[str, Any], *, session: str
) -> None:
    scope = str(update.get("scope") or "global")
    if scope == "corner":
        scope_name = str(update["scope_name"])
        name = str(update["name"])
        value = str(update["value"])
        corner_handle = _maestro_named_corner_handle(
            client, scope_name, session=session
        )
        writeback = client.execute_skill(
            "let((variable) "
            f'variable=axlPutVar({corner_handle} {json.dumps(name)} '
            f'{json.dumps(value)}) '
            "if(variable list(variable axlGetVarValue(variable)) nil))",
            timeout=30,
        )
        errors = getattr(writeback, "errors", None) or []
        if errors:
            raise RuntimeError(
                f"Maestro corner variable write failed for "
                f"{_maestro_variable_identity(update)}: {errors[0]}"
            )
        parsed = _parse_skill_sexpr(getattr(writeback, "output", ""))
        if (
            not isinstance(parsed, list)
            or len(parsed) != 2
            or parsed[0] in {None, "0"}
            or _normalized_maestro_variable_value(parsed[1]) != value
        ):
            raise RuntimeError(
                f"Maestro corner variable write did not return the requested "
                f"value for {_maestro_variable_identity(update)}: {parsed!r}"
            )
        return
    kwargs: dict[str, str] = {"session": session}
    if scope != "global":
        scope_name = str(update["scope_name"])
        kwargs.update(
            type_name=scope,
            type_value=f'("{scope_name}")',
        )
    set_var(client, str(update["name"]), str(update["value"]), **kwargs)


def _maestro_variable_fingerprint(
    tests: list[str],
    corners: list[str] | None,
    values: dict[str, str | None],
    global_selections: dict[str, Any] | None = None,
) -> str:
    canonical = json.dumps(
        {
            "tests": tests,
            "corners": corners,
            "declared_variables": values,
            "global_variable_selections": global_selections,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _maestro_global_variable_selection_state(
    client, *, session: str
) -> dict[str, Any]:
    """Read the complete enabled/disabled global-variable selector sets."""

    readback = client.execute_skill(
        "list("
        f'maeGetSetup(?typeName "variables" ?enabled t ?session "{session}") '
        f'maeGetSetup(?typeName "variables" ?enabled nil ?session "{session}"))',
        timeout=30,
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro global-variable selection readback failed: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise RuntimeError(
            f"invalid Maestro global-variable selection readback: {parsed!r}"
        )

    def names(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise RuntimeError(
                f"invalid Maestro global-variable selection list: {value!r}"
            )
        return sorted(value)

    enabled = names(parsed[0])
    disabled = names(parsed[1])
    overlap = sorted(set(enabled) & set(disabled))
    if overlap:
        raise RuntimeError(
            f"Maestro global variables appeared enabled and disabled: {overlap!r}"
        )
    return {"enabled": enabled, "disabled": disabled}


def _maestro_global_selection_values(
    state: dict[str, Any], names: list[str]
) -> dict[str, bool]:
    enabled = set(state.get("enabled") or [])
    disabled = set(state.get("disabled") or [])
    values: dict[str, bool] = {}
    for name in names:
        membership = int(name in enabled) + int(name in disabled)
        if membership != 1:
            raise RuntimeError(
                f"Maestro global variable {name!r} did not have one selection state"
            )
        values[name] = name in enabled
    return values


def _write_maestro_global_selection(
    client, name: str, enabled: bool, *, session: str
) -> None:
    enabled_skill = "t" if enabled else "nil"
    readback = client.execute_skill(
        f'maeSetSetup(?variables \'("{name}") ?enabled {enabled_skill} '
        f'?session "{session}")',
        timeout=30,
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro global-variable selection write failed for {name}: {errors[0]}"
        )


def _read_maestro_sweep_setup(
    client,
    get_var,
    verification: dict[str, Any],
    *,
    session: str,
) -> dict[str, Any]:
    """Read the exact saved variable scopes declared by a sweep Gate."""

    expected_tests = [str(value) for value in verification.get("expected_tests") or []]
    variables = list(verification.get("variables") or [])
    if not expected_tests or not variables:
        raise RuntimeError(
            "ADE sweep verification requires expected_tests and variables"
        )
    tests = _maestro_tests_readback(client, session)
    if tests != expected_tests:
        raise RuntimeError(
            "Maestro tests changed before sweep execution: "
            f"expected {expected_tests!r}, got {tests!r}"
        )
    expected_corners_raw = verification.get("expected_corners")
    expected_corners = (
        None
        if expected_corners_raw is None
        else [str(value) for value in expected_corners_raw]
    )
    corners = (
        _maestro_corners_readback(client, session)
        if expected_corners is not None
        else None
    )
    if corners != expected_corners:
        raise RuntimeError(
            "Maestro corners changed before sweep execution: "
            f"expected {expected_corners!r}, got {corners!r}"
        )

    values: dict[str, str | None] = {}
    methods: dict[str, str] = {}
    for variable in variables:
        if not isinstance(variable, dict):
            raise RuntimeError("ADE sweep variable expectation must be an object")
        expectation = {
            **variable,
            "value": variable.get("expected_value"),
        }
        _validate_maestro_variable_update(expectation)
        identity = _maestro_variable_identity(variable)
        if identity in values:
            raise RuntimeError(
                f"ADE sweep verification repeats variable identity {identity}"
            )
        values[identity] = _read_maestro_variable(
            client, get_var, variable, session=session
        )
        expected_value = variable.get("expected_value")
        if values[identity] != expected_value:
            raise RuntimeError(
                f"Maestro sweep variable mismatch for {identity}: expected "
                f"{expected_value!r}, got {values[identity]!r}"
            )
        scope = str(variable.get("scope") or "global")
        methods[identity] = (
            "bridge_public_get_var"
            if scope == "global"
            else (
                "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel"
                if scope == "test"
                else (
                    "cadence_axlGetCorner_axlGetVarValue_"
                    "via_bridge_skill_channel"
                )
            )
        )
    expected_selections_raw = verification.get(
        "expected_global_variable_selections"
    )
    if expected_selections_raw is None:
        expected_selections_raw = {}
    if not isinstance(expected_selections_raw, dict) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(enabled, bool)
        for name, enabled in expected_selections_raw.items()
    ):
        raise RuntimeError(
            "ADE sweep expected global-variable selections must be a "
            "name-to-boolean object"
        )
    expected_selections = {
        str(name): bool(enabled)
        for name, enabled in expected_selections_raw.items()
    }
    selection_state: dict[str, Any] | None = None
    selection_values: dict[str, bool] = {}
    if expected_selections:
        selection_state = _maestro_global_variable_selection_state(
            client, session=session
        )
        selection_values = _maestro_global_selection_values(
            selection_state, list(expected_selections)
        )
        if selection_values != expected_selections:
            raise RuntimeError(
                "Maestro global-variable selections changed before sweep "
                f"execution: expected {expected_selections!r}, got "
                f"{selection_values!r}"
            )

    readback = {
        "tests": tests,
        "corners": corners,
        "variables": values,
        "variable_readback_methods": methods,
        "fingerprint_sha256": _maestro_variable_fingerprint(
            tests, corners, values, selection_state
        ),
    }
    if expected_selections:
        readback.update(
            {
                "global_variable_selections": selection_values,
                "global_variable_selection_state": selection_state,
                "global_variable_selection_readback_method": (
                    "cadence_maeGetSetup_enabled_variables_"
                    "via_bridge_skill_channel"
                ),
            }
        )
    return readback


def prepare_maestro(payload: dict[str, Any]) -> dict[str, Any]:
    """Create one new persistent Maestro view and leave it for manual editing."""

    from virtuoso_bridge.virtuoso.maestro import (
        close_session,
        create_test,
        open_session,
        save_setup,
    )

    settings = payload.get("ade_prepare") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    target_view = str(payload["target"].get("view") or "")
    if target_view != "maestro":
        raise RuntimeError("ADE prepare target view must be maestro")
    requested_design = settings.get("design") or {}
    design_library = str(requested_design.get("library") or library)
    design_cell = str(requested_design.get("cell") or cell)
    design_view = str(
        requested_design.get("view") or settings.get("design_view") or "schematic"
    )
    design = {
        "library": design_library,
        "cell": design_cell,
        "view": design_view,
    }
    test_name = str(settings.get("test_name") or "VDA")
    simulator = str(settings.get("simulator") or "spectre")
    if simulator != "spectre":
        raise RuntimeError("ADE prepare currently supports only the Spectre simulator")

    client = _client()
    if not _cellview_exists(client, design_library, design_cell, design_view):
        raise RuntimeError(
            "ADE prepare requires existing design "
            f"{design_library}/{design_cell}/{design_view}"
        )
    if _cellview_exists(client, library, cell, "maestro"):
        raise RuntimeError(
            f"refusing to modify existing Maestro view {library}/{cell}/maestro"
        )

    session = open_session(client, library, cell)
    try:
        create_test(
            client,
            test_name,
            lib=design_library,
            cell=design_cell,
            view=design_view,
            simulator=simulator,
            session=session,
        )
        save_setup(client, library, cell, session=session)
    finally:
        close_session(client, session)

    if not _cellview_exists(client, library, cell, "maestro"):
        raise RuntimeError("Maestro save returned without a persistent view")

    verify_session = open_session(client, library, cell)
    try:
        tests = _maestro_tests_readback(client, verify_session)
        if tests != [test_name]:
            raise RuntimeError(
                "persistent Maestro setup did not read back exactly the requested "
                f"test {test_name!r}; got {tests!r}"
            )
        persisted_design = _maestro_test_design_readback(
            client, test_name, session=verify_session
        )
        if persisted_design != design:
            raise RuntimeError(
                "persistent Maestro test design did not read back exactly the "
                f"requested target: expected {design!r}, got {persisted_design!r}"
            )
    finally:
        close_session(client, verify_session)

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": "maestro"},
        "design": design,
        "design_readback": persisted_design,
        "design_target_confirmed": True,
        "test_name": test_name,
        "simulator_requested": simulator,
        "requested_setup_evidence_source": "user_input",
        "persistent_view_confirmed": True,
        "tests_readback": tests,
        "confirmed_setup_evidence_source": "bridge_readback",
        "existing_maestro_overwritten": False,
        "schematic_oa_write_performed": False,
        "maestro_oa_write_performed": True,
        "configured_analyses": [],
        "configured_sweeps": [],
        "configured_outputs": [],
        "completion_scope": (
            "persistent Spectre-backed Maestro test prepared for manual editing; "
            "no analysis, stimulus, sweep, output, or simulation was configured"
        ),
    }


def apply_maestro_variables(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare-and-swap declared variable scopes in one saved Maestro setup."""

    from virtuoso_bridge.virtuoso.maestro import (
        close_session,
        find_open_session,
        get_var,
        open_session,
        save_setup,
        set_var,
    )

    settings = payload.get("ade_variables") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    view = str(payload["target"].get("view") or "")
    if view != "maestro":
        raise RuntimeError("ADE variable patch target view must be maestro")
    expected_tests = [str(value) for value in settings.get("expected_tests") or []]
    expected_corners_raw = settings.get("expected_corners")
    expected_corners = (
        None
        if expected_corners_raw is None
        else [str(value) for value in expected_corners_raw]
    )
    updates = list(settings.get("updates") or [])
    selection_updates = list(settings.get("global_selection_updates") or [])
    if not expected_tests or (not updates and not selection_updates):
        raise RuntimeError(
            "ADE variable patch requires expected_tests and declared changes"
        )
    if len(expected_tests) != len(set(expected_tests)):
        raise RuntimeError("ADE variable patch expected_tests contain duplicates")
    if expected_corners is not None:
        if not expected_corners:
            raise RuntimeError("ADE variable patch expected_corners cannot be empty")
        if len(expected_corners) != len(set(expected_corners)):
            raise RuntimeError("ADE variable patch expected_corners contain duplicates")
    for item_type, values in (
        ("test", expected_tests),
        ("corner", expected_corners or []),
    ):
        for value in values:
            if (
                not value
                or len(value) > 128
                or any(
                    character in ('"', "\\")
                    or ord(character) < 32
                    or ord(character) == 127
                    for character in value
                )
            ):
                raise RuntimeError(f"invalid Maestro {item_type} name: {value!r}")
    for update in updates:
        if not isinstance(update, dict):
            raise RuntimeError("ADE variable update must be an object")
        _validate_maestro_variable_update(update)
        scope = str(update.get("scope") or "global")
        scope_name = update.get("scope_name")
        if scope == "test" and scope_name not in expected_tests:
            raise RuntimeError(
                "test-scoped Maestro variable must target one of expected_tests"
            )
        if scope == "corner" and (
            expected_corners is None or scope_name not in expected_corners
        ):
            raise RuntimeError(
                "corner-scoped Maestro variable must target one of expected_corners"
            )
    identities = [_maestro_variable_identity(update) for update in updates]
    if len(identities) != len(set(identities)):
        raise RuntimeError(
            "ADE variable patch contains duplicate scoped variable identities"
        )
    selection_names: list[str] = []
    for update in selection_updates:
        if not isinstance(update, dict):
            raise RuntimeError(
                "ADE global variable selection update must be an object"
            )
        name = str(update.get("name") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
            raise RuntimeError(f"invalid Maestro global variable name: {name!r}")
        if not isinstance(update.get("expected_enabled"), bool) or not isinstance(
            update.get("enabled"), bool
        ):
            raise RuntimeError(
                "Maestro global variable selection states must be booleans"
            )
        if update["expected_enabled"] == update["enabled"]:
            raise RuntimeError(
                "Maestro global variable selection update must change state"
            )
        selection_names.append(name)
    if len(selection_names) != len(set(selection_names)):
        raise RuntimeError(
            "ADE global variable selection updates contain duplicate names"
        )

    client = _client()
    if not _cellview_exists(client, library, cell, view):
        raise RuntimeError(
            f"ADE variable patch requires existing {library}/{cell}/{view}"
        )
    existing_session = find_open_session(client)
    if existing_session is not None:
        raise RuntimeError(
            "ADE variable patch refuses to save while any configured Maestro "
            f"session is already open: {existing_session}"
        )

    before: dict[str, str | None] = {}
    immediate: dict[str, str | None] = {}
    selection_before: dict[str, bool] = {}
    selection_immediate: dict[str, bool] = {}
    selection_state_before: dict[str, Any] | None = None
    selection_state_immediate: dict[str, Any] | None = None
    session = open_session(client, library, cell)
    try:
        tests = _maestro_tests_readback(client, session)
        if tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed before variable patch: "
                f"expected {expected_tests!r}, got {tests!r}"
            )
        corners = (
            _maestro_corners_readback(client, session)
            if expected_corners is not None
            else None
        )
        if corners != expected_corners:
            raise RuntimeError(
                "Maestro corners changed before variable patch: "
                f"expected {expected_corners!r}, got {corners!r}"
            )
        for update in updates:
            identity = _maestro_variable_identity(update)
            before[identity] = _read_maestro_variable(
                client, get_var, update, session=session
            )
        mismatches = {
            _maestro_variable_identity(update): {
                "expected": update.get("expected_value"),
                "actual": before[_maestro_variable_identity(update)],
            }
            for update in updates
            if before[_maestro_variable_identity(update)]
            != update.get("expected_value")
        }
        if mismatches:
            raise RuntimeError(
                "Maestro variable precondition mismatch: "
                + json.dumps(mismatches, sort_keys=True)
            )
        if selection_names:
            selection_state_before = _maestro_global_variable_selection_state(
                client, session=session
            )
            selection_before = _maestro_global_selection_values(
                selection_state_before, selection_names
            )
            selection_mismatches = {
                update["name"]: {
                    "expected": update["expected_enabled"],
                    "actual": selection_before[str(update["name"])],
                }
                for update in selection_updates
                if selection_before[str(update["name"])]
                != update["expected_enabled"]
            }
            if selection_mismatches:
                raise RuntimeError(
                    "Maestro global variable selection precondition mismatch: "
                    + json.dumps(selection_mismatches, sort_keys=True)
                )
        for update in updates:
            identity = _maestro_variable_identity(update)
            requested = str(update["value"])
            _write_maestro_variable(client, set_var, update, session=session)
            immediate[identity] = _read_maestro_variable(
                client, get_var, update, session=session
            )
            if immediate[identity] != requested:
                raise RuntimeError(
                    f"Maestro variable immediate readback mismatch for {identity}: "
                    f"requested {requested!r}, got {immediate[identity]!r}"
                )
        for update in selection_updates:
            _write_maestro_global_selection(
                client,
                str(update["name"]),
                bool(update["enabled"]),
                session=session,
            )
            selection_state_immediate = _maestro_global_variable_selection_state(
                client, session=session
            )
            selection_immediate = _maestro_global_selection_values(
                selection_state_immediate, selection_names
            )
            if selection_immediate[str(update["name"])] != update["enabled"]:
                raise RuntimeError(
                    "Maestro global variable selection immediate readback mismatch "
                    f"for {update['name']}"
                )
        if selection_state_before is not None and selection_state_immediate is not None:
            before_other = {
                name: name in set(selection_state_before["enabled"])
                for name in set(selection_state_before["enabled"])
                | set(selection_state_before["disabled"])
                if name not in selection_names
            }
            immediate_other = {
                name: name in set(selection_state_immediate["enabled"])
                for name in set(selection_state_immediate["enabled"])
                | set(selection_state_immediate["disabled"])
                if name not in selection_names
            }
            if before_other != immediate_other:
                raise RuntimeError(
                    "Maestro global variable selection changed undeclared names"
                )
        save_setup(client, library, cell, session=session)
    finally:
        close_session(client, session)

    persisted: dict[str, str | None] = {}
    selection_persisted: dict[str, bool] = {}
    selection_state_persisted: dict[str, Any] | None = None
    verify_session = open_session(client, library, cell)
    try:
        verify_tests = _maestro_tests_readback(client, verify_session)
        if verify_tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed after variable patch: "
                f"expected {expected_tests!r}, got {verify_tests!r}"
            )
        verify_corners = (
            _maestro_corners_readback(client, verify_session)
            if expected_corners is not None
            else None
        )
        if verify_corners != expected_corners:
            raise RuntimeError(
                "Maestro corners changed after variable patch: "
                f"expected {expected_corners!r}, got {verify_corners!r}"
            )
        for update in updates:
            identity = _maestro_variable_identity(update)
            persisted[identity] = _read_maestro_variable(
                client, get_var, update, session=verify_session
            )
            if persisted[identity] != update["value"]:
                raise RuntimeError(
                    f"Maestro variable persistent readback mismatch for {identity}: "
                    f"requested {update['value']!r}, got {persisted[identity]!r}"
                )
        if selection_names:
            selection_state_persisted = _maestro_global_variable_selection_state(
                client, session=verify_session
            )
            selection_persisted = _maestro_global_selection_values(
                selection_state_persisted, selection_names
            )
            expected_selection_after = {
                str(update["name"]): bool(update["enabled"])
                for update in selection_updates
            }
            if selection_persisted != expected_selection_after:
                raise RuntimeError(
                    "Maestro global variable selection persistent readback mismatch"
                )
            if selection_state_persisted != selection_state_immediate:
                raise RuntimeError(
                    "Maestro global variable selection full set changed after reopen"
                )
    finally:
        close_session(client, verify_session)

    requested = {
        _maestro_variable_identity(update): {
            "name": str(update["name"]),
            "scope": str(update.get("scope") or "global"),
            "scope_name": update.get("scope_name"),
            "expected_value": update.get("expected_value"),
            "value": str(update["value"]),
        }
        for update in updates
    }
    variable_scopes = list(
        dict.fromkeys(str(update.get("scope") or "global") for update in updates)
    )
    requested_selections = {
        str(update["name"]): {
            "expected_enabled": bool(update["expected_enabled"]),
            "enabled": bool(update["enabled"]),
        }
        for update in selection_updates
    }
    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "variable_scope": (
            "none"
            if not variable_scopes
            else ("global" if variable_scopes == ["global"] else "declared_scopes")
        ),
        "variable_scopes": variable_scopes,
        "expected_tests": expected_tests,
        "tests_readback_before": tests,
        "tests_readback_after": verify_tests,
        "expected_corners": expected_corners,
        "corners_readback_before": corners,
        "corners_readback_after": verify_corners,
        "requested_variable_updates": requested,
        "requested_global_selection_updates": requested_selections,
        "requested_evidence_source": "user_input",
        "before_variables": before,
        "immediate_variables": immediate,
        "persisted_variables": persisted,
        "global_variable_selection_before": selection_before,
        "global_variable_selection_immediate": selection_immediate,
        "global_variable_selection_persisted": selection_persisted,
        "global_variable_selection_state_before": selection_state_before,
        "global_variable_selection_state_immediate": selection_state_immediate,
        "global_variable_selection_state_persisted": selection_state_persisted,
        "global_variable_selection_readback_method": (
            "cadence_maeGetSetup_enabled_variables_via_bridge_skill_channel"
            if selection_names
            else None
        ),
        "global_variable_selection_write_method": (
            "cadence_maeSetSetup_variables_via_bridge_skill_channel"
            if selection_names
            else None
        ),
        "global_variable_selection_preserved_undeclared": bool(selection_names),
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": _maestro_variable_fingerprint(
            tests, corners, before, selection_state_before
        ),
        "after_target_fingerprint_sha256": _maestro_variable_fingerprint(
            verify_tests, verify_corners, persisted, selection_state_persisted
        ),
        "declared_global_sweep_variables": [
            _maestro_variable_identity(update)
            for update in updates
            if str(update.get("scope") or "global") == "global"
            and persisted[_maestro_variable_identity(update)]
            and "," in str(persisted[_maestro_variable_identity(update)])
        ],
        "declared_sweep_variables": [
            identity
            for identity, value in persisted.items()
            if value and "," in value
        ],
        "sweep_detection_evidence_source": "software_inference",
        "declared_scoped_values_verified": True,
        "declared_global_selections_verified": True,
        "variable_readback_methods": {
            scope: (
                "bridge_public_get_var"
                if scope == "global"
                else (
                    "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel"
                    if scope == "test"
                    else (
                        "cadence_axlGetCorner_axlGetVarValue_"
                        "via_bridge_skill_channel"
                    )
                )
            )
            for scope in variable_scopes
        },
        "variable_write_methods": {
            scope: (
                "bridge_public_set_var_global"
                if scope == "global"
                else (
                    "bridge_public_set_var_list_typeValue"
                    if scope == "test"
                    else "cadence_axlPutVar_via_bridge_skill_channel"
                )
            )
            for scope in variable_scopes
        },
        "test_or_corner_overrides_checked": False,
        "unlisted_scope_overrides_checked": False,
        "effective_simulation_value_verified": False,
        "existing_maestro_replaced": False,
        "schematic_oa_write_performed": False,
        "maestro_setup_write_performed": True,
        "automated_simulation_performed": False,
        "completion_scope": (
            "declared Maestro variable scopes and global selections matched their "
            "expected old states, were saved once, and matched after an independent "
            "reopen; tests and "
            "declared corner membership were preserved; analysis, outputs, and "
            "schematic were not changed; unlisted scope overrides and effective "
            "simulator values were not verified"
        ),
    }


def _maestro_corner_membership_fingerprint(
    tests: list[str], all_corners: list[str], enabled_corners: list[str]
) -> str:
    canonical = json.dumps(
        {
            "tests": tests,
            "all_corners": all_corners,
            "enabled_corners": enabled_corners,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def apply_maestro_corners(payload: dict[str, Any]) -> dict[str, Any]:
    """Add enabled Maestro corners after exact membership preconditions."""

    from virtuoso_bridge.virtuoso.maestro import (
        close_session,
        find_open_session,
        open_session,
        save_setup,
        set_corner,
    )

    settings = payload.get("ade_corners") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    view = str(payload["target"].get("view") or "")
    if view != "maestro":
        raise RuntimeError("ADE corner patch target view must be maestro")
    expected_tests = [str(value) for value in settings.get("expected_tests") or []]
    expected_corners = [
        str(value) for value in settings.get("expected_corners") or []
    ]
    additions = list(settings.get("additions") or [])
    if not expected_tests or not additions:
        raise RuntimeError(
            "ADE corner patch requires expected_tests and corner additions"
        )
    for label, values in (
        ("test", expected_tests),
        ("corner", expected_corners),
    ):
        if len(values) != len(set(values)):
            raise RuntimeError(
                f"ADE corner patch expected_{label}s contain duplicates"
            )
        for value in values:
            if (
                not value
                or len(value) > 128
                or any(
                    character in ('"', "\\")
                    or ord(character) < 32
                    or ord(character) == 127
                    for character in value
                )
            ):
                raise RuntimeError(f"invalid Maestro {label} name: {value!r}")
    addition_names: list[str] = []
    for addition in additions:
        if not isinstance(addition, dict):
            raise RuntimeError("ADE corner addition must be an object")
        name = addition.get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 128
            or any(
                character in ('"', "\\")
                or ord(character) < 32
                or ord(character) == 127
                for character in name
            )
        ):
            raise RuntimeError(f"invalid Maestro corner addition name: {name!r}")
        addition_names.append(name)
    if len(addition_names) != len(set(addition_names)):
        raise RuntimeError("ADE corner patch contains duplicate additions")
    overlap = sorted(set(addition_names) & set(expected_corners))
    if overlap:
        raise RuntimeError(
            f"ADE corner additions already exist in expected_corners: {overlap}"
        )

    client = _client()
    if not _cellview_exists(client, library, cell, view):
        raise RuntimeError(
            f"ADE corner patch requires existing {library}/{cell}/{view}"
        )
    existing_session = find_open_session(client)
    if existing_session is not None:
        raise RuntimeError(
            "ADE corner patch refuses to save while any configured Maestro "
            f"session is already open: {existing_session}"
        )

    expected_before = list(expected_corners)
    requested_after = [*expected_corners, *addition_names]
    immediate_states: list[dict[str, Any]] = []
    session = open_session(client, library, cell)
    try:
        tests = _maestro_tests_readback(client, session)
        if tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed before corner patch: "
                f"expected {expected_tests!r}, got {tests!r}"
            )
        all_before = _maestro_all_corners_readback(client, session)
        enabled_before = _maestro_corners_readback(client, session)
        if all_before != expected_before or enabled_before != expected_before:
            raise RuntimeError(
                "Maestro corner membership precondition mismatch: "
                f"expected all/enabled {expected_before!r}, got "
                f"all={all_before!r}, enabled={enabled_before!r}"
            )
        for name in addition_names:
            set_corner(client, name, session=session)
            all_now = _maestro_all_corners_readback(client, session)
            enabled_now = _maestro_corners_readback(client, session)
            expected_now = [
                *expected_corners,
                *addition_names[: len(immediate_states) + 1],
            ]
            if all_now != expected_now or enabled_now != expected_now:
                raise RuntimeError(
                    f"Maestro corner immediate readback mismatch after {name!r}: "
                    f"expected {expected_now!r}, got all={all_now!r}, "
                    f"enabled={enabled_now!r}"
                )
            immediate_states.append(
                {
                    "name": name,
                    "all_corners": all_now,
                    "enabled_corners": enabled_now,
                }
            )
        save_setup(client, library, cell, session=session)
    finally:
        close_session(client, session)

    verify_session = open_session(client, library, cell)
    try:
        verify_tests = _maestro_tests_readback(client, verify_session)
        all_after = _maestro_all_corners_readback(client, verify_session)
        enabled_after = _maestro_corners_readback(client, verify_session)
        if verify_tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed after corner patch: "
                f"expected {expected_tests!r}, got {verify_tests!r}"
            )
        if all_after != requested_after or enabled_after != requested_after:
            raise RuntimeError(
                "Maestro corner persistent readback mismatch: "
                f"expected {requested_after!r}, got all={all_after!r}, "
                f"enabled={enabled_after!r}"
            )
    finally:
        close_session(client, verify_session)

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "expected_tests": expected_tests,
        "tests_readback_before": tests,
        "tests_readback_after": verify_tests,
        "expected_corners_before": expected_before,
        "requested_corner_additions": addition_names,
        "all_corners_readback_before": all_before,
        "enabled_corners_readback_before": enabled_before,
        "immediate_corner_states": immediate_states,
        "all_corners_readback_after": all_after,
        "enabled_corners_readback_after": enabled_after,
        "requested_evidence_source": "user_input",
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": (
            _maestro_corner_membership_fingerprint(
                tests, all_before, enabled_before
            )
        ),
        "after_target_fingerprint_sha256": (
            _maestro_corner_membership_fingerprint(
                verify_tests, all_after, enabled_after
            )
        ),
        "corner_write_method": "bridge_public_set_corner",
        "corner_readback_method": (
            "cadence_maeGetSetup_all_and_enabled_via_bridge_skill_channel"
        ),
        "existing_corners_modified": False,
        "existing_maestro_replaced": False,
        "model_files_modified": False,
        "variables_modified": False,
        "analyses_or_outputs_modified": False,
        "schematic_oa_write_performed": False,
        "maestro_setup_write_performed": True,
        "automated_simulation_performed": False,
        "completion_scope": (
            "exact tests and all/enabled corner membership matched before the "
            "first writer; declared absent corners were added with Bridge public "
            "set_corner, saved once, and matched after an independent reopen; "
            "models, variables, analyses, outputs, and schematic were not changed"
        ),
    }


def _parse_skill_sexpr(raw: str) -> Any:
    """Parse the small SKILL value subset used by Maestro setup readback."""

    text = str(raw or "").strip()
    index = 0

    def skip_space() -> None:
        nonlocal index
        while index < len(text) and text[index].isspace():
            index += 1

    def parse_value() -> Any:
        nonlocal index
        skip_space()
        if index >= len(text):
            raise RuntimeError("truncated SKILL value")
        if text[index] == "(":
            index += 1
            values: list[Any] = []
            while True:
                skip_space()
                if index >= len(text):
                    raise RuntimeError("unterminated SKILL list")
                if text[index] == ")":
                    index += 1
                    return values
                values.append(parse_value())
        if text[index] == '"':
            index += 1
            characters: list[str] = []
            while index < len(text):
                character = text[index]
                index += 1
                if character == '"':
                    return "".join(characters)
                if character == "\\":
                    if index >= len(text):
                        raise RuntimeError("truncated SKILL string escape")
                    escaped = text[index]
                    index += 1
                    decoded = {
                        '"': '"',
                        "\\": "\\",
                        "n": "\n",
                        "r": "\r",
                        "t": "\t",
                    }.get(escaped)
                    if decoded is None:
                        characters.extend(("\\", escaped))
                    else:
                        characters.append(decoded)
                else:
                    characters.append(character)
            raise RuntimeError("unterminated SKILL string")
        start = index
        while (
            index < len(text)
            and not text[index].isspace()
            and text[index] not in "()"
        ):
            index += 1
        atom = text[start:index]
        if atom == "nil":
            return None
        if atom == "t":
            return True
        return atom

    if not text:
        return None
    parsed = parse_value()
    skip_space()
    if index != len(text):
        raise RuntimeError(f"unexpected trailing SKILL value: {text[index:]!r}")
    return parsed


def _maestro_analysis_state(
    client, test: str, analysis: str, *, session: str
) -> dict[str, Any] | None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    escaped_test = escape_skill_string(test)
    escaped_analysis = escape_skill_string(analysis)
    escaped_session = escape_skill_string(session)
    expression = (
        "list("
        f'if(member("{escaped_analysis}" '
        f'maeGetEnabledAnalysis("{escaped_test}" ?session "{escaped_session}")) '
        "t nil) "
        f'maeGetAnalysis("{escaped_test}" "{escaped_analysis}" '
        f'?session "{escaped_session}"))'
    )
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro analysis readback failed for {test}/{analysis}: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise RuntimeError(
            f"invalid Maestro analysis readback for {test}/{analysis}: {parsed!r}"
        )
    enabled = parsed[0] is True
    raw_options = parsed[1]
    if raw_options is None:
        if enabled:
            raise RuntimeError(
                f"enabled Maestro analysis {test}/{analysis} returned no options"
            )
        return None
    if not isinstance(raw_options, list):
        raise RuntimeError(
            f"invalid Maestro analysis options for {test}/{analysis}: "
            f"{raw_options!r}"
        )
    options: dict[str, str | bool | None] = {}
    for pair in raw_options:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or isinstance(pair[1], list)
        ):
            raise RuntimeError(
                f"unsupported Maestro analysis option for {test}/{analysis}: "
                f"{pair!r}"
            )
        if pair[0] in options:
            raise RuntimeError(
                f"duplicate Maestro analysis option for {test}/{analysis}: "
                f"{pair[0]!r}"
            )
        options[pair[0]] = pair[1]
    return {"enabled": enabled, "options": options}


def _maestro_output_state(
    client, test: str, name: str, *, session: str
) -> dict[str, Any] | None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    escaped_test = escape_skill_string(test)
    escaped_name = escape_skill_string(name)
    escaped_session = escape_skill_string(session)
    expression = f'''
let((outs o sdb)
  outs = setof(item maeGetTestOutputs("{escaped_test}" ?session "{escaped_session}")
    item~>name == "{escaped_name}")
  if(length(outs) == 0
    then list(0)
    else if(length(outs) != 1
      then list(length(outs))
      else
        o = car(outs)
        sdb = axlGetMainSetupDB("{escaped_session}")
        list(1 o~>name o~>type o~>signal
          if(o~>expression sprintf(nil "%L" o~>expression) nil) o~>evalType
          o~>plot o~>save axlGetSpecData(sdb "{escaped_name}" "{escaped_test}"))
    )
  )
)
'''
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro output readback failed for {test}/{name}: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if not isinstance(parsed, list) or not parsed:
        raise RuntimeError(
            f"invalid Maestro output readback for {test}/{name}: {parsed!r}"
        )
    count = str(parsed[0])
    if count == "0":
        return None
    if count != "1" or len(parsed) != 9:
        raise RuntimeError(
            f"Maestro output {test}/{name} is ambiguous or malformed: {parsed!r}"
        )
    def normalized_symbol(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("'"):
            return value[1:]
        return value

    spec_raw = parsed[8]
    if spec_raw is None:
        spec = None
    else:
        if (
            isinstance(spec_raw, list)
            and len(spec_raw) == 1
            and isinstance(spec_raw[0], list)
        ):
            spec_raw = spec_raw[0]
        if (
            not isinstance(spec_raw, list)
            or len(spec_raw) != 2
            or not all(isinstance(value, str) for value in spec_raw)
        ):
            raise RuntimeError(
                f"unsupported Maestro output spec for {test}/{name}: {spec_raw!r}"
            )
        relation = normalized_symbol(spec_raw[0])
        if relation not in {"lt", "gt"}:
            raise RuntimeError(
                f"unsupported Maestro output spec relation for {test}/{name}: "
                f"{relation!r}"
            )
        spec = {"relation": relation, "value": spec_raw[1]}

    return {
        "name": parsed[1],
        "type": normalized_symbol(parsed[2]),
        "signal_name": parsed[3],
        "expression": parsed[4],
        "eval_type": normalized_symbol(parsed[5]),
        "plot": parsed[6],
        "save": parsed[7],
        "spec": spec,
    }


def _analysis_options_skill(options: dict[str, Any]) -> str:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    pairs: list[str] = []
    for name, value in options.items():
        escaped_name = escape_skill_string(str(name))
        if value is True:
            encoded = "t"
        elif value is False or value is None:
            encoded = "nil"
        else:
            encoded = f'"{escape_skill_string(str(value))}"'
        pairs.append(f'("{escaped_name}" {encoded})')
    return "(" + " ".join(pairs) + ")"


def _requested_analysis_matches(
    update: dict[str, Any], actual: dict[str, Any] | None
) -> bool:
    if actual is None or actual.get("enabled") != bool(update["enabled"]):
        return False
    options = actual.get("options")
    if not isinstance(options, dict):
        return False
    expected = update.get("expected")
    if isinstance(expected, dict):
        desired = dict(expected.get("options") or {})
        desired.update(update.get("options") or {})
        return options == desired
    return all(
        options.get(name) == value
        for name, value in update.get("options", {}).items()
    )


def _requested_output_matches(
    output: dict[str, Any], actual: dict[str, Any] | None
) -> bool:
    if actual is None or actual.get("name") != output.get("name"):
        return False
    output_type = str(output.get("output_type") or "")
    if output_type not in {actual.get("type"), actual.get("eval_type")}:
        return False
    if actual.get("signal_name") != output.get("signal_name"):
        return False
    if not calculator_expressions_equal(
        actual.get("expression"), output.get("expression")
    ):
        return False
    return actual.get("spec") == output.get("spec")


def _read_ade_result_mapping_setup(
    client, mapping: dict[str, Any], *, session: str
) -> dict[str, Any]:
    """Read and pin the exact saved scalar outputs consumed by result mapping."""

    metrics = mapping.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RuntimeError("ADE result_mapping requires metric bindings")
    rows: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for binding in metrics:
        if not isinstance(binding, dict):
            raise RuntimeError("ADE result metric binding must be an object")
        test = binding.get("test")
        output = binding.get("output")
        metric = binding.get("metric")
        expected_expression = binding.get("expected_expression")
        if not all(
            isinstance(value, str) and value
            for value in (test, output, metric, expected_expression)
        ):
            raise RuntimeError(
                "ADE result metric binding requires test/output/metric/"
                "expected_expression"
            )
        identity = (test, output)
        if identity in identities:
            raise RuntimeError("ADE result metric bindings contain duplicate outputs")
        identities.add(identity)
        state = _maestro_output_state(client, test, output, session=session)
        if (
            not isinstance(state, dict)
            or output != state.get("name")
            or "point" not in {state.get("type"), state.get("eval_type")}
            or state.get("signal_name") is not None
            or not calculator_expressions_equal(
                expected_expression, state.get("expression")
            )
        ):
            raise RuntimeError(
                f"ADE result mapping output {test}/{output} did not match its "
                "declared scalar calculator expression"
            )
        rows.append(
            {
                "test": test,
                "output": output,
                "metric": metric,
                "state": state,
            }
        )
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {"outputs": rows, "fingerprint_sha256": hashlib.sha256(payload).hexdigest()}


def _maestro_setup_patch_fingerprint(
    tests: list[str],
    analyses: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> str:
    canonical = json.dumps(
        {"tests": tests, "analyses": analyses, "outputs": outputs},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_maestro_analysis_options(
    options: Any, *, context: str
) -> dict[str, str | bool | None]:
    if not isinstance(options, dict):
        raise RuntimeError(f"{context} must be an object")
    validated: dict[str, str | bool | None] = {}
    for raw_name, value in options.items():
        name = str(raw_name)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
            raise RuntimeError(f"invalid Maestro analysis option name: {name!r}")
        if value is not None and not isinstance(value, (str, bool)):
            raise RuntimeError(
                f"Maestro analysis option {name!r} must be string, boolean, or null"
            )
        if isinstance(value, str) and (
            not value
            or len(value) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise RuntimeError(f"invalid Maestro analysis option value for {name!r}")
        validated[name] = value
    return validated


def _validate_maestro_analysis_update(update: dict[str, Any]) -> None:
    analysis = str(update.get("analysis") or "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", analysis):
        raise RuntimeError(f"invalid Maestro analysis name: {analysis!r}")
    if "expected" not in update or not isinstance(update.get("enabled"), bool):
        raise RuntimeError("ADE analysis update requires expected and enabled fields")
    options = _validate_maestro_analysis_options(
        update.get("options"), context="ADE analysis options"
    )
    expected = update.get("expected")
    if expected is None:
        if not update["enabled"]:
            raise RuntimeError("a new Maestro analysis must be enabled")
        return
    if not isinstance(expected, dict) or not isinstance(expected.get("enabled"), bool):
        raise RuntimeError(
            "ADE analysis expected state requires enabled and options fields"
        )
    expected_options = _validate_maestro_analysis_options(
        expected.get("options"), context="ADE analysis expected options"
    )
    desired_options = dict(expected_options)
    desired_options.update(options)
    if expected["enabled"] == update["enabled"] and desired_options == expected_options:
        raise RuntimeError("Maestro analysis update must change enable or options")


def _validate_maestro_output_addition(output: dict[str, Any]) -> None:
    name = output.get("name")
    if not isinstance(name, str) or not name or len(name) > 128:
        raise RuntimeError(f"invalid Maestro output name: {name!r}")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise RuntimeError(f"invalid Maestro output name: {name!r}")
    output_type = output.get("output_type")
    if output_type not in {"net", "point"}:
        raise RuntimeError(f"invalid Maestro output type: {output_type!r}")
    signal_name = output.get("signal_name")
    expression = output.get("expression")
    if output_type == "net":
        if not isinstance(signal_name, str) or not signal_name or expression is not None:
            raise RuntimeError(
                "net Maestro outputs require signal_name and forbid expression"
            )
    elif not isinstance(expression, str) or not expression or signal_name is not None:
        raise RuntimeError(
            "point Maestro outputs require expression and forbid signal_name"
        )
    source = signal_name if output_type == "net" else expression
    assert isinstance(source, str)
    maximum = 1024 if output_type == "net" else 4096
    if len(source) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in source
    ):
        raise RuntimeError(f"invalid Maestro {output_type} output source")
    spec = output.get("spec")
    if spec is None:
        return
    if not isinstance(spec, dict) or spec.get("relation") not in {"lt", "gt"}:
        raise RuntimeError("Maestro output spec requires relation lt or gt")
    value = spec.get("value")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RuntimeError("invalid Maestro output spec value")


def apply_maestro_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """CAS analysis state and add non-conflicting named outputs in one save."""

    from virtuoso_bridge.virtuoso.maestro import (
        add_output,
        close_session,
        find_open_session,
        open_session,
        save_setup,
        set_analysis,
        set_spec,
    )
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    settings = payload.get("ade_setup") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    view = str(payload["target"].get("view") or "")
    if view != "maestro":
        raise RuntimeError("ADE setup patch target view must be maestro")
    expected_tests = [str(value) for value in settings.get("expected_tests") or []]
    analyses = list(settings.get("analyses") or [])
    outputs = list(settings.get("outputs") or [])
    if not expected_tests or (not analyses and not outputs):
        raise RuntimeError(
            "ADE setup patch requires expected_tests and analyses or outputs"
        )
    if len(expected_tests) != len(set(expected_tests)):
        raise RuntimeError("ADE setup patch expected_tests contain duplicates")
    for test in expected_tests:
        if (
            not test
            or len(test) > 128
            or any(
                character in ('"', "\\")
                or ord(character) < 32
                or ord(character) == 127
                for character in test
            )
        ):
            raise RuntimeError(f"invalid Maestro test name: {test!r}")
    analysis_identities: set[tuple[str, str]] = set()
    for update in analyses:
        if not isinstance(update, dict):
            raise RuntimeError("ADE analysis update must be an object")
        test = str(update.get("test") or "")
        analysis = str(update.get("analysis") or "")
        identity = (test, analysis)
        if test not in expected_tests:
            raise RuntimeError("ADE analysis update targets an undeclared test")
        if identity in analysis_identities:
            raise RuntimeError("ADE setup patch contains duplicate analyses")
        analysis_identities.add(identity)
        _validate_maestro_analysis_update(update)
    output_identities: set[tuple[str, str]] = set()
    for output in outputs:
        if not isinstance(output, dict):
            raise RuntimeError("ADE output addition must be an object")
        test = str(output.get("test") or "")
        name = str(output.get("name") or "")
        identity = (test, name)
        if test not in expected_tests:
            raise RuntimeError("ADE output addition targets an undeclared test")
        if identity in output_identities:
            raise RuntimeError("ADE setup patch contains duplicate outputs")
        output_identities.add(identity)
        _validate_maestro_output_addition(output)

    client = _client()
    if not _cellview_exists(client, library, cell, view):
        raise RuntimeError(f"ADE setup patch requires existing {library}/{cell}/{view}")
    existing_session = find_open_session(client)
    if existing_session is not None:
        raise RuntimeError(
            "ADE setup patch refuses to save while any configured Maestro "
            f"session is already open: {existing_session}"
        )

    before_analyses: list[dict[str, Any]] = []
    immediate_analyses: list[dict[str, Any]] = []
    before_outputs: list[dict[str, Any]] = []
    immediate_outputs: list[dict[str, Any]] = []
    session = open_session(client, library, cell)
    try:
        tests = _maestro_tests_readback(client, session)
        if tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed before setup patch: "
                f"expected {expected_tests!r}, got {tests!r}"
            )
        mismatches: list[str] = []
        for update in analyses:
            state = _maestro_analysis_state(
                client,
                str(update["test"]),
                str(update["analysis"]),
                session=session,
            )
            entry = {
                "test": str(update["test"]),
                "analysis": str(update["analysis"]),
                "state": state,
            }
            before_analyses.append(entry)
            if state != update.get("expected"):
                mismatches.append(
                    f"analysis {update['test']}/{update['analysis']} expected "
                    f"{update.get('expected')!r}, got {state!r}"
                )
        for output in outputs:
            state = _maestro_output_state(
                client,
                str(output["test"]),
                str(output["name"]),
                session=session,
            )
            entry = {
                "test": str(output["test"]),
                "name": str(output["name"]),
                "state": state,
            }
            before_outputs.append(entry)
            if state is not None:
                mismatches.append(
                    f"output {output['test']}/{output['name']} already exists"
                )
        if mismatches:
            raise RuntimeError(
                "Maestro setup precondition mismatch before any write: "
                + "; ".join(mismatches)
            )

        for update in analyses:
            options = dict(update.get("options") or {})
            set_analysis(
                client,
                escape_skill_string(str(update["test"])),
                str(update["analysis"]),
                enable=bool(update["enabled"]),
                options=_analysis_options_skill(options) if options else "",
                session=session,
            )
            state = _maestro_analysis_state(
                client,
                str(update["test"]),
                str(update["analysis"]),
                session=session,
            )
            if not _requested_analysis_matches(update, state):
                raise RuntimeError(
                    "Maestro analysis immediate readback mismatch for "
                    f"{update['test']}/{update['analysis']}: {state!r}"
                )
            immediate_analyses.append(
                {
                    "test": str(update["test"]),
                    "analysis": str(update["analysis"]),
                    "state": state,
                }
            )
        for output in outputs:
            add_output(
                client,
                escape_skill_string(str(output["name"])),
                escape_skill_string(str(output["test"])),
                output_type=str(output["output_type"]),
                signal_name=(
                    escape_skill_string(str(output["signal_name"]))
                    if output.get("signal_name") is not None
                    else ""
                ),
                expr=(
                    escape_skill_string(str(output["expression"]))
                    if output.get("expression") is not None
                    else ""
                ),
                session=session,
            )
            spec = output.get("spec")
            if isinstance(spec, dict):
                kwargs = {
                    str(spec["relation"]): escape_skill_string(str(spec["value"]))
                }
                set_spec(
                    client,
                    escape_skill_string(str(output["name"])),
                    escape_skill_string(str(output["test"])),
                    session=session,
                    **kwargs,
                )
            state = _maestro_output_state(
                client,
                str(output["test"]),
                str(output["name"]),
                session=session,
            )
            if not _requested_output_matches(output, state):
                raise RuntimeError(
                    "Maestro output immediate readback mismatch for "
                    f"{output['test']}/{output['name']}: {state!r}"
                )
            immediate_outputs.append(
                {
                    "test": str(output["test"]),
                    "name": str(output["name"]),
                    "state": state,
                }
            )
        save_setup(client, library, cell, session=session)
    finally:
        close_session(client, session)

    persisted_analyses: list[dict[str, Any]] = []
    persisted_outputs: list[dict[str, Any]] = []
    verify_session = open_session(client, library, cell)
    try:
        verify_tests = _maestro_tests_readback(client, verify_session)
        if verify_tests != expected_tests:
            raise RuntimeError(
                "Maestro tests changed after setup patch: "
                f"expected {expected_tests!r}, got {verify_tests!r}"
            )
        for immediate in immediate_analyses:
            state = _maestro_analysis_state(
                client,
                str(immediate["test"]),
                str(immediate["analysis"]),
                session=verify_session,
            )
            persisted = {**immediate, "state": state}
            persisted_analyses.append(persisted)
            if persisted != immediate:
                raise RuntimeError(
                    "Maestro analysis persistent readback mismatch for "
                    f"{immediate['test']}/{immediate['analysis']}"
                )
        for immediate in immediate_outputs:
            state = _maestro_output_state(
                client,
                str(immediate["test"]),
                str(immediate["name"]),
                session=verify_session,
            )
            persisted = {**immediate, "state": state}
            persisted_outputs.append(persisted)
            if persisted != immediate:
                raise RuntimeError(
                    "Maestro output persistent readback mismatch for "
                    f"{immediate['test']}/{immediate['name']}"
                )
    finally:
        close_session(client, verify_session)

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "expected_tests": expected_tests,
        "tests_readback_before": tests,
        "tests_readback_after": verify_tests,
        "requested_analysis_updates": analyses,
        "requested_output_additions": outputs,
        "requested_evidence_source": "user_input",
        "before_analyses": before_analyses,
        "immediate_analyses": immediate_analyses,
        "persisted_analyses": persisted_analyses,
        "before_outputs": before_outputs,
        "immediate_outputs": immediate_outputs,
        "persisted_outputs": persisted_outputs,
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": _maestro_setup_patch_fingerprint(
            tests, before_analyses, before_outputs
        ),
        "after_target_fingerprint_sha256": _maestro_setup_patch_fingerprint(
            verify_tests, persisted_analyses, persisted_outputs
        ),
        "analysis_write_method": "bridge_public_set_analysis",
        "analysis_readback_method": "cadence_maeGetAnalysis_via_bridge_skill_channel",
        "output_write_method": "bridge_public_add_output_and_set_spec",
        "output_readback_method": (
            "cadence_maeGetTestOutputs_and_axlGetSpecData_via_bridge_skill_channel"
        ),
        "existing_outputs_replaced": False,
        "existing_maestro_replaced": False,
        "unlisted_setup_state_checked": False,
        "full_setup_fingerprint_verified": False,
        "schematic_oa_write_performed": False,
        "maestro_setup_write_performed": True,
        "automated_simulation_performed": False,
        "completion_scope": (
            "declared analyses matched exact old state and requested updates; "
            "declared named outputs were absent before addition; all targeted state "
            "was saved once and matched after independent reopen; existing outputs, "
            "unlisted setup state, simulator input, and simulation results were not "
            "modified or verified"
        ),
    }


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ade_artifact_category(relative_path: str) -> str:
    path = Path(relative_path)
    name = path.name
    lowered_parts = {part.lower() for part in path.parts}
    if name in _ADE_SETUP_FILENAMES:
        return "setup"
    if "netlist" in lowered_parts or name in _ADE_RUN_INPUT_FILENAMES:
        return "simulator_input"
    if "psf" in lowered_parts:
        if name in _ADE_RUN_LOG_FILENAMES:
            return "run_log"
        return "eda_result"
    if name.endswith(".rdb"):
        return "eda_result"
    if name.endswith(".log") or name.endswith(".msg.db"):
        return "run_log"
    return "other"


def _ade_capture_manifest(output_dir: Path) -> list[dict[str, Any]]:
    if not output_dir.is_dir():
        raise RuntimeError(f"ADE capture output directory is missing: {output_dir}")
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        category = _ade_artifact_category(relative)
        source = (
            "eda_result"
            if category in {"simulator_input", "eda_result", "run_log"}
            else "bridge_readback"
        )
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_path(path),
                "category": category,
                "evidence_source": source,
            }
        )
    return entries


def _manifest_fingerprint(
    manifest: list[dict[str, Any]], categories: set[str]
) -> str | None:
    selected = [
        {"path": item["path"], "sha256": item["sha256"]}
        for item in manifest
        if item["category"] in categories
    ]
    if not selected:
        return None
    canonical = json.dumps(
        selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _assert_focused_maestro(
    snapshot_data: dict[str, Any],
    *,
    library: str,
    cell: str,
    view: str,
    expected_session: str | None = None,
) -> str:
    session = str(snapshot_data.get("session") or "")
    if not session:
        raise RuntimeError(
            "no focused ADE Explorer/Assembler Maestro window; focus the declared "
            "view and retry"
        )
    actual = (
        str(snapshot_data.get("lib") or ""),
        str(snapshot_data.get("cell") or ""),
        str(snapshot_data.get("view") or ""),
    )
    expected = (library, cell, view)
    if actual != expected:
        raise RuntimeError(
            "focused ADE target mismatch: "
            f"expected {library}/{cell}/{view}, got {'/'.join(actual)}"
        )
    if expected_session is not None and session != expected_session:
        raise RuntimeError(
            "focused ADE session changed while capturing; no mixed-session evidence "
            "was accepted"
        )
    return session


def _has_structured_ade_outputs(results: dict[str, Any]) -> bool:
    points = results.get("points")
    return bool(
        isinstance(points, list)
        and any(
            isinstance(point, dict)
            and isinstance(point.get("outputs"), dict)
            and bool(point["outputs"])
            for point in points
        )
    )


def _parse_ade_corner_detail_csv(
    text: str, *, history: str, expected_corners: list[str]
) -> dict[str, Any]:
    """Preserve Maestro Detail CSV's orthogonal point and corner dimensions."""

    rows = list(csv.reader(str(text or "").splitlines()))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if len(row) >= 3
            and row[0].strip() == "Point"
            and row[1].strip() == "Test"
            and row[2].strip() == "Output"
        ),
        None,
    )
    if header_index is None:
        raise RuntimeError("ADE corner Detail CSV lacked its Point/Test/Output header")
    header = rows[header_index]
    corner_columns: dict[str, int] = {}
    for corner in expected_corners:
        matches = [
            index for index, value in enumerate(header) if value.strip() == corner
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"ADE corner Detail CSV expected one column for {corner!r}, "
                f"found {len(matches)}"
            )
        corner_columns[corner] = matches[0]

    corner_parameters: dict[str, dict[str, str]] = {
        corner: {} for corner in expected_corners
    }
    for row in rows[:header_index]:
        if len(row) < 3:
            continue
        name = row[2].strip()
        if name == "Parameter":
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
            continue
        for corner, column in corner_columns.items():
            value = row[column].strip() if column < len(row) else ""
            if value:
                corner_parameters[corner][name] = value

    cases: dict[tuple[int, str], dict[str, Any]] = {}
    active_parameters: dict[str, str] | None = None
    tests_seen: set[str] = set()
    for row in rows[header_index + 1 :]:
        if not row or not any(value.strip() for value in row):
            continue
        first = row[0].strip()
        if first.startswith("Parameters:"):
            active_parameters = {}
            for pair in first[len("Parameters:") :].strip().split(","):
                name, separator, value = pair.strip().partition("=")
                if separator:
                    active_parameters[name.strip()] = value.strip()
            continue
        if not first.isdigit():
            continue
        maestro_point = int(first)
        if active_parameters is None:
            active_parameters = {}
        test = row[1].strip() if len(row) > 1 else ""
        output = row[2].strip() if len(row) > 2 else ""
        if test:
            tests_seen.add(test)
        for corner, column in corner_columns.items():
            key = (maestro_point, corner)
            case = cases.setdefault(
                key,
                {
                    "maestro_point": maestro_point,
                    "corner": corner,
                    "parameters": {
                        **active_parameters,
                        **corner_parameters[corner],
                    },
                    "outputs": {},
                },
            )
            if output:
                value = row[column].strip() if column < len(row) else ""
                case["outputs"][output] = {
                    "value": value,
                    "spec": row[4].strip() if len(row) > 4 else "",
                    "weight": row[5].strip() if len(row) > 5 else "",
                    "pass_fail": row[6].strip() if len(row) > 6 else "",
                    "min": row[7].strip() if len(row) > 7 else "",
                    "max": row[8].strip() if len(row) > 8 else "",
                }

    if not cases:
        raise RuntimeError("ADE corner Detail CSV did not expose any result cells")
    maestro_points = sorted({key[0] for key in cases})
    expected_keys = {
        (point, corner) for point in maestro_points for corner in expected_corners
    }
    if set(cases) != expected_keys:
        raise RuntimeError("ADE corner Detail CSV did not form a complete point grid")
    return {
        "history": history,
        "tests": sorted(tests_seen),
        "corners": list(expected_corners),
        "points": [
            cases[key]
            for key in sorted(
                cases,
                key=lambda key: (key[0], expected_corners.index(key[1])),
            )
        ],
        "detail_csv_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "detail_csv_size_bytes": len(text.encode("utf-8")),
        "raw_evidence_source": "eda_result",
        "parser_evidence_source": "software_inference",
    }


def _normalized_maestro_history(value: Any) -> str:
    history = str(value or "").strip()
    if len(history) >= 2 and history.startswith('"') and history.endswith('"'):
        history = history[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", history):
        raise RuntimeError(f"Maestro returned an invalid history name: {value!r}")
    return history


def _validated_ade_remote_path(value: Any, label: str) -> str:
    path = str(value or "").strip().rstrip("/")
    if not (path == "/data/xum" or path.startswith("/data/xum/")):
        raise RuntimeError(f"{label} must stay under /data/xum: {path!r}")
    if "\\" in path or any(ord(character) < 32 for character in path):
        raise RuntimeError(f"{label} contains unsafe path characters: {path!r}")
    if any(part in {"", ".", ".."} for part in path.split("/")[1:]):
        raise RuntimeError(f"{label} is not a normalized absolute path: {path!r}")
    return path


def _unwrap_single_skill_value(value: Any) -> Any:
    current = value
    while isinstance(current, list) and len(current) == 1:
        current = current[0]
    return current


def _maestro_test_runtime_path_state(
    client,
    *,
    session: str,
    test: str,
) -> dict[str, str]:
    """Read the transient Analog Session directories for one Maestro test."""

    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    escaped_session = escape_skill_string(session)
    escaped_test = escape_skill_string(test)
    expression = (
        "let((a) "
        f'a=maeGetTestSession("{escaped_test}" ?session "{escaped_session}") '
        "list(asiGetProjectDir(a) asiGetResultsDir(a) asiGetAnalogRunDir(a)))"
    )
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro runtime directory readback failed for test {test}: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if (
        not isinstance(parsed, list)
        or len(parsed) != 3
        or not all(isinstance(value, str) and value for value in parsed)
    ):
        raise RuntimeError(
            f"invalid Maestro runtime directory readback for test {test}: {parsed!r}"
        )
    return {
        "project_dir": parsed[0],
        "results_dir": parsed[1],
        "analog_run_dir": parsed[2],
    }


def _set_maestro_test_runtime_paths(
    client,
    *,
    session: str,
    test: str,
    project_dir: str,
    results_dir: str,
) -> dict[str, str]:
    """Set and immediately read back one test's in-memory run directories."""

    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    escaped_session = escape_skill_string(session)
    escaped_test = escape_skill_string(test)
    escaped_project = escape_skill_string(project_dir)
    escaped_results = escape_skill_string(results_dir)
    expression = (
        "let((a) "
        f'a=maeGetTestSession("{escaped_test}" ?session "{escaped_session}") '
        f'asiSetProjectDir(a "{escaped_project}") '
        f'asiSetResultsDir(a "{escaped_results}") '
        "list(asiGetProjectDir(a) asiGetResultsDir(a) asiGetAnalogRunDir(a)))"
    )
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"Maestro runtime directory update failed for test {test}: {errors[0]}"
        )
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if (
        not isinstance(parsed, list)
        or len(parsed) != 3
        or not all(isinstance(value, str) and value for value in parsed)
    ):
        raise RuntimeError(
            f"invalid Maestro runtime directory update readback for test {test}: "
            f"{parsed!r}"
        )
    return {
        "project_dir": parsed[0],
        "results_dir": parsed[1],
        "analog_run_dir": parsed[2],
    }


def _configure_background_ade_runtime(
    client,
    payload: dict[str, Any],
    *,
    session: str,
    tests: list[str],
    library: str,
    cell: str,
    view: str,
    scratch_root: str | None = None,
) -> dict[str, Any]:
    """Redirect every test's transient run state beneath the profile run root."""

    profile = payload.get("profile") or {}
    run_root = _validated_ade_remote_path(
        profile.get("remote_run_root"), "ADE runtime root"
    )
    task_slug = re.sub(
        r"[^A-Za-z0-9_.-]", "_", str(payload.get("task_id") or "task")
    )
    if scratch_root is None:
        scratch_root = _validated_ade_remote_path(
            f"{run_root}/vda_ade_run_{task_slug}_{uuid.uuid4().hex[:12]}",
            "ADE scratch root",
        )
    else:
        scratch_root = _validated_ade_remote_path(
            scratch_root, "resumed ADE scratch root"
        )
        if not scratch_root.startswith(f"{run_root}/"):
            raise RuntimeError(
                "resumed ADE scratch root must stay below the active profile "
                f"run root {run_root!r}: {scratch_root!r}"
            )
    marker = f"/{library}/{cell}/{view}/results/maestro"
    configured: list[dict[str, Any]] = []
    try:
        for index, test in enumerate(tests):
            before = _maestro_test_runtime_path_state(
                client, session=session, test=test
            )
            test_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", test) or "test"
            runtime_dir = _validated_ade_remote_path(
                f"{scratch_root}/{library}/{cell}/{view}/results/maestro/"
                f".tmpADEDir_vda/{index}_{test_slug}/simulation",
                f"ADE runtime directory for test {test}",
            )
            after = _set_maestro_test_runtime_paths(
                client,
                session=session,
                test=test,
                project_dir=runtime_dir,
                results_dir=runtime_dir,
            )
            if after["project_dir"] != runtime_dir or after["results_dir"] != runtime_dir:
                raise RuntimeError(
                    f"Maestro runtime directory did not read back for test {test}: "
                    f"{after!r}"
                )
            analog_run_dir = _validated_ade_remote_path(
                after["analog_run_dir"],
                f"Maestro analog run directory for test {test}",
            )
            if not analog_run_dir.startswith(f"{scratch_root}/") or marker not in analog_run_dir:
                raise RuntimeError(
                    f"Maestro analog run directory escaped the declared scratch root "
                    f"or target anchor for test {test}: {analog_run_dir!r}"
                )
            configured.append(
                {
                    "test": test,
                    "previous": before,
                    "applied": {**after, "analog_run_dir": analog_run_dir},
                }
            )
    except Exception:
        for item in reversed(configured):
            previous = item["previous"]
            _set_maestro_test_runtime_paths(
                client,
                session=session,
                test=item["test"],
                project_dir=previous["project_dir"],
                results_dir=previous["results_dir"],
            )
        raise
    return {
        "scratch_root": scratch_root,
        "tests": configured,
        "evidence_source": "bridge_readback",
    }


def _restore_background_ade_runtime(
    client,
    *,
    session: str,
    runtime: dict[str, Any],
) -> None:
    """Restore all transient directory values before closing the session."""

    for item in reversed(runtime.get("tests") or []):
        previous = item["previous"]
        restored = _set_maestro_test_runtime_paths(
            client,
            session=session,
            test=item["test"],
            project_dir=previous["project_dir"],
            results_dir=previous["results_dir"],
        )
        if (
            restored["project_dir"] != previous["project_dir"]
            or restored["results_dir"] != previous["results_dir"]
        ):
            raise RuntimeError(
                f"Maestro runtime directory restoration failed for test "
                f"{item['test']}: {restored!r}"
            )


def _maestro_history_locations(
    client,
    *,
    session: str,
    test: str,
    library: str,
    cell: str,
    view: str,
) -> list[dict[str, str]]:
    """Resolve project and scratch Maestro roots without selecting a history."""

    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    expression = (
        "list("
        f'ddGetObj("{escape_skill_string(library)}")~>readPath '
        "errset(asiGetAnalogRunDir(maeGetTestSession("
        f'"{escape_skill_string(test)}" ?session '
        f'"{escape_skill_string(session)}"))))'
    )
    readback = client.execute_skill(expression, timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro history path readback failed: {errors[0]}")
    parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise RuntimeError(f"invalid Maestro history path readback: {parsed!r}")
    lib_path_raw = _unwrap_single_skill_value(parsed[0])
    analog_run_dir_raw = _unwrap_single_skill_value(parsed[1])
    if not isinstance(lib_path_raw, str) or not isinstance(
        analog_run_dir_raw, str
    ):
        raise RuntimeError(
            "Maestro did not expose both library and analog run directories"
        )
    lib_path = _validated_ade_remote_path(lib_path_raw, "Maestro library path")
    analog_run_dir = _validated_ade_remote_path(
        analog_run_dir_raw, "Maestro analog run directory"
    )
    marker = f"/{library}/{cell}/{view}/results/maestro"
    marker_index = analog_run_dir.find(marker)
    if marker_index <= 0:
        raise RuntimeError(
            "Maestro analog run directory did not contain the declared target "
            f"anchor {marker!r}: {analog_run_dir!r}"
        )
    scratch_root = _validated_ade_remote_path(
        analog_run_dir[:marker_index], "Maestro scratch root"
    )
    candidates = [
        {
            "source_location": "project",
            "maestro_root": _validated_ade_remote_path(
                f"{lib_path}/{cell}/{view}/results/maestro",
                "project Maestro result root",
            ),
        },
        {
            "source_location": "scratch",
            "maestro_root": _validated_ade_remote_path(
                f"{scratch_root}/{library}/{cell}/{view}/results/maestro",
                "scratch Maestro result root",
            ),
        },
    ]
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate["maestro_root"] in seen:
            continue
        unique.append(candidate)
        seen.add(candidate["maestro_root"])
    return unique


def _maestro_history_log_paths(
    client, locations: list[dict[str, str]]
) -> dict[str, list[str]]:
    """List exact Maestro history logs without using a shell or choosing latest."""

    histories: dict[str, list[str]] = {}
    for location in locations:
        root = _validated_ade_remote_path(
            location.get("maestro_root"), "Maestro result root"
        )
        readback = client.execute_skill(
            f"getDirFiles({json.dumps(root)})", timeout=30
        )
        errors = getattr(readback, "errors", None) or []
        if errors:
            raise RuntimeError(
                f"Maestro history listing failed for {root}: {errors[0]}"
            )
        parsed = _parse_skill_sexpr(getattr(readback, "output", ""))
        if parsed is None:
            continue
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) for item in parsed
        ):
            raise RuntimeError(
                f"invalid Maestro history listing for {root}: {parsed!r}"
            )
        for filename in parsed:
            if not filename.endswith(".log"):
                continue
            history = filename.removesuffix(".log")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", history):
                continue
            path = _validated_ade_remote_path(
                f"{root}/{filename}", "Maestro history log"
            )
            histories.setdefault(history, []).append(path)
    return {
        history: sorted(set(paths))
        for history, paths in sorted(histories.items())
    }


def _recover_single_new_completed_maestro_history(
    client,
    *,
    locations: list[dict[str, str]],
    histories_before: dict[str, list[str]],
    timeout_error: TimeoutError,
) -> tuple[str, dict[str, Any]]:
    """Recover only one newly named, already-completed history after timeout."""

    histories_after = _maestro_history_log_paths(client, locations)
    new_histories = sorted(set(histories_after) - set(histories_before))
    if len(new_histories) != 1:
        raise RuntimeError(
            "Bridge completion wait timed out and VDA could not identify exactly "
            "one newly named Maestro history: "
            f"new_histories={new_histories!r}"
        ) from timeout_error
    history = new_histories[0]
    completed_logs: list[dict[str, Any]] = []
    for path in histories_after[history]:
        text = _read_remote_text_via_skill(client, path, page_lines=16)
        if re.search(
            rf"(?m)^\s*{re.escape(history)} completed\.\s*$", text
        ) is None:
            continue
        encoded = text.encode("utf-8")
        completed_logs.append(
            {
                "path": path,
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    if not completed_logs:
        raise RuntimeError(
            "Bridge completion wait timed out and the single new Maestro history "
            f"{history!r} did not have a completed log"
        ) from timeout_error
    return history, {
        "method": "single_new_completed_history_log_after_bridge_timeout",
        "bridge_timeout": str(timeout_error),
        "histories_before": sorted(histories_before),
        "new_histories": new_histories,
        "completed_history_logs": completed_logs,
        "evidence_sources": {
            "history_log": "eda_result",
            "selection": "software_inference",
        },
    }


def _ade_artifact_hash_commands(
    *,
    tree_root: str,
    manifest_path: str,
    companions: tuple[str, ...] = (),
) -> list[str]:
    # IC6.1.8 csh() expands dollar expressions before the nested sh receives
    # them and applies a small command buffer. Protect sh's dollars from csh,
    # then keep independent calls below the observed safe size.
    discover = (
        'set -eu;m=$1;t=$2;mkdir -p "${m%/*}";:>"$m.paths";'
        'if [ -d "$t" ];then find "$t" -type f -print0>>"$m.paths";fi'
    )
    commands = [
        "sh -c {} sh {} {}".format(
            shlex.quote(discover.replace("$", r"\$")),
            shlex.quote(manifest_path),
            shlex.quote(tree_root),
        )
    ]
    if companions:
        append = (
            'set -eu;m=$1;p=$2;if [ -f "$p" ];then find "$p" '
            '-maxdepth 0 -type f -print0>>"$m.paths";fi'
        )
        commands.extend(
            "sh -c {} sh {} {}".format(
                shlex.quote(append.replace("$", r"\$")),
                shlex.quote(manifest_path),
                shlex.quote(path),
            )
            for path in companions
        )
    manifest_dir, manifest_name = manifest_path.rsplit("/", 1)
    digest_stages = (
        'set -eu;d=$1;n=$2;cd "$d";xargs -0 -r sha256sum'
        '<"$n.paths">"$n.sha256"',
        'set -eu;d=$1;n=$2;cd "$d";xargs -0 -r -n1 wc -c'
        '<"$n.paths">"$n.sizes"',
        'set -eu;d=$1;n=$2;cd "$d";paste "$n.sizes" "$n.sha256">"$n"',
    )
    commands.extend(
        "sh -c {} sh {} {}".format(
            shlex.quote(stage.replace("$", r"\$")),
            shlex.quote(manifest_dir),
            shlex.quote(manifest_name),
        )
        for stage in digest_stages
    )
    for command in commands:
        skill_expression_size = len(
            'csh("")' + command.replace("\\", "\\\\").replace('"', '\\"')
        )
        if skill_expression_size >= 768:
            raise RuntimeError(
                "ADE artifact hash command exceeds the 768-byte escaped csh safety "
                f"limit: {skill_expression_size}"
            )
    return commands


def _parse_remote_ade_artifact_manifest(
    text: str,
    *,
    history: str,
    source_location: str,
    maestro_root: str,
    runtime_input_root: str | None = None,
    runtime_test: str | None = None,
) -> list[dict[str, Any]]:
    history_root = f"{maestro_root}/{history}"
    extras = {
        f"{maestro_root}/{history}.log",
        f"{maestro_root}/{history}.rdb",
        f"{maestro_root}/{history}.msg.db",
    }
    entries: list[dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        if not raw_line:
            continue
        if raw_line.startswith(_ADE_ARTIFACT_LINE_PREFIX):
            fields = raw_line.split("\t", 3)
            if len(fields) != 4:
                raise RuntimeError(
                    f"malformed ADE artifact manifest line: {raw_line!r}"
                )
            _, size_raw, digest, remote_path = fields
        else:
            fields = raw_line.split("\t", 1)
            size_match = re.fullmatch(r"\s*(\d+)\s+(.+)", fields[0])
            digest_match = (
                re.fullmatch(r"([0-9A-Fa-f]{64}) ([ *])(.+)", fields[1])
                if len(fields) == 2
                else None
            )
            if size_match is None or digest_match is None:
                raise RuntimeError(
                    f"unexpected ADE artifact manifest line: {raw_line!r}"
                )
            size_raw, size_path = size_match.groups()
            digest, _, remote_path = digest_match.groups()
            if size_path != remote_path:
                raise RuntimeError(
                    "ADE size/hash manifests named different artifacts: "
                    f"{size_path!r} != {remote_path!r}"
                )
        if any(ord(character) < 32 for character in remote_path):
            raise RuntimeError("ADE artifact path contained control characters")
        if runtime_input_root is not None:
            runtime_prefix = f"{runtime_input_root}/"
            if not remote_path.startswith(runtime_prefix):
                raise RuntimeError(
                    "ADE runtime input artifact escaped the unique invocation root: "
                    f"{remote_path!r}"
                )
            relative = remote_path[len(runtime_prefix) :]
            test_token = re.sub(
                r"[^A-Za-z0-9_.-]", "_", str(runtime_test or "test")
            )
            logical_path = f"{history}/runtime/{test_token}/{relative}"
            category = "simulator_input"
            binding = "unique_runtime_session"
        elif remote_path.startswith(f"{history_root}/"):
            relative = remote_path[len(history_root) + 1 :]
            logical_path = f"{history}/{relative}"
            category = _ade_artifact_category(logical_path)
            binding = "exact_history_path"
        elif remote_path in extras:
            relative = remote_path.rsplit("/", 1)[-1]
            logical_path = f"{history}/{relative}"
            category = _ade_artifact_category(logical_path)
            binding = "exact_history_companion"
        else:
            raise RuntimeError(
                "ADE artifact escaped the exact returned history: "
                f"{remote_path!r}"
            )
        if not relative or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise RuntimeError(f"invalid ADE artifact relative path: {relative!r}")
        try:
            size = int(size_raw.strip())
        except ValueError as exc:
            raise RuntimeError(
                f"invalid ADE artifact size for {remote_path!r}: {size_raw!r}"
            ) from exc
        normalized_digest = digest.strip().lower()
        if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", normalized_digest):
            raise RuntimeError(
                f"invalid ADE artifact hash record for {remote_path!r}"
            )
        entries.append(
            {
                "path": logical_path,
                "remote_path": remote_path,
                "source_location": source_location,
                "size_bytes": size,
                "sha256": normalized_digest,
                "category": category,
                "binding": binding,
                "evidence_source": (
                    "eda_result"
                    if category in {"simulator_input", "eda_result", "run_log"}
                    else "bridge_readback"
                ),
            }
        )
    return entries


def _read_remote_text_via_skill(
    client,
    remote_path: str,
    *,
    allow_bridge_results_csv: bool = False,
    page_lines: int = 4,
    max_lines: int = 4096,
    max_bytes: int = 2_000_000,
) -> str:
    """Read a bounded text artifact over the existing Bridge SKILL channel."""

    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    candidate = str(remote_path or "").strip()
    if allow_bridge_results_csv and re.fullmatch(
        r"/tmp/vb_results_[0-9a-f]{32}\.csv", candidate
    ):
        path = candidate
    else:
        path = _validated_ade_remote_path(candidate, "ADE manifest fallback path")
    escaped_path = escape_skill_string(path)
    offset = 0
    pages: list[str] = []
    total_bytes = 0
    while offset < max_lines:
        expression = (
            "let((port line text skipped count) "
            f'port=infile("{escaped_path}") '
            'unless(port error("VDA_MANIFEST_OPEN_FAILED")) '
            'text="" skipped=0 '
            f"while(skipped<{offset} && gets(line port) skipped=skipped+1) "
            f"count=0 while(count<{page_lines} && gets(line port) "
            "text=strcat(text line) count=count+1) "
            "close(port) list(count text))"
        )
        result = client.execute_skill(expression, timeout=30)
        _require_bridge_result(result, "read ADE text manifest via SKILL fallback")
        parsed = _parse_skill_sexpr(getattr(result, "output", ""))
        if not isinstance(parsed, list) or len(parsed) != 2:
            raise RuntimeError("ADE SKILL manifest fallback returned malformed data")
        try:
            count = int(str(parsed[0]))
        except ValueError as exc:
            raise RuntimeError(
                "ADE SKILL manifest fallback returned an invalid line count"
            ) from exc
        text = parsed[1]
        if count < 0 or count > page_lines or not isinstance(text, str):
            raise RuntimeError("ADE SKILL manifest fallback returned invalid page data")
        encoded_size = len(text.encode("utf-8"))
        total_bytes += encoded_size
        if total_bytes > max_bytes:
            raise RuntimeError("ADE text manifest exceeded the SKILL fallback byte limit")
        pages.append(text)
        offset += count
        if count < page_lines:
            return "".join(pages)
    raise RuntimeError("ADE text manifest exceeded the SKILL fallback line limit")


def _normalize_single_point_detail_csv(path: Path) -> dict[str, str] | None:
    """Adapt IC6.1.8's six-column single-point Detail CSV for Bridge 0.7.0."""

    text = path.read_text(encoding="utf-8", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    expected_header = ["Test", "Output", "Nominal", "Spec", "Weight", "Pass/Fail"]
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if [cell.strip() for cell in row[:6]] == expected_header
        ),
        None,
    )
    if header_index is None or any(
        row and row[0].strip() == "Point" for row in rows
    ):
        return None
    data_indices = [
        index
        for index, row in enumerate(rows)
        if index > header_index and any(cell.strip() for cell in row)
    ]
    if not data_indices or any(len(rows[index]) < 6 for index in data_indices):
        return None

    normalized_rows: list[list[str]] = []
    for index, row in enumerate(rows):
        if index == header_index:
            normalized_rows.append(["Point", *row])
        elif index in data_indices:
            normalized_rows.append(["1", *row])
        else:
            normalized_rows.append(row)
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(normalized_rows)
    normalized = buffer.getvalue()
    path.write_text(normalized, encoding="utf-8")
    return {
        "normalization": "cadence_single_point_detail_add_point_column",
        "original_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "normalized_sha256": hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest(),
    }


class _BridgeTextDownloadFallback:
    """Preserve Bridge downloads, with a narrow fallback for its Detail CSV."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.skill_fallback_paths: list[str] = []
        self.detail_csv_compatibility: list[dict[str, str]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def download_file(self, remote_path, local_path, **kwargs):
        result = self._inner.download_file(remote_path, local_path, **kwargs)
        try:
            _require_bridge_result(result, "download Bridge Maestro Detail CSV")
        except RuntimeError as exc:
            candidate = str(remote_path)
            if not re.fullmatch(r"/tmp/vb_results_[0-9a-f]{32}\.csv", candidate):
                return result
            try:
                text = _read_remote_text_via_skill(
                    self._inner,
                    candidate,
                    allow_bridge_results_csv=True,
                )
            except RuntimeError as fallback_exc:
                raise RuntimeError(
                    f"{exc}; bounded SKILL Detail-CSV fallback also failed: "
                    f"{fallback_exc}"
                ) from fallback_exc
            Path(local_path).write_text(text, encoding="utf-8")
            self.skill_fallback_paths.append(candidate)
        candidate = str(remote_path)
        if re.fullmatch(r"/tmp/vb_results_[0-9a-f]{32}\.csv", candidate):
            local = Path(local_path)
            if local.is_file():
                compatibility = _normalize_single_point_detail_csv(local)
                if compatibility is not None:
                    self.detail_csv_compatibility.append(
                        {"remote_path": candidate, **compatibility}
                    )
        return result


def _merge_remote_ade_artifacts(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in entries:
        path = str(item["path"])
        existing = merged.get(path)
        if existing is None:
            normalized = dict(item)
            normalized["remote_paths"] = [str(item["remote_path"])]
            normalized["source_locations"] = [str(item["source_location"])]
            merged[path] = normalized
            continue
        if (
            existing["sha256"] != item["sha256"]
            or existing["size_bytes"] != item["size_bytes"]
            or existing["category"] != item["category"]
        ):
            raise RuntimeError(
                "project and scratch contain conflicting exact-history artifact "
                f"copies for {path!r}"
            )
        remote_path = str(item["remote_path"])
        source_location = str(item["source_location"])
        if remote_path not in existing["remote_paths"]:
            existing["remote_paths"].append(remote_path)
        if source_location not in existing["source_locations"]:
            existing["source_locations"].append(source_location)
    return [merged[path] for path in sorted(merged)]


def _validate_background_ade_artifacts(
    manifest: list[dict[str, Any]], history: str
) -> dict[str, int]:
    counts = {
        category: sum(1 for item in manifest if item["category"] == category)
        for category in ("simulator_input", "eda_result", "run_log", "other")
    }
    nonempty_names = {
        Path(str(item["path"])).name
        for item in manifest
        if item["category"] == "simulator_input" and item["size_bytes"] > 0
    }
    missing_inputs = {"netlist", "input.scs"} - nonempty_names
    if missing_inputs:
        raise RuntimeError(
            f"ADE history {history!r} lacked non-empty core simulator inputs: "
            + ", ".join(sorted(missing_inputs))
        )
    for category in ("eda_result", "run_log"):
        if not any(
            item["category"] == category and item["size_bytes"] > 0
            for item in manifest
        ):
            raise RuntimeError(
                f"ADE history {history!r} lacked a non-empty {category} artifact"
            )
    return counts


def _collect_background_ade_artifacts(
    client,
    payload: dict[str, Any],
    *,
    session: str,
    test: str,
    history: str,
    library: str,
    cell: str,
    view: str,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    history_locations = _maestro_history_locations(
        client,
        session=session,
        test=test,
        library=library,
        cell=cell,
        view=view,
    )
    locations: list[dict[str, Any]] = []
    for index, location in enumerate(history_locations):
        maestro_root = location["maestro_root"]
        locations.append(
            {
                **location,
                "manifest_token": f"history_{index}_{location['source_location']}",
                "tree_root": f"{maestro_root}/{history}",
                "companions": (
                    f"{maestro_root}/{history}.log",
                    f"{maestro_root}/{history}.rdb",
                    f"{maestro_root}/{history}.msg.db",
                ),
                "binding": "exact_history",
            }
        )
    runtime_scratch_root = _validated_ade_remote_path(
        runtime.get("scratch_root"), "ADE runtime scratch root"
    )
    for index, item in enumerate(runtime.get("tests") or []):
        applied = item.get("applied") or {}
        analog_run_dir = _validated_ade_remote_path(
            applied.get("analog_run_dir"),
            f"ADE analog run directory for test {item.get('test')}",
        )
        if not analog_run_dir.startswith(f"{runtime_scratch_root}/"):
            raise RuntimeError(
                "ADE runtime input root escaped the unique invocation scratch root: "
                f"{analog_run_dir!r}"
            )
        locations.append(
            {
                "source_location": "runtime",
                "manifest_token": f"runtime_{index}",
                "tree_root": analog_run_dir,
                "companions": (),
                "binding": "unique_runtime_session",
                "runtime_input_root": analog_run_dir,
                "runtime_test": str(item.get("test") or "test"),
            }
        )
    profile = payload.get("profile") or {}
    run_root = _validated_ade_remote_path(
        profile.get("remote_run_root"), "ADE manifest run root"
    )
    manifest_dir = _validated_ade_remote_path(
        f"{run_root}/vda_ade_manifest_{uuid.uuid4().hex[:12]}",
        "ADE manifest directory",
    )
    all_entries: list[dict[str, Any]] = []
    remote_manifests: list[dict[str, Any]] = []
    skill_manifest_fallback_used = False
    with tempfile.TemporaryDirectory(prefix="vda_ade_manifest_") as temp_dir:
        local_root = Path(temp_dir)
        for index, location in enumerate(locations):
            maestro_root = str(location.get("maestro_root") or "")
            tree_root = str(location["tree_root"])
            remote_manifest = (
                f"{manifest_dir}/{index}_{location['manifest_token']}.tsv"
            )
            commands = _ade_artifact_hash_commands(
                tree_root=tree_root,
                manifest_path=remote_manifest,
                companions=tuple(location.get("companions") or ()),
            )
            for stage, command in enumerate(commands, start=1):
                shell_result = client.run_shell_command(command, timeout=120)
                try:
                    _require_bridge_result(
                        shell_result,
                        "hash exact Maestro artifacts at "
                        f"{location['source_location']} stage {stage}/{len(commands)}",
                    )
                except RuntimeError as exc:
                    raise RuntimeError(
                        f"{exc}; remote ADE manifest directory retained at "
                        f"{manifest_dir}"
                    ) from exc
            local_manifest = local_root / f"{index}.tsv"
            download = client.download_file(
                remote_manifest, local_manifest, timeout=60
            )
            manifest_transport = "bridge_public_download"
            try:
                _require_bridge_result(
                    download,
                    "download ADE artifact manifest from "
                    f"{location['source_location']}",
                )
            except RuntimeError as exc:
                try:
                    manifest_text = _read_remote_text_via_skill(
                        client, remote_manifest
                    )
                except RuntimeError as fallback_exc:
                    raise RuntimeError(
                        f"{exc}; SKILL text-manifest fallback also failed: "
                        f"{fallback_exc}; remote ADE manifest retained at "
                        f"{remote_manifest}"
                    ) from fallback_exc
                local_manifest.write_text(manifest_text, encoding="utf-8")
                manifest_transport = "bridge_public_skill_text_fallback"
                skill_manifest_fallback_used = True
            else:
                try:
                    manifest_text = local_manifest.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise RuntimeError(
                        "downloaded ADE artifact manifest was unreadable; remote copy "
                        f"retained at {remote_manifest}"
                    ) from exc
            try:
                entries = _parse_remote_ade_artifact_manifest(
                    manifest_text,
                    history=history,
                    source_location=location["source_location"],
                    maestro_root=maestro_root,
                    runtime_input_root=location.get("runtime_input_root"),
                    runtime_test=location.get("runtime_test"),
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"{exc}; remote ADE manifest retained at {remote_manifest}"
                ) from exc
            all_entries.extend(entries)
            remote_manifests.append(
                {
                    "source_location": location["source_location"],
                    "binding": location["binding"],
                    "tree_root": tree_root,
                    "maestro_root": maestro_root or None,
                    "history_root": (
                        tree_root if location["binding"] == "exact_history" else None
                    ),
                    "runtime_test": location.get("runtime_test"),
                    "remote_manifest_path": remote_manifest,
                    "manifest_transport": manifest_transport,
                    "manifest_sha256": _sha256_path(local_manifest),
                    "entry_count": len(entries),
                }
            )
    try:
        manifest = _merge_remote_ade_artifacts(all_entries)
        counts = _validate_background_ade_artifacts(manifest, history)
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc}; remote ADE manifest directory retained at {manifest_dir}"
        ) from exc
    return {
        "artifact_history": history,
        "artifact_history_path_binding_verified": True,
        "artifact_runtime_input_binding_verified": any(
            item.get("binding") == "unique_runtime_session"
            and item.get("category") == "simulator_input"
            for item in manifest
        ),
        "artifact_run_binding_verified": True,
        "artifact_manifest": manifest,
        "artifact_counts": counts,
        "artifact_manifest_complete": True,
        "artifacts_captured": True,
        "artifact_collection_method": (
            "bridge_public_shell_hash_to_remote_manifest_then_public_text_transfer"
        ),
        "artifact_manifest_skill_fallback_used": skill_manifest_fallback_used,
        "artifact_locations_checked": remote_manifests,
        "remote_manifest_directory": manifest_dir,
        "simulation_fingerprint_sha256": _manifest_fingerprint(
            manifest, {"simulator_input", "eda_result", "run_log"}
        ),
    }


def run_background_maestro(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a saved Maestro setup without opening or focusing a GUI window."""

    from virtuoso_bridge.virtuoso.maestro import (
        close_session,
        open_session,
        read_results,
        run_and_wait,
    )

    settings = payload.get("ade_run") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    view = str(payload["target"].get("view") or "")
    if view != "maestro":
        raise RuntimeError("ADE run target view must be maestro")

    client = _client()
    if not _cellview_exists(client, library, cell, view):
        raise RuntimeError(f"ADE run requires existing {library}/{cell}/{view}")

    session = open_session(client, library, cell)
    runtime: dict[str, Any] | None = None
    runtime_restored = False
    artifact_evidence: dict[str, Any] = {
        "artifact_history": None,
        "artifact_history_path_binding_verified": False,
        "artifact_runtime_input_binding_verified": False,
        "artifact_run_binding_verified": False,
        "artifact_manifest": [],
        "artifact_counts": {},
        "artifact_manifest_complete": False,
        "artifacts_captured": False,
        "simulation_fingerprint_sha256": None,
        "simulator_input_consistency_verified": False,
        "simulator_input_consistency": [],
        "sweep_setup_readback_before": None,
        "sweep_setup_readback_after": None,
        "sweep_point_consistency_verified": False,
        "sweep_point_consistency": [],
        "effective_simulation_values_verified": False,
        "exact_point_input_result_binding_verified": False,
        "native_sweep_database_binding_verified": False,
        "sweep_point_evidence_mode": None,
        "sweep_history_log_evidence": None,
        "sweep_result_database_artifacts": [],
        "result_mapping_setup_readback_before": None,
        "result_mapping_setup_readback_after": None,
    }
    sweep_verification = settings.get("sweep_verification")
    result_mapping = settings.get("result_mapping")
    corner_case_mode = bool(
        isinstance(sweep_verification, dict)
        and any(
            isinstance(point, dict) and point.get("corner") is not None
            for point in sweep_verification.get("points") or []
        )
    )
    sweep_setup_before: dict[str, Any] | None = None
    result_mapping_setup_before: dict[str, Any] | None = None
    callback_timeout_recovery: dict[str, Any] | None = None
    try:
        tests = _maestro_tests_readback(client, session)
        if not tests:
            raise RuntimeError("saved Maestro setup did not contain any tests")
        if sweep_verification is not None:
            if not isinstance(sweep_verification, dict):
                raise RuntimeError("ADE sweep_verification must be an object")
            from virtuoso_bridge.virtuoso.maestro import get_var

            sweep_setup_before = _read_maestro_sweep_setup(
                client, get_var, sweep_verification, session=session
            )
        if result_mapping is not None:
            if not isinstance(result_mapping, dict):
                raise RuntimeError("ADE result_mapping must be an object")
            result_mapping_setup_before = _read_ade_result_mapping_setup(
                client, result_mapping, session=session
            )
        resume_history = settings.get("resume_history")
        resume_scratch_root = settings.get("resume_runtime_scratch_root")
        runtime = _configure_background_ade_runtime(
            client,
            payload,
            session=session,
            tests=tests,
            library=library,
            cell=cell,
            view=view,
            scratch_root=(
                str(resume_scratch_root) if resume_scratch_root is not None else None
            ),
        )
        if resume_history is not None:
            history = _normalized_maestro_history(resume_history)
            run_status = "recovered"
            simulation_performed_by_this_invocation = False
        else:
            history_locations: list[dict[str, str]] = []
            histories_before: dict[str, list[str]] = {}
            if corner_case_mode:
                history_locations = _maestro_history_locations(
                    client,
                    session=session,
                    test=tests[0],
                    library=library,
                    cell=cell,
                    view=view,
                )
                histories_before = _maestro_history_log_paths(
                    client, history_locations
                )
            simulation_performed_by_this_invocation = True
            try:
                raw_history, run_status = run_and_wait(
                    client,
                    session=session,
                    timeout=int(payload.get("timeout_seconds") or 600),
                )
                history = _normalized_maestro_history(raw_history)
            except TimeoutError as exc:
                if not corner_case_mode:
                    raise
                history, callback_timeout_recovery = (
                    _recover_single_new_completed_maestro_history(
                        client,
                        locations=history_locations,
                        histories_before=histories_before,
                        timeout_error=exc,
                    )
                )
                run_status = "recovered_after_bridge_timeout"
            if str(run_status).strip().lower() != "done":
                if callback_timeout_recovery is None:
                    raise RuntimeError(
                        "Maestro background run did not reach done status: "
                        f"{run_status!r}"
                    )
        results_client = _BridgeTextDownloadFallback(client)
        results = read_results(
            results_client,
            session,
            lib=library,
            cell=cell,
            history=history,
            include_raw=corner_case_mode,
        )
        if corner_case_mode:
            raw_detail_csv = results.pop("raw_csv", None)
            expected_corners = list(
                (sweep_verification or {}).get("expected_corners") or []
            )
            if not isinstance(raw_detail_csv, str) or not raw_detail_csv:
                raise RuntimeError(
                    "ADE corner sweep required the raw exact-history Detail CSV"
                )
            corner_results = _parse_ade_corner_detail_csv(
                raw_detail_csv,
                history=history,
                expected_corners=[str(value) for value in expected_corners],
            )
            results["corner_points"] = corner_results["points"]
            results["corner_order"] = corner_results["corners"]
            results["corner_tests"] = corner_results["tests"]
            results["corner_detail_csv_sha256"] = corner_results[
                "detail_csv_sha256"
            ]
            results["corner_detail_csv_size_bytes"] = corner_results[
                "detail_csv_size_bytes"
            ]
            results["corner_detail_csv_evidence_sources"] = {
                "raw": corner_results["raw_evidence_source"],
                "parser": corner_results["parser_evidence_source"],
            }
        result_history = str(results.get("history") or "")
        if result_history and result_history != history:
            raise RuntimeError(
                "Maestro structured results history does not match the history "
                "created by this run"
            )
        structured_outputs = _has_structured_ade_outputs(results)
        try:
            artifact_evidence = _collect_background_ade_artifacts(
                client,
                payload,
                session=session,
                test=tests[0],
                history=history,
                library=library,
                cell=cell,
                view=view,
                runtime=runtime,
            )
        except RuntimeError as exc:
            if settings.get("require_artifact_manifest", True):
                raise RuntimeError(
                    f"{exc}; recoverable Maestro history={history}; "
                    f"runtime scratch={runtime['scratch_root']}"
                ) from exc
            artifact_evidence["artifact_capture_error"] = str(exc)
        if settings.get("require_simulator_input_consistency", False):
            try:
                if sweep_verification is not None:
                    consistency_evidence = _verify_ade_sweep_consistency(
                        client,
                        session=session,
                        tests=tests,
                        history=history,
                        results=results,
                        artifact_evidence=artifact_evidence,
                        verification=sweep_verification,
                    )
                else:
                    consistency_evidence = _verify_ade_simulator_inputs(
                        client,
                        session=session,
                        tests=tests,
                        artifact_evidence=artifact_evidence,
                    )
                artifact_evidence.update(consistency_evidence)
            except RuntimeError as exc:
                retained = artifact_evidence.get("remote_manifest_directory")
                raise RuntimeError(
                    f"{exc}"
                    + (
                        f"; remote ADE manifests retained at {retained}"
                        if retained
                        else ""
                    )
                ) from exc
        if sweep_verification is not None:
            from virtuoso_bridge.virtuoso.maestro import get_var

            sweep_setup_after = _read_maestro_sweep_setup(
                client, get_var, sweep_verification, session=session
            )
            if sweep_setup_before != sweep_setup_after:
                raise RuntimeError(
                    "Maestro sweep setup changed while ade.run was executing"
                )
            artifact_evidence.update(
                {
                    "sweep_setup_readback_before": sweep_setup_before,
                    "sweep_setup_readback_after": sweep_setup_after,
                    "sweep_setup_readback_evidence_source": "bridge_readback",
                    "expected_sweep_evidence_source": "user_input",
                }
            )
        if result_mapping is not None:
            result_mapping_setup_after = _read_ade_result_mapping_setup(
                client, result_mapping, session=session
            )
            if result_mapping_setup_before != result_mapping_setup_after:
                raise RuntimeError(
                    "Maestro result-mapping outputs changed while ade.run was executing"
                )
            artifact_evidence.update(
                {
                    "result_mapping_setup_readback_before": (
                        result_mapping_setup_before
                    ),
                    "result_mapping_setup_readback_after": (
                        result_mapping_setup_after
                    ),
                    "result_mapping_setup_readback_evidence_source": (
                        "bridge_readback"
                    ),
                    "expected_result_mapping_evidence_source": "user_input",
                    "result_mapping_setup_unchanged": True,
                }
            )
        if settings.get("require_structured_outputs", True) and not structured_outputs:
            retained = artifact_evidence.get("remote_manifest_directory")
            raise RuntimeError(
                "Maestro background run completed but did not expose a non-empty "
                "point/output/spec table"
                + (
                    f"; remote ADE manifests retained at {retained}"
                    if retained
                    else ""
                )
            )
    finally:
        try:
            if runtime is not None:
                _restore_background_ade_runtime(
                    client, session=session, runtime=runtime
                )
                runtime_restored = True
        finally:
            close_session(client, session)

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "session_mode": "background",
        "gui_focus_required": False,
        "tests_readback": tests,
        "setup_evidence_source": "bridge_readback",
        "history": history,
        "history_evidence_source": "eda_result",
        "history_naming_policy": "saved_setup_unmodified",
        "history_uniqueness_verified": False,
        "run_status": str(run_status).strip().lower(),
        "structured_results_available": structured_outputs,
        "structured_results": results,
        "structured_results_evidence_source": "eda_result",
        "structured_results_transfer_method": (
            "bridge_read_results_with_bounded_skill_text_fallback"
            if results_client.skill_fallback_paths
            else "bridge_public_read_results"
        ),
        "structured_results_skill_fallback_used": bool(
            results_client.skill_fallback_paths
        ),
        "structured_results_csv_compatibility": (
            results_client.detail_csv_compatibility
        ),
        "structured_results_compatibility_evidence_source": (
            "software_inference"
            if results_client.detail_csv_compatibility
            else None
        ),
        "automated_simulation_performed": True,
        "simulation_performed_by_this_invocation": (
            simulation_performed_by_this_invocation
        ),
        "history_recovery_performed": resume_history is not None,
        "history_recovery_request_evidence_source": (
            "user_input" if resume_history is not None else None
        ),
        "callback_timeout_history_recovery_performed": (
            callback_timeout_recovery is not None
        ),
        "callback_timeout_history_recovery_evidence": callback_timeout_recovery,
        "oa_write_performed": False,
        "maestro_setup_write_performed": False,
        "runtime_directory_policy": "transient_per_test_session_override",
        "runtime_scratch_root": runtime["scratch_root"] if runtime else None,
        "runtime_directory_overrides": runtime["tests"] if runtime else [],
        "runtime_directory_evidence_source": "bridge_readback",
        "runtime_directory_persisted": False,
        "runtime_directory_restored": runtime_restored,
        "runtime_artifacts_restricted_to_data_xum": bool(runtime),
        **artifact_evidence,
        "completion_scope": (
            (
                "an explicitly named existing Maestro history was recovered without "
                "running simulation again"
                if resume_history is not None
                else (
                    "the saved Maestro setup was executed in a background session; "
                    "Bridge completion-wait timeout was recovered only after one "
                    "new completed history log was identified"
                    if callback_timeout_recovery is not None
                    else "the saved Maestro setup was executed in a background session"
                )
            )
            + "; exact-history simulator input, "
            "result, and log artifacts were hashed across project and scratch "
            "locations when required"
            + (
                (
                    "; every declared native sweep point was bound through the "
                    "saved variable scope, one symbolic runtime Spectre input per "
                    "test, the exact-history Maestro result database and completion "
                    "log, OA parameter references, and structured point results"
                    if artifact_evidence.get(
                        "native_sweep_database_binding_verified"
                    )
                    else "; every declared native sweep point was bound to saved "
                    "variable scope readback, exact-history input.scs, OA parameter "
                    "references, and structured results"
                )
                if sweep_verification is not None
                else ""
            )
            + (
                "; declared result-mapping scalar output expressions were pinned "
                "before and after the run"
                if result_mapping is not None
                else "; VDA constraint mapping was not requested"
            )
            + "; history name uniqueness was not proved"
        ),
    }


def capture_focused_maestro(payload: dict[str, Any]) -> dict[str, Any]:
    """Capture a user-operated Maestro view without changing or rerunning it."""

    from virtuoso_bridge.virtuoso.maestro import read_results, snapshot

    settings = payload.get("ade_capture") or {}
    if settings.get("backend", "maestro") != "maestro":
        raise RuntimeError("only the verified Bridge Maestro backend is supported")
    library, cell = _target(payload)
    view = str(payload["target"].get("view") or "")
    if view != "maestro":
        raise RuntimeError("ADE capture target view must be maestro")

    client = _client()
    before = snapshot(client)
    session = _assert_focused_maestro(
        before, library=library, cell=cell, view=view
    )
    if bool(before.get("unsaved")) and settings.get("require_saved_setup", True):
        raise RuntimeError(
            "focused Maestro setup has unsaved changes; save it before capture or "
            "explicitly set require_saved_setup=false"
        )

    output_root = Path(str(payload.get("capture_output_root") or "")).resolve()
    if not str(payload.get("capture_output_root") or ""):
        raise RuntimeError("ADE capture requires a local output root")
    requested_history = str(settings.get("history") or "")
    captured = snapshot(
        client,
        output_root=str(output_root),
        history=requested_history or None,
    )
    _assert_focused_maestro(
        captured,
        library=library,
        cell=cell,
        view=view,
        expected_session=session,
    )
    if bool(captured.get("unsaved")) and settings.get("require_saved_setup", True):
        raise RuntimeError("Maestro setup became unsaved while it was being captured")

    output_dir = Path(str(captured.get("output_dir") or "")).resolve()
    try:
        output_dir.relative_to(output_root)
    except ValueError as exc:
        raise RuntimeError(
            "Bridge returned an ADE capture path outside its output root"
        ) from exc
    manifest = _ade_capture_manifest(output_dir)
    setup_entries = [
        item
        for item in manifest
        if item["category"] == "setup" and item["size_bytes"] > 0
    ]
    if not setup_entries:
        raise RuntimeError("ADE capture did not contain any setup evidence")

    selected_history = str(captured.get("latest_history") or requested_history)
    eda_result_entries = [
        item
        for item in manifest
        if item["category"] == "eda_result" and item["size_bytes"] > 0
    ]
    results: dict[str, Any] = {}
    if selected_history:
        results = read_results(
            client,
            session,
            lib=library,
            cell=cell,
            history=selected_history,
        )
        actual_result_history = str(results.get("history") or "")
        if actual_result_history and actual_result_history != selected_history:
            raise RuntimeError(
                "ADE structured results history does not match the captured history"
            )

    structured_outputs = _has_structured_ade_outputs(results)
    if settings.get("require_results", True) and (
        not selected_history or not eda_result_entries
    ):
        raise RuntimeError(
            "ADE capture did not contain a non-empty simulation history with EDA "
            "result artifacts"
        )
    if settings.get("require_structured_outputs", False) and not structured_outputs:
        raise RuntimeError(
            "ADE capture did not expose structured output/spec values for the selected "
            "history"
        )

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "session": session,
        "application": captured.get("app"),
        "mode": captured.get("mode"),
        "setup_saved": not bool(captured.get("unsaved")),
        "setup_evidence_source": "bridge_readback",
        "raw_setup_sections": captured.get("raw_sections") or [],
        "setup_fingerprint_sha256": _manifest_fingerprint(
            manifest, {"setup"}
        ),
        "requested_history": requested_history or None,
        "requested_history_evidence_source": (
            "user_input" if requested_history else None
        ),
        "selected_history": selected_history or None,
        "history_selection_evidence_source": (
            "user_input" if requested_history else "software_inference"
        ),
        "structured_results_available": structured_outputs,
        "structured_results": results,
        "structured_results_evidence_source": (
            "eda_result" if structured_outputs else None
        ),
        "simulation_fingerprint_sha256": _manifest_fingerprint(
            manifest, {"simulator_input", "eda_result", "run_log"}
        ),
        "artifact_directory": str(output_dir),
        "artifact_manifest": manifest,
        "artifact_counts": {
            category: sum(1 for item in manifest if item["category"] == category)
            for category in (
                "setup",
                "simulator_input",
                "eda_result",
                "run_log",
                "other",
            )
        },
        "automated_simulation_performed": False,
        "oa_write_performed": False,
        "completion_scope": (
            "human-operated ADE setup and existing result capture only; no VDA "
            "specification closure was inferred"
        ),
    }


def probe(payload: dict[str, Any]) -> dict[str, Any]:
    import virtuoso_bridge

    client = _client()
    result = client.execute_skill("1+2", timeout=15)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"Bridge SKILL probe failed: {errors[0]}")
    return {
        "connected": True,
        "bridge_version": virtuoso_bridge.__version__,
        "skill_probe": str(getattr(result, "output", "")).strip().strip('"'),
        "daemon_bind": getattr(client, "_vda_daemon_bind"),
        "profile": payload["profile"]["name"],
    }


def _parse_spectre_environment_probe(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tool_paths = [
        line
        for line in lines
        if re.fullmatch(r"/[A-Za-z0-9_./-]+/spectre", line)
    ]
    if len(tool_paths) != 1:
        raise RuntimeError("standalone Spectre probe did not return one tool path")
    return tool_paths[0]


def probe_spectre_environment(payload: dict[str, Any]) -> dict[str, Any]:
    """Probe the standalone simulator without starting Virtuoso or touching OA."""

    import virtuoso_bridge
    from virtuoso_bridge.transport.tunnel import SSHClient

    profile = payload["profile"]
    cadence_cshrc = str(profile.get("cadence_cshrc", ""))
    if re.fullmatch(r"/[A-Za-z0-9_./-]+", cadence_cshrc) is None:
        raise RuntimeError("invalid Cadence environment path in PDK profile")
    if not SSHClient.is_running():
        raise RuntimeError("no default virtuoso-bridge connection is running")
    ssh_client = _register_worker_resource(SSHClient.from_env(keep_remote_files=True))
    runner = ssh_client.ssh_runner
    runner._persistent_shell_enabled = False
    command = f"source {cadence_cshrc}; which spectre"
    result = runner.run_command(f"csh -fc {shlex.quote(command)}", timeout=25)
    if int(getattr(result, "returncode", -1)) != 0:
        detail = str(getattr(result, "stderr", "")).strip()
        raise RuntimeError(
            "standalone Spectre probe failed: " + (detail or "no stderr")
        )
    spectre_path = _parse_spectre_environment_probe(
        str(getattr(result, "stdout", ""))
    )
    return {
        "connected": True,
        "bridge_version": virtuoso_bridge.__version__,
        "profile": str(profile["name"]),
        "spectre_path": spectre_path,
        "spectre_version_source": "guarded_simulation_log",
        "virtuoso_started": False,
        "oa_access_performed": False,
        "oa_write_performed": False,
    }


def create_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
    )

    client = _client()
    library, cell = _target(payload)
    existing = _try_read_schematic(client, library, cell)
    if existing is not None and not payload.get("replace_existing", False):
        _assert_inverter(existing, payload["profile"])
        return {
            "created": False,
            "already_exists": True,
            "readback": _summary(existing),
        }
    if existing is not None:
        result = client.execute_skill(
            f'let((v) v=ddGetObj("{library}" "{cell}" "schematic") when(v ddDeleteObj(v)))',
            timeout=30,
        )
        errors = getattr(result, "errors", None) or []
        if errors:
            raise RuntimeError(f"delete existing schematic failed: {errors[0]}")

    profile = payload["profile"]
    with client.schematic.edit(library, cell, timeout=90) as schematic:
        schematic.add(
            inst(
                profile["tech_library"],
                profile["pmos_cell"],
                "symbol",
                "MP0",
                0.0,
                1.0,
                "R0",
            )
        )
        schematic.add(
            inst(
                profile["tech_library"],
                profile["nmos_cell"],
                "symbol",
                "MN0",
                0.0,
                0.0,
                "R0",
            )
        )
        schematic.add_net_label_to_transistor(
            "MP0", drain_net="OUT", gate_net="IN", source_net="VDD", body_net="VDD"
        )
        schematic.add_net_label_to_transistor(
            "MN0", drain_net="OUT", gate_net="IN", source_net="VSS", body_net="VSS"
        )
        schematic.add(pin("IN", -1.4, 0.5, "R0", direction="input"))
        schematic.add(pin("OUT", 1.4, 0.5, "R0", direction="output"))
        schematic.add(pin("VDD", 0.6, 1.6, "R0", direction="inputOutput"))
        schematic.add(pin("VSS", 0.6, -0.6, "R0", direction="inputOutput"))

    parameters = _resolved_parameters(payload)
    device_parameters = {
        name: parameters[name]
        for name in ("nmos_width_um", "pmos_width_um", "length_um")
    }
    readback = _apply_parameters(
        client, library, cell, device_parameters, profile
    )
    return {
        "created": True,
        "already_exists": False,
        "applied_device_parameters": device_parameters,
        "readback": readback,
    }


def inspect_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    data = _read_schematic(client, library, cell)
    _assert_inverter(data, payload["profile"])
    return _attach_targeted_parameter_verification(
        client, library, cell, payload, _summary(data)
    )


def apply_inverter_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    current = _read_schematic(client, library, cell)
    _assert_inverter(current, payload["profile"])
    result: dict[str, Any] = {}
    if payload.get("parameters"):
        parameters = _resolved_parameters(
            payload, _semantic_parameters_from_schematic(current)
        )
        device_parameters = {
            name: parameters[name]
            for name in ("nmos_width_um", "pmos_width_um", "length_um")
        }
        result = {
            "requested_parameters": payload["parameters"],
            "applied_device_parameters": device_parameters,
            "readback": _apply_parameters(
                client, library, cell, device_parameters, payload["profile"]
            ),
        }
    if payload.get("instance_parameter_updates"):
        explicit = _apply_explicit_instance_parameters(
            client, library, cell, payload
        )
        requested_semantic = {
            name: float(payload["parameters"][name])
            for name in ("nmos_width_um", "pmos_width_um", "length_um")
            if name in payload.get("parameters", {})
        }
        _assert_parameter_consistency(
            requested_semantic,
            explicit["readback"]["semantic_parameters"],
            expected_label="requested semantic write",
            actual_label="final OA readback after explicit CDF callbacks",
        )
        result.update(explicit)
        result["semantic_readback"] = explicit["readback"]["semantic_parameters"]
    return result


def _skill_string(value: str) -> str:
    if any(ord(character) < 32 for character in value):
        raise RuntimeError("OA identifiers and net names cannot contain control characters")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _instance_terminal_label_selection_operation(
    *,
    rename: bool,
    current_label: str,
    replacement_label: str,
    instance_name: str,
    terminal: str,
    missing_label_operation: str | None = None,
) -> str:
    instance_name = _skill_string(instance_name)
    terminal = _skill_string(terminal)
    current_label = _skill_string(current_label)
    replacement_label = _skill_string(replacement_label)
    selected_label_action = (
        f'rbLabel~>theLabel = "{replacement_label}" rbLabel'
        if rename
        else "rbLabel"
    )
    missing_label_action = (
        missing_label_operation
        if rename and missing_label_operation is not None
        else (
            f'error("{instance_name}.{terminal} {current_label} label is missing")'
        )
    )
    return (
        "let((rbInsts rbInst rbInstTerms rbInstTerm rbTerm rbPin rbFig rbBBox "
        "rbCtr rbLabels rbLabel rbWires) "
        f'rbInsts = setof(x cv~>instances x~>name == "{instance_name}") '
        f'unless(length(rbInsts) == 1 error("{instance_name} instance selection was not unique during topology transform")) '
        "rbInst = car(rbInsts) "
        f'rbInstTerms = setof(x rbInst~>instTerms x~>name == "{terminal}") '
        f'unless(length(rbInstTerms) == 1 error("{instance_name}.{terminal} instance terminal selection was not unique")) '
        "rbInstTerm = car(rbInstTerms) "
        "unless(rbInstTerm~>net && "
        f'rbInstTerm~>net~>name == "{current_label}" '
        f'error("{instance_name}.{terminal} connectivity changed before topology reconnect")) '
        f'rbTerm = car(setof(x rbInst~>master~>terminals x~>name == "{terminal}")) '
        f'unless(rbTerm error("{instance_name}.{terminal} not found during topology transform")) '
        "rbPin = car(rbTerm~>pins) "
        "rbFig = when(rbPin car(rbPin~>figs)) "
        "rbBBox = when(rbFig dbTransformBBox(rbFig~>bBox rbInst~>transform)) "
        "rbCtr = when(rbBBox list("
        "(xCoord(car(rbBBox)) + xCoord(cadr(rbBBox))) / 2.0 "
        "(yCoord(car(rbBBox)) + yCoord(cadr(rbBBox))) / 2.0)) "
        f'unless(rbCtr error("{instance_name}.{terminal} center could not be resolved")) '
        "rbLabels = setof(x cv~>shapes "
        f'x~>objType == "label" && x~>theLabel == "{current_label}" && x~>xy && '
        "let((dx dy) dx = xCoord(x~>xy) - xCoord(rbCtr) "
        "dy = yCoord(x~>xy) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 0.02)) "
        "rbWires = setof(x cv~>shapes "
        'x~>objType == "line" && x~>points && '
        "exists(rbPoint x~>points let((dx dy) "
        "dx = xCoord(rbPoint) - xCoord(rbCtr) "
        "dy = yCoord(rbPoint) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 1e-8))) "
        "cond("
        "(length(rbLabels) == 1 "
        f'unless(length(rbWires) == 1 error("{instance_name}.{terminal} terminal wire selection was not unique")) '
        "rbLabel = car(rbLabels) "
        f"{selected_label_action}) "
        "(length(rbLabels) == 0 "
        f'unless(length(rbWires) == 0 error("{instance_name}.{terminal} unlabeled terminal geometry is ambiguous")) '
        f"{missing_label_action}) "
        f'(t error("{instance_name}.{terminal} {current_label} label selection was not unique"))))'
    )


def _mn0_source_label_selection_operation(
    *,
    rename: bool,
    current_label: str = "VSS",
    replacement_label: str = "NSRC",
    instance_name: str = "MN0",
) -> str:
    return _instance_terminal_label_selection_operation(
        rename=rename,
        current_label=current_label,
        replacement_label=replacement_label,
        instance_name=instance_name,
        terminal="S",
    )


def _rename_mn0_source_label_operation() -> str:
    """Rename only the VDA-created VSS label nearest the MN0 source terminal."""
    return _mn0_source_label_selection_operation(rename=True)


def _restore_mn0_source_label_operation() -> str:
    """Restore only the VDA-created NSRC label nearest MN0.S to VSS."""
    return _mn0_source_label_selection_operation(
        rename=True,
        current_label="NSRC",
        replacement_label="VSS",
    )


def _instance_terminal_stub_selection_operation(
    terminal: str,
    net_name: str,
    *,
    delete: bool,
    instance_name: str = "RS0",
) -> str:
    """Select one VDA-created instance-terminal label and wire by geometry."""
    terminal = _skill_string(terminal)
    net_name = _skill_string(net_name)
    instance_name = _skill_string(instance_name)
    final_action = (
        "dbDeleteObject(rbLabel) dbDeleteObject(rbWire) t"
        if delete
        else "t"
    )
    return (
        "let((rbInst rbTerm rbPin rbFig rbBBox rbCtr rbLabels rbLabel rbWires "
        "rbWire) "
        f'rbInst = car(setof(x cv~>instances x~>name == "{instance_name}")) '
        f'unless(rbInst error("{instance_name} not found during topology transform")) '
        f'rbTerm = car(setof(x rbInst~>master~>terminals x~>name == "{terminal}")) '
        'unless(rbTerm error("instance terminal not found during topology transform")) '
        "rbPin = car(rbTerm~>pins) "
        "rbFig = when(rbPin car(rbPin~>figs)) "
        "rbBBox = when(rbFig dbTransformBBox(rbFig~>bBox rbInst~>transform)) "
        "rbCtr = when(rbBBox list("
        "(xCoord(car(rbBBox)) + xCoord(cadr(rbBBox))) / 2.0 "
        "(yCoord(car(rbBBox)) + yCoord(cadr(rbBBox))) / 2.0)) "
        f'unless(rbCtr error("{instance_name} terminal center could not be resolved")) '
        "rbLabels = setof(x cv~>shapes "
        f'x~>objType == "label" && x~>theLabel == "{net_name}" && x~>xy && '
        "let((dx dy) dx = xCoord(x~>xy) - xCoord(rbCtr) "
        "dy = yCoord(x~>xy) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 0.02)) "
        f'unless(length(rbLabels) == 1 error("{instance_name} terminal label selection was not unique")) '
        "rbLabel = car(rbLabels) "
        "rbWires = setof(x cv~>shapes "
        'x~>objType == "line" && x~>points && '
        "exists(rbPoint x~>points let((dx dy) "
        "dx = xCoord(rbPoint) - xCoord(rbCtr) "
        "dy = yCoord(rbPoint) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 1e-8))) "
        f'unless(length(rbWires) == 1 error("{instance_name} terminal wire selection was not unique")) '
        "rbWire = car(rbWires) "
        f"{final_action})"
    )


def _delete_source_degeneration_operation() -> str:
    return " ".join(
        [
            _restore_mn0_source_label_operation(),
            _instance_terminal_stub_selection_operation("PLUS", "NSRC", delete=True),
            _instance_terminal_stub_selection_operation("MINUS", "VSS", delete=True),
            "let((rbInst) "
            'rbInst = car(setof(x cv~>instances x~>name == "RS0")) '
            'unless(rbInst error("RS0 not found during source-degeneration removal")) '
            "dbDeleteObject(rbInst) t)",
        ]
    )


def _edit_existing_schematic(client, library: str, cell: str, *, timeout: int = 90):
    """Open an existing schematic for mutation without replacement semantics."""
    return client.schematic.edit(
        library, cell, mode="a", timeout=timeout
    )


def _generic_instance_placement(instance: TopologyInstance) -> tuple[float, float, str]:
    attributes = dict(instance.attributes)
    unknown = sorted(set(attributes) - {"xy", "orient", "numInst"})
    if unknown:
        raise RuntimeError(
            f"generic OA instance {instance.name!r} has unsupported structural "
            f"attributes: {unknown}"
        )
    xy = attributes.get("xy")
    orient = attributes.get("orient")
    if (
        not isinstance(xy, list)
        or len(xy) != 2
        or any(not isinstance(value, (int, float)) for value in xy)
        or any(not math.isfinite(float(value)) for value in xy)
    ):
        raise RuntimeError(
            f"generic OA instance {instance.name!r} requires finite xy=[x, y]"
        )
    if not isinstance(orient, str) or not orient:
        raise RuntimeError(
            f"generic OA instance {instance.name!r} requires an orientation"
        )
    if attributes.get("numInst", 1) != 1:
        raise RuntimeError(
            "generic OA topology delta currently supports only scalar instances"
        )
    return float(xy[0]), float(xy[1]), orient


_GENERIC_PIN_MASTER_BY_DIRECTION = {
    "input": "ipin",
    "output": "opin",
    "inputOutput": "iopin",
}
_GENERIC_PIN_ORIENTATIONS = {
    "R0",
    "R90",
    "R180",
    "R270",
    "MX",
    "MY",
    "MXR90",
    "MYR90",
}


def _generic_pin_geometry(
    pin: Any,
) -> tuple[float, float, str, str, str, str]:
    if pin.name != pin.net:
        raise RuntimeError(
            "generic OA pins currently require pin.name == pin.net"
        )
    if pin.direction not in _GENERIC_PIN_MASTER_BY_DIRECTION:
        raise RuntimeError(
            f"generic OA pin {pin.name!r} requires input/output/inputOutput direction"
        )
    attributes = dict(pin.attributes)
    unknown = sorted(
        set(attributes) - {"numBits", "master", "xy", "orient"}
    )
    if unknown:
        raise RuntimeError(
            f"generic OA pin {pin.name!r} has unsupported structural attributes: "
            f"{unknown}"
        )
    if attributes.get("numBits", 1) != 1:
        raise RuntimeError(
            "generic OA topology delta currently supports only scalar pins"
        )
    xy = attributes.get("xy")
    if (
        not isinstance(xy, list)
        or len(xy) != 2
        or any(not isinstance(value, (int, float)) for value in xy)
        or any(not math.isfinite(float(value)) for value in xy)
    ):
        raise RuntimeError(
            f"generic OA pin {pin.name!r} requires finite xy=[x, y]"
        )
    orient = attributes.get("orient")
    if orient not in _GENERIC_PIN_ORIENTATIONS:
        raise RuntimeError(
            f"generic OA pin {pin.name!r} has unsupported orientation {orient!r}"
        )
    master = attributes.get("master")
    if not isinstance(master, dict):
        raise RuntimeError(
            f"generic OA pin {pin.name!r} requires a physical master"
        )
    library = master.get("library")
    cell = master.get("cell")
    view = master.get("view")
    expected_cell = _GENERIC_PIN_MASTER_BY_DIRECTION[str(pin.direction)]
    if (library, cell, view) != ("basic", expected_cell, "symbol"):
        raise RuntimeError(
            f"generic OA pin {pin.name!r} master {(library, cell, view)!r} "
            f"does not match direction {pin.direction!r}"
        )
    return (
        float(xy[0]),
        float(xy[1]),
        str(orient),
        str(library),
        str(cell),
        str(view),
    )


def _generic_delete_pin_operation(pin: Any) -> str:
    x, y, orient, library, cell, view = _generic_pin_geometry(pin)
    name = _skill_string(pin.name)
    direction = _skill_string(str(pin.direction))
    return " ".join(
        [
            "let((rbTerms rbTerm rbPins rbPin rbFigs rbFig rbOrphanFigs)",
            f'rbTerms = setof(x cv~>terminals x~>name == "{name}")',
            f'unless(length(rbTerms) == 1 error("{name} logical pin selection was not unique"))',
            "rbTerm = car(rbTerms)",
            "unless("
            f'rbTerm~>direction == "{direction}" && '
            "if(rbTerm~>numBits rbTerm~>numBits 1) == 1 "
            f'error("{name} logical pin attributes changed before removal"))',
            "rbPins = rbTerm~>pins",
            f'unless(length(rbPins) == 1 error("{name} OA pin selection was not unique"))',
            "rbPin = car(rbPins)",
            "rbFigs = rbPin~>figs",
            f'unless(length(rbFigs) == 1 error("{name} pin figure selection was not unique"))',
            "rbFig = car(rbFigs)",
            "unless("
            'rbFig~>objType == "inst" && rbFig~>purpose == "pin" && '
            f'rbFig~>libName == "{_skill_string(library)}" && '
            f'rbFig~>cellName == "{_skill_string(cell)}" && '
            f'rbFig~>viewName == "{_skill_string(view)}" && '
            f'rbFig~>orient == "{_skill_string(orient)}" && '
            f"abs(xCoord(rbFig~>xy) - {x:.12g}) <= 1e-9 && "
            f"abs(yCoord(rbFig~>xy) - {y:.12g}) <= 1e-9 "
            f'error("{name} physical pin geometry changed before removal"))',
            # IC6.1.8 removes the logical term/pin hierarchy but can leave the
            # pin-symbol figure behind.  Re-select that physical figure from
            # the cellview after deleting the term, then delete it separately.
            "dbDeleteObject(rbTerm)",
            "rbOrphanFigs = setof(x cv~>instances "
            'x~>objType == "inst" && '
            '(x~>purpose == "pin" || x~>purpose == "cell") && '
            f'x~>libName == "{_skill_string(library)}" && '
            f'x~>cellName == "{_skill_string(cell)}" && '
            f'x~>viewName == "{_skill_string(view)}" && '
            f'x~>orient == "{_skill_string(orient)}" && '
            f"abs(xCoord(x~>xy) - {x:.12g}) <= 1e-9 && "
            f"abs(yCoord(x~>xy) - {y:.12g}) <= 1e-9)",
            f'unless(length(rbOrphanFigs) <= 1 error("{name} physical pin cleanup selection was not unique"))',
            "when(rbOrphanFigs dbDeleteObject(car(rbOrphanFigs)))",
            "t)",
        ]
    )


def _generic_delete_orphan_pin_figure_operation(pin: Any) -> str:
    """Delete one contract-bound physical pin figure with no logical terminal."""

    x, y, orient, library, cell, view = _generic_pin_geometry(pin)
    name = _skill_string(pin.name)
    return " ".join(
        [
            "let((rbOrphanFigs)",
            "rbOrphanFigs = setof(x cv~>instances "
            'x~>objType == "inst" && '
            '(x~>purpose == "pin" || x~>purpose == "cell") && '
            f'x~>libName == "{_skill_string(library)}" && '
            f'x~>cellName == "{_skill_string(cell)}" && '
            f'x~>viewName == "{_skill_string(view)}" && '
            f'x~>orient == "{_skill_string(orient)}" && '
            f"abs(xCoord(x~>xy) - {x:.12g}) <= 1e-9 && "
            f"abs(yCoord(x~>xy) - {y:.12g}) <= 1e-9)",
            f'unless(length(rbOrphanFigs) == 1 error("{name} orphan pin figure selection was not unique"))',
            "dbDeleteObject(car(rbOrphanFigs))",
            "t)",
        ]
    )


def _generic_delete_instance_operation(instance: TopologyInstance) -> str:
    operations = [
        _instance_terminal_stub_selection_operation(
            terminal,
            net,
            delete=True,
            instance_name=instance.name,
        )
        for terminal, net in sorted(instance.terminals.items())
    ]
    name = _skill_string(instance.name)
    operations.append(
        "let((rbInst) "
        f'rbInst = car(setof(x cv~>instances x~>name == "{name}")) '
        f'unless(rbInst error("{name} not found during generic topology removal")) '
        "dbDeleteObject(rbInst) t)"
    )
    return " ".join(operations)


def _generic_replace_master_operation(operation: ReplaceMasterOperation) -> str:
    """Build an instance-scoped master CAS without changing Bridge itself."""

    expected_view = operation.expected_master.view or "symbol"
    replacement_view = operation.master.view or "symbol"
    if expected_view != "symbol" or replacement_view != "symbol":
        raise RuntimeError(
            "generic OA master replacement currently requires symbol views"
        )
    name = _skill_string(operation.instance)
    old_library = _skill_string(operation.expected_master.library)
    old_cell = _skill_string(operation.expected_master.cell)
    new_library = _skill_string(operation.master.library)
    new_cell = _skill_string(operation.master.cell)
    return " ".join(
        [
            "let((rbInsts rbInst rbMaster)",
            f'rbInsts = setof(x cv~>instances x~>name == "{name}")',
            f'unless(length(rbInsts) == 1 error("{name} instance selection was not unique during master replacement"))',
            "rbInst = car(rbInsts)",
            "unless("
            f'rbInst~>libName == "{old_library}" && '
            f'rbInst~>cellName == "{old_cell}" && '
            'rbInst~>viewName == "symbol" '
            f'error("{name} master changed before replacement"))',
            "rbMaster = dbOpenCellViewByType("
            f'"{new_library}" "{new_cell}" "symbol" "schematicSymbol" "r")',
            f'unless(rbMaster error("replacement master unavailable for {name}"))',
            "rbInst~>master = rbMaster",
            "unless("
            f'rbInst~>libName == "{new_library}" && '
            f'rbInst~>cellName == "{new_cell}" && '
            'rbInst~>viewName == "symbol" '
            f'error("{name} master replacement did not apply"))',
            "t)",
        ]
    )


def _compile_generic_topology_commands(
    operations: list[Any],
    *,
    instance_builder=None,
    terminal_label_builder=None,
    pin_builder=None,
) -> tuple[list[str], list[str]]:
    """Compile bounded graph operations to the existing Bridge editor surface."""

    needs_instance_builder = any(
        isinstance(operation, AddInstanceOperation) for operation in operations
    )
    needs_terminal_label_builder = any(
        isinstance(operation, (AddInstanceOperation, ReconnectTerminalOperation))
        for operation in operations
    )
    if (
        needs_instance_builder and instance_builder is None
    ) or (needs_terminal_label_builder and terminal_label_builder is None):
        from virtuoso_bridge.virtuoso.schematic.ops import (
            schematic_create_inst_by_master_name,
            schematic_label_instance_term,
        )

        instance_builder = (
            instance_builder or schematic_create_inst_by_master_name
        )
        terminal_label_builder = (
            terminal_label_builder or schematic_label_instance_term
        )
    if any(isinstance(operation, AddPinOperation) for operation in operations):
        if pin_builder is None:
            from virtuoso_bridge.virtuoso.schematic.ops import schematic_create_pin

            pin_builder = schematic_create_pin

    commands: list[str] = []
    declarative_operations: list[str] = []
    for operation in operations:
        if isinstance(operation, (AddNetOperation, RemoveNetOperation)):
            # OA nets are materialized by the instance-terminal labels.  The
            # complete post-readback fingerprint proves creation/removal.
            declarative_operations.append(operation.operation)
        elif isinstance(operation, ReconnectTerminalOperation):
            assert terminal_label_builder is not None
            commands.append(
                _instance_terminal_label_selection_operation(
                    rename=True,
                    current_label=operation.expected_net,
                    replacement_label=operation.net,
                    instance_name=operation.instance,
                    terminal=operation.terminal,
                    missing_label_operation=terminal_label_builder(
                        operation.instance,
                        operation.terminal,
                        operation.net,
                    ),
                )
            )
        elif isinstance(operation, AddInstanceOperation):
            assert instance_builder is not None
            assert terminal_label_builder is not None
            instance = operation.instance
            x, y, orient = _generic_instance_placement(instance)
            commands.append(
                instance_builder(
                    instance.master.library,
                    instance.master.cell,
                    instance.master.view or "symbol",
                    instance.name,
                    x,
                    y,
                    orient,
                )
            )
            commands.extend(
                terminal_label_builder(instance.name, terminal, net)
                for terminal, net in sorted(instance.terminals.items())
            )
        elif isinstance(operation, RemoveInstanceOperation):
            _generic_instance_placement(operation.expected)
            commands.append(_generic_delete_instance_operation(operation.expected))
        elif isinstance(operation, ReplaceMasterOperation):
            commands.append(_generic_replace_master_operation(operation))
        elif isinstance(operation, AddPinOperation):
            assert pin_builder is not None
            pin = operation.pin
            x, y, orient, _library, _cell, _view = _generic_pin_geometry(pin)
            commands.append(
                pin_builder(
                    pin.name,
                    x,
                    y,
                    orient,
                    direction=pin.direction,
                )
            )
        elif isinstance(operation, RemovePinOperation):
            commands.append(_generic_delete_pin_operation(operation.expected))
        else:  # pragma: no cover - exhaustive typed operation union
            raise AssertionError(f"unsupported generic topology operation: {operation}")
    return commands, declarative_operations


def _generic_topology_allowed_master_libraries(
    before_snapshot: Any,
    operations: list[Any],
    profile: dict[str, Any],
    target_library: str,
) -> list[str]:
    allowed = {
        item.master.library for item in before_snapshot.instances
    } | {"analogLib", str(profile["tech_library"]), target_library}
    requested = {
        operation.instance.master.library
        for operation in operations
        if isinstance(operation, AddInstanceOperation)
    } | {
        operation.master.library
        for operation in operations
        if isinstance(operation, ReplaceMasterOperation)
    }
    disallowed = sorted(requested - allowed)
    if disallowed:
        raise RuntimeError(
            "generic topology delta requested master libraries outside the "
            "existing/profile boundary: " + ", ".join(disallowed)
        )
    return sorted(allowed)


def _directed_master_parameter_migrations(
    execution: TopologyDeltaExecutionSpec,
) -> list[MasterParameterMigration]:
    operations = (
        execution.contract.operations
        if execution.direction == "forward"
        else execution.contract.inverse_operations
    )
    replacement_instances = [
        operation.instance
        for operation in operations
        if isinstance(operation, ReplaceMasterOperation)
    ]
    if len(replacement_instances) != len(set(replacement_instances)):
        raise RuntimeError(
            "generic OA execution supports at most one master replacement per instance"
        )
    migrations = execution.contract.master_parameter_migrations
    migration_instances = {migration.instance for migration in migrations}
    if set(replacement_instances) != migration_instances:
        missing = sorted(set(replacement_instances) - migration_instances)
        extra = sorted(migration_instances - set(replacement_instances))
        raise RuntimeError(
            "generic OA master replacement requires one explicit CDF parameter "
            f"migration per replaced instance: missing={missing}, extra={extra}"
        )
    if execution.direction == "forward":
        return list(migrations)
    return [
        MasterParameterMigration(
            instance=migration.instance,
            expected_parameters=migration.parameters,
            parameters=migration.expected_parameters,
            undeclared_parameter_policy=migration.undeclared_parameter_policy,
        )
        for migration in migrations
    ]


def _preflight_generic_master_replacements(
    client,
    library: str,
    cell: str,
    before_snapshot: TopologySnapshot,
    operations: list[Any],
    migrations: list[MasterParameterMigration],
) -> None:
    replacements = [
        operation
        for operation in operations
        if isinstance(operation, ReplaceMasterOperation)
    ]
    if not replacements:
        return
    by_instance = {item.name: item for item in before_snapshot.instances}
    migration_by_instance = {item.instance: item for item in migrations}
    checks: list[str] = []
    for operation in replacements:
        expected_view = operation.expected_master.view or "symbol"
        replacement_view = operation.master.view or "symbol"
        if expected_view != "symbol" or replacement_view != "symbol":
            raise RuntimeError(
                "generic OA master replacement currently requires symbol views"
            )
        instance = by_instance.get(operation.instance)
        if instance is None:
            raise RuntimeError(
                f"master replacement instance missing from input: {operation.instance}"
            )
        migration = migration_by_instance[operation.instance]
        name = _skill_string(operation.instance)
        old_library = _skill_string(operation.expected_master.library)
        old_cell = _skill_string(operation.expected_master.cell)
        new_library = _skill_string(operation.master.library)
        new_cell = _skill_string(operation.master.cell)
        checks.extend(
            [
                f'rbInsts = setof(x rbCv~>instances x~>name == "{name}")',
                f'unless(length(rbInsts) == 1 error("{name} instance selection was not unique during master preflight"))',
                "rbInst = car(rbInsts)",
                "unless("
                f'rbInst~>libName == "{old_library}" && '
                f'rbInst~>cellName == "{old_cell}" && '
                'rbInst~>viewName == "symbol" '
                f'error("{name} master changed before preflight"))',
                "rbMaster = dbOpenCellViewByType("
                f'"{new_library}" "{new_cell}" "symbol" "schematicSymbol" "r")',
                f'unless(rbMaster error("replacement master unavailable for {name}"))',
                "unless(length(rbMaster~>terminals) == "
                f'{len(instance.terminals)} error("replacement terminal count mismatch for {name}"))',
            ]
        )
        for terminal in sorted(instance.terminals):
            escaped_terminal = _skill_string(terminal)
            checks.extend(
                [
                    "rbOldTerm = car(setof(x rbInst~>master~>terminals "
                    f'x~>name == "{escaped_terminal}"))',
                    "rbNewTerm = car(setof(x rbMaster~>terminals "
                    f'x~>name == "{escaped_terminal}"))',
                    f'unless(rbOldTerm && rbNewTerm error("replacement terminal missing for {name}.{escaped_terminal}"))',
                    "unless(length(rbOldTerm~>pins) == 1 && "
                    "length(rbNewTerm~>pins) == 1 "
                    f'error("replacement pin count mismatch for {name}.{escaped_terminal}"))',
                    "rbOldPin = car(rbOldTerm~>pins)",
                    "rbNewPin = car(rbNewTerm~>pins)",
                    f'unless(rbOldPin && rbNewPin error("replacement pin figure missing for {name}.{escaped_terminal}"))',
                    "unless(length(rbOldPin~>figs) == 1 && "
                    "length(rbNewPin~>figs) == 1 "
                    f'error("replacement terminal figure count mismatch for {name}.{escaped_terminal}"))',
                    "rbOldFig = car(rbOldPin~>figs)",
                    "rbNewFig = car(rbNewPin~>figs)",
                    f'unless(rbOldFig && rbNewFig error("replacement terminal figure missing for {name}.{escaped_terminal}"))',
                    "unless(equal(rbOldFig~>bBox rbNewFig~>bBox) "
                    f'error("replacement terminal geometry mismatch for {name}.{escaped_terminal}"))',
                    "unless(rbOldTerm~>direction == rbNewTerm~>direction "
                    f'error("replacement terminal direction mismatch for {name}.{escaped_terminal}"))',
                ]
            )
        checks.extend(
            [
                "rbCellCDF = cdfGetCellCDF("
                f'ddGetObj("{new_library}" "{new_cell}"))',
                f'unless(rbCellCDF error("replacement cell CDF unavailable for {name}"))',
            ]
        )
        checks.extend(
            f'unless(get(rbCellCDF "{_skill_string(parameter)}") '
            f'error("replacement CDF parameter missing for {name}.{_skill_string(parameter)}"))'
            for parameter in migration.parameters
        )
    skill = " ".join(
        [
            "let((rbCv rbInsts rbInst rbMaster rbCellCDF rbOldTerm rbNewTerm "
            "rbOldPin rbNewPin rbOldFig rbNewFig)",
            "rbCv = dbOpenCellViewByType("
            f'"{_skill_string(library)}" "{_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(rbCv error("target schematic missing during master preflight"))',
            'when(rbCv~>modified error("target schematic has unsaved changes"))',
            *checks,
            "t)",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"generic master replacement preflight failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            f"unexpected generic master replacement preflight result: {output!r}"
        )


def _apply_exact_master_parameter_migrations(
    client,
    library: str,
    cell: str,
    migrations: list[MasterParameterMigration],
) -> dict[str, Any]:
    requested = {
        migration.instance: dict(migration.parameters) for migration in migrations
    }
    if not requested:
        return {
            "requested": {},
            "applied": {},
            "confirmed": {},
            "application_method": "not_applicable",
        }
    applied: dict[str, dict[str, str]] = {}
    for instance, parameters in requested.items():
        result = _set_target_instance_params(
            client,
            library,
            cell,
            instance,
            param_filters=None,
            strict=True,
            **parameters,
        )
        normalized = {
            str(name): str(value) for name, value in dict(result or {}).items()
        }
        if normalized != parameters:
            raise RuntimeError(
                "master parameter migration did not apply the exact declared map "
                f"for {instance}: requested={parameters!r}, applied={normalized!r}"
            )
        applied[instance] = normalized
    application_method = "bridge_batch"
    try:
        confirmed = _verify_instance_parameter_values(
            client, library, cell, requested
        )
    except ParameterReadbackMismatch:
        application_method = "bridge_batch_then_ordered_replay"
        for instance, parameters in requested.items():
            replayed: dict[str, str] = {}
            for name, value in parameters.items():
                result = _set_target_instance_params(
                    client,
                    library,
                    cell,
                    instance,
                    param_filters=None,
                    strict=True,
                    **{name: value},
                )
                replayed.update(
                    {
                        str(actual_name): str(actual_value)
                        for actual_name, actual_value in dict(result or {}).items()
                    }
                )
            if replayed != parameters:
                raise RuntimeError(
                    "ordered master parameter migration replay did not apply the "
                    f"exact declared map for {instance}"
                )
        confirmed = _verify_instance_parameter_values(
            client, library, cell, requested
        )
    return {
        "requested": requested,
        "requested_evidence_source": "user_input",
        "applied": applied,
        "confirmed": confirmed,
        "confirmed_evidence_source": "bridge_readback",
        "confirmation_method": "independent_targeted_cdf_equality",
        "application_method": application_method,
        "undeclared_parameter_policy": "record_only",
    }


def _preflight_mn0_source_label(client, library: str, cell: str) -> None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    skill = " ".join(
        [
            "let((cv rbLabel)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during transform preflight"))',
            'when(cv~>modified error("target schematic has unsaved changes"))',
            f"rbLabel = {_mn0_source_label_selection_operation(rename=False)}",
            "if(rbLabel t nil))",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"source-degeneration transform preflight failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            f"unexpected source-degeneration preflight result: {output!r}"
        )


def _preflight_source_degeneration_removal(
    client, library: str, cell: str
) -> None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    skill = " ".join(
        [
            "let((cv rbLabel)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during transform preflight"))',
            'when(cv~>modified error("target schematic has unsaved changes"))',
            "rbLabel = "
            + _mn0_source_label_selection_operation(
                rename=False,
                current_label="NSRC",
                replacement_label="VSS",
            ),
            _instance_terminal_stub_selection_operation("PLUS", "NSRC", delete=False),
            _instance_terminal_stub_selection_operation("MINUS", "VSS", delete=False),
            "if(rbLabel t nil))",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"source-degeneration removal preflight failed: {errors[0]}"
        )
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            f"unexpected source-degeneration removal preflight result: {output!r}"
        )


def _differential_source_label_operation(
    instance_name: str,
    *,
    rename: bool,
    current_label: str,
    replacement_label: str,
) -> str:
    return _mn0_source_label_selection_operation(
        rename=rename,
        current_label=current_label,
        replacement_label=replacement_label,
        instance_name=instance_name,
    )


def _preflight_differential_source_degeneration(
    client, library: str, cell: str, *, remove: bool
) -> None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    selections: list[str] = []
    if remove:
        selections.extend(
            [
                _differential_source_label_operation(
                    "MN0",
                    rename=False,
                    current_label="NSP",
                    replacement_label="TAIL",
                ),
                _differential_source_label_operation(
                    "MN1",
                    rename=False,
                    current_label="NSN",
                    replacement_label="TAIL",
                ),
                _instance_terminal_stub_selection_operation(
                    "PLUS", "NSP", delete=False, instance_name="RS0"
                ),
                _instance_terminal_stub_selection_operation(
                    "MINUS", "TAIL", delete=False, instance_name="RS0"
                ),
                _instance_terminal_stub_selection_operation(
                    "PLUS", "NSN", delete=False, instance_name="RS1"
                ),
                _instance_terminal_stub_selection_operation(
                    "MINUS", "TAIL", delete=False, instance_name="RS1"
                ),
            ]
        )
    else:
        selections.extend(
            [
                _differential_source_label_operation(
                    "MN0",
                    rename=False,
                    current_label="TAIL",
                    replacement_label="NSP",
                ),
                _differential_source_label_operation(
                    "MN1",
                    rename=False,
                    current_label="TAIL",
                    replacement_label="NSN",
                ),
            ]
        )
    skill = " ".join(
        [
            "let((cv)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during transform preflight"))',
            'when(cv~>modified error("target schematic has unsaved changes"))',
            *selections,
            "t)",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        label = "removal" if remove else "addition"
        raise RuntimeError(
            f"differential source-degeneration {label} preflight failed: "
            f"{errors[0]}"
        )
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            "unexpected differential source-degeneration preflight result: "
            f"{output!r}"
        )


def _delete_differential_source_degeneration_operation() -> str:
    operations = [
        _differential_source_label_operation(
            "MN0",
            rename=True,
            current_label="NSP",
            replacement_label="TAIL",
        ),
        _differential_source_label_operation(
            "MN1",
            rename=True,
            current_label="NSN",
            replacement_label="TAIL",
        ),
    ]
    for instance_name, source_net in (("RS0", "NSP"), ("RS1", "NSN")):
        operations.extend(
            [
                _instance_terminal_stub_selection_operation(
                    "PLUS", source_net, delete=True, instance_name=instance_name
                ),
                _instance_terminal_stub_selection_operation(
                    "MINUS", "TAIL", delete=True, instance_name=instance_name
                ),
                "let((rbInst) "
                f'rbInst = car(setof(x cv~>instances x~>name == "{instance_name}")) '
                f'unless(rbInst error("{instance_name} not found during source-degeneration removal")) '
                "dbDeleteObject(rbInst) t)",
            ]
        )
    return " ".join(operations)


def _instance_terminal_stub_cleanup_operation(
    instance_name: str, terminal_nets: tuple[tuple[str, str], ...]
) -> str:
    operations = [
        _instance_terminal_stub_selection_operation(
            terminal, net_name, delete=True, instance_name=instance_name
        )
        for terminal, net_name in terminal_nets
    ]
    operations.append(
        "let((rbInst) "
        f'rbInst = car(setof(x cv~>instances x~>name == "{instance_name}")) '
        f'unless(rbInst error("{instance_name} not found during load transform")) '
        "dbDeleteObject(rbInst) t)"
    )
    return " ".join(operations)


def _preflight_differential_pair_load_transform(
    client, library: str, cell: str, *, current_mirror_to_resistors: bool
) -> None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    if current_mirror_to_resistors:
        terminal_sets = (
            ("MP0", (("D", "OUTP"), ("G", "OUTP"), ("S", "VDD"), ("B", "VDD"))),
            ("MP1", (("D", "OUTN"), ("G", "OUTP"), ("S", "VDD"), ("B", "VDD"))),
        )
    else:
        terminal_sets = (
            ("RD0", (("PLUS", "VDD"), ("MINUS", "OUTP"))),
            ("RD1", (("PLUS", "VDD"), ("MINUS", "OUTN"))),
        )
    selections = [
        _instance_terminal_stub_selection_operation(
            terminal, net_name, delete=False, instance_name=instance_name
        )
        for instance_name, terminal_nets in terminal_sets
        for terminal, net_name in terminal_nets
    ]
    skill = " ".join(
        [
            "let((cv)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during load preflight"))',
            'when(cv~>modified error("target schematic has unsaved changes"))',
            *selections,
            "t)",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"differential load transform preflight failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            f"unexpected differential load preflight result: {output!r}"
        )


def _discard_failed_existing_schematic_edit(
    client, library: str, cell: str
) -> None:
    """Purge only an unsaved target view left by a failed VDA edit batch."""
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    skill = (
        "let((rbCv) "
        "rbCv = dbOpenCellViewByType("
        f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
        '"schematic" "schematic" "r") '
        'unless(rbCv error("target schematic missing during failed-edit cleanup")) '
        "when(rbCv~>modified "
        'unless(dbPurge(rbCv) error("failed to purge unsaved target edit"))) '
        "t)"
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"failed-edit cleanup failed: {errors[0]}")


def _run_generic_topology_editor_commands(
    client,
    library: str,
    cell: str,
    commands: list[str],
) -> None:
    if not commands:
        return
    try:
        with _edit_existing_schematic(client, library, cell, timeout=120) as schematic:
            for command in commands:
                schematic.add(command)
    except Exception as edit_error:
        try:
            _discard_failed_existing_schematic_edit(client, library, cell)
        except Exception as cleanup_error:
            raise RuntimeError(
                "generic topology edit failed and unsaved-edit cleanup also "
                f"failed: {cleanup_error}"
            ) from edit_error
        raise


def _attempt_generic_topology_inverse_recovery(
    client,
    library: str,
    cell: str,
    execution: TopologyDeltaExecutionSpec,
    expected_written_snapshot: TopologySnapshot,
    original_input_summary: dict[str, Any],
    original_input_parameters: dict[str, dict[str, str]],
    original_input_placement: dict[str, Any],
) -> dict[str, Any]:
    """Undo a saved delta only after a fresh read proves its exact output state."""

    expected_written_sha256 = topology_fingerprint(expected_written_snapshot)
    try:
        current = _read_schematic(client, library, cell)
        current_pin_geometry, current_placement = _schematic_geometry_bundle(
            client, library, cell
        )
    except Exception as error:
        return {
            "status": "state_unknown_no_write",
            "reason": f"{type(error).__name__}: {error}",
            "decision_source": "system_event",
        }
    current_summary = _existing_schematic_summary(
        current,
        pin_geometry=current_pin_geometry,
        placement=current_placement,
    )
    try:
        current_snapshot = snapshot_from_inspection(current_summary)
    except Exception as error:
        return {
            "status": "state_unverifiable_no_write",
            "reason": f"{type(error).__name__}: {error}",
            "state_readback_source": "bridge_readback",
            "decision_source": "software_inference",
        }
    current_sha256 = topology_fingerprint(current_snapshot)
    if current_sha256 != expected_written_sha256:
        return {
            "status": "unexpected_topology_no_write",
            "expected_written_topology_sha256": expected_written_sha256,
            "actual_topology_sha256": current_sha256,
            "state_readback_source": "bridge_readback",
            "decision_source": "software_inference",
        }

    recovery_direction = "inverse" if execution.direction == "forward" else "forward"
    recovery_execution = TopologyDeltaExecutionSpec(
        direction=recovery_direction,
        contract=execution.contract,
    )
    recovery_operations = (
        recovery_execution.contract.operations
        if recovery_direction == "forward"
        else recovery_execution.contract.inverse_operations
    )
    recovery_migrations = _directed_master_parameter_migrations(
        recovery_execution
    )
    try:
        _preflight_generic_master_replacements(
            client,
            library,
            cell,
            current_snapshot,
            recovery_operations,
            recovery_migrations,
        )
        commands, declarative_operations = _compile_generic_topology_commands(
            recovery_operations
        )
        _run_generic_topology_editor_commands(
            client, library, cell, commands
        )
        parameter_restoration = _apply_exact_master_parameter_migrations(
            client,
            library,
            cell,
            recovery_migrations,
        )
        restored = _read_schematic(client, library, cell)
        restored_pin_geometry, restored_placement = _schematic_geometry_bundle(
            client, library, cell
        )
        restored_summary = _existing_schematic_summary(
            restored,
            pin_geometry=restored_pin_geometry,
            placement=restored_placement,
        )
        audit = validate_topology_execution_readback(
            current_summary,
            restored_summary,
            recovery_execution,
        )
        restored_parameters = _instance_parameters_from_schematic(restored)
        if restored_parameters != original_input_parameters:
            changed = sorted(
                name
                for name in set(restored_parameters) | set(original_input_parameters)
                if restored_parameters.get(name) != original_input_parameters.get(name)
            )
            raise RuntimeError(
                "inverse topology restored but complete instance parameters did "
                "not return to the input readback: " + ", ".join(changed)
            )
        restored_sha256 = topology_fingerprint(
            snapshot_from_inspection(restored_summary)
        )
        original_sha256 = topology_fingerprint(
            snapshot_from_inspection(original_input_summary)
        )
        if restored_sha256 != original_sha256:
            raise RuntimeError(
                "inverse recovery did not restore the original topology fingerprint"
            )
        if restored_placement["sha256"] != original_input_placement["sha256"]:
            raise RuntimeError(
                "inverse recovery did not restore the original wire/label/pin "
                "placement fingerprint"
            )
        return {
            "status": "restored",
            "recovery_direction": recovery_direction,
            "recovery_operation_count": len(recovery_operations),
            "compiled_editor_command_count": len(commands),
            "declarative_net_operations": declarative_operations,
            "expected_written_topology_sha256": expected_written_sha256,
            "confirmed_written_topology_sha256": current_sha256,
            "restored_topology_sha256": restored_sha256,
            "written_placement": current_placement,
            "restored_placement": restored_placement,
            "restored_placement_match": True,
            "restored_instance_parameters": True,
            "parameter_restoration": parameter_restoration,
            "contract_audit": audit.model_dump(mode="json"),
            "state_readback_source": "bridge_readback",
            "decision_source": "software_inference",
        }
    except Exception as error:
        return {
            "status": "recovery_failed",
            "reason": f"{type(error).__name__}: {error}",
            "expected_written_topology_sha256": expected_written_sha256,
            "confirmed_written_topology_sha256": current_sha256,
            "state_readback_source": "bridge_readback",
            "decision_source": "system_event",
        }


def _mn0_ground_label_selection_operation(
    terminal: str, *, rename: bool
) -> str:
    final_action = 'rbLabel~>theLabel = "gnd!" rbLabel' if rename else "rbLabel"
    return (
        "let((rbInst rbTerm rbPin rbFig rbBBox rbCtr rbLabels rbLabel rbDx rbDy "
        "rbTermName) "
        'rbInst = car(setof(x cv~>instances x~>name == "MN0")) '
        'unless(rbInst error("MN0 not found during inverter-testbench transform")) '
        f'rbTermName = "{terminal}" '
        "rbTerm = car(setof(x rbInst~>master~>terminals "
        "x~>name == rbTermName)) "
        'unless(rbTerm error("MN0 terminal not found during inverter-testbench transform")) '
        "rbPin = car(rbTerm~>pins) "
        "rbFig = when(rbPin car(rbPin~>figs)) "
        "rbBBox = when(rbFig dbTransformBBox(rbFig~>bBox rbInst~>transform)) "
        "rbCtr = when(rbBBox list("
        "(xCoord(car(rbBBox)) + xCoord(cadr(rbBBox))) / 2.0 "
        "(yCoord(car(rbBBox)) + yCoord(cadr(rbBBox))) / 2.0)) "
        'unless(rbCtr error("MN0 terminal center could not be resolved")) '
        "rbLabels = setof(x cv~>shapes "
        'x~>objType == "label" && x~>theLabel == "VSS" && x~>xy && '
        "let((dx dy) dx = xCoord(x~>xy) - xCoord(rbCtr) "
        "dy = yCoord(x~>xy) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 0.02)) "
        'unless(length(rbLabels) == 1 error("MN0 VSS label selection was not unique")) '
        "rbLabel = car(rbLabels) "
        f"{final_action})"
    )


def _rename_inverter_ground_labels_operation() -> str:
    """Ground only the VDA-created VSS labels nearest MN0.S and MN0.B."""

    return " ".join(
        _mn0_ground_label_selection_operation(terminal, rename=True)
        for terminal in ("S", "B")
    )


def _preflight_inverter_ground_labels(client, library: str, cell: str) -> None:
    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    selections = " ".join(
        _mn0_ground_label_selection_operation(terminal, rename=False)
        for terminal in ("S", "B")
    )
    skill = " ".join(
        [
            "let((cv)",
            "cv = dbOpenCellViewByType("
            f'"{escape_skill_string(library)}" "{escape_skill_string(cell)}" '
            '"schematic" "schematic" "r")',
            'unless(cv error("target schematic not found during transform preflight"))',
            'when(cv~>modified error("target schematic has unsaved changes"))',
            selections,
            "t)",
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"inverter-testbench transform preflight failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output != "t":
        raise RuntimeError(
            f"unexpected inverter-testbench preflight result: {output!r}"
        )


def _assert_inverter_testbench_transform_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    vdd_v: float,
    load_ff: float,
) -> None:
    before_variant = _assert_inverter(before)
    after_variant = _assert_inverter(after)
    if after_variant != "inverter_testbench":
        raise RuntimeError("inverter-testbench transform did not produce the testbench")
    if before.get("pins") != after.get("pins"):
        raise RuntimeError("inverter-testbench transform changed top-level pins")
    before_nets = set((before.get("nets") or {}).keys())
    after_nets = set((after.get("nets") or {}).keys())
    expected_nets = before_nets | ({"gnd!"} if before_variant == "inverter_core" else set())
    if after_nets != expected_nets:
        raise RuntimeError(
            "inverter-testbench transform changed nets beyond grounding the testbench"
        )

    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MP0"):
        before_without_terms = {
            key: value for key, value in before_by_name[name].items() if key != "terms"
        }
        after_without_terms = {
            key: value for key, value in after_by_name[name].items() if key != "terms"
        }
        if before_without_terms != after_without_terms:
            raise RuntimeError(
                f"inverter-testbench transform changed {name} beyond grounding"
            )
    if before_variant == "inverter_testbench":
        for name in ("MN0", "MP0"):
            if before_by_name[name].get("terms") != after_by_name[name].get("terms"):
                raise RuntimeError(
                    f"repeated inverter-testbench transform changed {name} topology"
                )

    expected_parameters = {
        "VDD0": {"vdc": f"{vdd_v:.12g}", "srcType": "dc"},
        "VIN0": {
            "v1": "0",
            "v2": f"{vdd_v:.12g}",
            "per": "100p",
            "td": "0",
            "tr": "5p",
            "tf": "5p",
            "pw": "50p",
            "srcType": "pulse",
        },
        "CL0": {"c": f"{load_ff:.12g}f"},
    }
    for instance, parameters in expected_parameters.items():
        actual_parameters = after_by_name[instance].get("params") or {}
        for name, expected in parameters.items():
            actual = actual_parameters.get(name)
            if actual is None or not spectre_values_equal(actual, expected):
                raise RuntimeError(
                    f"inverter-testbench {instance}.{name} mismatch: expected "
                    f"{expected!r}, got {actual!r}"
                )


def transform_inverter_testbench(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_label_instance_term as label_term,
    )
    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    variant = _assert_inverter(before, payload["profile"])
    topology_changed = variant == "inverter_core"
    if topology_changed:
        _preflight_inverter_ground_labels(client, library, cell)
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=120
            ) as schematic:
                schematic.add(_rename_inverter_ground_labels_operation())
                schematic.add(
                    inst("analogLib", "vdc", "symbol", "VDD0", 2.5, 1.5, "R0")
                )
                schematic.add(
                    inst("analogLib", "vpulse", "symbol", "VIN0", -2.5, 0.5, "R0")
                )
                schematic.add(
                    inst("analogLib", "cap", "symbol", "CL0", 2.5, 0.0, "R0")
                )
                schematic.add(
                    inst("analogLib", "gnd", "symbol", "GND0", 0.0, -2.0, "R0")
                )
                schematic.add(label_term("VDD0", "PLUS", "VDD"))
                schematic.add(label_term("VDD0", "MINUS", "gnd!"))
                schematic.add(label_term("VIN0", "PLUS", "IN"))
                schematic.add(label_term("VIN0", "MINUS", "gnd!"))
                schematic.add(label_term("CL0", "PLUS", "OUT"))
                schematic.add(label_term("CL0", "MINUS", "gnd!"))
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "inverter-testbench edit failed and unsaved-edit cleanup also "
                    f"failed: {cleanup_error}"
                ) from edit_error
            raise

    vdd_v = float(payload["parameters"]["vdd_v"])
    load_ff = float(payload["parameters"]["load_ff"])
    _set_target_instance_params(
        client,
        library,
        cell,
        "VDD0",
        param_filters=None,
        vdc=f"{vdd_v:.12g}",
        srcType="dc",
    )
    _set_target_instance_params(
        client,
        library,
        cell,
        "VIN0",
        param_filters=None,
        v1="0",
        v2=f"{vdd_v:.12g}",
        per="100p",
        td="0",
        tr="5p",
        tf="5p",
        pw="50p",
        srcType="pulse",
    )
    _set_target_instance_params(
        client,
        library,
        cell,
        "CL0",
        param_filters=None,
        c=f"{load_ff:.12g}f",
    )
    after = _read_schematic(client, library, cell)
    _assert_inverter_testbench_transform_preserved(before, after, vdd_v, load_ff)
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
        "topology_delta": {
            "grounded_terminals": ["MN0.S", "MN0.B"] if topology_changed else [],
            "added_instances": (
                ["VDD0", "VIN0", "CL0", "GND0"] if topology_changed else []
            ),
            "preserved_instances": ["MN0", "MP0"],
            "preserved_pins": sorted((before.get("pins") or {}).keys()),
        },
        "requested_testbench_parameters": {"vdd_v": vdd_v, "load_ff": load_ff},
        "readback": _summary(after),
    }


def _assert_common_source_transform_preserved(
    before: dict[str, Any], after: dict[str, Any], source_resistance_ohm: float
) -> None:
    before_variant = _assert_common_source(before)
    after_variant = _assert_common_source(after)
    if after_variant != "source_degenerated_common_source":
        raise RuntimeError("source-degeneration transform did not produce RS0/NSRC")
    if before.get("pins") != after.get("pins"):
        raise RuntimeError("source-degeneration transform changed top-level pins")
    before_nets = set(before.get("nets", {}).keys())
    after_nets = set(after.get("nets", {}).keys())
    if after_nets != before_nets | {"NSRC"}:
        raise RuntimeError(
            "source-degeneration transform changed nets beyond adding NSRC"
        )

    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    before_mn0_without_terms = {
        key: value for key, value in before_by_name["MN0"].items() if key != "terms"
    }
    after_mn0_without_terms = {
        key: value for key, value in after_by_name["MN0"].items() if key != "terms"
    }
    if before_mn0_without_terms != after_mn0_without_terms:
        raise RuntimeError("source-degeneration transform changed MN0 beyond its S net")
    if before_by_name["RD0"] != after_by_name["RD0"]:
        raise RuntimeError("source-degeneration transform changed RD0")
    if before_variant == "source_degenerated_common_source":
        before_rs0_without_params = {
            key: value
            for key, value in before_by_name["RS0"].items()
            if key != "params"
        }
        after_rs0_without_params = {
            key: value
            for key, value in after_by_name["RS0"].items()
            if key != "params"
        }
        if before_rs0_without_params != after_rs0_without_params:
            raise RuntimeError(
                "repeated source-degeneration transform changed RS0 topology or placement"
            )
    semantic = _common_source_semantic_parameters_from_schematic(after)
    _assert_parameter_consistency(
        {"source_resistance_ohm": float(source_resistance_ohm)},
        semantic,
        expected_label="requested source degeneration",
        actual_label="OA readback",
    )


def _assert_common_source_removal_preserved(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    before_variant = _assert_common_source(before)
    after_variant = _assert_common_source(after)
    if after_variant != "common_source":
        raise RuntimeError(
            "source-degeneration removal did not restore the nominal topology"
        )
    if before.get("pins") != after.get("pins"):
        raise RuntimeError("source-degeneration removal changed top-level pins")
    before_nets = set(before.get("nets", {}).keys())
    after_nets = set(after.get("nets", {}).keys())
    expected_after_nets = before_nets - (
        {"NSRC"}
        if before_variant == "source_degenerated_common_source"
        else set()
    )
    if after_nets != expected_after_nets:
        raise RuntimeError(
            "source-degeneration removal changed nets beyond removing NSRC"
        )

    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    before_mn0_without_terms = {
        key: value for key, value in before_by_name["MN0"].items() if key != "terms"
    }
    after_mn0_without_terms = {
        key: value for key, value in after_by_name["MN0"].items() if key != "terms"
    }
    if before_mn0_without_terms != after_mn0_without_terms:
        raise RuntimeError("source-degeneration removal changed MN0 beyond its S net")
    if before_by_name["RD0"] != after_by_name["RD0"]:
        raise RuntimeError("source-degeneration removal changed RD0")
    semantic = _common_source_semantic_parameters_from_schematic(after)
    if "source_resistance_ohm" in semantic:
        raise RuntimeError(
            "source-degeneration removal left source_resistance_ohm in OA readback"
        )


def _remove_common_source_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_common_source(before, payload["profile"])
    topology_changed = variant == "source_degenerated_common_source"
    if topology_changed:
        _preflight_source_degeneration_removal(client, library, cell)
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(_delete_source_degeneration_operation())
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "source-degeneration removal failed and unsaved-edit cleanup "
                    f"also failed: {cleanup_error}"
                ) from edit_error
            raise
    after = _read_schematic(client, library, cell)
    placement_after = _schematic_placement_snapshot(client, library, cell)
    _assert_common_source_removal_preserved(before, after)
    transform_spec = payload.get("schematic_transform") or {}
    expected_placement = transform_spec.get(
        "expected_restored_placement_sha256"
    )
    if (
        expected_placement is not None
        and placement_after["sha256"] != expected_placement
    ):
        raise RuntimeError(
            "source-degeneration removal did not restore the declared placement "
            f"fingerprint: expected {expected_placement}, got "
            f"{placement_after['sha256']}"
        )
    return {
        "transformed": topology_changed,
        "already_removed": not topology_changed,
        "transform_action": "remove_source_degeneration",
        "placement_before": placement_before,
        "placement_after": placement_after,
        "restored_placement_match": (
            placement_after["sha256"] == expected_placement
            if expected_placement is not None
            else None
        ),
        "topology_delta": {
            "renamed_terminal_net": (
                "MN0.S: NSRC -> VSS" if topology_changed else None
            ),
            "removed_instance": "RS0" if topology_changed else None,
            "removed_net": "NSRC" if topology_changed else None,
            "preserved_instances": ["MN0", "RD0"],
            "preserved_pins": sorted(before.get("pins", {}).keys()),
        },
        "readback": _common_source_summary(after),
    }


def transform_common_source_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_label_instance_term as label_term,
    )

    transform_spec = payload.get("schematic_transform") or {}
    transform_action = transform_spec.get("action", "add_source_degeneration")
    if transform_action == "remove_source_degeneration":
        return _remove_common_source_source_degeneration(payload)
    if transform_action != "add_source_degeneration":
        raise RuntimeError(
            f"unsupported common-source transform action: {transform_action!r}"
        )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_common_source(before, payload["profile"])
    resistance = float(payload["parameters"]["source_resistance_ohm"])
    before_semantic = _common_source_semantic_parameters_from_schematic(before)
    topology_changed = variant == "common_source"

    if topology_changed:
        _preflight_mn0_source_label(client, library, cell)
        # This operation mutates an existing cellview.  Bridge's schematic
        # editor defaults to mode="w", which is appropriate for creation but
        # can replace existing OA contents.  Append mode is therefore part of
        # the transform safety contract, not an optional caller setting.
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(_rename_mn0_source_label_operation())
                schematic.add(
                    inst("analogLib", "res", "symbol", "RS0", 0.0, -1.3, "R0")
                )
                schematic.add(label_term("RS0", "PLUS", "NSRC"))
                schematic.add(label_term("RS0", "MINUS", "VSS"))
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "source-degeneration edit failed and unsaved-edit cleanup also "
                    f"failed: {cleanup_error}"
                ) from edit_error
            raise

    resistance_changed = (
        before_semantic.get("source_resistance_ohm") is None
        or abs(before_semantic["source_resistance_ohm"] - resistance)
        > max(abs(resistance) * 1e-6, 1e-9)
    )
    if resistance_changed:
        summary = _apply_common_source_parameters(
            client,
            library,
            cell,
            {"source_resistance_ohm": resistance},
            payload["profile"],
        )
        after = summary["bridge_schematic"]
    else:
        after = _read_schematic(client, library, cell)
        summary = _common_source_summary(after)
    _assert_common_source_transform_preserved(before, after, resistance)
    placement_after = _schematic_placement_snapshot(client, library, cell)
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
        "transform_action": "add_source_degeneration",
        "placement_before": placement_before,
        "placement_after": placement_after,
        "resistance_changed": resistance_changed,
        "topology_delta": {
            "renamed_terminal_net": "MN0.S: VSS -> NSRC" if topology_changed else None,
            "added_instance": "RS0" if topology_changed else None,
            "added_net": "NSRC" if topology_changed else None,
            "preserved_instances": ["MN0", "RD0"],
            "preserved_pins": sorted(before.get("pins", {}).keys()),
        },
        "readback": summary,
    }


def preflight_common_source_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    schematic = _read_schematic(client, library, cell)
    variant = _assert_common_source(schematic, payload["profile"])
    transform_spec = payload.get("schematic_transform") or {}
    transform_action = transform_spec.get("action", "add_source_degeneration")
    if transform_action == "add_source_degeneration" and variant == "common_source":
        _preflight_mn0_source_label(client, library, cell)
    elif (
        transform_action == "remove_source_degeneration"
        and variant == "source_degenerated_common_source"
    ):
        _preflight_source_degeneration_removal(client, library, cell)
    elif transform_action not in {
        "add_source_degeneration",
        "remove_source_degeneration",
    }:
        raise RuntimeError(
            f"unsupported common-source transform action: {transform_action!r}"
        )
    return {
        "target": payload["target"],
        "topology_variant": variant,
        "transform_action": transform_action,
        "source_label_selection": (
            "unique"
            if (
                transform_action == "add_source_degeneration"
                and variant == "common_source"
            )
            or (
                transform_action == "remove_source_degeneration"
                and variant == "source_degenerated_common_source"
            )
            else "not_applicable"
        ),
        "semantic_parameters": _common_source_semantic_parameters_from_schematic(
            schematic
        ),
    }


def create_common_source(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
        schematic_label_instance_term as label_term,
    )

    client = _client()
    library, cell = _target(payload)
    existing = _try_read_schematic(client, library, cell)
    if existing is not None and not payload.get("replace_existing", False):
        _assert_common_source(existing, payload["profile"])
        return {
            "created": False,
            "already_exists": True,
            "readback": _common_source_summary(existing),
        }
    if existing is not None:
        result = client.execute_skill(
            f'let((v) v=ddGetObj("{library}" "{cell}" "schematic") when(v ddDeleteObj(v)))',
            timeout=30,
        )
        errors = getattr(result, "errors", None) or []
        if errors:
            raise RuntimeError(f"delete existing schematic failed: {errors[0]}")

    profile = payload["profile"]
    with client.schematic.edit(library, cell, timeout=90) as schematic:
        schematic.add(
            inst(
                profile["tech_library"],
                profile["nmos_cell"],
                "symbol",
                "MN0",
                0.0,
                0.0,
                "R0",
            )
        )
        schematic.add(inst("analogLib", "res", "symbol", "RD0", 0.0, 1.3, "R0"))
        schematic.add_net_label_to_transistor(
            "MN0", drain_net="OUT", gate_net="IN", source_net="VSS", body_net="VSS"
        )
        schematic.add(label_term("RD0", "PLUS", "VDD"))
        schematic.add(label_term("RD0", "MINUS", "OUT"))
        schematic.add(pin("IN", -1.4, 0.0, "R0", direction="input"))
        schematic.add(pin("OUT", 1.4, 0.65, "R0", direction="output"))
        schematic.add(pin("VDD", 0.7, 1.8, "R0", direction="inputOutput"))
        schematic.add(pin("VSS", 0.7, -0.6, "R0", direction="inputOutput"))

    parameters = _resolved_common_source_parameters(payload)
    oa_parameters = {
        name: parameters[name]
        for name in ("device_width_um", "length_um", "load_resistance_ohm")
    }
    readback = _apply_common_source_parameters(
        client, library, cell, oa_parameters, profile
    )
    return {
        "created": True,
        "already_exists": False,
        "applied_device_parameters": oa_parameters,
        "readback": readback,
    }


def inspect_common_source(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    data = _read_schematic(client, library, cell)
    _assert_common_source(data, payload["profile"])
    return _attach_targeted_parameter_verification(
        client, library, cell, payload, _common_source_summary(data)
    )


def _symbol_source_contract(
    summary: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    expected_sha256 = str(settings["expected_schematic_topology_sha256"])
    actual_sha256 = topology_fingerprint(snapshot_from_inspection(summary))
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            "symbol source topology mismatch: expected "
            f"{expected_sha256}, got {actual_sha256}"
        )
    raw_pins = summary.get("bridge_schematic", {}).get("pins")
    if not isinstance(raw_pins, dict):
        raise RuntimeError("symbol source schematic pin readback is missing")
    observed_pins = sorted(
        (
            {
                "name": str(name),
                "direction": str(value.get("direction") or "inputOutput"),
                "num_bits": int(value.get("numBits") or 1),
            }
            for name, value in raw_pins.items()
            if isinstance(value, dict)
        ),
        key=lambda item: item["name"],
    )
    expected_pins = sorted(
        (
            {
                "name": str(item["name"]),
                "direction": str(item["direction"]),
                "num_bits": int(item.get("num_bits", 1)),
            }
            for item in settings["expected_pins"]
        ),
        key=lambda item: item["name"],
    )
    if observed_pins != expected_pins:
        raise RuntimeError(
            "symbol source pin contract mismatch: expected "
            f"{expected_pins!r}, got {observed_pins!r}"
        )
    return {
        "expected_topology_sha256": expected_sha256,
        "observed_topology_sha256": actual_sha256,
        "topology_match": True,
        "expected_pins": expected_pins,
        "observed_pins": observed_pins,
        "pins_match": True,
        "evidence_source": "bridge_readback",
    }


def _parse_symbol_readback(raw: str) -> dict[str, Any]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines or lines[0] != "SYMBOL" or lines[-1] != "END":
        raise RuntimeError("symbol readback has invalid section framing")
    terminals: list[dict[str, Any]] = []
    bbox: str | None = None
    for line in lines[1:-1]:
        parts = line.split("|")
        if parts[0] == "TERM" and len(parts) == 4:
            try:
                num_bits = int(parts[3])
            except ValueError as exc:
                raise RuntimeError(
                    f"symbol terminal has invalid numBits: {line!r}"
                ) from exc
            terminals.append(
                {
                    "name": parts[1],
                    "direction": parts[2],
                    "num_bits": num_bits,
                }
            )
        elif parts[0] == "BBOX" and len(parts) == 2 and bbox is None:
            bbox = parts[1]
        else:
            raise RuntimeError(f"invalid symbol readback record: {line!r}")
    names = [item["name"] for item in terminals]
    if not terminals or len(names) != len(set(names)):
        raise RuntimeError("symbol readback terminals are empty or repeated")
    if bbox is None or bbox.lower() == "nil":
        raise RuntimeError("symbol readback has an empty bounding box")
    return {
        "terminals": terminals,
        "terminal_order": names,
        "bbox_skill": bbox,
        "non_empty_bbox": True,
    }


def _read_schematic_symbol(
    client, library: str, cell: str
) -> dict[str, Any]:
    from virtuoso_bridge import decode_skill_output

    escaped_library = _skill_string(library)
    escaped_cell = _skill_string(cell)
    skill = " ".join(
        [
            "let((rbCv rbResult)",
            "rbCv=dbOpenCellViewByType("
            f'"{escaped_library}" "{escaped_cell}" '
            '"symbol" "schematicSymbol" "r")',
            'unless(rbCv error("target symbol view is missing"))',
            'rbResult="SYMBOL\\n"',
            "foreach(rbTerm rbCv~>terminals",
            'rbResult=strcat(rbResult sprintf(nil "TERM|%s|%s|%d\\n" '
            'rbTerm~>name if(rbTerm~>direction rbTerm~>direction "inputOutput") '
            "if(rbTerm~>numBits rbTerm~>numBits 1)))",
            ")",
            'rbResult=strcat(rbResult sprintf(nil "BBOX|%L\\n" rbCv~>bBox))',
            "dbClose(rbCv)",
            'strcat(rbResult "END\\n"))',
        ]
    )
    result = client.execute_skill(skill, timeout=60)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"symbol readback failed: {errors[0]}")
    return _parse_symbol_readback(
        decode_skill_output(str(getattr(result, "output", "")))
    )


def _schematic_env_literal(client, variable: str) -> str:
    from virtuoso_bridge import decode_skill_output

    result = client.execute_skill(
        f'sprintf(nil "%L" schGetEnv("{_skill_string(variable)}"))',
        timeout=30,
    )
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(
            f"schematic environment readback failed for {variable}: {errors[0]}"
        )
    literal = decode_skill_output(str(getattr(result, "output", ""))).strip()
    if not literal:
        raise RuntimeError(
            f"schematic environment readback was empty for {variable}"
        )
    return literal


def _delete_new_symbol_view(client, library: str, cell: str) -> bool:
    result = client.execute_skill(
        "let((rbView) "
        f'rbView=ddGetObj("{_skill_string(library)}" '
        f'"{_skill_string(cell)}" "symbol") '
        "if(rbView ddDeleteObj(rbView) t))",
        timeout=60,
    )
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"new symbol rollback failed: {errors[0]}")
    return not _cellview_exists(client, library, cell, "symbol")


def _fresh_existing_schematic_summary(
    client, library: str, cell: str
) -> dict[str, Any]:
    if not _schematic_exists(client, library, cell):
        raise RuntimeError(
            f"target schematic does not exist: {library}/{cell}/schematic"
        )
    data = _read_schematic(client, library, cell)
    pin_geometry, placement = _schematic_geometry_bundle(client, library, cell)
    return _existing_schematic_summary(
        data,
        pin_geometry=pin_geometry,
        placement=placement,
    )


def _hierarchy_parameter_scope_specs(
    payload: dict[str, Any],
) -> list[HierarchyParameterScope]:
    raw_context = payload.get("design_context")
    if not isinstance(raw_context, dict):
        return []
    raw_scopes = raw_context.get("hierarchy_parameter_scopes", [])
    if not isinstance(raw_scopes, list):
        raise RuntimeError("hierarchy parameter scopes are not structured")
    return [HierarchyParameterScope.model_validate(item) for item in raw_scopes]


def _read_hierarchy_parameter_scope_state(
    client,
    top_library: str,
    top_cell: str,
    top_schematic: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    """Bind exact one-level child graphs and expose scoped CDF readback."""

    specs = _hierarchy_parameter_scope_specs(payload)
    if not specs:
        return {}, {}
    top_instances = {
        str(item.get("name")): item for item in top_schematic.get("instances", [])
    }
    scoped_parameters: dict[str, dict[str, str]] = {}
    scope_readbacks: dict[str, dict[str, Any]] = {}
    for spec in specs:
        top_instance = top_instances.get(spec.top_instance)
        if top_instance is None:
            raise RuntimeError(
                "hierarchy parameter scope references missing top instance "
                f"{spec.top_instance!r}"
            )
        if (
            str(top_instance.get("lib")) != spec.library
            or str(top_instance.get("cell")) != spec.cell
        ):
            raise RuntimeError(
                "hierarchy parameter scope master mismatch for "
                f"{spec.top_instance}: OA={top_instance.get('lib')}/"
                f"{top_instance.get('cell')}, declared={spec.library}/{spec.cell}"
            )
        aliased_instances = sorted(
            name
            for name, item in top_instances.items()
            if str(item.get("lib")) == spec.library
            and str(item.get("cell")) == spec.cell
        )
        if aliased_instances != [spec.top_instance]:
            raise RuntimeError(
                "hierarchy parameter child schematic is shared by top instances "
                f"{aliased_instances}; a scoped child OA write would affect every "
                "alias"
            )
        child = _try_read_schematic(client, spec.library, spec.cell)
        if child is None:
            raise RuntimeError(
                "hierarchy parameter child schematic does not exist: "
                f"{spec.library}/{spec.cell}/schematic"
            )
        pin_geometry, placement = _schematic_geometry_bundle(
            client,
            spec.library,
            spec.cell,
        )
        child_summary = _existing_schematic_summary(
            child,
            pin_geometry=pin_geometry,
            placement=placement,
        )
        topology_sha256 = topology_fingerprint(
            snapshot_from_inspection(child_summary)
        )
        placement_sha256 = child_summary.get("placement", {}).get("sha256")
        if topology_sha256 != spec.expected_child_topology_sha256:
            raise RuntimeError(
                "hierarchy parameter child topology mismatch for "
                f"{spec.top_instance}: expected={spec.expected_child_topology_sha256}, "
                f"actual={topology_sha256}"
            )
        if placement_sha256 != spec.expected_child_placement_sha256:
            raise RuntimeError(
                "hierarchy parameter child placement mismatch for "
                f"{spec.top_instance}: expected={spec.expected_child_placement_sha256}, "
                f"actual={placement_sha256}"
            )
        child_parameters = _instance_parameters_from_schematic(child)
        scoped_names: list[str] = []
        for local_instance, parameters in child_parameters.items():
            scoped_name = f"{spec.top_instance}/{local_instance}"
            if scoped_name in scoped_parameters:
                raise RuntimeError(
                    f"duplicate hierarchy parameter path {scoped_name!r}"
                )
            scoped_parameters[scoped_name] = parameters
            scoped_names.append(scoped_name)
        scope_readbacks[spec.top_instance] = {
            "top_instance": spec.top_instance,
            "top_target": {
                "library": top_library,
                "cell": top_cell,
                "view": "schematic",
            },
            "child_target": {
                "library": spec.library,
                "cell": spec.cell,
                "view": spec.view,
            },
            "child_topology_sha256": topology_sha256,
            "child_placement_sha256": placement_sha256,
            "scoped_instances": sorted(scoped_names),
            "state_source": "bridge_readback",
            "binding_source": "software_inference",
        }
    return scoped_parameters, scope_readbacks


def _attach_hierarchy_parameter_scope_state(
    client,
    library: str,
    cell: str,
    schematic: dict[str, Any],
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    scoped_parameters, scope_readbacks = _read_hierarchy_parameter_scope_state(
        client,
        library,
        cell,
        schematic,
        payload,
    )
    if not scope_readbacks:
        return summary
    instance_parameters = dict(summary.get("instance_parameters") or {})
    collisions = sorted(set(instance_parameters) & set(scoped_parameters))
    if collisions:
        raise RuntimeError(
            "hierarchy parameter paths collide with top-level instance names: "
            + ", ".join(collisions)
        )
    instance_parameters.update(scoped_parameters)
    summary["instance_parameters"] = instance_parameters
    summary["hierarchy_parameter_scopes"] = scope_readbacks
    return summary


def _hierarchy_parameter_scope_by_top_instance(
    payload: dict[str, Any],
) -> dict[str, HierarchyParameterScope]:
    return {
        spec.top_instance: spec
        for spec in _hierarchy_parameter_scope_specs(payload)
    }


def _parameter_target_location(
    payload: dict[str, Any],
    top_library: str,
    top_cell: str,
    instance_path: str,
) -> tuple[str, str, str]:
    top_instance, local_instance = split_instance_path(instance_path)
    if top_instance is None:
        return top_library, top_cell, local_instance
    scope = _hierarchy_parameter_scope_by_top_instance(payload).get(top_instance)
    if scope is None:
        raise RuntimeError(
            f"no hierarchy parameter scope declared for {instance_path!r}"
        )
    return scope.library, scope.cell, local_instance


def _verify_scoped_instance_parameter_values(
    client,
    library: str,
    cell: str,
    payload: dict[str, Any],
    expected: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    if not _hierarchy_parameter_scope_specs(payload) and all(
        split_instance_path(instance_path)[0] is None
        for instance_path in expected
    ):
        return _verify_instance_parameter_values(
            client,
            library,
            cell,
            expected,
        )
    top = _read_schematic(client, library, cell)
    _read_hierarchy_parameter_scope_state(client, library, cell, top, payload)
    grouped: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
    scoped_names: dict[tuple[str, str, str], str] = {}
    for instance_path, parameters in expected.items():
        target_library, target_cell, local_instance = _parameter_target_location(
            payload,
            library,
            cell,
            instance_path,
        )
        grouped.setdefault((target_library, target_cell), {})[
            local_instance
        ] = parameters
        scoped_names[(target_library, target_cell, local_instance)] = instance_path
    confirmed: dict[str, dict[str, str]] = {}
    for (target_library, target_cell), local_expected in grouped.items():
        local_confirmed = _verify_instance_parameter_values(
            client,
            target_library,
            target_cell,
            local_expected,
        )
        for local_instance, parameters in local_confirmed.items():
            instance_path = scoped_names[
                (target_library, target_cell, local_instance)
            ]
            confirmed[instance_path] = parameters
    return confirmed


def generate_existing_schematic_symbol(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("symbol_generation")
    if not isinstance(settings, dict):
        raise RuntimeError("symbol generation contract is missing")
    client = _client()
    library, cell = _target(payload)
    source = _fresh_existing_schematic_summary(client, library, cell)
    source_contract = _symbol_source_contract(source, settings)
    if _cellview_exists(client, library, cell, "symbol"):
        raise RuntimeError(
            f"refusing to modify existing symbol view {library}/{cell}/symbol"
        )

    setting_before = _schematic_env_literal(client, "ssgSortPins")
    escaped_library = _skill_string(library)
    escaped_cell = _skill_string(cell)
    pin_sort = str(settings["pin_sort"])
    skill = " ".join(
        [
            "let((rbOldSort rbPinList rbGenerated)",
            'rbOldSort=schGetEnv("ssgSortPins")',
            "unwindProtect(",
            "progn(",
            f'schSetEnv("ssgSortPins" "{_skill_string(pin_sort)}")',
            "rbPinList=schSchemToPinList("
            f'"{escaped_library}" "{escaped_cell}" "schematic")',
            'unless(rbPinList error("source schematic produced no symbol pin list"))',
            "rbGenerated=schPinListToSymbol("
            f'"{escaped_library}" "{escaped_cell}" "symbol" rbPinList)',
            'unless(rbGenerated error("Cadence symbol generation returned nil"))',
            "t)",
            'schSetEnv("ssgSortPins" rbOldSort)',
            "))",
        ]
    )
    result = client.execute_skill(skill, timeout=120)
    errors = getattr(result, "errors", None) or []
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    setting_after = _schematic_env_literal(client, "ssgSortPins")
    failure: str | None = None
    if errors:
        failure = str(errors[0])
    elif output != "t":
        failure = f"unexpected Cadence symbol generation result: {output!r}"
    elif setting_after != setting_before:
        failure = (
            "schematic symbol pin-sort setting was not restored: "
            f"before={setting_before!r}, after={setting_after!r}"
        )
    elif not _cellview_exists(client, library, cell, "symbol"):
        failure = "Cadence symbol generation did not create the sibling view"
    if failure is not None:
        rolled_back = True
        if _cellview_exists(client, library, cell, "symbol"):
            rolled_back = _delete_new_symbol_view(client, library, cell)
        raise RuntimeError(
            f"symbol generation failed: {failure}; new-view rollback={rolled_back}"
        )
    return {
        "created": True,
        "already_exists": False,
        "source_contract": source_contract,
        "target": {"library": library, "cell": cell, "view": "symbol"},
        "cadence_api": ["schSchemToPinList", "schPinListToSymbol"],
        "pin_sort": pin_sort,
        "session_setting": {
            "name": "ssgSortPins",
            "before_skill_literal": setting_before,
            "after_skill_literal": setting_after,
            "restored": True,
        },
        "replace_existing": False,
    }


def inspect_existing_schematic_symbol(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("symbol_generation")
    if not isinstance(settings, dict):
        raise RuntimeError("symbol inspection contract is missing")
    client = _client()
    library, cell = _target(payload)
    source = _fresh_existing_schematic_summary(client, library, cell)
    source_contract = _symbol_source_contract(source, settings)
    if not _cellview_exists(client, library, cell, "symbol"):
        raise RuntimeError(f"symbol view does not exist: {library}/{cell}/symbol")
    readback = _read_schematic_symbol(client, library, cell)
    expected = sorted(
        (
            {
                "name": str(item["name"]),
                "direction": str(item["direction"]),
                "num_bits": int(item.get("num_bits", 1)),
            }
            for item in settings["expected_pins"]
        ),
        key=lambda item: item["name"],
    )
    observed = sorted(readback["terminals"], key=lambda item: item["name"])
    if observed != expected:
        raise RuntimeError(
            f"generated symbol terminal mismatch: expected {expected!r}, got "
            f"{observed!r}"
        )
    return {
        "target": {"library": library, "cell": cell, "view": "symbol"},
        "source_contract": source_contract,
        **readback,
        "expected_terminals": expected,
        "terminals_match": True,
        "evidence_source": "bridge_readback",
    }


def inspect_existing_schematic(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    if not _schematic_exists(client, library, cell):
        raise RuntimeError(
            f"target schematic does not exist: {library}/{cell}/schematic"
        )
    data = _read_schematic(client, library, cell)
    partial_prefix_state: dict[str, Any] | None = None
    try:
        pin_geometry, placement = _schematic_geometry_bundle(
            client, library, cell
        )
    except RuntimeError:
        raw_execution = payload.get("topology_delta")
        if not isinstance(raw_execution, dict):
            raise
        execution = TopologyDeltaExecutionSpec.model_validate(raw_execution)
        if not execution.resume_partial_prefix:
            raise
        placement = _schematic_placement_snapshot(client, library, cell)
        pin_geometry = _read_schematic_pin_geometry(client, library, cell)
        orphan_instances = _unmatched_placement_pin_instances(
            pin_geometry, placement
        )
        projected_data = _strip_verified_orphan_pin_instances(
            data, orphan_instances
        )
        partial_summary = _existing_schematic_summary(
            projected_data,
            pin_geometry=pin_geometry,
            placement=placement,
        )
        current_snapshot = snapshot_from_inspection(partial_summary)
        operations = (
            execution.contract.operations
            if execution.direction == "forward"
            else execution.contract.inverse_operations
        )
        expected_input_sha256 = (
            execution.contract.expected_before_sha256
            if execution.direction == "forward"
            else execution.contract.expected_after_sha256
        )
        expected_output_sha256 = (
            execution.contract.expected_after_sha256
            if execution.direction == "forward"
            else execution.contract.expected_before_sha256
        )
        prefix_length, _removed_pins = (
            _select_partial_generic_topology_prefix(
                current_snapshot,
                operations,
                expected_input_sha256,
                expected_output_sha256,
                sorted(
                    (
                        _placement_pin_signature(item)
                        for item in orphan_instances
                    ),
                    key=repr,
                ),
            )
        )
        partial_prefix_state = {
            "status": "verified_read_only",
            "already_applied_operation_count": prefix_length,
            "remaining_operation_count": len(operations) - prefix_length,
            "orphan_pin_figure_count": len(orphan_instances),
            "observed_topology_sha256": topology_fingerprint(
                current_snapshot
            ),
            "input_reconstruction_match": True,
            "output_projection_match": True,
            "state_readback_source": "bridge_readback",
            "decision_source": "software_inference",
        }
    summary_data = data if partial_prefix_state is None else projected_data
    summary = _existing_schematic_summary(
        summary_data,
        pin_geometry=pin_geometry,
        placement=placement,
    )
    raw_execution = payload.get("topology_delta")
    if partial_prefix_state is None and isinstance(raw_execution, dict):
        execution = TopologyDeltaExecutionSpec.model_validate(raw_execution)
        if execution.resume_partial_prefix:
            current_snapshot = snapshot_from_inspection(summary)
            expected_input_sha256 = (
                execution.contract.expected_before_sha256
                if execution.direction == "forward"
                else execution.contract.expected_after_sha256
            )
            if topology_fingerprint(current_snapshot) != expected_input_sha256:
                operations = (
                    execution.contract.operations
                    if execution.direction == "forward"
                    else execution.contract.inverse_operations
                )
                expected_output_sha256 = (
                    execution.contract.expected_after_sha256
                    if execution.direction == "forward"
                    else execution.contract.expected_before_sha256
                )
                prefix_length, _cleanup_pins = (
                    _select_partial_generic_topology_prefix(
                        current_snapshot,
                        operations,
                        expected_input_sha256,
                        expected_output_sha256,
                        [],
                    )
                )
                partial_prefix_state = {
                    "status": "verified_read_only",
                    "already_applied_operation_count": prefix_length,
                    "remaining_operation_count": len(operations) - prefix_length,
                    "orphan_pin_figure_count": 0,
                    "observed_topology_sha256": topology_fingerprint(
                        current_snapshot
                    ),
                    "input_reconstruction_match": True,
                    "output_projection_match": True,
                    "state_readback_source": "bridge_readback",
                    "decision_source": "software_inference",
                }
    if partial_prefix_state is not None:
        summary["partial_prefix_state"] = partial_prefix_state
    summary = _attach_hierarchy_parameter_scope_state(
        client,
        library,
        cell,
        summary_data,
        payload,
        summary,
    )
    raw_symbol_generation = payload.get("symbol_generation")
    if isinstance(raw_symbol_generation, dict):
        summary["symbol_source_contract"] = _symbol_source_contract(
            summary,
            raw_symbol_generation,
        )
    return _attach_targeted_parameter_verification(
        client,
        library,
        cell,
        payload,
        summary,
    )


def _select_partial_generic_topology_prefix(
    current_snapshot: TopologySnapshot,
    operations: list[Any],
    expected_input_sha256: str,
    expected_output_sha256: str,
    actual_orphan_signatures: list[tuple[Any, ...]],
) -> tuple[int, list[Any]]:
    matches: list[tuple[int, list[Any]]] = []
    for prefix_length in range(1, len(operations) + 1):
        prefix = operations[:prefix_length]
        removed_pins = [
            operation.expected
            for operation in prefix
            if isinstance(operation, RemovePinOperation)
        ]
        if not removed_pins:
            continue
        expected_orphan_signatures = sorted(
            (_topology_pin_signature(pin) for pin in removed_pins), key=repr
        )
        if actual_orphan_signatures == expected_orphan_signatures:
            cleanup_pins = removed_pins
        elif not actual_orphan_signatures:
            cleanup_pins = []
        else:
            continue
        try:
            reconstructed_input = apply_topology_operations(
                current_snapshot,
                invert_topology_operations(prefix),
            )
            expected_output = apply_topology_operations(
                current_snapshot,
                operations[prefix_length:],
            )
        except Exception:
            continue
        if (
            topology_fingerprint(reconstructed_input) == expected_input_sha256
            and topology_fingerprint(expected_output) == expected_output_sha256
        ):
            matches.append((prefix_length, cleanup_pins))
    if len(matches) != 1:
        raise RuntimeError(
            "partial-prefix topology state did not select exactly one declared "
            f"resume boundary; matches={len(matches)}"
        )
    return matches[0]


def _resume_partial_generic_topology_prefix(
    client,
    library: str,
    cell: str,
    payload: dict[str, Any],
    execution: TopologyDeltaExecutionSpec,
    current: dict[str, Any],
    pin_geometry: dict[str, dict[str, Any]],
    placement: dict[str, Any],
) -> dict[str, Any]:
    """Resume only one exact operation prefix with contract-bound orphan pins."""

    if execution.contract.master_parameter_migrations:
        raise RuntimeError(
            "partial-prefix topology resume does not support master migrations"
        )
    if execution.expected_output_placement_sha256 is None:
        raise RuntimeError(
            "partial-prefix topology resume requires an expected output placement"
        )
    operations = (
        execution.contract.operations
        if execution.direction == "forward"
        else execution.contract.inverse_operations
    )
    expected_input_sha256 = (
        execution.contract.expected_before_sha256
        if execution.direction == "forward"
        else execution.contract.expected_after_sha256
    )
    expected_output_sha256 = (
        execution.contract.expected_after_sha256
        if execution.direction == "forward"
        else execution.contract.expected_before_sha256
    )
    orphan_instances = _unmatched_placement_pin_instances(
        pin_geometry, placement
    )
    projected_current = _strip_verified_orphan_pin_instances(
        current, orphan_instances
    )
    current_summary = _existing_schematic_summary(
        projected_current,
        pin_geometry=pin_geometry,
        placement=placement,
    )
    current_snapshot = snapshot_from_inspection(current_summary)
    actual_orphan_signatures = sorted(
        (_placement_pin_signature(item) for item in orphan_instances),
        key=repr,
    )
    prefix_length, removed_pins = _select_partial_generic_topology_prefix(
        current_snapshot,
        operations,
        expected_input_sha256,
        expected_output_sha256,
        actual_orphan_signatures,
    )
    cleanup_commands = [
        _generic_delete_orphan_pin_figure_operation(pin)
        for pin in removed_pins
    ]
    remaining_commands, declarative_operations = (
        _compile_generic_topology_commands(operations[prefix_length:])
    )
    before_parameters = _instance_parameters_from_schematic(projected_current)
    commands = cleanup_commands + remaining_commands
    _run_generic_topology_editor_commands(client, library, cell, commands)

    after = _read_schematic(client, library, cell)
    after_pin_geometry, after_placement = _schematic_geometry_bundle(
        client, library, cell
    )
    after_summary = _existing_schematic_summary(
        after,
        pin_geometry=after_pin_geometry,
        placement=after_placement,
    )
    actual_output_sha256 = topology_fingerprint(
        snapshot_from_inspection(after_summary)
    )
    if actual_output_sha256 != expected_output_sha256:
        raise RuntimeError(
            "partial-prefix topology resume output mismatch: expected "
            f"{expected_output_sha256}, got {actual_output_sha256}"
        )
    output_placement_match_mode = _placement_fingerprint_match_mode(
        execution.expected_output_placement_sha256,
        after_placement,
    )
    if output_placement_match_mode is None:
        raise RuntimeError(
            "partial-prefix topology resume placement mismatch: expected "
            f"{execution.expected_output_placement_sha256}, got "
            f"exact={after_placement['sha256']}, logical-pin-bound="
            f"{after_placement.get('logical_pin_bound_sha256')}"
        )
    after_parameters = _instance_parameters_from_schematic(after)
    if after_parameters != before_parameters:
        changed = sorted(
            name
            for name in set(after_parameters) | set(before_parameters)
            if after_parameters.get(name) != before_parameters.get(name)
        )
        raise RuntimeError(
            "partial-prefix topology resume changed preserved instance "
            "parameters: " + ", ".join(changed)
        )
    allowed_master_libraries = _generic_topology_allowed_master_libraries(
        current_snapshot,
        operations[prefix_length:],
        payload["profile"],
        library,
    )
    return {
        "contract_id": execution.contract.id,
        "direction": execution.direction,
        "operation_count": len(operations),
        "operation_kinds": [operation.operation for operation in operations],
        "compiled_editor_command_count": len(commands),
        "declarative_net_operations": declarative_operations,
        "append_mode": True,
        "replace_existing": False,
        "allowed_master_libraries": allowed_master_libraries,
        "input_topology_sha256": expected_input_sha256,
        "observed_partial_topology_sha256": topology_fingerprint(
            current_snapshot
        ),
        "expected_output_topology_sha256": expected_output_sha256,
        "actual_output_topology_sha256": actual_output_sha256,
        "placement_before": placement,
        "placement_after": after_placement,
        "expected_output_placement_sha256": (
            execution.expected_output_placement_sha256
        ),
        "output_placement_match": True,
        "output_placement_match_mode": output_placement_match_mode,
        "preserved_instance_parameters": True,
        "preserved_instances": sorted(after_parameters),
        "master_parameter_migrations": [],
        "migrated_instance_parameter_tables": {},
        "automatic_inverse_recovery": {
            "status": "not_needed",
            "source": "system_event",
        },
        "partial_prefix_resume": {
            "status": "resumed",
            "already_applied_operation_count": prefix_length,
            "remaining_operation_count": len(operations) - prefix_length,
            "orphan_pin_figure_count": len(orphan_instances),
            "input_reconstruction_match": True,
            "output_projection_match": True,
            "state_readback_source": "bridge_readback",
            "decision_source": "software_inference",
        },
        "contract_audit": {
            "source": "software_inference",
            "partial_prefix_resume": True,
            "input_reconstruction_match": True,
            "output_readback_match": True,
            "placement_readback_match": True,
        },
        "readback": after_summary,
    }


def transform_existing_schematic_topology_delta(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute one exact direction of a predeclared, bounded topology delta."""

    raw_execution = payload.get("topology_delta")
    if not isinstance(raw_execution, dict):
        raise RuntimeError(
            "generic existing-schematic transform requires topology_delta"
        )
    execution = TopologyDeltaExecutionSpec.model_validate(raw_execution)
    operations = (
        execution.contract.operations
        if execution.direction == "forward"
        else execution.contract.inverse_operations
    )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    try:
        before_pin_geometry, before_placement = _schematic_geometry_bundle(
            client, library, cell
        )
    except RuntimeError:
        if not execution.resume_partial_prefix:
            raise
        before_placement = _schematic_placement_snapshot(client, library, cell)
        before_pin_geometry = _read_schematic_pin_geometry(
            client, library, cell
        )
        return _resume_partial_generic_topology_prefix(
            client,
            library,
            cell,
            payload,
            execution,
            before,
            before_pin_geometry,
            before_placement,
        )
    before_summary = _existing_schematic_summary(
        before,
        pin_geometry=before_pin_geometry,
        placement=before_placement,
    )
    before_snapshot = snapshot_from_inspection(before_summary)
    expected_input_sha256 = (
        execution.contract.expected_before_sha256
        if execution.direction == "forward"
        else execution.contract.expected_after_sha256
    )
    if (
        execution.resume_partial_prefix
        and topology_fingerprint(before_snapshot) != expected_input_sha256
    ):
        return _resume_partial_generic_topology_prefix(
            client,
            library,
            cell,
            payload,
            execution,
            before,
            before_pin_geometry,
            before_placement,
        )
    expected_after = apply_topology_delta_execution(before_snapshot, execution)
    # Prove the declared opposite direction locally before opening append mode.
    validate_topology_execution_readback(
        before_snapshot,
        expected_after,
        execution,
    )
    allowed_master_libraries = _generic_topology_allowed_master_libraries(
        before_snapshot,
        operations,
        payload["profile"],
        library,
    )
    migrations = _directed_master_parameter_migrations(execution)
    if migrations:
        _verify_instance_parameter_values(
            client,
            library,
            cell,
            {
                migration.instance: dict(migration.expected_parameters)
                for migration in migrations
            },
        )
    _preflight_generic_master_replacements(
        client,
        library,
        cell,
        before_snapshot,
        operations,
        migrations,
    )
    commands, declarative_operations = _compile_generic_topology_commands(operations)
    before_parameters = _instance_parameters_from_schematic(before)

    _run_generic_topology_editor_commands(client, library, cell, commands)

    try:
        migration_application = _apply_exact_master_parameter_migrations(
            client,
            library,
            cell,
            migrations,
        )
        after = _read_schematic(client, library, cell)
        after_pin_geometry, after_placement = _schematic_geometry_bundle(
            client, library, cell
        )
        after_summary = _existing_schematic_summary(
            after,
            pin_geometry=after_pin_geometry,
            placement=after_placement,
        )
        audit = validate_topology_execution_readback(
            before_summary,
            after_summary,
            execution,
        )
        after_parameters = _instance_parameters_from_schematic(after)
        migrated_instances = {migration.instance for migration in migrations}
        preserved_instances = sorted(
            (set(before_parameters) & set(after_parameters)) - migrated_instances
        )
        changed_parameters = [
            name
            for name in preserved_instances
            if before_parameters[name] != after_parameters[name]
        ]
        if changed_parameters:
            raise RuntimeError(
                "generic topology transform changed parameters on preserved "
                "instances: " + ", ".join(changed_parameters)
            )
        expected_output_placement = execution.expected_output_placement_sha256
        output_placement_match_mode = (
            None
            if expected_output_placement is None
            else _placement_fingerprint_match_mode(
                expected_output_placement,
                after_placement,
            )
        )
        if expected_output_placement is not None and output_placement_match_mode is None:
            raise RuntimeError(
                "generic topology output placement fingerprint mismatch: expected "
                f"{expected_output_placement}, got exact="
                f"{after_placement['sha256']}, logical-pin-bound="
                f"{after_placement.get('logical_pin_bound_sha256')}"
            )
    except Exception as audit_error:
        if not commands:
            raise
        recovery = _attempt_generic_topology_inverse_recovery(
            client,
            library,
            cell,
            execution,
            expected_after,
            before_summary,
            before_parameters,
            before_placement,
        )
        raise RuntimeError(
            "generic topology post-save audit failed; original_error="
            f"{type(audit_error).__name__}: {audit_error}; "
            "automatic_inverse_recovery="
            + json.dumps(recovery, ensure_ascii=False, sort_keys=True)
        ) from audit_error
    actual_after_sha256 = topology_fingerprint(
        snapshot_from_inspection(after_summary)
    )
    return {
        "contract_id": execution.contract.id,
        "direction": execution.direction,
        "operation_count": len(operations),
        "operation_kinds": [operation.operation for operation in operations],
        "compiled_editor_command_count": len(commands),
        "declarative_net_operations": declarative_operations,
        "append_mode": True,
        "replace_existing": False,
        "allowed_master_libraries": allowed_master_libraries,
        "input_topology_sha256": topology_fingerprint(before_snapshot),
        "expected_output_topology_sha256": topology_fingerprint(expected_after),
        "actual_output_topology_sha256": actual_after_sha256,
        "placement_before": before_placement,
        "placement_after": after_placement,
        "expected_output_placement_sha256": (
            execution.expected_output_placement_sha256
        ),
        "output_placement_match": (
            True
            if execution.expected_output_placement_sha256 is not None
            else None
        ),
        "output_placement_match_mode": output_placement_match_mode,
        "preserved_instance_parameters": True,
        "preserved_instances": preserved_instances,
        "master_parameter_migrations": migration_application,
        "migrated_instance_parameter_tables": {
            migration.instance: {
                "before": before_parameters.get(migration.instance, {}),
                "after": after_parameters.get(migration.instance, {}),
                "readback_source": "bridge_readback",
            }
            for migration in migrations
        },
        "automatic_inverse_recovery": {
            "status": "not_needed",
            "source": "system_event",
        },
        "contract_audit": {
            "source": "software_inference",
            **audit.model_dump(mode="json"),
        },
        "readback": after_summary,
    }


def apply_existing_schematic_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    return _apply_explicit_instance_parameters(client, library, cell, payload)


def apply_common_source_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    current = _read_schematic(client, library, cell)
    _assert_common_source(current, payload["profile"])
    result: dict[str, Any] = {}
    if payload.get("parameters"):
        oa_parameters = {
            name: float(payload["parameters"][name])
            for name in (
                "device_width_um",
                "length_um",
                "load_resistance_ohm",
                "source_resistance_ohm",
                "cascode_width_um",
                "cascode_length_um",
            )
            if name in payload["parameters"]
        }
        if oa_parameters:
            result = {
                "requested_parameters": payload["parameters"],
                "applied_device_parameters": oa_parameters,
                "readback": _apply_common_source_parameters(
                    client, library, cell, oa_parameters, payload["profile"]
                ),
            }
        else:
            result = {
                **_common_source_summary(current),
                "requested_parameters": payload["parameters"],
                "applied_device_parameters": {},
            }
    if payload.get("instance_parameter_updates"):
        explicit = _apply_explicit_instance_parameters(
            client, library, cell, payload
        )
        requested_semantic = {
            name: float(payload["parameters"][name])
            for name in (
                "device_width_um",
                "length_um",
                "load_resistance_ohm",
                "source_resistance_ohm",
                "cascode_width_um",
                "cascode_length_um",
            )
            if name in payload.get("parameters", {})
        }
        _assert_parameter_consistency(
            requested_semantic,
            explicit["readback"]["semantic_parameters"],
            expected_label="requested semantic write",
            actual_label="final OA readback after explicit CDF callbacks",
        )
        result.update(explicit)
        result["semantic_readback"] = explicit["readback"]["semantic_parameters"]
    return result


def create_differential_pair(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
        schematic_label_instance_term as label_term,
    )

    client = _client()
    library, cell = _target(payload)
    existing = _try_read_schematic(client, library, cell)
    if existing is not None and not payload.get("replace_existing", False):
        _assert_differential_pair(existing, payload["profile"])
        return {
            "created": False,
            "already_exists": True,
            "readback": _differential_pair_summary(existing),
        }
    if existing is not None:
        result = client.execute_skill(
            f'let((v) v=ddGetObj("{library}" "{cell}" "schematic") '
            "when(v ddDeleteObj(v)))",
            timeout=30,
        )
        errors = getattr(result, "errors", None) or []
        if errors:
            raise RuntimeError(f"delete existing schematic failed: {errors[0]}")

    profile = payload["profile"]
    with client.schematic.edit(library, cell, timeout=90) as schematic:
        schematic.add(
            inst(
                profile["tech_library"],
                profile["nmos_cell"],
                "symbol",
                "MN0",
                -0.8,
                0.0,
                "R0",
            )
        )
        schematic.add(
            inst(
                profile["tech_library"],
                profile["nmos_cell"],
                "symbol",
                "MN1",
                0.8,
                0.0,
                "R0",
            )
        )
        schematic.add(inst("analogLib", "res", "symbol", "RD0", -0.8, 1.3, "R0"))
        schematic.add(inst("analogLib", "res", "symbol", "RD1", 0.8, 1.3, "R0"))
        schematic.add_net_label_to_transistor(
            "MN0",
            drain_net="OUTP",
            gate_net="INP",
            source_net="TAIL",
            body_net="VSS",
        )
        schematic.add_net_label_to_transistor(
            "MN1",
            drain_net="OUTN",
            gate_net="INN",
            source_net="TAIL",
            body_net="VSS",
        )
        schematic.add(label_term("RD0", "PLUS", "VDD"))
        schematic.add(label_term("RD0", "MINUS", "OUTP"))
        schematic.add(label_term("RD1", "PLUS", "VDD"))
        schematic.add(label_term("RD1", "MINUS", "OUTN"))
        schematic.add(pin("INP", -2.0, 0.0, "R0", direction="input"))
        schematic.add(pin("INN", 2.0, 0.0, "R0", direction="input"))
        schematic.add(pin("OUTP", -1.8, 0.8, "R0", direction="output"))
        schematic.add(pin("OUTN", 1.8, 0.8, "R0", direction="output"))
        schematic.add(pin("TAIL", 0.0, -0.8, "R0", direction="inputOutput"))
        schematic.add(pin("VDD", 0.0, 1.9, "R0", direction="inputOutput"))
        schematic.add(pin("VSS", 1.4, -0.8, "R0", direction="inputOutput"))

    parameters = _resolved_differential_pair_parameters(payload)
    oa_parameters = {
        name: parameters[name]
        for name in ("input_width_um", "length_um", "load_resistance_ohm")
    }
    readback = _apply_differential_pair_parameters(
        client, library, cell, oa_parameters, profile
    )
    return {
        "created": True,
        "already_exists": False,
        "applied_device_parameters": oa_parameters,
        "testbench_contract": {
            "tail_source": "external ideal current source",
            "input_bias": "external matched common-mode voltage sources",
            "source": "software_inference",
        },
        "readback": readback,
    }


def _assert_differential_pair_tail_transform_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    tail_width_um: float,
    tail_length_um: float,
) -> None:
    before_variant = _assert_differential_pair(before)
    after_variant = _assert_differential_pair(after)
    expected_variant = "resistive_load_nmos_differential_pair_with_tail_device"
    if after_variant != expected_variant:
        raise RuntimeError("tail-device transform did not produce MNTAIL/BIAS")
    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MN1", "RD0", "RD1"):
        if before_by_name[name] != after_by_name[name]:
            raise RuntimeError(f"tail-device transform changed preserved {name}")
    before_pins = set((before.get("pins") or {}).keys())
    after_pins = set((after.get("pins") or {}).keys())
    before_nets = set((before.get("nets") or {}).keys())
    after_nets = set((after.get("nets") or {}).keys())
    expected_pins = before_pins | (
        {"BIAS"}
        if before_variant == "resistive_load_nmos_differential_pair"
        else set()
    )
    expected_nets = before_nets | (
        {"BIAS"}
        if before_variant == "resistive_load_nmos_differential_pair"
        else set()
    )
    if after_pins != expected_pins:
        raise RuntimeError("tail-device transform changed pins beyond adding BIAS")
    if after_nets != expected_nets:
        raise RuntimeError("tail-device transform changed nets beyond adding BIAS")
    if before_variant == expected_variant:
        before_tail = {
            key: value
            for key, value in before_by_name["MNTAIL"].items()
            if key != "params"
        }
        after_tail = {
            key: value
            for key, value in after_by_name["MNTAIL"].items()
            if key != "params"
        }
        if before_tail != after_tail:
            raise RuntimeError(
                "repeated tail-device transform changed MNTAIL topology or placement"
            )
    _assert_parameter_consistency(
        {
            "tail_width_um": tail_width_um,
            "tail_length_um": tail_length_um,
        },
        _differential_pair_semantic_parameters_from_schematic(after),
        expected_label="requested tail geometry",
        actual_label="OA readback",
    )


def transform_differential_pair_tail_device(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_create_pin as pin,
    )

    transform_spec = payload.get("schematic_transform") or {}
    if transform_spec.get("action") != "add_tail_device":
        raise RuntimeError("differential-pair transform requires add_tail_device")
    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    variant = _assert_differential_pair(before, payload["profile"])
    base_variant = "resistive_load_nmos_differential_pair"
    if variant not in {base_variant, _DIFFERENTIAL_PAIR_TAIL_VARIANT}:
        raise RuntimeError(
            "add_tail_device supports only the nominal or already-real-tail "
            "resistive-load differential pair; retarget an existing tail with "
            "parameters.apply"
        )
    topology_changed = variant == base_variant
    tail_width_um = float(payload["parameters"]["tail_width_um"])
    tail_length_um = float(payload["parameters"]["tail_length_um"])
    if topology_changed:
        profile = payload["profile"]
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(
                    inst(
                        profile["tech_library"],
                        profile["nmos_cell"],
                        "symbol",
                        "MNTAIL",
                        0.0,
                        -1.6,
                        "R0",
                    )
                )
                schematic.add_net_label_to_transistor(
                    "MNTAIL",
                    drain_net="TAIL",
                    gate_net="BIAS",
                    source_net="VSS",
                    body_net="VSS",
                )
                schematic.add(pin("BIAS", 2.0, -1.6, "R0", direction="input"))
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "tail-device edit failed and unsaved-edit cleanup also failed: "
                    f"{cleanup_error}"
                ) from edit_error
            raise
    summary = _apply_differential_pair_parameters(
        client,
        library,
        cell,
        {
            "tail_width_um": tail_width_um,
            "tail_length_um": tail_length_um,
        },
        payload["profile"],
    )
    after = summary["bridge_schematic"]
    _assert_differential_pair_tail_transform_preserved(
        before,
        after,
        tail_width_um,
        tail_length_um,
    )
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
        "transform_action": "add_tail_device",
        "topology_delta": {
            "added_instance": "MNTAIL" if topology_changed else None,
            "added_pin": "BIAS" if topology_changed else None,
            "added_net": "BIAS" if topology_changed else None,
            "preserved_instances": ["MN0", "MN1", "RD0", "RD1"],
        },
        "readback": summary,
    }


def _assert_differential_pair_source_degeneration_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    source_resistance_ohm: float,
) -> None:
    before_variant = _assert_differential_pair(before)
    after_variant = _assert_differential_pair(after)
    if before_variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "differential source degeneration requires a real-tail differential pair"
        )
    if after_variant != _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT:
        raise RuntimeError(
            "differential source-degeneration transform did not produce RS0/RS1"
        )
    if before.get("pins") != after.get("pins"):
        raise RuntimeError(
            "differential source-degeneration transform changed top-level pins"
        )
    before_nets = set((before.get("nets") or {}).keys())
    after_nets = set((after.get("nets") or {}).keys())
    if after_nets != before_nets | {"NSP", "NSN"}:
        raise RuntimeError(
            "differential source-degeneration transform changed nets beyond NSP/NSN"
        )

    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MN1"):
        before_without_terms = {
            key: value for key, value in before_by_name[name].items() if key != "terms"
        }
        after_without_terms = {
            key: value for key, value in after_by_name[name].items() if key != "terms"
        }
        if before_without_terms != after_without_terms:
            raise RuntimeError(
                f"differential source-degeneration transform changed {name} "
                "beyond its source net"
            )
    for name in ("RD0", "RD1", "MNTAIL"):
        if before_by_name[name] != after_by_name[name]:
            raise RuntimeError(
                f"differential source-degeneration transform changed preserved {name}"
            )
    if before_variant == _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT:
        for name in ("RS0", "RS1"):
            before_without_params = {
                key: value
                for key, value in before_by_name[name].items()
                if key != "params"
            }
            after_without_params = {
                key: value
                for key, value in after_by_name[name].items()
                if key != "params"
            }
            if before_without_params != after_without_params:
                raise RuntimeError(
                    f"repeated differential source-degeneration transform changed {name}"
                )
    _assert_parameter_consistency(
        {"source_resistance_ohm": float(source_resistance_ohm)},
        _differential_pair_semantic_parameters_from_schematic(after),
        expected_label="requested differential source degeneration",
        actual_label="OA readback",
    )


def _assert_differential_pair_source_degeneration_removal_preserved(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    before_variant = _assert_differential_pair(before)
    after_variant = _assert_differential_pair(after)
    if before_variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "differential source-degeneration removal requires a real-tail topology"
        )
    if after_variant != _DIFFERENTIAL_PAIR_TAIL_VARIANT:
        raise RuntimeError(
            "differential source-degeneration removal did not restore real-tail topology"
        )
    if before.get("pins") != after.get("pins"):
        raise RuntimeError(
            "differential source-degeneration removal changed top-level pins"
        )
    before_nets = set((before.get("nets") or {}).keys())
    after_nets = set((after.get("nets") or {}).keys())
    expected_nets = before_nets - (
        {"NSP", "NSN"}
        if before_variant == _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT
        else set()
    )
    if after_nets != expected_nets:
        raise RuntimeError(
            "differential source-degeneration removal changed nets beyond NSP/NSN"
        )
    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MN1"):
        before_without_terms = {
            key: value for key, value in before_by_name[name].items() if key != "terms"
        }
        after_without_terms = {
            key: value for key, value in after_by_name[name].items() if key != "terms"
        }
        if before_without_terms != after_without_terms:
            raise RuntimeError(
                f"differential source-degeneration removal changed {name} "
                "beyond its source net"
            )
    for name in ("RD0", "RD1", "MNTAIL"):
        if before_by_name[name] != after_by_name[name]:
            raise RuntimeError(
                f"differential source-degeneration removal changed preserved {name}"
            )
    semantic = _differential_pair_semantic_parameters_from_schematic(after)
    if "source_resistance_ohm" in semantic:
        raise RuntimeError(
            "differential source-degeneration removal left source resistance"
        )


def _add_differential_pair_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_label_instance_term as label_term,
    )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_differential_pair(before, payload["profile"])
    if variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "add_source_degeneration requires add_tail_device to be completed first"
        )
    resistance = float(payload["parameters"]["source_resistance_ohm"])
    before_semantic = _differential_pair_semantic_parameters_from_schematic(before)
    topology_changed = variant == _DIFFERENTIAL_PAIR_TAIL_VARIANT
    if topology_changed:
        _preflight_differential_source_degeneration(
            client, library, cell, remove=False
        )
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(
                    _differential_source_label_operation(
                        "MN0",
                        rename=True,
                        current_label="TAIL",
                        replacement_label="NSP",
                    )
                )
                schematic.add(
                    _differential_source_label_operation(
                        "MN1",
                        rename=True,
                        current_label="TAIL",
                        replacement_label="NSN",
                    )
                )
                schematic.add(
                    inst("analogLib", "res", "symbol", "RS0", -0.8, -0.9, "R0")
                )
                schematic.add(
                    inst("analogLib", "res", "symbol", "RS1", 0.8, -0.9, "R0")
                )
                schematic.add(label_term("RS0", "PLUS", "NSP"))
                schematic.add(label_term("RS0", "MINUS", "TAIL"))
                schematic.add(label_term("RS1", "PLUS", "NSN"))
                schematic.add(label_term("RS1", "MINUS", "TAIL"))
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "differential source-degeneration edit failed and unsaved-edit "
                    f"cleanup also failed: {cleanup_error}"
                ) from edit_error
            raise

    previous_resistance = before_semantic.get("source_resistance_ohm")
    resistance_changed = previous_resistance is None or abs(
        previous_resistance - resistance
    ) > max(abs(resistance) * 1e-6, 1e-9)
    if resistance_changed:
        summary = _apply_differential_pair_parameters(
            client,
            library,
            cell,
            {"source_resistance_ohm": resistance},
            payload["profile"],
        )
        after = summary["bridge_schematic"]
    else:
        after = _read_schematic(client, library, cell)
        summary = _differential_pair_summary(after)
    _assert_differential_pair_source_degeneration_preserved(
        before, after, resistance
    )
    placement_after = _schematic_placement_snapshot(client, library, cell)
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
        "transform_action": "add_source_degeneration",
        "placement_before": placement_before,
        "placement_after": placement_after,
        "resistance_changed": resistance_changed,
        "topology_delta": {
            "renamed_terminal_nets": (
                ["MN0.S: TAIL -> NSP", "MN1.S: TAIL -> NSN"]
                if topology_changed
                else []
            ),
            "added_instances": ["RS0", "RS1"] if topology_changed else [],
            "added_nets": ["NSP", "NSN"] if topology_changed else [],
            "preserved_instances": ["MN0", "MN1", "RD0", "RD1", "MNTAIL"],
            "preserved_pins": sorted((before.get("pins") or {}).keys()),
        },
        "readback": summary,
    }


def _remove_differential_pair_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_differential_pair(before, payload["profile"])
    if variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "remove_source_degeneration requires a real-tail differential pair"
        )
    topology_changed = variant == _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT
    if topology_changed:
        _preflight_differential_source_degeneration(
            client, library, cell, remove=True
        )
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(_delete_differential_source_degeneration_operation())
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "differential source-degeneration removal failed and unsaved-edit "
                    f"cleanup also failed: {cleanup_error}"
                ) from edit_error
            raise
    after = _read_schematic(client, library, cell)
    placement_after = _schematic_placement_snapshot(client, library, cell)
    _assert_differential_pair_source_degeneration_removal_preserved(before, after)
    transform_spec = payload.get("schematic_transform") or {}
    expected_placement = transform_spec.get("expected_restored_placement_sha256")
    if (
        expected_placement is not None
        and placement_after["sha256"] != expected_placement
    ):
        raise RuntimeError(
            "differential source-degeneration removal did not restore the declared "
            f"placement fingerprint: expected {expected_placement}, got "
            f"{placement_after['sha256']}"
        )
    return {
        "transformed": topology_changed,
        "already_removed": not topology_changed,
        "transform_action": "remove_source_degeneration",
        "placement_before": placement_before,
        "placement_after": placement_after,
        "restored_placement_match": (
            placement_after["sha256"] == expected_placement
            if expected_placement is not None
            else None
        ),
        "topology_delta": {
            "renamed_terminal_nets": (
                ["MN0.S: NSP -> TAIL", "MN1.S: NSN -> TAIL"]
                if topology_changed
                else []
            ),
            "removed_instances": ["RS0", "RS1"] if topology_changed else [],
            "removed_nets": ["NSP", "NSN"] if topology_changed else [],
            "preserved_instances": ["MN0", "MN1", "RD0", "RD1", "MNTAIL"],
            "preserved_pins": sorted((before.get("pins") or {}).keys()),
        },
        "readback": _differential_pair_summary(after),
    }


def _assert_differential_pair_current_mirror_load_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    pmos_load_width_um: float,
    pmos_load_length_um: float,
) -> None:
    before_variant = _assert_differential_pair(before)
    after_variant = _assert_differential_pair(after)
    if before_variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
    }:
        raise RuntimeError(
            "current-mirror-load transform requires an undegenerated real-tail "
            "differential pair"
        )
    if after_variant != _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT:
        raise RuntimeError(
            "current-mirror-load transform did not produce MP0/MP1"
        )
    if set((before.get("pins") or {}).keys()) != set(
        (after.get("pins") or {}).keys()
    ):
        raise RuntimeError("current-mirror-load transform changed top-level pins")
    if set((before.get("nets") or {}).keys()) != set(
        (after.get("nets") or {}).keys()
    ):
        raise RuntimeError("current-mirror-load transform changed top-level nets")
    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MN1", "MNTAIL"):
        if before_by_name[name] != after_by_name[name]:
            raise RuntimeError(
                f"current-mirror-load transform changed preserved {name}"
            )
    if before_variant == _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT:
        for name in ("MP0", "MP1"):
            before_without_params = {
                key: value
                for key, value in before_by_name[name].items()
                if key != "params"
            }
            after_without_params = {
                key: value
                for key, value in after_by_name[name].items()
                if key != "params"
            }
            if before_without_params != after_without_params:
                raise RuntimeError(
                    f"repeated current-mirror-load transform changed {name}"
                )
    _assert_parameter_consistency(
        {
            "pmos_load_width_um": pmos_load_width_um,
            "pmos_load_length_um": pmos_load_length_um,
        },
        _differential_pair_semantic_parameters_from_schematic(after),
        expected_label="requested PMOS load geometry",
        actual_label="OA readback",
    )


def _replace_differential_pair_resistive_load_with_current_mirror(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
    )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_differential_pair(before, payload["profile"])
    if variant not in {
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
    }:
        raise RuntimeError(
            "replace_resistive_load_with_current_mirror requires a real-tail "
            "differential pair without source degeneration"
        )
    pmos_load_width_um = float(payload["parameters"]["pmos_load_width_um"])
    pmos_load_length_um = float(payload["parameters"]["pmos_load_length_um"])
    topology_changed = variant == _DIFFERENTIAL_PAIR_TAIL_VARIANT
    if topology_changed:
        _preflight_differential_pair_load_transform(
            client, library, cell, current_mirror_to_resistors=False
        )
        profile = payload["profile"]
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(
                    _instance_terminal_stub_cleanup_operation(
                        "RD0", (("PLUS", "VDD"), ("MINUS", "OUTP"))
                    )
                )
                schematic.add(
                    _instance_terminal_stub_cleanup_operation(
                        "RD1", (("PLUS", "VDD"), ("MINUS", "OUTN"))
                    )
                )
                schematic.add(
                    inst(
                        profile["tech_library"],
                        profile["pmos_cell"],
                        "symbol",
                        "MP0",
                        -0.8,
                        1.3,
                        "R0",
                    )
                )
                schematic.add(
                    inst(
                        profile["tech_library"],
                        profile["pmos_cell"],
                        "symbol",
                        "MP1",
                        0.8,
                        1.3,
                        "R0",
                    )
                )
                schematic.add_net_label_to_transistor(
                    "MP0",
                    drain_net="OUTP",
                    gate_net="OUTP",
                    source_net="VDD",
                    body_net="VDD",
                )
                schematic.add_net_label_to_transistor(
                    "MP1",
                    drain_net="OUTN",
                    gate_net="OUTP",
                    source_net="VDD",
                    body_net="VDD",
                )
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "current-mirror-load edit failed and unsaved-edit cleanup also "
                    f"failed: {cleanup_error}"
                ) from edit_error
            raise
    summary = _apply_differential_pair_parameters(
        client,
        library,
        cell,
        {
            "pmos_load_width_um": pmos_load_width_um,
            "pmos_load_length_um": pmos_load_length_um,
        },
        payload["profile"],
    )
    after = summary["bridge_schematic"]
    _assert_differential_pair_current_mirror_load_preserved(
        before, after, pmos_load_width_um, pmos_load_length_um
    )
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
        "transform_action": "replace_resistive_load_with_current_mirror",
        "placement_before": placement_before,
        "placement_after": _schematic_placement_snapshot(client, library, cell),
        "topology_delta": {
            "removed_instances": ["RD0", "RD1"] if topology_changed else [],
            "added_instances": ["MP0", "MP1"] if topology_changed else [],
            "preserved_instances": ["MN0", "MN1", "MNTAIL"],
            "preserved_pins": sorted((before.get("pins") or {}).keys()),
            "preserved_nets": sorted((before.get("nets") or {}).keys()),
        },
        "output_contract": {
            "input": "INP-INN",
            "primary_output": "OUTN",
            "mirror_reference": "OUTP",
        },
        "readback": summary,
    }


def _assert_differential_pair_resistive_load_restored(
    before: dict[str, Any], after: dict[str, Any], load_resistance_ohm: float
) -> None:
    before_variant = _assert_differential_pair(before)
    after_variant = _assert_differential_pair(after)
    if before_variant not in {
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "restore_resistive_load requires a current-mirror-load or restored "
            "real-tail differential pair"
        )
    if after_variant != _DIFFERENTIAL_PAIR_TAIL_VARIANT:
        raise RuntimeError("restore_resistive_load did not produce RD0/RD1")
    if set((before.get("pins") or {}).keys()) != set(
        (after.get("pins") or {}).keys()
    ) or set((before.get("nets") or {}).keys()) != set(
        (after.get("nets") or {}).keys()
    ):
        raise RuntimeError("restore_resistive_load changed top-level pins or nets")
    before_by_name = {
        str(item.get("name")): item for item in before.get("instances", [])
    }
    after_by_name = {
        str(item.get("name")): item for item in after.get("instances", [])
    }
    for name in ("MN0", "MN1", "MNTAIL"):
        if before_by_name[name] != after_by_name[name]:
            raise RuntimeError(f"restore_resistive_load changed preserved {name}")
    _assert_parameter_consistency(
        {"load_resistance_ohm": load_resistance_ohm},
        _differential_pair_semantic_parameters_from_schematic(after),
        expected_label="requested restored load",
        actual_label="OA readback",
    )


def _restore_differential_pair_resistive_load(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_label_instance_term as label_term,
    )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
    placement_before = _schematic_placement_snapshot(client, library, cell)
    variant = _assert_differential_pair(before, payload["profile"])
    if variant not in {
        _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT,
        _DIFFERENTIAL_PAIR_TAIL_VARIANT,
    }:
        raise RuntimeError(
            "restore_resistive_load requires a current-mirror-load differential pair"
        )
    load_resistance_ohm = float(payload["parameters"]["load_resistance_ohm"])
    topology_changed = variant == _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT
    if topology_changed:
        _preflight_differential_pair_load_transform(
            client, library, cell, current_mirror_to_resistors=True
        )
        try:
            with _edit_existing_schematic(
                client, library, cell, timeout=90
            ) as schematic:
                schematic.add(
                    _instance_terminal_stub_cleanup_operation(
                        "MP0",
                        (("D", "OUTP"), ("G", "OUTP"), ("S", "VDD"), ("B", "VDD")),
                    )
                )
                schematic.add(
                    _instance_terminal_stub_cleanup_operation(
                        "MP1",
                        (("D", "OUTN"), ("G", "OUTP"), ("S", "VDD"), ("B", "VDD")),
                    )
                )
                schematic.add(
                    inst("analogLib", "res", "symbol", "RD0", -0.8, 1.3, "R0")
                )
                schematic.add(
                    inst("analogLib", "res", "symbol", "RD1", 0.8, 1.3, "R0")
                )
                schematic.add(label_term("RD0", "PLUS", "VDD"))
                schematic.add(label_term("RD0", "MINUS", "OUTP"))
                schematic.add(label_term("RD1", "PLUS", "VDD"))
                schematic.add(label_term("RD1", "MINUS", "OUTN"))
        except Exception as edit_error:
            try:
                _discard_failed_existing_schematic_edit(client, library, cell)
            except Exception as cleanup_error:
                raise RuntimeError(
                    "resistive-load restore failed and unsaved-edit cleanup also "
                    f"failed: {cleanup_error}"
                ) from edit_error
            raise
    summary = _apply_differential_pair_parameters(
        client,
        library,
        cell,
        {"load_resistance_ohm": load_resistance_ohm},
        payload["profile"],
    )
    after = summary["bridge_schematic"]
    _assert_differential_pair_resistive_load_restored(
        before, after, load_resistance_ohm
    )
    placement_after = _schematic_placement_snapshot(client, library, cell)
    transform_spec = payload.get("schematic_transform") or {}
    expected_placement = transform_spec.get("expected_restored_placement_sha256")
    if expected_placement is not None and placement_after["sha256"] != expected_placement:
        raise RuntimeError(
            "resistive-load restore did not restore the declared placement "
            f"fingerprint: expected {expected_placement}, got "
            f"{placement_after['sha256']}"
        )
    return {
        "transformed": topology_changed,
        "already_restored": not topology_changed,
        "transform_action": "restore_resistive_load",
        "placement_before": placement_before,
        "placement_after": placement_after,
        "restored_placement_match": (
            placement_after["sha256"] == expected_placement
            if expected_placement is not None
            else None
        ),
        "topology_delta": {
            "removed_instances": ["MP0", "MP1"] if topology_changed else [],
            "added_instances": ["RD0", "RD1"] if topology_changed else [],
            "preserved_instances": ["MN0", "MN1", "MNTAIL"],
            "preserved_pins": sorted((before.get("pins") or {}).keys()),
            "preserved_nets": sorted((before.get("nets") or {}).keys()),
        },
        "readback": summary,
    }


def transform_differential_pair(payload: dict[str, Any]) -> dict[str, Any]:
    transform_spec = payload.get("schematic_transform") or {}
    action = transform_spec.get("action")
    if action == "add_tail_device":
        return transform_differential_pair_tail_device(payload)
    if action == "add_source_degeneration":
        return _add_differential_pair_source_degeneration(payload)
    if action == "remove_source_degeneration":
        return _remove_differential_pair_source_degeneration(payload)
    if action == "replace_resistive_load_with_current_mirror":
        return _replace_differential_pair_resistive_load_with_current_mirror(payload)
    if action == "restore_resistive_load":
        return _restore_differential_pair_resistive_load(payload)
    raise RuntimeError(f"unsupported differential-pair transform action: {action!r}")


def inspect_differential_pair(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    data = _read_schematic(client, library, cell)
    _assert_differential_pair(data, payload["profile"])
    return _attach_targeted_parameter_verification(
        client, library, cell, payload, _differential_pair_summary(data)
    )


def apply_differential_pair_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    current = _read_schematic(client, library, cell)
    _assert_differential_pair(current, payload["profile"])
    result: dict[str, Any] = {}
    if payload.get("parameters"):
        oa_parameters = {
            name: float(payload["parameters"][name])
            for name in (
                "input_width_um",
                "length_um",
                "load_resistance_ohm",
                "tail_width_um",
                "tail_length_um",
                "source_resistance_ohm",
                "pmos_load_width_um",
                "pmos_load_length_um",
            )
            if name in payload["parameters"]
        }
        if oa_parameters:
            result = {
                "requested_parameters": payload["parameters"],
                "applied_device_parameters": oa_parameters,
                "readback": _apply_differential_pair_parameters(
                    client, library, cell, oa_parameters, payload["profile"]
                ),
            }
        else:
            result = {
                **_differential_pair_summary(current),
                "requested_parameters": payload["parameters"],
                "applied_device_parameters": {},
            }
    if payload.get("instance_parameter_updates"):
        explicit = _apply_explicit_instance_parameters(
            client, library, cell, payload
        )
        requested_semantic = {
            name: float(payload["parameters"][name])
            for name in (
                "input_width_um",
                "length_um",
                "load_resistance_ohm",
                "tail_width_um",
                "tail_length_um",
                "source_resistance_ohm",
                "pmos_load_width_um",
                "pmos_load_length_um",
            )
            if name in payload.get("parameters", {})
        }
        _assert_parameter_consistency(
            requested_semantic,
            explicit["readback"]["semantic_parameters"],
            expected_label="requested semantic write",
            actual_label="final OA readback after explicit CDF callbacks",
        )
        result.update(explicit)
        result["semantic_readback"] = explicit["readback"]["semantic_parameters"]
    return result


_SI_ENV_OVERRIDES = {
    "simNotIncremental": "simNotIncremental = 't",
    "simReNetlistAll": "simReNetlistAll = nil",
    "simViewList": "simViewList = '(\"spectre\" \"config\" \"schematic\" \"veriloga\")",
    "simStopList": "simStopList = '(\"spectre\")",
    "simNetlistHier": "simNetlistHier = 't",
    "nlFormatterClass": "nlFormatterClass = 'spectreFormatter",
    "nlCreateAmap": "nlCreateAmap = 't",
    "nlDesignVarNameList": "nlDesignVarNameList = nil",
}


def _complete_si_env(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match is not None and match.group(1) in _SI_ENV_OVERRIDES:
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept + ["", *_SI_ENV_OVERRIDES.values(), ""])


def _read_nonempty_text(path: Path, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"{label} was not created")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise RuntimeError(f"{label} is empty")
    return text


def _logical_netlist_records(text: str) -> list[str]:
    records: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        # Spectre ``si`` indents ordinary instances inside a subckt.  Indent is
        # therefore not a continuation marker; only an explicit leading ``+``
        # or a trailing backslash on the prior record joins physical lines.
        continuation = bool(current) and (
            stripped.startswith("+") or current.endswith("\\")
        )
        if continuation:
            current = (
                current.rstrip("\\").rstrip()
                + " "
                + stripped.lstrip("+").strip()
            )
            continue
        if current:
            records.append(current)
        current = stripped
    if current:
        records.append(current)
    return records


def _parse_ade_spectre_input(text: str) -> dict[str, Any]:
    headers: dict[str, str] = {}
    for key, label in (
        ("library", "library"),
        ("cell", "cell"),
        ("view", "view"),
    ):
        match = re.search(
            rf"^// Design {label} name:\s*(\S+)\s*$", text, re.MULTILINE
        )
        if match is None:
            raise RuntimeError(f"ADE Spectre input is missing Design {label} header")
        headers[key] = match.group(1)

    instances: dict[str, dict[str, Any]] = {}
    design_variables: dict[str, str] = {}
    analyses: list[str] = []
    saves: list[str] = []
    for record in _logical_netlist_records(text):
        if record.startswith("parameters "):
            for match in re.finditer(
                r"(?:^|\s)([A-Za-z_][A-Za-z0-9_$]*)=(\"[^\"]*\"|\S+)",
                record[len("parameters ") :],
            ):
                name = match.group(1)
                value = match.group(2).strip('"')
                if name in design_variables and design_variables[name] != value:
                    raise RuntimeError(
                        f"ADE Spectre input repeats design variable {name} with "
                        "different values"
                    )
                design_variables[name] = value
            continue
        instance_match = re.match(
            r"^(\S+)\s*\(([^)]*)\)\s+(\S+)(?:\s+(.*))?$", record
        )
        if instance_match is not None:
            name, nodes_text, model, parameter_text = instance_match.groups()
            if name in instances:
                raise RuntimeError(f"ADE Spectre input repeats instance {name}")
            parameters = {
                match.group(1): match.group(2).strip('"')
                for match in re.finditer(
                    r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|\S+)",
                    parameter_text or "",
                )
            }
            instances[name] = {
                "nodes": nodes_text.split(),
                "model": model,
                "parameters": parameters,
            }
            continue
        if re.match(r"^(ac|dc|noise|tran)\s+", record):
            analyses.append(record)
        elif record.startswith("save "):
            saves.append(record)
    if not instances:
        raise RuntimeError("ADE Spectre input contains no top-level instances")
    return {
        "design": headers,
        "design_variables": design_variables,
        "instances": instances,
        "analysis_records": analyses,
        "save_records": saves,
    }


def _normalized_net_name(value: Any) -> str:
    name = str(value)
    return "0" if name in {"0", "gnd!"} else name


def _ade_instance_contract(instance: dict[str, Any]) -> dict[str, Any] | None:
    cell = str(instance.get("cell") or "")
    terminals = instance.get("terms") or {}
    if {"D", "G", "S", "B"}.issubset(terminals):
        return {
            "model": str((instance.get("params") or {}).get("model") or cell),
            "terminal_order": ("D", "G", "S", "B"),
            "parameter_map": (("l", "l"), ("w", "w"), ("nf", "nf"), ("simM", "multi")),
        }
    analog_contracts = {
        "cap": {
            "model": "capacitor",
            "terminal_order": ("PLUS", "MINUS"),
            "parameter_map": (("c", "c"),),
        },
        "res": {
            "model": "resistor",
            "terminal_order": ("PLUS", "MINUS"),
            "parameter_map": (("r", "r"),),
        },
        "vdc": {
            "model": "vsource",
            "terminal_order": ("PLUS", "MINUS"),
            "parameter_map": (("vdc", "dc"), ("srcType", "type")),
        },
        "vpulse": {
            "model": "vsource",
            "terminal_order": ("PLUS", "MINUS"),
            "parameter_map": (
                ("v1", "val0"),
                ("v2", "val1"),
                ("per", "period"),
                ("td", "delay"),
                ("tr", "rise"),
                ("tf", "fall"),
                ("pw", "width"),
                ("srcType", "type"),
            ),
        },
    }
    return analog_contracts.get(cell)


def _compare_ade_input_to_schematic(
    parsed: dict[str, Any],
    schematic: dict[str, Any],
    *,
    design: dict[str, str],
    sweep_bindings: dict[tuple[str, str], dict[str, str]] | None = None,
    symbolic_sweep_bindings: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    if sweep_bindings and symbolic_sweep_bindings:
        raise RuntimeError(
            "ADE input comparison cannot mix resolved-point and symbolic sweep "
            "bindings"
        )
    if parsed["design"] != design:
        raise RuntimeError(
            "ADE Spectre input design header does not match Maestro test design: "
            f"{parsed['design']!r} != {design!r}"
        )
    oa_instances = {
        str(item.get("name")): item for item in schematic.get("instances", [])
    }
    omitted_ground_symbols = sorted(
        name
        for name, item in oa_instances.items()
        if str(item.get("cell") or "").lower() in {"gnd", "vss"}
    )
    expected_names = set(oa_instances) - set(omitted_ground_symbols)
    actual_names = set(parsed["instances"])
    if expected_names != actual_names:
        raise RuntimeError(
            "ADE Spectre/OA instance set mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )

    comparisons: list[dict[str, Any]] = []
    excluded_cdf_semantics: list[dict[str, str]] = []
    verified_parameter_pairs = 0
    verified_sweep_bindings: set[tuple[str, str]] = set()
    verified_symbolic_sweep_bindings: set[tuple[str, str]] = set()
    requested_sweep_bindings = sweep_bindings or {}
    requested_symbolic_sweep_bindings = symbolic_sweep_bindings or {}
    for name in sorted(expected_names):
        oa_instance = oa_instances[name]
        contract = _ade_instance_contract(oa_instance)
        if contract is None:
            raise RuntimeError(
                "ADE same-source consistency has no explicit primitive contract for "
                f"{name} ({oa_instance.get('lib')}/{oa_instance.get('cell')})"
            )
        netlist_instance = parsed["instances"][name]
        if netlist_instance["model"] != contract["model"]:
            raise RuntimeError(
                f"ADE Spectre model mismatch for {name}: "
                f"{netlist_instance['model']!r} != {contract['model']!r}"
            )
        terminals = oa_instance.get("terms") or {}
        expected_nodes = [
            _normalized_net_name(terminals[terminal])
            for terminal in contract["terminal_order"]
        ]
        if netlist_instance["nodes"] != expected_nodes:
            raise RuntimeError(
                f"ADE Spectre node mismatch for {name}: "
                f"{netlist_instance['nodes']!r} != {expected_nodes!r}"
            )
        oa_parameters = oa_instance.get("params") or {}
        netlist_parameters = netlist_instance["parameters"]
        parameter_checks: list[dict[str, str]] = []
        for oa_name, netlist_name in contract["parameter_map"]:
            if oa_name not in oa_parameters or netlist_name not in netlist_parameters:
                raise RuntimeError(
                    f"ADE Spectre parameter mapping is missing {name}."
                    f"{oa_name}->{netlist_name}"
                )
            oa_value = str(oa_parameters[oa_name])
            netlist_value = str(netlist_parameters[netlist_name])
            binding = requested_sweep_bindings.get((name, oa_name))
            symbolic_variable = requested_symbolic_sweep_bindings.get(
                (name, oa_name)
            )
            if binding is None and symbolic_variable is None:
                if not spectre_values_equal(oa_value, netlist_value):
                    raise RuntimeError(
                        f"ADE Spectre parameter mismatch for {name}."
                        f"{oa_name}->{netlist_name}: {oa_value!r} != "
                        f"{netlist_value!r}"
                    )
                parameter_checks.append(
                    {
                        "oa_parameter": oa_name,
                        "netlist_parameter": netlist_name,
                        "oa_value": oa_value,
                        "netlist_value": netlist_value,
                    }
                )
            elif binding is not None:
                variable = str(binding["variable"])
                point_value = str(binding["point_value"])
                if oa_value != variable:
                    raise RuntimeError(
                        f"ADE sweep binding expected OA {name}.{oa_name} to "
                        f"reference {variable!r}, got {oa_value!r}"
                    )
                if spectre_values_equal(netlist_value, point_value):
                    effective_value = netlist_value
                    resolution = "resolved_instance_parameter"
                elif netlist_value == variable:
                    design_value = (parsed.get("design_variables") or {}).get(
                        variable
                    )
                    if design_value is None or not spectre_values_equal(
                        design_value, point_value
                    ):
                        raise RuntimeError(
                            "ADE effective sweep value mismatch for "
                            f"{variable} at {name}.{oa_name}: expected "
                            f"{point_value!r}, input declared {design_value!r}"
                        )
                    effective_value = str(design_value)
                    resolution = "spectre_design_variable"
                else:
                    raise RuntimeError(
                        "ADE effective sweep value mismatch for "
                        f"{variable} at {name}.{oa_name}: expected "
                        f"{point_value!r}, netlist used {netlist_value!r}"
                    )
                parameter_checks.append(
                    {
                        "oa_parameter": oa_name,
                        "netlist_parameter": netlist_name,
                        "oa_value": oa_value,
                        "netlist_value": netlist_value,
                        "sweep_variable": variable,
                        "point_value": point_value,
                        "effective_value": effective_value,
                        "resolution": resolution,
                    }
                )
                verified_sweep_bindings.add((name, oa_name))
            else:
                variable = str(symbolic_variable)
                if oa_value != variable:
                    raise RuntimeError(
                        f"ADE symbolic sweep binding expected OA {name}.{oa_name} "
                        f"to reference {variable!r}, got {oa_value!r}"
                    )
                if netlist_value != variable:
                    raise RuntimeError(
                        f"ADE symbolic sweep binding expected netlist {name}."
                        f"{netlist_name} to reference {variable!r}, got "
                        f"{netlist_value!r}"
                    )
                retained_value = (parsed.get("design_variables") or {}).get(variable)
                if retained_value is None:
                    raise RuntimeError(
                        f"ADE symbolic sweep input did not declare {variable!r}"
                    )
                parameter_checks.append(
                    {
                        "oa_parameter": oa_name,
                        "netlist_parameter": netlist_name,
                        "oa_value": oa_value,
                        "netlist_value": netlist_value,
                        "sweep_variable": variable,
                        "retained_design_value": str(retained_value),
                        "resolution": "symbolic_spectre_design_variable",
                    }
                )
                verified_symbolic_sweep_bindings.add((name, oa_name))
            verified_parameter_pairs += 1
        if "Wfg" in oa_parameters:
            excluded_cdf_semantics.append(
                {
                    "instance": name,
                    "parameter": "Wfg",
                    "reason": "PDK CDF effective-width relation is not a literal netlist w equality",
                }
            )
        comparisons.append(
            {
                "instance": name,
                "oa_master": f"{oa_instance.get('lib')}/{oa_instance.get('cell')}",
                "netlist_model": netlist_instance["model"],
                "nodes": netlist_instance["nodes"],
                "parameter_checks": parameter_checks,
            }
        )
    missing_sweep_bindings = sorted(
        set(requested_sweep_bindings) - verified_sweep_bindings
    )
    if missing_sweep_bindings:
        raise RuntimeError(
            "ADE sweep bindings were not covered by known primitive parameter "
            f"contracts: {missing_sweep_bindings}"
        )
    missing_symbolic_sweep_bindings = sorted(
        set(requested_symbolic_sweep_bindings)
        - verified_symbolic_sweep_bindings
    )
    if missing_symbolic_sweep_bindings:
        raise RuntimeError(
            "ADE symbolic sweep bindings were not covered by known primitive "
            f"parameter contracts: {missing_symbolic_sweep_bindings}"
        )
    all_verified_sweep_bindings = (
        verified_sweep_bindings | verified_symbolic_sweep_bindings
    )
    payload = {
        "design": design,
        "instances": comparisons,
        "omitted_ground_symbols": omitted_ground_symbols,
        "verified_sweep_bindings": [
            {"instance": instance, "oa_parameter": parameter}
            for instance, parameter in sorted(all_verified_sweep_bindings)
        ],
    }
    return {
        **payload,
        "design_identity_verified": True,
        "instance_set_verified": True,
        "node_connectivity_verified": True,
        "verified_parameter_pairs": verified_parameter_pairs,
        "raw_parameter_mapping_verified": True,
        "verified_sweep_binding_pairs": len(all_verified_sweep_bindings),
        "effective_sweep_bindings_verified": (
            bool(verified_sweep_bindings) if requested_sweep_bindings else False
        ),
        "symbolic_sweep_bindings_verified": (
            bool(verified_symbolic_sweep_bindings)
            if requested_symbolic_sweep_bindings
            else False
        ),
        "excluded_cdf_semantics": excluded_cdf_semantics,
        "effective_pdk_width_semantics_verified": False,
        "comparison_sha256": hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    }


def _verify_ade_simulator_inputs(
    client,
    *,
    session: str,
    tests: list[str],
    artifact_evidence: dict[str, Any],
) -> dict[str, Any]:
    manifest = artifact_evidence.get("artifact_manifest") or []
    evidence: list[dict[str, Any]] = []
    for test in tests:
        test_token = re.sub(r"[^A-Za-z0-9_.-]", "_", test)
        suffix = f"/runtime/{test_token}/input.scs"
        matches = [
            item
            for item in manifest
            if item.get("binding") == "unique_runtime_session"
            and str(item.get("path") or "").endswith(suffix)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"ADE input consistency requires one exact input.scs for {test}; "
                f"found {len(matches)}"
            )
        manifest_item = matches[0]
        input_text = _read_remote_text_via_skill(
            client, str(manifest_item["remote_path"]), page_lines=8
        )
        digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        if digest != manifest_item.get("sha256"):
            raise RuntimeError(
                f"ADE input.scs content hash changed after manifest capture for {test}"
            )
        design = _maestro_test_design_readback(client, test, session=session)
        if design["view"] != "schematic":
            raise RuntimeError(
                "ADE input consistency currently requires a schematic source view: "
                f"{design!r}"
            )
        schematic = _read_schematic(client, design["library"], design["cell"])
        comparison = _compare_ade_input_to_schematic(
            _parse_ade_spectre_input(input_text),
            schematic,
            design=design,
        )
        evidence.append(
            {
                "test": test,
                "input_path": manifest_item["path"],
                "input_remote_path": manifest_item["remote_path"],
                "input_sha256": digest,
                **comparison,
            }
        )
    return {
        "simulator_input_consistency_verified": bool(evidence),
        "simulator_input_consistency": evidence,
        "simulator_input_consistency_evidence_sources": {
            "maestro_design_and_oa": "bridge_readback",
            "spectre_input": "eda_result",
            "comparison": "software_inference",
        },
    }


def _ade_point_artifacts(
    manifest: list[dict[str, Any]],
    *,
    history: str,
    point: int,
    test: str,
    category: str,
    allow_unlabeled: bool,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    prefix = f"{history}/{point}/"
    test_token = re.sub(r"[^A-Za-z0-9_.-]", "_", test)
    point_candidates = [
        item
        for item in manifest
        if item.get("binding") == "exact_history_path"
        and item.get("category") == category
        and int(item.get("size_bytes") or 0) > 0
        and str(item.get("path") or "").startswith(prefix)
        and (
            filename is None
            or Path(str(item.get("path") or "")).name == filename
        )
    ]
    test_candidates = [
        item
        for item in point_candidates
        if test in str(item.get("path") or "").split("/")[2:-1]
        or test_token in str(item.get("path") or "").split("/")[2:-1]
    ]
    if test_candidates:
        return test_candidates
    return point_candidates if allow_unlabeled else []


def _remote_manifest_item_path(item: dict[str, Any]) -> str:
    remote_path = str(item.get("remote_path") or "")
    if remote_path:
        return remote_path
    remote_paths = item.get("remote_paths")
    if isinstance(remote_paths, list) and remote_paths:
        return str(remote_paths[0])
    raise RuntimeError(
        f"ADE artifact {item.get('path')!r} has no retained remote path"
    )


def _is_ade_output_evaluation_error(value: Any) -> bool:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in {"eval err", "evaluation error", "error"}


def _ade_sweep_output_evaluation_error_evidence(
    *,
    tests: list[str],
    expected_points: list[dict[str, Any]],
    actual_points: dict[int, dict[str, Any]],
    verification: dict[str, Any],
) -> dict[str, Any]:
    """Require every Detail calculator error to match an explicit point rule."""

    expectations = list(
        verification.get("expected_output_evaluation_errors") or []
    )
    if expectations and len(tests) != 1:
        raise RuntimeError(
            "ADE expected output evaluation errors require exactly one test"
        )
    expected_cells: dict[tuple[int, str], dict[str, Any]] = {}
    for expectation in expectations:
        if not isinstance(expectation, dict):
            raise RuntimeError(
                "ADE expected output evaluation-error rule is not an object"
            )
        test = str(expectation.get("test") or "")
        output = str(expectation.get("output") or "")
        point_values = expectation.get("point_values") or {}
        if not isinstance(point_values, dict) or test not in tests or not output:
            raise RuntimeError(
                "ADE expected output evaluation-error rule is invalid"
            )
        matched = False
        for point in expected_points:
            values = point.get("values") or {}
            if all(
                name in values
                and spectre_values_equal(values[name], expected_value)
                for name, expected_value in point_values.items()
            ):
                matched = True
                point_number = int(point["point"])
                identity = (point_number, output)
                if identity in expected_cells:
                    raise RuntimeError(
                        "ADE expected output evaluation-error rules overlap at "
                        f"point {point_number} output {output!r}"
                    )
                expected_cells[identity] = {
                    "point": point_number,
                    "test": test,
                    "output": output,
                    "point_values": {
                        str(name): str(value)
                        for name, value in point_values.items()
                    },
                    "expectation_evidence_source": "user_input",
                }
        if not matched:
            raise RuntimeError(
                "ADE expected output evaluation-error rule did not match a point"
            )

    actual_cells: dict[tuple[int, str], dict[str, Any]] = {}
    for point_number, point in actual_points.items():
        outputs = point.get("outputs") or {}
        if not isinstance(outputs, dict):
            continue
        for output, details in outputs.items():
            if not isinstance(details, dict) or not _is_ade_output_evaluation_error(
                details.get("value")
            ):
                continue
            identity = (point_number, str(output))
            actual_cells[identity] = {
                "point": point_number,
                "test": tests[0] if len(tests) == 1 else None,
                "output": str(output),
                "raw_value": str(details.get("value") or ""),
                "raw_evidence_source": "eda_result",
            }

    missing = sorted(set(expected_cells) - set(actual_cells))
    unexpected = sorted(set(actual_cells) - set(expected_cells))
    if missing or unexpected:
        raise RuntimeError(
            "ADE native sweep output evaluation errors did not exactly match the "
            f"declared point/output cells: missing={missing}, unexpected={unexpected}"
        )
    verified = [
        {**expected_cells[identity], **actual_cells[identity]}
        for identity in sorted(expected_cells)
    ]
    return {
        "expected_output_evaluation_errors_verified": True,
        "output_evaluation_errors": verified,
        "output_evaluation_error_count": len(verified),
        "output_evaluation_error_evidence_sources": {
            "expected": "user_input" if expectations else None,
            "actual": "eda_result",
            "comparison": "software_inference",
        },
    }


def _verify_ade_sweep_consistency(
    client,
    *,
    session: str,
    tests: list[str],
    history: str,
    results: dict[str, Any],
    artifact_evidence: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    """Bind each declared sweep point through exact files or Maestro's RDB."""

    expected_tests = [str(value) for value in verification.get("expected_tests") or []]
    if tests != expected_tests:
        raise RuntimeError(
            "ADE sweep test mismatch: "
            f"expected {expected_tests!r}, got {tests!r}"
        )
    variables = list(verification.get("variables") or [])
    point_variable_names = [
        str(variable.get("name") or "")
        for variable in variables
        if variable.get("sweep", True)
    ]
    variable_names = list(
        dict.fromkeys(str(variable.get("name") or "") for variable in variables)
    )
    if (
        not point_variable_names
        or len(point_variable_names) != len(set(point_variable_names))
        or any(not name for name in variable_names)
    ):
        raise RuntimeError("ADE sweep verification has invalid variable names")
    expected_points = list(verification.get("points") or [])
    if len(expected_points) < 2:
        raise RuntimeError("ADE sweep verification requires at least two points")
    bindings = list(verification.get("input_bindings") or [])
    if not bindings:
        raise RuntimeError("ADE sweep verification requires OA input bindings")

    corner_mode = any(
        isinstance(point, dict) and point.get("corner") is not None
        for point in expected_points
    )
    raw_points = results.get("corner_points" if corner_mode else "points")
    if not isinstance(raw_points, list):
        raise RuntimeError("ADE sweep structured results did not contain points")
    actual_points: dict[int, dict[str, Any]] = {}
    if corner_mode:
        expected_corners = [
            str(value) for value in verification.get("expected_corners") or []
        ]
        if results.get("corner_order") != expected_corners:
            raise RuntimeError("ADE sweep result corner columns changed")
        if results.get("corner_tests") != tests:
            raise RuntimeError("ADE sweep result test columns changed")
        if results.get("corner_detail_csv_evidence_sources") != {
            "raw": "eda_result",
            "parser": "software_inference",
        } or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(results.get("corner_detail_csv_sha256") or ""),
        ):
            raise RuntimeError("ADE corner sweep lacked hashed Detail CSV evidence")
        raw_by_selector: dict[tuple[int, str], dict[str, Any]] = {}
        for item in raw_points:
            if not isinstance(item, dict):
                raise RuntimeError("ADE corner result cell is not an object")
            try:
                selector = (int(item.get("maestro_point")), str(item.get("corner")))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("ADE corner result has an invalid selector") from exc
            if selector in raw_by_selector:
                raise RuntimeError(f"ADE corner result repeats {selector!r}")
            raw_by_selector[selector] = item
        for expected in expected_points:
            selector = (
                int(expected.get("maestro_point")),
                str(expected.get("corner")),
            )
            item = raw_by_selector.pop(selector, None)
            if item is None:
                raise RuntimeError(f"ADE corner result omitted {selector!r}")
            point_number = int(expected.get("point"))
            actual_points[point_number] = {**item, "point": point_number}
        if raw_by_selector:
            raise RuntimeError(
                f"ADE corner result exposed undeclared cells: {sorted(raw_by_selector)!r}"
            )
        results["corner_points"] = [
            actual_points[int(expected["point"])] for expected in expected_points
        ]
    else:
        for item in raw_points:
            if not isinstance(item, dict):
                raise RuntimeError("ADE sweep structured result point is not an object")
            try:
                point_number = int(item.get("point"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("ADE sweep result has an invalid point number") from exc
            if point_number in actual_points:
                raise RuntimeError(f"ADE sweep result repeats point {point_number}")
            actual_points[point_number] = item
    expected_numbers = [int(item.get("point")) for item in expected_points]
    if sorted(actual_points) != expected_numbers:
        raise RuntimeError(
            "ADE sweep result point set mismatch: "
            f"expected {expected_numbers!r}, got {sorted(actual_points)!r}"
        )
    evaluation_error_evidence = _ade_sweep_output_evaluation_error_evidence(
        tests=tests,
        expected_points=expected_points,
        actual_points=actual_points,
        verification=verification,
    )

    manifest = artifact_evidence.get("artifact_manifest") or []
    if not isinstance(manifest, list) or not manifest:
        raise RuntimeError("ADE sweep verification requires a non-empty artifact manifest")
    designs: dict[str, dict[str, str]] = {}
    schematics: dict[str, dict[str, Any]] = {}
    for test in tests:
        design = _maestro_test_design_readback(client, test, session=session)
        if design["view"] != "schematic":
            raise RuntimeError(
                "ADE sweep input consistency currently requires schematic source "
                f"views: {design!r}"
            )
        designs[test] = design
        schematics[test] = _read_schematic(
            client, design["library"], design["cell"]
        )

    input_evidence: list[dict[str, Any]] = []
    point_evidence: list[dict[str, Any]] = []
    exact_point_tree_present = any(
        item.get("binding") == "exact_history_path"
        and item.get("category") in {"simulator_input", "eda_result"}
        and re.match(
            rf"^{re.escape(history)}/[0-9]+/",
            str(item.get("path") or ""),
        )
        for item in manifest
    )
    database_mode = not exact_point_tree_present
    if corner_mode and not database_mode:
        raise RuntimeError(
            "ADE corner sweep verification requires exact-history database mode"
        )
    database_result_artifacts: list[dict[str, Any]] = []
    shared_inputs_by_test: dict[str, dict[str, Any]] = {}
    history_log_evidence: dict[str, Any] | None = None
    if database_mode:
        expected_rdb_path = f"{history}/{history}.rdb"
        rdb_candidates = [
            item
            for item in manifest
            if item.get("path") == expected_rdb_path
            and item.get("binding") == "exact_history_companion"
            and item.get("category") == "eda_result"
            and int(item.get("size_bytes") or 0) > 0
        ]
        if len(rdb_candidates) != 1:
            raise RuntimeError(
                "ADE native sweep database evidence requires one non-empty "
                f"exact-history RDB; found {len(rdb_candidates)}"
            )
        rdb_item = rdb_candidates[0]
        database_result_artifacts = [
            {
                "path": rdb_item["path"],
                "sha256": rdb_item["sha256"],
                "size_bytes": rdb_item["size_bytes"],
            }
        ]

        expected_log_path = f"{history}/{history}.log"
        log_candidates = [
            item
            for item in manifest
            if item.get("path") == expected_log_path
            and item.get("binding") == "exact_history_companion"
            and item.get("category") == "run_log"
            and int(item.get("size_bytes") or 0) > 0
        ]
        if len(log_candidates) != 1:
            raise RuntimeError(
                "ADE native sweep database evidence requires one non-empty "
                f"exact-history log; found {len(log_candidates)}"
            )
        log_item = log_candidates[0]
        log_remote_path = _remote_manifest_item_path(log_item)
        log_text = _read_remote_text_via_skill(
            client, log_remote_path, page_lines=16
        )
        log_digest = hashlib.sha256(log_text.encode("utf-8")).hexdigest()
        if log_digest != log_item.get("sha256"):
            raise RuntimeError(
                "ADE native sweep history log changed after manifest capture"
            )
        completed_match = re.search(
            r"Number of points completed:\s*([0-9]+)", log_text
        )
        error_match = re.search(
            r"Number of simulation errors:\s*([0-9]+)", log_text
        )
        history_completed = re.search(
            rf"(?m)^\s*{re.escape(history)} completed\.\s*$", log_text
        ) is not None
        points_completed = (
            int(completed_match.group(1)) if completed_match is not None else -1
        )
        simulation_errors = (
            int(error_match.group(1)) if error_match is not None else -1
        )
        expected_simulation_errors = int(
            evaluation_error_evidence["output_evaluation_error_count"]
        )
        if (
            points_completed
            != len(
                {
                    int(point.get("maestro_point") or point.get("point"))
                    for point in expected_points
                }
            )
            or simulation_errors != expected_simulation_errors
            or not history_completed
        ):
            error_requirement = (
                "zero simulation errors"
                if expected_simulation_errors == 0
                else "only the explicitly declared output evaluation errors"
            )
            raise RuntimeError(
                "ADE native sweep history log did not prove the exact point count, "
                f"{error_requirement}, and completed history"
            )
        history_log_evidence = {
            "path": log_item["path"],
            "sha256": log_digest,
            "size_bytes": log_item["size_bytes"],
            "points_completed": points_completed,
            "simulation_errors": simulation_errors,
            "simulation_errors_accounted_by_output_evaluation_errors": (
                expected_simulation_errors
            ),
            "unaccounted_simulation_errors": 0,
            "history_completed": True,
        }

        for test in tests:
            test_token = re.sub(r"[^A-Za-z0-9_.-]", "_", test)
            input_suffix = f"/runtime/{test_token}/input.scs"
            shared_inputs = [
                item
                for item in manifest
                if item.get("binding") == "unique_runtime_session"
                and item.get("category") == "simulator_input"
                and int(item.get("size_bytes") or 0) > 0
                and str(item.get("path") or "").endswith(input_suffix)
            ]
            if len(shared_inputs) != 1:
                raise RuntimeError(
                    "ADE native sweep database evidence requires one unique "
                    f"runtime input.scs for {test}; found {len(shared_inputs)}"
                )
            input_item = shared_inputs[0]
            input_remote_path = _remote_manifest_item_path(input_item)
            input_text = _read_remote_text_via_skill(
                client, input_remote_path, page_lines=32
            )
            input_digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
            if input_digest != input_item.get("sha256"):
                raise RuntimeError(
                    "ADE native sweep runtime input changed after manifest capture "
                    f"for {test}"
                )
            netlist_path = str(input_item["path"]).removesuffix("input.scs") + "netlist"
            netlist_candidates = [
                item
                for item in manifest
                if item.get("path") == netlist_path
                and item.get("binding") == "unique_runtime_session"
                and item.get("category") == "simulator_input"
                and int(item.get("size_bytes") or 0) > 0
            ]
            if len(netlist_candidates) != 1:
                raise RuntimeError(
                    "ADE native sweep runtime input requires one non-empty sibling "
                    f"netlist for {test}; found {len(netlist_candidates)}"
                )
            if re.search(
                r'(?m)^\s*include\s+"(?:[.]/)?netlist"\s*$', input_text
            ) is None:
                raise RuntimeError(
                    f"ADE native sweep input.scs did not include its sibling netlist "
                    f"for {test}"
                )
            netlist_item = netlist_candidates[0]
            netlist_remote_path = _remote_manifest_item_path(netlist_item)
            netlist_text = _read_remote_text_via_skill(
                client, netlist_remote_path, page_lines=32
            )
            netlist_digest = hashlib.sha256(netlist_text.encode("utf-8")).hexdigest()
            if netlist_digest != netlist_item.get("sha256"):
                raise RuntimeError(
                    "ADE native sweep runtime netlist changed after manifest "
                    f"capture for {test}"
                )
            test_bindings = [
                binding
                for binding in bindings
                if isinstance(binding, dict) and binding.get("test") == test
            ]
            symbolic_binding_map = {
                (str(binding["instance"]), str(binding["oa_parameter"])): str(
                    binding["variable"]
                )
                for binding in test_bindings
            }
            if not symbolic_binding_map:
                raise RuntimeError(
                    f"ADE native sweep test {test} has no OA bindings"
                )
            parsed_input = _parse_ade_spectre_input(
                f"{input_text.rstrip()}\n{netlist_text}"
            )
            comparison = _compare_ade_input_to_schematic(
                parsed_input,
                schematics[test],
                design=designs[test],
                symbolic_sweep_bindings=symbolic_binding_map,
            )
            if comparison.get("symbolic_sweep_bindings_verified") is not True:
                raise RuntimeError(
                    f"ADE native sweep test {test} did not verify symbolic OA "
                    "variable bindings"
                )
            retained_values: dict[str, str] = {}
            for variable_name in variable_names:
                retained_value = (parsed_input.get("design_variables") or {}).get(
                    variable_name
                )
                if retained_value is None:
                    raise RuntimeError(
                        "ADE native sweep runtime input did not declare "
                        f"{variable_name!r} for {test}"
                    )
                retained_values[variable_name] = str(retained_value)
            retained_points = {
                int(point.get("maestro_point") or point["point"])
                for point in expected_points
                if all(
                    spectre_values_equal(
                        retained_values[name], str(point["values"][name])
                    )
                    for name in point_variable_names
                )
            }
            if len(retained_points) != 1:
                raise RuntimeError(
                    "ADE native sweep runtime input retained values did not match "
                    f"one declared point for {test}: {retained_values!r}"
                )
            retained_maestro_point = next(iter(retained_points))
            item_evidence = {
                "test": test,
                "input_path": input_item["path"],
                "input_remote_path": input_remote_path,
                "input_sha256": input_digest,
                "included_netlist_path": netlist_item["path"],
                "included_netlist_remote_path": netlist_remote_path,
                "included_netlist_sha256": netlist_digest,
                "input_bundle_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "input.scs": input_digest,
                            "netlist": netlist_digest,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "retained_sweep_values": retained_values,
                **comparison,
            }
            if corner_mode:
                item_evidence["retained_maestro_point"] = retained_maestro_point
            else:
                item_evidence["retained_point"] = retained_maestro_point
            input_evidence.append(item_evidence)
            shared_inputs_by_test[test] = {
                "path": input_item["path"],
                "sha256": input_digest,
                "included_netlist_path": netlist_item["path"],
                "included_netlist_sha256": netlist_digest,
                "input_bundle_sha256": item_evidence["input_bundle_sha256"],
                "comparison_sha256": comparison["comparison_sha256"],
            }

    for expected in expected_points:
        point_number = int(expected["point"])
        expected_values = {
            str(name): str(value)
            for name, value in (expected.get("values") or {}).items()
        }
        if set(expected_values) != set(variable_names):
            raise RuntimeError(
                f"ADE sweep point {point_number} has an invalid expected variable set"
            )
        actual = actual_points[point_number]
        actual_parameters = actual.get("parameters")
        if not isinstance(actual_parameters, dict):
            raise RuntimeError(
                f"ADE sweep point {point_number} did not expose result parameters"
            )
        for name, expected_value in expected_values.items():
            actual_value = actual_parameters.get(name)
            if actual_value is None or not spectre_values_equal(
                actual_value, expected_value
            ):
                raise RuntimeError(
                    f"ADE sweep result parameter mismatch at point {point_number} "
                    f"for {name}: expected {expected_value!r}, got {actual_value!r}"
                )
        outputs = actual.get("outputs")
        scalar_outputs = {
            str(name): str(info.get("value") or "")
            for name, info in (outputs or {}).items()
            if isinstance(info, dict) and str(info.get("value") or "").strip()
        }
        if not scalar_outputs:
            raise RuntimeError(
                f"ADE sweep point {point_number} had no non-empty scalar output"
            )

        point_tests: list[dict[str, Any]] = []
        for test in tests:
            if database_mode:
                point_tests.append(
                    {
                        "test": test,
                        "evidence_mode": (
                            "maestro_exact_history_rdb_with_shared_symbolic_"
                            "runtime_input"
                        ),
                        "inputs": [shared_inputs_by_test[test]],
                        "result_artifacts": database_result_artifacts,
                    }
                )
                continue
            allow_unlabeled = len(tests) == 1
            point_inputs = _ade_point_artifacts(
                manifest,
                history=history,
                point=point_number,
                test=test,
                category="simulator_input",
                allow_unlabeled=allow_unlabeled,
                filename="input.scs",
            )
            if not point_inputs:
                raise RuntimeError(
                    f"ADE sweep point {point_number} test {test} had no "
                    "exact-history input.scs"
                )
            point_results = _ade_point_artifacts(
                manifest,
                history=history,
                point=point_number,
                test=test,
                category="eda_result",
                allow_unlabeled=allow_unlabeled,
            )
            if not point_results:
                raise RuntimeError(
                    f"ADE sweep point {point_number} test {test} had no non-empty "
                    "exact-history result"
                )
            test_bindings = [
                binding
                for binding in bindings
                if isinstance(binding, dict) and binding.get("test") == test
            ]
            sweep_binding_map = {
                (str(binding["instance"]), str(binding["oa_parameter"])): {
                    "variable": str(binding["variable"]),
                    "point_value": expected_values[str(binding["variable"])],
                }
                for binding in test_bindings
            }
            if not sweep_binding_map:
                raise RuntimeError(
                    f"ADE sweep point {point_number} test {test} has no OA bindings"
                )
            test_inputs: list[dict[str, Any]] = []
            for manifest_item in point_inputs:
                remote_path = _remote_manifest_item_path(manifest_item)
                input_text = _read_remote_text_via_skill(
                    client, remote_path, page_lines=32
                )
                digest = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
                if digest != manifest_item.get("sha256"):
                    raise RuntimeError(
                        "ADE sweep input.scs content hash changed after manifest "
                        f"capture at point {point_number} test {test}"
                    )
                comparison = _compare_ade_input_to_schematic(
                    _parse_ade_spectre_input(input_text),
                    schematics[test],
                    design=designs[test],
                    sweep_bindings=sweep_binding_map,
                )
                if comparison.get("effective_sweep_bindings_verified") is not True:
                    raise RuntimeError(
                        f"ADE sweep point {point_number} test {test} did not verify "
                        "effective variable bindings"
                    )
                item_evidence = {
                    "point": point_number,
                    "test": test,
                    "input_path": manifest_item["path"],
                    "input_remote_path": remote_path,
                    "input_sha256": digest,
                    **comparison,
                }
                input_evidence.append(item_evidence)
                test_inputs.append(
                    {
                        "path": manifest_item["path"],
                        "sha256": digest,
                        "comparison_sha256": comparison["comparison_sha256"],
                    }
                )
            result_artifacts = [
                {
                    "path": item["path"],
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                }
                for item in point_results
            ]
            point_tests.append(
                {
                    "test": test,
                    "inputs": test_inputs,
                    "result_artifacts": result_artifacts,
                }
            )
        point_payload = {
            "point": point_number,
            "expected_parameters": expected_values,
            "result_parameters": {
                str(name): str(value) for name, value in actual_parameters.items()
            },
            "scalar_outputs": scalar_outputs,
            "tests": point_tests,
        }
        if corner_mode:
            point_payload.update(
                maestro_point=expected.get("maestro_point"),
                corner=expected.get("corner"),
            )
        point_evidence.append(
            {
                **point_payload,
                "point_binding_sha256": hashlib.sha256(
                    json.dumps(
                        point_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )

    return {
        **evaluation_error_evidence,
        "simulator_input_consistency_verified": bool(input_evidence),
        "simulator_input_consistency": input_evidence,
        "simulator_input_consistency_evidence_sources": {
            "maestro_design_and_oa": "bridge_readback",
            "spectre_input": "eda_result",
            "comparison": "software_inference",
        },
        "sweep_point_consistency_verified": bool(point_evidence),
        "sweep_point_consistency": point_evidence,
        "effective_simulation_values_verified": bool(point_evidence),
        "exact_point_input_result_binding_verified": (
            bool(point_evidence) and not database_mode
        ),
        "native_sweep_database_binding_verified": (
            bool(point_evidence) and database_mode
        ),
        "sweep_point_evidence_mode": (
            "maestro_exact_history_rdb_with_shared_symbolic_runtime_input"
            if database_mode
            else "exact_point_artifacts"
        ),
        "sweep_history_log_evidence": history_log_evidence,
        "sweep_result_database_artifacts": database_result_artifacts,
        "corner_detail_csv_sha256": results.get("corner_detail_csv_sha256"),
        "corner_detail_csv_size_bytes": results.get(
            "corner_detail_csv_size_bytes"
        ),
        "corner_detail_csv_evidence_sources": results.get(
            "corner_detail_csv_evidence_sources"
        ),
        "sweep_consistency_evidence_sources": {
            "expected_sweep": "user_input",
            "maestro_setup_and_oa": "bridge_readback",
            "spectre_input_and_results": "eda_result",
            "comparison": "software_inference",
        },
    }


def _parse_inverter_netlist(
    text: str, profile: dict[str, Any]
) -> dict[str, Any]:
    records = _logical_netlist_records(text)
    instances: dict[str, dict[str, Any]] = {}
    expected = {
        "MN0": {
            "model": profile["nmos_cell"],
            "nodes": ["OUT", "IN", "VSS", "VSS"],
            "semantic": "nmos_width_um",
        },
        "MP0": {
            "model": profile["pmos_cell"],
            "nodes": ["OUT", "IN", "VDD", "VDD"],
            "semantic": "pmos_width_um",
        },
    }
    semantic_parameters: dict[str, float] = {}
    lengths: list[float] = []
    for name, expected_item in expected.items():
        record = next(
            (
                item
                for item in records
                if re.match(rf"^{re.escape(name)}\s*\(", item)
            ),
            None,
        )
        if record is None:
            raise RuntimeError(f"si netlist is missing inverter instance {name}")
        match = re.match(r"^\S+\s*\(([^)]*)\)\s+(\S+)\s+(.*)$", record)
        if match is None:
            raise RuntimeError(f"cannot parse si netlist instance {name}")
        nodes = match.group(1).split()
        model = match.group(2)
        parameter_text = match.group(3)
        if nodes != expected_item["nodes"]:
            raise RuntimeError(
                f"si netlist topology mismatch for {name}: nodes={nodes}, "
                f"expected={expected_item['nodes']}"
            )
        if model != expected_item["model"]:
            raise RuntimeError(
                f"si netlist model mismatch for {name}: {model!r} != "
                f"{expected_item['model']!r}"
            )
        width_match = re.search(r"(?:^|\s)w=([^\s\\]+)", parameter_text)
        length_match = re.search(r"(?:^|\s)l=([^\s\\]+)", parameter_text)
        if width_match is None or length_match is None:
            raise RuntimeError(f"si netlist is missing w/l for {name}")
        width_um = _length_um(width_match.group(1))
        length_um = _length_um(length_match.group(1))
        semantic_parameters[str(expected_item["semantic"])] = width_um
        lengths.append(length_um)
        instances[name] = {
            "nodes": nodes,
            "model": model,
            "width_um": width_um,
            "length_um": length_um,
        }
    if abs(lengths[0] - lengths[1]) > 1e-9:
        raise RuntimeError(
            f"si netlist NMOS/PMOS lengths differ: {lengths[0]}um vs {lengths[1]}um"
        )
    semantic_parameters["length_um"] = lengths[0]
    return {
        "instances": instances,
        "semantic_parameters": semantic_parameters,
    }


def _parse_si_instance_parameters(
    parameter_text: str, instance_name: str
) -> tuple[dict[str, str], list[str]]:
    parameters: dict[str, str] = {}
    unparsed_parameter_tokens: list[str] = []
    for token in parameter_text.split():
        parameter_match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_$]*)=([^\s\\]+)", token
        )
        if parameter_match is None:
            unparsed_parameter_tokens.append(token)
            continue
        parameter_name, parameter_value = parameter_match.groups()
        if parameter_name in parameters:
            raise RuntimeError(
                f"si netlist repeats parameter {parameter_name!r} for "
                f"{instance_name}"
            )
        parameters[parameter_name] = parameter_value
        if _ENGINEERING_VALUE.fullmatch(parameter_value) is None:
            unparsed_parameter_tokens.append(token)
    return parameters, unparsed_parameter_tokens


def _spectre_hierarchy_records(
    text: str,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    top_records: list[str] = []
    subcircuits: dict[str, dict[str, Any]] = {}
    active_name: str | None = None
    for record in _logical_netlist_records(text):
        start = re.match(
            r"^subckt\s+(\S+)\s*(?:\(([^)]*)\)|(.*))$",
            record,
            flags=re.IGNORECASE,
        )
        if start is not None:
            if active_name is not None:
                raise RuntimeError("nested si subcircuit definitions are unsupported")
            name = start.group(1)
            if name in subcircuits:
                raise RuntimeError(f"si netlist repeats subcircuit {name!r}")
            pin_text = start.group(2) if start.group(2) is not None else start.group(3)
            pins = [
                _normalized_net_name(value)
                for value in str(pin_text or "").split()
            ]
            if not pins or len(pins) != len(set(pins)):
                raise RuntimeError(
                    f"si subcircuit {name!r} has invalid or duplicate terminals"
                )
            subcircuits[name] = {"pins": pins, "records": []}
            active_name = name
            continue
        end = re.match(r"^ends(?:\s+(\S+))?$", record, flags=re.IGNORECASE)
        if end is not None:
            if active_name is None:
                raise RuntimeError("si netlist contains ends outside a subcircuit")
            if end.group(1) is not None and end.group(1) != active_name:
                raise RuntimeError(
                    f"si subcircuit end {end.group(1)!r} does not match "
                    f"{active_name!r}"
                )
            active_name = None
            continue
        if active_name is None:
            top_records.append(record)
        else:
            subcircuits[active_name]["records"].append(record)
    if active_name is not None:
        raise RuntimeError(f"si subcircuit {active_name!r} is missing ends")
    return top_records, subcircuits


def _parse_si_instance_records(
    records: list[str],
    *,
    scope: str,
) -> dict[str, dict[str, Any]]:
    parsed_instances: dict[str, dict[str, Any]] = {}
    for record in records:
        match = re.match(r"^(\S+)\s*\(([^)]*)\)\s+(\S+)(?:\s+(.*))?$", record)
        if match is None:
            continue
        name, nodes_text, model, parameter_text = match.groups()
        if name in parsed_instances:
            raise RuntimeError(f"si netlist repeats instance {scope}/{name}")
        parameters, _ = _parse_si_instance_parameters(parameter_text or "", name)
        parsed_instances[name] = {
            "nodes": [
                _normalized_net_name(value) for value in nodes_text.split()
            ],
            "model": model,
            "parameters": {
                key: value.strip('"') for key, value in sorted(parameters.items())
            },
        }
    return parsed_instances


def _verify_primitive_scope(
    oa_instances: dict[str, dict[str, Any]],
    parsed_instances: dict[str, dict[str, Any]],
    *,
    scope: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    omitted_ground_symbols = sorted(
        name
        for name, item in oa_instances.items()
        if str(item.get("cell") or "").lower() in {"gnd", "vss"}
    )
    expected_names = set(oa_instances) - set(omitted_ground_symbols)
    actual_names = set(parsed_instances)
    if expected_names != actual_names:
        raise RuntimeError(
            f"si netlist/OA instance set mismatch in {scope}: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    checks: list[dict[str, Any]] = []
    for name in sorted(expected_names):
        oa_instance = oa_instances[name]
        contract = _ade_instance_contract(oa_instance)
        if contract is None:
            raise RuntimeError(
                "generic OA hierarchy supports only primitive instances inside "
                f"bound child schematics; unsupported {scope}/{name} "
                f"({oa_instance.get('lib')}/{oa_instance.get('cell')})"
            )
        parsed = parsed_instances[name]
        if parsed["model"] != contract["model"]:
            raise RuntimeError(
                f"si netlist model mismatch for {scope}/{name}: "
                f"{parsed['model']!r} != {contract['model']!r}"
            )
        terminals = oa_instance.get("terms") or {}
        expected_nodes = [
            _normalized_net_name(terminals[terminal])
            for terminal in contract["terminal_order"]
        ]
        if parsed["nodes"] != expected_nodes:
            raise RuntimeError(
                f"si netlist node mismatch for {scope}/{name}: "
                f"{parsed['nodes']!r} != {expected_nodes!r}"
            )
        checks.append(
            {
                "instance": name,
                "model": parsed["model"],
                "nodes": parsed["nodes"],
            }
        )
    return checks, omitted_ground_symbols


def _parse_existing_schematic_netlist(
    text: str,
    schematic: dict[str, Any],
    settings: GenericOaSimulationSpec | None,
    hierarchy_schematics: dict[tuple[str, str], dict[str, Any]] | None = None,
    hierarchy_summaries: dict[tuple[str, str], dict[str, Any]] | None = None,
    *,
    hierarchy_bindings: list[GenericHierarchyBinding] | None = None,
    parameter_bindings: list[GenericNetlistParameterBinding] | None = None,
    include_parameter_inventory: bool = False,
) -> dict[str, Any]:
    """Prove a primitive or explicitly bound one-level OA hierarchy matches si."""

    top_records, subcircuits = _spectre_hierarchy_records(text)
    parsed_instances = _parse_si_instance_records(top_records, scope="top")

    oa_instances = {
        str(item.get("name")): item for item in schematic.get("instances", [])
    }
    omitted_ground_symbols = sorted(
        name
        for name, item in oa_instances.items()
        if str(item.get("cell") or "").lower() in {"gnd", "vss"}
    )
    expected_names = set(oa_instances) - set(omitted_ground_symbols)
    actual_names = set(parsed_instances)
    if expected_names != actual_names:
        raise RuntimeError(
            "si netlist/OA top-level instance set mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    resolved_hierarchy_bindings = (
        list(settings.hierarchy_bindings)
        if settings is not None
        else list(hierarchy_bindings or [])
    )
    resolved_parameter_bindings = (
        list(settings.netlist_parameter_bindings)
        if settings is not None
        else list(parameter_bindings or [])
    )
    hierarchy_by_instance = {
        binding.instance: binding for binding in resolved_hierarchy_bindings
    }
    unknown_hierarchy_instances = sorted(
        set(hierarchy_by_instance) - expected_names
    )
    if unknown_hierarchy_instances:
        raise RuntimeError(
            "generic hierarchy bindings reference missing top-level instances: "
            + ", ".join(unknown_hierarchy_instances)
        )

    if settings is not None:
        wrapper_names = {item.name for item in (*settings.sources, *settings.loads)}
        collisions = sorted(wrapper_names & expected_names)
        if collisions:
            raise RuntimeError(
                "generic testbench element names collide with OA instances: "
                + ", ".join(collisions)
            )

        oa_nets = {
            _normalized_net_name(name)
            for name in (schematic.get("nets") or {}).keys()
        }
        unknown_nodes = sorted(settings.referenced_nodes() - oa_nets - {"0"})
        if unknown_nodes:
            raise RuntimeError(
                "generic testbench references nodes absent from OA schematic: "
                + ", ".join(unknown_nodes)
            )

    topology_checks: list[dict[str, Any]] = []
    hierarchy_checks: list[dict[str, Any]] = []
    scoped_oa_instances: dict[str, dict[str, Any]] = {}
    scoped_parsed_instances: dict[str, dict[str, Any]] = {}
    for name in sorted(expected_names):
        oa_instance = oa_instances[name]
        hierarchy_binding = hierarchy_by_instance.get(name)
        if hierarchy_binding is not None:
            if (
                oa_instance.get("lib") != hierarchy_binding.library
                or oa_instance.get("cell") != hierarchy_binding.cell
            ):
                raise RuntimeError(
                    f"hierarchy binding master mismatch for {name}: OA="
                    f"{oa_instance.get('lib')}/{oa_instance.get('cell')}, declared="
                    f"{hierarchy_binding.library}/{hierarchy_binding.cell}"
                )
            parsed = parsed_instances[name]
            if parsed["model"] != hierarchy_binding.subcircuit:
                raise RuntimeError(
                    f"si subcircuit call mismatch for {name}: "
                    f"{parsed['model']!r} != {hierarchy_binding.subcircuit!r}"
                )
            terminals = oa_instance.get("terms") or {}
            missing_terminals = sorted(
                set(hierarchy_binding.terminal_order) - set(terminals)
            )
            if missing_terminals:
                raise RuntimeError(
                    f"hierarchy binding {name} references missing OA terminals: "
                    + ", ".join(missing_terminals)
                )
            expected_nodes = [
                _normalized_net_name(terminals[terminal])
                for terminal in hierarchy_binding.terminal_order
            ]
            if parsed["nodes"] != expected_nodes:
                raise RuntimeError(
                    f"si subcircuit node mismatch for {name}: "
                    f"{parsed['nodes']!r} != {expected_nodes!r}"
                )
            subcircuit = subcircuits.get(hierarchy_binding.subcircuit)
            if subcircuit is None:
                raise RuntimeError(
                    f"si netlist is missing subcircuit definition "
                    f"{hierarchy_binding.subcircuit!r} for {name}"
                )
            declared_pins = [
                _normalized_net_name(value)
                for value in hierarchy_binding.terminal_order
            ]
            if subcircuit["pins"] != declared_pins:
                raise RuntimeError(
                    f"si subcircuit terminal order mismatch for {name}: "
                    f"{subcircuit['pins']!r} != {declared_pins!r}"
                )
            child_key = (hierarchy_binding.library, hierarchy_binding.cell)
            child = (hierarchy_schematics or {}).get(child_key)
            if child is None:
                raise RuntimeError(
                    f"hierarchy child schematic readback is missing for "
                    f"{hierarchy_binding.library}/{hierarchy_binding.cell}"
                )
            child_pins = {
                _normalized_net_name(value)
                for value in (child.get("pins") or {}).keys()
            }
            if child_pins != set(declared_pins):
                raise RuntimeError(
                    f"hierarchy child OA pin set mismatch for {name}: "
                    f"OA={sorted(child_pins)}, declared={sorted(declared_pins)}"
                )
            child_instances = {
                str(item.get("name")): item
                for item in child.get("instances", [])
            }
            child_parsed = _parse_si_instance_records(
                subcircuit["records"],
                scope=hierarchy_binding.subcircuit,
            )
            child_checks, child_grounds = _verify_primitive_scope(
                child_instances,
                child_parsed,
                scope=hierarchy_binding.subcircuit,
            )
            for child_instance_name in sorted(set(child_instances) & set(child_parsed)):
                scoped_name = f"{name}/{child_instance_name}"
                if scoped_name in scoped_oa_instances:
                    raise RuntimeError(
                        f"duplicate scoped si instance identity {scoped_name!r}"
                    )
                scoped_oa_instances[scoped_name] = child_instances[
                    child_instance_name
                ]
                scoped_parsed_instances[scoped_name] = child_parsed[
                    child_instance_name
                ]
            child_summary = (hierarchy_summaries or {}).get(child_key)
            if child_summary is None:
                child_summary = _existing_schematic_summary(child)
            child_placement_sha256 = (
                child_summary.get("placement", {}).get("sha256")
                if isinstance(child_summary.get("placement"), dict)
                else None
            )
            hierarchy_checks.append(
                {
                    "instance": name,
                    "master": {
                        "library": hierarchy_binding.library,
                        "cell": hierarchy_binding.cell,
                        "view": hierarchy_binding.view,
                    },
                    "subcircuit": hierarchy_binding.subcircuit,
                    "terminal_order": list(hierarchy_binding.terminal_order),
                    "nodes": parsed["nodes"],
                    "child_topology_sha256": topology_fingerprint(
                        snapshot_from_inspection(child_summary)
                    ),
                    "child_topology_source": "bridge_readback",
                    "child_placement_sha256": child_placement_sha256,
                    "child_placement_source": (
                        "bridge_readback"
                        if child_placement_sha256 is not None
                        else None
                    ),
                    "subcircuit_source": "eda_result",
                    "consistency_source": "software_inference",
                    "child_instances": child_checks,
                    "child_omitted_ground_symbols": child_grounds,
                }
            )
            topology_checks.append(
                {
                    "instance": name,
                    "model": parsed["model"],
                    "nodes": parsed["nodes"],
                    "kind": "subcircuit",
                }
            )
            continue
        contract = _ade_instance_contract(oa_instance)
        if contract is None:
            raise RuntimeError(
                "generic OA simulation has no primitive contract or explicit "
                "hierarchy binding for "
                f"{name} ({oa_instance.get('lib')}/{oa_instance.get('cell')})"
            )
        parsed = parsed_instances[name]
        if parsed["model"] != contract["model"]:
            raise RuntimeError(
                f"si netlist model mismatch for {name}: "
                f"{parsed['model']!r} != {contract['model']!r}"
            )
        terminals = oa_instance.get("terms") or {}
        expected_nodes = [
            _normalized_net_name(terminals[terminal])
            for terminal in contract["terminal_order"]
        ]
        if parsed["nodes"] != expected_nodes:
            raise RuntimeError(
                f"si netlist node mismatch for {name}: "
                f"{parsed['nodes']!r} != {expected_nodes!r}"
            )
        topology_checks.append(
            {
                "instance": name,
                "model": parsed["model"],
                "nodes": parsed["nodes"],
            }
        )

    parameter_checks: list[dict[str, str]] = []
    derived_callback_checks: list[dict[str, str]] = []
    for binding in resolved_parameter_bindings:
        scoped = split_instance_path(binding.instance)[0] is not None
        oa_instance = (
            scoped_oa_instances.get(binding.instance)
            if scoped
            else oa_instances.get(binding.instance)
        )
        parsed_instance = (
            scoped_parsed_instances.get(binding.instance)
            if scoped
            else parsed_instances.get(binding.instance)
        )
        if oa_instance is None or parsed_instance is None:
            raise RuntimeError(
                "generic netlist parameter binding references missing instance "
                f"{binding.instance!r}"
            )
        oa_parameters = oa_instance.get("params") or {}
        netlist_parameters = parsed_instance["parameters"]
        if binding.oa_parameter not in oa_parameters:
            raise RuntimeError(
                "OA readback is missing bound parameter "
                f"{binding.instance}.{binding.oa_parameter}"
            )
        if binding.netlist_parameter not in netlist_parameters:
            raise RuntimeError(
                "si netlist is missing bound parameter "
                f"{binding.instance}.{binding.netlist_parameter}"
            )
        oa_value = str(oa_parameters[binding.oa_parameter])
        netlist_value = str(netlist_parameters[binding.netlist_parameter])
        if not spectre_values_equal(oa_value, netlist_value):
            raise RuntimeError(
                "OA/si parameter mismatch for "
                f"{binding.instance}.{binding.oa_parameter}->"
                f"{binding.netlist_parameter}: {oa_value!r} != {netlist_value!r}"
            )
        parameter_checks.append(
            {
                "instance": binding.instance,
                "oa_parameter": binding.oa_parameter,
                "netlist_parameter": binding.netlist_parameter,
                "oa_value": oa_value,
                "netlist_value": netlist_value,
            }
        )
        for callback in binding.derived_callbacks or []:
            if callback.oa_parameter not in oa_parameters:
                raise RuntimeError(
                    "OA readback is missing derived callback parameter "
                    f"{binding.instance}.{callback.oa_parameter}"
                )
            if callback.netlist_parameter not in netlist_parameters:
                raise RuntimeError(
                    "si netlist is missing derived callback parameter "
                    f"{binding.instance}.{callback.netlist_parameter}"
                )
            callback_oa_value = str(oa_parameters[callback.oa_parameter])
            callback_netlist_value = str(
                netlist_parameters[callback.netlist_parameter]
            )
            if not spectre_values_equal(
                callback_oa_value,
                callback_netlist_value,
            ):
                raise RuntimeError(
                    "OA/si derived callback mismatch for "
                    f"{binding.instance}.{callback.oa_parameter}->"
                    f"{callback.netlist_parameter}: {callback_oa_value!r} != "
                    f"{callback_netlist_value!r}"
                )
            derived_callback_checks.append(
                {
                    "instance": binding.instance,
                    "primary_oa_parameter": binding.oa_parameter,
                    "primary_netlist_parameter": binding.netlist_parameter,
                    "oa_parameter": callback.oa_parameter,
                    "netlist_parameter": callback.netlist_parameter,
                    "oa_value": callback_oa_value,
                    "netlist_value": callback_netlist_value,
                }
            )

    if settings is not None:
        missing_op_instances = sorted(
            {item.instance for item in settings.operating_point_metrics}
            - expected_names
        )
        if missing_op_instances:
            raise RuntimeError(
                "generic operating-point metrics reference missing instances: "
                + ", ".join(missing_op_instances)
            )
    result = {
        "instances": topology_checks,
        "omitted_ground_symbols": omitted_ground_symbols,
        "topology_consistency": "matched",
        "parameter_bindings": parameter_checks,
        "parameter_consistency": "matched",
        "flat_primitive_scope": not bool(hierarchy_checks),
        "hierarchy_scope": (
            "explicit_one_level_primitive_children"
            if hierarchy_checks
            else "flat_primitive"
        ),
        "hierarchy_bindings": hierarchy_checks,
    }
    if derived_callback_checks:
        result["derived_callback_bindings"] = derived_callback_checks
        result["derived_callback_consistency"] = "matched"
    if include_parameter_inventory:
        parameter_inventory = {
            name: {
                "model": parsed_instances[name]["model"],
                "nodes": list(parsed_instances[name]["nodes"]),
                "parameters": dict(parsed_instances[name]["parameters"]),
            }
            for name in sorted(parsed_instances)
        }
        for scoped_name in sorted(scoped_parsed_instances):
            parsed_instance = scoped_parsed_instances[scoped_name]
            parameter_inventory[scoped_name] = {
                "model": parsed_instance["model"],
                "nodes": list(parsed_instance["nodes"]),
                "parameters": dict(parsed_instance["parameters"]),
            }
        signature_payload = {
            name: parameter_inventory[name]
            for name in sorted(parameter_inventory)
        }
        result["parameter_inventory"] = parameter_inventory
        result["canonical_signature_sha256"] = hashlib.sha256(
            json.dumps(
                signature_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    return result


def _parse_common_source_netlist(
    text: str, profile: dict[str, Any]
) -> dict[str, Any]:
    records = _logical_netlist_records(text)
    has_source_resistor = any(
        re.match(r"^RS0\s*\(", item) is not None for item in records
    )
    has_cascode = any(
        re.match(r"^MNCAS\s*\(", item) is not None for item in records
    )
    if has_source_resistor and has_cascode:
        raise RuntimeError(
            "si netlist common-source parser does not combine source degeneration "
            "and cascode devices in one topology Gate"
        )
    expected = {
        "MN0": {
            "model": profile["nmos_cell"],
            "nodes": [
                "NCAS" if has_cascode else "OUT",
                "IN",
                "NSRC" if has_source_resistor else "VSS",
                "VSS",
            ],
        },
        "RD0": {
            "model": "resistor",
            "nodes": ["VDD", "OUT"],
        },
    }
    if has_source_resistor:
        expected["RS0"] = {
            "model": "resistor",
            "nodes": ["NSRC", "VSS"],
        }
    elif has_cascode:
        expected["MNCAS"] = {
            "model": profile["nmos_cell"],
            "nodes": ["OUT", "VCAS", "NCAS", "VSS"],
        }
    instances: dict[str, dict[str, Any]] = {}
    for name, expected_item in expected.items():
        record = next(
            (
                item
                for item in records
                if re.match(rf"^{re.escape(name)}\s*\(", item)
            ),
            None,
        )
        if record is None:
            raise RuntimeError(f"si netlist is missing common-source instance {name}")
        match = re.match(r"^\S+\s*\(([^)]*)\)\s+(\S+)\s+(.*)$", record)
        if match is None:
            raise RuntimeError(f"cannot parse si netlist instance {name}")
        nodes = match.group(1).split()
        model = match.group(2)
        parameter_text = match.group(3)
        parameters, unparsed_parameter_tokens = _parse_si_instance_parameters(
            parameter_text, name
        )
        if nodes != expected_item["nodes"]:
            raise RuntimeError(
                f"si netlist topology mismatch for {name}: nodes={nodes}, "
                f"expected={expected_item['nodes']}"
            )
        if model != expected_item["model"]:
            raise RuntimeError(
                f"si netlist model mismatch for {name}: {model!r} != "
                f"{expected_item['model']!r}"
            )
        instances[name] = {"nodes": nodes, "model": model}
        if name in {"MN0", "MNCAS"}:
            if "w" not in parameters or "l" not in parameters:
                raise RuntimeError(f"si netlist is missing w/l for {name}")
            fingers = _positive_device_count(
                parameters.get("nf", 1),
                f"si {name}.nf",
            )
            if "multi" in parameters and "m" in parameters:
                raise RuntimeError(
                    f"si netlist {name} declares both multi and m"
                )
            multiplicity = _positive_device_count(
                parameters.get("multi", parameters.get("m", 1)),
                f"si {name}.multi",
            )
            netlist_width_um = _length_um(parameters["w"])
            controlled = {"w", "l", "nf", "m", "multi"}
            instances[name].update(
                {
                    "netlist_width_um": netlist_width_um,
                    "finger_width_um": netlist_width_um / fingers,
                    "fingers": fingers,
                    "multiplicity": multiplicity,
                    "total_width_um": netlist_width_um * multiplicity,
                    "length_um": _length_um(parameters["l"]),
                    "model_parameters": {
                        parameter_name: parameter_value
                        for parameter_name, parameter_value in sorted(
                            parameters.items()
                        )
                        if parameter_name.lower() not in controlled
                        and _ENGINEERING_VALUE.fullmatch(parameter_value) is not None
                    },
                    "unparsed_model_parameter_tokens": unparsed_parameter_tokens,
                }
            )
        else:
            if "r" not in parameters:
                raise RuntimeError(f"si netlist is missing resistance for {name}")
            instances[name]["resistance_ohm"] = _resistance_ohm(
                parameters["r"]
            )
    semantic_parameters = {
        "device_width_um": instances["MN0"]["finger_width_um"],
        "length_um": instances["MN0"]["length_um"],
        "load_resistance_ohm": instances["RD0"]["resistance_ohm"],
    }
    if has_source_resistor:
        semantic_parameters["source_resistance_ohm"] = instances["RS0"][
            "resistance_ohm"
        ]
    if has_cascode:
        semantic_parameters.update(
            {
                "cascode_width_um": instances["MNCAS"]["finger_width_um"],
                "cascode_length_um": instances["MNCAS"]["length_um"],
            }
        )
    device_geometry = {
        name: instances["MN0"][name]
        for name in (
            "finger_width_um",
            "fingers",
            "multiplicity",
            "total_width_um",
        )
    }
    if has_cascode:
        device_geometry.update(
            {
                f"cascode_{name}": instances["MNCAS"][name]
                for name in (
                    "finger_width_um",
                    "fingers",
                    "multiplicity",
                    "total_width_um",
                )
            }
        )
    return {
        "instances": instances,
        "semantic_parameters": semantic_parameters,
        "device_geometry": device_geometry,
        "topology_variant": (
            _COMMON_SOURCE_CASCODE_VARIANT
            if has_cascode
            else (
                "source_degenerated_common_source"
                if has_source_resistor
                else "common_source"
            )
        ),
    }


def _parse_differential_pair_netlist(
    text: str, profile: dict[str, Any]
) -> dict[str, Any]:
    records = _logical_netlist_records(text)
    has_tail_device = any(re.match(r"^MNTAIL\s*\(", item) for item in records)
    has_rd0 = any(re.match(r"^RD0\s*\(", item) for item in records)
    has_rd1 = any(re.match(r"^RD1\s*\(", item) for item in records)
    has_pm0 = any(re.match(r"^MP0\s*\(", item) for item in records)
    has_pm1 = any(re.match(r"^MP1\s*\(", item) for item in records)
    if has_rd0 != has_rd1:
        raise RuntimeError(
            "si netlist differential resistive load requires both RD0 and RD1"
        )
    if has_pm0 != has_pm1:
        raise RuntimeError(
            "si netlist differential current mirror requires both MP0 and MP1"
        )
    has_resistive_load = has_rd0 and has_rd1
    has_current_mirror_load = has_pm0 and has_pm1
    if has_resistive_load == has_current_mirror_load:
        raise RuntimeError(
            "si netlist differential pair must contain exactly one complete load pair"
        )
    if has_current_mirror_load and not has_tail_device:
        raise RuntimeError(
            "si netlist current-mirror-load differential pair is missing MNTAIL"
        )
    has_rs0 = any(re.match(r"^RS0\s*\(", item) for item in records)
    has_rs1 = any(re.match(r"^RS1\s*\(", item) for item in records)
    if has_rs0 != has_rs1:
        raise RuntimeError(
            "si netlist differential source degeneration requires both RS0 and RS1"
        )
    has_source_degeneration = has_rs0 and has_rs1
    if has_source_degeneration and not has_tail_device:
        raise RuntimeError(
            "si netlist source-degenerated differential pair is missing MNTAIL"
        )
    source_p = "NSP" if has_source_degeneration else "TAIL"
    source_n = "NSN" if has_source_degeneration else "TAIL"
    expected = {
        "MN0": {
            "model": profile["nmos_cell"],
            "nodes": ["OUTP", "INP", source_p, "VSS"],
        },
        "MN1": {
            "model": profile["nmos_cell"],
            "nodes": ["OUTN", "INN", source_n, "VSS"],
        },
    }
    if has_current_mirror_load:
        expected.update(
            {
                "MP0": {
                    "model": profile["pmos_cell"],
                    "nodes": ["OUTP", "OUTP", "VDD", "VDD"],
                },
                "MP1": {
                    "model": profile["pmos_cell"],
                    "nodes": ["OUTN", "OUTP", "VDD", "VDD"],
                },
            }
        )
    else:
        expected.update(
            {
                "RD0": {"model": "resistor", "nodes": ["VDD", "OUTP"]},
                "RD1": {"model": "resistor", "nodes": ["VDD", "OUTN"]},
            }
        )
    if has_tail_device:
        expected["MNTAIL"] = {
            "model": profile["nmos_cell"],
            "nodes": ["TAIL", "BIAS", "VSS", "VSS"],
        }
    if has_source_degeneration:
        expected.update(
            {
                "RS0": {"model": "resistor", "nodes": ["NSP", "TAIL"]},
                "RS1": {"model": "resistor", "nodes": ["NSN", "TAIL"]},
            }
        )
    instances: dict[str, dict[str, Any]] = {}
    for name, expected_item in expected.items():
        record = next(
            (
                item
                for item in records
                if re.match(rf"^{re.escape(name)}\s*\(", item)
            ),
            None,
        )
        if record is None:
            raise RuntimeError(
                f"si netlist is missing differential-pair instance {name}"
            )
        match = re.match(r"^\S+\s*\(([^)]*)\)\s+(\S+)\s+(.*)$", record)
        if match is None:
            raise RuntimeError(f"cannot parse si netlist instance {name}")
        nodes = match.group(1).split()
        model = match.group(2)
        parameter_text = match.group(3)
        parameters, unparsed_parameter_tokens = _parse_si_instance_parameters(
            parameter_text, name
        )
        if nodes != expected_item["nodes"]:
            raise RuntimeError(
                f"si netlist topology mismatch for {name}: nodes={nodes}, "
                f"expected={expected_item['nodes']}"
            )
        if model != expected_item["model"]:
            raise RuntimeError(
                f"si netlist model mismatch for {name}: {model!r} != "
                f"{expected_item['model']!r}"
            )
        instances[name] = {"nodes": nodes, "model": model}
        if name in {"MN0", "MN1", "MNTAIL", "MP0", "MP1"}:
            if "w" not in parameters or "l" not in parameters:
                raise RuntimeError(f"si netlist is missing w/l for {name}")
            fingers = _positive_device_count(
                parameters.get("nf", 1),
                f"si {name}.nf",
            )
            if "multi" in parameters and "m" in parameters:
                raise RuntimeError(f"si netlist {name} declares both multi and m")
            multiplicity = _positive_device_count(
                parameters.get("multi", parameters.get("m", 1)),
                f"si {name}.multi",
            )
            netlist_width_um = _length_um(parameters["w"])
            controlled = {"w", "l", "nf", "m", "multi"}
            instances[name].update(
                {
                    "netlist_width_um": netlist_width_um,
                    "finger_width_um": netlist_width_um / fingers,
                    "fingers": fingers,
                    "multiplicity": multiplicity,
                    "total_width_um": netlist_width_um * multiplicity,
                    "length_um": _length_um(parameters["l"]),
                    "model_parameters": {
                        parameter_name: parameter_value
                        for parameter_name, parameter_value in sorted(
                            parameters.items()
                        )
                        if parameter_name.lower() not in controlled
                        and _ENGINEERING_VALUE.fullmatch(parameter_value) is not None
                    },
                    "unparsed_model_parameter_tokens": unparsed_parameter_tokens,
                }
            )
        else:
            if "r" not in parameters:
                raise RuntimeError(f"si netlist is missing resistance for {name}")
            instances[name]["resistance_ohm"] = _resistance_ohm(
                parameters["r"]
            )

    mos_geometry_fields = (
        "finger_width_um",
        "fingers",
        "multiplicity",
        "total_width_um",
        "length_um",
    )
    _assert_parameter_consistency(
        {name: float(instances["MN0"][name]) for name in mos_geometry_fields},
        {name: float(instances["MN1"][name]) for name in mos_geometry_fields},
        expected_label="MN0 si geometry",
        actual_label="MN1 si geometry",
    )
    semantic_parameters = {
        "input_width_um": instances["MN0"]["finger_width_um"],
        "length_um": instances["MN0"]["length_um"],
    }
    if has_resistive_load:
        _assert_parameter_consistency(
            {"load_resistance_ohm": float(instances["RD0"]["resistance_ohm"])},
            {"load_resistance_ohm": float(instances["RD1"]["resistance_ohm"])},
            expected_label="RD0 si resistance",
            actual_label="RD1 si resistance",
        )
        semantic_parameters["load_resistance_ohm"] = instances["RD0"][
            "resistance_ohm"
        ]
    else:
        _assert_parameter_consistency(
            {name: float(instances["MP0"][name]) for name in mos_geometry_fields},
            {name: float(instances["MP1"][name]) for name in mos_geometry_fields},
            expected_label="MP0 si geometry",
            actual_label="MP1 si geometry",
        )
        semantic_parameters.update(
            {
                "pmos_load_width_um": instances["MP0"]["finger_width_um"],
                "pmos_load_length_um": instances["MP0"]["length_um"],
            }
        )
    if has_tail_device:
        semantic_parameters.update(
            {
                "tail_width_um": instances["MNTAIL"]["finger_width_um"],
                "tail_length_um": instances["MNTAIL"]["length_um"],
            }
        )
    if has_source_degeneration:
        _assert_parameter_consistency(
            {
                "source_resistance_ohm": float(
                    instances["RS0"]["resistance_ohm"]
                )
            },
            {
                "source_resistance_ohm": float(
                    instances["RS1"]["resistance_ohm"]
                )
            },
            expected_label="RS0 si resistance",
            actual_label="RS1 si resistance",
        )
        semantic_parameters["source_resistance_ohm"] = instances["RS0"][
            "resistance_ohm"
        ]
    parsed = {
        "instances": instances,
        "semantic_parameters": semantic_parameters,
        "device_geometry": {
            name: instances["MN0"][name]
            for name in (
                "finger_width_um",
                "fingers",
                "multiplicity",
                "total_width_um",
            )
        },
        "topology_variant": (
            _DIFFERENTIAL_PAIR_CURRENT_MIRROR_DEGENERATED_VARIANT
            if has_current_mirror_load and has_source_degeneration
            else _DIFFERENTIAL_PAIR_CURRENT_MIRROR_LOAD_VARIANT
            if has_current_mirror_load
            else _DIFFERENTIAL_PAIR_DEGENERATED_TAIL_VARIANT
            if has_source_degeneration
            else _DIFFERENTIAL_PAIR_TAIL_VARIANT
            if has_tail_device
            else _DIFFERENTIAL_PAIR_BASE_VARIANT
        ),
    }
    if has_tail_device:
        parsed["tail_device_geometry"] = {
            name: instances["MNTAIL"][name]
            for name in (
                "finger_width_um",
                "fingers",
                "multiplicity",
                "total_width_um",
            )
        }
    if has_current_mirror_load:
        parsed["current_mirror_load_geometry"] = {
            name: instances["MP0"][name]
            for name in (
                "finger_width_um",
                "fingers",
                "multiplicity",
                "total_width_um",
            )
        }
    return parsed


def _validate_si_log(text: str) -> None:
    failure = re.search(
        r"(?im)(\*Error\*|ERROR\s*\(|Stopping netlisting|netlisting failed)", text
    )
    if failure is not None:
        raise RuntimeError(f"si netlisting failed: {failure.group(1)}")
    if "End netlisting" not in text:
        raise RuntimeError("si netlisting log has no completion marker")


def _bridge_result_error(result: Any) -> str:
    errors = getattr(result, "errors", None) or []
    if errors:
        return str(errors[0])
    return str(getattr(result, "status", "unknown failure"))


def _require_bridge_result(result: Any, action: str) -> None:
    if not bool(getattr(result, "ok", False)):
        raise RuntimeError(f"{action} failed: {_bridge_result_error(result)}")


def _require_transport_result(result: Any, action: str) -> None:
    if hasattr(result, "returncode"):
        returncode = int(getattr(result, "returncode", -1))
        if returncode != 0:
            detail = str(getattr(result, "stderr", "")).strip()
            raise RuntimeError(
                f"{action} failed (rc={returncode}): {detail or 'no stderr'}"
            )
        return
    _require_bridge_result(result, action)


def _spectre_failure_detail(result: Any, work_dir: Path) -> str:
    errors = [str(item) for item in (getattr(result, "errors", None) or [])]
    if errors:
        detail = "; ".join(errors[:20])
    else:
        status = getattr(result, "status", "unknown failure")
        detail = str(getattr(status, "value", status))

    log_path = work_dir / "spectre.out"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return detail

    error_indexes = [
        index
        for index, line in enumerate(lines)
        if "error" in line.lower() or "fatal" in line.lower()
    ]
    selected_indexes: set[int] = set()
    for index in error_indexes:
        selected_indexes.update(range(max(0, index - 2), min(len(lines), index + 4)))
    if selected_indexes:
        excerpt_lines = [lines[index] for index in sorted(selected_indexes)]
    else:
        excerpt_lines = lines[-30:]
    excerpt = "\n".join(excerpt_lines).strip()
    if not excerpt:
        return detail
    if len(excerpt) > 6000:
        excerpt = excerpt[-6000:]
    return f"{detail}; spectre.out excerpt:\n{excerpt}"


def _download_text(
    client, remote_path: str, local_path: Path, label: str, *, timeout: int
) -> str:
    result = client.download_file(remote_path, local_path, timeout=timeout)
    _require_bridge_result(result, f"download {label}")
    return _read_nonempty_text(local_path, label)


def _upload_file(client, local_path: Path, remote_path: str, *, timeout: int) -> None:
    result = client.upload_file(local_path, remote_path, timeout=timeout)
    _require_bridge_result(result, f"upload {local_path.name}")


def _install_remote_spectre_guard(
    client: Any,
    work_dir: Path,
    remote_run_dir: str,
    *,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    """Install a bounded remote Spectre launcher without changing Bridge."""

    runner = getattr(client, "ssh_runner", None)
    if runner is None:
        return "spectre", {
            "source": "software_inference",
            "status": "not_applicable_local_runner",
            "bounded_remote_process": False,
        }
    remote_run_dir = str(remote_run_dir).rstrip("/")
    if not remote_run_dir.startswith("/data/xum/"):
        raise RuntimeError("remote Spectre guard must stay under /data/xum")
    remote_path = f"{remote_run_dir}/vda_spectre_guard.sh"
    script = (
        "#!/bin/sh\n"
        "exec timeout --signal=TERM "
        f"--kill-after={_SPECTRE_REMOTE_KILL_AFTER_SECONDS}s "
        f"{int(timeout)}s spectre \"$@\"\n"
    )
    local_path = work_dir / "vda_spectre_guard.sh"
    local_path.write_text(script, encoding="utf-8", newline="\n")
    upload_timeout = min(max(int(timeout), 1), 60)
    if hasattr(client, "upload_file"):
        upload_result = client.upload_file(
            local_path,
            remote_path,
            timeout=upload_timeout,
        )
    else:
        upload_result = runner.upload(
            local_path,
            remote_path,
            timeout=upload_timeout,
        )
    _require_transport_result(upload_result, "upload remote Spectre guard")
    remote_q = shlex.quote(remote_path)
    verify_result = runner.run_command(
        f"command -v timeout >/dev/null && chmod 700 {remote_q} "
        f"&& sha256sum {remote_q}",
        timeout=upload_timeout,
    )
    _require_transport_result(verify_result, "verify remote Spectre guard")
    expected_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
    returned_sha256 = str(getattr(verify_result, "stdout", "")).split(maxsplit=1)[0]
    if returned_sha256 != expected_sha256:
        raise RuntimeError(
            "remote Spectre guard SHA-256 mismatch: "
            f"expected {expected_sha256}, got {returned_sha256 or '<empty>'}"
        )
    return remote_path, {
        "source": "bridge_readback",
        "configuration_source": "software_inference",
        "status": "installed_and_hash_matched",
        "bounded_remote_process": True,
        "remote_path": remote_path,
        "sha256": expected_sha256,
        "simulation_timeout_seconds": int(timeout),
        "kill_after_seconds": _SPECTRE_REMOTE_KILL_AFTER_SECONDS,
        "bridge_wait_timeout_seconds": (
            int(timeout) + _SPECTRE_REMOTE_CLEANUP_GRACE_SECONDS
        ),
    }


def _create_spectre_simulator(
    simulator_type: Any,
    client: Any,
    *,
    spectre_cmd: str,
    timeout: int,
    work_dir: Path,
    remote_run_dir: str,
    keep_remote_files: bool,
) -> Any:
    runner = getattr(client, "ssh_runner", None)
    common = {
        "spectre_cmd": spectre_cmd,
        "timeout": timeout,
        "work_dir": work_dir,
        "output_format": "psfascii",
        "keep_remote_files": keep_remote_files,
    }
    if runner is None:
        return simulator_type.from_env(**common)
    common["timeout"] = timeout + _SPECTRE_REMOTE_CLEANUP_GRACE_SECONDS
    runner._persistent_shell_enabled = False
    return simulator_type(
        **common,
        remote=True,
        ssh_runner=runner,
        remote_work_dir=remote_run_dir,
    )


def _si_init_environment_skill(
    run_dir: str,
    library: str,
    cell: str,
    view: str,
) -> str:
    """Build a scoped si initializer without an NFS-held foreground log."""

    init = "simInitEnvWithArgs({} {} {} {} \"spectre\" nil)".format(
        json.dumps(run_dir),
        json.dumps(library),
        json.dumps(cell),
        json.dumps(view),
    )
    # simInitEnvWithArgs keeps si.foregnd.log open in the long-lived
    # Virtuoso process.  Deleting an otherwise-complete NFS run directory
    # then leaves a .nfs tombstone until another initialization replaces the
    # handle.  The batch si stdout log is captured independently, so bind the
    # foreground log to /dev/null only for this call.  SKILL's dynamic let
    # scope restores the user's global value as soon as initialization exits.
    return (
        'let((simForeGndLogFile) simForeGndLogFile="/dev/null" '
        f"{init})"
    )


def _generate_oa_netlist(
    client,
    payload: dict[str, Any],
    work_dir: Path,
    *,
    timeout: int,
    schematic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    library, cell = _target(payload)
    profile = payload["profile"]
    run_root = str(profile["remote_run_root"]).rstrip("/")
    if not run_root.startswith("/data/xum/"):
        raise RuntimeError("remote netlist run root must stay under /data/xum")
    task_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("task_id", "task")))
    run_dir = f"{run_root}/vda_{task_slug}_{uuid.uuid4().hex[:12]}"

    init_skill = _si_init_environment_skill(
        run_dir,
        library,
        cell,
        str(payload["target"].get("view", "schematic")),
    )
    init_result = client.execute_skill(init_skill, timeout=min(timeout, 90))
    _require_bridge_result(init_result, "initialize si environment")

    local_si_env = work_dir / "si.env"
    remote_si_env = f"{run_dir}/si.env"
    generated_env = _download_text(
        client,
        remote_si_env,
        local_si_env,
        "generated si.env",
        timeout=min(timeout, 60),
    )
    local_si_env.write_text(_complete_si_env(generated_env), encoding="utf-8")
    _upload_file(client, local_si_env, remote_si_env, timeout=min(timeout, 60))

    command = (
        f"source {shlex.quote(str(profile['cadence_cshrc']))} ; "
        f"cd {shlex.quote(run_dir)} ; "
        f"si -batch -cdslib {shlex.quote(str(profile['cds_lib_path']))} "
        "-command nl >& si_batch_stdout.log"
    )
    shell_result = client.run_shell_command(command, timeout=min(timeout, 180))
    local_log = work_dir / "si_batch_stdout.log"
    try:
        log_text = _download_text(
            client,
            f"{run_dir}/si_batch_stdout.log",
            local_log,
            "si netlisting log",
            timeout=min(timeout, 60),
        )
    except RuntimeError:
        log_text = ""
    if not bool(getattr(shell_result, "ok", False)):
        tail = "\n".join(log_text.splitlines()[-12:])
        raise RuntimeError(
            f"si batch command failed: {_bridge_result_error(shell_result)}"
            + (f"; log tail: {tail}" if tail else "")
            + f"; remote si run retained at {run_dir}"
        )
    if not log_text:
        raise RuntimeError(
            f"si netlisting log was not created; remote si run retained at {run_dir}"
        )
    _validate_si_log(log_text)

    local_netlist = work_dir / "oa_netlist.scs"
    remote_netlist = f"{run_dir}/netlist"
    netlist_text = _download_text(
        client,
        remote_netlist,
        local_netlist,
        "si netlist",
        timeout=min(timeout, 60),
    )
    circuit = str(payload.get("circuit", "inverter"))
    if circuit == "inverter":
        parsed = _parse_inverter_netlist(netlist_text, profile)
    elif circuit == "common_source":
        parsed = _parse_common_source_netlist(netlist_text, profile)
    elif circuit == "differential_pair":
        parsed = _parse_differential_pair_netlist(netlist_text, profile)
    elif circuit == "existing_schematic":
        if schematic is None:
            raise RuntimeError(
                "generic OA netlist consistency requires the same schematic readback"
            )
        raw_settings = payload.get("generic_simulation")
        raw_discovery = payload.get("parameter_binding_discovery")
        settings = (
            GenericOaSimulationSpec.model_validate(raw_settings)
            if isinstance(raw_settings, dict)
            else None
        )
        discovery = (
            ParameterBindingDiscoverySpec.model_validate(raw_discovery)
            if isinstance(raw_discovery, dict)
            else None
        )
        if (settings is None) == (discovery is None):
            raise RuntimeError(
                "generic OA netlisting requires exactly one simulation or "
                "parameter-binding discovery contract"
            )
        hierarchy_bindings = (
            list(settings.hierarchy_bindings)
            if settings is not None
            else list(discovery.hierarchy_bindings)
        )
        hierarchy_schematics: dict[tuple[str, str], dict[str, Any]] = {}
        hierarchy_summaries: dict[tuple[str, str], dict[str, Any]] = {}
        target_library, target_cell = _target(payload)
        for binding in hierarchy_bindings:
            child_key = (binding.library, binding.cell)
            if child_key == (target_library, target_cell):
                raise RuntimeError(
                    "generic hierarchy binding cannot recursively target the top cell"
                )
            if child_key in hierarchy_schematics:
                continue
            child = _try_read_schematic(client, *child_key)
            if child is None:
                raise RuntimeError(
                    "generic hierarchy child schematic does not exist: "
                    f"{binding.library}/{binding.cell}/schematic"
                )
            hierarchy_schematics[child_key] = child
            child_pin_geometry, child_placement = _schematic_geometry_bundle(
                client,
                *child_key,
            )
            hierarchy_summaries[child_key] = _existing_schematic_summary(
                child,
                pin_geometry=child_pin_geometry,
                placement=child_placement,
            )
        try:
            parsed = _parse_existing_schematic_netlist(
                netlist_text,
                schematic,
                settings,
                hierarchy_schematics,
                hierarchy_summaries,
                hierarchy_bindings=hierarchy_bindings,
                include_parameter_inventory=discovery is not None,
            )
            hierarchy_binding_by_instance = {
                binding.instance: binding for binding in hierarchy_bindings
            }
            parameter_scope_checks: list[dict[str, Any]] = []
            for scope in _hierarchy_parameter_scope_specs(payload):
                binding = hierarchy_binding_by_instance.get(scope.top_instance)
                if binding is None:
                    raise RuntimeError(
                        "hierarchy parameter scope has no matching si binding for "
                        f"{scope.top_instance!r}"
                    )
                if (binding.library, binding.cell, binding.view) != (
                    scope.library,
                    scope.cell,
                    scope.view,
                ):
                    raise RuntimeError(
                        "hierarchy parameter scope disagrees with si binding for "
                        f"{scope.top_instance!r}"
                    )
                child_summary = hierarchy_summaries.get(
                    (scope.library, scope.cell)
                )
                if child_summary is None:
                    raise RuntimeError(
                        "hierarchy parameter child readback is missing after si for "
                        f"{scope.top_instance!r}"
                    )
                topology_sha256 = topology_fingerprint(
                    snapshot_from_inspection(child_summary)
                )
                placement_sha256 = child_summary.get("placement", {}).get("sha256")
                if topology_sha256 != scope.expected_child_topology_sha256:
                    raise RuntimeError(
                        "hierarchy parameter child topology drifted before si binding: "
                        f"{scope.top_instance}"
                    )
                if placement_sha256 != scope.expected_child_placement_sha256:
                    raise RuntimeError(
                        "hierarchy parameter child placement drifted before si binding: "
                        f"{scope.top_instance}"
                    )
                parameter_scope_checks.append(
                    {
                        "top_instance": scope.top_instance,
                        "child": {
                            "library": scope.library,
                            "cell": scope.cell,
                            "view": scope.view,
                        },
                        "child_topology_sha256": topology_sha256,
                        "child_placement_sha256": placement_sha256,
                        "state_source": "bridge_readback",
                        "si_binding_source": "eda_result",
                        "consistency_source": "software_inference",
                    }
                )
            parsed["hierarchy_parameter_scopes"] = parameter_scope_checks
        except Exception as exc:
            raise RuntimeError(
                f"{exc}; si netlist retained at {remote_netlist} "
                f"(sha256={hashlib.sha256(netlist_text.encode('utf-8')).hexdigest()})"
            ) from exc
    else:
        raise RuntimeError(f"unsupported OA netlist circuit: {circuit}")
    spectre_netlist_path = remote_netlist
    spectre_netlist_sha256 = hashlib.sha256(
        netlist_text.encode("utf-8")
    ).hexdigest()
    spectre_netlist_transform = "raw_si_netlist"
    if circuit == "existing_schematic":
        # ``si`` writes its language declaration to a separate netlistHeader,
        # while the generated ``netlist`` body can start directly with a
        # Spectre subckt.  An include file without a .scs suffix may be parsed
        # as SPICE regardless of the parent deck state.  Keep the raw si file
        # immutable for evidence and upload a deterministic one-line language
        # envelope for simulation consumption.
        spectre_netlist_text = "simulator lang=spectre\n" + netlist_text
        local_spectre_netlist = work_dir / "oa_netlist_spectre.scs"
        local_spectre_netlist.write_text(
            spectre_netlist_text,
            encoding="utf-8",
        )
        spectre_netlist_path = f"{run_dir}/vda_oa_netlist.scs"
        _upload_file(
            client,
            local_spectre_netlist,
            spectre_netlist_path,
            timeout=min(timeout, 60),
        )
        spectre_netlist_sha256 = hashlib.sha256(
            spectre_netlist_text.encode("utf-8")
        ).hexdigest()
        spectre_netlist_transform = "prepend_simulator_lang_spectre"
    return {
        "remote_run_dir": run_dir,
        "remote_netlist_path": remote_netlist,
        "netlist_sha256": hashlib.sha256(netlist_text.encode("utf-8")).hexdigest(),
        "spectre_netlist_path": spectre_netlist_path,
        "spectre_netlist_sha256": spectre_netlist_sha256,
        "spectre_netlist_transform": spectre_netlist_transform,
        "parsed": parsed,
        "si_log_tail": log_text.splitlines()[-12:],
    }


def _binding_discovery_state(
    client,
    payload: dict[str, Any],
    discovery: ParameterBindingDiscoverySpec,
) -> dict[str, Any]:
    library, cell = _target(payload)
    schematic = _read_schematic(client, library, cell)
    pin_geometry, placement = _schematic_geometry_bundle(client, library, cell)
    summary = _existing_schematic_summary(
        schematic,
        pin_geometry=pin_geometry,
        placement=placement,
    )
    summary = _attach_hierarchy_parameter_scope_state(
        client,
        library,
        cell,
        schematic,
        payload,
        summary,
    )
    raw_context = payload.get("design_context")
    if not isinstance(raw_context, dict):
        raise RuntimeError("binding discovery design context is missing")
    context_audit = audit_design_context(
        summary,
        DesignContext.model_validate(raw_context),
    )
    raw_parameters = (summary.get("instance_parameters") or {}).get(
        discovery.instance
    )
    if not isinstance(raw_parameters, dict):
        raise RuntimeError(
            "binding discovery target instance is absent from OA readback: "
            f"{discovery.instance}"
        )
    parameters = {
        str(name): str(value) for name, value in sorted(raw_parameters.items())
    }
    return {
        "schematic": schematic,
        "summary": summary,
        "instance_parameters": parameters,
        "instance_parameters_sha256": canonical_parameter_table_sha256(parameters),
        "topology_sha256": context_audit.topology_sha256,
        "design_context_sha256": context_audit.context_sha256,
    }


def _binding_discovery_write(
    client,
    payload: dict[str, Any],
    discovery: ParameterBindingDiscoverySpec,
    value: str,
) -> dict[str, Any]:
    library, cell = _target(payload)
    write_payload = dict(payload)
    requested = {
        discovery.instance: {discovery.oa_parameter: str(value)}
    }
    write_payload["instance_parameter_updates"] = [
        {
            "instance": discovery.instance,
            "parameters": {discovery.oa_parameter: str(value)},
        }
    ]
    result = _apply_explicit_instance_parameters(
        client,
        library,
        cell,
        write_payload,
    )
    for key in (
        "requested_instance_parameters",
        "applied_instance_parameters",
        "confirmed_instance_parameters",
    ):
        if result.get(key) != requested:
            raise RuntimeError(
                f"binding discovery write did not confirm exact {key}: "
                f"expected={requested!r}, actual={result.get(key)!r}"
            )
    return result


def _persist_binding_netlist_artifacts(
    work_dir: Path,
    output_root: Path,
    stage: str,
) -> dict[str, Any]:
    stage_dir = output_root / stage
    stage_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    for name in ("oa_netlist.scs", "si_batch_stdout.log"):
        source = work_dir / name
        if not source.is_file() or source.stat().st_size <= 0:
            raise RuntimeError(
                f"binding discovery local {stage} artifact is missing or empty: {name}"
            )
        target = stage_dir / name
        shutil.copy2(source, target)
        content = target.read_bytes()
        entries.append(
            {
                "path": str(target),
                "relative_path": f"{stage}/{name}",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest_payload = {
        "schema_version": 1,
        "stage": stage,
        "files": entries,
    }
    manifest_text = json.dumps(
        manifest_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    return {
        "source": "eda_result",
        "directory": str(stage_dir),
        "files": entries,
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(
            manifest_text.encode("utf-8")
        ).hexdigest(),
    }


def _cleanup_binding_netlist_scratch(client, remote_run_dir: str) -> dict[str, Any]:
    normalized = posixpath.normpath(str(remote_run_dir).rstrip("/"))
    leaf = posixpath.basename(normalized)
    if not normalized.startswith("/data/xum/") or not leaf.startswith("vda_"):
        raise RuntimeError(
            "binding discovery refused to clean an unexpected remote path: "
            f"{remote_run_dir!r}"
        )
    remote_q = shlex.quote(normalized)
    runner = getattr(client, "ssh_runner", None)
    if runner is not None:
        # VirtuosoClient.run_shell_command() deliberately treats a nil csh
        # return as failure.  Successful, silent filesystem commands such as
        # rm(1) and test(1) therefore cannot be judged through that API.  Use
        # Bridge's existing transport runner and its real exit status instead.
        removed = runner.run_command(
            f"rm -rf -- {remote_q}",
            timeout=60,
        )
        _require_transport_result(removed, "clean binding-discovery si scratch")
        checked = runner.run_command(
            f"test ! -e {remote_q}",
            timeout=30,
        )
        _require_transport_result(
            checked,
            "verify binding-discovery si scratch cleanup",
        )
        transport = "bridge_ssh_runner"
    else:
        # Local-mode Bridge clients do not expose an SSH runner.  Emit a
        # marker so Bridge's csh wrapper has a non-nil success value while
        # still preserving the command's failure status.
        removed = client.run_shell_command(
            f"rm -rf -- {remote_q} && echo VDA_BINDING_CLEANUP_REMOVED",
            timeout=60,
        )
        _require_bridge_result(removed, "clean binding-discovery si scratch")
        checked = client.run_shell_command(
            f"test ! -e {remote_q} && echo VDA_BINDING_CLEANUP_VERIFIED",
            timeout=30,
        )
        _require_bridge_result(
            checked,
            "verify binding-discovery si scratch cleanup",
        )
        transport = "bridge_csh_marker_fallback"
    return {
        "remote_path": normalized,
        "removed": True,
        "transport": transport,
        "source": "system_event",
    }


def _binding_discovery_netlist_stage(
    client,
    payload: dict[str, Any],
    discovery: ParameterBindingDiscoverySpec,
    state: dict[str, Any],
    *,
    stage: str,
    output_root: Path,
    timeout: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix=f"vda_binding_discovery_{stage}_"
    ) as temp_dir:
        work_dir = Path(temp_dir)
        evidence = _generate_oa_netlist(
            client,
            payload,
            work_dir,
            timeout=timeout,
            schematic=state["schematic"],
        )
        parsed = evidence.get("parsed") or {}
        inventory = parsed.get("parameter_inventory") or {}
        target = inventory.get(discovery.instance)
        if not isinstance(target, dict) or not isinstance(
            target.get("parameters"), dict
        ):
            raise RuntimeError(
                "si parameter inventory is missing the binding discovery target "
                f"{discovery.instance!r}"
            )
        artifact_bundle = _persist_binding_netlist_artifacts(
            work_dir,
            output_root,
            stage,
        )
        cleanup = _cleanup_binding_netlist_scratch(
            client,
            str(evidence["remote_run_dir"]),
        )
        return {
            "instance": discovery.instance,
            "instance_model": str(target.get("model")),
            "instance_nodes": list(target.get("nodes") or []),
            "instance_parameters": {
                str(name): str(value)
                for name, value in sorted(target["parameters"].items())
            },
            "parameter_inventory": {
                str(instance): {
                    "model": str(item.get("model")),
                    "nodes": list(item.get("nodes") or []),
                    "parameters": {
                        str(name): str(value)
                        for name, value in sorted(
                            (item.get("parameters") or {}).items()
                        )
                    },
                }
                for instance, item in sorted(inventory.items())
            },
            "canonical_netlist_signature_sha256": str(
                parsed["canonical_signature_sha256"]
            ),
            "raw_netlist": {
                "source": "eda_result",
                "generator": "Cadence si -batch",
                "remote_path": evidence["remote_netlist_path"],
                "sha256": evidence["netlist_sha256"],
                "remote_retained": False,
                "si_log_tail": evidence["si_log_tail"],
            },
            "artifact_bundle": artifact_bundle,
            "remote_cleanup": cleanup,
            "source": "eda_result",
        }


def discover_existing_schematic_parameter_binding(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Discover one direct OA-CDF to ``si`` binding and restore exact OA state."""

    raw_discovery = payload.get("parameter_binding_discovery")
    if not isinstance(raw_discovery, dict):
        raise RuntimeError("parameter binding discovery contract is missing")
    discovery = ParameterBindingDiscoverySpec.model_validate(raw_discovery)
    raw_output_root = payload.get("binding_discovery_output_root")
    if not isinstance(raw_output_root, str) or not raw_output_root:
        raise RuntimeError("binding discovery local artifact root is missing")
    output_root = Path(raw_output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    client = _client()
    timeout = int(payload.get("timeout_seconds", 600))
    expected = {
        str(name): str(value)
        for name, value in sorted(
            discovery.expected_instance_parameters.items()
        )
    }
    original_value = expected[discovery.oa_parameter]
    state = _binding_discovery_state(client, payload, discovery)
    interrupted_recovery = {
        "performed": False,
        "source": "system_event",
    }
    if state["instance_parameters"] != expected:
        current_value = state["instance_parameters"].get(discovery.oa_parameter)
        if current_value is None or not spectre_values_equal(
            current_value,
            discovery.probe_value,
        ):
            raise RuntimeError(
                "binding discovery CAS mismatch: current CDF table is neither the "
                "declared baseline nor the declared interrupted probe state"
            )
        _binding_discovery_write(
            client,
            payload,
            discovery,
            original_value,
        )
        state = _binding_discovery_state(client, payload, discovery)
        if state["instance_parameters"] != expected:
            raise RuntimeError(
                "binding discovery restored the declared field after an interrupted "
                "probe, but the complete CDF table did not return to its CAS baseline"
            )
        interrupted_recovery = {
            "performed": True,
            "recovered_from": "declared_probe_value",
            "complete_cdf_table_match": True,
            "source": "system_event",
        }
    elif not spectre_values_equal(
        state["instance_parameters"][discovery.oa_parameter],
        original_value,
    ):
        raise RuntimeError(
            "binding discovery CDF table matched but the target field did not match "
            "its declared original value"
        )

    baseline_state = state
    baseline_netlist = _binding_discovery_netlist_stage(
        client,
        payload,
        discovery,
        baseline_state,
        stage="baseline",
        output_root=output_root,
        timeout=timeout,
    )
    probe_state: dict[str, Any] | None = None
    probe_netlist: dict[str, Any] | None = None
    restored_state: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    restoration_error: BaseException | None = None
    restore_required = True
    try:
        _binding_discovery_write(
            client,
            payload,
            discovery,
            discovery.probe_value,
        )
        probe_state = _binding_discovery_state(client, payload, discovery)
        actual_probe = probe_state["instance_parameters"].get(
            discovery.oa_parameter
        )
        if actual_probe is None or not spectre_values_equal(
            actual_probe,
            discovery.probe_value,
        ):
            raise RuntimeError(
                "binding discovery OA readback did not confirm the declared probe value"
            )
        probe_netlist = _binding_discovery_netlist_stage(
            client,
            payload,
            discovery,
            probe_state,
            stage="probe",
            output_root=output_root,
            timeout=timeout,
        )
    except BaseException as exc:
        primary_error = exc
    finally:
        if restore_required:
            try:
                _binding_discovery_write(
                    client,
                    payload,
                    discovery,
                    original_value,
                )
                restored_state = _binding_discovery_state(
                    client,
                    payload,
                    discovery,
                )
                if restored_state["instance_parameters"] != expected:
                    raise RuntimeError(
                        "complete OA CDF table did not match the CAS baseline after "
                        "binding discovery restoration"
                    )
            except BaseException as exc:
                restoration_error = exc
    if restoration_error is not None:
        primary_text = (
            f"; original probe error={type(primary_error).__name__}: {primary_error}"
            if primary_error is not None
            else ""
        )
        raise RuntimeError(
            "binding discovery automatic OA restoration failed; fresh readback is "
            f"required before retry: {type(restoration_error).__name__}: "
            f"{restoration_error}{primary_text}"
        ) from restoration_error
    if primary_error is not None:
        raise primary_error
    if probe_state is None or probe_netlist is None or restored_state is None:
        raise RuntimeError("binding discovery did not complete its three OA states")

    restored_netlist = _binding_discovery_netlist_stage(
        client,
        payload,
        discovery,
        restored_state,
        stage="restored",
        output_root=output_root,
        timeout=timeout,
    )
    canonical_restored = (
        baseline_netlist["canonical_netlist_signature_sha256"]
        == restored_netlist["canonical_netlist_signature_sha256"]
    )
    if not canonical_restored:
        raise RuntimeError(
            "binding discovery OA CDF restored, but the canonical si instance "
            "signature did not return to baseline"
        )
    classification = classify_parameter_binding_probe(
        discovery,
        baseline_oa_parameters=baseline_state["instance_parameters"],
        probe_oa_parameters=probe_state["instance_parameters"],
        baseline_netlist_inventory=baseline_netlist["parameter_inventory"],
        probe_netlist_inventory=probe_netlist["parameter_inventory"],
    )
    return {
        "contract": discovery.model_dump(mode="json"),
        "target": payload["target"],
        "spectre_simulation_performed": False,
        "oa_write_performed": True,
        "remote_compute_performed": True,
        "baseline": {
            "oa_instance_parameters": baseline_state["instance_parameters"],
            "oa_instance_parameters_sha256": baseline_state[
                "instance_parameters_sha256"
            ],
            "oa_topology_sha256": baseline_state["topology_sha256"],
            "oa_source": "bridge_readback",
            "netlist_instance_parameters": baseline_netlist[
                "instance_parameters"
            ],
            **baseline_netlist,
        },
        "probe": {
            "oa_instance_parameters": probe_state["instance_parameters"],
            "oa_instance_parameters_sha256": probe_state[
                "instance_parameters_sha256"
            ],
            "oa_topology_sha256": probe_state["topology_sha256"],
            "oa_source": "bridge_readback",
            "netlist_instance_parameters": probe_netlist["instance_parameters"],
            **probe_netlist,
        },
        "restored": {
            "oa_instance_parameters": restored_state["instance_parameters"],
            "oa_instance_parameters_sha256": restored_state[
                "instance_parameters_sha256"
            ],
            "oa_topology_sha256": restored_state["topology_sha256"],
            "oa_source": "bridge_readback",
            "netlist_instance_parameters": restored_netlist[
                "instance_parameters"
            ],
            **restored_netlist,
        },
        "restoration": {
            "oa_exact": restored_state["instance_parameters"] == expected,
            "canonical_netlist_signature_exact": canonical_restored,
            "verified": True,
        },
        "classification": classification,
        "interrupted_probe_recovery": interrupted_recovery,
        "local_artifact_root": str(output_root),
        "evidence_sources": {
            "oa_states": "bridge_readback",
            "si_netlists": "eda_result",
            "classification": "software_inference",
            "recovery_and_cleanup": "system_event",
            "probe_contract": "user_input",
        },
    }


def _signal(data: dict[str, Any], name: str) -> list[float]:
    for key, values in data.items():
        if key.lower() == name.lower():
            result = [float(value) for value in values]
            if not result:
                raise RuntimeError(f"Spectre signal {name} is empty")
            return result
    raise RuntimeError(f"Spectre result missing signal {name}; available={sorted(data)}")


def _complex_signal(data: dict[str, Any], name: str) -> list[complex]:
    for key, values in data.items():
        if key.lower() != name.lower():
            continue
        if not isinstance(values, list) or not values:
            raise RuntimeError(f"Spectre complex signal {name} is empty")
        try:
            return [complex(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Spectre complex signal {name} contains a non-numeric value"
            ) from exc
    raise RuntimeError(
        f"Spectre result missing complex signal {name}; available={sorted(data)}"
    )


def _scalar(data: dict[str, Any], name: str) -> float:
    for key, raw_value in data.items():
        if key.lower() != name.lower():
            continue
        value = raw_value
        if isinstance(value, list):
            if not value:
                raise RuntimeError(f"Spectre scalar {name} is empty")
            value = value[-1]
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Spectre scalar {name} is not numeric") from exc
    raise RuntimeError(f"Spectre result missing scalar {name}; available={sorted(data)}")


def _operating_point_scalar(
    data: dict[str, Any], instance: str, *quantities: str
) -> float:
    lowered_instance = instance.lower()
    for quantity in quantities:
        suffixes = (
            f"{lowered_instance}:{quantity.lower()}",
            f"{lowered_instance}.{quantity.lower()}",
            f"{lowered_instance}/{quantity.lower()}",
        )
        for key, raw_value in data.items():
            if not str(key).lower().endswith(suffixes):
                continue
            value = raw_value[-1] if isinstance(raw_value, list) and raw_value else raw_value
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"operating-point scalar {instance}:{quantity} is not numeric"
                ) from exc
    requested = "/".join(quantities)
    raise RuntimeError(
        f"missing operating-point scalar {instance}:{requested}; "
        f"available={sorted(data)}"
    )


def _has_operating_point_scalar(
    data: dict[str, Any], instance: str, quantity: str
) -> bool:
    lowered_instance = instance.lower()
    suffixes = (
        f"{lowered_instance}:{quantity.lower()}",
        f"{lowered_instance}.{quantity.lower()}",
        f"{lowered_instance}/{quantity.lower()}",
    )
    return any(str(key).lower().endswith(suffixes) for key in data)


def _optional_mos_small_signal_operating_point(
    data: dict[str, Any], instance: str
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if _has_operating_point_scalar(data, instance, "gmb"):
        values["gmb_s"] = _operating_point_scalar(data, instance, "gmb", "gmbs")
    if not _has_operating_point_scalar(data, instance, "cgg"):
        return values
    values["charge_derivative_matrix_f"] = {
        name: _operating_point_scalar(data, instance, name)
        for name in MOS_CHARGE_DERIVATIVE_NAMES
    }
    junction_presence = {
        name
        for name in ("cjd", "cjs")
        if _has_operating_point_scalar(data, instance, name)
    }
    if junction_presence and junction_presence != {"cjd", "cjs"}:
        raise RuntimeError(
            f"operating point for {instance} returned an incomplete junction-cap set"
        )
    if junction_presence:
        values["cjd_f"] = _operating_point_scalar(data, instance, "cjd")
        values["cjs_f"] = _operating_point_scalar(data, instance, "cjs")
    return values


def _mos_small_signal_operating_point_save(instance: str) -> str:
    quantities = ("gmb", *MOS_CHARGE_DERIVATIVE_NAMES, "cjd", "cjs")
    return "save " + " ".join(
        f"{instance}:{quantity}" for quantity in quantities
    ) + "\n"


def _select_shallow_psf_file(
    output_dir: Path, names: tuple[str, ...], *, label: str
) -> Path:
    matches: list[tuple[int, int, Path]] = []
    for name_rank, name in enumerate(names):
        for path in output_dir.rglob(name):
            if path.is_file():
                depth = len(path.relative_to(output_dir).parts)
                matches.append((depth, name_rank, path))
    if not matches:
        raise RuntimeError(
            f"Spectre result is missing the root {label} PSF file; "
            f"expected one of {list(names)}"
        )
    best_score = min((depth, name_rank) for depth, name_rank, _ in matches)
    selected = [
        path
        for depth, name_rank, path in matches
        if (depth, name_rank) == best_score
    ]
    if len(selected) != 1:
        raise RuntimeError(
            f"Spectre result has ambiguous root {label} PSF files: "
            f"{[str(path) for path in selected]}"
        )
    return selected[0]


def _common_source_dc_data_from_result(
    result: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from virtuoso_bridge.spectre.parsers import parse_spectre_psf_ascii

    raw_output_dir = getattr(result, "metadata", {}).get("output_dir")
    if not raw_output_dir:
        raise RuntimeError("Spectre result is missing its downloaded PSF path")
    output_dir = Path(str(raw_output_dir))
    dc_file = _select_shallow_psf_file(
        output_dir,
        ("dcOp.dc", "dc.dc", "spectre.dc"),
        label="DC",
    )
    op_file = _select_shallow_psf_file(
        output_dir,
        ("dcOpInfo.info",),
        label="operating-point",
    )
    dc_result = parse_spectre_psf_ascii(dc_file)
    op_result = parse_spectre_psf_ascii(op_file)
    dc_data = getattr(dc_result, "data", None)
    op_data = getattr(op_result, "data", None)
    if not isinstance(dc_data, dict) or not dc_data:
        raise RuntimeError("Spectre root DC PSF file is empty or unparseable")
    if not isinstance(op_data, dict) or not op_data:
        raise RuntimeError(
            "Spectre root operating-point PSF file is empty or unparseable"
        )
    merged = {f"dc_{key}": value for key, value in dc_data.items()}
    merged.update({f"dcOpInfo_{key}": value for key, value in op_data.items()})

    def file_evidence(path: Path) -> dict[str, str]:
        return {
            "relative_path": path.relative_to(output_dir).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    return merged, {
        "selection": "shallowest analysis-specific PSF files",
        "dc": file_evidence(dc_file),
        "operating_point": file_evidence(op_file),
    }


def _mos_characterization_deck(
    profile: dict[str, Any],
    settings: DeviceCharacterizationSpec,
    points: list[dict[str, Any]],
) -> str:
    model_path = str(profile["model_include"])
    model_section = str(profile["model_section"])
    if '"' in model_path or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", model_section
    ):
        raise ValueError("invalid MOS characterization model include")
    models = {
        "nmos": str(profile["nmos_cell"]),
        "pmos": str(profile["pmos_cell"]),
    }
    if any(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$.-]*", model) is None
        for model in models.values()
    ):
        raise ValueError("invalid MOS characterization model name")

    elements: list[str] = []
    saves: list[str] = []
    for index, point in enumerate(points):
        instance = f"MCHAR{index:04d}"
        suffix = f"P{index:04d}"
        polarity = str(point["polarity"])
        sign = 1.0 if polarity == "nmos" else -1.0
        gate_v = sign * float(point["vgs_magnitude_v"])
        drain_v = sign * float(point["vds_magnitude_v"])
        bulk_v = -sign * float(point["vsb_magnitude_v"])
        model_parameters = settings.model_parameters_by_polarity.get(polarity, {})
        model_parameter_text = "".join(
            f" {name}={value}" for name, value in sorted(model_parameters.items())
        )
        elements.extend(
            (
                f"VG{suffix} (G{suffix} 0) vsource dc={gate_v:.12g}",
                f"VD{suffix} (D{suffix} 0) vsource dc={drain_v:.12g}",
                f"VB{suffix} (B{suffix} 0) vsource dc={bulk_v:.12g}",
                (
                    f"{instance} (D{suffix} G{suffix} 0 B{suffix}) "
                    f"{models[polarity]} w={settings.width_um:.12g}u "
                    f"l={float(point['length_um']):.12g}u nf=1 multi=1"
                    f"{model_parameter_text}"
                ),
            )
        )
        saves.append(
            "save "
            + " ".join(
                f"{instance}:{quantity}"
                for quantity in (
                    "ids",
                    "vgs",
                    "vds",
                    "vbs",
                    "vdsat",
                    "gm",
                    "gds",
                    "gmb",
                    "cgg", "cgd", "cgs", "cgb",
                    "cdg", "cdd", "cds", "cdb",
                    "csg", "csd", "css", "csb",
                    "cbg", "cbd", "cbs", "cbb",
                    "cjd", "cjs",
                )
            )
        )
    return "\n".join(
        (
            "simulator lang=spectre",
            f'include "{model_path}" section={model_section}',
            "",
            *elements,
            "",
            (
                "simulatorOptions options psfversion=\"1.4.0\" "
                f"temp={settings.temperature_c:.12g} reltol=1e-5 "
                "vabstol=1e-8 iabstol=1e-15"
            ),
            "dcOp dc write=\"spectre.dc\" maxiters=150 maxsteps=10000 annotate=status",
            "dcOpInfo info what=oppoint where=rawfile",
            *saves,
            "saveOptions options save=allpub",
            "",
        )
    )


def _mos_characterization_points_from_result(
    result: Any,
    profile: dict[str, Any],
    points: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged, root_evidence = _common_source_dc_data_from_result(result)
    models = {
        "nmos": str(profile["nmos_cell"]),
        "pmos": str(profile["pmos_cell"]),
    }
    quantities: dict[str, tuple[str, ...]] = {
        "ids_a": ("ids", "id"),
        "vgs_v": ("vgs",),
        "vds_v": ("vds",),
        "vbs_v": ("vbs",),
        "vdsat_v": ("vdsat",),
        "gm_s": ("gm",),
        "gds_s": ("gds",),
        "gmb_s": ("gmb", "gmbs"),
        "cgg_f": ("cgg",),
        "cgd_f": ("cgd",),
        "cgs_f": ("cgs",),
        "cgb_f": ("cgb",),
        "cdg_f": ("cdg",),
        "cdd_f": ("cdd",),
        "cds_f": ("cds",),
        "cdb_f": ("cdb",),
        "csg_f": ("csg",),
        "csd_f": ("csd",),
        "css_f": ("css",),
        "csb_f": ("csb",),
        "cbg_f": ("cbg",),
        "cbd_f": ("cbd",),
        "cbs_f": ("cbs",),
        "cbb_f": ("cbb",),
        "cjd_f": ("cjd",),
        "cjs_f": ("cjs",),
    }
    returned: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        instance = f"MCHAR{index:04d}"
        raw = {
            name: _operating_point_scalar(merged, instance, *aliases)
            for name, aliases in quantities.items()
        }
        if any(not math.isfinite(float(value)) for value in raw.values()):
            raise RuntimeError(
                f"MOS characterization point {point['id']} contains a non-finite OP value"
            )
        returned.append(
            {
                **point,
                "model": models[str(point["polarity"])],
                "instance": instance,
                "raw": raw,
                "raw_evidence_source": "eda_result",
            }
        )
    return returned, root_evidence


def _spectre_artifact_manifest(work_dir: Path) -> tuple[list[dict[str, Any]], str]:
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in work_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(work_dir).as_posix()
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_path(path),
            }
        )
    if not entries:
        raise RuntimeError("Spectre run produced no simulator artifacts")
    canonical = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return entries, hashlib.sha256(canonical).hexdigest()


def _spectre_version_from_log(work_dir: Path) -> str:
    log_path = work_dir / "spectre.out"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError("Spectre log is missing") from exc
    match = re.search(r"(?im)^\s*Version\s+([^\s]+)", text)
    if match is None:
        raise RuntimeError("Spectre log has no version marker")
    return match.group(1)


def _ssh_command_result(runner: Any, command: str, label: str) -> str:
    result = runner.run_command(command)
    returncode = int(getattr(result, "returncode", -1))
    if returncode != 0:
        detail = str(getattr(result, "stderr", "")).strip()
        raise RuntimeError(f"{label} failed (rc={returncode}): {detail}")
    return str(getattr(result, "stdout", ""))


_RESOURCE_DIRECTORY_PREFIX = "VDA_RESOURCE_DIRECTORY\t"
_RESOURCE_PROCESS_PREFIX = "VDA_RESOURCE_PROCESS\t"
_RESOURCE_HOST_PREFIX = "VDA_RESOURCE_HOST\t"


def _parse_remote_resource_inventory(
    text: str, *, now: float, older_than_days: float
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, str]]:
    directories: list[dict[str, Any]] = []
    processes: dict[str, int] = {}
    host: dict[str, str] = {}
    for raw_line in text.splitlines():
        if raw_line.startswith(_RESOURCE_DIRECTORY_PREFIX):
            parts = raw_line.split("\t", 3)
            if len(parts) != 4:
                raise RuntimeError(f"invalid remote resource directory row: {raw_line!r}")
            modified_epoch = float(parts[1])
            size_bytes = int(parts[2])
            path = _validated_ade_remote_path(parts[3], "remote resource path")
            age_days = max(0.0, (now - modified_epoch) / 86400.0)
            directories.append(
                {
                    "path": path,
                    "modified_epoch": modified_epoch,
                    "age_days": age_days,
                    "size_bytes": size_bytes,
                    "review_candidate": age_days >= older_than_days,
                    "delete_authorized": False,
                    "evidence_source": "bridge_readback",
                }
            )
        elif raw_line.startswith(_RESOURCE_PROCESS_PREFIX):
            parts = raw_line.split("\t", 2)
            if len(parts) != 3 or parts[1] not in {"spectre", "si", "virtuoso"}:
                raise RuntimeError(f"invalid remote resource process row: {raw_line!r}")
            processes[parts[1]] = int(parts[2])
        elif raw_line.startswith(_RESOURCE_HOST_PREFIX):
            parts = raw_line.split("\t", 2)
            if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[1]):
                raise RuntimeError(f"invalid remote resource host row: {raw_line!r}")
            ipaddress.ip_address(parts[2])
            host = {"hostname": parts[1], "ip_address": parts[2]}
    return directories, processes, host


def _audit_maestro_sessions(client: Any, *, run_root: str) -> list[dict[str, Any]]:
    readback = client.execute_skill("maeGetSessions()", timeout=30)
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro session inventory failed: {errors[0]}")
    sessions = re.findall(r'"([^"\\]+)"', str(getattr(readback, "output", "") or ""))
    inventory: list[dict[str, Any]] = []
    prefix = f"{run_root}/vda_ade_run_"
    for session in sessions:
        tests = _maestro_tests_readback(client, session)
        runtime_paths = [
            {
                "test": test,
                **_maestro_test_runtime_path_state(client, session=session, test=test),
            }
            for test in tests
        ]
        vda_managed = any(
            str(item[field]).startswith(prefix)
            for item in runtime_paths
            for field in ("project_dir", "results_dir", "analog_run_dir")
        )
        inventory.append(
            {
                "session": session,
                "tests": runtime_paths,
                "vda_managed_runtime": vda_managed,
                "read_only_inventory": True,
                "evidence_source": "bridge_readback",
            }
        )
    return inventory


def _remote_resource_inventory_via_skill(
    client: Any, *, command: str, manifest_path: str
) -> str:
    """Run a read-only host probe through the live Virtuoso SKILL channel."""

    from virtuoso_bridge.virtuoso.ops import escape_skill_string

    path = _validated_ade_remote_path(manifest_path, "resource audit manifest")
    encoded_command = base64.b64encode(command.encode("utf-8")).decode("ascii")
    shell_command = (
        f"printf %s {encoded_command} | base64 -d | /bin/bash "
        f"> {shlex.quote(path)} 2>&1"
    )
    escaped_command = escape_skill_string(shell_command)
    escaped_path = escape_skill_string(path)
    try:
        result = client.execute_skill(f'system("{escaped_command}")', timeout=300)
        _require_bridge_result(result, "remote resource inventory via SKILL")
        return _read_remote_text_via_skill(
            client,
            path,
            page_lines=64,
            max_lines=8192,
        )
    finally:
        cleanup = client.execute_skill(
            f'system("rm -f -- {escape_skill_string(shlex.quote(path))}")',
            timeout=30,
        )
        _require_bridge_result(cleanup, "resource audit manifest cleanup")
        verification = client.execute_skill(
            f'if(isFile("{escaped_path}") then "present" else "absent")',
            timeout=30,
        )
        _require_bridge_result(verification, "resource audit manifest cleanup verify")
        if str(getattr(verification, "output", "") or "").strip('"') != "absent":
            raise RuntimeError(f"resource audit manifest remained after cleanup: {path}")


def audit_resources(payload: dict[str, Any]) -> dict[str, Any]:
    """Inventory retained remote evidence, EDA processes, and Maestro sessions."""

    from virtuoso_bridge.transport.tunnel import SSHClient

    profile = payload.get("profile") or {}
    run_root = _validated_ade_remote_path(
        profile.get("remote_run_root"), "resource audit run root"
    ).rstrip("/")
    older_than_days = float(payload.get("older_than_days", 7.0))
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")
    if not SSHClient.is_running():
        raise RuntimeError("no default virtuoso-bridge connection is running")
    ssh_client = _register_worker_resource(SSHClient.from_env(keep_remote_files=True))
    runner = ssh_client.ssh_runner
    runner._persistent_shell_enabled = False
    root_q = shlex.quote(run_root)
    command = f"""
if [ -d {root_q} ]; then
  find {root_q} -mindepth 1 -maxdepth 1 -type d -printf '%T@\\t%p\\n' |
  while IFS="$(printf '\\t')" read -r modified path; do
    bytes=$(du -sb -- "$path" | cut -f1)
    printf 'VDA_RESOURCE_DIRECTORY\\t%s\\t%s\\t%s\\n' "$modified" "$bytes" "$path"
  done
fi
for name in spectre si virtuoso; do
  count=$(pgrep -u "$(id -u)" -x "$name" 2>/dev/null | wc -l)
  printf 'VDA_RESOURCE_PROCESS\\t%s\\t%s\\n' "$name" "$count"
done
host_name=$(hostname -f 2>/dev/null || hostname)
host_ip=$(hostname -I | awk '{{print $1}}')
printf 'VDA_RESOURCE_HOST\t%s\t%s\n' "$host_name" "$host_ip"
""".strip()
    client = _client()
    direct_ssh_error: str | None = None
    try:
        raw = _ssh_command_result(runner, command, "remote resource inventory")
        inventory_transport = "bridge_public_ssh"
    except Exception as exc:
        direct_ssh_error = f"{type(exc).__name__}: {exc}"
        manifest_path = f"{run_root}/vda_resource_audit_{uuid.uuid4().hex[:12]}.tsv"
        raw = _remote_resource_inventory_via_skill(
            client,
            command=command,
            manifest_path=manifest_path,
        )
        inventory_transport = "bridge_skill_system_transient_manifest"
    directories, process_counts, host = _parse_remote_resource_inventory(
        raw,
        now=time.time(),
        older_than_days=older_than_days,
    )
    if set(process_counts) != {"spectre", "si", "virtuoso"}:
        raise RuntimeError(
            f"remote resource inventory omitted process counts: {process_counts!r}"
        )
    if not host:
        raise RuntimeError("remote resource inventory omitted host identity")
    maestro_sessions: list[dict[str, Any]] = []
    maestro_session_error: str | None = None
    try:
        maestro_sessions = _audit_maestro_sessions(client, run_root=run_root)
    except Exception as exc:
        maestro_session_error = f"{type(exc).__name__}: {exc}"
    return {
        "remote_run_root": run_root,
        "directories": directories,
        "directory_count": len(directories),
        "total_size_bytes": sum(item["size_bytes"] for item in directories),
        "review_candidate_count": sum(
            item["review_candidate"] for item in directories
        ),
        "process_counts": process_counts,
        "host": host,
        "inventory_transport": inventory_transport,
        "direct_ssh_transport_error": direct_ssh_error,
        "maestro_sessions": maestro_sessions,
        "vda_managed_maestro_session_count": sum(
            item["vda_managed_runtime"] for item in maestro_sessions
        ),
        "maestro_session_inventory_error": maestro_session_error,
        "older_than_days": older_than_days,
        "deletion_performed": False,
        "evidence_source": "bridge_readback",
    }


def characterize_mos_devices(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one standalone Spectre deck; this path never opens an OA object."""

    from virtuoso_bridge.spectre.runner import SpectreSimulator
    from virtuoso_bridge.transport.tunnel import SSHClient

    profile = payload["profile"]
    settings = DeviceCharacterizationSpec.model_validate(
        payload.get("device_characterization")
    )
    points = enumerate_mos_characterization_points(settings)
    timeout = int(payload.get("timeout_seconds", 600))
    run_root = str(profile["remote_run_root"]).rstrip("/")
    if not run_root.startswith("/data/xum/"):
        raise RuntimeError("MOS characterization remote root must stay under /data/xum")
    task_slug = re.sub(
        r"[^A-Za-z0-9_.-]",
        "_",
        str(payload.get("task_id", "task")),
    )[:48]
    remote_run_root = (
        f"{run_root}/vda_mos_characterization_{task_slug}_{uuid.uuid4().hex[:12]}"
    )
    # The VDA PDK profile selects foundry models.  It is intentionally not
    # treated as a Bridge tunnel-profile name; all established VDA adapters
    # reuse the already-running default Bridge connection.
    if not SSHClient.is_running():
        raise RuntimeError("no default virtuoso-bridge connection is running")
    ssh_client = _register_worker_resource(
        SSHClient.from_env(
            keep_remote_files=True,
        )
    )
    runner = ssh_client.ssh_runner
    runner._persistent_shell_enabled = False
    remote_q = shlex.quote(remote_run_root)
    _ssh_command_result(
        runner,
        f"test ! -e {remote_q}",
        "MOS characterization non-overwrite preflight",
    )

    with tempfile.TemporaryDirectory(prefix="vda_mos_characterization_") as temp_dir:
        work_dir = Path(temp_dir)
        deck_path = work_dir / "mos_characterization.scs"
        deck = _mos_characterization_deck(profile, settings, points)
        deck_path.write_text(deck, encoding="utf-8")
        spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
            ssh_client,
            work_dir,
            remote_run_root,
            timeout=timeout,
        )
        simulator = _create_spectre_simulator(
            SpectreSimulator,
            ssh_client,
            spectre_cmd=spectre_cmd,
            timeout=timeout,
            work_dir=work_dir,
            keep_remote_files=True,
            remote_run_dir=remote_run_root,
        )
        result = simulator.run_simulation(deck_path, {})
        if not result.ok:
            detail = _spectre_failure_detail(result, work_dir)
            raise RuntimeError(
                "Spectre MOS characterization failed; retained remote root "
                f"{remote_run_root}: {detail}"
            )
        returned_points, root_evidence = _mos_characterization_points_from_result(
            result,
            profile,
            points,
        )
        manifest, manifest_sha256 = _spectre_artifact_manifest(work_dir)
        tool_version = str(result.tool_version or "").strip()
        if not tool_version:
            tool_version = _spectre_version_from_log(work_dir)
        remote_children = [
            line.strip()
            for line in _ssh_command_result(
                runner,
                f"find {remote_q} -mindepth 1 -maxdepth 1 -type d -print",
                "MOS characterization remote artifact discovery",
            ).splitlines()
            if line.strip()
        ]
        if len(remote_children) != 1:
            raise RuntimeError(
                "MOS characterization expected exactly one remote simulator directory; "
                f"found {remote_children}"
            )
        return {
            "task_id": str(payload["task_id"]),
            "pdk_profile": str(profile["name"]),
            "process_corner": str(profile["model_section"]),
            "temperature_c": settings.temperature_c,
            "width_um": settings.width_um,
            "model_parameters_by_polarity": settings.model_parameters_by_polarity,
            "source_instance_binding": (
                settings.source_instance_binding.model_dump(mode="json")
                if settings.source_instance_binding is not None
                else None
            ),
            "raw_point_evidence_source": "eda_result",
            "points": returned_points,
            "tool_version": tool_version,
            "warnings": list(result.warnings[:20]),
            "evidence": {
                "source": "eda_result",
                "bridge_connection_profile": "default",
                "pdk_profile": str(profile["name"]),
                "remote_run_root": remote_run_root,
                "remote_simulation_dir": remote_children[0],
                "non_overwrite_preflight": "absent",
                "artifact_manifest_scope": (
                    "downloaded simulator input, raw PSF result, and run-log bundle"
                ),
                "artifact_manifest_complete": True,
                "artifact_manifest": manifest,
                "manifest_sha256": manifest_sha256,
                "root_psf_selection": root_evidence,
                "deck_sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                "spectre_tool_version": tool_version,
                "process_lifecycle": process_lifecycle,
                "oa_access_performed": False,
                "oa_write_performed": False,
            },
        }


def _preview_node_names(
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


def _preview_dc_metrics(
    spec: NetlistPreviewSpec,
    variant: NetlistPreviewVariant,
    data: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    nodes = sorted(_preview_node_names(spec, variant))
    node_values = {
        node: 0.0 if node == "0" else _scalar(data, f"dc_{node}")
        for node in nodes
    }
    input_source = next(
        source for source in spec.voltage_sources if source.name == spec.input_source
    )
    input_dc_v = (
        node_values[input_source.positive] - node_values[input_source.negative]
    )
    output_dc_v = (
        node_values[variant.output_positive]
        - node_values[variant.output_negative]
    )
    all_sources = [*spec.voltage_sources, *variant.voltage_sources]
    source_values: dict[str, dict[str, float]] = {}
    for source in all_sources:
        actual_voltage = node_values[source.positive] - node_values[source.negative]
        if not math.isclose(
            actual_voltage,
            source.dc_v,
            rel_tol=1e-7,
            abs_tol=1e-8,
        ):
            raise RuntimeError(
                f"preview source {source.name} DC voltage mismatch: "
                f"declared={source.dc_v}, actual={actual_voltage}"
            )
        source_values[source.name] = {
            "voltage_v": actual_voltage,
            "positive_terminal_current_a": _scalar(data, f"dc_{source.name}:p"),
        }

    mos_values: dict[str, dict[str, float | str]] = {}
    margins: list[float] = []
    intrinsic_gains: list[float] = []
    metrics: dict[str, float] = {
        "input_dc_v": input_dc_v,
        "output_dc_v": output_dc_v,
    }
    for mosfet in variant.mosfets:
        ids_a = _operating_point_scalar(data, mosfet.name, "ids", "id")
        vgs_v = _operating_point_scalar(data, mosfet.name, "vgs")
        vds_v = _operating_point_scalar(data, mosfet.name, "vds")
        vbs_v = _operating_point_scalar(data, mosfet.name, "vbs")
        vdsat_v = _operating_point_scalar(data, mosfet.name, "vdsat")
        gm_s = _operating_point_scalar(data, mosfet.name, "gm")
        gds_s = _operating_point_scalar(data, mosfet.name, "gds")
        gmb_s = _operating_point_scalar(data, mosfet.name, "gmb", "gmbs")
        margin_v = abs(vds_v) - abs(vdsat_v)
        intrinsic_gain = abs(gm_s) / max(abs(gds_s), 1e-30)
        prefix = mosfet.name
        device_metrics = {
            f"{prefix}.drain_current_ua": abs(ids_a) * 1e6,
            f"{prefix}.vgs_v": vgs_v,
            f"{prefix}.vds_v": vds_v,
            f"{prefix}.vbs_v": vbs_v,
            f"{prefix}.vdsat_v": vdsat_v,
            f"{prefix}.saturation_margin_v": margin_v,
            f"{prefix}.gm_us": abs(gm_s) * 1e6,
            f"{prefix}.gds_us": abs(gds_s) * 1e6,
            f"{prefix}.gmb_us": abs(gmb_s) * 1e6,
            f"{prefix}.intrinsic_gain_v_per_v": intrinsic_gain,
        }
        metrics.update(device_metrics)
        margins.append(margin_v)
        intrinsic_gains.append(intrinsic_gain)
        mos_values[mosfet.name] = {
            "polarity": mosfet.polarity,
            "ids_a": ids_a,
            "vgs_v": vgs_v,
            "vds_v": vds_v,
            "vbs_v": vbs_v,
            "vdsat_v": vdsat_v,
            "gm_s": gm_s,
            "gds_s": gds_s,
            "gmb_s": gmb_s,
            "saturation_margin_v": margin_v,
        }

    supply_power_w = 0.0
    supply_current_a = 0.0
    for source_name in spec.supply_sources:
        values = source_values[source_name]
        supply_power_w += -values["voltage_v"] * values[
            "positive_terminal_current_a"
        ]
        supply_current_a += -values["positive_terminal_current_a"]
    metrics.update(
        {
            "minimum_saturation_margin_v": min(margins),
            "all_mos_saturation_region": float(
                all(
                    abs(float(values["ids_a"])) > 0.0
                    and float(values["saturation_margin_v"]) >= 0.0
                    for values in mos_values.values()
                )
            ),
            "minimum_intrinsic_gain_v_per_v": min(intrinsic_gains),
            "supply_current_ua": supply_current_a * 1e6,
            "dc_supply_power_uw": supply_power_w * 1e6,
            "gate_area_proxy_um2": sum(
                mosfet.width_um
                * mosfet.length_um
                * mosfet.fingers
                * mosfet.multiplicity
                for mosfet in variant.mosfets
            ),
        }
    )
    if any(not math.isfinite(value) for value in metrics.values()):
        raise RuntimeError(f"preview variant {variant.id} produced non-finite DC metrics")
    return metrics, {
        "node_values_v": node_values,
        "source_values": source_values,
        "mos_operating_points": mos_values,
        "region_rule": "saturation when |VDS| >= |VDSAT| and |IDS| > 0",
        "source_voltage_consistency": "matched",
    }


def _preview_ac_metrics(
    spec: NetlistPreviewSpec,
    variant: NetlistPreviewVariant,
    data: dict[str, Any],
    ac_sweep: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    frequency_hz = _signal(data, "ac_freq")

    def complex_node(node: str) -> list[complex]:
        if node == "0":
            return [0j] * len(frequency_hz)
        return _complex_signal(data, f"ac_{node}")

    input_source = next(
        source for source in spec.voltage_sources if source.name == spec.input_source
    )
    input_v = [
        positive - negative
        for positive, negative in zip(
            complex_node(input_source.positive),
            complex_node(input_source.negative),
            strict=True,
        )
    ]
    output_v = [
        positive - negative
        for positive, negative in zip(
            complex_node(variant.output_positive),
            complex_node(variant.output_negative),
            strict=True,
        )
    ]
    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        input_v,
        output_v,
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
    )
    diagnostics.update(
        {
            "input_nodes": [input_source.positive, input_source.negative],
            "output_nodes": [variant.output_positive, variant.output_negative],
            "transfer": "declared differential output / shared input source voltage",
            "frequency_hz": [float(value) for value in frequency_hz],
        }
    )
    return metrics, diagnostics


def _preview_comparisons(
    spec: NetlistPreviewSpec,
    metrics_by_variant: dict[str, dict[str, float]],
) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
    baseline_id = spec.variants[0].id
    baseline = metrics_by_variant[baseline_id]
    flat_metrics: dict[str, float] = {}
    flat_sources: dict[str, str] = {}
    comparisons: dict[str, Any] = {}

    def metric_name(*parts: str) -> str:
        return "__".join(part.replace(".", "__") for part in parts)

    for variant in spec.variants:
        for name, value in metrics_by_variant[variant.id].items():
            flattened = metric_name(variant.id, name)
            flat_metrics[flattened] = value
            flat_sources[flattened] = (
                "software_inference"
                if name in {"all_mos_saturation_region", "gate_area_proxy_um2"}
                else "eda_result"
            )
        if variant.id == baseline_id:
            continue
        rows: dict[str, Any] = {}
        for name in sorted(set(baseline) & set(metrics_by_variant[variant.id])):
            baseline_value = baseline[name]
            candidate_value = metrics_by_variant[variant.id][name]
            delta = candidate_value - baseline_value
            delta_name = metric_name(
                variant.id,
                "delta_vs",
                baseline_id,
                name,
            )
            flat_metrics[delta_name] = delta
            flat_sources[delta_name] = "software_inference"
            row: dict[str, float] = {
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
            }
            if abs(baseline_value) > 1e-30:
                ratio = candidate_value / baseline_value
                ratio_name = metric_name(
                    variant.id,
                    "ratio_vs",
                    baseline_id,
                    name,
                )
                flat_metrics[ratio_name] = ratio
                flat_sources[ratio_name] = "software_inference"
                row["ratio"] = ratio
            rows[name] = row
        comparisons[variant.id] = rows
    return flat_metrics, flat_sources, {
        "source": "software_inference",
        "baseline_variant": baseline_id,
        "comparisons": comparisons,
    }


def simulate_netlist_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a validated standalone Spectre topology bundle without OA or si."""

    from virtuoso_bridge.spectre.runner import SpectreSimulator
    from virtuoso_bridge.transport.tunnel import SSHClient

    profile = payload["profile"]
    spec = NetlistPreviewSpec.model_validate(payload.get("netlist_preview"))
    analysis = str(payload.get("analysis", ""))
    if analysis not in {"dc", "ac"}:
        raise RuntimeError("netlist preview supports only dc or ac analysis")
    ac_sweep = payload.get("ac_sweep")
    if analysis == "ac" and not isinstance(ac_sweep, dict):
        raise RuntimeError("netlist preview AC simulation requires ac_sweep")
    timeout = int(payload.get("timeout_seconds", 600))
    run_root = str(profile["remote_run_root"]).rstrip("/")
    if not run_root.startswith("/data/xum/"):
        raise RuntimeError("netlist preview remote root must stay under /data/xum")
    task_slug = re.sub(
        r"[^A-Za-z0-9_.-]",
        "_",
        str(payload.get("task_id", "task")),
    )[:48]
    remote_run_root = (
        f"{run_root}/vda_netlist_preview_{task_slug}_{uuid.uuid4().hex[:12]}"
    )
    if not SSHClient.is_running():
        raise RuntimeError("no default virtuoso-bridge connection is running")
    ssh_client = _register_worker_resource(SSHClient.from_env(keep_remote_files=True))
    runner = ssh_client.ssh_runner
    runner._persistent_shell_enabled = False
    remote_root_q = shlex.quote(remote_run_root)
    _ssh_command_result(
        runner,
        f"test ! -e {remote_root_q}",
        "netlist preview non-overwrite preflight",
    )

    variant_results: dict[str, Any] = {}
    metrics_by_variant: dict[str, dict[str, float]] = {}
    tool_versions: set[str] = set()
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vda_netlist_preview_") as temp_dir:
        work_root = Path(temp_dir)
        for variant in spec.variants:
            work_dir = work_root / variant.id
            work_dir.mkdir()
            remote_variant_root = f"{remote_run_root}/{variant.id}"
            deck_path = work_dir / f"preview_{variant.id}.scs"
            deck = render_spectre_preview_deck(
                spec,
                variant.id,
                profile,
                analysis=analysis,
                ac_sweep=ac_sweep if isinstance(ac_sweep, dict) else None,
            )
            deck_path.write_text(deck, encoding="utf-8", newline="\n")
            spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
                ssh_client,
                work_dir,
                remote_variant_root,
                timeout=timeout,
            )
            simulator = _create_spectre_simulator(
                SpectreSimulator,
                ssh_client,
                spectre_cmd=spectre_cmd,
                timeout=timeout,
                work_dir=work_dir,
                keep_remote_files=True,
                remote_run_dir=remote_variant_root,
            )
            result = simulator.run_simulation(deck_path, {})
            if not result.ok:
                detail = _spectre_failure_detail(result, work_dir)
                raise RuntimeError(
                    f"Spectre preview variant {variant.id} failed; retained remote "
                    f"root {remote_variant_root}: {detail}"
                )
            tool_version = str(result.tool_version or "").strip()
            if not tool_version:
                tool_version = _spectre_version_from_log(work_dir)
            tool_versions.add(tool_version)
            warnings.extend(
                f"{variant.id}: {warning}" for warning in result.warnings[:20]
            )
            dc_data, dc_files = _common_source_dc_data_from_result(result)
            metrics, operating_point = _preview_dc_metrics(
                spec,
                variant,
                dc_data,
            )
            analysis_issues: list[str] = []
            analysis_warnings: list[str] = []
            ac_diagnostics: dict[str, Any] | None = None
            analysis_complete = True
            if analysis == "ac":
                assert isinstance(ac_sweep, dict)
                ac_metrics, ac_diagnostics = _preview_ac_metrics(
                    spec,
                    variant,
                    result.data,
                    ac_sweep,
                )
                ac_diagnostics["raw_files"] = _spectre_ac_file_evidence_from_result(
                    result
                )
                metrics.update(ac_metrics)
                analysis_issues.extend(
                    str(value) for value in ac_diagnostics.get("issues", [])
                )
                analysis_warnings.extend(
                    str(value) for value in ac_diagnostics.get("warnings", [])
                )
                analysis_complete = bool(
                    ac_diagnostics.get("analysis_complete", False)
                ) and not analysis_issues
            manifest, manifest_sha256 = _spectre_artifact_manifest(work_dir)
            remote_children = [
                line.strip()
                for line in _ssh_command_result(
                    runner,
                    f"find {shlex.quote(remote_variant_root)} -mindepth 1 "
                    "-maxdepth 1 -type d -print",
                    f"preview variant {variant.id} remote artifact discovery",
                ).splitlines()
                if line.strip()
            ]
            if len(remote_children) != 1:
                raise RuntimeError(
                    f"preview variant {variant.id} expected exactly one remote "
                    f"simulator directory; found {remote_children}"
                )
            metrics_by_variant[variant.id] = metrics
            variant_results[variant.id] = {
                "analysis_complete": analysis_complete,
                "analysis_issues": analysis_issues,
                "analysis_warnings": analysis_warnings,
                "metrics": metrics,
                "operating_point": {
                    "source": "eda_result",
                    "raw_files": dc_files,
                    **operating_point,
                },
                "ac_response": (
                    {"source": "eda_result", **ac_diagnostics}
                    if ac_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "deck_sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                "artifact_manifest": manifest,
                "manifest_sha256": manifest_sha256,
                "remote_run_root": remote_variant_root,
                "remote_simulation_dir": remote_children[0],
                "process_lifecycle": process_lifecycle,
            }

    if len(tool_versions) != 1:
        raise RuntimeError(
            f"preview variants did not use one Spectre version: {sorted(tool_versions)}"
        )
    metrics, metric_sources, comparison = _preview_comparisons(
        spec,
        metrics_by_variant,
    )
    analysis_issues = [
        f"{variant_id}: {issue}"
        for variant_id, data in variant_results.items()
        for issue in data["analysis_issues"]
    ]
    analysis_warnings = [
        f"{variant_id}: {warning}"
        for variant_id, data in variant_results.items()
        for warning in data["analysis_warnings"]
    ]
    return {
        "parameters": {},
        "metrics": metrics,
        "metric_sources": metric_sources,
        "analysis_complete": all(
            data["analysis_complete"] for data in variant_results.values()
        ),
        "analysis_issues": analysis_issues,
        "analysis_warnings": analysis_warnings,
        "tool_version": next(iter(tool_versions)),
        "warnings": warnings,
        "evidence": {
            "source": "eda_result",
            "preview_spec_sha256": spec.canonical_sha256(),
            "preview_spec_source": "user_input",
            "source_bindings": dict(spec.source_bindings),
            "source_bindings_evidence_source": (
                spec.source_bindings_evidence_source
            ),
            "variant_source_ids": dict(spec.variant_source_ids),
            "variant_source_ids_evidence_source": (
                spec.variant_source_ids_evidence_source
            ),
            "analysis": analysis,
            "analysis_source": str(payload.get("analysis_source", "user_input")),
            "pdk_profile": str(profile["name"]),
            "process_corner": str(profile["model_section"]),
            "remote_run_root": remote_run_root,
            "non_overwrite_preflight": "absent",
            "variants": variant_results,
            "comparison": comparison,
            "netlist_source": "validated_structured_preview_spec",
            "oa_access_performed": False,
            "oa_write_performed": False,
            "si_netlisting_performed": False,
            "maestro_access_performed": False,
            "remote_compute_performed": True,
            "completion_scope": (
                "preliminary topology screening only; OA-to-si-to-Spectre or ADE "
                "validation remains required"
            ),
        },
    }


def _spectre_ac_file_evidence_from_result(result: Any) -> dict[str, Any]:
    raw_output_dir = getattr(result, "metadata", {}).get("output_dir")
    if not raw_output_dir:
        raise RuntimeError("Spectre result is missing its downloaded PSF path")
    output_dir = Path(str(raw_output_dir))
    ac_file = _select_shallow_psf_file(
        output_dir,
        ("ac.ac", "ac.ac.ac"),
        label="AC",
    )
    ac_bytes = ac_file.read_bytes()
    return {
        "selection": "shallowest analysis-specific PSF file",
        "ac": {
            "relative_path": ac_file.relative_to(output_dir).as_posix(),
            "size_bytes": len(ac_bytes),
            "sha256": hashlib.sha256(ac_bytes).hexdigest(),
        },
    }


def _common_source_metrics_from_result(
    data: dict[str, Any], parameters: dict[str, float]
) -> tuple[dict[str, float], dict[str, Any]]:
    source_degenerated = "source_resistance_ohm" in parameters
    cascode = "cascode_bias_v" in parameters
    if source_degenerated and cascode:
        raise RuntimeError(
            "common-source DC extraction does not combine source degeneration "
            "and cascode devices in one topology Gate"
        )
    node_values = {
        "IN": _scalar(data, "dc_IN"),
        "OUT": _scalar(data, "dc_OUT"),
        "VDD": _scalar(data, "dc_VDD"),
        "VSS": _scalar(data, "dc_VSS"),
    }
    if source_degenerated:
        node_values["NSRC"] = _scalar(data, "dc_NSRC")
    elif cascode:
        node_values["NCAS"] = _scalar(data, "dc_NCAS")
        node_values["VCAS"] = _scalar(data, "dc_VCAS")
    op_values = {
        "ids_a": _operating_point_scalar(data, "MN0", "ids", "id"),
        "vgs_v": _operating_point_scalar(data, "MN0", "vgs"),
        "vds_v": _operating_point_scalar(data, "MN0", "vds"),
        "vdsat_v": _operating_point_scalar(data, "MN0", "vdsat"),
        "gm_s": _operating_point_scalar(data, "MN0", "gm"),
        "gds_s": _operating_point_scalar(data, "MN0", "gds"),
        "supply_source_current_a": _scalar(data, "dc_VDD_SRC:p"),
    }
    cascode_op_values: dict[str, float] | None = None
    if cascode:
        cascode_op_values = {
            "ids_a": _operating_point_scalar(data, "MNCAS", "ids", "id"),
            "vgs_v": _operating_point_scalar(data, "MNCAS", "vgs"),
            "vds_v": _operating_point_scalar(data, "MNCAS", "vds"),
            "vdsat_v": _operating_point_scalar(data, "MNCAS", "vdsat"),
            "gm_s": _operating_point_scalar(data, "MNCAS", "gm"),
            "gds_s": _operating_point_scalar(data, "MNCAS", "gds"),
        }
    source_tolerance_v = 1e-5
    if abs(node_values["VDD"] - parameters["vdd_v"]) > source_tolerance_v:
        raise RuntimeError("DC VDD does not match the testbench source value")
    if abs(node_values["IN"] - parameters["bias_v"]) > source_tolerance_v:
        raise RuntimeError("DC input does not match the testbench bias value")
    if abs(node_values["VSS"]) > source_tolerance_v:
        raise RuntimeError("DC VSS does not match the testbench source value")
    if cascode and abs(
        node_values["VCAS"] - parameters["cascode_bias_v"]
    ) > source_tolerance_v:
        raise RuntimeError("DC VCAS does not match the testbench source value")

    mos_source_v = node_values["NSRC"] if source_degenerated else node_values["VSS"]
    node_vgs_v = node_values["IN"] - mos_source_v
    node_vds_v = (
        node_values["NCAS"] - mos_source_v
        if cascode
        else node_values["OUT"] - mos_source_v
    )
    for name, node_value in (("vgs_v", node_vgs_v), ("vds_v", node_vds_v)):
        tolerance = max(abs(node_value) * 1e-4, 1e-5)
        if abs(abs(op_values[name]) - abs(node_value)) > tolerance:
            raise RuntimeError(
                f"DC node/device mismatch for {name}: node={node_value:.12g}, "
                f"device={op_values[name]:.12g}"
            )

    if cascode:
        assert cascode_op_values is not None
        cascode_node_values = {
            "vgs_v": node_values["VCAS"] - node_values["NCAS"],
            "vds_v": node_values["OUT"] - node_values["NCAS"],
        }
        for name, node_value in cascode_node_values.items():
            tolerance = max(abs(node_value) * 1e-4, 1e-5)
            if abs(abs(cascode_op_values[name]) - abs(node_value)) > tolerance:
                raise RuntimeError(
                    f"DC node/device mismatch for MNCAS {name}: "
                    f"node={node_value:.12g}, "
                    f"device={cascode_op_values[name]:.12g}"
                )

    if cascode:
        assert cascode_op_values is not None
        values = [
            *node_values.values(),
            *op_values.values(),
            *cascode_op_values.values(),
            parameters["load_resistance_ohm"],
        ]
        if any(not math.isfinite(float(value)) for value in values):
            raise RuntimeError("cascode operating-point values must be finite")
        if parameters["load_resistance_ohm"] <= 0.0:
            raise RuntimeError("cascode load resistance must be positive")
        if abs(op_values["gds_s"]) <= 0.0 or abs(cascode_op_values["gds_s"]) <= 0.0:
            raise RuntimeError("cascode device gds values must be non-zero")
        lower_current_a = abs(op_values["ids_a"])
        upper_current_a = abs(cascode_op_values["ids_a"])
        resistor_current_a = (
            node_values["VDD"] - node_values["OUT"]
        ) / parameters["load_resistance_ohm"]
        lower_margin_v = abs(op_values["vds_v"]) - abs(op_values["vdsat_v"])
        upper_margin_v = abs(cascode_op_values["vds_v"]) - abs(
            cascode_op_values["vdsat_v"]
        )
        stack_headroom_v = (
            node_values["OUT"]
            - node_values["VSS"]
            - abs(op_values["vdsat_v"])
            - abs(cascode_op_values["vdsat_v"])
        )
        upper_headroom_v = node_values["VDD"] - node_values["OUT"]
        resistor_scale_a = max(upper_current_a, abs(resistor_current_a), 1e-18)
        metrics = {
            "drain_current_ua": lower_current_a * 1e6,
            "cascode_drain_current_ua": upper_current_a * 1e6,
            "vgs_v": node_vgs_v,
            "vds_v": node_vds_v,
            "vdsat_v": abs(op_values["vdsat_v"]),
            "input_device_saturation_margin_v": lower_margin_v,
            "cascode_vgs_v": cascode_node_values["vgs_v"],
            "cascode_vds_v": cascode_node_values["vds_v"],
            "cascode_vdsat_v": abs(cascode_op_values["vdsat_v"]),
            "cascode_saturation_margin_v": upper_margin_v,
            "saturation_margin_v": min(lower_margin_v, upper_margin_v),
            "upper_output_headroom_v": upper_headroom_v,
            "lower_saturation_headroom_v": stack_headroom_v,
            "output_swing_margin_v": min(upper_headroom_v, stack_headroom_v),
            "cascode_node_v": node_values["NCAS"],
            "cascode_bias_v": node_values["VCAS"],
            "gm_us": abs(op_values["gm_s"]) * 1e6,
            "gds_us": abs(op_values["gds_s"]) * 1e6,
            "intrinsic_gain_v_per_v": abs(op_values["gm_s"])
            / abs(op_values["gds_s"]),
            "cascode_gm_us": abs(cascode_op_values["gm_s"]) * 1e6,
            "cascode_gds_us": abs(cascode_op_values["gds_s"]) * 1e6,
            "cascode_intrinsic_gain_v_per_v": abs(cascode_op_values["gm_s"])
            / abs(cascode_op_values["gds_s"]),
            "resistor_current_ua": abs(resistor_current_a) * 1e6,
            "current_mismatch_percent": abs(
                upper_current_a - abs(resistor_current_a)
            )
            / resistor_scale_a
            * 100.0,
            "cascode_current_mismatch_percent": abs(
                lower_current_a - upper_current_a
            )
            / max(lower_current_a, upper_current_a, 1e-18)
            * 100.0,
            "saturation_region": float(
                lower_current_a > 0.0
                and upper_current_a > 0.0
                and lower_margin_v >= 0.0
                and upper_margin_v >= 0.0
            ),
        }
    else:
        metrics = extract_common_source_dc_metrics(
            vdd_v=node_values["VDD"],
            vin_v=node_values["IN"],
            vout_v=node_values["OUT"],
            vss_v=mos_source_v,
            drain_current_a=op_values["ids_a"],
            vdsat_v=op_values["vdsat_v"],
            gm_s=op_values["gm_s"],
            gds_s=op_values["gds_s"],
            load_resistance_ohm=parameters["load_resistance_ohm"],
        )
    metrics.update(
        extract_dc_supply_metrics(
            vdd_v=node_values["VDD"],
            supply_source_current_a=op_values["supply_source_current_a"],
        )
    )
    supply_current_a = -float(op_values["supply_source_current_a"])
    supply_device_current_a = (
        abs(cascode_op_values["ids_a"])
        if cascode_op_values is not None
        else abs(op_values["ids_a"])
    )
    supply_scale_a = max(supply_device_current_a, abs(supply_current_a), 1e-18)
    supply_mismatch = (
        abs(supply_device_current_a - abs(supply_current_a))
        / supply_scale_a
        * 100.0
    )
    metrics["supply_current_mismatch_percent"] = supply_mismatch
    if metrics["current_mismatch_percent"] > 1.0:
        raise RuntimeError(
            "DC KCL mismatch between drain-stack ids and RD0 current: "
            f"{metrics['current_mismatch_percent']:.6g}%"
        )
    if cascode and metrics["cascode_current_mismatch_percent"] > 1.0:
        raise RuntimeError(
            "DC KCL mismatch between MN0 and MNCAS ids: "
            f"{metrics['cascode_current_mismatch_percent']:.6g}%"
        )
    if supply_mismatch > 1.0:
        raise RuntimeError(
            "DC KCL mismatch between MN0 ids and VDD source current: "
            f"{supply_mismatch:.6g}%"
        )
    if source_degenerated:
        source_resistance = float(parameters["source_resistance_ohm"])
        source_drop_v = node_values["NSRC"] - node_values["VSS"]
        source_current_a = source_drop_v / source_resistance
        current_scale_a = max(
            abs(op_values["ids_a"]), abs(source_current_a), 1e-18
        )
        source_mismatch = (
            abs(abs(op_values["ids_a"]) - abs(source_current_a))
            / current_scale_a
            * 100.0
        )
        metrics.update(
            {
                "source_voltage_v": node_values["NSRC"],
                "source_degeneration_drop_v": source_drop_v,
                "source_resistor_current_ua": abs(source_current_a) * 1e6,
                "source_current_mismatch_percent": source_mismatch,
            }
        )
        if source_mismatch > 1.0:
            raise RuntimeError(
                "DC KCL mismatch between MN0 ids and RS0 current: "
                f"{source_mismatch:.6g}%"
            )
    op_values.update(_optional_mos_small_signal_operating_point(data, "MN0"))
    if cascode_op_values is not None:
        cascode_op_values.update(
            _optional_mos_small_signal_operating_point(data, "MNCAS")
        )
    operating_region = (
        "saturation" if metrics["saturation_region"] == 1.0 else "non_saturation"
    )
    return metrics, {
        "node_values_v": node_values,
        "device_values": (
            {
                "MN0": {
                    name: value
                    for name, value in op_values.items()
                    if name != "supply_source_current_a"
                },
                "MNCAS": cascode_op_values,
                "supply_source_current_a": op_values["supply_source_current_a"],
            }
            if cascode
            else op_values
        ),
        "operating_region": operating_region,
        "region_rule": (
            "both MN0 and MNCAS require |VDS| >= |VDSAT| and |IDS| > 0"
            if cascode
            else "saturation when |VDS| >= |VDSAT| and |IDS| > 0"
        ),
        "device_operating_regions": (
            {
                "MN0": (
                    "saturation"
                    if metrics["input_device_saturation_margin_v"] >= 0.0
                    else "non_saturation"
                ),
                "MNCAS": (
                    "saturation"
                    if metrics["cascode_saturation_margin_v"] >= 0.0
                    else "non_saturation"
                ),
            }
            if cascode
            else {"MN0": operating_region}
        ),
        "node_device_consistency": "matched",
        "kcl_consistency": "matched",
        "source_degeneration_consistency": (
            "matched" if source_degenerated else "not_applicable"
        ),
        "cascode_stack_consistency": "matched" if cascode else "not_applicable",
    }


def _differential_pair_metrics_from_result(
    data: dict[str, Any],
    parameters: dict[str, float],
    topology_variant: str = "resistive_load_nmos_differential_pair",
) -> tuple[dict[str, float], dict[str, Any]]:
    source_degenerated = _differential_pair_has_source_degeneration(
        topology_variant
    )
    current_mirror_load = _differential_pair_has_current_mirror_load(
        topology_variant
    )
    saved_node_names = ["INP", "INN", "OUTP", "OUTN", "TAIL", "VDD", "VSS"]
    if source_degenerated:
        saved_node_names.extend(["NSP", "NSN"])
    node_values = {
        name: _scalar(data, f"dc_{name}")
        for name in saved_node_names
    }
    real_tail = _differential_pair_has_real_tail(topology_variant)
    if real_tail:
        node_values["BIAS"] = _scalar(data, "dc_BIAS")
    branch_values: dict[str, dict[str, float]] = {}
    for instance in ("MN0", "MN1"):
        branch_values[instance] = {
            "ids_a": _operating_point_scalar(data, instance, "ids", "id"),
            "vgs_v": _operating_point_scalar(data, instance, "vgs"),
            "vds_v": _operating_point_scalar(data, instance, "vds"),
            "vdsat_v": _operating_point_scalar(data, instance, "vdsat"),
            "gm_s": _operating_point_scalar(data, instance, "gm"),
            "gds_s": _operating_point_scalar(data, instance, "gds"),
            **_optional_mos_small_signal_operating_point(data, instance),
        }
    load_values: dict[str, dict[str, float]] = {}
    if current_mirror_load:
        for instance in ("MP0", "MP1"):
            load_values[instance] = {
                "ids_a": _operating_point_scalar(data, instance, "ids", "id"),
                "vgs_v": _operating_point_scalar(data, instance, "vgs"),
                "vds_v": _operating_point_scalar(data, instance, "vds"),
                "vdsat_v": _operating_point_scalar(data, instance, "vdsat"),
                "gm_s": _operating_point_scalar(data, instance, "gm"),
                "gds_s": _operating_point_scalar(data, instance, "gds"),
                **_optional_mos_small_signal_operating_point(data, instance),
            }
    tail_device_values: dict[str, float] | None = None
    tail_output_resistance = parameters.get("tail_output_resistance_ohm")
    if real_tail:
        tail_device_values = {
            "ids_a": _operating_point_scalar(data, "MNTAIL", "ids", "id"),
            "vgs_v": _operating_point_scalar(data, "MNTAIL", "vgs"),
            "vds_v": _operating_point_scalar(data, "MNTAIL", "vds"),
            "vdsat_v": _operating_point_scalar(data, "MNTAIL", "vdsat"),
            "gm_s": _operating_point_scalar(data, "MNTAIL", "gm"),
            "gds_s": _operating_point_scalar(data, "MNTAIL", "gds"),
            **_optional_mos_small_signal_operating_point(data, "MNTAIL"),
        }
        expected_tail_current_a = abs(tail_device_values["ids_a"])
        source_values = {
            "supply_source_current_a": _scalar(data, "dc_VDD_SRC:p"),
            "tail_device_current_a": tail_device_values["ids_a"],
            "total_tail_sink_current_a": expected_tail_current_a,
        }
        ideal_tail_current_a = None
        tail_resistor_current_a = 0.0
    else:
        ideal_tail_current_a = parameters["tail_current_ua"] * 1e-6
        tail_resistor_current_a = (
            node_values["TAIL"] / tail_output_resistance
            if tail_output_resistance is not None
            else 0.0
        )
        expected_tail_current_a = ideal_tail_current_a + tail_resistor_current_a
        source_values = {
            "supply_source_current_a": _scalar(data, "dc_VDD_SRC:p"),
            "ideal_tail_source_setpoint_a": ideal_tail_current_a,
            "tail_output_resistor_current_a": tail_resistor_current_a,
            "total_tail_sink_current_a": expected_tail_current_a,
        }
    source_tolerance_v = 1e-5
    expected_nodes = {
        "VDD": parameters["vdd_v"],
        "VSS": 0.0,
        "INP": parameters["common_mode_v"],
        "INN": parameters["common_mode_v"],
    }
    if real_tail:
        expected_nodes["BIAS"] = parameters["tail_bias_v"]
    for name, expected in expected_nodes.items():
        if abs(node_values[name] - expected) > source_tolerance_v:
            raise RuntimeError(
                f"DC {name} does not match the differential-pair testbench "
                f"source value: expected {expected:.12g}, got "
                f"{node_values[name]:.12g}"
            )

    branch_nodes = {
        "MN0": (
            node_values["INP"],
            node_values["OUTP"],
            node_values["NSP"] if source_degenerated else node_values["TAIL"],
        ),
        "MN1": (
            node_values["INN"],
            node_values["OUTN"],
            node_values["NSN"] if source_degenerated else node_values["TAIL"],
        ),
    }
    for instance, (gate_v, drain_v, source_v) in branch_nodes.items():
        expected_vgs = gate_v - source_v
        expected_vds = drain_v - source_v
        for quantity, expected in (("vgs_v", expected_vgs), ("vds_v", expected_vds)):
            actual = branch_values[instance][quantity]
            tolerance = max(abs(expected) * 1e-4, 1e-5)
            if abs(abs(actual) - abs(expected)) > tolerance:
                raise RuntimeError(
                    f"DC node/device mismatch for {instance}.{quantity}: "
                    f"node={expected:.12g}, device={actual:.12g}"
                )

    if tail_device_values is not None:
        for quantity, expected in (
            ("vgs_v", node_values["BIAS"] - node_values["VSS"]),
            ("vds_v", node_values["TAIL"] - node_values["VSS"]),
        ):
            actual = tail_device_values[quantity]
            tolerance = max(abs(expected) * 1e-4, 1e-5)
            if abs(abs(actual) - abs(expected)) > tolerance:
                raise RuntimeError(
                    f"DC node/device mismatch for MNTAIL.{quantity}: "
                    f"node={expected:.12g}, device={actual:.12g}"
                )

    if current_mirror_load:
        for instance, drain_node in (("MP0", "OUTP"), ("MP1", "OUTN")):
            for quantity, expected in (
                ("vgs_v", node_values["OUTP"] - node_values["VDD"]),
                ("vds_v", node_values[drain_node] - node_values["VDD"]),
            ):
                actual = load_values[instance][quantity]
                tolerance = max(abs(expected) * 1e-4, 1e-5)
                if abs(abs(actual) - abs(expected)) > tolerance:
                    raise RuntimeError(
                        f"DC node/device mismatch for {instance}.{quantity}: "
                        f"node={expected:.12g}, device={actual:.12g}"
                    )

    metrics = extract_differential_pair_dc_metrics(
        vdd_v=node_values["VDD"],
        common_mode_v=0.5 * (node_values["INP"] + node_values["INN"]),
        outp_v=node_values["OUTP"],
        outn_v=node_values["OUTN"],
        tail_v=node_values["TAIL"],
        branch_p_current_a=branch_values["MN0"]["ids_a"],
        branch_n_current_a=branch_values["MN1"]["ids_a"],
        tail_source_current_a=expected_tail_current_a,
        supply_source_current_a=source_values["supply_source_current_a"],
        branch_p_vdsat_v=branch_values["MN0"]["vdsat_v"],
        branch_n_vdsat_v=branch_values["MN1"]["vdsat_v"],
        branch_p_gm_s=branch_values["MN0"]["gm_s"],
        branch_n_gm_s=branch_values["MN1"]["gm_s"],
        branch_p_gds_s=branch_values["MN0"]["gds_s"],
        branch_n_gds_s=branch_values["MN1"]["gds_s"],
        load_resistance_ohm=parameters.get("load_resistance_ohm"),
        branch_p_source_v=branch_nodes["MN0"][2],
        branch_n_source_v=branch_nodes["MN1"][2],
        load_p_current_a=(
            load_values["MP0"]["ids_a"] if current_mirror_load else None
        ),
        load_n_current_a=(
            load_values["MP1"]["ids_a"] if current_mirror_load else None
        ),
        load_p_vdsat_v=(
            load_values["MP0"]["vdsat_v"] if current_mirror_load else None
        ),
        load_n_vdsat_v=(
            load_values["MP1"]["vdsat_v"] if current_mirror_load else None
        ),
        load_p_gm_s=(load_values["MP0"]["gm_s"] if current_mirror_load else None),
        load_n_gm_s=(load_values["MP1"]["gm_s"] if current_mirror_load else None),
        load_p_gds_s=(
            load_values["MP0"]["gds_s"] if current_mirror_load else None
        ),
        load_n_gds_s=(
            load_values["MP1"]["gds_s"] if current_mirror_load else None
        ),
    )
    if source_degenerated:
        source_resistance = float(parameters["source_resistance_ohm"])
        source_p_drop_v = node_values["NSP"] - node_values["TAIL"]
        source_n_drop_v = node_values["NSN"] - node_values["TAIL"]
        source_p_current_a = source_p_drop_v / source_resistance
        source_n_current_a = source_n_drop_v / source_resistance
        source_p_scale_a = max(
            abs(branch_values["MN0"]["ids_a"]),
            abs(source_p_current_a),
            1e-18,
        )
        source_n_scale_a = max(
            abs(branch_values["MN1"]["ids_a"]),
            abs(source_n_current_a),
            1e-18,
        )
        source_p_mismatch = (
            abs(abs(branch_values["MN0"]["ids_a"]) - abs(source_p_current_a))
            / source_p_scale_a
            * 100.0
        )
        source_n_mismatch = (
            abs(abs(branch_values["MN1"]["ids_a"]) - abs(source_n_current_a))
            / source_n_scale_a
            * 100.0
        )
        metrics.update(
            {
                "source_resistance_ohm": source_resistance,
                "source_p_voltage_v": node_values["NSP"],
                "source_n_voltage_v": node_values["NSN"],
                "source_p_degeneration_drop_v": source_p_drop_v,
                "source_n_degeneration_drop_v": source_n_drop_v,
                "source_p_resistor_current_ua": abs(source_p_current_a) * 1e6,
                "source_n_resistor_current_ua": abs(source_n_current_a) * 1e6,
                "source_p_current_mismatch_percent": source_p_mismatch,
                "source_n_current_mismatch_percent": source_n_mismatch,
                "max_source_current_mismatch_percent": max(
                    source_p_mismatch, source_n_mismatch
                ),
            }
        )
        if metrics["max_source_current_mismatch_percent"] > 1.0:
            raise RuntimeError(
                "differential-pair DC KCL mismatch across RS0/RS1: "
                f"{metrics['max_source_current_mismatch_percent']:.6g}%"
            )
    if tail_device_values is not None:
        tail_margin_v = abs(tail_device_values["vds_v"]) - abs(
            tail_device_values["vdsat_v"]
        )
        metrics.update(
            {
                "tail_device_current_ua": abs(tail_device_values["ids_a"]) * 1e6,
                "tail_device_vgs_v": abs(tail_device_values["vgs_v"]),
                "tail_device_vds_v": abs(tail_device_values["vds_v"]),
                "tail_device_vdsat_v": abs(tail_device_values["vdsat_v"]),
                "tail_device_gm_us": abs(tail_device_values["gm_s"]) * 1e6,
                "tail_device_gds_us": abs(tail_device_values["gds_s"]) * 1e6,
                "tail_device_saturation_margin_v": tail_margin_v,
                "tail_device_saturation_region": (
                    1.0
                    if abs(tail_device_values["ids_a"]) > 0.0
                    and tail_margin_v >= 0.0
                    else 0.0
                ),
                "tail_device_branch_sum_mismatch_percent": metrics[
                    "tail_current_mismatch_percent"
                ],
            }
        )
    else:
        assert ideal_tail_current_a is not None
        metrics["ideal_tail_source_current_ua"] = ideal_tail_current_a * 1e6
    if not real_tail and tail_output_resistance is not None:
        metrics.update(
            {
                "tail_output_resistance_ohm": tail_output_resistance,
                "tail_output_resistor_current_ua": (
                    abs(tail_resistor_current_a) * 1e6
                ),
            }
        )
    kcl_metrics = (
        "tail_current_mismatch_percent",
        "supply_current_mismatch_percent",
        "max_load_current_mismatch_percent",
    )
    for metric in kcl_metrics:
        if metrics[metric] > 1.0:
            raise RuntimeError(
                f"differential-pair DC KCL mismatch in {metric}: "
                f"{metrics[metric]:.6g}%"
            )
    operating_regions = {
        "MN0": (
            "saturation"
            if abs(branch_values["MN0"]["ids_a"]) > 0.0
            and metrics["branch_p_saturation_margin_v"] >= 0.0
            else "non_saturation"
        ),
        "MN1": (
            "saturation"
            if abs(branch_values["MN1"]["ids_a"]) > 0.0
            and metrics["branch_n_saturation_margin_v"] >= 0.0
            else "non_saturation"
        ),
    }
    if tail_device_values is not None:
        operating_regions["MNTAIL"] = (
            "saturation"
            if metrics["tail_device_saturation_region"] == 1.0
            else "non_saturation"
        )
    if current_mirror_load:
        operating_regions.update(
            {
                "MP0": (
                    "saturation"
                    if abs(load_values["MP0"]["ids_a"]) > 0.0
                    and metrics["load_p_saturation_margin_v"] >= 0.0
                    else "non_saturation"
                ),
                "MP1": (
                    "saturation"
                    if abs(load_values["MP1"]["ids_a"]) > 0.0
                    and metrics["load_n_saturation_margin_v"] >= 0.0
                    else "non_saturation"
                ),
            }
        )
    device_values: dict[str, Any] = dict(branch_values)
    if tail_device_values is not None:
        device_values["MNTAIL"] = tail_device_values
    device_values.update(load_values)
    return metrics, {
        "node_values_v": node_values,
        "device_values": device_values,
        "source_values_a": source_values,
        "tail_source_binding": (
            "oa_mntail_with_external_bias_voltage"
            if real_tail
            else "ideal_isource_plus_explicit_output_resistor"
            if tail_output_resistance is not None
            else "ideal_isource_dc_setpoint"
        ),
        "operating_regions": operating_regions,
        "region_rule": "saturation when |VDS| >= |VDSAT| and |IDS| > 0",
        "node_device_consistency": "matched",
        "tail_source_consistency": (
            "branch_sum_matches_oa_mntail_ids"
            if real_tail
            else "branch_sum_matches_ideal_source_plus_output_resistor"
            if tail_output_resistance is not None
            else "branch_sum_matches_declared_ideal_source"
        ),
        "kcl_consistency": "matched",
        "source_degeneration_consistency": (
            "matched" if source_degenerated else "not_applicable"
        ),
        "load_consistency": (
            "MN0/MN1 branch currents match MP0/MP1 currents and VDD source"
            if current_mirror_load
            else "MN0/MN1 branch currents match RD0/RD1 voltage-derived currents"
        ),
    }


def _common_source_ac_metrics_from_result(
    data: dict[str, Any], ac_sweep: dict[str, Any]
) -> tuple[dict[str, float], dict[str, Any]]:
    frequency_hz = _signal(data, "ac_freq")
    vin_v = _complex_signal(data, "ac_IN")
    vout_v = _complex_signal(data, "ac_OUT")
    metrics, diagnostics = extract_common_source_ac_metrics(
        frequency_hz,
        vin_v,
        vout_v,
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
    )
    diagnostics["signals"] = ["ac_freq", "ac_IN", "ac_OUT"]
    diagnostics["transfer"] = "VOUT/VIN complex ratio"
    diagnostics["frequency_hz"] = [float(value) for value in frequency_hz]
    return metrics, diagnostics


def _differential_pair_ac_metrics_from_result(
    data: dict[str, Any],
    ac_sweep: dict[str, Any],
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    frequency_hz = _signal(data, "ac_freq")
    metrics, diagnostics = extract_differential_pair_ac_metrics(
        frequency_hz,
        _complex_signal(data, "ac_INP"),
        _complex_signal(data, "ac_INN"),
        _complex_signal(data, "ac_OUTP"),
        _complex_signal(data, "ac_OUTN"),
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
        output_mode=(
            "single_ended_outn"
            if _differential_pair_has_current_mirror_load(topology_variant)
            else "differential"
        ),
    )
    diagnostics["frequency_hz"] = [float(value) for value in frequency_hz]
    return metrics, diagnostics


def _differential_pair_common_mode_ac_metrics_from_result(
    data: dict[str, Any],
    ac_sweep: dict[str, Any],
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    return extract_differential_pair_common_mode_ac_metrics(
        _signal(data, "ac_freq"),
        _complex_signal(data, "ac_INP"),
        _complex_signal(data, "ac_INN"),
        _complex_signal(data, "ac_OUTP"),
        _complex_signal(data, "ac_OUTN"),
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
        output_mode=(
            "single_ended_outn"
            if _differential_pair_has_current_mirror_load(topology_variant)
            else "differential"
        ),
    )


def _differential_pair_cmrr_metrics_from_results(
    differential_data: dict[str, Any],
    common_mode_data: dict[str, Any],
    ac_sweep: dict[str, Any],
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    return extract_differential_pair_cmrr_response_metrics(
        _signal(differential_data, "ac_freq"),
        _complex_signal(differential_data, "ac_INP"),
        _complex_signal(differential_data, "ac_INN"),
        _complex_signal(differential_data, "ac_OUTP"),
        _complex_signal(differential_data, "ac_OUTN"),
        _signal(common_mode_data, "ac_freq"),
        _complex_signal(common_mode_data, "ac_INP"),
        _complex_signal(common_mode_data, "ac_INN"),
        _complex_signal(common_mode_data, "ac_OUTP"),
        _complex_signal(common_mode_data, "ac_OUTN"),
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
        output_mode=(
            "single_ended_outn"
            if _differential_pair_has_current_mirror_load(topology_variant)
            else "differential"
        ),
    )


def _differential_pair_psrr_metrics_from_results(
    differential_data: dict[str, Any],
    positive_supply_data: dict[str, Any],
    negative_supply_data: dict[str, Any],
    ac_sweep: dict[str, Any],
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    return extract_differential_pair_psrr_metrics(
        _signal(differential_data, "ac_freq"),
        _complex_signal(differential_data, "ac_INP"),
        _complex_signal(differential_data, "ac_INN"),
        _complex_signal(differential_data, "ac_OUTP"),
        _complex_signal(differential_data, "ac_OUTN"),
        _signal(positive_supply_data, "ac_freq"),
        _complex_signal(positive_supply_data, "ac_VDD"),
        _complex_signal(positive_supply_data, "ac_OUTP"),
        _complex_signal(positive_supply_data, "ac_OUTN"),
        _signal(negative_supply_data, "ac_freq"),
        _complex_signal(negative_supply_data, "ac_VSS"),
        _complex_signal(negative_supply_data, "ac_OUTP"),
        _complex_signal(negative_supply_data, "ac_OUTN"),
        reference_points=int(ac_sweep.get("reference_points", 5)),
        max_reference_variation_db=float(
            ac_sweep.get("max_reference_variation_db", 0.5)
        ),
        evaluation_stop_hz=(
            float(ac_sweep["evaluation_stop_hz"])
            if ac_sweep.get("evaluation_stop_hz") is not None
            else None
        ),
        output_mode=(
            "single_ended_outn"
            if _differential_pair_has_current_mirror_load(topology_variant)
            else "differential"
        ),
    )


def _common_source_linearity_metrics_from_result(
    metadata: dict[str, Any],
    linearity_sweep: dict[str, Any],
    *,
    vdd_v: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    raw_points = metadata.get("sweep_points")
    if not isinstance(raw_points, dict) or not raw_points:
        raise RuntimeError("Spectre linearity sweep returned no transient points")
    amplitudes = [float(value) for value in linearity_sweep["amplitudes_v"]]
    if len(raw_points) != len(amplitudes):
        raise RuntimeError(
            "Spectre linearity sweep point count does not match declared amplitudes"
        )
    point_metrics: list[dict[str, float]] = []
    point_diagnostics: list[dict[str, Any]] = []
    for index, amplitude in enumerate(amplitudes, start=1):
        raw_point = raw_points.get(index, raw_points.get(str(index)))
        if not isinstance(raw_point, dict):
            raise RuntimeError(f"Spectre linearity sweep is missing point {index}")
        metrics, diagnostics = extract_common_source_linearity_point_metrics(
            _signal(raw_point, "time"),
            _signal(raw_point, "IN"),
            _signal(raw_point, "OUT"),
            _signal(raw_point, "VDD_SRC:p"),
            vdd_v=vdd_v,
            frequency_hz=float(linearity_sweep["frequency_hz"]),
            settling_cycles=int(linearity_sweep.get("settling_cycles", 4)),
            measurement_cycles=int(linearity_sweep.get("measurement_cycles", 8)),
            max_harmonic=int(linearity_sweep.get("max_harmonic", 5)),
        )
        tolerance = max(amplitude * 5e-3, 1e-8)
        if abs(metrics["input_fundamental_v_peak"] - amplitude) > tolerance:
            raise RuntimeError(
                "Spectre input fundamental does not match declared sweep amplitude "
                f"at point {index}"
            )
        point_metrics.append(metrics)
        point_diagnostics.append(
            {
                "index": index,
                "declared_input_amplitude_v_peak": amplitude,
                "signals": ["time", "IN", "OUT", "VDD_SRC:p"],
                "metrics": metrics,
                **diagnostics,
            }
        )
    metrics, diagnostics = aggregate_common_source_linearity_metrics(
        amplitudes,
        point_metrics,
        compression_db=float(linearity_sweep.get("compression_db", 1.0)),
    )
    diagnostics["point_details"] = point_diagnostics
    diagnostics["sweep_point_count"] = len(point_metrics)
    diagnostics["sweep_engine"] = "Spectre nested parameter sweep"
    return metrics, diagnostics


def _differential_pair_linearity_metrics_from_result(
    metadata: dict[str, Any],
    linearity_sweep: dict[str, Any],
    *,
    vdd_v: float,
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    raw_points = metadata.get("sweep_points")
    if not isinstance(raw_points, dict) or not raw_points:
        raise RuntimeError(
            "Spectre differential linearity sweep returned no transient points"
        )
    amplitudes = [float(value) for value in linearity_sweep["amplitudes_v"]]
    if len(raw_points) != len(amplitudes):
        raise RuntimeError(
            "Spectre differential linearity point count does not match declared "
            "amplitudes"
        )
    point_metrics: list[dict[str, float]] = []
    point_diagnostics: list[dict[str, Any]] = []
    for index, amplitude in enumerate(amplitudes, start=1):
        raw_point = raw_points.get(index, raw_points.get(str(index)))
        if not isinstance(raw_point, dict):
            raise RuntimeError(
                f"Spectre differential linearity sweep is missing point {index}"
            )
        inp = _signal(raw_point, "INP")
        inn = _signal(raw_point, "INN")
        outp = _signal(raw_point, "OUTP")
        outn = _signal(raw_point, "OUTN")
        differential_input = [
            positive - negative
            for positive, negative in zip(inp, inn, strict=True)
        ]
        single_ended_output = _differential_pair_has_current_mirror_load(
            topology_variant
        )
        differential_output = (
            list(outn)
            if single_ended_output
            else [
                positive - negative
                for positive, negative in zip(outp, outn, strict=True)
            ]
        )
        metrics, diagnostics = extract_common_source_linearity_point_metrics(
            _signal(raw_point, "time"),
            differential_input,
            differential_output,
            _signal(raw_point, "VDD_SRC:p"),
            vdd_v=vdd_v,
            frequency_hz=float(linearity_sweep["frequency_hz"]),
            settling_cycles=int(linearity_sweep.get("settling_cycles", 4)),
            measurement_cycles=int(linearity_sweep.get("measurement_cycles", 8)),
            max_harmonic=int(linearity_sweep.get("max_harmonic", 5)),
        )
        tolerance = max(amplitude * 5e-3, 1e-8)
        if abs(metrics["input_fundamental_v_peak"] - amplitude) > tolerance:
            raise RuntimeError(
                "Spectre differential input fundamental does not match declared "
                f"sweep amplitude at point {index}"
            )
        point_metrics.append(metrics)
        point_diagnostics.append(
            {
                "index": index,
                "declared_differential_input_amplitude_v_peak": amplitude,
                "signals": [
                    "time",
                    "INP",
                    "INN",
                    "OUTP",
                    "OUTN",
                    "VDD_SRC:p",
                ],
                "input_expression": "INP-INN",
                "output_expression": (
                    "OUTN" if single_ended_output else "OUTP-OUTN"
                ),
                "metrics": metrics,
                **diagnostics,
            }
        )
    metrics, diagnostics = aggregate_differential_pair_linearity_metrics(
        amplitudes,
        point_metrics,
        compression_db=float(linearity_sweep.get("compression_db", 1.0)),
        output_mode=(
            "single_ended_outn"
            if _differential_pair_has_current_mirror_load(topology_variant)
            else "differential"
        ),
    )
    diagnostics["point_details"] = point_diagnostics
    diagnostics["sweep_point_count"] = len(point_metrics)
    diagnostics["sweep_engine"] = "Spectre nested parameter sweep"
    return metrics, diagnostics


def _common_source_noise_metrics_from_result(
    result: Any, noise_sweep: dict[str, Any]
) -> tuple[dict[str, float], dict[str, Any]]:
    from virtuoso_bridge.spectre.parsers import parse_spectre_psf_ascii

    raw_output_dir = getattr(result, "metadata", {}).get("output_dir")
    if not raw_output_dir:
        raise RuntimeError("Spectre noise result is missing its downloaded PSF path")
    output_dir = Path(str(raw_output_dir))
    candidates = sorted(output_dir.rglob("noise.noise"))
    if not candidates:
        candidates = sorted(
            path
            for path in output_dir.rglob("*.noise")
            if "pnoise" not in path.name.lower()
        )
    if len(candidates) != 1:
        raise RuntimeError(
            "Spectre noise result requires exactly one ordinary noise PSF file; "
            f"found {len(candidates)}"
        )
    noise_file = candidates[0]
    parsed = parse_spectre_psf_ascii(noise_file)
    data = getattr(parsed, "data", None)
    if not isinstance(data, dict) or not data:
        raise RuntimeError("Spectre ordinary noise PSF file is empty or unparseable")
    frequency_hz = _signal(data, "freq")
    metrics, diagnostics = extract_common_source_noise_metrics(
        frequency_hz,
        _signal(data, "out"),
        _signal(data, "in"),
    )
    start_hz = float(noise_sweep["start_hz"])
    stop_hz = float(noise_sweep["stop_hz"])
    for label, actual, expected in (
        ("start", frequency_hz[0], start_hz),
        ("stop", frequency_hz[-1], stop_hz),
    ):
        tolerance = max(abs(expected) * 1e-6, 1e-6)
        if abs(actual - expected) > tolerance:
            raise RuntimeError(
                f"Spectre noise {label} frequency does not match the declared sweep"
            )
    diagnostics.update(
        {
            "signals": ["freq", "out", "in"],
            "temporary_psf_file": str(noise_file),
            "psf_sha256": hashlib.sha256(noise_file.read_bytes()).hexdigest(),
            "parser": "virtuoso_bridge.spectre.parsers.parse_spectre_psf_ascii",
        }
    )
    return metrics, diagnostics


def _differential_pair_noise_metrics_from_result(
    result: Any,
    noise_sweep: dict[str, Any],
    topology_variant: str = _DIFFERENTIAL_PAIR_BASE_VARIANT,
) -> tuple[dict[str, float], dict[str, Any]]:
    metrics, diagnostics = _common_source_noise_metrics_from_result(
        result, noise_sweep
    )
    diagnostics.update(
        {
            "input_source": "VIN_DIFF from VDIFF to ground",
            "input_expression": "INP-INN = VIN_DIFF",
            "output_expression": (
                "OUTN"
                if _differential_pair_has_current_mirror_load(topology_variant)
                else "OUTP-OUTN"
            ),
            "output_mode": (
                "single_ended_outn"
                if _differential_pair_has_current_mirror_load(topology_variant)
                else "differential"
            ),
            "dc_common_mode_bias": (
                "VCM_SRC plus ideal +0.5/-0.5 VCVS input drivers"
            ),
        }
    )
    return {
        f"differential_{name}": value for name, value in metrics.items()
    }, diagnostics


def _inverter_testbench_deck(
    profile: dict[str, Any],
    parameters: dict[str, float],
    remote_netlist_path: str,
) -> str:
    model_path = str(profile["model_include"])
    if '"' in model_path or '"' in remote_netlist_path:
        raise ValueError("netlist/model path contains an unsupported quote")
    return f'''simulator lang=spectre
include "{model_path}" section={profile["model_section"]}
include "{remote_netlist_path}"

parameters vdd={parameters["vdd_v"]:.12g} cload={parameters["load_ff"]:.12g}f

VDD_SRC (VDD 0) vsource dc=vdd
VSS_SRC (VSS 0) vsource dc=0
VIN_SRC (IN 0) vsource type=pulse val0=0 val1=vdd delay=20p rise=5p fall=5p width=100p period=200p
CL0 (OUT 0) capacitor c=cload

tran tran stop=380p maxstep=0.5p
save IN OUT VDD VSS VDD_SRC:p
'''


def _common_source_model_manifest(
    profile: dict[str, Any],
    operating_condition: dict[str, Any] | None,
) -> dict[str, Any]:
    if operating_condition is None:
        model_path = str(profile["model_include"])
        model_section = str(profile["model_section"])
        if '"' in model_path or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", model_section
        ):
            raise ValueError("invalid default model include")
        return {
            "source": "pdk_profile",
            "profile": str(profile["name"]),
            "profile_source": "pdk_profile",
            "process_corner": None,
            "process_corner_source": "pdk_profile",
            "temperature_c": None,
            "temperature_source": "simulator_default",
            "includes": [{"path": model_path, "section": model_section}],
        }

    corner = str(operating_condition["process_corner"])
    if corner == str(profile["model_section"]):
        raw_includes: Any = [
            {
                "path": str(profile["model_include"]),
                "section": str(profile["model_section"]),
            }
        ]
    else:
        raw_corners = profile.get("process_corners", {})
        raw_includes = raw_corners.get(corner)
        if not isinstance(raw_includes, list) or not raw_includes:
            raise ValueError(f"PDK profile does not map process corner {corner!r}")
    includes: list[dict[str, str]] = []
    for item in raw_includes:
        if not isinstance(item, dict):
            raise ValueError(f"invalid model include for process corner {corner!r}")
        path = str(item.get("path") or "")
        section = str(item.get("section") or "")
        if not path or '"' in path or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", section
        ):
            raise ValueError(f"invalid model include for process corner {corner!r}")
        includes.append({"path": path, "section": section})
    temperature_c = float(operating_condition["temperature_c"])
    if not (-273.15 <= temperature_c <= 300.0):
        raise ValueError("operating-condition temperature is outside the safe range")
    return {
        "source": "software_inference",
        "profile": str(profile["name"]),
        "profile_source": "pdk_profile",
        "process_corner": corner,
        "process_corner_source": "user_input",
        "temperature_c": temperature_c,
        "temperature_source": "user_input",
        "includes": includes,
    }


def _common_source_model_configuration(
    profile: dict[str, Any],
    operating_condition: dict[str, Any] | None,
) -> tuple[str, float | None]:
    manifest = _common_source_model_manifest(profile, operating_condition)
    lines = [
        f'include "{item["path"]}" section={item["section"]}'
        for item in manifest["includes"]
    ]
    return "\n".join(lines), manifest["temperature_c"]


def _common_source_testbench_deck(
    profile: dict[str, Any],
    parameters: dict[str, float],
    remote_netlist_path: str,
    *,
    analysis: str = "dc",
    ac_sweep: dict[str, Any] | None = None,
    linearity_sweep: dict[str, Any] | None = None,
    noise_sweep: dict[str, Any] | None = None,
    operating_condition: dict[str, Any] | None = None,
) -> str:
    if '"' in remote_netlist_path:
        raise ValueError("netlist path contains an unsupported quote")
    model_configuration, temperature_c = _common_source_model_configuration(
        profile, operating_condition
    )
    cascode = "cascode_bias_v" in parameters
    saved_nodes = "IN OUT VDD VSS" + (
        " NSRC" if "source_resistance_ohm" in parameters else ""
    )
    if cascode:
        saved_nodes += " VCAS NCAS"
    if analysis not in {"dc", "ac", "transient", "noise"}:
        raise ValueError(f"unsupported common-source analysis: {analysis}")
    source = "VIN_SRC (IN 0) vsource dc=vbias"
    extra_parameters = (
        f' vcas={parameters["cascode_bias_v"]:.12g}' if cascode else ""
    )
    cascode_source = "VCAS_SRC (VCAS 0) vsource dc=vcas" if cascode else ""
    cascode_save = (
        "save MNCAS:ids MNCAS:vgs MNCAS:vds MNCAS:vdsat "
        "MNCAS:gm MNCAS:gds\n"
        + _mos_small_signal_operating_point_save("MNCAS")
        if cascode
        else ""
    )
    input_device_small_signal_save = _mos_small_signal_operating_point_save("MN0")
    load = ""
    analysis_statement = ""
    if analysis == "ac":
        if ac_sweep is None:
            raise ValueError("common-source AC deck requires ac_sweep")
        if "load_ff" in parameters:
            load = f'CL0 (OUT 0) capacitor c={parameters["load_ff"]:.12g}f\n'
        analysis_statement = (
            f'ac ac start={float(ac_sweep["start_hz"]):.12g} '
            f'stop={float(ac_sweep["stop_hz"]):.12g} '
            f'dec={int(ac_sweep.get("points_per_decade", 20))} annotate=status\n'
        )
        source += " mag=1 type=dc"
    elif analysis == "transient":
        if linearity_sweep is None:
            raise ValueError("common-source transient deck requires linearity_sweep")
        amplitudes = [float(value) for value in linearity_sweep["amplitudes_v"]]
        frequency_hz = float(linearity_sweep["frequency_hz"])
        total_cycles = int(linearity_sweep.get("settling_cycles", 4)) + int(
            linearity_sweep.get("measurement_cycles", 8)
        )
        points_per_cycle = int(linearity_sweep.get("points_per_cycle", 128))
        sample_step_s = 1.0 / (frequency_hz * points_per_cycle)
        # Spectre may omit the requested stop point from a strobed transient.
        # One extra strobe guarantees coverage of the exact coherent window.
        stop_s = total_cycles / frequency_hz + sample_step_s
        extra_parameters += (
            f" vinamp={amplitudes[0]:.12g} flinearity={frequency_hz:.12g}"
        )
        source = (
            "VIN_SRC (IN 0) vsource dc=vbias type=sine sinedc=vbias "
            "ampl=vinamp freq=flinearity"
        )
        values = " ".join(f"{value:.12g}" for value in amplitudes)
        analysis_statement = (
            f"sw1 sweep param=vinamp values=[{values}] {{\n"
            f"  tran tran stop={stop_s:.12g} maxstep={sample_step_s:.12g} "
            f"strobeperiod={sample_step_s:.12g} strobeoutput=all annotate=status\n"
            "}\n"
        )
        if "load_ff" in parameters:
            load = f'CL0 (OUT 0) capacitor c={parameters["load_ff"]:.12g}f\n'
    elif analysis == "noise":
        if noise_sweep is None:
            raise ValueError("common-source noise deck requires noise_sweep")
        source += " mag=1 type=dc"
        analysis_statement = (
            f'noise (OUT 0) noise start={float(noise_sweep["start_hz"]):.12g} '
            f'stop={float(noise_sweep["stop_hz"]):.12g} '
            f'dec={int(noise_sweep.get("points_per_decade", 20))} '
            "iprobe=VIN_SRC annotate=status\n"
        )
        if "load_ff" in parameters:
            load = f'CL0 (OUT 0) capacitor c={parameters["load_ff"]:.12g}f\n'
    temperature_option = (
        "" if temperature_c is None else f" temp={temperature_c:.12g}"
    )
    return f'''simulator lang=spectre
{model_configuration}
include "{remote_netlist_path}"

parameters vdd={parameters["vdd_v"]:.12g} vbias={parameters["bias_v"]:.12g}{extra_parameters}

VDD_SRC (VDD 0) vsource dc=vdd
VSS_SRC (VSS 0) vsource dc=0
{source}
{cascode_source}
{load}

simulatorOptions options{temperature_option} psfversion="1.4.0" reltol=1e-4 vabstol=1e-6 iabstol=1e-12
dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status
dcOpInfo info what=oppoint where=rawfile
{analysis_statement}save {saved_nodes} VDD_SRC:p VIN_SRC:p
save MN0:ids MN0:vgs MN0:vds MN0:vdsat MN0:gm MN0:gds
{input_device_small_signal_save}{cascode_save}saveOptions options save=allpub
'''


def _differential_pair_testbench_deck(
    profile: dict[str, Any],
    parameters: dict[str, float],
    remote_netlist_path: str,
    *,
    analysis: str = "dc",
    ac_sweep: dict[str, Any] | None = None,
    linearity_sweep: dict[str, Any] | None = None,
    noise_sweep: dict[str, Any] | None = None,
    ac_mode: str = "differential",
    topology_variant: str = "resistive_load_nmos_differential_pair",
    operating_condition: dict[str, Any] | None = None,
) -> str:
    if '"' in remote_netlist_path:
        raise ValueError("netlist path contains an unsupported quote")
    model_configuration, temperature_c = _common_source_model_configuration(
        profile, operating_condition
    )
    if analysis not in {"dc", "ac", "transient", "noise"}:
        raise ValueError(f"unsupported differential-pair analysis: {analysis}")
    if ac_mode not in {
        "differential",
        "common_mode",
        "positive_supply",
        "negative_supply",
    }:
        raise ValueError(f"unsupported differential-pair AC mode: {ac_mode}")
    current_mirror_load = _differential_pair_has_current_mirror_load(
        topology_variant
    )
    inp_ac = ""
    inn_ac = ""
    extra_parameters = ""
    load = ""
    analysis_statement = ""
    input_sources = (
        "VINP_SRC (INP 0) vsource dc=vcm{inp_ac}\n"
        "VINN_SRC (INN 0) vsource dc=vcm{inn_ac}"
    )
    vdd_ac = ""
    vss_ac = ""
    if analysis == "ac":
        if ac_sweep is None:
            raise ValueError("differential-pair AC deck requires ac_sweep")
        if ac_mode == "differential":
            inp_ac = " mag=0.5 phase=0 type=dc"
            inn_ac = " mag=0.5 phase=180 type=dc"
        elif ac_mode == "common_mode":
            inp_ac = " mag=1 phase=0 type=dc"
            inn_ac = " mag=1 phase=0 type=dc"
        elif ac_mode == "positive_supply":
            vdd_ac = " mag=1 phase=0 type=dc"
        else:
            vss_ac = " mag=1 phase=0 type=dc"
        analysis_statement = (
            f'ac ac start={float(ac_sweep["start_hz"]):.12g} '
            f'stop={float(ac_sweep["stop_hz"]):.12g} '
            f'dec={int(ac_sweep.get("points_per_decade", 20))} annotate=status\n'
        )
    elif analysis == "transient":
        if linearity_sweep is None:
            raise ValueError(
                "differential-pair transient deck requires linearity_sweep"
            )
        amplitudes = [
            float(value) for value in linearity_sweep["amplitudes_v"]
        ]
        frequency_hz = float(linearity_sweep["frequency_hz"])
        total_cycles = int(linearity_sweep.get("settling_cycles", 4)) + int(
            linearity_sweep.get("measurement_cycles", 8)
        )
        points_per_cycle = int(linearity_sweep.get("points_per_cycle", 128))
        sample_step_s = 1.0 / (frequency_hz * points_per_cycle)
        stop_s = total_cycles / frequency_hz + sample_step_s
        extra_parameters = (
            f" vindiff={amplitudes[0]:.12g} "
            f"flinearity={frequency_hz:.12g} vinhalf=vindiff/2 "
            "vinnhalf=-vindiff/2"
        )
        inp_ac = " type=sine sinedc=vcm ampl=vinhalf freq=flinearity"
        inn_ac = " type=sine sinedc=vcm ampl=vinnhalf freq=flinearity"
        values = " ".join(f"{value:.12g}" for value in amplitudes)
        analysis_statement = (
            f"sw1 sweep param=vindiff values=[{values}] {{\n"
            f"  tran tran stop={stop_s:.12g} maxstep={sample_step_s:.12g} "
            f"strobeperiod={sample_step_s:.12g} strobeoutput=all annotate=status\n"
            "}\n"
        )
    elif analysis == "noise":
        if noise_sweep is None:
            raise ValueError("differential-pair noise deck requires noise_sweep")
        input_sources = (
            "VCM_SRC (VCM 0) vsource dc=vcm\n"
            "VIN_DIFF (VDIFF 0) vsource dc=0 mag=1 type=dc\n"
            "EINP (INP VCM VDIFF 0) vcvs gain=0.5\n"
            "EINN (INN VCM VDIFF 0) vcvs gain=-0.5"
        )
        noise_output = "(OUTN 0)" if current_mirror_load else "(OUTP OUTN)"
        analysis_statement = (
            f'noise {noise_output} noise start={float(noise_sweep["start_hz"]):.12g} '
            f'stop={float(noise_sweep["stop_hz"]):.12g} '
            f'dec={int(noise_sweep.get("points_per_decade", 20))} '
            "iprobe=VIN_DIFF annotate=status\n"
        )
    if "load_ff" in parameters:
        load = (
            f'CLN (OUTN 0) capacitor c={parameters["load_ff"]:.12g}f\n'
            if current_mirror_load
            else (
                f'CLP (OUTP 0) capacitor c={parameters["load_ff"]:.12g}f\n'
                f'CLN (OUTN 0) capacitor c={parameters["load_ff"]:.12g}f\n'
            )
        )
    real_tail = _differential_pair_has_real_tail(topology_variant)
    tail_output_resistance = parameters.get("tail_output_resistance_ohm")
    if real_tail and (
        "tail_current_ua" in parameters or tail_output_resistance is not None
    ):
        raise ValueError("real-tail deck rejects ideal-tail parameters")
    if not real_tail and "tail_bias_v" in parameters:
        raise ValueError("ideal-tail deck rejects tail_bias_v")
    tail_resistance_parameter = (
        f' rtail={tail_output_resistance:.12g}'
        if tail_output_resistance is not None
        else ""
    )
    tail_resistance_element = (
        "RTAIL (TAIL 0) resistor r=rtail\n"
        if tail_output_resistance is not None
        else ""
    )
    tail_parameter = (
        f' vbias={parameters["tail_bias_v"]:.12g}'
        if real_tail
        else f' itail={parameters["tail_current_ua"]:.12g}u'
    )
    tail_source = (
        "VBIAS_SRC (BIAS 0) vsource dc=vbias"
        if real_tail
        else "ITAIL_SRC (TAIL 0) isource dc=itail"
    )
    saved_nodes = "INP INN OUTP OUTN TAIL VDD VSS"
    tail_save = ""
    if _differential_pair_has_source_degeneration(topology_variant):
        saved_nodes += " NSP NSN"
    if real_tail:
        saved_nodes += " BIAS"
        tail_save = (
            "save MNTAIL:ids MNTAIL:vgs MNTAIL:vds MNTAIL:vdsat "
            "MNTAIL:gm MNTAIL:gds\n"
        )
    load_save = ""
    if current_mirror_load:
        load_save = (
            "save MP0:ids MP0:vgs MP0:vds MP0:vdsat MP0:gm MP0:gds\n"
            "save MP1:ids MP1:vgs MP1:vds MP1:vdsat MP1:gm MP1:gds\n"
        )
    small_signal_op_instances = ["MN0", "MN1"]
    if real_tail:
        small_signal_op_instances.append("MNTAIL")
    if current_mirror_load:
        small_signal_op_instances.extend(["MP0", "MP1"])
    small_signal_op_save = "".join(
        _mos_small_signal_operating_point_save(instance)
        for instance in small_signal_op_instances
    )
    temperature_option = (
        "" if temperature_c is None else f" temp={temperature_c:.12g}"
    )
    return f'''simulator lang=spectre
{model_configuration}
include "{remote_netlist_path}"

parameters vdd={parameters["vdd_v"]:.12g} vcm={parameters["common_mode_v"]:.12g}{tail_parameter}{tail_resistance_parameter}{extra_parameters}

VDD_SRC (VDD 0) vsource dc=vdd{vdd_ac}
VSS_SRC (VSS 0) vsource dc=0{vss_ac}
{input_sources.format(inp_ac=inp_ac, inn_ac=inn_ac)}
{tail_source}
{tail_resistance_element}
{load}

simulatorOptions options{temperature_option} psfversion="1.4.0" reltol=1e-4 vabstol=1e-6 iabstol=1e-12
dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status
dcOpInfo info what=oppoint where=rawfile
{analysis_statement}save {saved_nodes} VDD_SRC:p
save MN0:ids MN0:vgs MN0:vds MN0:vdsat MN0:gm MN0:gds
save MN1:ids MN1:vgs MN1:vds MN1:vdsat MN1:gm MN1:gds
{tail_save}{load_save}{small_signal_op_save}saveOptions options save=allpub
'''


def _generic_dc_voltage(
    data: dict[str, Any], expression: GenericVoltageExpression
) -> float:
    def node_value(node: str) -> float:
        return 0.0 if node == "0" else _scalar(data, f"dc_{node}")

    return node_value(expression.positive_node) - node_value(
        expression.negative_node
    )


def _generic_ac_voltage(
    data: dict[str, Any],
    expression: GenericVoltageExpression,
    *,
    sample_count: int,
) -> list[complex]:
    def node_values(node: str) -> list[complex]:
        if node == "0":
            return [0j] * sample_count
        values = _complex_signal(data, f"ac_{node}")
        if len(values) != sample_count:
            raise RuntimeError(
                f"Spectre complex signal ac_{node} length {len(values)} does not "
                f"match frequency length {sample_count}"
            )
        return values

    positive = node_values(expression.positive_node)
    negative = node_values(expression.negative_node)
    return [left - right for left, right in zip(positive, negative, strict=True)]


def _generic_transient_voltage(
    data: dict[str, Any], expression: GenericVoltageExpression
) -> list[float]:
    def node_values(node: str, sample_count: int | None = None) -> list[float]:
        if node == "0":
            if sample_count is None:
                raise RuntimeError(
                    "ground-only transient expression has no sample-count reference"
                )
            return [0.0] * sample_count
        return _signal(data, node)

    positive = node_values(expression.positive_node)
    negative = node_values(expression.negative_node, len(positive))
    if len(negative) != len(positive):
        raise RuntimeError(
            "generic transient voltage expression signals have different lengths"
        )
    return [left - right for left, right in zip(positive, negative, strict=True)]


def _generic_linearity_metrics_from_result(
    metadata: dict[str, Any],
    settings: GenericOaSimulationSpec,
    linearity_sweep: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    if settings.transfer is None or settings.dynamic_analysis is None:
        raise RuntimeError(
            "generic transient extraction requires transfer and dynamic_analysis"
        )
    raw_points = metadata.get("sweep_points")
    if not isinstance(raw_points, dict) or not raw_points:
        raise RuntimeError("generic Spectre linearity sweep returned no transient points")
    amplitudes = [float(value) for value in linearity_sweep["amplitudes_v"]]
    if len(raw_points) != len(amplitudes):
        raise RuntimeError(
            "generic Spectre linearity point count does not match declared amplitudes"
        )
    source_by_name = {source.name: source for source in settings.sources}
    power_source = source_by_name[settings.dynamic_analysis.power_source]
    supply_voltage_v = abs(float(power_source.dc_value))
    point_metrics: list[dict[str, float]] = []
    point_diagnostics: list[dict[str, Any]] = []
    for index, amplitude in enumerate(amplitudes, start=1):
        raw_point = raw_points.get(index, raw_points.get(str(index)))
        if not isinstance(raw_point, dict):
            raise RuntimeError(
                f"generic Spectre linearity sweep is missing point {index}"
            )
        input_voltage = _generic_transient_voltage(
            raw_point, settings.transfer.input
        )
        output_voltage = _generic_transient_voltage(
            raw_point, settings.transfer.output
        )
        metrics, diagnostics = extract_common_source_linearity_point_metrics(
            _signal(raw_point, "time"),
            input_voltage,
            output_voltage,
            _signal(raw_point, f"{power_source.name}:p"),
            vdd_v=supply_voltage_v,
            frequency_hz=float(linearity_sweep["frequency_hz"]),
            settling_cycles=int(linearity_sweep.get("settling_cycles", 4)),
            measurement_cycles=int(linearity_sweep.get("measurement_cycles", 8)),
            max_harmonic=int(linearity_sweep.get("max_harmonic", 5)),
        )
        tolerance = max(amplitude * 5e-3, 1e-8)
        if abs(metrics["input_fundamental_v_peak"] - amplitude) > tolerance:
            raise RuntimeError(
                "generic Spectre input fundamental does not match the declared "
                f"amplitude at point {index}"
            )
        point_metrics.append(metrics)
        point_diagnostics.append(
            {
                "index": index,
                "declared_input_amplitude_v_peak": amplitude,
                "input_expression": settings.transfer.input.model_dump(mode="json"),
                "output_expression": settings.transfer.output.model_dump(mode="json"),
                "power_source": power_source.name,
                "metrics": metrics,
                **diagnostics,
            }
        )
    metrics, diagnostics = aggregate_common_source_linearity_metrics(
        amplitudes,
        point_metrics,
        compression_db=float(linearity_sweep.get("compression_db", 1.0)),
    )
    diagnostics.update(
        {
            "point_details": point_diagnostics,
            "sweep_point_count": len(point_metrics),
            "sweep_engine": "Spectre nested parameter sweep",
            "supply_voltage_v": supply_voltage_v,
        }
    )
    return metrics, diagnostics


def simulate_existing_schematic(
    payload: dict[str, Any], *, _bundle_cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run a typed wrapper around one user-provided OA schematic."""

    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    analysis = str(payload.get("analysis", ""))
    raw_conditions = payload.get("operating_conditions")
    if raw_conditions is not None and payload.get("operating_condition") is None:
        return _simulate_existing_schematic_operating_conditions(
            payload,
            _bundle_cache=_bundle_cache,
        )
    raw_settings = payload.get("generic_simulation")
    raw_context = payload.get("design_context")
    if not isinstance(raw_settings, dict) or not isinstance(raw_context, dict):
        raise RuntimeError(
            "generic existing-schematic simulation requires design_context and "
            "generic_simulation"
        )
    settings = GenericOaSimulationSpec.model_validate(raw_settings)
    operating_condition = payload.get("operating_condition")
    if operating_condition is not None and not isinstance(
        operating_condition, dict
    ):
        raise RuntimeError("generic operating condition is not structured")
    effective_vdd: float | None = None
    if isinstance(operating_condition, dict):
        source_name = settings.operating_condition_supply_source
        if source_name is None:
            raise RuntimeError(
                "generic PVT simulation requires an operating-condition supply source"
            )
        sources = []
        for source in settings.sources:
            if source.name != source_name:
                sources.append(source)
                continue
            effective_vdd = float(
                operating_condition.get("vdd_v")
                if operating_condition.get("vdd_v") is not None
                else source.dc_value
            )
            sources.append(source.model_copy(update={"dc_value": effective_vdd}))
        if effective_vdd is None:
            raise RuntimeError(
                "generic operating-condition supply source disappeared from settings"
            )
        settings = settings.model_copy(
            update={
                "sources": sources,
                "temperature_c": float(operating_condition["temperature_c"]),
            }
        )
    settings.validate_analysis(analysis)
    context = DesignContext.model_validate(raw_context)
    ac_sweep = payload.get("ac_sweep")
    linearity_sweep = payload.get("linearity_sweep")
    noise_sweep = payload.get("noise_sweep")
    if analysis == "ac" and not isinstance(ac_sweep, dict):
        raise RuntimeError("generic existing-schematic AC requires ac_sweep")
    if analysis == "transient" and not isinstance(linearity_sweep, dict):
        raise RuntimeError(
            "generic existing-schematic transient requires linearity_sweep"
        )
    if analysis == "noise" and not isinstance(noise_sweep, dict):
        raise RuntimeError("generic existing-schematic noise requires noise_sweep")
    expected_sweep = {
        "ac": ac_sweep,
        "transient": linearity_sweep,
        "noise": noise_sweep,
    }
    if any(
        value is not None and name != analysis
        for name, value in expected_sweep.items()
    ):
        raise RuntimeError(
            "generic existing-schematic analysis rejects unrelated sweep settings"
        )

    binding_sha256 = hashlib.sha256(
        json.dumps(
            {
                "target": payload.get("target"),
                "profile": profile,
                "design_context": raw_context,
                "generic_simulation": raw_settings,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if _bundle_cache is not None and "client" in _bundle_cache:
        if _bundle_cache.get("binding_sha256") != binding_sha256:
            raise RuntimeError(
                "shared-netlist stage changed target, profile, context, or testbench"
            )
        client = _bundle_cache["client"]
        schematic = _bundle_cache["schematic"]
        context_audit = _bundle_cache["context_audit"]
    else:
        client = _client()
        library, cell = _target(payload)
        schematic = _read_schematic(client, library, cell)
        pin_geometry, placement = _schematic_geometry_bundle(
            client, library, cell
        )
        inspection_summary = _existing_schematic_summary(
            schematic,
            pin_geometry=pin_geometry,
            placement=placement,
        )
        inspection_summary = _attach_hierarchy_parameter_scope_state(
            client,
            library,
            cell,
            schematic,
            payload,
            inspection_summary,
        )
        context_audit = audit_design_context(
            inspection_summary,
            context,
        )
        if _bundle_cache is not None:
            _bundle_cache.update(
                {
                    "binding_sha256": binding_sha256,
                    "client": client,
                    "schematic": schematic,
                    "context_audit": context_audit,
                    "schematic_readback_count": 1,
                }
            )
    with tempfile.TemporaryDirectory(prefix="vda_existing_schematic_") as temp_dir:
        work_dir = Path(temp_dir)
        if _bundle_cache is not None and "netlist_evidence" in _bundle_cache:
            netlist_evidence = _bundle_cache["netlist_evidence"]
        else:
            netlist_evidence = _generate_oa_netlist(
                client,
                payload,
                work_dir,
                timeout=timeout,
                schematic=schematic,
            )
            if _bundle_cache is not None:
                _bundle_cache["netlist_evidence"] = netlist_evidence
                _bundle_cache["netlist_generation_count"] = 1
        model_configuration, _ = _common_source_model_configuration(
            profile,
            operating_condition if isinstance(operating_condition, dict) else None,
        )
        deck = render_generic_oa_testbench(
            settings,
            analysis=analysis,
            remote_netlist_path=netlist_evidence.get(
                "spectre_netlist_path",
                netlist_evidence["remote_netlist_path"],
            ),
            model_configuration=model_configuration,
            ac_sweep=ac_sweep if isinstance(ac_sweep, dict) else None,
            linearity_sweep=(
                linearity_sweep if isinstance(linearity_sweep, dict) else None
            ),
            noise_sweep=noise_sweep if isinstance(noise_sweep, dict) else None,
        )
        condition_slug = _operating_condition_slug(
            operating_condition if isinstance(operating_condition, dict) else None
        )
        wrapper_name = (
            f"input_from_oa_{analysis}"
            + (f"_{condition_slug}" if condition_slug is not None else "")
            + ".scs"
            if _bundle_cache is not None
            else "input_from_oa.scs"
        )
        local_wrapper = work_dir / wrapper_name
        local_wrapper.write_text(deck, encoding="utf-8")
        remote_wrapper = f"{netlist_evidence['remote_run_dir']}/{wrapper_name}"
        _upload_file(
            client,
            local_wrapper,
            remote_wrapper,
            timeout=min(timeout, 60),
        )
        spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
            client,
            work_dir,
            netlist_evidence["remote_run_dir"],
            timeout=timeout,
        )
        simulator = _create_spectre_simulator(
            SpectreSimulator,
            client,
            spectre_cmd=spectre_cmd,
            timeout=timeout,
            work_dir=work_dir,
            keep_remote_files=False,
            remote_run_dir=netlist_evidence["remote_run_dir"],
        )
        result = simulator.run_simulation(local_wrapper, {})
        if not result.ok:
            detail = _spectre_failure_detail(result, work_dir)
            raise RuntimeError(f"Spectre simulation failed: {detail}")
        tool_version = str(result.tool_version or "").strip()
        if not tool_version:
            tool_version = _spectre_version_from_log(work_dir)

        dc_data, dc_raw_files = _common_source_dc_data_from_result(result)
        metrics: dict[str, float] = {}
        for item in settings.dc_voltage_metrics:
            metrics[item.metric] = _generic_dc_voltage(dc_data, item.expression)
        for item in settings.dc_current_metrics:
            metrics[item.metric] = _scalar(dc_data, f"dc_{item.source}:p")
        for item in settings.operating_point_metrics:
            metrics[item.metric] = _operating_point_scalar(
                dc_data,
                item.instance,
                item.quantity,
            )
        if any(not math.isfinite(value) for value in metrics.values()):
            raise RuntimeError("generic DC/OP metrics contain a non-finite value")

        analysis_complete = True
        analysis_issues: list[str] = []
        analysis_warnings: list[str] = []
        ac_diagnostics: dict[str, Any] | None = None
        linearity_diagnostics: dict[str, Any] | None = None
        noise_diagnostics: dict[str, Any] | None = None
        if analysis == "ac":
            assert settings.transfer is not None
            assert isinstance(ac_sweep, dict)
            frequency_hz = _signal(result.data, "ac_freq")
            input_voltage = _generic_ac_voltage(
                result.data,
                settings.transfer.input,
                sample_count=len(frequency_hz),
            )
            output_voltage = _generic_ac_voltage(
                result.data,
                settings.transfer.output,
                sample_count=len(frequency_hz),
            )
            ac_metrics, ac_diagnostics = extract_common_source_ac_metrics(
                frequency_hz,
                input_voltage,
                output_voltage,
                reference_points=int(ac_sweep.get("reference_points", 5)),
                max_reference_variation_db=float(
                    ac_sweep.get("max_reference_variation_db", 0.5)
                ),
            )
            ac_diagnostics["frequency_hz"] = frequency_hz
            ac_diagnostics["input_expression"] = (
                settings.transfer.input.model_dump(mode="json")
            )
            ac_diagnostics["output_expression"] = (
                settings.transfer.output.model_dump(mode="json")
            )
            ac_diagnostics["raw_files"] = _spectre_ac_file_evidence_from_result(
                result
            )
            metrics.update(ac_metrics)
            analysis_issues.extend(
                str(value) for value in ac_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in ac_diagnostics.get("warnings", [])
            )
            analysis_complete = bool(
                ac_diagnostics.get("analysis_complete", False)
            ) and not analysis_issues
        elif analysis == "transient":
            assert isinstance(linearity_sweep, dict)
            metrics_update, linearity_diagnostics = (
                _generic_linearity_metrics_from_result(
                    getattr(result, "metadata", {}),
                    settings,
                    linearity_sweep,
                )
            )
            metrics.update(metrics_update)
            analysis_issues.extend(
                str(value)
                for value in linearity_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value)
                for value in linearity_diagnostics.get("warnings", [])
            )
            analysis_complete = bool(
                linearity_diagnostics.get("analysis_complete", False)
            ) and not analysis_issues
        elif analysis == "noise":
            assert isinstance(noise_sweep, dict)
            metrics_update, noise_diagnostics = (
                _common_source_noise_metrics_from_result(result, noise_sweep)
            )
            metrics.update(metrics_update)
            analysis_issues.extend(
                str(value) for value in noise_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in noise_diagnostics.get("warnings", [])
            )
            analysis_complete = bool(
                noise_diagnostics.get("analysis_complete", False)
            ) and not analysis_issues

        manifest, manifest_sha256 = _spectre_artifact_manifest(work_dir)
        return {
            "parameters": (
                {"vdd_v": effective_vdd}
                if effective_vdd is not None
                else {}
            ),
            "metrics": metrics,
            "metric_sources": {name: "eda_result" for name in metrics},
            "analysis_complete": analysis_complete,
            "analysis_issues": analysis_issues,
            "analysis_warnings": analysis_warnings,
            "scalar_count": len(result.data),
            "tool_version": tool_version,
            "warnings": result.warnings[:20],
            "evidence": {
                "side_effects": {
                    "oa_access_performed": True,
                    "oa_write_performed": False,
                    "remote_compute_performed": True,
                },
                "process_lifecycle": process_lifecycle,
                "design_context_binding": {
                    "source": "software_inference",
                    **context_audit.model_dump(mode="json"),
                },
                "schematic_readback": {
                    "source": "bridge_readback",
                    "target": payload["target"],
                    "topology_sha256": context_audit.topology_sha256,
                },
                "netlist": {
                    "source": "eda_result",
                    "generator": "Cadence si -batch",
                    "remote_path": netlist_evidence["remote_netlist_path"],
                    "sha256": netlist_evidence["netlist_sha256"],
                    "spectre_include": {
                        "source": "software_inference",
                        "remote_path": netlist_evidence.get(
                            "spectre_netlist_path",
                            netlist_evidence["remote_netlist_path"],
                        ),
                        "sha256": netlist_evidence.get(
                            "spectre_netlist_sha256",
                            netlist_evidence["netlist_sha256"],
                        ),
                        "transform": netlist_evidence.get(
                            "spectre_netlist_transform",
                            "raw_si_netlist",
                        ),
                        "raw_si_netlist_preserved": True,
                    },
                    "si_log_tail": netlist_evidence["si_log_tail"],
                    **netlist_evidence["parsed"],
                    "consistency_source": "software_inference",
                },
                "testbench": {
                    "contract_source": "user_input",
                    "rendering_source": "software_inference",
                    "remote_path": remote_wrapper,
                    "sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                    "analysis": analysis,
                    "settings": settings.model_dump(mode="json"),
                    "operating_condition": operating_condition,
                    "operating_condition_source": (
                        "user_input"
                        if isinstance(operating_condition, dict)
                        else None
                    ),
                    "ac_sweep": ac_sweep,
                    "linearity_sweep": linearity_sweep,
                    "noise_sweep": noise_sweep,
                    "model_resolution_source": "pdk_profile",
                    "model_configuration": _common_source_model_manifest(
                        profile,
                        operating_condition
                        if isinstance(operating_condition, dict)
                        else None,
                    ),
                },
                "simulation": {
                    "source": "eda_result",
                    "tool_version": tool_version,
                    "dc_raw_files": dc_raw_files,
                    "ac_response": (
                        {
                            "source": "eda_result",
                            "extraction_source": "software_inference",
                            **ac_diagnostics,
                        }
                        if ac_diagnostics is not None
                        else {"status": "not_requested"}
                    ),
                    "linearity": (
                        {
                            "source": "eda_result",
                            "extraction_source": "software_inference",
                            **linearity_diagnostics,
                        }
                        if linearity_diagnostics is not None
                        else {"status": "not_requested"}
                    ),
                    "noise": (
                        {
                            "source": "eda_result",
                            "extraction_source": "software_inference",
                            **noise_diagnostics,
                        }
                        if noise_diagnostics is not None
                        else {"status": "not_requested"}
                    ),
                    "artifact_manifest_complete": True,
                    "artifact_manifest": manifest,
                    "artifact_manifest_sha256": manifest_sha256,
                },
            },
        }


def simulate_existing_schematic_stages(payload: dict[str, Any]) -> dict[str, Any]:
    """Run an ordered gate sequence in one worker using one verified si netlist."""

    raw_stages = payload.get("analysis_stages")
    raw_constraints = payload.get("constraints")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise RuntimeError(
            "shared-netlist existing-schematic simulation requires analysis_stages"
        )
    if not isinstance(raw_constraints, list):
        raise RuntimeError(
            "shared-netlist existing-schematic simulation requires constraints"
        )
    stages = [AnalysisStageSpec.model_validate(item) for item in raw_stages]
    constraints = [
        MetricConstraint.model_validate(item) for item in raw_constraints
    ]
    constraints_by_metric: dict[str, list[MetricConstraint]] = {}
    for constraint in constraints:
        constraints_by_metric.setdefault(constraint.metric, []).append(constraint)

    cache: dict[str, Any] = {}
    stage_results: list[dict[str, Any]] = []
    terminated_after_stage: str | None = None
    shared_schematic: dict[str, Any] | None = None
    shared_netlist: dict[str, Any] | None = None
    for position, stage in enumerate(stages, start=1):
        member_payload = dict(payload)
        member_payload["analysis"] = stage.analysis.value
        member_payload["analysis_source"] = "user_input"
        member_payload["analysis_stages"] = []
        for sweep_name, owner in (
            ("ac_sweep", "ac"),
            ("linearity_sweep", "transient"),
            ("noise_sweep", "noise"),
        ):
            if stage.analysis.value != owner:
                member_payload.pop(sweep_name, None)
                member_payload.pop(f"{sweep_name}_user_fields", None)
        result = simulate_existing_schematic(
            member_payload,
            _bundle_cache=cache,
        )
        evidence = result.get("evidence")
        if not isinstance(evidence, dict):
            raise RuntimeError(
                f"shared-netlist stage {stage.id!r} lacks structured evidence"
            )
        schematic_evidence = evidence.get("schematic_readback")
        netlist_evidence = evidence.get("netlist")
        if not isinstance(schematic_evidence, dict) or not isinstance(
            netlist_evidence, dict
        ):
            raise RuntimeError(
                f"shared-netlist stage {stage.id!r} lacks OA/si binding evidence"
            )
        if shared_schematic is None:
            shared_schematic = schematic_evidence
            shared_netlist = netlist_evidence
        elif (
            schematic_evidence != shared_schematic
            or netlist_evidence != shared_netlist
        ):
            raise RuntimeError(
                f"shared-netlist OA/si evidence changed during stage {stage.id!r}"
            )

        missing_constraints = [
            name
            for name in stage.constraint_metrics
            if name not in constraints_by_metric
        ]
        if missing_constraints:
            raise RuntimeError(
                f"shared-netlist stage {stage.id!r} references undeclared constraints: "
                + ", ".join(missing_constraints)
            )
        stage_constraints = [
            constraint
            for name in stage.constraint_metrics
            for constraint in constraints_by_metric[name]
        ]
        raw_condition_results = result.get("operating_condition_results")
        if raw_condition_results is not None:
            if not isinstance(raw_condition_results, list) or not raw_condition_results:
                raise RuntimeError(
                    f"shared-netlist stage {stage.id!r} returned no PVT cases"
                )
            condition_gate_rows = []
            for raw_condition_result in raw_condition_results:
                if not isinstance(raw_condition_result, dict) or not isinstance(
                    raw_condition_result.get("result"), dict
                ):
                    raise RuntimeError(
                        f"shared-netlist stage {stage.id!r} returned an invalid PVT case"
                    )
                condition_metrics = {
                    str(name): float(value)
                    for name, value in raw_condition_result["result"].get(
                        "metrics", {}
                    ).items()
                }
                condition_gate_rows.append(
                    evaluate_constraints(condition_metrics, stage_constraints)
                )
            gate_evaluations = [
                max(
                    (row[index] for row in condition_gate_rows),
                    key=lambda item: (
                        item.normalized_violation,
                        float("-inf") if item.actual is None else abs(item.actual),
                    ),
                )
                for index in range(len(stage_constraints))
            ]
            every_condition_gate_passed = all(
                item.passed
                for row in condition_gate_rows
                for item in row
            )
        else:
            metrics = {
                str(name): float(value)
                for name, value in result.get("metrics", {}).items()
            }
            gate_evaluations = evaluate_constraints(metrics, stage_constraints)
            every_condition_gate_passed = all(
                item.passed for item in gate_evaluations
            )
        missing_metrics = [
            item.metric for item in gate_evaluations if item.actual is None
        ]
        if missing_metrics:
            result = dict(result)
            result["analysis_complete"] = False
            result["analysis_issues"] = [
                *[str(value) for value in result.get("analysis_issues", [])],
                "analysis omitted stage constraint metrics: "
                + ", ".join(missing_metrics),
            ]
        stage_results.append(
            {
                "stage_id": stage.id,
                "analysis": stage.analysis.value,
                "result": result,
                "gate_evaluations": [
                    item.model_dump(mode="json") for item in gate_evaluations
                ],
                "gate_evidence_source": "software_inference",
            }
        )
        if not bool(result.get("analysis_complete", True)):
            break
        if (
            stage.stop_on_failure
            and not every_condition_gate_passed
            and position < len(stages)
        ):
            terminated_after_stage = stage.id
            break

    assert shared_schematic is not None
    assert shared_netlist is not None
    if cache.get("netlist_generation_count") != 1:
        raise RuntimeError(
            "shared-netlist execution did not generate exactly one netlist"
        )
    return {
        "stage_results": stage_results,
        "terminated_after_stage": terminated_after_stage,
        "shared_netlist": {
            "source": "software_inference",
            "remote_path": shared_netlist.get("remote_path"),
            "sha256": shared_netlist.get("sha256"),
            "netlist_generation_count": cache["netlist_generation_count"],
            "schematic_readback_count": cache.get("schematic_readback_count"),
            "executed_stages": [item["stage_id"] for item in stage_results],
            "reuse_contract": "one_worker_one_oa_readback_one_si_netlist",
        },
        "evidence": {
            "schematic_readback": shared_schematic,
            "netlist": shared_netlist,
            "stage_gate_source": "software_inference",
        },
    }


def simulate_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    client = _client()
    library, cell = _target(payload)
    schematic = _read_schematic(client, library, cell)
    if _assert_inverter(schematic, profile) != "inverter_core":
        raise RuntimeError(
            "simulation.run uses the VDA-generated wrapper and therefore requires "
            "an inverter core, not an OA testbench with its own sources"
        )
    oa_parameters = _semantic_parameters_from_schematic(schematic)
    requested_device_parameters = {
        name: float(payload.get("parameters", {})[name])
        for name in ("nmos_width_um", "pmos_width_um", "length_um")
        if name in payload.get("parameters", {})
    }
    _assert_parameter_consistency(
        requested_device_parameters,
        oa_parameters,
        expected_label="requested candidate",
        actual_label="OA readback",
    )
    parameters = _resolved_parameters(payload, oa_parameters)
    with tempfile.TemporaryDirectory(prefix="vda_inverter_") as temp_dir:
        work_dir = Path(temp_dir)
        netlist_evidence = _generate_oa_netlist(
            client, payload, work_dir, timeout=timeout
        )
        netlist_parameters = netlist_evidence["parsed"]["semantic_parameters"]
        _assert_parameter_consistency(
            oa_parameters,
            netlist_parameters,
            expected_label="OA readback",
            actual_label="si netlist",
        )

        netlist = work_dir / "inverter_from_oa.scs"
        deck = _inverter_testbench_deck(
            profile, parameters, netlist_evidence["remote_netlist_path"]
        )
        netlist.write_text(deck, encoding="utf-8")
        remote_wrapper = f"{netlist_evidence['remote_run_dir']}/input_from_oa.scs"
        _upload_file(client, netlist, remote_wrapper, timeout=min(timeout, 60))
        spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
            client,
            work_dir,
            netlist_evidence["remote_run_dir"],
            timeout=timeout,
        )
        simulator = _create_spectre_simulator(
            SpectreSimulator,
            client,
            spectre_cmd=spectre_cmd,
            timeout=timeout,
            work_dir=work_dir,
            keep_remote_files=False,
            remote_run_dir=netlist_evidence["remote_run_dir"],
        )
        result = simulator.run_simulation(netlist, {})
        if not result.ok:
            detail = _spectre_failure_detail(result, work_dir)
            raise RuntimeError(f"Spectre simulation failed: {detail}")
        time_s = _signal(result.data, "time")
        vin_v = _signal(result.data, "IN")
        vout_v = _signal(result.data, "OUT")
        supply_current_a = _signal(result.data, "VDD_SRC:p")
        metrics = extract_inverter_metrics(
            time_s, vin_v, vout_v, vdd_v=parameters["vdd_v"]
        )
        metrics.update(
            extract_supply_metrics(
                time_s,
                vin_v,
                supply_current_a,
                vdd_v=parameters["vdd_v"],
            )
        )
        metrics["gate_area_proxy_um2"] = (
            oa_parameters["nmos_width_um"] + oa_parameters["pmos_width_um"]
        ) * oa_parameters["length_um"]
        metric_sources = {
            name: "eda_result" for name in metrics if name != "gate_area_proxy_um2"
        }
        metric_sources["gate_area_proxy_um2"] = "software_inference"
        return {
            "parameters": parameters,
            "metrics": metrics,
            "metric_sources": metric_sources,
            "sample_count": len(time_s),
            "tool_version": result.tool_version,
            "warnings": result.warnings[:20],
            "evidence": {
                "schematic_readback": {
                    "source": "bridge_readback",
                    "target": payload["target"],
                    "semantic_parameters": oa_parameters,
                },
                "netlist": {
                    "source": "eda_result",
                    "generator": "Cadence si -batch",
                    "remote_path": netlist_evidence["remote_netlist_path"],
                    "sha256": netlist_evidence["netlist_sha256"],
                    "semantic_parameters": netlist_parameters,
                    "parameter_consistency": "matched",
                    "instances": netlist_evidence["parsed"]["instances"],
                    "si_log_tail": netlist_evidence["si_log_tail"],
                },
                "testbench": {
                    "remote_path": remote_wrapper,
                    "sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                    "values": {
                        "vdd_v": parameters["vdd_v"],
                        "load_ff": parameters["load_ff"],
                    },
                    "value_sources": {
                        name: (
                            "user_input"
                            if name in payload.get("parameters", {})
                            else "software_inference"
                        )
                        for name in ("vdd_v", "load_ff")
                    },
                },
                "simulation": {
                    "source": "eda_result",
                    "sample_count": len(time_s),
                    "tool_version": result.tool_version,
                    "signals": ["time", "IN", "OUT", "VDD_SRC:p"],
                    "supply_metric_window": "first two VIN 50% rising crossings",
                },
                "process_lifecycle": process_lifecycle,
                "gate_area_proxy_um2": {
                    "source": "software_inference",
                    "formula": "(nmos_width_um + pmos_width_um) * length_um",
                },
            },
        }


def _merge_common_source_quality_results(
    payload: dict[str, Any], results: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    merged = merge_analysis_bundle(results)
    raw_results = merged.pop("analysis_results")
    first = raw_results[next(iter(raw_results))]
    first_evidence = first.get("evidence", {})
    shared_schematic = first_evidence.get("schematic_readback")
    shared_netlist = first_evidence.get("netlist")
    analysis_evidence: dict[str, Any] = {}
    runner_warnings: list[str] = []
    tool_versions: dict[str, str] = {}
    scalar_count = 0

    for analysis, data in raw_results.items():
        evidence = data.get("evidence", {})
        if evidence.get("schematic_readback") != shared_schematic:
            raise RuntimeError(
                f"quality bundle schematic evidence changed during {analysis}"
            )
        if evidence.get("netlist") != shared_netlist:
            raise RuntimeError(
                f"quality bundle netlist evidence changed during {analysis}"
            )
        scalar_count += int(data.get("scalar_count", 0))
        tool_versions[analysis] = str(data.get("tool_version", "unknown"))
        runner_warnings.extend(
            f"{analysis}: {value}" for value in data.get("warnings", [])
        )
        analysis_evidence[analysis] = {
            "analysis_complete": bool(data.get("analysis_complete", True)),
            "analysis_issues": list(data.get("analysis_issues", [])),
            "analysis_warnings": list(data.get("analysis_warnings", [])),
            "tool_version": data.get("tool_version"),
            "scalar_count": data.get("scalar_count", 0),
            **{
                name: value
                for name, value in evidence.items()
                if name not in {"schematic_readback", "netlist"}
            },
        }

    bundle = dict(merged["analysis_bundle"])
    bundle.update(
        {
            "source": "software_inference",
            "requested_analysis": "quality",
            "requested_analysis_source": str(
                payload.get("analysis_source", "software_inference")
            ),
            "oa_netlist_reuse": "one_verified_netlist",
        }
    )
    merged.update(
        {
            "analysis_bundle": bundle,
            "scalar_count": scalar_count,
            "tool_versions": tool_versions,
            "warnings": runner_warnings,
            "evidence": {
                "schematic_readback": shared_schematic,
                "netlist": shared_netlist,
                "analysis_bundle": bundle,
                "analyses": analysis_evidence,
            },
        }
    )
    return merged


def _operating_condition_slug(condition: dict[str, Any] | None) -> str | None:
    if condition is None:
        return None
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(condition.get("name") or ""))
    slug = slug.strip("_.-")
    if not slug:
        raise RuntimeError("operating condition requires a filesystem-safe name")
    return slug[:96]


def _payload_default_supply_vdd(payload: dict[str, Any]) -> float | None:
    semantic_vdd = payload.get("parameters", {}).get("vdd_v")
    if semantic_vdd is not None:
        return float(semantic_vdd)
    raw_settings = payload.get("generic_simulation")
    if not isinstance(raw_settings, dict):
        return None
    source_name = raw_settings.get("operating_condition_supply_source")
    raw_sources = raw_settings.get("sources")
    if not isinstance(source_name, str) or not isinstance(raw_sources, list):
        return None
    for source in raw_sources:
        if isinstance(source, dict) and source.get("name") == source_name:
            value = source.get("dc_value")
            return float(value) if value is not None else None
    return None


def _merge_common_source_operating_condition_results(
    payload: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("operating-condition simulation returned no cases")
    expected = payload.get("operating_conditions")
    if not isinstance(expected, list) or [condition for condition, _ in rows] != expected:
        raise RuntimeError(
            "operating-condition result identity/order does not match the request"
        )

    common_parameters = {
        str(name): float(value)
        for name, value in rows[0][1].get("parameters", {}).items()
    }
    first_evidence = rows[0][1].get("evidence", {})
    first_netlist = first_evidence.get("netlist")
    first_schematic = first_evidence.get("schematic_readback")
    issues: list[str] = []
    warnings: list[str] = []
    condition_results: list[dict[str, Any]] = []
    completion: dict[str, bool] = {}
    for condition, result in rows:
        name = str(condition["name"])
        parameters = {
            str(parameter): float(value)
            for parameter, value in result.get("parameters", {}).items()
        }
        for parameter in list(common_parameters):
            if parameter not in parameters or not math.isclose(
                common_parameters[parameter],
                parameters[parameter],
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                common_parameters.pop(parameter)
        effective_vdd = condition.get("vdd_v")
        if effective_vdd is None:
            effective_vdd = _payload_default_supply_vdd(payload)
        if effective_vdd is None or not math.isclose(
            parameters.get("vdd_v", float("nan")),
            float(effective_vdd),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise RuntimeError(
                f"operating condition {name} did not confirm its effective vdd_v"
            )
        evidence = result.get("evidence", {})
        if (
            evidence.get("netlist") != first_netlist
            or evidence.get("schematic_readback") != first_schematic
        ):
            raise RuntimeError(
                "operating-condition cases did not reuse identical OA/netlist evidence"
            )
        complete = bool(result.get("analysis_complete", True))
        completion[name] = complete
        raw_issues = [str(value) for value in result.get("analysis_issues", [])]
        if not complete and not raw_issues:
            raw_issues = ["analysis did not produce its required core metrics"]
        issues.extend(f"{name}: {value}" for value in raw_issues)
        warnings.extend(
            f"{name}: {value}"
            for value in result.get("analysis_warnings", [])
        )
        condition_results.append({"condition": dict(condition), "result": result})

    return {
        "parameters": common_parameters,
        "metrics": {},
        "metric_sources": {},
        "analysis_complete": all(completion.values()),
        "analysis_issues": issues,
        "analysis_warnings": warnings,
        "operating_condition_results": condition_results,
        "evidence": {
            "schematic_readback": first_schematic,
            "netlist": first_netlist,
            "operating_condition_bundle": {
                "source": "software_inference",
                "requested_conditions_source": str(
                    payload.get("operating_conditions_source", "user_input")
                ),
                "conditions": [dict(condition) for condition, _ in rows],
                "completion": completion,
                "oa_netlist_reuse": "one_verified_netlist",
                "selection_semantics": (
                    "all_conditions_with_robust_worst_case_objective"
                ),
            },
        },
    }


def _simulate_existing_schematic_operating_conditions(
    payload: dict[str, Any],
    *,
    _bundle_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_conditions = payload.get("operating_conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise RuntimeError("operating_conditions must contain at least one case")
    cache = _bundle_cache if _bundle_cache is not None else {}
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_condition in raw_conditions:
        if not isinstance(raw_condition, dict):
            raise RuntimeError("operating condition is not structured")
        condition = dict(raw_condition)
        condition_payload = dict(payload)
        condition_payload.pop("operating_conditions", None)
        condition_payload["operating_condition"] = condition
        rows.append(
            (
                condition,
                simulate_existing_schematic(
                    condition_payload,
                    _bundle_cache=cache,
                ),
            )
        )
    return _merge_common_source_operating_condition_results(payload, rows)


def _simulate_common_source_operating_conditions(
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_conditions = payload.get("operating_conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise RuntimeError("operating_conditions must contain at least one case")
    cache: dict[str, Any] = {}
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_condition in raw_conditions:
        if not isinstance(raw_condition, dict):
            raise RuntimeError("operating condition is not structured")
        condition = dict(raw_condition)
        condition_payload = dict(payload)
        condition_payload.pop("operating_conditions", None)
        condition_payload["operating_condition"] = condition
        condition_parameters = dict(payload.get("parameters", {}))
        if condition.get("vdd_v") is not None:
            condition_parameters["vdd_v"] = float(condition["vdd_v"])
        condition_payload["parameters"] = condition_parameters
        rows.append(
            (
                condition,
                simulate_common_source(
                    condition_payload,
                    _bundle_cache=cache,
                ),
            )
        )
    return _merge_common_source_operating_condition_results(payload, rows)


def _simulate_differential_pair_operating_conditions(
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_conditions = payload.get("operating_conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise RuntimeError("operating_conditions must contain at least one case")
    cache: dict[str, Any] = {}
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_condition in raw_conditions:
        if not isinstance(raw_condition, dict):
            raise RuntimeError("operating condition is not structured")
        condition = dict(raw_condition)
        condition_payload = dict(payload)
        condition_payload.pop("operating_conditions", None)
        condition_payload["operating_condition"] = condition
        condition_parameters = dict(payload.get("parameters", {}))
        if condition.get("vdd_v") is not None:
            condition_parameters["vdd_v"] = float(condition["vdd_v"])
        condition_payload["parameters"] = condition_parameters
        rows.append(
            (
                condition,
                simulate_differential_pair(
                    condition_payload,
                    _bundle_cache=cache,
                ),
            )
        )
    return _merge_common_source_operating_condition_results(payload, rows)


def simulate_common_source(
    payload: dict[str, Any], *, _bundle_cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    analysis = str(payload.get("analysis", "dc"))
    raw_conditions = payload.get("operating_conditions")
    if raw_conditions is not None and payload.get("operating_condition") is None:
        return _simulate_common_source_operating_conditions(payload)
    if analysis not in {"dc", "ac", "transient", "noise", "quality"}:
        raise RuntimeError(f"unsupported common-source analysis: {analysis}")
    ac_sweep = payload.get("ac_sweep")
    linearity_sweep = payload.get("linearity_sweep")
    noise_sweep = payload.get("noise_sweep")
    if analysis == "ac" and not isinstance(ac_sweep, dict):
        raise RuntimeError("common-source AC simulation requires ac_sweep")
    if analysis == "transient" and not isinstance(linearity_sweep, dict):
        raise RuntimeError(
            "common-source transient simulation requires linearity_sweep"
        )
    if analysis == "noise" and not isinstance(noise_sweep, dict):
        raise RuntimeError("common-source noise simulation requires noise_sweep")
    if analysis == "quality":
        missing = [
            name
            for name, value in (
                ("ac_sweep", ac_sweep),
                ("linearity_sweep", linearity_sweep),
                ("noise_sweep", noise_sweep),
            )
            if not isinstance(value, dict)
        ]
        if missing:
            raise RuntimeError(
                "common-source quality simulation requires " + ", ".join(missing)
            )
        cache = _bundle_cache if _bundle_cache is not None else {}
        results: dict[str, dict[str, Any]] = {}
        for member in ("ac", "transient", "noise"):
            member_payload = dict(payload)
            member_payload["analysis"] = member
            member_payload["analysis_source"] = "software_inference"
            results[member] = simulate_common_source(
                member_payload, _bundle_cache=cache
            )
        return _merge_common_source_quality_results(payload, results)

    if _bundle_cache is not None and "client" in _bundle_cache:
        client = _bundle_cache["client"]
        topology_variant = str(_bundle_cache["topology_variant"])
        oa_parameters = dict(_bundle_cache["oa_parameters"])
        oa_geometry = dict(_bundle_cache["oa_geometry"])
    else:
        client = _client()
        library, cell = _target(payload)
        schematic = _read_schematic(client, library, cell)
        topology_variant = _assert_common_source(schematic, profile)
        oa_parameters = _common_source_semantic_parameters_from_schematic(schematic)
        oa_geometry = _common_source_device_geometry_from_schematic(schematic)
        if _bundle_cache is not None:
            _bundle_cache.update(
                {
                    "client": client,
                    "topology_variant": topology_variant,
                    "oa_parameters": dict(oa_parameters),
                    "oa_geometry": dict(oa_geometry),
                }
            )
    requested_oa_parameters = {
        name: float(payload.get("parameters", {})[name])
        for name in (
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
            "cascode_width_um",
            "cascode_length_um",
        )
        if name in payload.get("parameters", {})
    }
    _assert_parameter_consistency(
        requested_oa_parameters,
        oa_parameters,
        expected_label="requested candidate",
        actual_label="OA readback",
    )
    parameters = _resolved_common_source_parameters(
        payload,
        oa_parameters,
        topology_variant,
    )
    with tempfile.TemporaryDirectory(prefix="vda_common_source_") as temp_dir:
        work_dir = Path(temp_dir)
        if _bundle_cache is not None and "netlist_evidence" in _bundle_cache:
            netlist_evidence = _bundle_cache["netlist_evidence"]
        else:
            netlist_evidence = _generate_oa_netlist(
                client, payload, work_dir, timeout=timeout
            )
            if _bundle_cache is not None:
                _bundle_cache["netlist_evidence"] = netlist_evidence
        netlist_parameters = netlist_evidence["parsed"]["semantic_parameters"]
        netlist_geometry = netlist_evidence["parsed"]["device_geometry"]
        try:
            if netlist_evidence["parsed"]["topology_variant"] != topology_variant:
                raise RuntimeError(
                    "OA schematic and si netlist topology variants do not match"
                )
            _assert_parameter_consistency(
                oa_parameters,
                netlist_parameters,
                expected_label="OA readback",
                actual_label="si netlist",
            )
            _assert_parameter_consistency(
                oa_geometry,
                netlist_geometry,
                expected_label="OA device geometry",
                actual_label="si netlist geometry",
            )
        except RuntimeError as consistency_error:
            raise RuntimeError(
                f"{consistency_error}; si netlist retained at "
                f"{netlist_evidence['remote_netlist_path']} "
                f"(sha256={netlist_evidence['netlist_sha256']})"
            ) from consistency_error

        netlist = work_dir / "common_source_from_oa.scs"
        deck = _common_source_testbench_deck(
            profile,
            parameters,
            netlist_evidence["remote_netlist_path"],
            analysis=analysis,
            ac_sweep=ac_sweep,
            linearity_sweep=linearity_sweep,
            noise_sweep=noise_sweep,
            operating_condition=payload.get("operating_condition"),
        )
        netlist.write_text(deck, encoding="utf-8")
        condition_slug = _operating_condition_slug(
            payload.get("operating_condition")
        )
        if condition_slug is not None:
            wrapper_name = f"input_from_oa_{condition_slug}_{analysis}.scs"
        elif _bundle_cache is not None:
            wrapper_name = f"input_from_oa_{analysis}.scs"
        else:
            wrapper_name = "input_from_oa.scs"
        remote_wrapper = f"{netlist_evidence['remote_run_dir']}/{wrapper_name}"
        _upload_file(client, netlist, remote_wrapper, timeout=min(timeout, 60))
        spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
            client,
            work_dir,
            netlist_evidence["remote_run_dir"],
            timeout=timeout,
        )
        simulator = _create_spectre_simulator(
            SpectreSimulator,
            client,
            spectre_cmd=spectre_cmd,
            timeout=timeout,
            work_dir=work_dir,
            keep_remote_files=False,
            remote_run_dir=netlist_evidence["remote_run_dir"],
        )
        result = simulator.run_simulation(netlist, {})
        if not result.ok:
            detail = _spectre_failure_detail(result, work_dir)
            raise RuntimeError(f"Spectre simulation failed: {detail}")
        tool_version = str(result.tool_version or "").strip()
        if not tool_version:
            tool_version = _spectre_version_from_log(work_dir)
        dc_data, dc_psf_evidence = _common_source_dc_data_from_result(result)
        metrics, operating_point = _common_source_metrics_from_result(
            dc_data, parameters
        )
        operating_point["raw_files"] = dc_psf_evidence
        analysis_complete = True
        analysis_issues: list[str] = []
        analysis_warnings: list[str] = []
        ac_diagnostics: dict[str, Any] | None = None
        linearity_diagnostics: dict[str, Any] | None = None
        noise_diagnostics: dict[str, Any] | None = None
        if analysis == "ac":
            assert isinstance(ac_sweep, dict)
            ac_metrics, ac_diagnostics = _common_source_ac_metrics_from_result(
                result.data, ac_sweep
            )
            ac_diagnostics["raw_files"] = _spectre_ac_file_evidence_from_result(
                result
            )
            metrics.update(ac_metrics)
            analysis_issues.extend(
                str(value) for value in ac_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in ac_diagnostics.get("warnings", [])
            )
            if metrics["saturation_region"] != 1.0:
                analysis_issues.append(
                    "AC design metrics require a saturated DC operating point"
                )
            analysis_complete = (
                bool(ac_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
        elif analysis == "transient":
            assert isinstance(linearity_sweep, dict)
            linearity_metrics, linearity_diagnostics = (
                _common_source_linearity_metrics_from_result(
                    getattr(result, "metadata", {}),
                    linearity_sweep,
                    vdd_v=parameters["vdd_v"],
                )
            )
            metrics.update(linearity_metrics)
            analysis_issues.extend(
                str(value) for value in linearity_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in linearity_diagnostics.get("warnings", [])
            )
            if metrics["saturation_region"] != 1.0:
                analysis_issues.append(
                    "linearity metrics require a saturated DC operating point"
                )
            analysis_complete = (
                bool(linearity_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
        elif analysis == "noise":
            assert isinstance(noise_sweep, dict)
            noise_metrics, noise_diagnostics = _common_source_noise_metrics_from_result(
                result, noise_sweep
            )
            temporary_psf = noise_diagnostics.pop("temporary_psf_file", None)
            if temporary_psf is not None:
                noise_name = (
                    "noise.noise.psfascii"
                    if condition_slug is None
                    else f"noise_{condition_slug}.noise.psfascii"
                )
                remote_noise_psf = (
                    f"{netlist_evidence['remote_run_dir']}/{noise_name}"
                )
                _upload_file(
                    client,
                    Path(str(temporary_psf)),
                    remote_noise_psf,
                    timeout=min(timeout, 60),
                )
                noise_diagnostics["remote_psf_path"] = remote_noise_psf
            metrics.update(noise_metrics)
            analysis_issues.extend(
                str(value) for value in noise_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in noise_diagnostics.get("warnings", [])
            )
            if metrics["saturation_region"] != 1.0:
                analysis_issues.append(
                    "noise metrics require a saturated DC operating point"
                )
            analysis_complete = (
                bool(noise_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
        metric_sources = {name: "eda_result" for name in metrics}
        metric_sources["saturation_region"] = "software_inference"
        testbench_values: dict[str, Any] = {
            "analysis": analysis,
            "bias_v": parameters["bias_v"],
            "vdd_v": parameters["vdd_v"],
        }
        testbench_value_sources = {
            "analysis": str(payload.get("analysis_source", "software_inference")),
            **{
                name: (
                    "user_input"
                    if name in payload.get("parameters", {})
                    else "software_inference"
                )
                for name in ("bias_v", "vdd_v")
            },
        }
        if topology_variant == _COMMON_SOURCE_CASCODE_VARIANT:
            testbench_values["cascode_bias_v"] = parameters["cascode_bias_v"]
            testbench_value_sources["cascode_bias_v"] = (
                "user_input"
                if "cascode_bias_v" in payload.get("parameters", {})
                else "software_inference"
            )
        operating_condition = payload.get("operating_condition")
        model_manifest = _common_source_model_manifest(
            profile,
            operating_condition if isinstance(operating_condition, dict) else None,
        )
        if isinstance(operating_condition, dict):
            testbench_values["operating_condition"] = dict(operating_condition)
            testbench_value_sources["operating_condition"] = {
                name: "user_input" for name in operating_condition
            }
        if analysis == "ac":
            assert isinstance(ac_sweep, dict)
            testbench_values["ac_sweep"] = dict(ac_sweep)
            user_sweep_fields = set(payload.get("ac_sweep_user_fields", []))
            testbench_value_sources["ac_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in ac_sweep
            }
            if "load_ff" in parameters:
                testbench_values["load_ff"] = parameters["load_ff"]
                testbench_value_sources["load_ff"] = "user_input"
        elif analysis == "transient":
            assert isinstance(linearity_sweep, dict)
            testbench_values["linearity_sweep"] = dict(linearity_sweep)
            user_sweep_fields = set(
                payload.get("linearity_sweep_user_fields", [])
            )
            testbench_value_sources["linearity_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in linearity_sweep
            }
        elif analysis == "noise":
            assert isinstance(noise_sweep, dict)
            testbench_values["noise_sweep"] = dict(noise_sweep)
            user_sweep_fields = set(payload.get("noise_sweep_user_fields", []))
            testbench_value_sources["noise_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in noise_sweep
            }
        if analysis in {"transient", "noise"} and "load_ff" in parameters:
            testbench_values["load_ff"] = parameters["load_ff"]
            testbench_value_sources["load_ff"] = "user_input"
        return {
            "parameters": parameters,
            "metrics": metrics,
            "metric_sources": metric_sources,
            "analysis_complete": analysis_complete,
            "analysis_issues": analysis_issues,
            "analysis_warnings": analysis_warnings,
            "scalar_count": len(result.data),
            "tool_version": tool_version,
            "warnings": result.warnings[:20],
            "evidence": {
                "side_effects": {
                    "oa_access_performed": True,
                    "oa_write_performed": False,
                    "remote_compute_performed": True,
                },
                "process_lifecycle": process_lifecycle,
                "schematic_readback": {
                    "source": "bridge_readback",
                    "target": payload["target"],
                    "semantic_parameters": oa_parameters,
                    "device_geometry": oa_geometry,
                    "topology_variant": topology_variant,
                },
                "netlist": {
                    "source": "eda_result",
                    "generator": "Cadence si -batch",
                    "remote_path": netlist_evidence["remote_netlist_path"],
                    "sha256": netlist_evidence["netlist_sha256"],
                    "semantic_parameters": netlist_parameters,
                    "device_geometry": netlist_geometry,
                    "topology_variant": netlist_evidence["parsed"][
                        "topology_variant"
                    ],
                    "parameter_consistency": "matched",
                    "instances": netlist_evidence["parsed"]["instances"],
                    "si_log_tail": netlist_evidence["si_log_tail"],
                },
                "testbench": {
                    "remote_path": remote_wrapper,
                    "sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                    "values": testbench_values,
                    "value_sources": testbench_value_sources,
                    "model_resolution_source": "pdk_profile",
                    "model_configuration": model_manifest,
                },
                "operating_point": {
                    "source": "eda_result",
                    **operating_point,
                    "operating_region_source": "software_inference",
                },
                "ac_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **ac_diagnostics,
                    }
                    if ac_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "linearity_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **linearity_diagnostics,
                    }
                    if linearity_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "noise_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **noise_diagnostics,
                    }
                    if noise_diagnostics is not None
                    else {"status": "not_requested"}
                ),
            },
        }


def simulate_differential_pair(
    payload: dict[str, Any], *, _bundle_cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    analysis = str(payload.get("analysis", "dc"))
    raw_conditions = payload.get("operating_conditions")
    if raw_conditions is not None and payload.get("operating_condition") is None:
        return _simulate_differential_pair_operating_conditions(payload)
    if analysis not in {"dc", "ac", "transient", "noise", "psrr"}:
        raise RuntimeError(f"unsupported differential-pair analysis: {analysis}")
    ac_sweep = payload.get("ac_sweep")
    linearity_sweep = payload.get("linearity_sweep")
    noise_sweep = payload.get("noise_sweep")
    if analysis in {"ac", "psrr"} and not isinstance(ac_sweep, dict):
        raise RuntimeError(
            f"differential-pair {analysis.upper()} simulation requires ac_sweep"
        )
    if analysis == "transient" and not isinstance(linearity_sweep, dict):
        raise RuntimeError(
            "differential-pair transient simulation requires linearity_sweep"
        )
    if analysis == "noise" and not isinstance(noise_sweep, dict):
        raise RuntimeError("differential-pair noise simulation requires noise_sweep")
    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    if _bundle_cache is not None and "client" in _bundle_cache:
        client = _bundle_cache["client"]
        topology_variant = str(_bundle_cache["topology_variant"])
        oa_parameters = dict(_bundle_cache["oa_parameters"])
        oa_geometry = dict(_bundle_cache["oa_geometry"])
        cached_tail_geometry = _bundle_cache["oa_tail_geometry"]
        oa_tail_geometry = (
            dict(cached_tail_geometry)
            if isinstance(cached_tail_geometry, dict)
            else None
        )
        cached_mirror_geometry = _bundle_cache["oa_current_mirror_geometry"]
        oa_current_mirror_geometry = (
            dict(cached_mirror_geometry)
            if isinstance(cached_mirror_geometry, dict)
            else None
        )
    else:
        client = _client()
        library, cell = _target(payload)
        schematic = _read_schematic(client, library, cell)
        topology_variant = _assert_differential_pair(schematic, profile)
        oa_parameters = _differential_pair_semantic_parameters_from_schematic(
            schematic
        )
        oa_geometry = _differential_pair_device_geometry_from_schematic(schematic)
        oa_tail_geometry = _differential_pair_tail_device_geometry_from_schematic(
            schematic
        )
        oa_current_mirror_geometry = (
            _differential_pair_current_mirror_geometry_from_schematic(schematic)
        )
        if _bundle_cache is not None:
            _bundle_cache.update(
                {
                    "client": client,
                    "topology_variant": topology_variant,
                    "oa_parameters": dict(oa_parameters),
                    "oa_geometry": dict(oa_geometry),
                    "oa_tail_geometry": (
                        dict(oa_tail_geometry)
                        if isinstance(oa_tail_geometry, dict)
                        else None
                    ),
                    "oa_current_mirror_geometry": (
                        dict(oa_current_mirror_geometry)
                        if isinstance(oa_current_mirror_geometry, dict)
                        else None
                    ),
                }
            )
    if analysis == "psrr" and not _differential_pair_has_real_tail(
        topology_variant
    ):
        raise RuntimeError(
            "differential-pair PSRR requires an OA tail device so VSS injection "
            "does not use the ideal-tail wrapper as a substitute circuit"
        )
    requested_oa_parameters = {
        name: float(payload.get("parameters", {})[name])
        for name in (
            "input_width_um",
            "length_um",
            "load_resistance_ohm",
            "tail_width_um",
            "tail_length_um",
            "source_resistance_ohm",
            "pmos_load_width_um",
            "pmos_load_length_um",
        )
        if name in payload.get("parameters", {})
    }
    _assert_parameter_consistency(
        requested_oa_parameters,
        oa_parameters,
        expected_label="requested candidate",
        actual_label="OA readback",
    )
    parameters = _resolved_differential_pair_parameters(
        payload, oa_parameters, topology_variant
    )

    with tempfile.TemporaryDirectory(prefix="vda_differential_pair_") as temp_dir:
        work_dir = Path(temp_dir)
        if _bundle_cache is not None and "netlist_evidence" in _bundle_cache:
            netlist_evidence = _bundle_cache["netlist_evidence"]
        else:
            netlist_evidence = _generate_oa_netlist(
                client, payload, work_dir, timeout=timeout
            )
            if _bundle_cache is not None:
                _bundle_cache["netlist_evidence"] = netlist_evidence
        netlist_parameters = netlist_evidence["parsed"]["semantic_parameters"]
        netlist_geometry = netlist_evidence["parsed"]["device_geometry"]
        netlist_tail_geometry = netlist_evidence["parsed"].get(
            "tail_device_geometry"
        )
        netlist_current_mirror_geometry = netlist_evidence["parsed"].get(
            "current_mirror_load_geometry"
        )
        try:
            if netlist_evidence["parsed"]["topology_variant"] != topology_variant:
                raise RuntimeError(
                    "OA schematic and si netlist topology variants do not match"
                )
            _assert_parameter_consistency(
                oa_parameters,
                netlist_parameters,
                expected_label="OA readback",
                actual_label="si netlist",
            )
            _assert_parameter_consistency(
                oa_geometry,
                netlist_geometry,
                expected_label="OA device geometry",
                actual_label="si netlist geometry",
            )
            if (oa_tail_geometry is None) != (netlist_tail_geometry is None):
                raise RuntimeError(
                    "OA schematic and si netlist disagree on MNTAIL geometry"
                )
            if oa_tail_geometry is not None:
                assert isinstance(netlist_tail_geometry, dict)
                _assert_parameter_consistency(
                    oa_tail_geometry,
                    netlist_tail_geometry,
                    expected_label="OA tail-device geometry",
                    actual_label="si netlist tail-device geometry",
                )
            if (oa_current_mirror_geometry is None) != (
                netlist_current_mirror_geometry is None
            ):
                raise RuntimeError(
                    "OA schematic and si netlist disagree on PMOS mirror geometry"
                )
            if oa_current_mirror_geometry is not None:
                assert isinstance(netlist_current_mirror_geometry, dict)
                _assert_parameter_consistency(
                    oa_current_mirror_geometry,
                    netlist_current_mirror_geometry,
                    expected_label="OA current-mirror-load geometry",
                    actual_label="si current-mirror-load geometry",
                )
        except RuntimeError as consistency_error:
            raise RuntimeError(
                f"{consistency_error}; si netlist retained at "
                f"{netlist_evidence['remote_netlist_path']} "
                f"(sha256={netlist_evidence['netlist_sha256']})"
            ) from consistency_error

        wrapper = work_dir / "differential_pair_from_oa.scs"
        deck = _differential_pair_testbench_deck(
            profile,
            parameters,
            netlist_evidence["remote_netlist_path"],
            analysis="ac" if analysis == "psrr" else analysis,
            ac_sweep=ac_sweep,
            linearity_sweep=linearity_sweep,
            noise_sweep=noise_sweep,
            topology_variant=topology_variant,
            operating_condition=payload.get("operating_condition"),
        )
        wrapper.write_text(deck, encoding="utf-8")
        condition_slug = _operating_condition_slug(
            payload.get("operating_condition")
        )
        wrapper_name = (
            f"input_from_oa_{condition_slug}_{analysis}.scs"
            if condition_slug is not None
            else "input_from_oa.scs"
        )
        remote_wrapper = f"{netlist_evidence['remote_run_dir']}/{wrapper_name}"
        _upload_file(client, wrapper, remote_wrapper, timeout=min(timeout, 60))
        spectre_cmd, process_lifecycle = _install_remote_spectre_guard(
            client,
            work_dir,
            netlist_evidence["remote_run_dir"],
            timeout=timeout,
        )
        simulator = _create_spectre_simulator(
            SpectreSimulator,
            client,
            spectre_cmd=spectre_cmd,
            timeout=timeout,
            work_dir=work_dir,
            keep_remote_files=False,
            remote_run_dir=netlist_evidence["remote_run_dir"],
        )
        result = simulator.run_simulation(wrapper, {})
        if not result.ok:
            detail = _spectre_failure_detail(result, work_dir)
            raise RuntimeError(f"Spectre simulation failed: {detail}")
        tool_version = str(result.tool_version or "").strip()
        if not tool_version:
            tool_version = _spectre_version_from_log(work_dir)
        dc_data, dc_psf_evidence = _common_source_dc_data_from_result(result)
        metrics, operating_point = _differential_pair_metrics_from_result(
            dc_data, parameters, topology_variant
        )
        operating_point["raw_files"] = dc_psf_evidence
        analysis_complete = True
        analysis_issues: list[str] = []
        analysis_warnings: list[str] = []
        ac_diagnostics: dict[str, Any] | None = None
        common_mode_ac_diagnostics: dict[str, Any] | None = None
        cmrr_diagnostics: dict[str, Any] | None = None
        psrr_diagnostics: dict[str, Any] | None = None
        linearity_diagnostics: dict[str, Any] | None = None
        noise_diagnostics: dict[str, Any] | None = None
        common_mode_operating_point: dict[str, Any] | None = None
        common_mode_deck: str | None = None
        common_mode_remote_wrapper: str | None = None
        common_mode_result = None
        psrr_results: dict[str, Any] = {}
        psrr_decks: dict[str, str] = {}
        psrr_remote_wrappers: dict[str, str] = {}
        psrr_operating_points: dict[str, dict[str, Any]] = {}
        ac_psf_evidence: dict[str, Any] | None = None
        psrr_ac_psf_evidence: dict[str, dict[str, Any]] = {}
        simulation_warnings = list(result.warnings)
        if (
            _differential_pair_has_real_tail(topology_variant)
            and metrics["tail_device_saturation_region"] != 1.0
        ):
            analysis_warnings.append(
                "operating-point constraint: MNTAIL is not in saturation at "
                "the DC point; use tail_device_saturation_region to evaluate "
                "feasibility"
            )
        if (
            _differential_pair_has_current_mirror_load(topology_variant)
            and metrics["both_load_saturation_region"] != 1.0
        ):
            analysis_warnings.append(
                "operating-point constraint: MP0/MP1 are not both in saturation; "
                "use both_load_saturation_region or "
                "all_signal_devices_saturation_region to evaluate feasibility"
            )
        if analysis in {"ac", "psrr"}:
            assert isinstance(ac_sweep, dict)
            ac_psf_evidence = _spectre_ac_file_evidence_from_result(result)
            ac_metrics, ac_diagnostics = _differential_pair_ac_metrics_from_result(
                result.data, ac_sweep, topology_variant
            )
            metrics.update(ac_metrics)
            analysis_issues.extend(
                str(value) for value in ac_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in ac_diagnostics.get("warnings", [])
            )
            if metrics["both_saturation_region"] != 1.0:
                analysis_warnings.append(
                    "operating-point constraint: differential AC branches are "
                    "not both in saturation; use both_saturation_region to "
                    "evaluate feasibility"
                )
            differential_ac_complete = (
                bool(ac_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
            analysis_complete = differential_ac_complete
            if analysis == "ac" and (
                "tail_output_resistance_ohm" in parameters
                or _differential_pair_has_real_tail(topology_variant)
            ):
                common_mode_dir = work_dir / "common_mode"
                common_mode_dir.mkdir()
                common_mode_wrapper = (
                    common_mode_dir / "differential_pair_common_mode_from_oa.scs"
                )
                common_mode_deck = _differential_pair_testbench_deck(
                    profile,
                    parameters,
                    netlist_evidence["remote_netlist_path"],
                    analysis="ac",
                    ac_sweep=ac_sweep,
                    ac_mode="common_mode",
                    topology_variant=topology_variant,
                    operating_condition=payload.get("operating_condition"),
                )
                common_mode_wrapper.write_text(
                    common_mode_deck, encoding="utf-8"
                )
                common_mode_remote_wrapper = (
                    f"{netlist_evidence['remote_run_dir']}/"
                    + (
                        f"input_from_oa_{condition_slug}_ac_common_mode.scs"
                        if condition_slug is not None
                        else "input_from_oa_ac_common_mode.scs"
                    )
                )
                _upload_file(
                    client,
                    common_mode_wrapper,
                    common_mode_remote_wrapper,
                    timeout=min(timeout, 60),
                )
                common_mode_simulator = _create_spectre_simulator(
                    SpectreSimulator,
                    client,
                    spectre_cmd=spectre_cmd,
                    timeout=timeout,
                    work_dir=common_mode_dir,
                    keep_remote_files=False,
                    remote_run_dir=netlist_evidence["remote_run_dir"],
                )
                common_mode_result = common_mode_simulator.run_simulation(
                    common_mode_wrapper, {}
                )
                if not common_mode_result.ok:
                    detail = _spectre_failure_detail(
                        common_mode_result, common_mode_dir
                    )
                    raise RuntimeError(
                        f"Spectre common-mode simulation failed: {detail}"
                    )
                common_mode_dc_data, common_mode_dc_psf_evidence = (
                    _common_source_dc_data_from_result(common_mode_result)
                )
                common_mode_dc_metrics, common_mode_operating_point = (
                    _differential_pair_metrics_from_result(
                        common_mode_dc_data, parameters, topology_variant
                    )
                )
                common_mode_operating_point["raw_files"] = (
                    common_mode_dc_psf_evidence
                )
                dc_consistency_names = (
                    "branch_p_current_ua",
                    "branch_n_current_ua",
                    "tail_current_ua",
                    "supply_current_ua",
                    "tail_voltage_v",
                    "output_common_mode_v",
                    "minimum_saturation_margin_v",
                )
                if _differential_pair_has_source_degeneration(topology_variant):
                    dc_consistency_names += (
                        "source_p_voltage_v",
                        "source_n_voltage_v",
                        "max_source_current_mismatch_percent",
                    )
                if _differential_pair_has_current_mirror_load(topology_variant):
                    dc_consistency_names += (
                        "current_mirror_current_mismatch_percent",
                        "minimum_load_saturation_margin_v",
                    )
                _assert_parameter_consistency(
                    {name: metrics[name] for name in dc_consistency_names},
                    {
                        name: common_mode_dc_metrics[name]
                        for name in dc_consistency_names
                    },
                    expected_label="differential-run DC operating point",
                    actual_label="common-mode-run DC operating point",
                )
                (
                    common_mode_metrics,
                    common_mode_ac_diagnostics,
                ) = _differential_pair_common_mode_ac_metrics_from_result(
                    common_mode_result.data, ac_sweep, topology_variant
                )
                cmrr_metrics, cmrr_diagnostics = (
                    _differential_pair_cmrr_metrics_from_results(
                        result.data,
                        common_mode_result.data,
                        ac_sweep,
                        topology_variant,
                    )
                )
                metrics.update(common_mode_metrics)
                metrics.update(cmrr_metrics)
                common_mode_issues = [
                    str(value)
                    for value in common_mode_ac_diagnostics.get("issues", [])
                ]
                common_mode_warnings = [
                    str(value)
                    for value in common_mode_ac_diagnostics.get("warnings", [])
                ]
                common_mode_reference = common_mode_ac_diagnostics.get(
                    "reference", {}
                )
                common_mode_reference_complete = (
                    isinstance(common_mode_reference, dict)
                    and common_mode_reference.get("status") == "flat"
                )
                if not common_mode_reference_complete:
                    analysis_issues.append(
                        "common-mode AC low-frequency reference is not flat"
                    )
                analysis_warnings.extend(
                    "common-mode standalone bandwidth not used for CMRR acceptance: "
                    f"{value}"
                    for value in common_mode_issues
                )
                analysis_warnings.extend(
                    f"common-mode AC: {value}" for value in common_mode_warnings
                )
                analysis_issues.extend(
                    f"CMRR response: {value}"
                    for value in cmrr_diagnostics.get("issues", [])
                )
                analysis_warnings.extend(
                    f"CMRR response: {value}"
                    for value in cmrr_diagnostics.get("warnings", [])
                )
                analysis_complete = (
                    differential_ac_complete
                    and common_mode_reference_complete
                    and bool(cmrr_diagnostics.get("analysis_complete", False))
                    and not analysis_issues
                )
            elif analysis == "psrr":
                dc_consistency_names = (
                    "branch_p_current_ua",
                    "branch_n_current_ua",
                    "tail_current_ua",
                    "supply_current_ua",
                    "tail_voltage_v",
                    "output_common_mode_v",
                    "minimum_saturation_margin_v",
                )
                if _differential_pair_has_source_degeneration(topology_variant):
                    dc_consistency_names += (
                        "source_p_voltage_v",
                        "source_n_voltage_v",
                        "max_source_current_mismatch_percent",
                    )
                if _differential_pair_has_current_mirror_load(topology_variant):
                    dc_consistency_names += (
                        "current_mirror_current_mismatch_percent",
                        "minimum_load_saturation_margin_v",
                    )
                for label, ac_mode in (
                    ("positive", "positive_supply"),
                    ("negative", "negative_supply"),
                ):
                    supply_dir = work_dir / f"{label}_supply"
                    supply_dir.mkdir()
                    supply_wrapper = (
                        supply_dir
                        / f"differential_pair_{label}_supply_from_oa.scs"
                    )
                    supply_deck = _differential_pair_testbench_deck(
                        profile,
                        parameters,
                        netlist_evidence["remote_netlist_path"],
                        analysis="ac",
                        ac_sweep=ac_sweep,
                        ac_mode=ac_mode,
                        topology_variant=topology_variant,
                        operating_condition=payload.get("operating_condition"),
                    )
                    supply_wrapper.write_text(supply_deck, encoding="utf-8")
                    supply_remote_wrapper = (
                        f"{netlist_evidence['remote_run_dir']}/"
                        + (
                            f"input_from_oa_{condition_slug}_ac_{label}_supply.scs"
                            if condition_slug is not None
                            else f"input_from_oa_ac_{label}_supply.scs"
                        )
                    )
                    _upload_file(
                        client,
                        supply_wrapper,
                        supply_remote_wrapper,
                        timeout=min(timeout, 60),
                    )
                    supply_simulator = _create_spectre_simulator(
                        SpectreSimulator,
                        client,
                        spectre_cmd=spectre_cmd,
                        timeout=timeout,
                        work_dir=supply_dir,
                        keep_remote_files=False,
                        remote_run_dir=netlist_evidence["remote_run_dir"],
                    )
                    supply_result = supply_simulator.run_simulation(
                        supply_wrapper, {}
                    )
                    if not supply_result.ok:
                        detail = _spectre_failure_detail(
                            supply_result, supply_dir
                        )
                        raise RuntimeError(
                            f"Spectre {label}-supply simulation failed: {detail}"
                        )
                    supply_dc_data, supply_dc_psf_evidence = (
                        _common_source_dc_data_from_result(supply_result)
                    )
                    supply_dc_metrics, supply_operating_point = (
                        _differential_pair_metrics_from_result(
                            supply_dc_data, parameters, topology_variant
                        )
                    )
                    supply_operating_point["raw_files"] = supply_dc_psf_evidence
                    psrr_ac_psf_evidence[label] = (
                        _spectre_ac_file_evidence_from_result(supply_result)
                    )
                    _assert_parameter_consistency(
                        {
                            name: metrics[name]
                            for name in dc_consistency_names
                        },
                        {
                            name: supply_dc_metrics[name]
                            for name in dc_consistency_names
                        },
                        expected_label="differential-run DC operating point",
                        actual_label=(
                            f"{label}-supply-run DC operating point"
                        ),
                    )
                    psrr_results[label] = supply_result
                    psrr_decks[label] = supply_deck
                    psrr_remote_wrappers[label] = supply_remote_wrapper
                    psrr_operating_points[label] = supply_operating_point
                    simulation_warnings.extend(supply_result.warnings)
                psrr_metrics, psrr_diagnostics = (
                    _differential_pair_psrr_metrics_from_results(
                        result.data,
                        psrr_results["positive"].data,
                        psrr_results["negative"].data,
                        ac_sweep,
                        topology_variant,
                    )
                )
                metrics.update(psrr_metrics)
                analysis_issues.extend(
                    str(value) for value in psrr_diagnostics.get("issues", [])
                )
                analysis_warnings.extend(
                    str(value)
                    for value in psrr_diagnostics.get("warnings", [])
                )
                analysis_complete = (
                    differential_ac_complete
                    and bool(psrr_diagnostics.get("analysis_complete", False))
                    and not analysis_issues
                )
        elif analysis == "transient":
            assert isinstance(linearity_sweep, dict)
            linearity_metrics, linearity_diagnostics = (
                _differential_pair_linearity_metrics_from_result(
                    getattr(result, "metadata", {}),
                    linearity_sweep,
                    vdd_v=parameters["vdd_v"],
                    topology_variant=topology_variant,
                )
            )
            metrics.update(linearity_metrics)
            analysis_issues.extend(
                str(value) for value in linearity_diagnostics.get("issues", [])
            )
            analysis_warnings.extend(
                str(value) for value in linearity_diagnostics.get("warnings", [])
            )
            if metrics["both_saturation_region"] != 1.0:
                analysis_warnings.append(
                    "operating-point constraint: differential transient branches "
                    "are not both in saturation; use both_saturation_region to "
                    "evaluate feasibility"
                )
            analysis_complete = (
                bool(linearity_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
        elif analysis == "noise":
            assert isinstance(noise_sweep, dict)
            noise_metrics, noise_diagnostics = (
                _differential_pair_noise_metrics_from_result(
                    result, noise_sweep, topology_variant
                )
            )
            metrics.update(noise_metrics)
            if metrics["both_saturation_region"] != 1.0:
                analysis_warnings.append(
                    "operating-point constraint: differential noise branches are "
                    "not both in saturation; use both_saturation_region to "
                    "evaluate feasibility"
                )
            analysis_complete = (
                bool(noise_diagnostics.get("analysis_complete", False))
                and not analysis_issues
            )
        metric_sources = {name: "eda_result" for name in metrics}
        metric_sources["both_saturation_region"] = "software_inference"
        metric_sources["all_signal_devices_saturation_region"] = (
            "software_inference"
        )
        for name in (
            "both_load_saturation_region",
            "current_mirror_current_mismatch_percent",
            "minimum_load_saturation_margin_v",
        ):
            if name in metric_sources:
                metric_sources[name] = "software_inference"
        for name in (
            "source_p_current_mismatch_percent",
            "source_n_current_mismatch_percent",
            "max_source_current_mismatch_percent",
        ):
            if name in metric_sources:
                metric_sources[name] = "software_inference"
        real_tail = _differential_pair_has_real_tail(topology_variant)
        tail_testbench_name = "tail_bias_v" if real_tail else "tail_current_ua"
        testbench_values = {
            "analysis": analysis,
            tail_testbench_name: parameters[tail_testbench_name],
            "common_mode_v": parameters["common_mode_v"],
            "vdd_v": parameters["vdd_v"],
        }
        testbench_value_sources = {
            "analysis": str(payload.get("analysis_source", "software_inference")),
            **{
                name: (
                    "user_input"
                    if name in payload.get("parameters", {})
                    else "software_inference"
                )
                for name in (tail_testbench_name, "common_mode_v", "vdd_v")
            },
        }
        operating_condition = payload.get("operating_condition")
        model_manifest = _common_source_model_manifest(
            profile,
            operating_condition if isinstance(operating_condition, dict) else None,
        )
        if isinstance(operating_condition, dict):
            testbench_values["operating_condition"] = dict(operating_condition)
            testbench_value_sources["operating_condition"] = {
                name: "user_input" for name in operating_condition
            }
        if "tail_output_resistance_ohm" in parameters:
            testbench_values["tail_output_resistance_ohm"] = parameters[
                "tail_output_resistance_ohm"
            ]
            testbench_value_sources["tail_output_resistance_ohm"] = (
                "user_input"
                if "tail_output_resistance_ohm" in payload.get("parameters", {})
                else "software_inference"
            )
        if "load_ff" in parameters:
            testbench_values["load_ff"] = parameters["load_ff"]
            testbench_value_sources["load_ff"] = (
                "user_input"
                if "load_ff" in payload.get("parameters", {})
                else "software_inference"
            )
        if analysis in {"ac", "psrr"}:
            assert isinstance(ac_sweep, dict)
            testbench_values["ac_sweep"] = dict(ac_sweep)
            testbench_values["differential_ac_stimulus"] = {
                "inp_magnitude_v": 0.5,
                "inp_phase_deg": 0.0,
                "inn_magnitude_v": 0.5,
                "inn_phase_deg": 180.0,
                "differential_input_magnitude_v": 1.0,
            }
            user_sweep_fields = set(payload.get("ac_sweep_user_fields", []))
            testbench_value_sources["ac_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in ac_sweep
            }
            testbench_value_sources["differential_ac_stimulus"] = (
                "software_inference"
            )
            if common_mode_ac_diagnostics is not None:
                testbench_values["common_mode_ac_stimulus"] = {
                    "inp_magnitude_v": 1.0,
                    "inp_phase_deg": 0.0,
                    "inn_magnitude_v": 1.0,
                    "inn_phase_deg": 0.0,
                    "common_mode_input_magnitude_v": 1.0,
                }
                testbench_value_sources["common_mode_ac_stimulus"] = (
                    "software_inference"
                )
            if analysis == "psrr":
                testbench_values["psrr_ac_stimuli"] = {
                    "positive_supply": {
                        "vdd_magnitude_v": 1.0,
                        "vss_magnitude_v": 0.0,
                    },
                    "negative_supply": {
                        "vdd_magnitude_v": 0.0,
                        "vss_magnitude_v": 1.0,
                    },
                    "input_and_bias_reference": (
                        "ideal ground-referenced DC sources"
                    ),
                    "netlist_binding": "same_si_netlist_sha256",
                }
                testbench_value_sources["psrr_ac_stimuli"] = (
                    "software_inference"
                )
        elif analysis == "transient":
            assert isinstance(linearity_sweep, dict)
            testbench_values["linearity_sweep"] = dict(linearity_sweep)
            testbench_values["differential_transient_stimulus"] = {
                "inp_peak_fraction_of_declared_differential_input": 0.5,
                "inn_peak_fraction_of_declared_differential_input": -0.5,
                "common_mode_v": parameters["common_mode_v"],
            }
            user_sweep_fields = set(
                payload.get("linearity_sweep_user_fields", [])
            )
            testbench_value_sources["linearity_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in linearity_sweep
            }
            testbench_value_sources["differential_transient_stimulus"] = (
                "software_inference"
            )
        elif analysis == "noise":
            assert isinstance(noise_sweep, dict)
            testbench_values["noise_sweep"] = dict(noise_sweep)
            testbench_values["differential_noise_stimulus"] = {
                "source": "VIN_DIFF",
                "source_positive_node": "VDIFF",
                "source_negative_node": "0",
                "effective_positive_node": "INP",
                "effective_negative_node": "INN",
                "inp_gain_v_per_v": 0.5,
                "inn_gain_v_per_v": -0.5,
                "ac_magnitude_v": 1.0,
                "dc_common_mode_v": parameters["common_mode_v"],
            }
            user_sweep_fields = set(payload.get("noise_sweep_user_fields", []))
            testbench_value_sources["noise_sweep"] = {
                name: (
                    "user_input"
                    if name in user_sweep_fields
                    else "software_inference"
                )
                for name in noise_sweep
            }
            testbench_value_sources["differential_noise_stimulus"] = (
                "software_inference"
            )
        metric_sources["tail_current_mismatch_percent"] = "software_inference"
        if real_tail:
            metric_sources["tail_current_ua"] = "eda_result"
            metric_sources["tail_device_saturation_region"] = "software_inference"
            metric_sources["tail_device_saturation_margin_v"] = "software_inference"
            metric_sources["tail_device_branch_sum_mismatch_percent"] = (
                "software_inference"
            )
        else:
            metric_sources["tail_current_ua"] = testbench_value_sources[
                "tail_current_ua"
            ]
            metric_sources["ideal_tail_source_current_ua"] = (
                testbench_value_sources["tail_current_ua"]
            )
        if "tail_output_resistance_ohm" in parameters:
            metric_sources["tail_current_ua"] = "software_inference"
            metric_sources["tail_output_resistance_ohm"] = (
                testbench_value_sources["tail_output_resistance_ohm"]
            )
            metric_sources["tail_output_resistor_current_ua"] = (
                "software_inference"
            )
        for name in metrics:
            if "cmrr" in name:
                metric_sources[name] = "software_inference"
            if "psrr" in name:
                metric_sources[name] = "software_inference"
        if real_tail:
            operating_point["source_value_sources"] = {
                "supply_source_current_a": "eda_result",
                "tail_device_current_a": "eda_result",
                "total_tail_sink_current_a": "eda_result",
            }
        else:
            operating_point["source_value_sources"] = {
                "supply_source_current_a": "eda_result",
                "ideal_tail_source_setpoint_a": testbench_value_sources[
                    "tail_current_ua"
                ],
                "tail_output_resistor_current_a": "software_inference",
                "total_tail_sink_current_a": "software_inference",
            }
        if common_mode_operating_point is not None:
            common_mode_operating_point["source_value_sources"] = dict(
                operating_point["source_value_sources"]
            )
        for supply_operating_point in psrr_operating_points.values():
            supply_operating_point["source_value_sources"] = dict(
                operating_point["source_value_sources"]
            )
        return {
            "parameters": parameters,
            "metrics": metrics,
            "metric_sources": metric_sources,
            "analysis_complete": analysis_complete,
            "analysis_issues": analysis_issues,
            "analysis_warnings": analysis_warnings,
            "scalar_count": len(result.data) + (
                len(common_mode_result.data)
                if common_mode_result is not None
                else 0
            )
            + sum(len(value.data) for value in psrr_results.values()),
            "tool_version": tool_version,
            "warnings": simulation_warnings[:20],
            "evidence": {
                "side_effects": {
                    "oa_access_performed": True,
                    "oa_write_performed": False,
                    "remote_compute_performed": True,
                },
                "process_lifecycle": process_lifecycle,
                "schematic_readback": {
                    "source": "bridge_readback",
                    "target": payload["target"],
                    "semantic_parameters": oa_parameters,
                    "device_geometry": oa_geometry,
                    "tail_device_geometry": oa_tail_geometry,
                    "current_mirror_load_geometry": oa_current_mirror_geometry,
                    "topology_variant": topology_variant,
                },
                "netlist": {
                    "source": "eda_result",
                    "generator": "Cadence si -batch",
                    "remote_path": netlist_evidence["remote_netlist_path"],
                    "sha256": netlist_evidence["netlist_sha256"],
                    "semantic_parameters": netlist_parameters,
                    "device_geometry": netlist_geometry,
                    "tail_device_geometry": netlist_tail_geometry,
                    "current_mirror_load_geometry": (
                        netlist_current_mirror_geometry
                    ),
                    "topology_variant": netlist_evidence["parsed"][
                        "topology_variant"
                    ],
                    "parameter_consistency": "matched",
                    "instances": netlist_evidence["parsed"]["instances"],
                    "si_log_tail": netlist_evidence["si_log_tail"],
                },
                "testbench": {
                    "source": "software_inference",
                    "remote_path": remote_wrapper,
                    "sha256": hashlib.sha256(deck.encode("utf-8")).hexdigest(),
                    "values": testbench_values,
                    "value_sources": testbench_value_sources,
                    "tail_source_location": (
                        "OA schematic MNTAIL; external wrapper supplies BIAS only"
                        if real_tail
                        else "external_wrapper_only"
                    ),
                    "output_contract": (
                        {
                            "input": "INP-INN",
                            "primary_output": "OUTN",
                            "mirror_reference": "OUTP",
                            "mode": "single_ended_outn",
                        }
                        if _differential_pair_has_current_mirror_load(
                            topology_variant
                        )
                        else {
                            "input": "INP-INN",
                            "primary_output": "OUTP-OUTN",
                            "mode": "differential",
                        }
                    ),
                    "model_resolution_source": "pdk_profile",
                    "model_configuration": model_manifest,
                    "common_mode_pair": (
                        {
                            "remote_path": common_mode_remote_wrapper,
                            "sha256": hashlib.sha256(
                                common_mode_deck.encode("utf-8")
                            ).hexdigest(),
                            "netlist_binding": "same_si_netlist_sha256",
                        }
                        if common_mode_deck is not None
                        and common_mode_remote_wrapper is not None
                        else {"status": "not_requested"}
                    ),
                    "psrr_pair": (
                        {
                            label: {
                                "remote_path": psrr_remote_wrappers[label],
                                "sha256": hashlib.sha256(
                                    psrr_decks[label].encode("utf-8")
                                ).hexdigest(),
                                "netlist_binding": "same_si_netlist_sha256",
                            }
                            for label in ("positive", "negative")
                        }
                        if psrr_decks
                        else {"status": "not_requested"}
                    ),
                },
                "operating_point": {
                    "source": "eda_result",
                    **operating_point,
                    "operating_region_source": "software_inference",
                },
                "ac_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **ac_diagnostics,
                        **(
                            {"raw_files": ac_psf_evidence}
                            if ac_psf_evidence is not None
                            else {}
                        ),
                    }
                    if ac_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "common_mode_operating_point": (
                    {
                        "source": "eda_result",
                        **common_mode_operating_point,
                        "operating_region_source": "software_inference",
                        "consistency_with_differential_run": "matched",
                    }
                    if common_mode_operating_point is not None
                    else {"status": "not_requested"}
                ),
                "common_mode_ac_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **common_mode_ac_diagnostics,
                        "acceptance_role": "low_frequency_reference_and_shape",
                        "standalone_bandwidth_required": False,
                        "reference_complete": (
                            isinstance(
                                common_mode_ac_diagnostics.get("reference"), dict
                            )
                            and common_mode_ac_diagnostics["reference"].get(
                                "status"
                            )
                            == "flat"
                        ),
                    }
                    if common_mode_ac_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "cmrr": (
                    {
                        "source": "software_inference",
                        "definition": (
                            "differential low-frequency gain divided by common-mode "
                            "low-frequency gain"
                        ),
                        "tail_model": (
                            "OA MNTAIL biased by external DC voltage"
                            if real_tail
                            else "external ideal DC sink in parallel with explicit "
                            "finite small-signal output resistance"
                        ),
                        "netlist_binding": "same_si_netlist_sha256",
                        **cmrr_diagnostics,
                    }
                    if cmrr_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "psrr_supply_operating_points": (
                    {
                        "source": "eda_result",
                        "consistency_with_differential_run": "matched",
                        **psrr_operating_points,
                    }
                    if psrr_operating_points
                    else {"status": "not_requested"}
                ),
                "psrr": (
                    {
                        "source": "software_inference",
                        "netlist_binding": "same_si_netlist_sha256",
                        "input_ac_results": {
                            "differential": {
                                "source": "eda_result",
                                "raw_files": ac_psf_evidence,
                            },
                            **{
                                f"{label}_supply": {
                                    "source": "eda_result",
                                    "raw_files": psrr_ac_psf_evidence[label],
                                }
                                for label in ("positive", "negative")
                            },
                        },
                        **psrr_diagnostics,
                    }
                    if psrr_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "linearity_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **linearity_diagnostics,
                    }
                    if linearity_diagnostics is not None
                    else {"status": "not_requested"}
                ),
                "noise_response": (
                    {
                        "source": "eda_result",
                        "extraction_source": "software_inference",
                        **noise_diagnostics,
                    }
                    if noise_diagnostics is not None
                    else {"status": "not_requested"}
                ),
            },
        }


_ACTIONS = {
    "audit_resources": audit_resources,
    "probe": probe,
    "probe_spectre_environment": probe_spectre_environment,
    "characterize_mos_devices": characterize_mos_devices,
    "simulate_netlist_preview": simulate_netlist_preview,
    "prepare_maestro": prepare_maestro,
    "capture_focused_maestro": capture_focused_maestro,
    "run_background_maestro": run_background_maestro,
    "apply_maestro_variables": apply_maestro_variables,
    "apply_maestro_corners": apply_maestro_corners,
    "apply_maestro_setup": apply_maestro_setup,
    "inspect_existing_schematic": inspect_existing_schematic,
    "generate_existing_schematic_symbol": generate_existing_schematic_symbol,
    "inspect_existing_schematic_symbol": inspect_existing_schematic_symbol,
    "transform_existing_schematic_topology_delta": (
        transform_existing_schematic_topology_delta
    ),
    "apply_existing_schematic_parameters": apply_existing_schematic_parameters,
    "discover_existing_schematic_parameter_binding": (
        discover_existing_schematic_parameter_binding
    ),
    "simulate_existing_schematic": simulate_existing_schematic,
    "simulate_existing_schematic_stages": simulate_existing_schematic_stages,
    "create_inverter": create_inverter,
    "inspect_inverter": inspect_inverter,
    "transform_inverter_testbench": transform_inverter_testbench,
    "apply_inverter_parameters": apply_inverter_parameters,
    "simulate_inverter": simulate_inverter,
    "create_common_source": create_common_source,
    "inspect_common_source": inspect_common_source,
    "transform_common_source_source_degeneration": (
        transform_common_source_source_degeneration
    ),
    "preflight_common_source_source_degeneration": (
        preflight_common_source_source_degeneration
    ),
    "apply_common_source_parameters": apply_common_source_parameters,
    "simulate_common_source": simulate_common_source,
    "create_differential_pair": create_differential_pair,
    "inspect_differential_pair": inspect_differential_pair,
    "transform_differential_pair_tail_device": (
        transform_differential_pair_tail_device
    ),
    "transform_differential_pair": transform_differential_pair,
    "apply_differential_pair_parameters": apply_differential_pair_parameters,
    "simulate_differential_pair": simulate_differential_pair,
}


def main() -> int:
    global _WORKER_RESOURCE_TRACKING

    result: dict[str, Any]
    _WORKER_RESOURCE_TRACKING = True
    watchdog = _start_worker_parent_watchdog()
    try:
        request = json.loads(sys.stdin.read())
        action = request.get("action")
        if action not in _ACTIONS:
            raise ValueError(f"unsupported worker action: {action}")
        data = _ACTIONS[action](request.get("payload", {}))
        result = {"ok": True, "data": data}
        return_code = 0
    except Exception as exc:  # worker boundary: return a compact structured failure
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return_code = 1
    finally:
        if watchdog is not None:
            watchdog[0].set()
        cleanup_errors = _close_worker_resources()
        _WORKER_RESOURCE_TRACKING = False
        cancel_file = os.getenv("VDA_WORKER_CANCEL_FILE")
        if cancel_file:
            Path(cancel_file).unlink(missing_ok=True)
    if cleanup_errors:
        cleanup_detail = "; ".join(cleanup_errors)
        if result.get("ok", False):
            result = {
                "ok": False,
                "error": f"Bridge worker resource cleanup failed: {cleanup_detail}",
            }
        else:
            result["error"] = (
                f"{result.get('error', 'Bridge worker failed')}; "
                f"resource cleanup failed: {cleanup_detail}"
            )
        return_code = 1
    print(_MARKER + json.dumps(result, ensure_ascii=False, default=str))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
