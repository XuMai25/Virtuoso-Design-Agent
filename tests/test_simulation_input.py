"""Literal audits and real bounded local shell execution; no Bridge or Spectre."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from virtuoso_design_agent.generic_simulation import GenericOaSimulationSpec, render_generic_oa_testbench
from virtuoso_design_agent.simulation_input import (
    INPUT_RECEIPT, audit_generic_wrapper, input_guard_body, verify_input_receipt,
)

from test_generic_simulation import _generic_spec


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wrapper():
    spec = GenericOaSimulationSpec.model_validate(_generic_spec())
    deck = render_generic_oa_testbench(spec, analysis="ac", remote_netlist_path="/data/xum/job/netlist",
                                     model_configuration='', ac_sweep={"start_hz": 1e3, "stop_hz": 1e9})
    return spec, deck


@pytest.mark.parametrize("change", ["dc", "cap", "phase", "nodes", "duplicate", "unexpected_element", "include"])
def test_real_literal_changes_cannot_hide_behind_a_matching_task_tuple(change):
    spec, deck = wrapper()
    assert audit_generic_wrapper(deck.encode(), spec, "ac", "/data/xum/job/netlist")["status"] == "matched"
    if change == "dc":
        deck = deck.replace("dc=0.45", "dc=0.46")
    elif change == "cap":
        deck = deck.replace("c=1e-15", "c=1.1e-15")
    elif change == "phase":
        deck = deck.replace("phase=0", "phase=180", 1)
    elif change == "nodes":
        deck = deck.replace("VIN_SRC (IN 0)", "VIN_SRC (OUT 0)")
    elif change == "duplicate":
        deck += "VIN_SRC (IN 0) vsource dc=0.45 mag=1 phase=0 type=dc\n"
    elif change == "unexpected_element":
        deck += "UNEXPECTED (OUT 0) bsource v=1\n"
    else:
        deck = deck.replace('/data/xum/job/netlist', '/data/xum/other/netlist')
    report = audit_generic_wrapper(deck.encode(), spec, "ac", "/data/xum/job/netlist")
    assert report["status"] == "mismatch" and report["issues"]
    assert not report["performance_verified"]


def shell_executable():
    if os.name == "nt":
        git = shutil.which("git")
        candidates = ([Path(git).resolve().parent.parent / "bin" / "bash.exe",
                       Path(git).resolve().parent.parent / "usr" / "bin" / "bash.exe"] if git else [])
        return next((str(p) for p in candidates if p.is_file()), None)
    return shutil.which("sh")


def shell_path(path):
    text = path.as_posix()
    return f"/{text[0].lower()}{text[2:]}" if os.name == "nt" else text


@pytest.mark.parametrize("case", ["matched", "wrapper_changed", "include_changed", "wrong_raw", "receipt_exists"])
def test_guard_checks_bridges_final_staged_copy_before_any_simulator_process(tmp_path, case):
    shell = shell_executable()
    if shell is None:
        pytest.skip("a local POSIX shell is required to validate the generated remote guard program")
    root = tmp_path / "owned job root"
    job = root / "bridge-job"
    job.mkdir(parents=True)
    deck, include = job / "input_from_oa.scs", root / "oa_netlist.scs"
    deck.write_bytes(b"simulator lang=spectre\n")
    include.write_bytes(b"// local fixture, not a circuit\n")
    raw = job / "input_from_oa.raw"
    raw.mkdir()
    contract = {"wrapper_name": deck.name, "wrapper_sha256": digest(deck), "dependencies": {shell_path(include): digest(include)}}
    body = input_guard_body(shell_path(root), contract)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "spectre"
    stub.write_text('#!/bin/sh\nprintf ran > "$VDA_TEST_MARKER"\n', encoding="utf-8", newline="\n")
    marker = tmp_path / "simulator-would-run"
    if case == "wrapper_changed":
        deck.write_bytes(b"changed after the contract was frozen\n")
    elif case == "include_changed":
        include.write_bytes(b"changed dependency\n")
    elif case == "wrong_raw":
        raw = tmp_path / "not-the-bridge-job"
        raw.mkdir()
    elif case == "receipt_exists":
        (raw / INPUT_RECEIPT).write_text("must not overwrite", encoding="utf-8")
    import shlex
    command = (f"chmod 700 {shlex.quote(shell_path(stub))}\nPATH={shlex.quote(shell_path(bin_dir))}:$PATH\nexport PATH\n"
               f"exec timeout --signal=TERM --kill-after=2s 5s sh -c {shlex.quote(body)} vda-input-guard \"$@\"")
    env = os.environ.copy()
    env["VDA_TEST_MARKER"] = shell_path(marker)
    result = subprocess.run([shell, "-c", command, "vda-input-guard", shell_path(deck), "-raw", shell_path(raw)],
                            env=env, capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if case == "matched":
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert marker.read_text() == "ran"
        receipt = verify_input_receipt(raw, shell_path(root), contract)
        assert receipt["status"] == "matched" and len(receipt["remote_input_files"]) == 2
    else:
        assert result.returncode == 86 and b"VDA_INPUT_BINDING_FAILED" in result.stderr
        assert not marker.exists()
        if case == "receipt_exists":
            assert (raw / INPUT_RECEIPT).read_text() == "must not overwrite"


def test_guard_rejects_escaping_dependency_and_missing_receipt(tmp_path):
    contract = {"wrapper_name": "input.scs", "wrapper_sha256": "a" * 64,
                "dependencies": {"/data/xum/job/../outside": "b" * 64}}
    with pytest.raises(ValueError, match="owned run directory"):
        input_guard_body("/data/xum/job", contract)
    with pytest.raises(OSError):
        verify_input_receipt(tmp_path, "/data/xum/job", contract)
