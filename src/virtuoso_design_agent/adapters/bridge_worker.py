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
        "semantic_parameters": _semantic_parameters_from_schematic(data),
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


def _um(value: float) -> str:
    return f"{float(value):.12g}u"


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
    return _summary(data)


def apply_inverter_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    parameters = _resolved_parameters(payload)
    device_parameters = {
        name: parameters[name]
        for name in ("nmos_width_um", "pmos_width_um", "length_um")
    }
    return {
        "requested_parameters": payload.get("parameters", {}),
        "applied_device_parameters": device_parameters,
        "readback": _apply_parameters(
            client, library, cell, device_parameters, payload["profile"]
        ),
    }


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
    parsed = _parse_inverter_netlist(netlist_text, profile)
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


_ACTIONS = {
    "probe": probe,
    "create_inverter": create_inverter,
    "inspect_inverter": inspect_inverter,
    "apply_inverter_parameters": apply_inverter_parameters,
    "simulate_inverter": simulate_inverter,
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
