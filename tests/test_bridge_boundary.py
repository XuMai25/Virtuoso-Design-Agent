from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import virtuoso_design_agent.adapters.bridge_worker as bridge_worker
from virtuoso_design_agent.adapters.base import merge_analysis_bundle
from virtuoso_design_agent.adapters.bridge_worker import (
    _ade_capture_manifest,
    _apply_explicit_instance_parameters,
    _assert_common_source,
    _assert_common_source_removal_preserved,
    _assert_common_source_transform_preserved,
    _assert_inverter_testbench_transform_preserved,
    _assert_parameter_consistency,
    _assert_focused_maestro,
    _cellview_exists,
    _common_source_ac_metrics_from_result,
    _common_source_dc_data_from_result,
    _common_source_linearity_metrics_from_result,
    _common_source_noise_metrics_from_result,
    _common_source_instance_parameter_updates,
    _common_source_device_geometry_from_schematic,
    _common_source_metrics_from_result,
    _common_source_model_manifest,
    _common_source_testbench_deck,
    _complex_signal,
    _complete_si_env,
    _discard_failed_existing_schematic_edit,
    _delete_source_degeneration_operation,
    _edit_existing_schematic,
    _generate_oa_netlist,
    _focus_target_schematic,
    _inverter_testbench_deck,
    _instance_parameters_from_schematic,
    _has_structured_ade_outputs,
    _manifest_fingerprint,
    _merge_common_source_operating_condition_results,
    _parse_common_source_netlist,
    _parse_inverter_netlist,
    _placement_snapshot_from_readback,
    _preflight_mn0_source_label,
    _preflight_source_degeneration_removal,
    _rename_inverter_ground_labels_operation,
    _rename_mn0_source_label_operation,
    _restore_mn0_source_label_operation,
    ParameterReadbackMismatch,
    _read_nonempty_text,
    _requested_instance_parameters,
    _schematic_exists,
    _signal,
    _simulate_common_source_operating_conditions,
    _validate_si_log,
    _verify_instance_parameter_values,
    prepare_maestro,
    simulate_common_source,
    simulate_inverter,
)
from virtuoso_design_agent.adapters.subprocess_bridge import (
    BridgeWorkerError,
    SubprocessBridgeAdapter,
)
from virtuoso_design_agent.profiles import load_pdk_profile
from virtuoso_design_agent.models import EvidenceSource, TaskSpec


def _install_fake_maestro_module(
    monkeypatch: pytest.MonkeyPatch, **attributes
) -> None:
    maestro = ModuleType("virtuoso_bridge.virtuoso.maestro")
    for name, value in attributes.items():
        setattr(maestro, name, value)
    virtuoso = ModuleType("virtuoso_bridge.virtuoso")
    virtuoso.__path__ = []
    virtuoso.maestro = maestro
    ops = ModuleType("virtuoso_bridge.virtuoso.ops")
    ops.escape_skill_string = lambda value: (  # type: ignore[attr-defined]
        str(value).replace("\\", "\\\\").replace('"', '\\"')
    )
    virtuoso.ops = ops
    bridge = ModuleType("virtuoso_bridge")
    bridge.__path__ = []
    bridge.virtuoso = virtuoso
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", bridge)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso", virtuoso)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.maestro", maestro)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.ops", ops)


def _stub_background_runtime(monkeypatch: pytest.MonkeyPatch) -> dict:
    scratch_root = "/data/xum/vda_runs/vda_ade_run_test_0123456789ab"
    analog_run_dir = (
        f"{scratch_root}/vda_test/vda_manual_tb/maestro/results/maestro/"
        ".tmpADEDir_vda/0_AC/simulation/vda_manual_tb/spectre/schematic/netlist"
    )
    runtime = {
        "scratch_root": scratch_root,
        "tests": [
            {
                "test": "AC",
                "previous": {
                    "project_dir": "/home/xum/simulation/AC",
                    "results_dir": "/home/xum/simulation/AC",
                    "analog_run_dir": "/home/xum/simulation/AC/netlist",
                },
                "applied": {
                    "project_dir": analog_run_dir.rsplit("/vda_manual_tb/", 1)[0],
                    "results_dir": analog_run_dir.rsplit("/vda_manual_tb/", 1)[0],
                    "analog_run_dir": analog_run_dir,
                },
            }
        ],
        "evidence_source": "bridge_readback",
    }
    monkeypatch.setattr(
        bridge_worker,
        "_configure_background_ade_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        bridge_worker,
        "_restore_background_ade_runtime",
        lambda *_args, **_kwargs: None,
    )
    return runtime


def test_pdk_profile_contains_verified_nics4304_paths() -> None:
    profile = load_pdk_profile("nics4304_tsmc28")
    assert profile.tech_library == "tsmcN28"
    assert profile.model_include.startswith("/data/technique/")
    assert profile.cds_lib_path.startswith("/data/xum/")
    assert profile.remote_run_root.startswith("/data/xum/")


def test_ade_capture_manifest_separates_setup_input_results_and_logs(
    tmp_path: Path,
) -> None:
    files = {
        "maestro.sdb": b"setup",
        "Interactive.7/1/AC/netlist/input.scs": b"simulator input",
        "Interactive.7/1/AC/psf/ac.ac": b"waveform",
        "Interactive.7/1/AC/psf/spectre.out": b"spectre log",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    manifest = _ade_capture_manifest(tmp_path)
    categories = {item["path"]: item["category"] for item in manifest}

    assert categories["maestro.sdb"] == "setup"
    assert categories["Interactive.7/1/AC/netlist/input.scs"] == "simulator_input"
    assert categories["Interactive.7/1/AC/psf/ac.ac"] == "eda_result"
    assert categories["Interactive.7/1/AC/psf/spectre.out"] == "run_log"
    assert all(item["size_bytes"] > 0 for item in manifest)
    assert all(len(item["sha256"]) == 64 for item in manifest)
    assert _manifest_fingerprint(manifest, {"setup"}) is not None
    assert _manifest_fingerprint(manifest, {"eda_result"}) is not None


def test_focused_maestro_capture_rejects_missing_mismatched_or_changed_focus() -> None:
    expected = {
        "session": "fnxSession7",
        "lib": "vda_test",
        "cell": "vda_manual_tb",
        "view": "maestro",
    }
    assert (
        _assert_focused_maestro(
            expected,
            library="vda_test",
            cell="vda_manual_tb",
            view="maestro",
        )
        == "fnxSession7"
    )
    with pytest.raises(RuntimeError, match="no focused ADE"):
        _assert_focused_maestro(
            expected | {"session": ""},
            library="vda_test",
            cell="vda_manual_tb",
            view="maestro",
        )
    with pytest.raises(RuntimeError, match="target mismatch"):
        _assert_focused_maestro(
            expected | {"cell": "other"},
            library="vda_test",
            cell="vda_manual_tb",
            view="maestro",
        )
    with pytest.raises(RuntimeError, match="session changed"):
        _assert_focused_maestro(
            expected | {"session": "fnxSession8"},
            library="vda_test",
            cell="vda_manual_tb",
            view="maestro",
            expected_session="fnxSession7",
        )


def test_structured_ade_outputs_require_a_nonempty_point_output_table() -> None:
    assert not _has_structured_ade_outputs({})
    assert not _has_structured_ade_outputs({"points": [{"outputs": {}}]})
    assert _has_structured_ade_outputs(
        {"points": [{"outputs": {"gain": {"value": "3.1"}}}]}
    )


def test_prepare_maestro_creates_new_persistent_test_and_reads_it_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"maestro_exists": False}
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression and '"schematic"' in expression:
                return SimpleNamespace(output="t", errors=[])
            if "ddGetObj" in expression and '"maestro"' in expression:
                return SimpleNamespace(
                    output="t" if state["maestro_exists"] else "nil", errors=[]
                )
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA_AC")', errors=[])
            if "designObj" in expression:
                return SimpleNamespace(
                    output='("source_lib" "legacy_tb" "schematic")',
                    errors=[],
                )
            raise AssertionError(expression)

    sessions = iter(["fnxSession1", "fnxSession2"])

    def fake_open_session(_client, library, cell):
        calls.append(("open", library, cell))
        return next(sessions)

    def fake_create_test(_client, test, **kwargs):
        calls.append(("create_test", test, kwargs))

    def fake_save_setup(_client, library, cell, **kwargs):
        calls.append(("save", library, cell, kwargs))
        state["maestro_exists"] = True

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=fake_open_session,
        close_session=fake_close_session,
        create_test=fake_create_test,
        save_setup=fake_save_setup,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    prepared = prepare_maestro(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_prepare": {
                "backend": "maestro",
                "test_name": "VDA_AC",
                "design": {
                    "library": "source_lib",
                    "cell": "legacy_tb",
                    "view": "schematic",
                },
                "simulator": "spectre",
            },
        }
    )

    create_call = next(call for call in calls if call[0] == "create_test")
    assert create_call[1] == "VDA_AC"
    assert create_call[2]["lib"] == "source_lib"
    assert create_call[2]["cell"] == "legacy_tb"
    assert create_call[2]["view"] == "schematic"
    assert create_call[2]["simulator"] == "spectre"
    assert prepared["persistent_view_confirmed"] is True
    assert prepared["tests_readback"] == ["VDA_AC"]
    assert prepared["design_target_confirmed"] is True
    assert prepared["design_readback"] == {
        "library": "source_lib",
        "cell": "legacy_tb",
        "view": "schematic",
    }
    assert prepared["existing_maestro_overwritten"] is False
    assert prepared["schematic_oa_write_performed"] is False
    assert prepared["maestro_oa_write_performed"] is True
    assert prepared["configured_analyses"] == []
    assert [call[0] for call in calls].count("open") == 2
    assert [call[0] for call in calls].count("close") == 2


def test_prepare_maestro_refuses_an_existing_manual_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            raise AssertionError(expression)

    def unexpected(*args, **kwargs):
        raise AssertionError("existing Maestro view must not be opened or changed")

    _install_fake_maestro_module(
        monkeypatch,
        open_session=unexpected,
        close_session=unexpected,
        create_test=unexpected,
        save_setup=unexpected,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="refusing to modify existing Maestro"):
        prepare_maestro(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_prepare": {"backend": "maestro"},
            }
        )


def test_background_ade_runtime_redirects_each_test_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    states = {
        "AC": {
            "project_dir": "/home/xum/simulation/AC",
            "results_dir": "/home/xum/simulation/AC",
            "analog_run_dir": "/home/xum/simulation/AC/netlist",
        },
        "TRAN": {
            "project_dir": "/home/xum/simulation/TRAN",
            "results_dir": "/home/xum/simulation/TRAN",
            "analog_run_dir": "/home/xum/simulation/TRAN/netlist",
        },
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            parts = expression.split('"')
            test = parts[1]
            if "asiSetProjectDir" in expression:
                project_dir, results_dir = parts[5], parts[7]
                calls.append(("set", test, project_dir))
                states[test] = {
                    "project_dir": project_dir,
                    "results_dir": results_dir,
                    "analog_run_dir": (
                        f"{project_dir}/vda_manual_tb/spectre/schematic/netlist"
                    ),
                }
            state = states[test]
            return SimpleNamespace(
                output=(
                    f'("{state["project_dir"]}" "{state["results_dir"]}" '
                    f'"{state["analog_run_dir"]}")'
                ),
                errors=[],
            )

    _install_fake_maestro_module(monkeypatch)
    client = Client()
    runtime = bridge_worker._configure_background_ade_runtime(
        client,
        {
            "task_id": "saved-maestro",
            "profile": {"remote_run_root": "/data/xum/vda_runs"},
        },
        session="fnxBackground8",
        tests=["AC", "TRAN"],
        library="vda_test",
        cell="vda_manual_tb",
        view="maestro",
    )

    assert runtime["scratch_root"].startswith(
        "/data/xum/vda_runs/vda_ade_run_saved-maestro_"
    )
    assert [item["test"] for item in runtime["tests"]] == ["AC", "TRAN"]
    assert all(
        item["applied"]["project_dir"].startswith(runtime["scratch_root"] + "/")
        for item in runtime["tests"]
    )
    assert all(
        "/vda_test/vda_manual_tb/maestro/results/maestro/" in
        item["applied"]["analog_run_dir"]
        for item in runtime["tests"]
    )

    bridge_worker._restore_background_ade_runtime(
        client, session="fnxBackground8", runtime=runtime
    )

    assert states["AC"]["project_dir"] == "/home/xum/simulation/AC"
    assert states["TRAN"]["project_dir"] == "/home/xum/simulation/TRAN"
    assert [call[1] for call in calls[-2:]] == ["TRAN", "AC"]


def test_background_maestro_run_uses_exact_new_history_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    scratch_maestro_root = (
        "/data/xum/scratch/vda_test/vda_manual_tb/maestro/results/maestro"
    )
    project_maestro_root = "/data/xum/cds/vda_test/vda_manual_tb/maestro/results/maestro"
    runtime_input_root = (
        "/data/xum/vda_runs/vda_ade_run_test_0123456789ab/vda_test/"
        "vda_manual_tb/maestro/results/maestro/.tmpADEDir_vda/0_AC/"
        "simulation/vda_manual_tb/spectre/schematic/netlist"
    )

    class Client:
        def execute_skill(self, expression, **kwargs):
            if expression.startswith("list(ddGetObj"):
                return SimpleNamespace(
                    output=(
                        '("/data/xum/cds/vda_test" '
                        '("/data/xum/scratch/vda_test/vda_manual_tb/maestro/'
                        'results/maestro/Interactive.8/1/AC"))'
                    ),
                    errors=[],
                )
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

        def run_shell_command(self, command, **kwargs):
            calls.append(("shell", command, kwargs))
            return SimpleNamespace(ok=True)

        def download_file(self, remote_path, local_path, **kwargs):
            calls.append(("download", remote_path, kwargs))
            contents = ""
            if remote_path.endswith("0_history_0_project.tsv"):
                rows = [
                    (24, "3" * 64, f"{project_maestro_root}/Interactive.8.rdb"),
                    (30, "4" * 64, f"{project_maestro_root}/Interactive.8.log"),
                ]
            elif remote_path.endswith("2_runtime_0.tsv"):
                rows = [
                    (12, "1" * 64, f"{runtime_input_root}/netlist"),
                    (18, "2" * 64, f"{runtime_input_root}/input.scs"),
                ]
            else:
                rows = []
            if rows:
                contents = "".join(
                    f"VDA_ARTIFACT\t{size}\t{digest}\t{path}\n"
                    for size, digest, path in rows
                )
            Path(local_path).write_text(contents, encoding="utf-8")
            return SimpleNamespace(ok=True)

    def fake_open_session(_client, library, cell):
        calls.append(("open", library, cell))
        return "fnxBackground8"

    def fake_run_and_wait(_client, **kwargs):
        calls.append(("run", kwargs))
        return '"Interactive.8"', "done"

    def fake_read_results(_client, session, **kwargs):
        calls.append(("results", session, kwargs))
        return {
            "history": "Interactive.8",
            "points": [
                {
                    "point": 1,
                    "parameters": {"c_val": "1p"},
                    "outputs": {"BW": {"value": "1.2G", "pass_fail": "pass"}},
                }
            ],
        }

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=fake_open_session,
        close_session=fake_close_session,
        run_and_wait=fake_run_and_wait,
        read_results=fake_read_results,
    )
    runtime = _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)

    result = bridge_worker.run_background_maestro(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {
                "backend": "maestro",
                "require_structured_outputs": True,
                "require_artifact_manifest": True,
            },
            "profile": {"remote_run_root": "/data/xum/vda_runs"},
            "task_id": "run-saved-maestro",
            "timeout_seconds": 321,
        }
    )

    assert result["session_mode"] == "background"
    assert result["gui_focus_required"] is False
    assert result["tests_readback"] == ["AC"]
    assert result["history"] == "Interactive.8"
    assert result["history_naming_policy"] == "saved_setup_unmodified"
    assert result["history_uniqueness_verified"] is False
    assert result["structured_results_available"] is True
    assert result["automated_simulation_performed"] is True
    assert result["oa_write_performed"] is False
    assert result["maestro_setup_write_performed"] is False
    assert result["runtime_scratch_root"] == runtime["scratch_root"]
    assert result["runtime_directory_persisted"] is False
    assert result["runtime_directory_restored"] is True
    assert result["runtime_artifacts_restricted_to_data_xum"] is True
    assert result["artifacts_captured"] is True
    assert result["artifact_manifest_complete"] is True
    assert result["artifact_history"] == "Interactive.8"
    assert result["artifact_history_path_binding_verified"] is True
    assert result["artifact_runtime_input_binding_verified"] is True
    assert result["artifact_run_binding_verified"] is True
    assert result["artifact_counts"] == {
        "simulator_input": 2,
        "eda_result": 1,
        "run_log": 1,
        "other": 0,
    }
    assert len(result["artifact_manifest"]) == 4
    assert result["simulation_fingerprint_sha256"]
    assert result["remote_manifest_directory"].startswith(
        "/data/xum/vda_runs/vda_ade_manifest_"
    )
    assert ("run", {"session": "fnxBackground8", "timeout": 321}) in calls
    read_call = next(call for call in calls if call[0] == "results")
    assert read_call[2]["history"] == "Interactive.8"
    assert calls[-1] == ("close", "fnxBackground8")
    assert any(
        call[0] == "download" and call[1].endswith("0_history_0_project.tsv")
        for call in calls
    )
    assert any(
        call[0] == "download" and call[1].endswith("2_runtime_0.tsv")
        for call in calls
    )


def test_background_maestro_resume_reads_exact_history_without_running_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("resume must not call run_and_wait")

    def fake_read_results(_client, session, **kwargs):
        calls.append(("results", session, kwargs))
        return {
            "history": "Interactive.0",
            "points": [{"outputs": {"VoutAvg": {"value": "364m"}}}],
        }

    _install_fake_maestro_module(
        monkeypatch,
        open_session=lambda *_args: "fnxResume0",
        close_session=lambda _client, session: calls.append(("close", session)),
        run_and_wait=unexpected_run,
        read_results=fake_read_results,
    )
    runtime = _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(
        bridge_worker,
        "_collect_background_ade_artifacts",
        lambda *_args, **_kwargs: {
            "artifact_history": "Interactive.0",
            "artifact_history_path_binding_verified": True,
            "artifact_runtime_input_binding_verified": True,
            "artifact_run_binding_verified": True,
            "artifact_manifest": [
                {
                    "path": "Interactive.0/runtime/VDA/input.scs",
                    "category": "simulator_input",
                }
            ],
            "artifact_counts": {"simulator_input": 1},
            "artifact_manifest_complete": True,
            "artifacts_captured": True,
            "simulation_fingerprint_sha256": "a" * 64,
        },
    )

    result = bridge_worker.run_background_maestro(
        {
            "task_id": "resume-saved-maestro",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "profile": {"remote_run_root": "/data/xum/vda_runs"},
            "ade_run": {
                "resume_history": "Interactive.0",
                "resume_runtime_scratch_root": runtime["scratch_root"],
            },
        }
    )

    assert result["history"] == "Interactive.0"
    assert result["run_status"] == "recovered"
    assert result["history_recovery_performed"] is True
    assert result["simulation_performed_by_this_invocation"] is False
    assert calls[0][0] == "results"
    assert calls[-1] == ("close", "fnxResume0")


def test_background_maestro_run_rejects_empty_structured_results_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=lambda *_args: "fnxBackground9",
        close_session=fake_close_session,
        run_and_wait=lambda *_args, **_kwargs: ('"Interactive.9"', "done"),
        read_results=lambda *_args, **_kwargs: {
            "history": "Interactive.9",
            "points": [],
        },
    )
    _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(
        bridge_worker,
        "_collect_background_ade_artifacts",
        lambda *_args, **_kwargs: {
            "artifact_history": "Interactive.9",
            "artifact_history_path_binding_verified": True,
            "artifact_manifest": [{"path": "Interactive.9/input.scs"}],
            "artifact_counts": {"simulator_input": 1},
            "artifact_manifest_complete": True,
            "artifacts_captured": True,
            "simulation_fingerprint_sha256": "a" * 64,
            "remote_manifest_directory": "/data/xum/vda_runs/empty-output",
        },
    )

    with pytest.raises(
        RuntimeError, match="non-empty point/output/spec table"
    ) as failure:
        bridge_worker.run_background_maestro(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_run": {"require_structured_outputs": True},
            }
        )

    assert "/data/xum/vda_runs/empty-output" in str(failure.value)
    assert calls == [("close", "fnxBackground9")]


def test_remote_ade_manifest_rejects_a_different_history_path() -> None:
    maestro_root = "/data/xum/scratch/vda_test/cell/maestro/results/maestro"
    text = (
        f"VDA_ARTIFACT\t10\t{'a' * 64}\t"
        f"{maestro_root}/Interactive.7/1/AC/netlist/netlist\n"
    )

    with pytest.raises(RuntimeError, match="escaped the exact returned history"):
        bridge_worker._parse_remote_ade_artifact_manifest(
            text,
            history="Interactive.8",
            source_location="scratch",
            maestro_root=maestro_root,
        )


def test_remote_ade_artifact_hash_command_is_single_line_and_shell_quoted() -> None:
    tree_root = (
        "/data/xum/scratch/vda_test/cell/maestro/results/maestro/"
        "Interactive.8"
    )
    maestro_root = "/data/xum/scratch/vda_test/cell/maestro/results/maestro"
    manifest_path = "/data/xum/vda runs/manifest/scratch.tsv"
    commands = bridge_worker._ade_artifact_hash_commands(
        tree_root=tree_root,
        manifest_path=manifest_path,
        companions=(
            f"{maestro_root}/Interactive.8.log",
            f"{maestro_root}/Interactive.8.rdb",
            f"{maestro_root}/Interactive.8.msg.db",
        ),
    )

    assert len(commands) == 7
    assert all("\n" not in command for command in commands)
    assert all(
        len('csh("")' + command.replace("\\", "\\\\").replace('"', '\\"'))
        < 768
        for command in commands
    )
    parsed = [shlex.split(command) for command in commands]
    assert all(parts[:2] == ["sh", "-c"] for parts in parsed)
    assert all(parts[3] == "sh" for parts in parsed)
    assert all(parts[4] == manifest_path for parts in parsed[:4])
    assert all(
        parts[4] == "/data/xum/vda runs/manifest" for parts in parsed[-3:]
    )
    assert all(parts[5] == "scratch.tsv" for parts in parsed[-3:])
    assert all("$" not in parts[2].replace(r"\$", "") for parts in parsed)
    assert parsed[0][5] == tree_root
    assert parsed[3][-1] == f"{maestro_root}/Interactive.8.msg.db"
    assert "sha256sum" in parsed[-3][2]
    assert "wc -c" in parsed[-2][2]
    assert "paste" in parsed[-1][2]


