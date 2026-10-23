"""Literal VDA-wrapper audit and in-job input binding; not a general netlister."""

from __future__ import annotations

import hashlib
import math
import re
import shlex
from pathlib import Path

from .generic_simulation import GenericOaSimulationSpec
from .spectre_values import spectre_scalar

INPUT_RECEIPT = "vda-input-manifest.sha256"
_ELEMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*(\S+)\s+(\S+)\s*\)\s+(vsource|isource|resistor|capacitor)\s+(.+)$")
_ATTRIBUTE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("[^"\n]*"|[^\s]+)')


def audit_generic_wrapper(raw: bytes, settings: GenericOaSimulationSpec, analysis: str, netlist_path: str) -> dict:
    """Read the emitted literals independently; never expand model/DUT includes."""
    if analysis not in {"dc", "ac", "transient", "noise", "stability"}:
        raise ValueError("unsupported literal-wrapper analysis")
    text = raw.decode("utf-8")
    issues, elements, includes = [], {}, []
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith(("subckt ", "ends ", ".subckt", ".ends")):
            issues.append("inline subcircuits are outside the literal-wrapper audit scope")
        include = re.fullmatch(r'include\s+"([^"\n]+)"(?:\s+section=\S+)?', line)
        if include:
            includes.append(include.group(1))
        match = _ELEMENT.fullmatch(line)
        if not match:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*\s*\(", line) and not (
                    analysis == "noise" and re.match(r"^noise\s*\([^)]*\)\s+noise\s", line)):
                issues.append("unrecognized inline wrapper element")
            continue
        name, positive, negative, primitive, tail = match.groups()
        pairs = _ATTRIBUTE.findall(tail)
        remainder = _ATTRIBUTE.sub("", tail).strip()
        attrs = dict(pairs)
        if name in elements or len(attrs) != len(pairs) or remainder:
            issues.append(f"duplicate or malformed literal element {name}")
        elements[name] = (positive, negative, primitive, attrs)
    if includes.count(netlist_path) != 1:
        issues.append("wrapper must include the exact OA-derived netlist once")
    expected_names = {s.name for s in settings.sources} | {load.name for load in settings.loads}
    if set(elements) != expected_names:
        issues.append("source/load element inventory differs from the effective testbench")

    def check(name: str, nodes: tuple[str, str], primitive: str, expected: dict):
        actual = elements.get(name)
        if actual is None:
            issues.append(f"missing source/load {name}")
            return
        if actual[:3] != (*nodes, primitive):
            issues.append(f"source/load {name} connectivity or type differs")
        attrs = actual[3]
        if set(attrs) != set(expected):
            issues.append(f"source/load {name} attribute inventory differs")
        for key, value in expected.items():
            literal = attrs.get(key)
            if isinstance(value, str):
                matches = literal == value
            else:
                parsed = spectre_scalar(literal) if literal is not None else None
                matches = parsed is not None and math.isfinite(parsed) and math.isclose(parsed, value, rel_tol=1e-10, abs_tol=0.0)
            if not matches:
                issues.append(f"source/load {name}.{key} differs from its effective value")

    for source in settings.sources:
        attrs = {"dc": source.dc_value}
        if analysis == "ac":
            attrs.update(mag=source.ac_magnitude, phase=source.ac_phase_deg, type="dc")
        elif settings.dynamic_analysis and source.name == settings.dynamic_analysis.stimulus_source:
            if analysis == "noise":
                attrs.update(mag=1.0, phase=0.0, type="dc")
            elif analysis == "transient":
                attrs.update(type="sine", sinedc=source.dc_value, ampl="vda_stimulus_amplitude", freq="vda_stimulus_frequency")
        check(source.name, (source.positive_node, source.negative_node),
              "vsource" if source.kind == "voltage" else "isource", attrs)
    for load in settings.loads:
        check(load.name, (load.positive_node, load.negative_node), load.kind,
              {"r" if load.kind == "resistor" else "c": load.value})
    return {"source": "software_inference", "status": "matched" if not issues else "mismatch",
            "issues": issues, "wrapper_sha256": hashlib.sha256(raw).hexdigest(),
            "checked_source_count": len(settings.sources), "checked_load_count": len(settings.loads),
            "scope": "literal wrapper inventory, connectivity, DC/AC/source/load fields; included files are not expanded",
            "unexpanded_includes": includes, "performance_verified": False}


