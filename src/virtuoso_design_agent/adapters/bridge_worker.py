"""Worker executed by the virtuoso-bridge Python environment.

The process reads one JSON request from stdin and emits one marker-prefixed JSON
result. It intentionally keeps Bridge imports out of the main VDA environment.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from virtuoso_design_agent.adapters.base import merge_analysis_bundle
from virtuoso_design_agent.metrics import (
    aggregate_common_source_linearity_metrics,
    extract_common_source_ac_metrics,
    extract_common_source_dc_metrics,
    extract_common_source_linearity_point_metrics,
    extract_common_source_noise_metrics,
    extract_dc_supply_metrics,
    extract_inverter_metrics,
    extract_supply_metrics,
)

_MARKER = "VDA_RESULT="


def _client():
    from virtuoso_bridge import VirtuosoClient

    client = VirtuosoClient.from_env()
    ssh_runner = getattr(client, "ssh_runner", None)
    if ssh_runner is not None:
        # Required by the verified Windows + nics4304 setup.
        ssh_runner._persistent_shell_enabled = False
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


def _existing_schematic_summary(data: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "bridge_schematic": data,
    }


def _assert_inverter(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> None:
    names = {str(item.get("name")) for item in data.get("instances", [])}
    pins = set(data.get("pins", {}).keys())
    if names != {"MN0", "MP0"}:
        raise RuntimeError(f"existing schematic is not the VDA inverter: instances={sorted(names)}")
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
    if profile is not None:
        by_name = {str(item.get("name")): item for item in data.get("instances", [])}
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


def _assert_common_source(
    data: dict[str, Any], profile: dict[str, Any] | None = None
) -> str:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    names = set(by_name)
    base_names = {"MN0", "RD0"}
    degenerated_names = base_names | {"RS0"}
    if names == base_names:
        variant = "common_source"
    elif names == degenerated_names:
        variant = "source_degenerated_common_source"
    else:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"instances={sorted(names)}"
        )
    required_pins = {"IN", "OUT", "VDD", "VSS"}
    required_nets = set(required_pins)
    if variant == "source_degenerated_common_source":
        required_nets.add("NSRC")
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
            "D": "OUT",
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
        for name, expected in expected_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    "existing schematic is not the VDA common-source stage: "
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
    return semantic_parameters


def _positive_device_count(value: Any, label: str) -> float:
    try:
        count = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot parse {label}: {value!r}") from exc
    if count <= 0:
        raise RuntimeError(f"{label} must be positive, got {value!r}")
    return count


def _common_source_device_geometry_from_schematic(
    data: dict[str, Any],
) -> dict[str, float]:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    if "MN0" not in by_name:
        raise RuntimeError("common-source readback is missing MN0")
    params = by_name["MN0"].get("params", {})
    fingers = _positive_device_count(params.get("fingers", 1), "MN0.fingers")
    multiplicity = _positive_device_count(params.get("m", 1), "MN0.m")
    if params.get("Wfg") is not None:
        finger_width_um = _length_um(params["Wfg"])
    elif params.get("w") is not None:
        finger_width_um = _length_um(params["w"]) / fingers
    else:
        raise RuntimeError("common-source readback is missing MN0 Wfg/w")
    return {
        "finger_width_um": finger_width_um,
        "fingers": fingers,
        "multiplicity": multiplicity,
        "total_width_um": finger_width_um * fingers * multiplicity,
    }


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
    return {
        "nmos_width_um": float(
            supplied.get(
                "nmos_width_um", device_parameters.get("nmos_width_um", 0.5)
            )
        ),
        "pmos_width_um": float(
            supplied.get(
                "pmos_width_um", device_parameters.get("pmos_width_um", 1.0)
            )
        ),
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
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params

    client.open_window(library, cell, view="schematic")
    set_instance_params(
        client,
        "MN0",
        wf=_um(parameters["nmos_width_um"]),
        l=_um(parameters["length_um"]),
        nf="1",
        m="1",
        param_filters=None,
    )
    set_instance_params(
        client,
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
    return updates


def _apply_common_source_parameters(
    client,
    library: str,
    cell: str,
    parameters: dict[str, float],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params

    persistable = {
        name: float(parameters[name])
        for name in (
            "device_width_um",
            "length_um",
            "load_resistance_ohm",
            "source_resistance_ohm",
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

    client.open_window(library, cell, view="schematic")
    instance_updates = _common_source_instance_parameter_updates(persistable)
    if "MN0" in instance_updates:
        set_instance_params(
            client,
            "MN0",
            param_filters=None,
            **instance_updates["MN0"],
        )
    if "RD0" in instance_updates:
        set_instance_params(
            client,
            "RD0",
            param_filters=None,
            **instance_updates["RD0"],
        )
    if "RS0" in instance_updates:
        set_instance_params(
            client,
            "RS0",
            param_filters=None,
            **instance_updates["RS0"],
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


def _apply_explicit_instance_parameters(
    client,
    library: str,
    cell: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.params import set_instance_params

    requested = _requested_instance_parameters(payload)
    current = _read_schematic(client, library, cell)
    if payload["circuit"] == "inverter":
        _assert_inverter(current, payload["profile"])
        summarize = _summary
    elif payload["circuit"] == "common_source":
        _assert_common_source(current, payload["profile"])
        summarize = _common_source_summary
    elif payload["circuit"] == "existing_schematic":
        summarize = _existing_schematic_summary
    else:
        raise RuntimeError(
            f"explicit instance parameter writes are unsupported for {payload['circuit']}"
        )

    current_parameters = _instance_parameters_from_schematic(current)
    missing_instances = sorted(set(requested) - set(current_parameters))
    if missing_instances:
        raise RuntimeError(
            "explicit parameter write targets missing instances: "
            + ", ".join(missing_instances)
        )
    client.open_window(library, cell, view="schematic")
    applied_parameters: dict[str, dict[str, str]] = {}
    for instance, parameters in requested.items():
        applied = set_instance_params(
            client,
            instance,
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
    repair_applied_parameters: dict[str, dict[str, str]] = {}
    repair_reason: str | None = None
    try:
        confirmed = _verify_instance_parameter_values(
            client, library, cell, applied_parameters
        )
    except ParameterReadbackMismatch as error:
        repair_reason = str(error)
        for instance, parameters in requested.items():
            repaired: dict[str, str] = {}
            for name, value in parameters.items():
                applied = set_instance_params(
                    client,
                    instance,
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
            confirmed = _verify_instance_parameter_values(
                client, library, cell, applied_parameters
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
        "readback": summarize(updated),
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
    summary["confirmed_instance_parameters"] = _verify_instance_parameter_values(
        client, library, cell, expected
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


def _maestro_tests_readback(client, session: str) -> list[str]:
    readback = client.execute_skill(
        f'maeGetSetup(?session "{session}")', timeout=30
    )
    errors = getattr(readback, "errors", None) or []
    if errors:
        raise RuntimeError(f"Maestro setup readback failed: {errors[0]}")
    raw_tests = str(getattr(readback, "output", "") or "")
    return re.findall(r'"([^"\\]+)"', raw_tests)


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


def _read_maestro_variable(
    client, get_var, update: dict[str, Any], *, session: str
) -> str | None:
    scope = str(update.get("scope") or "global")
    name = str(update["name"])
    if scope == "global":
        raw = get_var(client, name, session=session)
    else:
        scope_name = str(update["scope_name"])
        readback = client.execute_skill(
            f'maeGetVar("{name}" ?typeName "{scope}" '
            f'?typeValue "{scope_name}" ?session "{session}")',
            timeout=30,
        )
        errors = getattr(readback, "errors", None) or []
        if errors:
            raise RuntimeError(
                f"Maestro variable readback failed for "
                f"{_maestro_variable_identity(update)}: {errors[0]}"
            )
        raw = getattr(readback, "output", "")
    return _normalized_maestro_variable_value(raw)


def _write_maestro_variable(
    client, set_var, update: dict[str, Any], *, session: str
) -> None:
    scope = str(update.get("scope") or "global")
    kwargs: dict[str, str] = {"session": session}
    if scope != "global":
        kwargs.update(
            type_name=scope,
            type_value=f'("{update["scope_name"]}")',
        )
    set_var(client, str(update["name"]), str(update["value"]), **kwargs)


def _maestro_variable_fingerprint(
    tests: list[str],
    corners: list[str] | None,
    values: dict[str, str | None],
) -> str:
    canonical = json.dumps(
        {"tests": tests, "corners": corners, "declared_variables": values},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    design_view = str(settings.get("design_view") or "schematic")
    test_name = str(settings.get("test_name") or "VDA")
    simulator = str(settings.get("simulator") or "spectre")
    if simulator != "spectre":
        raise RuntimeError("ADE prepare currently supports only the Spectre simulator")

    client = _client()
    if not _cellview_exists(client, library, cell, design_view):
        raise RuntimeError(
            f"ADE prepare requires existing design {library}/{cell}/{design_view}"
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
            lib=library,
            cell=cell,
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
    finally:
        close_session(client, verify_session)

    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": "maestro"},
        "design": {"library": library, "cell": cell, "view": design_view},
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
    if not expected_tests or not updates:
        raise RuntimeError(
            "ADE variable patch requires expected_tests and variable updates"
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
        save_setup(client, library, cell, session=session)
    finally:
        close_session(client, session)

    persisted: dict[str, str | None] = {}
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
    return {
        "backend": "maestro",
        "target": {"library": library, "cell": cell, "view": view},
        "variable_scope": (
            "global" if variable_scopes == ["global"] else "declared_scopes"
        ),
        "variable_scopes": variable_scopes,
        "expected_tests": expected_tests,
        "tests_readback_before": tests,
        "tests_readback_after": verify_tests,
        "expected_corners": expected_corners,
        "corners_readback_before": corners,
        "corners_readback_after": verify_corners,
        "requested_variable_updates": requested,
        "requested_evidence_source": "user_input",
        "before_variables": before,
        "immediate_variables": immediate,
        "persisted_variables": persisted,
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": _maestro_variable_fingerprint(
            tests, corners, before
        ),
        "after_target_fingerprint_sha256": _maestro_variable_fingerprint(
            verify_tests, verify_corners, persisted
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
        "variable_readback_methods": {
            scope: (
                "bridge_public_get_var"
                if scope == "global"
                else "cadence_maeGetVar_via_bridge_skill_channel"
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
            "declared Maestro variable scopes matched their expected old values, "
            "were saved once, and matched after an independent reopen; tests and "
            "declared corner membership were preserved; analysis, outputs, and "
            "schematic were not changed; unlisted scope overrides and effective "
            "simulator values were not verified"
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
        list(1 o~>name o~>type o~>signal o~>expression o~>evalType
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
    if actual.get("expression") != output.get("expression"):
        return False
    return actual.get("spec") == output.get("spec")


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


def _normalized_maestro_history(value: Any) -> str:
    history = str(value or "").strip()
    if len(history) >= 2 and history.startswith('"') and history.endswith('"'):
        history = history[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", history):
        raise RuntimeError(f"Maestro returned an invalid history name: {value!r}")
    return history


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
    try:
        tests = _maestro_tests_readback(client, session)
        if not tests:
            raise RuntimeError("saved Maestro setup did not contain any tests")
        raw_history, run_status = run_and_wait(
            client,
            session=session,
            timeout=int(payload.get("timeout_seconds") or 600),
        )
        history = _normalized_maestro_history(raw_history)
        if str(run_status).strip().lower() != "done":
            raise RuntimeError(
                f"Maestro background run did not reach done status: {run_status!r}"
            )
        results = read_results(
            client,
            session,
            lib=library,
            cell=cell,
            history=history,
        )
    finally:
        close_session(client, session)

    result_history = str(results.get("history") or "")
    if result_history and result_history != history:
        raise RuntimeError(
            "Maestro structured results history does not match the history created "
            "by this run"
        )
    structured_outputs = _has_structured_ade_outputs(results)
    if settings.get("require_structured_outputs", True) and not structured_outputs:
        raise RuntimeError(
            "Maestro background run completed but did not expose a non-empty "
            "point/output/spec table"
        )

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
        "automated_simulation_performed": True,
        "oa_write_performed": False,
        "maestro_setup_write_performed": False,
        "artifacts_captured": False,
        "completion_scope": (
            "saved Maestro setup executed in a background session and the history "
            "returned for this invocation was read; history uniqueness, simulator "
            "input, and PSF artifacts were not captured by this operation"
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
        "profile": payload["profile"]["name"],
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


def _mn0_source_label_selection_operation(*, rename: bool) -> str:
    final_action = (
        'rbLabel~>theLabel = "NSRC" rbLabel' if rename else "rbLabel"
    )
    return (
        "let((rbInst rbTerm rbPin rbFig rbBBox rbCtr rbLabels rbLabel rbDx rbDy) "
        'rbInst = car(setof(x cv~>instances x~>name == "MN0")) '
        'unless(rbInst error("MN0 not found during source-degeneration transform")) '
        'rbTerm = car(setof(x rbInst~>master~>terminals x~>name == "S")) '
        'unless(rbTerm error("MN0.S not found during source-degeneration transform")) '
        "rbPin = car(rbTerm~>pins) "
        "rbFig = when(rbPin car(rbPin~>figs)) "
        "rbBBox = when(rbFig dbTransformBBox(rbFig~>bBox rbInst~>transform)) "
        "rbCtr = when(rbBBox list("
        "(xCoord(car(rbBBox)) + xCoord(cadr(rbBBox))) / 2.0 "
        "(yCoord(car(rbBBox)) + yCoord(cadr(rbBBox))) / 2.0)) "
        'unless(rbCtr error("MN0.S center could not be resolved")) '
        "rbLabels = setof(x cv~>shapes "
        'x~>objType == "label" && x~>theLabel == "VSS" && x~>xy && '
        "let((dx dy) dx = xCoord(x~>xy) - xCoord(rbCtr) "
        "dy = yCoord(x~>xy) - yCoord(rbCtr) "
        "dx * dx + dy * dy <= 0.02)) "
        'unless(length(rbLabels) == 1 error("MN0.S VSS label selection was not unique")) '
        "rbLabel = car(rbLabels) "
        f"{final_action})"
    )


def _rename_mn0_source_label_operation() -> str:
    """Rename only the VDA-created VSS label nearest the MN0 source terminal."""
    return _mn0_source_label_selection_operation(rename=True)


def _edit_existing_schematic(client, library: str, cell: str, *, timeout: int = 90):
    """Open an existing schematic for mutation without replacement semantics."""
    return client.schematic.edit(
        library, cell, mode="a", timeout=timeout
    )


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


def transform_common_source_source_degeneration(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name as inst,
        schematic_label_instance_term as label_term,
    )

    client = _client()
    library, cell = _target(payload)
    before = _read_schematic(client, library, cell)
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
    return {
        "transformed": topology_changed,
        "already_transformed": not topology_changed,
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
    if variant == "common_source":
        _preflight_mn0_source_label(client, library, cell)
    return {
        "target": payload["target"],
        "topology_variant": variant,
        "source_label_selection": (
            "unique" if variant == "common_source" else "not_applicable"
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


def inspect_existing_schematic(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    data = _read_schematic(client, library, cell)
    return _attach_targeted_parameter_verification(
        client, library, cell, payload, _existing_schematic_summary(data)
    )


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
        continuation = bool(current) and (
            raw_line[:1].isspace()
            or stripped.startswith("+")
            or current.endswith("\\")
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


def _parse_common_source_netlist(
    text: str, profile: dict[str, Any]
) -> dict[str, Any]:
    records = _logical_netlist_records(text)
    has_source_resistor = any(
        re.match(r"^RS0\s*\(", item) is not None for item in records
    )
    expected = {
        "MN0": {
            "model": profile["nmos_cell"],
            "nodes": [
                "OUT",
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
        if name == "MN0":
            width_match = re.search(r"(?:^|\s)w=([^\s\\]+)", parameter_text)
            length_match = re.search(r"(?:^|\s)l=([^\s\\]+)", parameter_text)
            if width_match is None or length_match is None:
                raise RuntimeError("si netlist is missing w/l for MN0")
            fingers_match = re.search(
                r"(?:^|\s)nf=([^\s\\]+)", parameter_text
            )
            multiplicity_match = re.search(
                r"(?:^|\s)multi=([^\s\\]+)", parameter_text
            )
            fingers = _positive_device_count(
                fingers_match.group(1) if fingers_match else 1,
                "si MN0.nf",
            )
            multiplicity = _positive_device_count(
                multiplicity_match.group(1) if multiplicity_match else 1,
                "si MN0.multi",
            )
            netlist_width_um = _length_um(width_match.group(1))
            instances[name].update(
                {
                    "netlist_width_um": netlist_width_um,
                    "finger_width_um": netlist_width_um / fingers,
                    "fingers": fingers,
                    "multiplicity": multiplicity,
                    "total_width_um": netlist_width_um * multiplicity,
                    "length_um": _length_um(length_match.group(1)),
                }
            )
        else:
            resistance_match = re.search(
                r"(?:^|\s)r=([^\s\\]+)", parameter_text
            )
            if resistance_match is None:
                raise RuntimeError(f"si netlist is missing resistance for {name}")
            instances[name]["resistance_ohm"] = _resistance_ohm(
                resistance_match.group(1)
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
    return {
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
            "source_degenerated_common_source"
            if has_source_resistor
            else "common_source"
        ),
    }


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


def _download_text(
    client, remote_path: str, local_path: Path, label: str, *, timeout: int
) -> str:
    result = client.download_file(remote_path, local_path, timeout=timeout)
    _require_bridge_result(result, f"download {label}")
    return _read_nonempty_text(local_path, label)


def _upload_file(client, local_path: Path, remote_path: str, *, timeout: int) -> None:
    result = client.upload_file(local_path, remote_path, timeout=timeout)
    _require_bridge_result(result, f"upload {local_path.name}")


def _generate_oa_netlist(
    client,
    payload: dict[str, Any],
    work_dir: Path,
    *,
    timeout: int,
) -> dict[str, Any]:
    library, cell = _target(payload)
    profile = payload["profile"]
    run_root = str(profile["remote_run_root"]).rstrip("/")
    if not run_root.startswith("/data/xum/"):
        raise RuntimeError("remote netlist run root must stay under /data/xum")
    task_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("task_id", "task")))
    run_dir = f"{run_root}/vda_{task_slug}_{uuid.uuid4().hex[:12]}"

    init_skill = "simInitEnvWithArgs({} {} {} {} \"spectre\" nil)".format(
        json.dumps(run_dir),
        json.dumps(library),
        json.dumps(cell),
        json.dumps(str(payload["target"].get("view", "schematic"))),
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
    else:
        raise RuntimeError(f"unsupported OA netlist circuit: {circuit}")
    return {
        "remote_run_dir": run_dir,
        "remote_netlist_path": remote_netlist,
        "netlist_sha256": hashlib.sha256(netlist_text.encode("utf-8")).hexdigest(),
        "parsed": parsed,
        "si_log_tail": log_text.splitlines()[-12:],
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


def _common_source_metrics_from_result(
    data: dict[str, Any], parameters: dict[str, float]
) -> tuple[dict[str, float], dict[str, Any]]:
    source_degenerated = "source_resistance_ohm" in parameters
    node_values = {
        "IN": _scalar(data, "dc_IN"),
        "OUT": _scalar(data, "dc_OUT"),
        "VDD": _scalar(data, "dc_VDD"),
        "VSS": _scalar(data, "dc_VSS"),
    }
    if source_degenerated:
        node_values["NSRC"] = _scalar(data, "dc_NSRC")
    op_values = {
        "ids_a": _operating_point_scalar(data, "MN0", "ids", "id"),
        "vgs_v": _operating_point_scalar(data, "MN0", "vgs"),
        "vds_v": _operating_point_scalar(data, "MN0", "vds"),
        "vdsat_v": _operating_point_scalar(data, "MN0", "vdsat"),
        "gm_s": _operating_point_scalar(data, "MN0", "gm"),
        "gds_s": _operating_point_scalar(data, "MN0", "gds"),
        "supply_source_current_a": _scalar(data, "dc_VDD_SRC:p"),
    }
    source_tolerance_v = 1e-5
    if abs(node_values["VDD"] - parameters["vdd_v"]) > source_tolerance_v:
        raise RuntimeError("DC VDD does not match the testbench source value")
    if abs(node_values["IN"] - parameters["bias_v"]) > source_tolerance_v:
        raise RuntimeError("DC input does not match the testbench bias value")
    if abs(node_values["VSS"]) > source_tolerance_v:
        raise RuntimeError("DC VSS does not match the testbench source value")

    mos_source_v = node_values["NSRC"] if source_degenerated else node_values["VSS"]
    node_vgs_v = node_values["IN"] - mos_source_v
    node_vds_v = node_values["OUT"] - mos_source_v
    for name, node_value in (("vgs_v", node_vgs_v), ("vds_v", node_vds_v)):
        tolerance = max(abs(node_value) * 1e-4, 1e-5)
        if abs(abs(op_values[name]) - abs(node_value)) > tolerance:
            raise RuntimeError(
                f"DC node/device mismatch for {name}: node={node_value:.12g}, "
                f"device={op_values[name]:.12g}"
            )

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
    supply_scale_a = max(abs(op_values["ids_a"]), abs(supply_current_a), 1e-18)
    supply_mismatch = (
        abs(abs(op_values["ids_a"]) - abs(supply_current_a))
        / supply_scale_a
        * 100.0
    )
    metrics["supply_current_mismatch_percent"] = supply_mismatch
    if metrics["current_mismatch_percent"] > 1.0:
        raise RuntimeError(
            "DC KCL mismatch between MN0 ids and RD0 current: "
            f"{metrics['current_mismatch_percent']:.6g}%"
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
    operating_region = (
        "saturation" if metrics["saturation_region"] == 1.0 else "non_saturation"
    )
    return metrics, {
        "node_values_v": node_values,
        "device_values": op_values,
        "operating_region": operating_region,
        "region_rule": "saturation when |VDS| >= |VDSAT| and |IDS| > 0",
        "node_device_consistency": "matched",
        "kcl_consistency": "matched",
        "source_degeneration_consistency": (
            "matched" if source_degenerated else "not_applicable"
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
    return metrics, diagnostics


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


def _common_source_testbench_deck(
    profile: dict[str, Any],
    parameters: dict[str, float],
    remote_netlist_path: str,
    *,
    analysis: str = "dc",
    ac_sweep: dict[str, Any] | None = None,
    linearity_sweep: dict[str, Any] | None = None,
    noise_sweep: dict[str, Any] | None = None,
) -> str:
    model_path = str(profile["model_include"])
    if '"' in model_path or '"' in remote_netlist_path:
        raise ValueError("netlist/model path contains an unsupported quote")
    saved_nodes = "IN OUT VDD VSS" + (
        " NSRC" if "source_resistance_ohm" in parameters else ""
    )
    if analysis not in {"dc", "ac", "transient", "noise"}:
        raise ValueError(f"unsupported common-source analysis: {analysis}")
    source = "VIN_SRC (IN 0) vsource dc=vbias"
    extra_parameters = ""
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
        extra_parameters = (
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
    return f'''simulator lang=spectre
include "{model_path}" section={profile["model_section"]}
include "{remote_netlist_path}"

parameters vdd={parameters["vdd_v"]:.12g} vbias={parameters["bias_v"]:.12g}{extra_parameters}

VDD_SRC (VDD 0) vsource dc=vdd
VSS_SRC (VSS 0) vsource dc=0
{source}
{load}

simulatorOptions options psfversion="1.4.0" reltol=1e-4 vabstol=1e-6 iabstol=1e-12
dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status
dcOpInfo info what=oppoint where=rawfile
{analysis_statement}save {saved_nodes} VDD_SRC:p VIN_SRC:p
save MN0:ids MN0:vgs MN0:vds MN0:vdsat MN0:gm MN0:gds
saveOptions options save=allpub
'''


def simulate_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    client = _client()
    library, cell = _target(payload)
    schematic = _read_schematic(client, library, cell)
    _assert_inverter(schematic, profile)
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
        simulator = SpectreSimulator.from_env(
            timeout=timeout,
            work_dir=work_dir,
            output_format="psfascii",
            keep_remote_files=False,
            ssh_runner=getattr(client, "ssh_runner", None),
        )
        ssh_runner = getattr(simulator, "_ssh_runner", None)
        if ssh_runner is not None:
            ssh_runner._persistent_shell_enabled = False
        result = simulator.run_simulation(netlist, {})
        if not result.ok:
            detail = result.errors[0] if result.errors else result.status.value
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


def simulate_common_source(
    payload: dict[str, Any], *, _bundle_cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    analysis = str(payload.get("analysis", "dc"))
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
        cache: dict[str, Any] = {}
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
        )
        if name in payload.get("parameters", {})
    }
    _assert_parameter_consistency(
        requested_oa_parameters,
        oa_parameters,
        expected_label="requested candidate",
        actual_label="OA readback",
    )
    parameters = _resolved_common_source_parameters(payload, oa_parameters)
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
        )
        netlist.write_text(deck, encoding="utf-8")
        wrapper_name = (
            f"input_from_oa_{analysis}.scs"
            if _bundle_cache is not None
            else "input_from_oa.scs"
        )
        remote_wrapper = f"{netlist_evidence['remote_run_dir']}/{wrapper_name}"
        _upload_file(client, netlist, remote_wrapper, timeout=min(timeout, 60))
        simulator = SpectreSimulator.from_env(
            timeout=timeout,
            work_dir=work_dir,
            output_format="psfascii",
            keep_remote_files=False,
            ssh_runner=getattr(client, "ssh_runner", None),
        )
        ssh_runner = getattr(simulator, "_ssh_runner", None)
        if ssh_runner is not None:
            ssh_runner._persistent_shell_enabled = False
        result = simulator.run_simulation(netlist, {})
        if not result.ok:
            detail = result.errors[0] if result.errors else result.status.value
            raise RuntimeError(f"Spectre simulation failed: {detail}")
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
                remote_noise_psf = (
                    f"{netlist_evidence['remote_run_dir']}/noise.noise.psfascii"
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
            "tool_version": result.tool_version,
            "warnings": result.warnings[:20],
            "evidence": {
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


_ACTIONS = {
    "probe": probe,
    "prepare_maestro": prepare_maestro,
    "capture_focused_maestro": capture_focused_maestro,
    "run_background_maestro": run_background_maestro,
    "apply_maestro_variables": apply_maestro_variables,
    "apply_maestro_setup": apply_maestro_setup,
    "inspect_existing_schematic": inspect_existing_schematic,
    "apply_existing_schematic_parameters": apply_existing_schematic_parameters,
    "create_inverter": create_inverter,
    "inspect_inverter": inspect_inverter,
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
}


def main() -> int:
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
    print(_MARKER + json.dumps(result, ensure_ascii=False, default=str))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
