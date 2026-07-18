"""Worker executed by the virtuoso-bridge Python environment.

The process reads one JSON request from stdin and emits one marker-prefixed JSON
result. It intentionally keeps Bridge imports out of the main VDA environment.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from virtuoso_design_agent.metrics import extract_inverter_metrics

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
    }


def _assert_inverter(data: dict[str, Any]) -> None:
    names = {str(item.get("name")) for item in data.get("instances", [])}
    pins = set(data.get("pins", {}).keys())
    if names != {"MN0", "MP0"}:
        raise RuntimeError(f"existing schematic is not the VDA inverter: instances={sorted(names)}")
    missing_pins = {"IN", "OUT", "VDD", "VSS"} - pins
    if missing_pins:
        raise RuntimeError(
            f"existing schematic is not the VDA inverter: missing pins={sorted(missing_pins)}"
        )


def _um(value: float) -> str:
    return f"{float(value):.12g}u"


def _resolved_parameters(payload: dict[str, Any]) -> dict[str, float]:
    profile = payload["profile"]
    supplied = payload.get("parameters", {})
    return {
        "nmos_width_um": float(supplied.get("nmos_width_um", 0.5)),
        "pmos_width_um": float(supplied.get("pmos_width_um", 1.0)),
        "length_um": float(
            supplied.get("length_um", profile["default_length_um"])
        ),
        "load_ff": float(supplied.get("load_ff", profile["default_load_ff"])),
        "vdd_v": float(supplied.get("vdd_v", profile["default_vdd_v"])),
    }


def _apply_parameters(
    client, library: str, cell: str, parameters: dict[str, float]
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
    _assert_inverter(data)
    return _summary(data)


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
        _assert_inverter(existing)
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
    readback = _apply_parameters(client, library, cell, parameters)
    return {
        "created": True,
        "already_exists": False,
        "applied_parameters": parameters,
        "readback": readback,
    }


def inspect_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    data = _read_schematic(client, library, cell)
    _assert_inverter(data)
    return _summary(data)


def apply_inverter_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    library, cell = _target(payload)
    parameters = _resolved_parameters(payload)
    return {
        "applied_parameters": parameters,
        "readback": _apply_parameters(client, library, cell, parameters),
    }


def _signal(data: dict[str, Any], name: str) -> list[float]:
    for key, values in data.items():
        if key.lower() == name.lower():
            return [float(value) for value in values]
    raise RuntimeError(f"Spectre result missing signal {name}; available={sorted(data)}")


def _inverter_deck(profile: dict[str, Any], parameters: dict[str, float]) -> str:
    model_path = str(profile["model_include"])
    if '"' in model_path:
        raise ValueError("model_include contains an unsupported quote")
    return f'''simulator lang=spectre
include "{model_path}" section={profile["model_section"]}

parameters vdd={parameters["vdd_v"]:.12g} wn={parameters["nmos_width_um"]:.12g}u \\
  wp={parameters["pmos_width_um"]:.12g}u lch={parameters["length_um"]:.12g}u \\
  cload={parameters["load_ff"]:.12g}f

VDD_SRC (VDD 0) vsource dc=vdd
VIN_SRC (VIN 0) vsource type=pulse val0=0 val1=vdd delay=20p rise=5p fall=5p width=100p period=200p
MN0 (VOUT VIN 0 0) {profile["nmos_cell"]} l=lch w=wn nf=1 multi=1
MP0 (VOUT VIN VDD VDD) {profile["pmos_cell"]} l=lch w=wp nf=1 multi=1
CL0 (VOUT 0) capacitor c=cload

tran tran stop=380p maxstep=0.5p
save VIN VOUT VDD
'''


def simulate_inverter(payload: dict[str, Any]) -> dict[str, Any]:
    from virtuoso_bridge.spectre.runner import SpectreSimulator

    parameters = _resolved_parameters(payload)
    profile = payload["profile"]
    timeout = int(payload.get("timeout_seconds", 600))
    with tempfile.TemporaryDirectory(prefix="vda_inverter_") as temp_dir:
        work_dir = Path(temp_dir)
        netlist = work_dir / "inverter_tran.scs"
        netlist.write_text(_inverter_deck(profile, parameters), encoding="utf-8")
        simulator = SpectreSimulator.from_env(
            timeout=timeout,
            work_dir=work_dir,
            output_format="psfascii",
            keep_remote_files=False,
        )
        ssh_runner = getattr(simulator, "_ssh_runner", None)
        if ssh_runner is not None:
            ssh_runner._persistent_shell_enabled = False
        result = simulator.run_simulation(netlist, {})
        if not result.ok:
            detail = result.errors[0] if result.errors else result.status.value
            raise RuntimeError(f"Spectre simulation failed: {detail}")
        time_s = _signal(result.data, "time")
        vin_v = _signal(result.data, "VIN")
        vout_v = _signal(result.data, "VOUT")
        metrics = extract_inverter_metrics(
            time_s, vin_v, vout_v, vdd_v=parameters["vdd_v"]
        )
        return {
            "parameters": parameters,
            "metrics": metrics,
            "sample_count": len(time_s),
            "tool_version": result.tool_version,
            "warnings": result.warnings[:20],
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