def test_remote_ade_manifest_accepts_joined_size_and_sha256_rows() -> None:
    maestro_root = "/data/xum/scratch/vda_test/cell/maestro/results/maestro"
    remote_path = f"{maestro_root}/Interactive.8.log"
    text = f"24 {remote_path}\t{'a' * 64}  {remote_path}\n"

    entries = bridge_worker._parse_remote_ade_artifact_manifest(
        text,
        history="Interactive.8",
        source_location="scratch",
        maestro_root=maestro_root,
    )

    assert entries == [
        {
            "path": "Interactive.8/Interactive.8.log",
            "remote_path": remote_path,
            "source_location": "scratch",
            "size_bytes": 24,
            "sha256": "a" * 64,
            "category": "run_log",
            "binding": "exact_history_companion",
            "evidence_source": "eda_result",
        }
    ]


def test_remote_manifest_skill_fallback_reads_bounded_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_maestro_module(monkeypatch)
    calls: list[str] = []
    pages = iter(
        [
            '(2 "first\\nsecond\\n")',
            '(1 "third\\n")',
        ]
    )

    class Client:
        def execute_skill(self, expression, **_kwargs):
            calls.append(expression)
            return SimpleNamespace(ok=True, output=next(pages), errors=[])

    text = bridge_worker._read_remote_text_via_skill(
        Client(),
        "/data/xum/vda_runs/manifest.tsv",
        page_lines=2,
    )

    assert text == "first\nsecond\nthird\n"
    assert len(calls) == 2
    assert "skipped<0" in calls[0]
    assert "skipped<2" in calls[1]


def test_bridge_results_csv_download_has_narrow_skill_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_maestro_module(monkeypatch)
    remote_path = f"/tmp/vb_results_{'a' * 32}.csv"

    class Client:
        def download_file(self, remote, local, **_kwargs):
            assert remote == remote_path
            assert Path(local) == tmp_path / "results.csv"
            return SimpleNamespace(ok=False, errors=["dns unavailable"])

        def execute_skill(self, expression, **_kwargs):
            assert remote_path in expression
            return SimpleNamespace(
                ok=True,
                output='(1 "Point,Test,Output\\n")',
                errors=[],
            )

    client = bridge_worker._BridgeTextDownloadFallback(Client())
    local_path = tmp_path / "results.csv"

    client.download_file(remote_path, local_path, timeout=60)

    assert local_path.read_text(encoding="utf-8") == "Point,Test,Output\n"
    assert client.skill_fallback_paths == [remote_path]


def test_single_point_detail_csv_is_normalized_for_bridge_parser(
    tmp_path: Path,
) -> None:
    path = tmp_path / "detail.csv"
    path.write_text(
        ",Parameter,Nominal,,,\n\n\n"
        "Test,Output,Nominal,Spec,Weight,Pass/Fail\n"
        "VDA,Vin,,,,\n"
        "VDA,VoutAvg,364e-3,> 0.1,,pass\n",
        encoding="utf-8",
    )

    compatibility = bridge_worker._normalize_single_point_detail_csv(path)

    assert compatibility is not None
    assert compatibility["normalization"] == (
        "cadence_single_point_detail_add_point_column"
    )
    assert compatibility["original_sha256"] != compatibility["normalized_sha256"]
    normalized = path.read_text(encoding="utf-8")
    assert "Point,Test,Output,Nominal,Spec,Weight,Pass/Fail" in normalized
    assert "1,VDA,VoutAvg,364e-3,> 0.1,,pass" in normalized


def test_ade_spectre_input_matches_oa_raw_parameters_but_excludes_wfg() -> None:
    text = """// Design library name: vb_pdk_smoke
// Design cell name: inverter_tb
// Design view name: schematic
simulator lang=spectre
MP0 (OUT IN VDD VDD) pch_lvt_mac l=30n w=100n multi=1 nf=1
MN0 (OUT IN 0 0) nch_lvt_mac l=30n w=100n multi=1 nf=1
VIN0 (IN 0) vsource type=pulse val0=0 val1=900m period=100p delay=0 rise=5p fall=5p width=50p
VDD0 (VDD 0) vsource dc=900m type=dc
CL0 (OUT 0) capacitor c=2f
tran tran stop=300p maxstep=1p
save IN OUT
"""
    schematic = {
        "instances": [
            {
                "name": "MP0",
                "lib": "tsmcN28",
                "cell": "pch_lvt_mac",
                "params": {
                    "model": "pch_lvt_mac",
                    "l": "30n",
                    "w": "100n",
                    "Wfg": "1u",
                    "nf": "1",
                    "simM": "1",
                },
                "terms": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
            },
            {
                "name": "MN0",
                "lib": "tsmcN28",
                "cell": "nch_lvt_mac",
                "params": {
                    "model": "nch_lvt_mac",
                    "l": "30n",
                    "w": "100n",
                    "Wfg": "500n",
                    "nf": "1",
                    "simM": "1",
                },
                "terms": {"D": "OUT", "G": "IN", "S": "gnd!", "B": "gnd!"},
            },
            {
                "name": "VIN0",
                "lib": "analogLib",
                "cell": "vpulse",
                "params": {
                    "v1": "0",
                    "v2": "900m",
                    "per": "100p",
                    "td": "0",
                    "tr": "5p",
                    "tf": "5p",
                    "pw": "50p",
                    "srcType": "pulse",
                },
                "terms": {"PLUS": "IN", "MINUS": "gnd!"},
            },
            {
                "name": "VDD0",
                "lib": "analogLib",
                "cell": "vdc",
                "params": {"vdc": "900m", "srcType": "dc"},
                "terms": {"PLUS": "VDD", "MINUS": "gnd!"},
            },
            {
                "name": "CL0",
                "lib": "analogLib",
                "cell": "cap",
                "params": {"c": "2f"},
                "terms": {"PLUS": "OUT", "MINUS": "gnd!"},
            },
            {
                "name": "GND0",
                "lib": "analogLib",
                "cell": "gnd",
                "params": {},
                "terms": {"gnd!": "gnd!"},
            },
        ]
    }
    design = {"library": "vb_pdk_smoke", "cell": "inverter_tb", "view": "schematic"}

    comparison = bridge_worker._compare_ade_input_to_schematic(
        bridge_worker._parse_ade_spectre_input(text),
        schematic,
        design=design,
    )

    assert comparison["verified_parameter_pairs"] == 19
    assert comparison["omitted_ground_symbols"] == ["GND0"]
    assert comparison["effective_pdk_width_semantics_verified"] is False
    assert {item["instance"] for item in comparison["excluded_cdf_semantics"]} == {
        "MN0",
        "MP0",
    }


def _native_sweep_verification() -> dict:
    return {
        "expected_tests": ["VDA"],
        "expected_corners": None,
        "expected_global_variable_selections": {},
        "variables": [
            {
                "name": "CL",
                "expected_value": "1f,2f",
                "scope": "global",
                "scope_name": None,
                "sweep": True,
            }
        ],
        "points": [
            {
                "point": 1,
                "maestro_point": None,
                "corner": None,
                "values": {"CL": "1f"},
            },
            {
                "point": 2,
                "maestro_point": None,
                "corner": None,
                "values": {"CL": "2f"},
            },
        ],
        "input_bindings": [
            {
                "test": "VDA",
                "variable": "CL",
                "instance": "CL0",
                "oa_parameter": "c",
            }
        ],
        "expected_output_evaluation_errors": [],
    }


def _native_corner_sweep_verification() -> dict:
    corners = ["Nominal", "VDA_LOW_VDD", "VDA_NOMINAL_VDD"]
    points: list[dict] = []
    point = 1
    for maestro_point, load in ((1, "1f"), (2, "4f")):
        for corner, vdd in zip(corners, ("0.9", "0.8", "0.9"), strict=True):
            points.append(
                {
                    "point": point,
                    "maestro_point": maestro_point,
                    "corner": corner,
                    "values": {"CL": load, "VDD": vdd},
                }
            )
            point += 1
    return {
        "expected_tests": ["VDA"],
        "expected_corners": corners,
        "expected_global_variable_selections": {"CL": False, "VDD": True},
        "variables": [
            {
                "name": "CL",
                "expected_value": "1f,4f",
                "scope": "test",
                "scope_name": "VDA",
                "sweep": True,
            },
            {
                "name": "VDD",
                "expected_value": "0.9",
                "scope": "global",
                "scope_name": None,
                "sweep": False,
            },
            {
                "name": "VDD",
                "expected_value": "0.8",
                "scope": "corner",
                "scope_name": "VDA_LOW_VDD",
                "sweep": False,
            },
            {
                "name": "VDD",
                "expected_value": "0.9",
                "scope": "corner",
                "scope_name": "VDA_NOMINAL_VDD",
                "sweep": False,
            },
        ],
        "points": points,
        "input_bindings": [
            {
                "test": "VDA",
                "variable": "CL",
                "instance": "CL0",
                "oa_parameter": "c",
            },
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VDD0",
                "oa_parameter": "vdc",
            },
            {
                "test": "VDA",
                "variable": "VDD",
                "instance": "VIN0",
                "oa_parameter": "v2",
            },
        ],
        "expected_output_evaluation_errors": [],
    }


def _native_sweep_schematic() -> dict:
    return {
        "instances": [
            {
                "name": "CL0",
                "lib": "analogLib",
                "cell": "cap",
                "params": {"c": "CL"},
                "terms": {"PLUS": "OUT", "MINUS": "gnd!"},
            },
            {
                "name": "GND0",
                "lib": "analogLib",
                "cell": "gnd",
                "params": {},
                "terms": {"gnd!": "gnd!"},
            },
        ]
    }


def _native_corner_sweep_schematic() -> dict:
    return {
        "instances": [
            {
                "name": "VDD0",
                "lib": "analogLib",
                "cell": "vdc",
                "params": {"vdc": "VDD", "srcType": "dc"},
                "terms": {"PLUS": "VDD", "MINUS": "gnd!"},
            },
            {
                "name": "VIN0",
                "lib": "analogLib",
                "cell": "vpulse",
                "params": {
                    "v1": "0",
                    "v2": "VDD",
                    "per": "100p",
                    "td": "0",
                    "tr": "5p",
                    "tf": "5p",
                    "pw": "50p",
                    "srcType": "pulse",
                },
                "terms": {"PLUS": "IN", "MINUS": "gnd!"},
            },
            {
                "name": "CL0",
                "lib": "analogLib",
                "cell": "cap",
                "params": {"c": "CL"},
                "terms": {"PLUS": "OUT", "MINUS": "gnd!"},
            },
        ]
    }


def _native_sweep_input(value: str) -> str:
    return f"""// Design library name: vda_test
// Design cell name: vda_sweep_tb
// Design view name: schematic
simulator lang=spectre
parameters CL={value}
CL0 (OUT 0) capacitor c=CL
tran tran stop=1n
save OUT
"""


def _native_corner_detail_csv() -> str:
    return (
        ",,Parameter,Nominal,,,,,,VDA_LOW_VDD,VDA_NOMINAL_VDD\n"
        ",,VDD,900e-3,,,,,,800e-3,900e-3\n"
        "Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,Min,Max,"
        "VDA_LOW_VDD,VDA_NOMINAL_VDD\n"
        "Parameters: CL=1f,,,,,,,,,,\n"
        "1,VDA,DelayVdd,2.8e-12,,,,,,3.2e-12,2.8e-12\n"
        "1,VDA,EnergyVdd,1.6e-15,,,,,,1.4e-15,1.6e-15\n"
        "Parameters: CL=4f,,,,,,,,,,\n"
        "2,VDA,DelayVdd,4.1e-12,,,,,,4.8e-12,4.1e-12\n"
        "2,VDA,EnergyVdd,2.7e-15,,,,,,2.4e-15,2.7e-15\n"
    )


def test_parse_ade_corner_detail_csv_preserves_point_corner_grid() -> None:
    text = _native_corner_detail_csv()

    parsed = bridge_worker._parse_ade_corner_detail_csv(
        text,
        history="Interactive.14",
        expected_corners=["Nominal", "VDA_LOW_VDD", "VDA_NOMINAL_VDD"],
    )

    assert parsed["history"] == "Interactive.14"
    assert parsed["tests"] == ["VDA"]
    assert len(parsed["points"]) == 6
    assert [
        (point["maestro_point"], point["corner"])
        for point in parsed["points"]
    ] == [
        (1, "Nominal"),
        (1, "VDA_LOW_VDD"),
        (1, "VDA_NOMINAL_VDD"),
        (2, "Nominal"),
        (2, "VDA_LOW_VDD"),
        (2, "VDA_NOMINAL_VDD"),
    ]
    assert parsed["points"][1]["parameters"] == {
        "CL": "1f",
        "VDD": "800e-3",
    }
    assert parsed["points"][5]["outputs"]["EnergyVdd"]["value"] == (
        "2.7e-15"
    )
    assert parsed["detail_csv_sha256"] == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def test_ade_spectre_input_resolves_a_declared_oa_sweep_binding() -> None:
    parsed = bridge_worker._parse_ade_spectre_input(_native_sweep_input("2f"))

    comparison = bridge_worker._compare_ade_input_to_schematic(
        parsed,
        _native_sweep_schematic(),
        design={
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "schematic",
        },
        sweep_bindings={
            ("CL0", "c"): {"variable": "CL", "point_value": "2f"}
        },
    )

    assert parsed["design_variables"] == {"CL": "2f"}
    assert comparison["effective_sweep_bindings_verified"] is True
    assert comparison["verified_sweep_binding_pairs"] == 1
    check = comparison["instances"][0]["parameter_checks"][0]
    assert check["oa_value"] == "CL"
    assert check["netlist_value"] == "CL"
    assert check["effective_value"] == "2f"


def test_ade_spectre_input_rejects_a_sweep_point_value_mismatch() -> None:
    with pytest.raises(RuntimeError, match="effective sweep value mismatch"):
        bridge_worker._compare_ade_input_to_schematic(
            bridge_worker._parse_ade_spectre_input(_native_sweep_input("4f")),
            _native_sweep_schematic(),
            design={
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "schematic",
            },
            sweep_bindings={
                ("CL0", "c"): {"variable": "CL", "point_value": "2f"}
            },
        )


def test_ade_spectre_input_verifies_a_symbolic_native_sweep_binding() -> None:
    comparison = bridge_worker._compare_ade_input_to_schematic(
        bridge_worker._parse_ade_spectre_input(_native_sweep_input("1f")),
        _native_sweep_schematic(),
        design={
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "schematic",
        },
        symbolic_sweep_bindings={("CL0", "c"): "CL"},
    )

    assert comparison["symbolic_sweep_bindings_verified"] is True
    assert comparison["effective_sweep_bindings_verified"] is False
    assert comparison["verified_sweep_binding_pairs"] == 1


def test_ade_spectre_input_verifies_one_symbolic_variable_at_two_sources() -> None:
    text = """// Design library name: vda_test
// Design cell name: vda_vdd_cl_tb
// Design view name: schematic
simulator lang=spectre
parameters CL=1f VDD=0.8
VDD0 (VDD 0) vsource dc=VDD type=dc
VIN0 (IN 0) vsource type=pulse val0=0 val1=VDD period=100p delay=0 rise=5p fall=5p width=50p
CL0 (OUT 0) capacitor c=CL
tran tran stop=300p
save IN OUT
"""
    schematic = {
        "instances": [
            {
                "name": "VDD0",
                "lib": "analogLib",
                "cell": "vdc",
                "params": {"vdc": "VDD", "srcType": "dc"},
                "terms": {"PLUS": "VDD", "MINUS": "gnd!"},
            },
            {
                "name": "VIN0",
                "lib": "analogLib",
                "cell": "vpulse",
                "params": {
                    "v1": "0",
                    "v2": "VDD",
                    "per": "100p",
                    "td": "0",
                    "tr": "5p",
                    "tf": "5p",
                    "pw": "50p",
                    "srcType": "pulse",
                },
                "terms": {"PLUS": "IN", "MINUS": "gnd!"},
            },
            {
                "name": "CL0",
                "lib": "analogLib",
                "cell": "cap",
                "params": {"c": "CL"},
                "terms": {"PLUS": "OUT", "MINUS": "gnd!"},
            },
        ]
    }

    comparison = bridge_worker._compare_ade_input_to_schematic(
        bridge_worker._parse_ade_spectre_input(text),
        schematic,
        design={
            "library": "vda_test",
            "cell": "vda_vdd_cl_tb",
            "view": "schematic",
        },
        symbolic_sweep_bindings={
            ("CL0", "c"): "CL",
            ("VDD0", "vdc"): "VDD",
            ("VIN0", "v2"): "VDD",
        },
    )

    assert comparison["symbolic_sweep_bindings_verified"] is True
    assert comparison["verified_sweep_binding_pairs"] == 3
    assert comparison["verified_sweep_bindings"] == [
        {"instance": "CL0", "oa_parameter": "c"},
        {"instance": "VDD0", "oa_parameter": "vdc"},
        {"instance": "VIN0", "oa_parameter": "v2"},
    ]


def test_native_ade_sweep_does_not_reuse_unlabeled_artifacts_across_tests() -> None:
    manifest = [
        {
            "path": "Interactive.12/1/netlist/input.scs",
            "binding": "exact_history_path",
            "category": "simulator_input",
            "size_bytes": 64,
        }
    ]

    assert bridge_worker._ade_point_artifacts(
        manifest,
        history="Interactive.12",
        point=1,
        test="VDA",
        category="simulator_input",
        allow_unlabeled=True,
        filename="input.scs",
    ) == manifest
    assert bridge_worker._ade_point_artifacts(
        manifest,
        history="Interactive.12",
        point=1,
        test="VDA",
        category="simulator_input",
        allow_unlabeled=False,
        filename="input.scs",
    ) == []


def test_native_ade_sweep_binds_each_structured_point_to_input_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = "Interactive.12"
    manifest: list[dict] = []
    texts: dict[str, str] = {}
    for point, value in ((1, "1f"), (2, "2f")):
        input_path = (
            f"/data/xum/results/{history}/{point}/VDA/netlist/input.scs"
        )
        text = _native_sweep_input(value)
        texts[input_path] = text
        manifest.extend(
            [
                {
                    "path": f"{history}/{point}/VDA/netlist/input.scs",
                    "remote_path": input_path,
                    "remote_paths": [input_path],
                    "binding": "exact_history_path",
                    "category": "simulator_input",
                    "size_bytes": len(text.encode("utf-8")),
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "evidence_source": "eda_result",
                },
                {
                    "path": f"{history}/{point}/VDA/psf/tran.tran",
                    "remote_path": (
                        f"/data/xum/results/{history}/{point}/VDA/psf/tran.tran"
                    ),
                    "remote_paths": [
                        f"/data/xum/results/{history}/{point}/VDA/psf/tran.tran"
                    ],
                    "binding": "exact_history_path",
                    "category": "eda_result",
                    "size_bytes": 64,
                    "sha256": str(point) * 64,
                    "evidence_source": "eda_result",
                },
            ]
        )
    results = {
        "history": history,
        "points": [
            {
                "point": 1,
                "parameters": {"CL": "1e-15"},
                "outputs": {"VoutAvg": {"value": "0.45", "pass_fail": "pass"}},
            },
            {
                "point": 2,
                "parameters": {"CL": "2e-15"},
                "outputs": {"VoutAvg": {"value": "0.40", "pass_fail": "pass"}},
            },
        ],
    }
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_test_design_readback",
        lambda *_args, **_kwargs: {
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "schematic",
        },
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_schematic",
        lambda *_args, **_kwargs: _native_sweep_schematic(),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_remote_text_via_skill",
        lambda _client, path, **_kwargs: texts[path],
    )

    evidence = bridge_worker._verify_ade_sweep_consistency(
        object(),
        session="fnxSweep12",
        tests=["VDA"],
        history=history,
        results=results,
        artifact_evidence={"artifact_manifest": manifest},
        verification=_native_sweep_verification(),
    )

    assert evidence["sweep_point_consistency_verified"] is True
    assert evidence["effective_simulation_values_verified"] is True
    assert evidence["exact_point_input_result_binding_verified"] is True
    assert evidence["native_sweep_database_binding_verified"] is False
    assert evidence["sweep_point_evidence_mode"] == "exact_point_artifacts"
    assert len(evidence["sweep_point_consistency"]) == 2
    assert len(evidence["simulator_input_consistency"]) == 2
    assert {
        point["result_parameters"]["CL"]
        for point in evidence["sweep_point_consistency"]
    } == {"1e-15", "2e-15"}


