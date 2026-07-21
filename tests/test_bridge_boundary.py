from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import virtuoso_design_agent.adapters.bridge_worker as bridge_worker
from virtuoso_design_agent.adapters.base import merge_analysis_bundle
from virtuoso_design_agent.adapters.bridge_worker import (
    _ade_capture_manifest,
    _apply_explicit_instance_parameters,
    _assert_common_source,
    _assert_common_source_transform_preserved,
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
    _common_source_testbench_deck,
    _complex_signal,
    _complete_si_env,
    _discard_failed_existing_schematic_edit,
    _edit_existing_schematic,
    _generate_oa_netlist,
    _inverter_testbench_deck,
    _instance_parameters_from_schematic,
    _has_structured_ade_outputs,
    _manifest_fingerprint,
    _parse_common_source_netlist,
    _parse_inverter_netlist,
    _preflight_mn0_source_label,
    _rename_mn0_source_label_operation,
    ParameterReadbackMismatch,
    _read_nonempty_text,
    _requested_instance_parameters,
    _schematic_exists,
    _signal,
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
    bridge = ModuleType("virtuoso_bridge")
    bridge.__path__ = []
    bridge.virtuoso = virtuoso
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", bridge)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso", virtuoso)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.virtuoso.maestro", maestro)


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
                "design_view": "schematic",
                "simulator": "spectre",
            },
        }
    )

    create_call = next(call for call in calls if call[0] == "create_test")
    assert create_call[1] == "VDA_AC"
    assert create_call[2]["view"] == "schematic"
    assert create_call[2]["simulator"] == "spectre"
    assert prepared["persistent_view_confirmed"] is True
    assert prepared["tests_readback"] == ["VDA_AC"]
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


def test_background_maestro_run_uses_exact_new_history_and_closes_session(
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
            },
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
    assert result["artifacts_captured"] is False
    assert ("run", {"session": "fnxBackground8", "timeout": 321}) in calls
    read_call = next(call for call in calls if call[0] == "results")
    assert read_call[2]["history"] == "Interactive.8"
    assert calls[-1] == ("close", "fnxBackground8")


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
    monkeypatch.setattr(bridge_worker, "_client", Client)

    with pytest.raises(RuntimeError, match="non-empty point/output/spec table"):
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

    assert calls == [("close", "fnxBackground9")]


def test_maestro_variable_patch_saves_once_and_reopens_for_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    state = {
        "persisted": {"bias_v": None, "load_ff": "1f"},
        "working": {},
    }

    class Client:
        def execute_skill(self, expression, **kwargs):
            if "ddGetObj" in expression:
                return SimpleNamespace(output="t", errors=[])
            if "maeGetSetup" in expression:
                return SimpleNamespace(output='("VDA")', errors=[])
            raise AssertionError(expression)

    sessions = iter(["fnxPatch1", "fnxPatch2"])

    def fake_open_session(_client, library, cell):
        session = next(sessions)
        state["working"] = dict(state["persisted"])
        calls.append(("open", session, library, cell))
        return session

    def fake_get_var(_client, name, *, session):
        calls.append(("get", session, name))
        value = state["working"].get(name)
        return "nil" if value is None else f'"{value}"'

    def fake_set_var(_client, name, value, *, session):
        calls.append(("set", session, name, value))
        state["working"][name] = value

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
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    },
                    {
                        "name": "load_ff",
                        "expected_value": "1f",
                        "value": "1f,2f",
                    },
                ],
            },
        }
    )

    assert result["variable_scope"] == "global"
    assert result["tests_readback_before"] == ["VDA"]
    assert result["tests_readback_after"] == ["VDA"]
    assert result["before_variables"] == {"bias_v": None, "load_ff": "1f"}
    assert result["immediate_variables"] == {
        "bias_v": "0.30,0.35,0.40",
        "load_ff": "1f,2f",
    }
    assert result["persisted_variables"] == result["immediate_variables"]
    assert result["declared_global_sweep_variables"] == ["bias_v", "load_ff"]
    assert result["test_or_corner_overrides_checked"] is False
    assert result["effective_simulation_value_verified"] is False
    assert result["maestro_setup_write_performed"] is True
    assert result["schematic_oa_write_performed"] is False
    assert result["automated_simulation_performed"] is False
    assert result["before_target_fingerprint_sha256"]
    assert result["after_target_fingerprint_sha256"]
    assert result["before_target_fingerprint_sha256"] != result[
        "after_target_fingerprint_sha256"
    ]
    assert [call[0] for call in calls].count("save") == 1
    assert [call[0] for call in calls].count("open") == 2
    assert [call[0] for call in calls].count("close") == 2


def test_maestro_variable_patch_worker_requires_explicit_old_value() -> None:
    with pytest.raises(RuntimeError, match="must be explicitly declared"):
        bridge_worker._validate_maestro_variable_update(  # noqa: SLF001
            {"name": "bias_v", "value": "0.35"}
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
    assert payload["ade_run_user_fields"] == ["require_structured_outputs"]


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
    assert payload["ade_variables"]["updates"] == [
        {
            "name": "bias_v",
            "expected_value": None,
            "value": "0.30,0.35,0.40",
        }
    ]
    assert set(payload["ade_variables_user_fields"]) == {
        "expected_tests",
        "updates",
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


def test_source_label_edit_is_strict_and_parameter_updates_are_partial() -> None:
    operation = _rename_mn0_source_label_operation()
    assert 'x~>theLabel == "VSS"' in operation
    assert "dx * dx + dy * dy <= 0.02" in operation
    assert "length(rbLabels) == 1" in operation
    assert 'rbLabel~>theLabel = "NSRC"' in operation

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
        lambda *args: None,
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
