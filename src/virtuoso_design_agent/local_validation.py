"""One offline acceptance entry; preserve the separate VDA/Bridge environments."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .adapters.subprocess_bridge import DEFAULT_BRIDGE_PYTHON


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_TESTS = ("tests/test_schematic_drawing_bridge.py",)


def _read_junit(path: Path) -> tuple[dict, list[dict]]:
    cases = []
    for node in ET.parse(path).getroot().iter("testcase"):
        skipped = node.find("skipped")
        cases.append({"module": node.get("classname", ""), "name": node.get("name", ""),
                      "failed": node.find("failure") is not None or node.find("error") is not None,
                      "skipped": skipped is not None,
                      "reason": "" if skipped is None else skipped.get("message", "") + " " + (skipped.text or "")})
    return {"total": len(cases), "failed": sum(c["failed"] for c in cases),
            "skipped": sum(c["skipped"] for c in cases),
            "passed": sum(not c["failed"] and not c["skipped"] for c in cases)}, cases


def _deferred_bridge_skip(case: dict) -> bool:
    modules = {str(Path(path).with_suffix("")).replace("\\", ".").replace("/", ".") for path in BRIDGE_TESTS}
    return (case["skipped"] and case["name"] in modules and not case["module"]
            and "No module named 'virtuoso_bridge'" in case["reason"])


def _run_phase(name: str, python: Path, targets: tuple[str, ...], output: Path, timeout: float) -> dict:
    folder = output / name
    folder.mkdir()
    junit, log = folder / "pytest.xml", folder / "pytest.log"
    command = [str(python), "-X", "utf8", "-m", "pytest", *targets, "-o", "addopts=-q -p no:cacheprovider", "-ra",
               "--basetemp", str(folder / "tmp"), "--junitxml", str(junit)]
    phase = {"name": name, "python": str(python), "command": command, "status": "failed",
             "returncode": None, "junit": str(junit), "log": str(log), "issues": []}
    print(f"[{name}] local tests: {python}", flush=True)
    if not python.is_file():
        phase["issues"].append("Python executable is missing; environment was not tested")
        return phase
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)  # A caller's -k/-m must not silently narrow acceptance.
    try:
        with log.open("x", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        phase["returncode"] = result.returncode
        counts, cases = _read_junit(junit)
    except (OSError, ET.ParseError, subprocess.TimeoutExpired) as error:
        phase["issues"].append(f"{type(error).__name__}: {error}")
        return phase
    phase["counts"] = counts
    phase["skips"] = [c for c in cases if c["skipped"]]
    if result.returncode != 0 or counts["failed"]:
        phase["issues"].append("pytest failed; inspect the phase log and JUnit report")
        return phase
    unexpected = [c for c in phase["skips"] if name != "main" or not _deferred_bridge_skip(c)]
    if not counts["passed"] or unexpected:
        phase["status"] = "incomplete"
        phase["issues"].append("No passing tests or unexplained/skipped coverage remains")
        return phase
    if name == "bridge":
        for target in targets:
            module = str(Path(target).with_suffix("")).replace("\\", ".").replace("/", ".")
            if not any((c["module"] == module or c["module"].startswith(module + ".")) and not c["skipped"] for c in cases):
                phase["status"] = "incomplete"
                phase["issues"].append(f"Required Bridge module was not exercised: {target}")
                return phase
    phase["status"] = "passed"
    print(f"[{name}] {counts['passed']} passed, {counts['skipped']} skipped", flush=True)
    if name == "main" and phase["skips"]:
        print("[main] Bridge import-only module is deferred to the required Bridge phase", flush=True)
    return phase


def run_local_validation(main_python: Path, bridge_python: Path, output: Path, *, timeout: float = 180) -> dict:
    # Keep venv invocation paths: resolving a bin/python symlink can launch base Python instead.
    main_python, bridge_python = Path(os.path.abspath(main_python)), Path(os.path.abspath(bridge_python))
    output = output.resolve()
    if main_python == bridge_python:
        raise ValueError("Dual-environment acceptance requires distinct Python executables")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive")
    if not (ROOT / "tests").is_dir() or not (ROOT / "pyproject.toml").is_file():
        raise ValueError("This developer entry requires the editable VDA checkout and its tests")
    if output.exists():
        raise ValueError("Acceptance output must be a new directory; no old report or test temp is overwritten")
    output.mkdir(parents=True)
    phases = [_run_phase("main", main_python, ("tests",), output, timeout),
              _run_phase("bridge", bridge_python, BRIDGE_TESTS, output, timeout)]
    status = ("failed" if any(p["status"] == "failed" for p in phases) else
              "incomplete" if any(p["status"] == "incomplete" for p in phases) else "passed")
    report = {"schema_version": 1, "kind": "dual_environment_local_validation", "status": status,
              "evidence_source": "software_inference", "workspace": str(ROOT),
              "scope": "Local software tests only; not Bridge/EDA live acceptance",
              "required_bridge_tests": list(BRIDGE_TESTS), "phases": phases,
              "note": "Main import-only Bridge skips are covered by the required, non-skipped Bridge phase; counts are not added."}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-python", type=Path, default=ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    parser.add_argument("--bridge-python", type=Path, default=Path(os.getenv("VDA_BRIDGE_PYTHON") or DEFAULT_BRIDGE_PYTHON))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "local-validation" / uuid.uuid4().hex)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        report = run_local_validation(args.main_python, args.bridge_python, args.output, timeout=args.timeout)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Local acceptance: {report['status']}; {args.output.resolve() / 'summary.json'}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