def _native_sweep_database_case(
    *,
    simulation_errors: int = 0,
    include_rdb: bool = True,
    include_netlist_reference: bool = True,
) -> tuple[dict, list[dict], dict[str, str]]:
    history = "Interactive.12"
    input_remote = "/data/xum/runtime/VDA/input.scs"
    input_text = """// Design library name: vda_test
// Design cell name: vda_sweep_tb
// Design view name: schematic
simulator lang=spectre
parameters CL=1f
{include_statement}
tran tran stop=1n
save OUT
""".format(
        include_statement=(
            'include "netlist"' if include_netlist_reference else "// no include"
        )
    )
    netlist_remote = "/data/xum/runtime/VDA/netlist"
    netlist_text = "CL0 (OUT 0) capacitor c=CL\n"
    log_remote = f"/data/xum/results/{history}.log"
    log_text = (
        "Starting Single Run, Sweeps and Corners...\n"
        "Best design point: 1\n"
        "Design parameters:\n\tCL\t\t1f\n"
        f"{history}\n"
        "Number of points completed: 2\n"
        f"Number of simulation errors: {simulation_errors}\n"
        f"{history} completed.\n"
    )
    manifest = [
        {
            "path": f"{history}/runtime/VDA/input.scs",
            "remote_path": input_remote,
            "binding": "unique_runtime_session",
            "category": "simulator_input",
            "size_bytes": len(input_text.encode()),
            "sha256": hashlib.sha256(input_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
        {
            "path": f"{history}/{history}.log",
            "remote_path": log_remote,
            "binding": "exact_history_companion",
            "category": "run_log",
            "size_bytes": len(log_text.encode()),
            "sha256": hashlib.sha256(log_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
        {
            "path": f"{history}/runtime/VDA/netlist",
            "remote_path": netlist_remote,
            "binding": "unique_runtime_session",
            "category": "simulator_input",
            "size_bytes": len(netlist_text.encode()),
            "sha256": hashlib.sha256(netlist_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
    ]
    if include_rdb:
        manifest.append(
            {
                "path": f"{history}/{history}.rdb",
                "remote_path": f"/data/xum/results/{history}.rdb",
                "binding": "exact_history_companion",
                "category": "eda_result",
                "size_bytes": 4096,
                "sha256": "a" * 64,
                "evidence_source": "eda_result",
            }
        )
    results = {
        "history": history,
        "points": [
            {
                "point": 1,
                "parameters": {"CL": "1f"},
                "outputs": {"VoutAvg": {"value": "0.4155"}},
            },
            {
                "point": 2,
                "parameters": {"CL": "2f"},
                "outputs": {"VoutAvg": {"value": "0.4198"}},
            },
        ],
    }
    return results, manifest, {
        input_remote: input_text,
        netlist_remote: netlist_text,
        log_remote: log_text,
    }


def _patch_native_sweep_database_readbacks(
    monkeypatch: pytest.MonkeyPatch, texts: dict[str, str]
) -> None:
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_test_design_readback",
        lambda *_args, **_kwargs: {
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "schematic",
        },
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_schematic",
        lambda *_args, **_kwargs: _native_sweep_schematic(),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_remote_text_via_skill",
        lambda _client, path, **_kwargs: texts[path],
    )


def test_native_ade_sweep_accepts_ic618_shared_input_and_history_rdb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case()
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    evidence = bridge_worker._verify_ade_sweep_consistency(
        object(),
        session="fnxSweep12",
        tests=["VDA"],
        history="Interactive.12",
        results=results,
        artifact_evidence={"artifact_manifest": manifest},
        verification=_native_sweep_verification(),
    )

    assert evidence["effective_simulation_values_verified"] is True
    assert evidence["exact_point_input_result_binding_verified"] is False
    assert evidence["native_sweep_database_binding_verified"] is True
    assert evidence["sweep_point_evidence_mode"] == (
        "maestro_exact_history_rdb_with_shared_symbolic_runtime_input"
    )
    assert evidence["sweep_history_log_evidence"]["points_completed"] == 2
    assert len(evidence["simulator_input_consistency"]) == 1
    assert {
        point["result_parameters"]["CL"]
        for point in evidence["sweep_point_consistency"]
    } == {"1f", "2f"}


def test_native_ade_corner_sweep_binds_two_points_to_three_corner_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = "Interactive.14"
    input_remote = "/data/xum/runtime/VDA/input.scs"
    input_text = """// Design library name: vda_test
// Design cell name: vda_corner_tb
// Design view name: schematic
simulator lang=spectre
parameters CL=1f VDD=900m
include "netlist"
tran tran stop=300p
save IN OUT
"""
    netlist_remote = "/data/xum/runtime/VDA/netlist"
    netlist_text = """VDD0 (VDD 0) vsource dc=VDD type=dc
VIN0 (IN 0) vsource type=pulse val0=0 val1=VDD period=100p delay=0 rise=5p fall=5p width=50p
CL0 (OUT 0) capacitor c=CL
"""
    log_remote = f"/data/xum/results/{history}.log"
    log_text = (
        "Starting Single Run, Sweeps and Corners...\n"
        "Number of points completed: 2\n"
        "Number of simulation errors: 0\n"
        f"{history} completed.\n"
    )
    manifest = [
        {
            "path": f"{history}/runtime/VDA/input.scs",
            "remote_path": input_remote,
            "binding": "unique_runtime_session",
            "category": "simulator_input",
            "size_bytes": len(input_text.encode()),
            "sha256": hashlib.sha256(input_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
        {
            "path": f"{history}/runtime/VDA/netlist",
            "remote_path": netlist_remote,
            "binding": "unique_runtime_session",
            "category": "simulator_input",
            "size_bytes": len(netlist_text.encode()),
            "sha256": hashlib.sha256(netlist_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
        {
            "path": f"{history}/{history}.log",
            "remote_path": log_remote,
            "binding": "exact_history_companion",
            "category": "run_log",
            "size_bytes": len(log_text.encode()),
            "sha256": hashlib.sha256(log_text.encode()).hexdigest(),
            "evidence_source": "eda_result",
        },
        {
            "path": f"{history}/{history}.rdb",
            "remote_path": f"/data/xum/results/{history}.rdb",
            "binding": "exact_history_companion",
            "category": "eda_result",
            "size_bytes": 4096,
            "sha256": "a" * 64,
            "evidence_source": "eda_result",
        },
    ]
    detail_csv = _native_corner_detail_csv()
    parsed = bridge_worker._parse_ade_corner_detail_csv(
        detail_csv,
        history=history,
        expected_corners=["Nominal", "VDA_LOW_VDD", "VDA_NOMINAL_VDD"],
    )
    results = {
        "history": history,
        "corner_points": parsed["points"],
        "corner_order": parsed["corners"],
        "corner_tests": parsed["tests"],
        "corner_detail_csv_sha256": parsed["detail_csv_sha256"],
        "corner_detail_csv_size_bytes": parsed["detail_csv_size_bytes"],
        "corner_detail_csv_evidence_sources": {
            "raw": "eda_result",
            "parser": "software_inference",
        },
    }
    texts = {
        input_remote: input_text,
        netlist_remote: netlist_text,
        log_remote: log_text,
    }
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_test_design_readback",
        lambda *_args, **_kwargs: {
            "library": "vda_test",
            "cell": "vda_corner_tb",
            "view": "schematic",
        },
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_schematic",
        lambda *_args, **_kwargs: _native_corner_sweep_schematic(),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_remote_text_via_skill",
        lambda _client, path, **_kwargs: texts[path],
    )

    evidence = bridge_worker._verify_ade_sweep_consistency(
        object(),
        session="fnxSweep14",
        tests=["VDA"],
        history=history,
        results=results,
        artifact_evidence={"artifact_manifest": manifest},
        verification=_native_corner_sweep_verification(),
    )

    assert evidence["native_sweep_database_binding_verified"] is True
    assert evidence["exact_point_input_result_binding_verified"] is False
    assert evidence["sweep_history_log_evidence"]["points_completed"] == 2
    assert evidence["simulator_input_consistency"][0][
        "retained_maestro_point"
    ] == 1
    assert [
        (point["maestro_point"], point["corner"])
        for point in evidence["sweep_point_consistency"]
    ] == [
        (1, "Nominal"),
        (1, "VDA_LOW_VDD"),
        (1, "VDA_NOMINAL_VDD"),
        (2, "Nominal"),
        (2, "VDA_LOW_VDD"),
        (2, "VDA_NOMINAL_VDD"),
    ]
    assert evidence["corner_detail_csv_sha256"] == hashlib.sha256(
        detail_csv.encode("utf-8")
    ).hexdigest()


def test_native_ade_sweep_accounts_for_exact_declared_output_evaluation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case(simulation_errors=1)
    results["points"][0]["outputs"]["LegacyRise"] = {"value": "eval err"}
    verification = _native_sweep_verification()
    verification["expected_output_evaluation_errors"] = [
        {
            "test": "VDA",
            "output": "LegacyRise",
            "point_values": {"CL": "1f"},
        }
    ]
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    evidence = bridge_worker._verify_ade_sweep_consistency(
        object(),
        session="fnxSweep12",
        tests=["VDA"],
        history="Interactive.12",
        results=results,
        artifact_evidence={"artifact_manifest": manifest},
        verification=verification,
    )

    assert evidence["expected_output_evaluation_errors_verified"] is True
    assert evidence["output_evaluation_error_count"] == 1
    assert evidence["output_evaluation_errors"][0]["output"] == "LegacyRise"
    assert evidence["sweep_history_log_evidence"]["simulation_errors"] == 1
    assert (
        evidence["sweep_history_log_evidence"]["unaccounted_simulation_errors"]
        == 0
    )


def test_native_ade_sweep_rejects_an_undeclared_output_evaluation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case(simulation_errors=1)
    results["points"][0]["outputs"]["LegacyRise"] = {"value": "eval err"}
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    with pytest.raises(RuntimeError, match="did not exactly match"):
        bridge_worker._verify_ade_sweep_consistency(
            object(),
            session="fnxSweep12",
            tests=["VDA"],
            history="Interactive.12",
            results=results,
            artifact_evidence={"artifact_manifest": manifest},
            verification=_native_sweep_verification(),
        )


def test_native_ade_sweep_database_mode_rejects_a_missing_history_rdb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case(include_rdb=False)
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    with pytest.raises(RuntimeError, match="exact-history RDB"):
        bridge_worker._verify_ade_sweep_consistency(
            object(),
            session="fnxSweep12",
            tests=["VDA"],
            history="Interactive.12",
            results=results,
            artifact_evidence={"artifact_manifest": manifest},
            verification=_native_sweep_verification(),
        )


def test_native_ade_sweep_database_mode_rejects_history_simulation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case(simulation_errors=1)
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    with pytest.raises(RuntimeError, match="zero simulation errors"):
        bridge_worker._verify_ade_sweep_consistency(
            object(),
            session="fnxSweep12",
            tests=["VDA"],
            history="Interactive.12",
            results=results,
            artifact_evidence={"artifact_manifest": manifest},
            verification=_native_sweep_verification(),
        )


def test_native_ade_sweep_database_mode_requires_the_sibling_include(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, manifest, texts = _native_sweep_database_case(
        include_netlist_reference=False
    )
    _patch_native_sweep_database_readbacks(monkeypatch, texts)

    with pytest.raises(RuntimeError, match="did not include its sibling netlist"):
        bridge_worker._verify_ade_sweep_consistency(
            object(),
            session="fnxSweep12",
            tests=["VDA"],
            history="Interactive.12",
            results=results,
            artifact_evidence={"artifact_manifest": manifest},
            verification=_native_sweep_verification(),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda results, manifest: results["points"][1]["parameters"].update(
                {"CL": "4f"}
            ),
            "result parameter mismatch",
        ),
        (
            lambda results, manifest: results["points"][1].update(
                {"outputs": {"VoutAvg": {"value": ""}}}
            ),
            "no non-empty scalar output",
        ),
        (
            lambda results, manifest: manifest.__setitem__(
                slice(None),
                [
                    item
                    for item in manifest
                    if not item["path"].endswith("2/VDA/netlist/input.scs")
                ],
            ),
            "no exact-history input.scs",
        ),
        (
            lambda results, manifest: manifest.__setitem__(
                slice(None),
                [
                    item
                    for item in manifest
                    if not item["path"].endswith("2/VDA/psf/tran.tran")
                ],
            ),
            "no non-empty exact-history result",
        ),
    ],
)
def test_native_ade_sweep_rejects_incomplete_point_evidence(
    mutation, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = "Interactive.12"
    results = {
        "history": history,
        "points": [
            {
                "point": 1,
                "parameters": {"CL": "1f"},
                "outputs": {"VoutAvg": {"value": "0.45"}},
            },
            {
                "point": 2,
                "parameters": {"CL": "2f"},
                "outputs": {"VoutAvg": {"value": "0.40"}},
            },
        ],
    }
    texts: dict[str, str] = {}
    manifest: list[dict] = []
    for point, value in ((1, "1f"), (2, "2f")):
        remote = f"/data/xum/results/{history}/{point}/VDA/netlist/input.scs"
        text = _native_sweep_input(value)
        texts[remote] = text
        manifest.extend(
            [
                {
                    "path": f"{history}/{point}/VDA/netlist/input.scs",
                    "remote_path": remote,
                    "binding": "exact_history_path",
                    "category": "simulator_input",
                    "size_bytes": len(text),
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                },
                {
                    "path": f"{history}/{point}/VDA/psf/tran.tran",
                    "remote_path": f"/data/xum/results/{history}/{point}/result",
                    "binding": "exact_history_path",
                    "category": "eda_result",
                    "size_bytes": 64,
                    "sha256": str(point) * 64,
                },
            ]
        )
    mutation(results, manifest)
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_test_design_readback",
        lambda *_args, **_kwargs: {
            "library": "vda_test",
            "cell": "vda_sweep_tb",
            "view": "schematic",
        },
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_schematic",
        lambda *_args, **_kwargs: _native_sweep_schematic(),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_read_remote_text_via_skill",
        lambda _client, path, **_kwargs: texts[path],
    )

    with pytest.raises(RuntimeError, match=message):
        bridge_worker._verify_ade_sweep_consistency(
            object(),
            session="fnxSweep12",
            tests=["VDA"],
            history=history,
            results=results,
            artifact_evidence={"artifact_manifest": manifest},
            verification=_native_sweep_verification(),
        )


def test_native_ade_sweep_setup_readback_requires_the_exact_saved_declaration() -> None:
    class Client:
        def execute_skill(self, expression, **_kwargs):
            assert "maeGetSetup" in expression
            return SimpleNamespace(output='("VDA")', errors=[])

    readback = bridge_worker._read_maestro_sweep_setup(
        Client(),
        lambda _client, name, **_kwargs: (
            '"1f,2f"' if name == "CL" else "nil"
        ),
        _native_sweep_verification(),
        session="fnxSweep12",
    )

    assert readback["tests"] == ["VDA"]
    assert readback["variables"] == {"CL": "1f,2f"}
    assert readback["variable_readback_methods"] == {
        "CL": "bridge_public_get_var"
    }
    assert re.fullmatch(r"[0-9a-f]{64}", readback["fingerprint_sha256"])

    with pytest.raises(RuntimeError, match="sweep variable mismatch"):
        bridge_worker._read_maestro_sweep_setup(
            Client(),
            lambda *_args, **_kwargs: '"4f"',
            _native_sweep_verification(),
            session="fnxSweep12",
        )


def test_native_ade_sweep_setup_readback_pins_global_variable_selections() -> None:
    class Client:
        def execute_skill(self, expression, **_kwargs):
            if '?typeName "variables"' in expression:
                return SimpleNamespace(output='(("CL" "VDD") nil)', errors=[])
            if expression.startswith("maeGetSetup(?session"):
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    verification = _native_sweep_verification()
    verification["expected_global_variable_selections"] = {"CL": True}
    readback = bridge_worker._read_maestro_sweep_setup(
        Client(),
        lambda _client, name, **_kwargs: (
            '"1f,2f"' if name == "CL" else "nil"
        ),
        verification,
        session="fnxSweep12",
    )

    assert readback["global_variable_selections"] == {"CL": True}
    assert readback["global_variable_selection_state"] == {
        "enabled": ["CL", "VDD"],
        "disabled": [],
    }
    assert readback["global_variable_selection_readback_method"] == (
        "cadence_maeGetSetup_enabled_variables_via_bridge_skill_channel"
    )

    verification["expected_global_variable_selections"] = {"CL": False}
    with pytest.raises(RuntimeError, match="selections changed"):
        bridge_worker._read_maestro_sweep_setup(
            Client(),
            lambda *_args, **_kwargs: '"1f,2f"',
            verification,
            session="fnxSweep12",
        )


def test_background_maestro_run_wires_native_sweep_preflight_and_point_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_calls: list[str] = []

    class Client:
        def execute_skill(self, expression, **_kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    _install_fake_maestro_module(
        monkeypatch,
        open_session=lambda *_args: "fnxSweep12",
        close_session=lambda *_args: None,
        run_and_wait=lambda *_args, **_kwargs: ('"Interactive.12"', "done"),
        read_results=lambda *_args, **_kwargs: {
            "history": "Interactive.12",
            "points": [
                {
                    "point": 1,
                    "parameters": {"CL": "1f"},
                    "outputs": {"VoutAvg": {"value": "0.45"}},
                },
                {
                    "point": 2,
                    "parameters": {"CL": "2f"},
                    "outputs": {"VoutAvg": {"value": "0.40"}},
                },
            ],
        },
        get_var=lambda *_args, **_kwargs: '"1f,2f"',
    )
    _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(
        bridge_worker,
        "_read_maestro_sweep_setup",
        lambda *_args, **_kwargs: (
            setup_calls.append("read")
            or {
                "tests": ["VDA"],
                "corners": None,
                "variables": {"CL": "1f,2f"},
                "variable_readback_methods": {"CL": "bridge_public_get_var"},
                "fingerprint_sha256": "a" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        bridge_worker,
        "_collect_background_ade_artifacts",
        lambda *_args, **_kwargs: {
            "artifact_history": "Interactive.12",
            "artifact_history_path_binding_verified": True,
            "artifact_runtime_input_binding_verified": True,
            "artifact_run_binding_verified": True,
            "artifact_manifest": [{"path": "Interactive.12/1/VDA/input.scs"}],
            "artifact_counts": {"simulator_input": 1},
            "artifact_manifest_complete": True,
            "artifacts_captured": True,
            "simulation_fingerprint_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        bridge_worker,
        "_verify_ade_sweep_consistency",
        lambda *_args, **_kwargs: {
            "simulator_input_consistency_verified": True,
            "simulator_input_consistency": [{"point": 1}, {"point": 2}],
            "simulator_input_consistency_evidence_sources": {
                "maestro_design_and_oa": "bridge_readback",
                "spectre_input": "eda_result",
                "comparison": "software_inference",
            },
            "sweep_point_consistency_verified": True,
            "sweep_point_consistency": [{"point": 1}, {"point": 2}],
            "effective_simulation_values_verified": True,
            "exact_point_input_result_binding_verified": True,
            "sweep_consistency_evidence_sources": {
                "expected_sweep": "user_input",
                "maestro_setup_and_oa": "bridge_readback",
                "spectre_input_and_results": "eda_result",
                "comparison": "software_inference",
            },
        },
    )

    result = bridge_worker.run_background_maestro(
        {
            "task_id": "run-native-sweep",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "profile": {"remote_run_root": "/data/xum/vda_runs"},
            "ade_run": {
                "require_structured_outputs": True,
                "require_artifact_manifest": True,
                "require_simulator_input_consistency": True,
                "sweep_verification": _native_sweep_verification(),
            },
        }
    )

    assert setup_calls == ["read", "read"]
    assert result["sweep_setup_readback_before"] == result[
        "sweep_setup_readback_after"
    ]
    assert result["sweep_point_consistency_verified"] is True
    assert result["exact_point_input_result_binding_verified"] is True


def test_remote_ade_manifest_deduplicates_identical_copies_and_rejects_conflict() -> None:
    base = {
        "path": "Interactive.8/1/AC/netlist/netlist",
        "remote_path": "/data/xum/project/Interactive.8/1/AC/netlist/netlist",
        "source_location": "project",
        "size_bytes": 10,
        "sha256": "a" * 64,
        "category": "simulator_input",
        "evidence_source": "eda_result",
    }
    scratch = {
        **base,
        "remote_path": "/data/xum/scratch/Interactive.8/1/AC/netlist/netlist",
        "source_location": "scratch",
    }

    merged = bridge_worker._merge_remote_ade_artifacts([base, scratch])

    assert len(merged) == 1
    assert merged[0]["source_locations"] == ["project", "scratch"]
    assert len(merged[0]["remote_paths"]) == 2
    with pytest.raises(RuntimeError, match="conflicting exact-history artifact"):
        bridge_worker._merge_remote_ade_artifacts(
            [base, {**scratch, "sha256": "b" * 64}]
        )


def test_background_ade_artifact_gate_rejects_empty_core_input() -> None:
    manifest = [
        {
            "path": "Interactive.8/1/AC/netlist/netlist",
            "size_bytes": 0,
            "category": "simulator_input",
        },
        {
            "path": "Interactive.8/1/AC/netlist/input.scs",
            "size_bytes": 10,
            "category": "simulator_input",
        },
        {
            "path": "Interactive.8/1/AC/psf/ac.ac",
            "size_bytes": 10,
            "category": "eda_result",
        },
        {
            "path": "Interactive.8/Interactive.8.log",
            "size_bytes": 10,
            "category": "run_log",
        },
    ]

    with pytest.raises(RuntimeError, match="core simulator inputs: netlist"):
        bridge_worker._validate_background_ade_artifacts(
            manifest, "Interactive.8"
        )


def test_maestro_history_log_listing_and_single_completed_timeout_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locations = [
        {"source_location": "project", "maestro_root": "/data/xum/project"},
        {"source_location": "scratch", "maestro_root": "/data/xum/scratch"},
    ]

    class Client:
        def execute_skill(self, expression, **_kwargs):
            if "/data/xum/project" in expression:
                return SimpleNamespace(
                    output='("." "Interactive.3.log" "Interactive.4.log" '
                    '"Interactive.4.rdb")',
                    errors=[],
                )
            if "/data/xum/scratch" in expression:
                return SimpleNamespace(output="nil", errors=[])
            raise AssertionError(expression)

    histories = bridge_worker._maestro_history_log_paths(Client(), locations)
    assert histories == {
        "Interactive.3": ["/data/xum/project/Interactive.3.log"],
        "Interactive.4": ["/data/xum/project/Interactive.4.log"],
    }
    monkeypatch.setattr(
        bridge_worker,
        "_read_remote_text_via_skill",
        lambda *_args, **_kwargs: (
            "Number of points completed: 2\nInteractive.4 completed.\n"
        ),
    )

    history, evidence = (
        bridge_worker._recover_single_new_completed_maestro_history(
            Client(),
            locations=locations,
            histories_before={
                "Interactive.3": ["/data/xum/project/Interactive.3.log"]
            },
            timeout_error=TimeoutError("Simulation did not finish within 90s"),
        )
    )

    assert history == "Interactive.4"
    assert evidence["new_histories"] == ["Interactive.4"]
    assert evidence["completed_history_logs"][0]["size_bytes"] > 0
    assert evidence["evidence_sources"] == {
        "history_log": "eda_result",
        "selection": "software_inference",
    }


def test_maestro_timeout_recovery_rejects_ambiguous_new_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_history_log_paths",
        lambda *_args, **_kwargs: {
            "Interactive.4": ["/data/xum/results/Interactive.4.log"],
            "Interactive.5": ["/data/xum/results/Interactive.5.log"],
        },
    )

    with pytest.raises(RuntimeError, match="exactly one newly named"):
        bridge_worker._recover_single_new_completed_maestro_history(
            object(),
            locations=[],
            histories_before={},
            timeout_error=TimeoutError("Simulation did not finish within 90s"),
        )


def test_background_maestro_run_rejects_artifact_transport_failure_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if expression.startswith("list(ddGetObj"):
                return SimpleNamespace(
                    output=(
                        '("/data/xum/cds/vda_test" '
                        '("/data/xum/scratch/vda_test/vda_manual_tb/maestro/'
                        'results/maestro/Interactive.8/1/AC"))'
                    ),
                    errors=[],
                )
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

        def run_shell_command(self, command, **kwargs):
            calls.append(("shell", command))
            return SimpleNamespace(ok=False, errors=["transport interrupted"])

        def download_file(self, *_args, **_kwargs):
            raise AssertionError("failed manifest command must not be downloaded")

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=lambda *_args: "fnxBackground8",
        close_session=fake_close_session,
        run_and_wait=lambda *_args, **_kwargs: ('"Interactive.8"', "done"),
        read_results=lambda *_args, **_kwargs: {
            "history": "Interactive.8",
            "points": [{"outputs": {"BW": {"value": "1G"}}}],
        },
    )
    _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="transport interrupted") as failure:
        bridge_worker.run_background_maestro(
            {
                "task_id": "transport-failure",
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "profile": {"remote_run_root": "/data/xum/vda_runs"},
                "ade_run": {"require_artifact_manifest": True},
            }
        )

    assert "/data/xum/vda_runs/vda_ade_manifest_" in str(failure.value)
    assert calls[-1] == ("close", "fnxBackground8")

    calls.clear()
    partial = bridge_worker.run_background_maestro(
        {
            "task_id": "optional-transport-failure",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "profile": {"remote_run_root": "/data/xum/vda_runs"},
            "ade_run": {"require_artifact_manifest": False},
        }
    )

    assert partial["artifact_manifest_complete"] is False
    assert partial["artifacts_captured"] is False
    assert "transport interrupted" in partial["artifact_capture_error"]
    assert calls[-1] == ("close", "fnxBackground8")


def test_background_maestro_run_rejects_structured_history_mismatch_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=lambda *_args: "fnxBackground8",
        close_session=fake_close_session,
        run_and_wait=lambda *_args, **_kwargs: ('"Interactive.8"', "done"),
        read_results=lambda *_args, **_kwargs: {
            "history": "Interactive.7",
            "points": [{"outputs": {"BW": {"value": "1G"}}}],
        },
    )
    _stub_background_runtime(monkeypatch)
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="history does not match"):
        bridge_worker.run_background_maestro(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_run": {},
            }
        )

    assert calls == [("close", "fnxBackground8")]


def test_maestro_variable_patch_saves_once_and_reopens_for_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    state = {
        "persisted": {
            "bias_v": None,
            "test:VDA:bias_v": "0.35",
            "corner:TT:vdd": "0.9",
        },
        "working": {},
        "session": "",
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if '?typeName "corners"' in expression:
                return SimpleNamespace(output='("nominal" "TT")', errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            if expression.startswith("maeGetVar"):
                calls.append(("get_expr", expression))
                parts = expression.split('"')
                name, scope, scope_name, session = (
                    parts[1],
                    parts[3],
                    parts[5],
                    parts[7],
                )
                identity = f"{scope}:{scope_name}:{name}"
                calls.append(("get", session, identity))
                value = state["working"].get(identity)
                return SimpleNamespace(
                    output="nil" if value is None else f'"{value}"', errors=[]
                )
            if "axlPutVar" in expression:
                calls.append(("set_expr", expression))
                parts = expression.split('"')
                session = state["session"]
                scope_name, name, value = "TT", parts[1], parts[3]
                identity = f"corner:{scope_name}:{name}"
                calls.append(
                    ("set", session, identity, value, "corner", "axlPutVar")
                )
                state["working"][identity] = value
                return SimpleNamespace(output=f'(2001 "{value}")', errors=[])
            if "axlGetCorner" in expression:
                calls.append(("get_expr", expression))
                parts = expression.split('"')
                session, scope_name = parts[1], parts[3]
                calls.append(("corner_handle", session, scope_name))
                return SimpleNamespace(output="(1001 1002)", errors=[])
            if expression.startswith("axlGetVar("):
                name = expression.split('"')[1]
                identity = f"corner:TT:{name}"
                calls.append(("get", state["session"], identity))
                value = state["working"].get(identity)
                return SimpleNamespace(
                    output="0" if value is None else "2001", errors=[]
                )
            if expression.startswith("axlGetVarValue("):
                value = state["working"].get("corner:TT:vdd")
                return SimpleNamespace(output=f'"{value}"', errors=[])
            raise AssertionError(expression)

    sessions = iter(["fnxPatch1", "fnxPatch2"])

    def fake_open_session(_client, library, cell):
        session = next(sessions)
        state["working"] = dict(state["persisted"])
        state["session"] = session
        calls.append(("open", session, library, cell))
        return session

    def fake_get_var(_client, name, *, session):
        calls.append(("get", session, name))
        value = state["working"].get(name)
        return "nil" if value is None else f'"{value}"'

    def fake_set_var(
        _client,
        name,
        value,
        *,
        type_name="",
        type_value="",
        session,
    ):
        scope_name = type_value.strip('()"')
        identity = (
            name if not type_name else f"{type_name}:{scope_name}:{name}"
        )
        calls.append(
            ("set", session, identity, value, type_name, type_value)
        )
        state["working"][identity] = value

    def fake_save_setup(_client, library, cell, *, session):
        calls.append(("save", session, library, cell))
        state["persisted"] = dict(state["working"])

    def fake_close_session(_client, session):
        calls.append(("close", session))

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=fake_open_session,
        close_session=fake_close_session,
        get_var=fake_get_var,
        set_var=fake_set_var,
        save_setup=fake_save_setup,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    result = bridge_worker.apply_maestro_variables(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "backend": "maestro",
                "expected_tests": ["VDA"],
                "expected_corners": ["nominal", "TT"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    },
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "VDA",
                        "expected_value": "0.35",
                        "value": "0.30,0.35",
                    },
                    {
                        "name": "vdd",
                        "scope": "corner",
                        "scope_name": "TT",
                        "expected_value": "0.9",
                        "value": "0.95",
                    },
                ],
            },
        }
    )

    assert result["variable_scope"] == "declared_scopes"
    assert result["variable_scopes"] == ["global", "test", "corner"]
    assert result["tests_readback_before"] == ["VDA"]
    assert result["tests_readback_after"] == ["VDA"]
    assert result["corners_readback_before"] == ["nominal", "TT"]
    assert result["corners_readback_after"] == ["nominal", "TT"]
    assert result["before_variables"] == {
        "bias_v": None,
        "test:VDA:bias_v": "0.35",
        "corner:TT:vdd": "0.9",
    }
    assert result["immediate_variables"] == {
        "bias_v": "0.30,0.35,0.40",
        "test:VDA:bias_v": "0.30,0.35",
        "corner:TT:vdd": "0.95",
    }
    assert result["persisted_variables"] == result["immediate_variables"]
    assert result["declared_global_sweep_variables"] == ["bias_v"]
    assert result["declared_sweep_variables"] == [
        "bias_v",
        "test:VDA:bias_v",
    ]
    assert result["declared_scoped_values_verified"] is True
    assert result["variable_readback_methods"] == {
        "global": "bridge_public_get_var",
        "test": "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel",
        "corner": (
            "cadence_axlGetCorner_axlGetVarValue_via_bridge_skill_channel"
        ),
    }
    assert result["variable_write_methods"] == {
        "global": "bridge_public_set_var_global",
        "test": "bridge_public_set_var_list_typeValue",
        "corner": "cadence_axlPutVar_via_bridge_skill_channel",
    }
    assert result["test_or_corner_overrides_checked"] is False
    assert result["unlisted_scope_overrides_checked"] is False
    assert result["effective_simulation_value_verified"] is False
    assert result["maestro_setup_write_performed"] is True
    assert result["schematic_oa_write_performed"] is False
    assert result["automated_simulation_performed"] is False
    assert result["before_target_fingerprint_sha256"]
    assert result["after_target_fingerprint_sha256"]
    assert result["before_target_fingerprint_sha256"] != result[
        "after_target_fingerprint_sha256"
    ]
    scoped_get_expressions = [
        call[1] for call in calls if call[0] == "get_expr"
    ]
    assert any(
        '?typeName "test" ?typeValue "VDA"' in value
        for value in scoped_get_expressions
    )
    assert any(
        'axlGetCorner(sdb "TT")' in value and "list(sdb corner)" in value
        for value in scoped_get_expressions
    )
    assert [call[0] for call in calls].count("save") == 1
    assert [call[0] for call in calls].count("open") == 2
    assert [call[0] for call in calls].count("close") == 2
    first_set = next(index for index, call in enumerate(calls) if call[0] == "set")
    assert [call[2] for call in calls[:first_set] if call[0] == "get"] == [
        "bias_v",
        "test:VDA:bias_v",
        "corner:TT:vdd",
    ]
    scoped_sets = [call for call in calls if call[0] == "set" and call[4]]
    assert scoped_sets == [
        (
            "set",
            "fnxPatch1",
            "test:VDA:bias_v",
            "0.30,0.35",
            "test",
            '("VDA")',
        ),
        (
            "set",
            "fnxPatch1",
            "corner:TT:vdd",
            "0.95",
            "corner",
            "axlPutVar",
        ),
    ]


def test_maestro_variable_patch_stops_before_write_on_corner_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if '?typeName "corners"' in expression:
                return SimpleNamespace(output='("nominal" "FF")', errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    def unexpected(*args, **kwargs):
        raise AssertionError("corner mismatch must stop before variable I/O or save")

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=lambda *_args: "fnxCornerMismatch",
        close_session=lambda _client, session: calls.append(("close", session)),
        get_var=unexpected,
        set_var=unexpected,
        save_setup=unexpected,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="corners changed before"):
        bridge_worker.apply_maestro_variables(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {
                    "expected_tests": ["VDA"],
                    "expected_corners": ["nominal", "TT"],
                    "updates": [
                        {
                            "name": "vdd",
                            "scope": "corner",
                            "scope_name": "TT",
                            "expected_value": "0.9",
                            "value": "0.95",
                        }
                    ],
                },
            }
        )

    assert calls == [("close", "fnxCornerMismatch")]


def test_corner_variable_readback_rejects_unaddressable_corner_handle() -> None:
    update = {
        "name": "VDD",
        "scope": "corner",
        "scope_name": "Nominal",
    }

    class MissingClient:
        def execute_skill(self, expression, **kwargs):
            return SimpleNamespace(
                output="0",
                errors=[
                    "*Error* error: Cannot find a setup database entry for handle 0."
                ],
            )

    with pytest.raises(RuntimeError, match="named-corner handle readback failed"):
        bridge_worker._read_maestro_variable(
            MissingClient(), lambda *_args, **_kwargs: None, update, session="fnx1"
        )

    class BrokenClient:
        def execute_skill(self, expression, **kwargs):
            return SimpleNamespace(output="nil", errors=["unexpected SKILL failure"])

    with pytest.raises(RuntimeError, match="unexpected SKILL failure"):
        bridge_worker._read_maestro_variable(
            BrokenClient(), lambda *_args, **_kwargs: None, update, session="fnx1"
        )


def test_corner_variable_readback_treats_only_zero_variable_handle_as_absent() -> None:
    update = {
        "name": "VDD",
        "scope": "corner",
        "scope_name": "VDA_LOW_VDD",
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "axlGetCorner" in expression:
                return SimpleNamespace(output="(1001 1002)", errors=[])
            if expression.startswith("axlGetVar("):
                return SimpleNamespace(output="0", errors=[])
            raise AssertionError(expression)

    assert (
        bridge_worker._read_maestro_variable(
            Client(), lambda *_args, **_kwargs: None, update, session="fnx1"
        )
        is None
    )


def test_maestro_global_variable_selection_patch_preserves_other_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    state = {
        "persisted": {"enabled": ["CL", "VDD"], "disabled": []},
        "working": {"enabled": [], "disabled": []},
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if '?typeName "corners"' in expression:
                return SimpleNamespace(output='("Nominal")', errors=[])
            if '?typeName "variables"' in expression:
                enabled = " ".join(
                    f'"{name}"' for name in state["working"]["enabled"]
                )
                disabled = " ".join(
                    f'"{name}"' for name in state["working"]["disabled"]
                )
                return SimpleNamespace(
                    output=(
                        f"(({enabled}) "
                        + (f"({disabled}))" if disabled else "nil)")
                    ),
                    errors=[],
                )
            if expression.startswith("maeSetSetup"):
                calls.append(("selection", expression))
                state["working"]["enabled"].remove("CL")
                state["working"]["disabled"].append("CL")
                state["working"]["disabled"].sort()
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    sessions = iter(["fnxSelect1", "fnxSelect2"])

    def fake_open_session(_client, library, cell):
        session = next(sessions)
        state["working"] = {
            key: list(values) for key, values in state["persisted"].items()
        }
        calls.append(("open", session, library, cell))
        return session

    def fake_save_setup(_client, library, cell, *, session):
        calls.append(("save", session, library, cell))
        state["persisted"] = {
            key: list(values) for key, values in state["working"].items()
        }

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=fake_open_session,
        close_session=lambda _client, session: calls.append(("close", session)),
        get_var=lambda *_args, **_kwargs: "nil",
        set_var=lambda *_args, **_kwargs: None,
        save_setup=fake_save_setup,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    result = bridge_worker.apply_maestro_variables(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "expected_corners": ["Nominal"],
                "updates": [],
                "global_selection_updates": [
                    {
                        "name": "CL",
                        "expected_enabled": True,
                        "enabled": False,
                    }
                ],
            },
        }
    )

    assert result["variable_scope"] == "none"
    assert result["global_variable_selection_before"] == {"CL": True}
    assert result["global_variable_selection_immediate"] == {"CL": False}
    assert result["global_variable_selection_persisted"] == {"CL": False}
    assert result["global_variable_selection_state_before"] == {
        "enabled": ["CL", "VDD"],
        "disabled": [],
    }
    assert result["global_variable_selection_state_persisted"] == {
        "enabled": ["VDD"],
        "disabled": ["CL"],
    }
    assert result["global_variable_selection_preserved_undeclared"] is True
    assert len([call for call in calls if call[0] == "save"]) == 1
    assert len([call for call in calls if call[0] == "open"]) == 2


def test_maestro_variable_patch_worker_requires_explicit_old_value() -> None:
    with pytest.raises(RuntimeError, match="must be explicitly declared"):
        bridge_worker._validate_maestro_variable_update(  # noqa: SLF001
            {"name": "bias_v", "value": "0.35"}
        )

    with pytest.raises(RuntimeError, match="exceeds 128"):
        bridge_worker._validate_maestro_variable_update(  # noqa: SLF001
            {
                "name": "bias_v",
                "scope": "test",
                "scope_name": "x" * 129,
                "expected_value": None,
                "value": "0.35",
            }
        )


def test_maestro_variable_patch_stops_before_write_on_precondition_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    def fake_get_var(_client, name, *, session):
        calls.append(("get", session, name))
        return '"0.35"'

    def unexpected(*args, **kwargs):
        raise AssertionError("precondition mismatch must stop before write/save")

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=lambda *_args: "fnxPatchMismatch",
        close_session=lambda _client, session: calls.append(("close", session)),
        get_var=fake_get_var,
        set_var=unexpected,
        save_setup=unexpected,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="precondition mismatch"):
        bridge_worker.apply_maestro_variables(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {
                    "expected_tests": ["VDA"],
                    "updates": [
                        {
                            "name": "bias_v",
                            "expected_value": "0.30",
                            "value": "0.40",
                        }
                    ],
                },
            }
        )

    assert calls == [
        ("get", "fnxPatchMismatch", "bias_v"),
        ("close", "fnxPatchMismatch"),
    ]


