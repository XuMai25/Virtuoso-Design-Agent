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

from virtuoso_design_agent.metrics import (
    extract_common_source_dc_metrics,
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


def _schematic_exists(client, library: str, cell: str) -> bool:
    result = client.execute_skill(
        f'let((v) v=ddGetObj("{library}" "{cell}" "schematic") if(v t nil))',
        timeout=15,
    )
    errors = getattr(result, "errors", None) or []
    if errors:
        raise RuntimeError(f"schematic existence check failed: {errors[0]}")
    output = str(getattr(result, "output", "")).strip().strip('"').lower()
    if output not in {"t", "nil"}:
        raise RuntimeError(f"unexpected schematic existence result: {output!r}")
    return output == "t"


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
) -> None:
    by_name = {str(item.get("name")): item for item in data.get("instances", [])}
    names = set(by_name)
    if names != {"MN0", "RD0"}:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"instances={sorted(names)}"
        )
    required = {"IN", "OUT", "VDD", "VSS"}
    missing_pins = required - set(data.get("pins", {}).keys())
    if missing_pins:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"missing pins={sorted(missing_pins)}"
        )
    missing_nets = required - set(data.get("nets", {}).keys())
    if missing_nets:
        raise RuntimeError(
            "existing schematic is not the VDA common-source stage: "
            f"missing nets={sorted(missing_nets)}"
        )
    expected_terminals = {
        "MN0": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
        "RD0": {"PLUS": "VDD", "MINUS": "OUT"},
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
        for name, expected in expected_masters.items():
            actual = (by_name[name].get("lib"), by_name[name].get("cell"))
            if actual != expected:
                raise RuntimeError(
                    "existing schematic is not the VDA common-source stage: "
                    f"{name} master is {actual!r}, expected {expected!r}"
                )


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
    return {
        "device_width_um": _length_um(width),
        "length_um": _length_um(length),
        "load_resistance_ohm": _resistance_ohm(resistance),
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
            }
        )
    return {
        "instances": sorted(instances, key=lambda item: str(item["name"])),
        "nets": sorted(data.get("nets", {}).keys()),
        "pins": sorted(data.get("pins", {}).keys()),
        "instance_parameters": _instance_parameters_from_schematic(data),
        "semantic_parameters": _common_source_semantic_parameters_from_schematic(data),
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
    return {
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


def _apply_common_source_parameters(
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
        wf=_um(parameters["device_width_um"]),
        l=_um(parameters["length_um"]),
        nf="1",
        m="1",
        param_filters=None,
    )
    set_instance_params(
        client,
        "RD0",
        r=_ohm(parameters["load_resistance_ohm"]),
        param_filters=None,
    )
    data = _read_schematic(client, library, cell)
    _assert_common_source(data, profile)
    summary = _common_source_summary(data)
    _assert_parameter_consistency(
        {
            name: float(parameters[name])
            for name in (
                "device_width_um",
                "length_um",
                "load_resistance_ohm",
            )
        },
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
        parameters = _resolved_common_source_parameters(
            payload, _common_source_semantic_parameters_from_schematic(current)
        )
        oa_parameters = {
            name: parameters[name]
            for name in ("device_width_um", "length_um", "load_resistance_ohm")
        }
        result = {
            "requested_parameters": payload["parameters"],
            "applied_device_parameters": oa_parameters,
            "readback": _apply_common_source_parameters(
                client, library, cell, oa_parameters, payload["profile"]
            ),
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
    expected = {
        "MN0": {
            "model": profile["nmos_cell"],
            "nodes": ["OUT", "IN", "VSS", "VSS"],
        },
        "RD0": {
            "model": "resistor",
            "nodes": ["VDD", "OUT"],
        },
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
            instances[name].update(
                {
                    "width_um": _length_um(width_match.group(1)),
                    "length_um": _length_um(length_match.group(1)),
                }
            )
        else:
            resistance_match = re.search(
                r"(?:^|\s)r=([^\s\\]+)", parameter_text
            )
            if resistance_match is None:
                raise RuntimeError("si netlist is missing resistance for RD0")
            instances[name]["resistance_ohm"] = _resistance_ohm(
                resistance_match.group(1)
            )
    return {
        "instances": instances,
        "semantic_parameters": {
            "device_width_um": instances["MN0"]["width_um"],
            "length_um": instances["MN0"]["length_um"],
            "load_resistance_ohm": instances["RD0"]["resistance_ohm"],
        },
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
        )
    if not log_text:
        raise RuntimeError("si netlisting log was not created")
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


def _common_source_metrics_from_result(
    data: dict[str, Any], parameters: dict[str, float]
) -> tuple[dict[str, float], dict[str, Any]]:
    node_values = {
        "IN": _scalar(data, "dc_IN"),
        "OUT": _scalar(data, "dc_OUT"),
        "VDD": _scalar(data, "dc_VDD"),
        "VSS": _scalar(data, "dc_VSS"),
    }
    op_values = {
        "ids_a": _operating_point_scalar(data, "MN0", "ids", "id"),
        "vgs_v": _operating_point_scalar(data, "MN0", "vgs"),
        "vds_v": _operating_point_scalar(data, "MN0", "vds"),
        "vdsat_v": _operating_point_scalar(data, "MN0", "vdsat"),
        "gm_s": _operating_point_scalar(data, "MN0", "gm"),
        "gds_s": _operating_point_scalar(data, "MN0", "gds"),
    }
    source_tolerance_v = 1e-5
    if abs(node_values["VDD"] - parameters["vdd_v"]) > source_tolerance_v:
        raise RuntimeError("DC VDD does not match the testbench source value")
    if abs(node_values["IN"] - parameters["bias_v"]) > source_tolerance_v:
        raise RuntimeError("DC input does not match the testbench bias value")

    node_vgs_v = node_values["IN"] - node_values["VSS"]
    node_vds_v = node_values["OUT"] - node_values["VSS"]
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
        vss_v=node_values["VSS"],
        drain_current_a=op_values["ids_a"],
        vdsat_v=op_values["vdsat_v"],
        gm_s=op_values["gm_s"],
        gds_s=op_values["gds_s"],
        load_resistance_ohm=parameters["load_resistance_ohm"],
    )
    if metrics["current_mismatch_percent"] > 1.0:
        raise RuntimeError(
            "DC KCL mismatch between MN0 ids and RD0 current: "
            f"{metrics['current_mismatch_percent']:.6g}%"
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
    }


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
) -> str:
    model_path = str(profile["model_include"])
    if '"' in model_path or '"' in remote_netlist_path:
        raise ValueError("netlist/model path contains an unsupported quote")
    return f'''simulator lang=spectre
include "{model_path}" section={profile["model_section"]}
include "{remote_netlist_path}"

parameters vdd={parameters["vdd_v"]:.12g} vbias={parameters["bias_v"]:.12g}

VDD_SRC (VDD 0) vsource dc=vdd
VSS_SRC (VSS 0) vsource dc=0
VIN_SRC (IN 0) vsource dc=vbias

simulatorOptions options psfversion="1.4.0" reltol=1e-4 vabstol=1e-6 iabstol=1e-12
dcOp dc write="spectre.dc" maxiters=150 maxsteps=10000 annotate=status
dcOpInfo info what=oppoint where=rawfile
save IN OUT VDD VSS VDD_SRC:p VIN_SRC:p
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


def simulate_common_source(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    client = _client()
    library, cell = _target(payload)
    schematic = _read_schematic(client, library, cell)
    _assert_common_source(schematic, profile)
    oa_parameters = _common_source_semantic_parameters_from_schematic(schematic)
    requested_oa_parameters = {
        name: float(payload.get("parameters", {})[name])
        for name in ("device_width_um", "length_um", "load_resistance_ohm")
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

        netlist = work_dir / "common_source_from_oa.scs"
        deck = _common_source_testbench_deck(
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
        metrics, operating_point = _common_source_metrics_from_result(
            result.data, parameters
        )
        metric_sources = {name: "eda_result" for name in metrics}
        metric_sources["saturation_region"] = "software_inference"
        return {
            "parameters": parameters,
            "metrics": metrics,
            "metric_sources": metric_sources,
            "scalar_count": len(result.data),
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
                        "bias_v": parameters["bias_v"],
                        "vdd_v": parameters["vdd_v"],
                    },
                    "value_sources": {
                        name: (
                            "user_input"
                            if name in payload.get("parameters", {})
                            else "software_inference"
                        )
                        for name in ("bias_v", "vdd_v")
                    },
                },
                "operating_point": {
                    "source": "eda_result",
                    **operating_point,
                    "operating_region_source": "software_inference",
                },
            },
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


_ACTIONS = {
    "probe": probe,
    "inspect_existing_schematic": inspect_existing_schematic,
    "apply_existing_schematic_parameters": apply_existing_schematic_parameters,
    "create_inverter": create_inverter,
    "inspect_inverter": inspect_inverter,
    "apply_inverter_parameters": apply_inverter_parameters,
    "simulate_inverter": simulate_inverter,
    "create_common_source": create_common_source,
    "inspect_common_source": inspect_common_source,
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