def input_guard_body(root: str, contract: dict) -> str:
    """Body executed inside the existing timeout, after Bridge's final upload."""
    name, digest = contract["wrapper_name"], contract["wrapper_sha256"]
    dependencies = contract["dependencies"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.scs", name) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid wrapper input guard contract")
    root = root.rstrip("/")
    for path, value in dependencies.items():
        if (not path.startswith(root + "/") or ".." in path.split("/") or "\n" in path or "\r" in path
                or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise ValueError("input guard dependencies must stay inside the owned run directory")
    lines = ["set -eu", 'vda_fail() { echo "VDA_INPUT_BINDING_FAILED" >&2; exit 86; }',
             "vda_input=; vda_raw=; vda_next_raw=0",
             'for vda_arg in "$@"; do',
             '  if [ "$vda_next_raw" = 1 ]; then vda_raw=$vda_arg; vda_next_raw=0; continue; fi',
             '  if [ "$vda_arg" = -raw ]; then [ -z "$vda_raw" ] || vda_fail; vda_next_raw=1; continue; fi',
             f'  case "$vda_arg" in {shlex.quote(root)}/*/{shlex.quote(name)}) [ -z "$vda_input" ] || vda_fail; vda_input=$vda_arg;; esac',
             "done", '[ -n "$vda_input" ] && [ "$vda_next_raw" = 0 ] || vda_fail',
             'case "$vda_input" in */../*|*/./*) vda_fail;; esac',
             f'[ "$vda_raw" = "${{vda_input%/*}}/{name[:-4]}.raw" ] && [ -d "$vda_raw" ] || vda_fail',
             'vda_sum=$(sha256sum -- "$vda_input") || vda_fail',
             f'[ "${{vda_sum%% *}}" = {shlex.quote(digest)} ] || vda_fail']
    receipt_args = [shlex.quote(digest), '"$vda_input"']
    for path, expected in sorted(dependencies.items()):
        lines.extend([f"vda_sum=$(sha256sum -- {shlex.quote(path)}) || vda_fail",
                      f'[ "${{vda_sum%% *}}" = {shlex.quote(expected)} ] || vda_fail'])
        receipt_args.extend([shlex.quote(expected), shlex.quote(path)])
    lines.extend([f'(umask 077; set -C; printf \'%s  %s\\n\' {" ".join(receipt_args)} > "$vda_raw/{INPUT_RECEIPT}") || vda_fail',
                  'exec spectre "$@"'])
    return "\n".join(lines)


def validate_input_receipt_entries(entries: dict, root: str, contract: dict) -> None:
    """Check recorded entries without claiming that local bytes were re-read."""
    if (not isinstance(entries, dict) or any(not isinstance(p, str) or not isinstance(h, str)
            or re.fullmatch(r'[0-9a-f]{64}', h) is None for p, h in entries.items())):
        raise ValueError('malformed recorded executed-input entries')
    dependencies = contract["dependencies"]
    wrapper_paths = [p for p in entries if p not in dependencies]
    if (len(wrapper_paths) != 1 or len(entries) != len(dependencies) + 1
            or any(entries.get(p) != digest for p, digest in dependencies.items())):
        raise ValueError("executed-input receipt differs from declared dependencies")
    wrapper = wrapper_paths[0]
    if (not wrapper.startswith(root.rstrip("/") + "/") or ".." in wrapper.split("/")
            or not wrapper.endswith("/" + contract["wrapper_name"]) or entries[wrapper] != contract["wrapper_sha256"]):
        raise ValueError("executed wrapper input does not match the declared bytes/path")


def verify_input_receipt(raw_dir: Path, root: str, contract: dict) -> dict:
    path = raw_dir / INPUT_RECEIPT
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None or match.group(2) in entries:
            raise ValueError("malformed or duplicate executed-input receipt")
        entries[match.group(2)] = match.group(1)
    validate_input_receipt_entries(entries, root, contract)
    return {"source": "bridge_readback", "status": "matched", "receipt_path": str(path),
            "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "remote_input_files": entries,
            "scope": "guard checked final staged input/include bytes immediately before Spectre; not a model/OP/performance guarantee"}