def test_maestro_variable_patch_refuses_any_existing_configured_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            raise AssertionError(expression)

    def unexpected(*args, **kwargs):
        raise AssertionError("existing session must stop before opening another")

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: "fnxManual7",
        open_session=unexpected,
        close_session=unexpected,
        get_var=unexpected,
        set_var=unexpected,
        save_setup=unexpected,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="already open: fnxManual7"):
        bridge_worker.apply_maestro_variables(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {
                    "expected_tests": ["VDA"],
                    "updates": [
                        {
                            "name": "bias_v",
                            "expected_value": None,
                            "value": "0.35",
                        }
                    ],
                },
            }
        )


def test_maestro_variable_patch_rejects_persistent_readback_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    state = {"value": "0.35", "verify": False}

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    sessions = iter(["fnxPatchWrite", "fnxPatchVerify"])

    def fake_open_session(*_args):
        session = next(sessions)
        state["verify"] = session == "fnxPatchVerify"
        return session

    def fake_get_var(_client, _name, *, session):
        value = "0.38" if state["verify"] else state["value"]
        return f'"{value}"'

    def fake_set_var(_client, _name, value, *, session):
        state["value"] = value

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=fake_open_session,
        close_session=lambda _client, session: calls.append(("close", session)),
        get_var=fake_get_var,
        set_var=fake_set_var,
        save_setup=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="persistent readback mismatch"):
        bridge_worker.apply_maestro_variables(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_variables": {
                    "expected_tests": ["VDA"],
                    "updates": [
                        {
                            "name": "bias_v",
                            "expected_value": "0.35",
                            "value": "0.40",
                        }
                    ],
                },
            }
        )

    assert calls == [
        ("close", "fnxPatchWrite"),
        ("close", "fnxPatchVerify"),
    ]


def test_maestro_setup_readback_parser_preserves_skill_values_and_escapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_maestro_module(monkeypatch)

    parsed = bridge_worker._parse_skill_sexpr(  # noqa: SLF001
        r'''(t (("start" "1") ("saveOppoint" t) ("unset" nil)
        ("path" "A\\B") ("unknown" "x\qy")))'''
    )

    assert parsed == [
        True,
        [
            ["start", "1"],
            ["saveOppoint", True],
            ["unset", None],
            ["path", "A\\B"],
            ["unknown", "x\\qy"],
        ],
    ]

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "maeGetEnabledAnalysis" in expression:
                return SimpleNamespace(
                    output='(t (("anaName" "ac") ("start" "1") '
                    '("saveOppoint" t)))',
                    errors=[],
                )
            if "maeGetTestOutputs" in expression:
                assert "axlGetSpecData" in expression
                assert 'sprintf(nil "%L" o~>expression)' in expression
                return SimpleNamespace(
                    output=(
                        '(1 "BW" \'point nil "bandwidth(mag(VF(\\"/OUT\\")) '
                        '3 \\"low\\")" \'point t nil ((\'gt "1G")))'
                    ),
                    errors=[],
                )
            raise AssertionError(expression)

    analysis = bridge_worker._maestro_analysis_state(  # noqa: SLF001
        Client(), "AC", "ac", session="fnxRead1"
    )
    output = bridge_worker._maestro_output_state(  # noqa: SLF001
        Client(), "AC", "BW", session="fnxRead1"
    )

    assert analysis == {
        "enabled": True,
        "options": {"anaName": "ac", "start": "1", "saveOppoint": True},
    }
    assert output == {
        "name": "BW",
        "type": "point",
        "signal_name": None,
        "expression": 'bandwidth(mag(VF("/OUT")) 3 "low")',
        "eval_type": "point",
        "plot": True,
        "save": None,
        "spec": {"relation": "gt", "value": "1G"},
    }


def test_ade_result_mapping_setup_pins_exact_scalar_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expression = (
        'cross(clip(VT("/OUT") 80p 130p) 0.45 1 "falling" nil nil nil) '
        '- cross(clip(VT("/IN") 80p 130p) 0.45 1 "rising" nil nil nil)'
    )
    canonical = (
        '(cross(clip(VT("/OUT") 8e-11 1.3e-10) 0.45 1 "falling" nil '
        'nil nil) - cross(clip(VT("/IN") 8e-11 1.3e-10) 0.45 1 '
        '"rising" nil nil nil))'
    )
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_output_state",
        lambda *_args, **_kwargs: {
            "name": "Delay",
            "type": None,
            "signal_name": None,
            "expression": canonical,
            "eval_type": "point",
            "plot": True,
            "save": True,
            "spec": None,
        },
    )

    result = bridge_worker._read_ade_result_mapping_setup(  # noqa: SLF001
        object(),
        {
            "metrics": [
                {
                    "test": "VDA",
                    "output": "Delay",
                    "metric": "delay_ps",
                    "expected_expression": expression,
                }
            ]
        },
        session="fnxResultMapping",
    )

    assert result["outputs"][0]["state"]["expression"] == canonical
    assert len(result["fingerprint_sha256"]) == 64


def test_ade_result_mapping_setup_rejects_changed_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_output_state",
        lambda *_args, **_kwargs: {
            "name": "Delay",
            "type": None,
            "signal_name": None,
            "expression": 'ymax(VT("/OUT"))',
            "eval_type": "point",
            "plot": True,
            "save": True,
            "spec": None,
        },
    )

    with pytest.raises(RuntimeError, match="did not match"):
        bridge_worker._read_ade_result_mapping_setup(  # noqa: SLF001
            object(),
            {
                "metrics": [
                    {
                        "test": "VDA",
                        "output": "Delay",
                        "metric": "delay_ps",
                        "expected_expression": 'average(VT("/OUT"))',
                    }
                ]
            },
            session="fnxResultMapping",
        )


