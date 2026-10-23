"""Exercise the acceptance controller with local fake subprocesses, not pytest recursion."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from virtuoso_design_agent import local_validation as validation


@pytest.fixture
def fake_environments(tmp_path, monkeypatch):
    main_python, bridge_python = tmp_path / "main.exe", tmp_path / "bridge.exe"
    main_python.touch()
    bridge_python.touch()
    state = {"calls": [], "mode": "pass"}
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k accidental_subset")

    def run(command, **kwargs):
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert kwargs["cwd"] == validation.ROOT and "--execute" not in command
        state["calls"].append(command)
        bridge = command[0] == str(bridge_python)
        path = Path(command[command.index("--junitxml") + 1])
        if state["mode"] == "missing_xml" and bridge:
            return SimpleNamespace(returncode=0)
        suite = ET.Element("testsuite")
        if bridge:
            node = ET.SubElement(suite, "testcase", classname="tests.test_schematic_drawing_bridge", name="test_local")
            if state["mode"] == "bridge_skip":
                ET.SubElement(node, "skipped", message="dependency unavailable")
            if state["mode"] == "failed_xml":
                ET.SubElement(node, "failure", message="failure even with rc 0")
        else:
            ET.SubElement(suite, "testcase", classname="tests.test_unit", name="test_local")
            node = ET.SubElement(suite, "testcase", classname="", name="tests.test_schematic_drawing_bridge")
            ET.SubElement(node, "skipped", message="collection skipped").text = "No module named 'virtuoso_bridge'"
            if state["mode"] == "unknown_skip":
                ET.SubElement(ET.SubElement(suite, "testcase", classname="tests.test_new", name="test_new"), "skipped")
        ET.ElementTree(suite).write(path, encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(validation.subprocess, "run", run)
    return main_python, bridge_python, state


def test_two_environments_cover_the_deferred_module_and_keep_separate_counts(fake_environments, tmp_path):
    main_python, bridge_python, state = fake_environments
    output = tmp_path / "acceptance"
    report = validation.run_local_validation(main_python, bridge_python, output)
    assert report["status"] == "passed" and len(state["calls"]) == 2
    assert report["phases"][0]["counts"]["skipped"] == 1
    assert report["phases"][1]["counts"]["skipped"] == 0
    assert "tests" in state["calls"][0] and "tests/test_schematic_drawing_bridge.py" in state["calls"][1]
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("mode, expected", [("bridge_skip", "incomplete"), ("unknown_skip", "incomplete"),
                                            ("missing_xml", "failed"), ("failed_xml", "failed")])
def test_skip_or_missing_evidence_never_becomes_overall_success(fake_environments, tmp_path, mode, expected):
    main_python, bridge_python, state = fake_environments
    state["mode"] = mode
    report = validation.run_local_validation(main_python, bridge_python, tmp_path / "acceptance")
    assert report["status"] == expected


def test_missing_bridge_environment_is_a_failure_not_a_silent_skip(fake_environments, tmp_path):
    main_python, _, state = fake_environments
    report = validation.run_local_validation(main_python, tmp_path / "missing.exe", tmp_path / "acceptance")
    assert report["status"] == "failed" and len(state["calls"]) == 1
    assert "missing" in report["phases"][1]["issues"][0]


def test_existing_output_and_same_interpreter_are_rejected_without_launch(fake_environments, tmp_path):
    main_python, bridge_python, state = fake_environments
    with pytest.raises(ValueError, match="new directory"):
        validation.run_local_validation(main_python, bridge_python, tmp_path)
    with pytest.raises(ValueError, match="distinct"):
        validation.run_local_validation(main_python, main_python, tmp_path / "new")
    with pytest.raises(ValueError, match="positive"):
        validation.run_local_validation(main_python, bridge_python, tmp_path / "new", timeout=float("nan"))
    assert not state["calls"]


def test_cli_returns_failure_for_incomplete_coverage(fake_environments, tmp_path):
    main_python, bridge_python, state = fake_environments
    state["mode"] = "bridge_skip"
    assert validation.main(["--main-python", str(main_python), "--bridge-python", str(bridge_python),
                            "--output", str(tmp_path / "acceptance")]) == 1


def test_requested_venv_paths_are_not_replaced_by_resolved_base_python(fake_environments, tmp_path, monkeypatch):
    main_python, bridge_python, state = fake_environments
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path in (main_python, bridge_python):
            return tmp_path / "underlying-base-python.exe"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    report = validation.run_local_validation(main_python, bridge_python, tmp_path / "acceptance")
    assert report["status"] == "passed"
    assert [command[0] for command in state["calls"]] == [str(main_python), str(bridge_python)]
