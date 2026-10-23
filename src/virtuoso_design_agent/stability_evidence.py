"""Narrow native STB scalar checks and offline audits of retained evidence.

Waveform decoding remains Bridge's job. Offline replay recomputes margins from
its saved decoded samples; it is not an independent PSF waveform decoder.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from .stability import StabilitySpec, StabilitySweep, extract_stability_metrics

CORE_ARTIFACT_ROLES = frozenset({
    "loop_file", "margin_file", "dc_file", "op_file", "log_file", "netlist_file",
    "spectre_include_file", "wrapper_file", "si_env_file", "si_log_file", "response_snapshot",
})


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> dict:
    return {"size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _header(text: str) -> dict[str, str]:
    if not text.startswith("HEADER\n") or "\nTYPE\n" not in text:
        raise ValueError("unsupported native STB ASCII header")
    result = {}
    for line in text.split("\nTYPE\n", 1)[0].splitlines()[1:]:
        match = re.fullmatch(r'"([^"\n]+)"\s+"([^"\n]*)"', line)
        if match:
            name, value = match.groups()
            if name in result:
                raise ValueError("duplicate native STB header field: " + name)
            result[name] = value
    return result


def read_native_stb_margin(loop_path: Path, margin_path: Path, spec: StabilitySpec) -> dict:
    """Only the scalar margin.stb format verified on Spectre 21.1 is supported."""
    if loop_path.parent.resolve() != margin_path.parent.resolve():
        raise ValueError("native margin and loop waveform must be siblings from one result directory")
    loop_text = loop_path.read_text(encoding="utf-8")
    text = margin_path.read_text(encoding="utf-8")
    loop_header, header = _header(loop_text), _header(text)
    if loop_header.get("analysis type") != "stb" or header.get("analysis type") != "margin.stb":
        raise ValueError("native result analysis type is not stb/margin.stb")
    for key in ("PSFversion", "simulator", "version", "date", "analysis name",
                "operating point producer", "probe"):
        if not header.get(key) or loop_header.get(key) != header[key]:
            raise ValueError("native margin/waveform identity differs: " + key)
    if header["simulator"].lower() != "spectre" or header["probe"] != spec.probe_source:
        raise ValueError("native STB simulator/probe does not match the contract")
    if "\nVALUE\n" not in text or not text.rstrip().endswith("\nEND"):
        raise ValueError("native STB scalar value section is incomplete")
    values_text = text.split("\nVALUE\n", 1)[1]

    def scalar(name: str, unit: str) -> float | None:
        rows = re.findall(r'^"' + re.escape(name) + r'"\s+"' + re.escape(unit)
                          + r'"\s+([^\s]+)(?:\s+PROP\()?\s*$', values_text, re.M)
        if len(rows) != 1:
            raise ValueError("missing or duplicate native scalar: " + name)
        value = float(rows[0])
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("native scalar is infinite: " + name)
        return value

    def string(name: str, typed: bool = False) -> str | None:
        pattern = r'^"' + re.escape(name) + r'"\s+'
        if typed:
            pattern += r'"stbstate"\s+'
        rows = re.findall(pattern + r'"([^"\n]*)"\s*$', text, re.M)
        if len(set(rows)) > 1:
            raise ValueError("contradictory native metadata: " + name)
        return rows[0] if rows else None

    native = {"phase_margin_deg": scalar("phaseMargin", "Deg"),
              "loop_unity_gain_frequency_hz": scalar("phaseMarginFreq", "Hz"),
              "gain_margin_db": scalar("gainMargin", "dB"),
              "phase_crossover_frequency_hz": scalar("gainMarginFreq", "Hz"),
              "gain_margin_reason": string("gainMarginInfo"),
              "phase_margin_reason": string("phaseMarginInfo"),
              "state": string("stb_state", typed=True), "header": header,
              "source": "eda_result", "parser_scope": "native margin.stb scalar metadata only"}
    for metric, frequency in (("phase_margin_deg", "loop_unity_gain_frequency_hz"),
                              ("gain_margin_db", "phase_crossover_frequency_hz")):
        if (native[metric] is None) != (native[frequency] is None):
            raise ValueError("native margin and crossover availability disagree: " + metric)
        if native[frequency] is not None and native[frequency] <= 0:
            raise ValueError("native crossover frequency must be positive")
        if native[metric] is None and not native["gain_margin_reason" if metric == "gain_margin_db"
                                                  else "phase_margin_reason"]:
            raise ValueError("native missing margin has no explicit reason: " + metric)
    return native


def compare_native_stb_margin(metrics: dict[str, float], native: dict, spec: StabilitySpec,
                              sweep: dict) -> dict:
    policy = spec.native_margin_tolerances
    issues, comparisons = [], []
    for metric in ("phase_margin_deg", "loop_unity_gain_frequency_hz", "gain_margin_db",
                   "phase_crossover_frequency_hz"):
        actual, expected = metrics.get(metric), native.get(metric)
        if actual is None and expected is None:
            if metric in {"phase_margin_deg", "loop_unity_gain_frequency_hz"}:
                issues.append("native PM/unity frequency is unavailable")
            continue
        if actual is None or expected is None:
            issues.append("native/VDA availability differs: " + metric)
            continue
        error = abs(actual - expected)
        relative = error / abs(expected) if expected else None
        if metric.endswith("frequency_hz"):
            passed = (float(sweep["start_hz"]) <= expected <= float(sweep["stop_hz"])
                      and relative is not None and relative <= policy.frequency_relative)
        else:
            limit = policy.phase_margin_abs_deg if metric == "phase_margin_deg" else policy.gain_margin_abs_db
            passed = error <= limit
        comparisons.append({"metric": metric, "native": expected, "extracted": actual,
                            "absolute_difference": error, "relative_difference": relative, "passed": passed})
        if not passed:
            issues.append("native/VDA extraction consistency failed: " + metric)
    return {"source": "software_inference", "status": "matched" if not issues else "incomplete",
            "passed": not issues, "issues": list(dict.fromkeys(issues)), "comparisons": comparisons,
            "tolerances": policy.model_dump(mode="json"), "native": native,
            "global_stability_proven": False}


def audit_stability_artifacts(evidence_path: Path, *, expected_sha256: str | None = None) -> dict:
    """No Bridge import or remote work. Verify bytes before replaying saved samples."""
    evidence_path = evidence_path.resolve()
    root = evidence_path.parent
    report = {"schema_version": 1, "kind": "stability_artifact_audit", "read_only": True,
              "source": "software_inference", "evidence_path": str(evidence_path),
              "evidence_sha256": None, "status": "incomplete", "issues": [],
              "analysis_complete": False, "metrics": {}, "native_comparison": None,
              "remote_execution_performed": False, "current_OA_verified": False,
              "waveform_replay_scope": "saved Bridge-decoded samples; not independent PSF waveform decoding"}
    try:
        digest = file_digest(evidence_path)["sha256"]
        report["evidence_sha256"] = digest
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("stability evidence JSON hash changed")
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or bundle.get("schema_version") != 1 or bundle.get("kind") != "stability_artifacts":
            raise ValueError("unsupported stability evidence bundle")
        entries = bundle["artifact_manifest"]
        if not isinstance(entries, list) or not entries or canonical_sha256(entries) != bundle["artifact_manifest_sha256"]:
            raise ValueError("stability artifact manifest hash differs")
        paths = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid stability artifact manifest entry")
            name = entry["path"]
            windows_path = PureWindowsPath(name)
            if (name in paths or Path(name).is_absolute() or windows_path.drive or windows_path.root
                    or ".." in name.replace("\\", "/").split("/")):
                raise ValueError("duplicate or escaping stability artifact path")
            path = (root / name).resolve()
            if not path.is_relative_to(root) or path == evidence_path:
                raise ValueError("duplicate or escaping stability artifact path")
            if file_digest(path) != {key: entry[key] for key in ("size_bytes", "sha256")}:
                raise ValueError("stability artifact bytes changed: " + name)
            paths[name] = path
        required = bundle["core_artifacts"]
        if (not isinstance(required, dict) or set(required) != CORE_ARTIFACT_ROLES
                or len(required.values()) != len(set(required.values()))
                or any(bundle.get(role) != name for role, name in required.items())):
            raise ValueError("stability core artifact roles are missing, duplicated or inconsistent")
        for name in required.values():
            if name not in paths or paths[name].stat().st_size == 0:
                raise ValueError("missing/empty stability core artifact: " + name)
        report.update(manifest_verified=True, artifact_count=len(entries))
        snapshot_path = paths[bundle["response_snapshot"]]
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if canonical_sha256(snapshot["binding"]) != bundle["binding_sha256"]:
            raise ValueError("stability response binding differs")
        binding = snapshot["binding"]
        if (file_digest(paths[bundle["netlist_file"]])["sha256"] != binding["netlist_sha256"]
                or file_digest(paths[bundle["wrapper_file"]])["sha256"] != binding["wrapper_sha256"]
                or file_digest(paths[bundle["spectre_include_file"]])["sha256"] != binding["spectre_include_sha256"]):
            raise ValueError("stability input bytes differ from OA/netlist/wrapper binding")
        spec = StabilitySpec.model_validate(snapshot["spec"])
        sweep = StabilitySweep.model_validate(snapshot["sweep"]).model_dump(mode="json")
        if snapshot["binding"]["stability"] != spec.model_dump(mode="json") or snapshot["binding"]["sweep"] != sweep:
            raise ValueError("stability response contract differs from its binding")
        frequency = snapshot["frequency_hz"]
        for actual, expected in ((frequency[0], sweep["start_hz"]), (frequency[-1], sweep["stop_hz"])):
            if not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError("stability snapshot sweep endpoints differ")
        metrics, details = extract_stability_metrics(frequency, [complex(*v) for v in snapshot["raw_loop_gain"]], spec)
        native = read_native_stb_margin(paths[bundle["loop_file"]], paths[bundle["margin_file"]], spec)
        comparison = compare_native_stb_margin(metrics, native, spec, sweep)
        report.update(metrics=metrics, native_comparison=comparison, binding=snapshot["binding"],
                      artifact_count=len(entries), manifest_verified=True)
        report["issues"] = [*details["issues"], *comparison["issues"]]
        report["analysis_complete"] = details["analysis_complete"] and comparison["passed"]
        report["status"] = "verified" if report["analysis_complete"] else "incomplete"
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        report["issues"].append(f"{type(exc).__name__}: {exc}")
    return report


def render_stability_audit(report: dict) -> str:
    lines = ["# STB 原始证据复核", "", f"状态：`{report['status']}`。仅本地复核，没有重新仿真或核对当前 OA。",
             "", f"证据：`{report['evidence_path']}`", "", "| 指标 | Spectre 原生 | VDA 重提取 |",
             "| --- | ---: | ---: |"]
    native = (report.get("native_comparison") or {}).get("native", {})
    for metric in ("phase_margin_deg", "loop_unity_gain_frequency_hz", "gain_margin_db"):
        lines.append(f"| {metric} | {native.get(metric)} | {report['metrics'].get(metric)} |")
    if native.get("gain_margin_db") is None:
        lines.extend(["", "GM 无有限值，不作无穷值处理：" + str(native.get("gain_margin_reason", "缺少证据"))])
    lines.extend(["", *["- " + issue for issue in report["issues"]], "",
                  "向量重放基于保存的 Bridge 解码样本；没有重新独立解析 PSF 波形或证明全局稳定。"])
    return "\n".join(lines) + "\n"