def test_maestro_setup_patch_saves_once_and_reopens_for_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    before_analysis = {
        "enabled": True,
        "options": {
            "anaName": "ac",
            "start": "1",
            "stop": "1G",
            "dec": "10",
        },
    }
    desired_analysis = {
        "enabled": True,
        "options": {
            "anaName": "ac",
            "start": "1",
            "stop": "10G",
            "dec": "20",
        },
    }
    expression = 'bandwidth(mag(VF("/OUT")) 3 "low")'
    state = {
        "persisted_analyses": {("AC", "ac"): before_analysis},
        "persisted_outputs": {},
        "working_analyses": {},
        "working_outputs": {},
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

    sessions = iter(["fnxSetupWrite", "fnxSetupVerify"])

    def fake_open_session(_client, library, cell):
        session = next(sessions)
        state["working_analyses"] = deepcopy(state["persisted_analyses"])
        state["working_outputs"] = deepcopy(state["persisted_outputs"])
        calls.append(("open", session, library, cell))
        return session

    def fake_analysis_state(_client, test, analysis, *, session):
        calls.append(("read_analysis", session, test, analysis))
        return deepcopy(state["working_analyses"].get((test, analysis)))

    def fake_output_state(_client, test, name, *, session):
        calls.append(("read_output", session, test, name))
        return deepcopy(state["working_outputs"].get((test, name)))

    def fake_set_analysis(
        _client, test, analysis, *, enable, options, session
    ):
        calls.append(("set_analysis", session, test, analysis, enable, options))
        assert options == '(("stop" "10G") ("dec" "20"))'
        state["working_analyses"][(test, analysis)] = deepcopy(desired_analysis)

    def fake_add_output(
        _client,
        name,
        test,
        *,
        output_type,
        signal_name,
        expr,
        session,
    ):
        calls.append(("add_output", session, test, name, output_type))
        assert '\\"/OUT\\"' in expr
        state["working_outputs"][(test, name)] = {
            "name": name,
            "type": output_type,
            "signal_name": signal_name or None,
            "expression": expression,
            "eval_type": output_type,
            "plot": None,
            "save": None,
            "spec": None,
        }

    def fake_set_spec(_client, name, test, *, session, **kwargs):
        calls.append(("set_spec", session, test, name, kwargs))
        relation, value = next(iter(kwargs.items()))
        state["working_outputs"][(test, name)]["spec"] = {
            "relation": relation,
            "value": value,
        }

    def fake_save_setup(_client, library, cell, *, session):
        calls.append(("save", session, library, cell))
        state["persisted_analyses"] = deepcopy(state["working_analyses"])
        state["persisted_outputs"] = deepcopy(state["working_outputs"])

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=fake_open_session,
        close_session=lambda _client, session: calls.append(("close", session)),
        set_analysis=fake_set_analysis,
        add_output=fake_add_output,
        set_spec=fake_set_spec,
        save_setup=fake_save_setup,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(bridge_worker, "_maestro_analysis_state", fake_analysis_state)
    monkeypatch.setattr(bridge_worker, "_maestro_output_state", fake_output_state)

    result = bridge_worker.apply_maestro_setup(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_setup": {
                "backend": "maestro",
                "expected_tests": ["AC"],
                "analyses": [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "expected": before_analysis,
                        "enabled": True,
                        "options": {"stop": "10G", "dec": "20"},
                    }
                ],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "BW",
                        "output_type": "point",
                        "signal_name": None,
                        "expression": expression,
                        "spec": {"relation": "gt", "value": "1G"},
                    }
                ],
            },
        }
    )

    assert result["tests_readback_before"] == ["AC"]
    assert result["tests_readback_after"] == ["AC"]
    assert result["before_analyses"][0]["state"] == before_analysis
    assert result["before_outputs"] == [
        {"test": "AC", "name": "BW", "state": None}
    ]
    assert result["immediate_analyses"] == result["persisted_analyses"]
    assert result["immediate_outputs"] == result["persisted_outputs"]
    assert result["persisted_analyses"][0]["state"] == desired_analysis
    assert result["persisted_outputs"][0]["state"]["spec"] == {
        "relation": "gt",
        "value": "1G",
    }
    assert result["existing_outputs_replaced"] is False
    assert result["existing_maestro_replaced"] is False
    assert result["maestro_setup_write_performed"] is True
    assert result["automated_simulation_performed"] is False
    assert result["before_target_fingerprint_sha256"] != result[
        "after_target_fingerprint_sha256"
    ]
    assert [call[0] for call in calls].count("save") == 1
    assert [call[0] for call in calls].count("open") == 2
    assert [call[0] for call in calls].count("close") == 2
    first_write = next(
        index
        for index, call in enumerate(calls)
        if call[0] in {"set_analysis", "add_output"}
    )
    assert [call[0] for call in calls[:first_write] if call[0].startswith("read_")] == [
        "read_analysis",
        "read_output",
    ]


def test_maestro_setup_patch_stops_before_write_on_any_precondition_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

    def fake_analysis_state(_client, test, analysis, *, session):
        calls.append(("read_analysis", test, analysis))
        return {"enabled": True, "options": {"stop": "2G"}}

    def fake_output_state(_client, test, name, *, session):
        calls.append(("read_output", test, name))
        return None

    def unexpected(*args, **kwargs):
        raise AssertionError("precondition mismatch must stop before write/save")

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=lambda *_args: "fnxSetupMismatch",
        close_session=lambda _client, session: calls.append(("close", session)),
        set_analysis=unexpected,
        add_output=unexpected,
        set_spec=unexpected,
        save_setup=unexpected,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(bridge_worker, "_maestro_analysis_state", fake_analysis_state)
    monkeypatch.setattr(bridge_worker, "_maestro_output_state", fake_output_state)

    with pytest.raises(RuntimeError, match="before any write"):
        bridge_worker.apply_maestro_setup(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_setup": {
                    "expected_tests": ["AC"],
                    "analyses": [
                        {
                            "test": "AC",
                            "analysis": "ac",
                            "expected": {
                                "enabled": True,
                                "options": {"stop": "1G"},
                            },
                            "enabled": True,
                            "options": {"stop": "10G"},
                        }
                    ],
                    "outputs": [
                        {
                            "test": "AC",
                            "name": "Vout",
                            "output_type": "net",
                            "signal_name": "/OUT",
                            "expression": None,
                            "spec": None,
                        }
                    ],
                },
            }
        )

    assert calls == [
        ("read_analysis", "AC", "ac"),
        ("read_output", "AC", "Vout"),
        ("close", "fnxSetupMismatch"),
    ]


def test_maestro_setup_patch_rejects_persistent_readback_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    states = iter(
        [
            None,
            {"enabled": True, "options": {"stop": "10G"}},
            {"enabled": True, "options": {"stop": "1G"}},
        ]
    )

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("AC")', errors=[])
            raise AssertionError(expression)

    _install_fake_maestro_module(
        monkeypatch,
        find_open_session=lambda _client: None,
        open_session=lambda *_args: (
            "fnxSetupWrite" if not calls else "fnxSetupVerify"
        ),
        close_session=lambda _client, session: calls.append(("close", session)),
        set_analysis=lambda *_args, **_kwargs: None,
        add_output=lambda *_args, **_kwargs: None,
        set_spec=lambda *_args, **_kwargs: None,
        save_setup=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bridge_worker, "_client", Client)
    monkeypatch.setattr(
        bridge_worker,
        "_maestro_analysis_state",
        lambda *_args, **_kwargs: next(states),
    )

    with pytest.raises(RuntimeError, match="persistent readback mismatch"):
        bridge_worker.apply_maestro_setup(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_setup": {
                    "expected_tests": ["AC"],
                    "analyses": [
                        {
                            "test": "AC",
                            "analysis": "ac",
                            "expected": None,
                            "enabled": True,
                            "options": {"stop": "10G"},
                        }
                    ],
                    "outputs": [],
                },
            }
        )

    assert calls == [
        ("close", "fnxSetupWrite"),
        ("close", "fnxSetupVerify"),
    ]


def test_focused_maestro_capture_keeps_manual_state_and_real_result_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = {
        "session": "fnxSession7",
        "lib": "vda_test",
        "cell": "vda_manual_tb",
        "view": "maestro",
        "app": "explorer",
        "mode": "Editing",
        "unsaved": False,
        "raw_sections": [["maeGetSetup", '("AC")']],
    }
    calls: list[dict[str, object]] = []

    def fake_snapshot(_client, *, output_root=None, history=None):
        calls.append({"output_root": output_root, "history": history})
        if output_root is None:
            return dict(session)
        output_dir = Path(output_root) / "capture"
        setup = output_dir / "maestro.sdb"
        result = output_dir / "Interactive.7" / "1" / "AC" / "psf" / "ac.ac"
        netlist = (
            output_dir / "Interactive.7" / "1" / "AC" / "netlist" / "input.scs"
        )
        for path, content in (
            (setup, b"saved ADE setup"),
            (result, b"real PSF result"),
            (netlist, b"real Spectre input"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return dict(session) | {
            "output_dir": str(output_dir),
            "latest_history": history or "Interactive.7",
        }

    def fake_read_results(_client, captured_session, **kwargs):
        assert captured_session == "fnxSession7"
        assert kwargs["history"] == "Interactive.7"
        return {
            "history": "Interactive.7",
            "points": [
                {
                    "point": 1,
                    "parameters": {"vdd": "0.9"},
                    "outputs": {"gain": {"value": "3.1", "pass_fail": "pass"}},
                }
            ],
        }

    _install_fake_maestro_module(
        monkeypatch, snapshot=fake_snapshot, read_results=fake_read_results
    )
    monkeypatch.setattr(bridge_worker, "_client", lambda: object())

    captured = bridge_worker.capture_focused_maestro(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_capture": {
                "backend": "maestro",
                "history": "Interactive.7",
                "require_results": True,
                "require_saved_setup": True,
                "require_structured_outputs": True,
            },
            "capture_output_root": str(tmp_path / "capture-root"),
        }
    )

    assert calls == [
        {"output_root": None, "history": None},
        {
            "output_root": str((tmp_path / "capture-root").resolve()),
            "history": "Interactive.7",
        },
    ]
    assert captured["setup_evidence_source"] == "bridge_readback"
    assert captured["structured_results_evidence_source"] == "eda_result"
    assert captured["artifact_counts"]["setup"] == 1
    assert captured["artifact_counts"]["simulator_input"] == 1
    assert captured["artifact_counts"]["eda_result"] == 1
    assert captured["automated_simulation_performed"] is False
    assert captured["oa_write_performed"] is False
    assert captured["setup_fingerprint_sha256"]
    assert captured["simulation_fingerprint_sha256"]


def test_subprocess_ade_prepare_payload_has_no_invented_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "prepare-manual-ade",
            "operation": "ade.prepare",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_prepare": {"test_name": "VDA_AC"},
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"existing_maestro_overwritten": False}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.prepare_ade(task)

    assert result.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert request["action"] == "prepare_maestro"
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert payload["ade_prepare"]["test_name"] == "VDA_AC"
    assert payload["ade_prepare"]["simulator"] == "spectre"
    assert payload["ade_prepare_user_fields"] == ["test_name"]


def test_subprocess_ade_capture_payload_and_artifact_root_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "capture-manual-ade",
            "operation": "ade.capture",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_capture": {
                "history": "Interactive.7",
                "require_structured_outputs": True,
            },
        }
    )
    adapter = SubprocessBridgeAdapter(
        tmp_path / "bridge-python.exe", artifact_root=tmp_path / "captures"
    )
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"structured_results_available": True}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.capture_ade(task)

    assert result.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert request["action"] == "capture_focused_maestro"
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert payload["ade_capture"]["history"] == "Interactive.7"
    assert payload["ade_capture"]["require_structured_outputs"] is True
    assert set(payload["ade_capture_user_fields"]) == {
        "history",
        "require_structured_outputs",
    }
    output_root = Path(payload["capture_output_root"])
    output_root.relative_to(tmp_path / "captures" / task.id)


def test_subprocess_ade_run_payload_has_no_configuration_or_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {"require_structured_outputs": True},
            "safety": {"allow_remote_compute": True},
            "limits": {"timeout_seconds": 321},
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"structured_results_available": True}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.run_ade(task)

    assert result.evidence_source is EvidenceSource.EDA_RESULT
    assert request["action"] == "run_background_maestro"
    assert request["timeout"] == 561
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert "ade_prepare" not in payload
    assert "ade_capture" not in payload
    assert payload["ade_run"]["require_structured_outputs"] is True
    assert payload["ade_run"]["require_artifact_manifest"] is True
    assert payload["ade_run_user_fields"] == ["require_structured_outputs"]


def test_subprocess_ade_run_payload_preserves_native_sweep_expectations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "run-native-cl-sweep",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": _native_sweep_verification(),
            },
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"structured_results_available": True}

    monkeypatch.setattr(adapter, "_request", fake_request)

    adapter.run_ade(task)

    payload = request["payload"]
    assert isinstance(payload, dict)
    assert payload["ade_run"]["sweep_verification"] == _native_sweep_verification()
    assert set(payload["ade_run_user_fields"]) == {
        "require_simulator_input_consistency",
        "sweep_verification",
    }


def test_subprocess_ade_variable_payload_preserves_exact_strings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-variables",
            "operation": "ade.variables.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_variables": {
                "expected_tests": ["VDA"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    }
                ],
            },
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"persisted_variables": {"bias_v": "0.30,0.35,0.40"}}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.apply_ade_variables(task)

    assert result.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert request["action"] == "apply_maestro_variables"
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert payload["ade_variables"]["expected_tests"] == ["VDA"]
    assert payload["ade_variables"]["expected_corners"] is None
    assert payload["ade_variables"]["updates"] == [
        {
            "name": "bias_v",
            "expected_value": None,
            "value": "0.30,0.35,0.40",
            "scope": "global",
            "scope_name": None,
        }
    ]
    assert set(payload["ade_variables_user_fields"]) == {
        "expected_tests",
        "updates",
    }


def test_apply_maestro_corners_is_add_only_and_independently_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corners: list[str] = []
    calls: list[tuple] = []

    class FakeClient:
        def execute_skill(self, expression, **kwargs):
            calls.append(("skill", expression, kwargs))
            if '?typeName "corners"' in expression:
                output = "(" + " ".join(f'\"{name}\"' for name in corners) + ")"
                return SimpleNamespace(output=output, errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    def fake_open_session(_client, library, cell):
        session = f"fnxSession{len([c for c in calls if c[0] == 'open']) + 1}"
        calls.append(("open", library, cell, session))
        return session

    def fake_close_session(_client, session):
        calls.append(("close", session))

    def fake_set_corner(_client, name, **kwargs):
        calls.append(("set_corner", name, kwargs))
        corners.append(name)

    def fake_save_setup(_client, library, cell, **kwargs):
        calls.append(("save", library, cell, kwargs))

    _install_fake_maestro_module(
        monkeypatch,
        open_session=fake_open_session,
        close_session=fake_close_session,
        find_open_session=lambda _client: None,
        save_setup=fake_save_setup,
        set_corner=fake_set_corner,
    )
    monkeypatch.setattr(bridge_worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(bridge_worker, "_cellview_exists", lambda *_args: True)

    result = bridge_worker.apply_maestro_corners(
        {
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_corners": {
                "backend": "maestro",
                "expected_tests": ["VDA"],
                "expected_corners": [],
                "additions": [{"name": "VDA_LOW"}, {"name": "VDA_NOMINAL"}],
            },
        }
    )

    assert result["all_corners_readback_before"] == []
    assert result["enabled_corners_readback_before"] == []
    assert result["all_corners_readback_after"] == ["VDA_LOW", "VDA_NOMINAL"]
    assert result["enabled_corners_readback_after"] == [
        "VDA_LOW",
        "VDA_NOMINAL",
    ]
    assert result["model_files_modified"] is False
    assert result["maestro_setup_write_performed"] is True
    assert [call[1] for call in calls if call[0] == "set_corner"] == [
        "VDA_LOW",
        "VDA_NOMINAL",
    ]
    assert len([call for call in calls if call[0] == "save"]) == 1
    assert len([call for call in calls if call[0] == "open"]) == 2


def test_apply_maestro_corners_rejects_reordered_existing_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corners = ["Nominal", "VDA_LOW"]
    calls: list[tuple] = []

    class FakeClient:
        def execute_skill(self, expression, **kwargs):
            calls.append(("skill", expression, kwargs))
            if '?typeName "corners"' in expression:
                output = "(" + " ".join(f'\"{name}\"' for name in corners) + ")"
                return SimpleNamespace(output=output, errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    def fake_open_session(_client, library, cell):
        calls.append(("open", library, cell))
        return "fnxSession1"

    def fake_close_session(_client, session):
        calls.append(("close", session))

    def unexpected_writer(*_args, **_kwargs):
        raise AssertionError("corner writer must not run after an order mismatch")

    _install_fake_maestro_module(
        monkeypatch,
        open_session=fake_open_session,
        close_session=fake_close_session,
        find_open_session=lambda _client: None,
        save_setup=unexpected_writer,
        set_corner=unexpected_writer,
    )
    monkeypatch.setattr(bridge_worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(bridge_worker, "_cellview_exists", lambda *_args: True)

    with pytest.raises(RuntimeError, match="corner membership precondition mismatch"):
        bridge_worker.apply_maestro_corners(
            {
                "target": {
                    "library": "vda_test",
                    "cell": "vda_manual_tb",
                    "view": "maestro",
                },
                "ade_corners": {
                    "backend": "maestro",
                    "expected_tests": ["VDA"],
                    "expected_corners": ["VDA_LOW", "Nominal"],
                    "additions": [{"name": "VDA_HIGH"}],
                },
            }
        )

    assert not [call for call in calls if call[0] == "set_corner"]
    assert [call for call in calls if call[0] == "close"] == [
        ("close", "fnxSession1")
    ]


def test_subprocess_ade_corner_payload_preserves_exact_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = TaskSpec.model_validate(
        {
            "id": "add-maestro-corners",
            "operation": "ade.corners.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_corners": {
                "expected_tests": ["VDA"],
                "expected_corners": [],
                "additions": [{"name": "VDA_LOW"}, {"name": "VDA_NOMINAL"}],
            },
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"all_corners_readback_after": ["VDA_LOW", "VDA_NOMINAL"]}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.apply_ade_corners(task)

    assert result.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert request["action"] == "apply_maestro_corners"
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert payload["ade_corners"] == {
        "backend": "maestro",
        "expected_tests": ["VDA"],
        "expected_corners": [],
        "additions": [{"name": "VDA_LOW"}, {"name": "VDA_NOMINAL"}],
    }
    assert set(payload["ade_corners_user_fields"]) == {
        "expected_tests",
        "expected_corners",
        "additions",
    }


def test_subprocess_ade_setup_payload_preserves_cas_and_output_expression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expression = 'bandwidth(mag(VF("/OUT")) 3 "low")'
    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-setup",
            "operation": "ade.setup.apply",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_setup": {
                "expected_tests": ["AC"],
                "analyses": [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "expected": None,
                        "enabled": True,
                        "options": {"start": "1", "stop": "10G"},
                    }
                ],
                "outputs": [
                    {
                        "test": "AC",
                        "name": "BW",
                        "output_type": "point",
                        "expression": expression,
                        "spec": {"relation": "gt", "value": "1G"},
                    }
                ],
            },
        }
    )
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    request: dict[str, object] = {}

    def fake_request(action, payload, *, timeout):
        request.update(action=action, payload=payload, timeout=timeout)
        return {"persisted_outputs": []}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.apply_ade_setup(task)

    assert result.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert request["action"] == "apply_maestro_setup"
    assert request["timeout"] == 240
    payload = request["payload"]
    assert isinstance(payload, dict)
    assert "analysis" not in payload
    assert "analysis_source" not in payload
    assert payload["ade_setup"]["expected_tests"] == ["AC"]
    assert payload["ade_setup"]["analyses"][0]["expected"] is None
    assert payload["ade_setup"]["analyses"][0]["options"] == {
        "start": "1",
        "stop": "10G",
    }
    assert payload["ade_setup"]["outputs"][0]["expression"] == expression
    assert payload["ade_setup"]["outputs"][0]["spec"] == {
        "relation": "gt",
        "value": "1G",
    }
    assert set(payload["ade_setup_user_fields"]) == {
        "expected_tests",
        "analyses",
        "outputs",
    }


def test_inverter_testbench_deck_includes_oa_netlist_without_device_topology() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _inverter_testbench_deck(
        profile,
        {
            "nmos_width_um": 0.5,
            "pmos_width_um": 1.0,
            "length_um": 0.03,
            "load_ff": 2.0,
            "vdd_v": 0.9,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_run/netlist",
    )
    assert 'include "/data/xum/virtuoso_bridge_smoke/vda_run/netlist"' in deck
    assert "VIN_SRC (IN 0)" in deck
    assert "VSS_SRC (VSS 0)" in deck
    assert "save IN OUT VDD VSS VDD_SRC:p" in deck
    assert "MN0 (" not in deck
    assert "MP0 (" not in deck


def test_common_source_deck_uses_oa_topology_and_requests_dc_op() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.45,
            "vdd_v": 0.9,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs/netlist",
    )
    assert 'include "/data/xum/virtuoso_bridge_smoke/vda_cs/netlist"' in deck
    assert "VIN_SRC (IN 0) vsource dc=vbias" in deck
    assert "dcOp dc" in deck
    assert "dcOpInfo info what=oppoint where=rawfile" in deck
    assert "save MN0:ids MN0:vgs MN0:vds MN0:vdsat MN0:gm MN0:gds" in deck
    assert "MN0 (" not in deck
    assert "RD0 (" not in deck


def test_common_source_ac_deck_reuses_oa_topology_and_adds_only_testbench() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.45,
            "vdd_v": 0.9,
            "load_ff": 2.0,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs_ac/netlist",
        analysis="ac",
        ac_sweep={
            "start_hz": 1e3,
            "stop_hz": 1e11,
            "points_per_decade": 20,
        },
    )

    assert "VIN_SRC (IN 0) vsource dc=vbias mag=1 type=dc" in deck
    assert "CL0 (OUT 0) capacitor c=2f" in deck
    assert "dcOp dc" in deck
    assert "ac ac start=1000 stop=100000000000 dec=20" in deck
    assert "save IN OUT VDD VSS" in deck
    assert "MN0 (" not in deck
    assert "RD0 (" not in deck


def test_common_source_pvt_deck_uses_profile_mapped_process_and_temperature() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump(mode="json")
    condition = {
        "name": "ss_125c_0p81v",
        "process_corner": "ss",
        "temperature_c": 125.0,
        "vdd_v": 0.81,
    }
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.81,
            "load_ff": 1.0,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs_pvt/netlist",
        analysis="ac",
        ac_sweep={"start_hz": 1e4, "stop_hz": 1e11},
        operating_condition=condition,
    )
    manifest = _common_source_model_manifest(profile, condition)

    assert 'include "' + profile["model_include"] + '" section=top_tt' not in deck
    for section in (
        "ssmacro_mos_moscap",
        "ss_res_bip_dio_disres",
        "ss_mom",
        "ss_r_metal",
    ):
        assert f"section={section}" in deck
    assert "simulatorOptions options temp=125" in deck
    assert "parameters vdd=0.81" in deck
    assert manifest == {
        "source": "software_inference",
        "profile": "nics4304_tsmc28",
        "profile_source": "pdk_profile",
        "process_corner": "ss",
        "process_corner_source": "user_input",
        "temperature_c": 125.0,
        "temperature_source": "user_input",
        "includes": profile["process_corners"]["ss"],
    }


def test_common_source_pvt_bundle_keeps_per_condition_results_and_one_netlist() -> None:
    conditions = [
        {
            "name": "tt_25c_0p90v",
            "process_corner": "tt",
            "temperature_c": 25.0,
            "vdd_v": 0.9,
        },
        {
            "name": "ss_125c_0p81v",
            "process_corner": "ss",
            "temperature_c": 125.0,
            "vdd_v": 0.81,
        },
    ]
    schematic = {"source": "bridge_readback", "semantic_parameters": {"length_um": 0.03}}
    netlist = {"source": "eda_result", "remote_path": "/data/xum/vda/netlist", "sha256": "a" * 64}

    def result(vdd_v: float, gain: float) -> dict:
        return {
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": vdd_v,
            },
            "metrics": {"low_frequency_gain_v_per_v": gain},
            "metric_sources": {"low_frequency_gain_v_per_v": "eda_result"},
            "analysis_complete": True,
            "analysis_issues": [],
            "analysis_warnings": [],
            "evidence": {
                "schematic_readback": schematic,
                "netlist": netlist,
            },
        }

    merged = _merge_common_source_operating_condition_results(
        {
            "parameters": {"bias_v": 0.35},
            "operating_conditions": conditions,
            "operating_conditions_source": "user_input",
        },
        [
            (conditions[0], result(0.9, 3.0)),
            (conditions[1], result(0.81, 2.0)),
        ],
    )

    assert merged["analysis_complete"] is True
    assert "vdd_v" not in merged["parameters"]
    assert merged["parameters"]["length_um"] == pytest.approx(0.03)
    assert [
        row["condition"]["name"]
        for row in merged["operating_condition_results"]
    ] == ["tt_25c_0p90v", "ss_125c_0p81v"]
    bundle = merged["evidence"]["operating_condition_bundle"]
    assert bundle["oa_netlist_reuse"] == "one_verified_netlist"
    assert bundle["requested_conditions_source"] == "user_input"

    drifted = result(0.81, 2.0)
    drifted["evidence"] = dict(drifted["evidence"])
    drifted["evidence"]["netlist"] = netlist | {"sha256": "b" * 64}
    with pytest.raises(RuntimeError, match="identical OA/netlist evidence"):
        _merge_common_source_operating_condition_results(
            {"parameters": {}, "operating_conditions": conditions},
            [(conditions[0], result(0.9, 3.0)), (conditions[1], drifted)],
        )


def test_common_source_pvt_orchestration_overrides_vdd_and_shares_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conditions = [
        {
            "name": "tt_25c_0p90v",
            "process_corner": "tt",
            "temperature_c": 25.0,
            "vdd_v": 0.9,
        },
        {
            "name": "ff_m40c_0p99v",
            "process_corner": "ff",
            "temperature_c": -40.0,
            "vdd_v": 0.99,
        },
    ]
    calls: list[tuple[dict, int]] = []
    shared_schematic = {"source": "bridge_readback"}
    shared_netlist = {"source": "eda_result", "sha256": "a" * 64}

    def fake_simulate(payload, *, _bundle_cache=None):
        assert _bundle_cache is not None
        calls.append((deepcopy(payload), id(_bundle_cache)))
        return {
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": payload["parameters"]["vdd_v"],
            },
            "metrics": {"gain": 2.0},
            "metric_sources": {"gain": "eda_result"},
            "analysis_complete": True,
            "analysis_issues": [],
            "analysis_warnings": [],
            "evidence": {
                "schematic_readback": shared_schematic,
                "netlist": shared_netlist,
            },
        }

    monkeypatch.setattr(bridge_worker, "simulate_common_source", fake_simulate)
    merged = _simulate_common_source_operating_conditions(
        {
            "parameters": {"bias_v": 0.35},
            "operating_conditions": conditions,
            "operating_conditions_source": "user_input",
        }
    )

    assert [call[0]["parameters"]["vdd_v"] for call in calls] == [0.9, 0.99]
    assert len({call[1] for call in calls}) == 1
    assert all("operating_conditions" not in call[0] for call in calls)
    assert [
        call[0]["operating_condition"]["name"] for call in calls
    ] == ["tt_25c_0p90v", "ff_m40c_0p99v"]
    assert len(merged["operating_condition_results"]) == 2


def test_common_source_linearity_deck_uses_one_nested_transient_sweep() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs_linearity/netlist",
        analysis="transient",
        linearity_sweep={
            "frequency_hz": 100e6,
            "amplitudes_v": [0.005, 0.02, 0.05],
            "settling_cycles": 4,
            "measurement_cycles": 8,
            "points_per_cycle": 128,
        },
    )

    assert "type=sine sinedc=vbias ampl=vinamp freq=flinearity" in deck
    assert "sw1 sweep param=vinamp values=[0.005 0.02 0.05]" in deck
    assert "tran tran stop=1.20078125e-07" in deck
    assert "strobeoutput=all" in deck
    assert "CL0 (OUT 0) capacitor c=1f" in deck
    assert "MN0 (" not in deck
    assert "RD0 (" not in deck


def test_common_source_noise_deck_uses_vin_source_as_input_probe() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs_noise/netlist",
        analysis="noise",
        noise_sweep={
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "points_per_decade": 20,
        },
    )

    assert "VIN_SRC (IN 0) vsource dc=vbias mag=1 type=dc" in deck
    assert "noise (OUT 0) noise start=1000 stop=10000000000 dec=20" in deck
    assert "iprobe=VIN_SRC" in deck
    assert "CL0 (OUT 0) capacitor c=1f" in deck
    assert "MN0 (" not in deck


def test_common_source_ac_result_uses_complex_vout_over_vin() -> None:
    frequency_hz = [10.0 ** (2.0 + index / 10.0) for index in range(61)]
    vin = [0.5 + 0.5j for _ in frequency_hz]
    transfer = [-10.0 / (1.0 + 1j * frequency / 1e6) for frequency in frequency_hz]
    vout = [input_value * gain for input_value, gain in zip(vin, transfer)]

    metrics, diagnostics = _common_source_ac_metrics_from_result(
        {"ac_freq": frequency_hz, "ac_IN": vin, "ac_OUT": vout},
        {"reference_points": 5, "max_reference_variation_db": 0.5},
    )

    assert metrics["low_frequency_gain_v_per_v"] == pytest.approx(10.0, rel=1e-5)
    assert metrics["bandwidth_3db_hz"] == pytest.approx(1e6, rel=0.01)
    assert metrics["gain_bandwidth_product_hz"] == pytest.approx(1e7, rel=0.01)
    assert diagnostics["analysis_complete"] is True
    assert diagnostics["transfer"] == "VOUT/VIN complex ratio"

    with pytest.raises(RuntimeError, match="complex signal ac_OUT is empty"):
        _complex_signal({"ac_OUT": []}, "ac_OUT")


def test_common_source_linearity_sweep_maps_declared_amplitudes_to_psf_points() -> None:
    frequency_hz = 1e6
    points_per_cycle = 64
    amplitudes = [0.005, 0.02, 0.05]
    gains = [4.0, 3.9, 3.4]
    time_s = [
        index / (frequency_hz * points_per_cycle)
        for index in range(3 * points_per_cycle + 1)
    ]
    sweep_points = {}
    for index, (amplitude, gain) in enumerate(zip(amplitudes, gains), start=1):
        sweep_points[index] = {
            "time": time_s,
            "IN": [
                0.35 + amplitude * math.sin(2.0 * math.pi * frequency_hz * time)
                for time in time_s
            ],
            "OUT": [
                0.5 - amplitude * gain * math.sin(2.0 * math.pi * frequency_hz * time)
                for time in time_s
            ],
            "VDD_SRC:p": [-20e-6] * len(time_s),
        }

    metrics, diagnostics = _common_source_linearity_metrics_from_result(
        {"sweep_points": sweep_points},
        {
            "frequency_hz": frequency_hz,
            "amplitudes_v": amplitudes,
            "settling_cycles": 1,
            "measurement_cycles": 2,
            "points_per_cycle": points_per_cycle,
            "max_harmonic": 5,
            "compression_db": 1.0,
        },
        vdd_v=0.9,
    )

    assert metrics["small_signal_gain_v_per_v"] == pytest.approx(4.0, rel=1e-3)
    assert 0.02 < metrics["input_1db_compression_v_peak"] < 0.05
    assert diagnostics["sweep_point_count"] == 3
    assert diagnostics["point_details"][0]["signals"] == [
        "time",
        "IN",
        "OUT",
        "VDD_SRC:p",
    ]


def test_common_source_noise_reads_the_downloaded_bridge_psf(
    tmp_path, monkeypatch
) -> None:
    import sys
    from types import ModuleType

    output_dir = tmp_path / "common_source_from_oa.raw"
    output_dir.mkdir()
    noise_file = output_dir / "noise.noise"
    noise_file.write_text("test noise psf", encoding="utf-8")
    parser_module = ModuleType("virtuoso_bridge.spectre.parsers")
    parser_module.parse_spectre_psf_ascii = lambda path: SimpleNamespace(
        data={
            "freq": [1e3, 1.001e6],
            "out": [10e-9, 10e-9],
            "in": [2e-9, 2e-9],
        }
    )
    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge.spectre.parsers", parser_module
    )

    metrics, diagnostics = _common_source_noise_metrics_from_result(
        SimpleNamespace(metadata={"output_dir": str(output_dir)}),
        {"start_hz": 1e3, "stop_hz": 1.001e6},
    )

    assert metrics["integrated_output_noise_uv_rms"] == pytest.approx(10.0)
    assert metrics["integrated_input_referred_noise_uv_rms"] == pytest.approx(2.0)
    assert diagnostics["signals"] == ["freq", "out", "in"]
    assert diagnostics["psf_sha256"] == hashlib.sha256(
        noise_file.read_bytes()
    ).hexdigest()


def test_common_source_dc_reads_root_psf_instead_of_sweep_point(
    tmp_path, monkeypatch
) -> None:
    import sys
    from types import ModuleType

    output_dir = tmp_path / "common_source_from_oa.raw"
    sweep_dir = output_dir / "sw1.sweep1" / "1"
    sweep_dir.mkdir(parents=True)
    root_dc = output_dir / "dcOp.dc"
    root_op = output_dir / "dcOpInfo.info"
    sweep_dc = sweep_dir / "dcOp.dc"
    sweep_op = sweep_dir / "dcOpInfo.info"
    root_dc.write_text("root-dc", encoding="utf-8")
    root_op.write_text("root-op", encoding="utf-8")
    sweep_dc.write_text("sweep-dc", encoding="utf-8")
    sweep_op.write_text("sweep-op", encoding="utf-8")

    source_v = 0.02481892891792441
    node_vds = 0.37880220789412217
    root_dc_data = {
        "IN": 0.35,
        "OUT": source_v + node_vds,
        "VDD": 0.9,
        "VSS": 0.0,
        "NSRC": source_v,
        "VDD_SRC:p": -24.81960477308248e-6,
    }
    root_op_data = {
        "MN0:ids": 24.81960477308248e-6,
        "MN0:vgs": 0.35 - source_v,
        "MN0:vds": node_vds,
        "MN0:vdsat": 0.09612096152765043,
        "MN0:gm": 421.88045498791746e-6,
        "MN0:gds": 39.635511355790435e-6,
    }
    parsed_paths = []
    data_by_marker = {
        "root-dc": root_dc_data,
        "root-op": root_op_data,
        "sweep-dc": root_dc_data | {"OUT": 0.4},
        "sweep-op": root_op_data | {"MN0:vds": 0.379024248172},
    }

    def parse_psf(path):
        parsed_paths.append(path)
        return SimpleNamespace(
            data=data_by_marker[path.read_text(encoding="utf-8")]
        )

    parser_module = ModuleType("virtuoso_bridge.spectre.parsers")
    parser_module.parse_spectre_psf_ascii = parse_psf
    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge.spectre.parsers", parser_module
    )

    dc_data, diagnostics = _common_source_dc_data_from_result(
        SimpleNamespace(metadata={"output_dir": str(output_dir)})
    )
    metrics, evidence = _common_source_metrics_from_result(
        dc_data,
        {
            "vdd_v": 0.9,
            "bias_v": 0.35,
            "load_resistance_ohm": 20_000.0,
            "source_resistance_ohm": 1_000.0,
        },
    )

    assert parsed_paths == [root_dc, root_op]
    assert metrics["vds_v"] == pytest.approx(node_vds)
    assert evidence["node_device_consistency"] == "matched"
    assert diagnostics["dc"]["relative_path"] == "dcOp.dc"
    assert diagnostics["operating_point"]["relative_path"] == "dcOpInfo.info"


def test_si_env_completion_adds_verified_spectre_formatter_context_once() -> None:
    completed = _complete_si_env(
        'simLibName = "vb_pdk_smoke"\n'
        'simViewList = \'("schematic")\n'
    )
    assert completed.count("simViewList =") == 1
    assert 'simViewList = \'("spectre" "config" "schematic" "veriloga")' in completed
    assert "nlFormatterClass = 'spectreFormatter" in completed
    assert "simNotIncremental = 't" in completed


def test_oa_netlist_parameters_are_parsed_and_checked() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    parsed = _parse_inverter_netlist(
        """
MN0 (OUT IN VSS VSS) nch_lvt_mac l=30n w=500n nf=1 multi=1
MP0 (OUT IN VDD VDD) pch_lvt_mac l=30n w=1u nf=1 multi=1
""",
        profile,
    )
    assert parsed["semantic_parameters"] == {
        "nmos_width_um": pytest.approx(0.5),
        "pmos_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
    }
    assert parsed["instances"]["MN0"]["nodes"] == ["OUT", "IN", "VSS", "VSS"]
    _assert_parameter_consistency(
        {
            "nmos_width_um": 0.5,
            "pmos_width_um": 1.0,
            "length_um": 0.03,
        },
        parsed["semantic_parameters"],
        expected_label="OA readback",
        actual_label="si netlist",
    )


def test_common_source_oa_netlist_parameters_and_topology_are_parsed() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    parsed = _parse_common_source_netlist(
        """
MN0 (OUT IN VSS VSS) nch_lvt_mac l=30n w=1u nf=1 multi=1
RD0 (VDD OUT) resistor r=20k
""",
        profile,
    )
    assert parsed["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(20_000.0),
    }
    assert parsed["instances"]["RD0"]["nodes"] == ["VDD", "OUT"]
    assert parsed["device_geometry"] == {
        "finger_width_um": pytest.approx(1.0),
        "fingers": pytest.approx(1.0),
        "multiplicity": pytest.approx(1.0),
        "total_width_um": pytest.approx(1.0),
    }
    assert parsed["topology_variant"] == "common_source"


def test_source_degenerated_oa_netlist_is_parsed_without_a_second_pipeline() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    parsed = _parse_common_source_netlist(
        """
MN0 (OUT IN NSRC VSS) nch_lvt_mac l=30n w=2u nf=2 multi=1
RD0 (VDD OUT) resistor r=22k
RS0 (NSRC VSS) resistor r=1k
""",
        profile,
    )
    assert parsed["topology_variant"] == "source_degenerated_common_source"
    assert parsed["instances"]["MN0"]["nodes"] == ["OUT", "IN", "NSRC", "VSS"]
    assert parsed["instances"]["RS0"]["nodes"] == ["NSRC", "VSS"]
    assert parsed["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(22_000.0),
        "source_resistance_ohm": pytest.approx(1_000.0),
    }
    assert parsed["device_geometry"] == {
        "finger_width_um": pytest.approx(1.0),
        "fingers": pytest.approx(2.0),
        "multiplicity": pytest.approx(1.0),
        "total_width_um": pytest.approx(2.0),
    }


def test_multifinger_oa_and_si_geometry_use_the_same_width_semantics() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    oa_geometry = _common_source_device_geometry_from_schematic(
        _common_source_readback(degenerated=True)
    )
    parsed = _parse_common_source_netlist(
        """
MN0 (OUT IN NSRC VSS) nch_lvt_mac l=30n w=2u nf=2 multi=1
RD0 (VDD OUT) resistor r=22k
RS0 (NSRC VSS) resistor r=1k
""",
        profile,
    )

    assert oa_geometry == parsed["device_geometry"]
    _assert_parameter_consistency(
        oa_geometry,
        parsed["device_geometry"],
        expected_label="OA device geometry",
        actual_label="si netlist geometry",
    )


def test_source_degenerated_dc_uses_nsrc_and_checks_both_resistors() -> None:
    metrics, evidence = _common_source_metrics_from_result(
        {
            "dc_IN": 0.45,
            "dc_OUT": 0.5,
            "dc_VDD": 0.9,
            "dc_VSS": 0.0,
            "dc_NSRC": 0.02,
            "dc_VDD_SRC:p": -20e-6,
            "dcOpInfo_MN0:ids": 20e-6,
            "dcOpInfo_MN0:vgs": 0.43,
            "dcOpInfo_MN0:vds": 0.48,
            "dcOpInfo_MN0:vdsat": 0.12,
            "dcOpInfo_MN0:gm": 200e-6,
            "dcOpInfo_MN0:gds": 10e-6,
        },
        {
            "vdd_v": 0.9,
            "bias_v": 0.45,
            "load_resistance_ohm": 20_000.0,
            "source_resistance_ohm": 1_000.0,
        },
    )
    assert metrics["vgs_v"] == pytest.approx(0.43)
    assert metrics["vds_v"] == pytest.approx(0.48)
    assert metrics["source_voltage_v"] == pytest.approx(0.02)
    assert metrics["source_resistor_current_ua"] == pytest.approx(20.0)
    assert metrics["source_current_mismatch_percent"] == pytest.approx(0.0)
    assert evidence["source_degeneration_consistency"] == "matched"

    with pytest.raises(RuntimeError, match="missing scalar dc_NSRC"):
        _common_source_metrics_from_result(
            {
                "dc_IN": 0.45,
                "dc_OUT": 0.5,
                "dc_VDD": 0.9,
                "dc_VSS": 0.0,
            },
            {
                "vdd_v": 0.9,
                "bias_v": 0.45,
                "load_resistance_ohm": 20_000.0,
                "source_resistance_ohm": 1_000.0,
            },
        )


def test_source_degenerated_deck_saves_internal_source_node() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _common_source_testbench_deck(
        profile,
        {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "source_resistance_ohm": 1_000.0,
            "bias_v": 0.45,
            "vdd_v": 0.9,
        },
        "/data/xum/virtuoso_bridge_smoke/vda_cs_deg/netlist",
    )
    assert "save IN OUT VDD VSS NSRC" in deck


def _common_source_readback(*, degenerated: bool) -> dict:
    instances = [
        {
            "name": "MN0",
            "lib": "tsmcN28",
            "cell": "nch_lvt_mac",
            "params": {"Wfg": "1u", "l": "30n", "fingers": "2", "m": "1"},
            "terms": {
                "D": "OUT",
                "G": "IN",
                "S": "NSRC" if degenerated else "VSS",
                "B": "VSS",
            },
            "xy": [0.0, 0.0],
            "orient": "R0",
        },
        {
            "name": "RD0",
            "lib": "analogLib",
            "cell": "res",
            "params": {"r": "22k"},
            "terms": {"PLUS": "VDD", "MINUS": "OUT"},
            "xy": [0.0, 1.3],
            "orient": "R0",
        },
    ]
    if degenerated:
        instances.append(
            {
                "name": "RS0",
                "lib": "analogLib",
                "cell": "res",
                "params": {"r": "1k"},
                "terms": {"PLUS": "NSRC", "MINUS": "VSS"},
                "xy": [0.0, -1.3],
                "orient": "R0",
            }
        )
    nets = {name: {} for name in ("IN", "OUT", "VDD", "VSS")}
    if degenerated:
        nets["NSRC"] = {}
    return {
        "instances": instances,
        "nets": nets,
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }


def test_source_degeneration_delta_preserves_existing_oa_objects() -> None:
    before = _common_source_readback(degenerated=False)
    after = _common_source_readback(degenerated=True)
    assert _assert_common_source(before) == "common_source"
    assert _assert_common_source(after) == "source_degenerated_common_source"
    _assert_common_source_transform_preserved(before, after, 1_000.0)

    after["instances"][0]["params"]["fingers"] = "1"
    with pytest.raises(RuntimeError, match="changed MN0 beyond its S net"):
        _assert_common_source_transform_preserved(before, after, 1_000.0)


def test_source_degeneration_removal_preserves_existing_oa_objects() -> None:
    before = _common_source_readback(degenerated=True)
    after = _common_source_readback(degenerated=False)

    _assert_common_source_removal_preserved(before, after)

    after["instances"][1]["params"]["r"] = "99k"
    with pytest.raises(RuntimeError, match="changed RD0"):
        _assert_common_source_removal_preserved(before, after)


def _inverter_core_readback() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "lib": "tsmcN28",
                "cell": "nch_lvt_mac",
                "params": {"Wfg": "500n", "l": "30n", "w": "500n"},
                "terms": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
                "xy": [0.0, 0.0],
                "orient": "R0",
            },
            {
                "name": "MP0",
                "lib": "tsmcN28",
                "cell": "pch_lvt_mac",
                "params": {"Wfg": "1u", "l": "30n", "w": "1u"},
                "terms": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
                "xy": [0.0, 1.0],
                "orient": "R0",
            },
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }


def test_inverter_testbench_delta_preserves_the_core_and_binds_ground() -> None:
    before = _inverter_core_readback()
    after = deepcopy(before)
    after["instances"][0]["terms"].update({"S": "gnd!", "B": "gnd!"})
    after["instances"].extend(
        [
            {
                "name": "VDD0",
                "lib": "analogLib",
                "cell": "vdc",
                "params": {"vdc": "900m", "srcType": "dc"},
                "terms": {"PLUS": "VDD", "MINUS": "gnd!"},
            },
            {
                "name": "VIN0",
                "lib": "analogLib",
                "cell": "vpulse",
                "params": {
                    "v1": "0",
                    "v2": "900m",
                    "per": "100p",
                    "td": "0",
                    "tr": "5p",
                    "tf": "5p",
                    "pw": "50p",
                    "srcType": "pulse",
                },
                "terms": {"PLUS": "IN", "MINUS": "gnd!"},
            },
            {
                "name": "CL0",
                "lib": "analogLib",
                "cell": "cap",
                "params": {"c": "2f"},
                "terms": {"PLUS": "OUT", "MINUS": "gnd!"},
            },
            {
                "name": "GND0",
                "lib": "analogLib",
                "cell": "gnd",
                "params": {},
                "terms": {"gnd!": "gnd!"},
            },
        ]
    )
    after["nets"]["gnd!"] = {}

    _assert_inverter_testbench_transform_preserved(before, after, 0.9, 2.0)

    after["instances"][0]["params"]["Wfg"] = "9u"
    with pytest.raises(RuntimeError, match="changed MN0 beyond grounding"):
        _assert_inverter_testbench_transform_preserved(before, after, 0.9, 2.0)


def test_inverter_testbench_ground_label_edit_is_terminal_scoped() -> None:
    operation = _rename_inverter_ground_labels_operation()

    assert operation.count('x~>theLabel == "VSS"') == 2
    assert 'rbTermName = "S"' in operation
    assert 'rbTermName = "B"' in operation
    assert operation.count("length(rbLabels) == 1") == 2
    assert operation.count('rbLabel~>theLabel = "gnd!"') == 2


def test_source_label_edit_is_strict_and_parameter_updates_are_partial() -> None:
    operation = _rename_mn0_source_label_operation()
    assert 'x~>theLabel == "VSS"' in operation
    assert "dx * dx + dy * dy <= 0.02" in operation
    assert "length(rbLabels) == 1" in operation
    assert 'rbLabel~>theLabel = "NSRC"' in operation

    restore = _restore_mn0_source_label_operation()
    assert 'x~>theLabel == "NSRC"' in restore
    assert 'rbLabel~>theLabel = "VSS"' in restore
    assert "length(rbLabels) == 1" in restore

    deletion = _delete_source_degeneration_operation()
    assert deletion.count("dbDeleteObject(rbLabel)") == 2
    assert deletion.count("dbDeleteObject(rbWire)") == 2
    assert 'x~>objType == "line"' in deletion
    assert "length(rbWires) == 1" in deletion
    assert "dbDeleteObject(rbInst)" in deletion

    updates = _common_source_instance_parameter_updates(
        {"source_resistance_ohm": 1_000.0}
    )
    assert updates == {"RS0": {"r": "1000"}}
    mos_updates = _common_source_instance_parameter_updates(
        {"device_width_um": 0.5}
    )
    assert mos_updates == {"MN0": {"wf": "0.5u"}}
    assert "nf" not in mos_updates["MN0"]
    assert "m" not in mos_updates["MN0"]


def test_existing_schematic_transform_is_forced_to_append_mode() -> None:
    captured = {}

    class Schematic:
        def edit(self, library, cell, **kwargs):
            captured.update({"library": library, "cell": cell, **kwargs})
            return object()

    client = SimpleNamespace(schematic=Schematic())
    editor = _edit_existing_schematic(
        client, "vda_test", "vda_existing", timeout=123
    )

    assert editor is not None
    assert captured == {
        "library": "vda_test",
        "cell": "vda_existing",
        "mode": "a",
        "timeout": 123,
    }


def test_transform_preflight_rejects_unsaved_target_edits(monkeypatch) -> None:
    import sys
    from types import ModuleType

    ops_module = ModuleType("virtuoso_bridge.virtuoso.ops")
    ops_module.escape_skill_string = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.ops", ops_module)
    captured = {}

    class Client:
        def execute_skill(self, skill, timeout):
            captured.update({"skill": skill, "timeout": timeout})
            return SimpleNamespace(output="t", errors=[])

    _preflight_mn0_source_label(Client(), "vda_test", "vda_existing")

    assert '"schematic" "schematic" "r"' in captured["skill"]
    assert 'cv~>modified error("target schematic has unsaved changes")' in captured[
        "skill"
    ]
    assert captured["timeout"] == 60


def test_removal_preflight_is_read_only_and_checks_owned_stubs(monkeypatch) -> None:
    import sys
    from types import ModuleType

    ops_module = ModuleType("virtuoso_bridge.virtuoso.ops")
    ops_module.escape_skill_string = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.ops", ops_module)
    captured = {}

    class Client:
        def execute_skill(self, skill, timeout):
            captured.update({"skill": skill, "timeout": timeout})
            return SimpleNamespace(output="t", errors=[])

    _preflight_source_degeneration_removal(
        Client(), "vda_test", "vda_existing"
    )

    assert '"schematic" "schematic" "r"' in captured["skill"]
    assert "target schematic has unsaved changes" in captured["skill"]
    assert 'x~>theLabel == "NSRC"' in captured["skill"]
    assert captured["skill"].count("length(rbWires) == 1") == 2
    assert captured["timeout"] == 60


def test_bridge_payload_preserves_explicit_removal_action() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "remove-source-degeneration",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "schematic_transform": {"action": "remove_source_degeneration"},
        }
    )

    payload = SubprocessBridgeAdapter._task_payload(task)

    assert payload["schematic_transform"] == {
        "action": "remove_source_degeneration"
    }
    assert payload["parameters"] == {}


def test_bridge_payload_preserves_raw_instance_search_dimensions() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "raw-instance-search",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9},
            "instance_parameter_space": [
                {
                    "instance": "MN0",
                    "parameter": "fingers",
                    "values": ["1", "2"],
                }
            ],
            "constraints": [
                {"metric": "drain_current_ua", "relation": ">=", "value": 1.0}
            ],
        }
    )

    payload = SubprocessBridgeAdapter._task_payload(task)

    assert payload["instance_parameter_space"] == [
        {
            "instance": "MN0",
            "parameter": "fingers",
            "values": ["1", "2"],
        }
    ]


def test_placement_snapshot_is_order_independent_and_shape_sensitive() -> None:
    first = {
        "instances": [{"name": "RD0"}, {"name": "MN0"}],
        "pins": [{"name": "OUT"}, {"name": "IN"}],
        "labels": [
            {"text": "OUT", "xy": "(1 0)"},
            {"text": "IN", "xy": "(-1 0)"},
        ],
        "wires": ["((1 0) (2 0))", "((-1 0) (0 0))"],
    }
    reordered = {
        field: list(reversed(values)) for field, values in first.items()
    }

    baseline = _placement_snapshot_from_readback(first)
    assert _placement_snapshot_from_readback(reordered) == baseline
    reordered["wires"].append("((0 -1) (0 -2))")
    changed = _placement_snapshot_from_readback(reordered)
    assert changed["sha256"] != baseline["sha256"]
    assert changed["counts"]["wires"] == baseline["counts"]["wires"] + 1


def test_failed_transform_cleanup_purges_only_unsaved_target_view(monkeypatch) -> None:
    import sys
    from types import ModuleType

    ops_module = ModuleType("virtuoso_bridge.virtuoso.ops")
    ops_module.escape_skill_string = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.ops", ops_module)
    captured = {}

    class Client:
        def execute_skill(self, skill, timeout):
            captured.update({"skill": skill, "timeout": timeout})
            return SimpleNamespace(output="t", errors=[])

    _discard_failed_existing_schematic_edit(
        Client(), "vda_test", "vda_existing"
    )

    assert "when(rbCv~>modified" in captured["skill"]
    assert "dbPurge(rbCv)" in captured["skill"]
    assert "dbSave" not in captured["skill"]
    assert captured["timeout"] == 60


def test_common_source_operating_point_requires_consistent_eda_scalars() -> None:
    metrics, evidence = _common_source_metrics_from_result(
        {
            "dc_IN": 0.45,
            "dc_OUT": 0.5,
            "dc_VDD": 0.9,
            "dc_VSS": 0.0,
            "dc_VDD_SRC:p": -20e-6,
            "dcOpInfo_MN0:ids": 20e-6,
            "dcOpInfo_MN0:vgs": 0.45,
            "dcOpInfo_MN0:vds": 0.5,
            "dcOpInfo_MN0:vdsat": 0.12,
            "dcOpInfo_MN0:gm": 200e-6,
            "dcOpInfo_MN0:gds": 10e-6,
        },
        {
            "vdd_v": 0.9,
            "bias_v": 0.45,
            "load_resistance_ohm": 20_000.0,
        },
    )
    assert metrics["drain_current_ua"] == pytest.approx(20.0)
    assert metrics["dc_supply_power_uw"] == pytest.approx(18.0)
    assert metrics["supply_current_mismatch_percent"] == pytest.approx(0.0)
    assert evidence["operating_region"] == "saturation"
    assert evidence["node_device_consistency"] == "matched"
    assert evidence["kcl_consistency"] == "matched"

    with pytest.raises(RuntimeError, match="missing operating-point scalar.*vdsat"):
        _common_source_metrics_from_result(
            {
                "dc_IN": 0.45,
                "dc_OUT": 0.5,
                "dc_VDD": 0.9,
                "dc_VSS": 0.0,
                "dc_VDD_SRC:p": -20e-6,
                "dcOpInfo_MN0:ids": 20e-6,
                "dcOpInfo_MN0:vgs": 0.45,
                "dcOpInfo_MN0:vds": 0.5,
                "dcOpInfo_MN0:gm": 200e-6,
                "dcOpInfo_MN0:gds": 10e-6,
            },
            {
                "vdd_v": 0.9,
                "bias_v": 0.45,
                "load_resistance_ohm": 20_000.0,
            },
        )

    inconsistent = {
        "dc_IN": 0.45,
        "dc_OUT": 0.5,
        "dc_VDD": 0.9,
        "dc_VSS": 0.0,
        "dc_VDD_SRC:p": -20e-6,
        "dcOpInfo_MN0:ids": 30e-6,
        "dcOpInfo_MN0:vgs": 0.45,
        "dcOpInfo_MN0:vds": 0.5,
        "dcOpInfo_MN0:vdsat": 0.12,
        "dcOpInfo_MN0:gm": 200e-6,
        "dcOpInfo_MN0:gds": 10e-6,
    }
    with pytest.raises(RuntimeError, match="KCL mismatch"):
        _common_source_metrics_from_result(
            inconsistent,
            {
                "vdd_v": 0.9,
                "bias_v": 0.45,
                "load_resistance_ohm": 20_000.0,
            },
        )

    inconsistent["dcOpInfo_MN0:ids"] = 20e-6
    inconsistent["dcOpInfo_MN0:vds"] = 0.4
    with pytest.raises(RuntimeError, match="node/device mismatch for vds_v"):
        _common_source_metrics_from_result(
            inconsistent,
            {
                "vdd_v": 0.9,
                "bias_v": 0.45,
                "load_resistance_ohm": 20_000.0,
            },
        )

    inconsistent["dcOpInfo_MN0:vds"] = 0.5
    inconsistent["dc_VDD_SRC:p"] = -10e-6
    with pytest.raises(RuntimeError, match="VDD source current"):
        _common_source_metrics_from_result(
            inconsistent,
            {
                "vdd_v": 0.9,
                "bias_v": 0.45,
                "load_resistance_ohm": 20_000.0,
            },
        )


def test_parameter_mismatch_is_not_silently_simulated() -> None:
    with pytest.raises(RuntimeError, match="parameter mismatch.*nmos_width_um"):
        _assert_parameter_consistency(
            {"nmos_width_um": 0.5},
            {"nmos_width_um": 0.6},
            expected_label="requested",
            actual_label="OA readback",
        )


def _inverter_schematic_data(mn_multiplier: str) -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "lib": "tsmcN28",
                "cell": "nch_lvt_mac",
                "params": {
                    "Wfg": "0.5u",
                    "l": "0.03u",
                    "fingers": "1",
                    "m": mn_multiplier,
                    "geo": "2",
                },
                "terms": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
            },
            {
                "name": "MP0",
                "lib": "tsmcN28",
                "cell": "pch_lvt_mac",
                "params": {
                    "Wfg": "1u",
                    "l": "0.03u",
                    "fingers": "1",
                    "m": "1",
                },
                "terms": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
            },
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }


def test_explicit_parameter_contract_keeps_unfiltered_cdf_values() -> None:
    data = _inverter_schematic_data("1")
    readback = _instance_parameters_from_schematic(data)
    assert readback["MN0"]["geo"] == "2"

    requested = _requested_instance_parameters(
        {
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"m": "2", "geo": "3"}}
            ]
        }
    )
    assert requested == {"MN0": {"m": "2", "geo": "3"}}


def test_parameter_write_focuses_and_verifies_exact_target_window() -> None:
    calls: list[tuple] = []

    class Client:
        def open_window(self, library, cell, view):
            calls.append(("open", library, cell, view))
            return SimpleNamespace(errors=[])

        def execute_skill(self, expression, timeout):
            calls.append(("skill", expression, timeout))
            return SimpleNamespace(output="t", errors=[])

    _focus_target_schematic(Client(), "vda_test", "vda_cs")

    assert calls[0] == ("open", "vda_test", "vda_cs", "schematic")
    assert "hiSetCurrentWindow(window)" in calls[1][1]
    assert 'cv~>libName == "vda_test"' in calls[1][1]
    assert 'cv~>cellName == "vda_cs"' in calls[1][1]

    class WrongTarget(Client):
        def execute_skill(self, expression, timeout):
            return SimpleNamespace(output="nil", errors=[])

    with pytest.raises(RuntimeError, match="does not match"):
        _focus_target_schematic(WrongTarget(), "vda_test", "vda_cs")


def test_explicit_parameter_worker_uses_bridge_callback_and_exact_readback(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    calls = []
    params_module = ModuleType("virtuoso_bridge.virtuoso.schematic.params")

    def set_instance_params(client, instance, param_filters=None, **parameters):
        calls.append((instance, {"param_filters": param_filters, **parameters}))
        return {
            {"wf": "Wfg", "nf": "fingers"}.get(name, name): value
            for name, value in parameters.items()
        }

    params_module.set_instance_params = set_instance_params
    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge.virtuoso.schematic.params", params_module
    )
    reads = iter([_inverter_schematic_data("1"), _inverter_schematic_data("1")])
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args, **kwargs: next(reads),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._verify_instance_parameter_values",
        lambda client, library, cell, expected: expected,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._focus_target_schematic",
        lambda *args, **kwargs: None,
    )

    class Client:
        def open_window(self, library, cell, view):
            calls.append((library, cell, view))

    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    result = _apply_explicit_instance_parameters(
        Client(),
        "vda_test",
        "vda_inv",
        {
            "circuit": "inverter",
            "profile": profile,
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"wf": "0.6u", "nf": "2"}}
            ],
        },
    )

    assert calls[-1] == (
        "MN0",
        {"param_filters": None, "wf": "0.6u", "nf": "2"},
    )
    assert result["requested_evidence_source"] == "user_input"
    assert result["confirmed_evidence_source"] == "bridge_readback"
    assert result["applied_instance_parameters"] == {
        "MN0": {"Wfg": "0.6u", "fingers": "2"}
    }
    assert result["before_instance_parameters"] == {
        "MN0": {"Wfg": "0.5u", "fingers": "1"}
    }
    assert result["confirmed_instance_parameters"] == {
        "MN0": {"Wfg": "0.6u", "fingers": "2"}
    }
    assert result["application_method"] == "bridge_batch"
    assert result["ordered_replay_reason"] is None


def test_explicit_parameter_worker_repairs_callback_order_once(monkeypatch) -> None:
    import sys
    from types import ModuleType

    calls = []
    params_module = ModuleType("virtuoso_bridge.virtuoso.schematic.params")

    def set_instance_params(client, instance, param_filters=None, **parameters):
        calls.append((instance, dict(parameters)))
        return dict(parameters)

    params_module.set_instance_params = set_instance_params
    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge.virtuoso.schematic.params", params_module
    )
    reads = iter(
        [
            _inverter_schematic_data("1"),
            _inverter_schematic_data("1"),
            _inverter_schematic_data("2"),
        ]
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args, **kwargs: next(reads),
    )
    verification_count = 0

    def verify(client, library, cell, expected):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 1:
            raise ParameterReadbackMismatch(
                "targeted CDF readback failed: CDF parameter readback mismatch: MN0.m"
            )
        return expected

    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._verify_instance_parameter_values",
        verify,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._focus_target_schematic",
        lambda *args, **kwargs: None,
    )

    class Client:
        def open_window(self, library, cell, view):
            return None

    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    result = _apply_explicit_instance_parameters(
        Client(),
        "vda_test",
        "vda_inv",
        {
            "circuit": "inverter",
            "profile": profile,
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"fingers": "2", "m": "2"}}
            ],
        },
    )

    assert calls == [
        ("MN0", {"fingers": "2", "m": "2"}),
        ("MN0", {"fingers": "2"}),
        ("MN0", {"m": "2"}),
    ]
    assert verification_count == 2
    assert result["application_method"] == "bridge_batch_then_ordered_replay"
    assert result["ordered_replay_applied_instance_parameters"] == {
        "MN0": {"fingers": "2", "m": "2"}
    }
    assert "MN0.m" in result["ordered_replay_reason"]

    failed_reads = iter(
        [_inverter_schematic_data("1"), _inverter_schematic_data("1")]
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args, **kwargs: next(failed_reads),
    )

    def always_mismatch(*args, **kwargs):
        raise ParameterReadbackMismatch("MN0.m still mismatched")

    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._verify_instance_parameter_values",
        always_mismatch,
    )
    with pytest.raises(ParameterReadbackMismatch, match="attempted once"):
        _apply_explicit_instance_parameters(
            Client(),
            "vda_test",
            "vda_inv",
            {
                "circuit": "inverter",
                "profile": profile,
                "instance_parameter_updates": [
                    {
                        "instance": "MN0",
                        "parameters": {"fingers": "2", "m": "2"},
                    }
                ],
            },
        )


def test_targeted_cdf_verification_does_not_use_reader_length_or_empty_filters(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    ops_module = ModuleType("virtuoso_bridge.virtuoso.ops")
    ops_module.escape_skill_string = lambda value: value.replace("\\", "\\\\").replace(
        '"', '\\"'
    )
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.ops", ops_module)
    captured = {}

    class Client:
        def execute_skill(self, skill, timeout):
            captured["skill"] = skill
            captured["timeout"] = timeout
            return SimpleNamespace(output="t", errors=[])

    long_value = "x" * 256
    expected = {
        "I0<3>": {
            "empty_value": "",
            "long_value": long_value,
            "display-mode": "layout dependent",
        }
    }

    confirmed = _verify_instance_parameter_values(
        Client(), "vda_test", "vda_existing", expected
    )

    assert confirmed == expected
    assert captured["timeout"] == 60
    assert 'p~>value == ""' in captured["skill"]
    assert long_value in captured["skill"]
    assert 'p = get(iCDF "display-mode")' in captured["skill"]


def test_empty_netlist_and_empty_waveform_are_rejected(tmp_path) -> None:
    netlist = tmp_path / "netlist"
    netlist.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="netlist is empty"):
        _read_nonempty_text(netlist, "si netlist")
    with pytest.raises(RuntimeError, match="signal time is empty"):
        _signal({"time": []}, "time")


def test_si_netlisting_rejects_empty_generated_output(tmp_path) -> None:
    success = SimpleNamespace(ok=True, errors=[])

    class FakeClient:
        def execute_skill(self, *args, **kwargs):
            return success

        def upload_file(self, *args, **kwargs):
            return success

        def run_shell_command(self, *args, **kwargs):
            return success

        def download_file(self, remote_path, local_path, **kwargs):
            if str(remote_path).endswith("/si.env"):
                text = 'simLibName = "vb_pdk_smoke"\n'
            elif str(remote_path).endswith("si_batch_stdout.log"):
                text = "Begin Incremental Netlisting\nEnd netlisting\n"
            else:
                text = ""
            local_path.write_text(text, encoding="utf-8")
            return success

    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    with pytest.raises(RuntimeError, match="si netlist is empty"):
        _generate_oa_netlist(
            FakeClient(),
            {
                "task_id": "netlist-failure",
                "target": {
                    "library": "vb_pdk_smoke",
                    "cell": "vda_inv",
                    "view": "schematic",
                },
                "profile": profile,
            },
            tmp_path,
            timeout=60,
        )


def test_si_command_failure_reports_the_retained_remote_run(tmp_path) -> None:
    success = SimpleNamespace(ok=True, errors=[])
    failure = SimpleNamespace(
        ok=False,
        errors=["Socket error: WinError 10054"],
        output="",
    )

    class FakeClient:
        def execute_skill(self, *args, **kwargs):
            return success

        def upload_file(self, *args, **kwargs):
            return success

        def run_shell_command(self, *args, **kwargs):
            return failure

        def download_file(self, remote_path, local_path, **kwargs):
            text = (
                'simLibName = "vb_pdk_smoke"\n'
                if str(remote_path).endswith("/si.env")
                else ""
            )
            local_path.write_text(text, encoding="utf-8")
            return success

    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    with pytest.raises(
        RuntimeError,
        match=(
            r"si batch command failed: Socket error: WinError 10054.*"
            r"remote si run retained at /data/xum/virtuoso_bridge_smoke/"
            r"vda_netlist-failure_"
        ),
    ):
        _generate_oa_netlist(
            FakeClient(),
            {
                "task_id": "netlist-failure",
                "target": {
                    "library": "vb_pdk_smoke",
                    "cell": "vda_inv",
                    "view": "schematic",
                },
                "profile": profile,
            },
            tmp_path,
            timeout=60,
        )


def test_si_log_requires_a_real_completion_marker() -> None:
    with pytest.raises(RuntimeError, match="no completion marker"):
        _validate_si_log("SI_RC=0 but no netlisting completion evidence")


@pytest.mark.parametrize(("output", "expected"), [("t", True), ('"nil"', False)])
def test_schematic_existence_uses_lightweight_skill(output, expected) -> None:
    client = SimpleNamespace(
        execute_skill=lambda *args, **kwargs: SimpleNamespace(output=output, errors=[])
    )
    assert _schematic_exists(client, "vda_test", "vda_inv") is expected


def test_inverter_simulation_worker_returns_structured_evidence(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    runner_module = ModuleType("virtuoso_bridge.spectre.runner")

    class Simulator:
        _ssh_runner = None

        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def run_simulation(self, netlist, parameters):
            return SimpleNamespace(
                ok=True,
                data={
                    "time": [0.0, 1e-9],
                    "IN": [0.0, 0.9],
                    "OUT": [0.9, 0.0],
                    "VDD_SRC:p": [0.0, -1e-6],
                },
                tool_version="test-spectre",
                warnings=[],
            )

    runner_module.SpectreSimulator = Simulator
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._client",
        lambda: SimpleNamespace(ssh_runner=None),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args: {},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._assert_inverter",
        lambda *args: "inverter_core",
    )
    oa = {"nmos_width_um": 0.5, "pmos_width_um": 1.0, "length_um": 0.03}
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._semantic_parameters_from_schematic",
        lambda data: oa,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._resolved_parameters",
        lambda payload, readback: oa | {"vdd_v": 0.9, "load_ff": 2.0},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._generate_oa_netlist",
        lambda *args, **kwargs: {
            "remote_run_dir": "/data/xum/virtuoso_bridge_smoke/vda_test",
            "remote_netlist_path": "/data/xum/virtuoso_bridge_smoke/vda_test/netlist",
            "netlist_sha256": "0" * 64,
            "parsed": {"semantic_parameters": oa, "instances": {}},
            "si_log_tail": ["End netlisting"],
        },
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._upload_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker.extract_inverter_metrics",
        lambda *args, **kwargs: {"delay_ps": 10.0},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker.extract_supply_metrics",
        lambda *args, **kwargs: {"supply_energy_per_cycle_fj": 1.0},
    )

    result = simulate_inverter(
        {
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "profile": {"model_include": "/data/model.scs", "model_section": "tt"},
            "parameters": {"vdd_v": 0.9, "load_ff": 2.0},
            "timeout_seconds": 60,
        }
    )

    assert result["tool_version"] == "test-spectre"
    assert result["evidence"]["netlist"]["parameter_consistency"] == "matched"
    assert result["metric_sources"]["delay_ps"] == "eda_result"
    assert result["metric_sources"]["gate_area_proxy_um2"] == "software_inference"


def test_common_source_ac_worker_returns_dc_and_complex_ac_evidence(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    frequency_hz = [10.0 ** (4.0 + index / 20.0) for index in range(141)]
    vin = [1.0 + 0.0j for _ in frequency_hz]
    vout = [-3.0 / (1.0 + 1j * frequency / 2e9) for frequency in frequency_hz]
    runner_module = ModuleType("virtuoso_bridge.spectre.runner")

    class Simulator:
        _ssh_runner = None

        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def run_simulation(self, netlist, parameters):
            return SimpleNamespace(
                ok=True,
                data={
                    "dc_IN": 0.35,
                    "dc_OUT": 0.68,
                        "dc_VDD": 0.9,
                        "dc_VSS": 0.0,
                        "dc_VDD_SRC:p": -10e-6,
                    "dcOpInfo_MN0:ids": 10e-6,
                    "dcOpInfo_MN0:vgs": 0.35,
                    "dcOpInfo_MN0:vds": 0.68,
                    "dcOpInfo_MN0:vdsat": 0.1,
                    "dcOpInfo_MN0:gm": 200e-6,
                    "dcOpInfo_MN0:gds": 10e-6,
                    "ac_freq": frequency_hz,
                    "ac_IN": vin,
                    "ac_OUT": vout,
                },
                tool_version="test-spectre-ac",
                warnings=[],
            )

    runner_module.SpectreSimulator = Simulator
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_dc_data_from_result",
        lambda result: (result.data, {"selection": "test fixture"}),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._client",
        lambda: SimpleNamespace(ssh_runner=None),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args: {},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._assert_common_source",
        lambda *args: "common_source",
    )
    oa_parameters = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 22_000.0,
    }
    geometry = {
        "finger_width_um": 1.0,
        "fingers": 2.0,
        "multiplicity": 1.0,
        "total_width_um": 2.0,
    }
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_semantic_parameters_from_schematic",
        lambda data: oa_parameters,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_device_geometry_from_schematic",
        lambda data: geometry,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._generate_oa_netlist",
        lambda *args, **kwargs: {
            "remote_run_dir": "/data/xum/virtuoso_bridge_smoke/vda_cs_ac",
            "remote_netlist_path": (
                "/data/xum/virtuoso_bridge_smoke/vda_cs_ac/netlist"
            ),
            "netlist_sha256": "1" * 64,
            "parsed": {
                "semantic_parameters": oa_parameters,
                "device_geometry": geometry,
                "topology_variant": "common_source",
                "instances": {},
            },
            "si_log_tail": ["End netlisting"],
        },
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._upload_file",
        lambda *args, **kwargs: None,
    )

    result = simulate_common_source(
        {
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "profile": load_pdk_profile("nics4304_tsmc28").model_dump(
                mode="json"
            ),
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
                "reference_points": 5,
                "max_reference_variation_db": 0.5,
            },
            "ac_sweep_user_fields": [
                "start_hz",
                "stop_hz",
                "points_per_decade",
            ],
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 2.0},
            "timeout_seconds": 60,
        }
    )

    assert result["tool_version"] == "test-spectre-ac"
    assert result["analysis_complete"] is True
    assert result["metrics"]["saturation_region"] == 1.0
    assert result["metrics"]["low_frequency_gain_v_per_v"] == pytest.approx(
        3.0, rel=1e-5
    )
    assert result["metrics"]["bandwidth_3db_hz"] == pytest.approx(2e9, rel=0.01)
    assert result["metrics"]["gain_bandwidth_product_hz"] == pytest.approx(
        6e9, rel=0.01
    )
    assert result["metric_sources"]["bandwidth_3db_hz"] == "eda_result"
    assert result["metric_sources"]["saturation_region"] == "software_inference"
    assert result["evidence"]["ac_response"]["source"] == "eda_result"
    assert result["evidence"]["ac_response"]["bandwidth"]["status"] == "resolved"
    sources = result["evidence"]["testbench"]["value_sources"]["ac_sweep"]
    assert sources["start_hz"] == "user_input"
    assert sources["reference_points"] == "software_inference"


@pytest.mark.parametrize(
    ("analysis", "sweep_field", "sweep", "helper", "metric", "evidence_key"),
    [
        (
            "transient",
            "linearity_sweep",
            {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.02],
                "settling_cycles": 4,
                "measurement_cycles": 8,
                "points_per_cycle": 128,
                "max_harmonic": 5,
                "compression_db": 1.0,
            },
            "_common_source_linearity_metrics_from_result",
            "max_thd_percent",
            "linearity_response",
        ),
        (
            "noise",
            "noise_sweep",
            {"start_hz": 1e3, "stop_hz": 1e9, "points_per_decade": 20},
            "_common_source_noise_metrics_from_result",
            "integrated_input_referred_noise_uv_rms",
            "noise_response",
        ),
    ],
)
def test_common_source_quality_worker_routes_metrics_and_evidence(
    monkeypatch,
    analysis,
    sweep_field,
    sweep,
    helper,
    metric,
    evidence_key,
) -> None:
    import sys
    from types import ModuleType

    runner_module = ModuleType("virtuoso_bridge.spectre.runner")

    class Simulator:
        _ssh_runner = None

        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def run_simulation(self, netlist, parameters):
            return SimpleNamespace(
                ok=True,
                data={
                    "dc_IN": 0.35,
                    "dc_OUT": 0.7,
                    "dc_VDD": 0.9,
                    "dc_VSS": 0.0,
                    "dc_VDD_SRC:p": -10e-6,
                    "dcOpInfo_MN0:ids": 10e-6,
                    "dcOpInfo_MN0:vgs": 0.35,
                    "dcOpInfo_MN0:vds": 0.7,
                    "dcOpInfo_MN0:vdsat": 0.1,
                    "dcOpInfo_MN0:gm": 200e-6,
                    "dcOpInfo_MN0:gds": 10e-6,
                },
                metadata={},
                tool_version="test-spectre-quality",
                warnings=[],
            )

    runner_module.SpectreSimulator = Simulator
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_dc_data_from_result",
        lambda result: (result.data, {"selection": "test fixture"}),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._client",
        lambda: SimpleNamespace(ssh_runner=None),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args: {},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._assert_common_source",
        lambda *args: "common_source",
    )
    oa_parameters = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 20_000.0,
    }
    geometry = {
        "finger_width_um": 1.0,
        "fingers": 1.0,
        "multiplicity": 1.0,
        "total_width_um": 1.0,
    }
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_semantic_parameters_from_schematic",
        lambda data: oa_parameters,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_device_geometry_from_schematic",
        lambda data: geometry,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._generate_oa_netlist",
        lambda *args, **kwargs: {
            "remote_run_dir": "/data/xum/virtuoso_bridge_smoke/vda_cs_quality",
            "remote_netlist_path": "/data/xum/virtuoso_bridge_smoke/vda_cs_quality/netlist",
            "netlist_sha256": "2" * 64,
            "parsed": {
                "semantic_parameters": oa_parameters,
                "device_geometry": geometry,
                "topology_variant": "common_source",
                "instances": {},
            },
            "si_log_tail": ["End netlisting"],
        },
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._upload_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        f"virtuoso_design_agent.adapters.bridge_worker.{helper}",
        lambda *args, **kwargs: (
            {metric: 1.0},
            {
                "analysis_complete": True,
                "warnings": [],
                **(
                    {"temporary_psf_file": "C:/temp/noise.noise"}
                    if analysis == "noise"
                    else {}
                ),
            },
        ),
    )
    payload = {
        "target": {"library": "vda_test", "cell": "vda_cs"},
        "profile": load_pdk_profile("nics4304_tsmc28").model_dump(mode="json"),
        "analysis": analysis,
        sweep_field: sweep,
        f"{sweep_field}_user_fields": list(sweep),
        "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
        "timeout_seconds": 60,
    }

    result = simulate_common_source(payload)

    assert result["analysis_complete"] is True
    assert result["metrics"][metric] == pytest.approx(1.0)
    assert result["metrics"]["dc_supply_power_uw"] == pytest.approx(9.0)
    assert result["evidence"][evidence_key]["source"] == "eda_result"
    assert result["evidence"]["testbench"]["values"][sweep_field] == sweep
    assert result["evidence"]["testbench"]["model_resolution_source"] == (
        "pdk_profile"
    )
    assert result["evidence"]["testbench"]["model_configuration"] == {
        "source": "pdk_profile",
        "profile": "nics4304_tsmc28",
        "profile_source": "pdk_profile",
        "process_corner": None,
        "process_corner_source": "pdk_profile",
        "temperature_c": None,
        "temperature_source": "simulator_default",
        "includes": [
            {
                "path": payload["profile"]["model_include"],
                "section": payload["profile"]["model_section"],
            }
        ],
    }
    if analysis == "noise":
        assert result["evidence"][evidence_key]["remote_psf_path"].endswith(
            "/noise.noise.psfascii"
        )


def test_common_source_quality_bundle_reuses_one_verified_oa_netlist(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    runner_module = ModuleType("virtuoso_bridge.spectre.runner")
    simulation_calls: list[str] = []

    class Simulator:
        _ssh_runner = None

        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def run_simulation(self, netlist, parameters):
            simulation_calls.append(netlist.read_text(encoding="utf-8"))
            return SimpleNamespace(
                ok=True,
                data={"fixture": 1.0},
                metadata={},
                tool_version="test-spectre-quality-bundle",
                warnings=[],
            )

    runner_module.SpectreSimulator = Simulator
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_dc_data_from_result",
        lambda result: (result.data, {"selection": "test fixture"}),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_metrics_from_result",
        lambda data, parameters: (
            {"shared_dc_metric": 1.0, "saturation_region": 1.0},
            {"fixture": True},
        ),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_ac_metrics_from_result",
        lambda *args, **kwargs: (
            {"bundle_ac_metric": 2.0},
            {"analysis_complete": True, "issues": [], "warnings": []},
        ),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_linearity_metrics_from_result",
        lambda *args, **kwargs: (
            {"bundle_linearity_metric": 3.0},
            {"analysis_complete": True, "issues": [], "warnings": []},
        ),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_noise_metrics_from_result",
        lambda *args, **kwargs: (
            {"bundle_noise_metric": 4.0},
            {"analysis_complete": True, "issues": [], "warnings": []},
        ),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._client",
        lambda: SimpleNamespace(ssh_runner=None),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._read_schematic",
        lambda *args: {},
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._assert_common_source",
        lambda *args: "common_source",
    )
    oa_parameters = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 20_000.0,
    }
    geometry = {
        "finger_width_um": 1.0,
        "fingers": 1.0,
        "multiplicity": 1.0,
        "total_width_um": 1.0,
    }
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_semantic_parameters_from_schematic",
        lambda data: oa_parameters,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_device_geometry_from_schematic",
        lambda data: geometry,
    )
    netlist_calls = 0

    def generate_netlist(*args, **kwargs):
        nonlocal netlist_calls
        netlist_calls += 1
        return {
            "remote_run_dir": "/data/xum/virtuoso_bridge_smoke/vda_cs_bundle",
            "remote_netlist_path": (
                "/data/xum/virtuoso_bridge_smoke/vda_cs_bundle/netlist"
            ),
            "netlist_sha256": "3" * 64,
            "parsed": {
                "semantic_parameters": oa_parameters,
                "device_geometry": geometry,
                "topology_variant": "common_source",
                "instances": {},
            },
            "si_log_tail": ["End netlisting"],
        }

    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._generate_oa_netlist",
        generate_netlist,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._upload_file",
        lambda *args, **kwargs: None,
    )

    result = simulate_common_source(
        {
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "profile": load_pdk_profile("nics4304_tsmc28").model_dump(
                mode="json"
            ),
            "analysis": "quality",
            "analysis_source": "user_input",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
                "reference_points": 5,
                "max_reference_variation_db": 0.5,
            },
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
                "settling_cycles": 4,
                "measurement_cycles": 8,
                "points_per_cycle": 128,
                "max_harmonic": 5,
                "compression_db": 1.0,
            },
            "noise_sweep": {
                "start_hz": 1e3,
                "stop_hz": 1e10,
                "points_per_decade": 20,
            },
            "parameters": {"bias_v": 0.35, "vdd_v": 0.9, "load_ff": 1.0},
            "timeout_seconds": 60,
        }
    )

    assert netlist_calls == 1
    assert len(simulation_calls) == 3
    assert result["analysis_complete"] is True
    assert result["analysis_bundle"]["analyses"] == [
        "ac",
        "transient",
        "noise",
    ]
    assert result["analysis_bundle"]["oa_netlist_reuse"] == "one_verified_netlist"
    assert result["analysis_bundle"]["requested_analysis_source"] == "user_input"
    assert result["metrics"]["bundle_ac_metric"] == pytest.approx(2.0)
    assert result["metrics"]["bundle_linearity_metric"] == pytest.approx(3.0)
    assert result["metrics"]["bundle_noise_metric"] == pytest.approx(4.0)
    assert result["evidence"]["analysis_bundle"]["source"] == "software_inference"
    assert set(result["evidence"]["analyses"]) == {"ac", "transient", "noise"}
    for evidence in result["evidence"]["analyses"].values():
        assert evidence["testbench"]["value_sources"]["analysis"] == (
            "software_inference"
        )


def test_analysis_bundle_rejects_parameter_or_shared_metric_mismatch() -> None:
    baseline = {
        "parameters": {"bias_v": 0.35},
        "metrics": {"dc_metric": 1.0},
        "metric_sources": {"dc_metric": "eda_result"},
        "analysis_complete": True,
    }
    mismatched_parameters = dict(baseline)
    mismatched_parameters["parameters"] = {"bias_v": 0.36}
    with pytest.raises(RuntimeError, match="parameter mismatch"):
        merge_analysis_bundle(
            {"ac": baseline, "noise": mismatched_parameters}
        )

    mismatched_metric = dict(baseline)
    mismatched_metric["metrics"] = {"dc_metric": 1.1}
    with pytest.raises(RuntimeError, match="shared metric mismatch"):
        merge_analysis_bundle({"ac": baseline, "noise": mismatched_metric})


def test_subprocess_boundary_parses_only_structured_marker(tmp_path, monkeypatch) -> None:
    bridge_python = tmp_path / "python.exe"
    bridge_python.touch()
    payload = {"ok": True, "data": {"connected": True}}

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="bridge noise\nVDA_RESULT=" + json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    result = SubprocessBridgeAdapter(bridge_python).probe("nics4304_tsmc28")
    assert result.data == {"connected": True}
    assert result.evidence_source.value == "bridge_readback"


def test_subprocess_payload_preserves_ac_sweep_and_user_input_fields() -> None:
    from virtuoso_design_agent.models import TaskSpec

    task = TaskSpec.model_validate(
        {
            "id": "payload-ac",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e3,
                "stop_hz": 1e11,
                "points_per_decade": 40,
            },
            "parameters": {"bias_v": 0.45, "load_ff": 2.0},
        }
    )

    payload = SubprocessBridgeAdapter._task_payload(task)

    assert payload["analysis"] == "ac"
    assert payload["analysis_source"] == "user_input"
    assert payload["ac_sweep"] == {
        "start_hz": 1e3,
        "stop_hz": 1e11,
        "points_per_decade": 40,
        "reference_points": 5,
        "max_reference_variation_db": 0.5,
    }
    assert payload["ac_sweep_user_fields"] == [
        "points_per_decade",
        "start_hz",
        "stop_hz",
    ]

    dc_task = TaskSpec.model_validate(
        {
            "id": "payload-default-dc",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.45},
        }
    )
    dc_payload = SubprocessBridgeAdapter._task_payload(dc_task)
    assert dc_payload["analysis"] == "dc"
    assert dc_payload["analysis_source"] == "software_inference"


def test_subprocess_payload_preserves_linearity_and_noise_sweep_sources() -> None:
    from virtuoso_design_agent.models import TaskSpec

    linearity_task = TaskSpec.model_validate(
        {
            "id": "payload-linearity",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "transient",
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.02],
                "measurement_cycles": 6,
            },
            "parameters": {"bias_v": 0.35},
        }
    )
    linearity_payload = SubprocessBridgeAdapter._task_payload(linearity_task)
    assert linearity_payload["linearity_sweep"]["settling_cycles"] == 4
    assert linearity_payload["linearity_sweep_user_fields"] == [
        "amplitudes_v",
        "frequency_hz",
        "measurement_cycles",
    ]

    noise_task = TaskSpec.model_validate(
        {
            "id": "payload-noise",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "noise",
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e9},
            "parameters": {"bias_v": 0.35},
        }
    )
    noise_payload = SubprocessBridgeAdapter._task_payload(noise_task)
    assert noise_payload["noise_sweep"]["points_per_decade"] == 20
    assert noise_payload["noise_sweep_user_fields"] == ["start_hz", "stop_hz"]


def test_subprocess_payload_preserves_explicit_pvt_conditions() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "payload-pvt",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.35, "load_ff": 1.0},
            "operating_conditions": [
                {
                    "name": "tt_25c_0p90v",
                    "process_corner": "tt",
                    "temperature_c": 25.0,
                    "vdd_v": 0.9,
                },
                {
                    "name": "ff_m40c_0p99v",
                    "process_corner": "ff",
                    "temperature_c": -40.0,
                    "vdd_v": 0.99,
                },
            ],
        }
    )

    payload = SubprocessBridgeAdapter._task_payload(task)

    assert payload["operating_conditions"] == [
        {
            "name": "tt_25c_0p90v",
            "process_corner": "tt",
            "temperature_c": 25.0,
            "vdd_v": 0.9,
        },
        {
            "name": "ff_m40c_0p99v",
            "process_corner": "ff",
            "temperature_c": -40.0,
            "vdd_v": 0.99,
        },
    ]
    assert payload["operating_conditions_source"] == "user_input"

    quality_task = TaskSpec.model_validate(
        {
            "id": "payload-quality",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "analysis": "quality",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
            },
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
            "parameters": {"bias_v": 0.35, "load_ff": 1.0},
        }
    )
    quality_payload = SubprocessBridgeAdapter._task_payload(quality_task)
    assert quality_payload["analysis"] == "quality"
    assert quality_payload["analysis_source"] == "user_input"
    assert set(quality_payload) >= {
        "ac_sweep",
        "linearity_sweep",
        "noise_sweep",
        "ac_sweep_user_fields",
        "linearity_sweep_user_fields",
        "noise_sweep_user_fields",
    }


def test_subprocess_boundary_rejects_unstructured_output(tmp_path, monkeypatch) -> None:
    bridge_python = tmp_path / "python.exe"
    bridge_python.touch()

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="traceback", stderr="failure")

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(BridgeWorkerError, match="no structured result"):
        SubprocessBridgeAdapter(bridge_python).probe("nics4304_tsmc28")


def test_subprocess_boundary_converts_timeout(tmp_path, monkeypatch) -> None:
    bridge_python = tmp_path / "python.exe"
    bridge_python.touch()

    def fake_run(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired("worker", 30)

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(BridgeWorkerError, match="timed out"):
        SubprocessBridgeAdapter(bridge_python).probe("nics4304_tsmc28")
