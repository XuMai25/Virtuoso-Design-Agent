from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from virtuoso_design_agent.adapters.bridge_worker import (
    _assert_parameter_consistency,
    _complete_si_env,
    _generate_oa_netlist,
    _inverter_testbench_deck,
    _parse_inverter_netlist,
    _read_nonempty_text,
    _schematic_exists,
    _signal,
    _validate_si_log,
)
from virtuoso_design_agent.adapters.subprocess_bridge import (
    BridgeWorkerError,
    SubprocessBridgeAdapter,
)
from virtuoso_design_agent.profiles import load_pdk_profile


def test_pdk_profile_contains_verified_nics4304_paths() -> None:
    profile = load_pdk_profile("nics4304_tsmc28")
    assert profile.tech_library == "tsmcN28"
    assert profile.model_include.startswith("/data/technique/")
    assert profile.cds_lib_path.startswith("/data/xum/")
    assert profile.remote_run_root.startswith("/data/xum/")


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


def test_parameter_mismatch_is_not_silently_simulated() -> None:
    with pytest.raises(RuntimeError, match="parameter mismatch.*nmos_width_um"):
        _assert_parameter_consistency(
            {"nmos_width_um": 0.5},
            {"nmos_width_um": 0.6},
            expected_label="requested",
            actual_label="OA readback",
        )


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


def test_si_log_requires_a_real_completion_marker() -> None:
    with pytest.raises(RuntimeError, match="no completion marker"):
        _validate_si_log("SI_RC=0 but no netlisting completion evidence")


@pytest.mark.parametrize(("output", "expected"), [("t", True), ('"nil"', False)])
def test_schematic_existence_uses_lightweight_skill(output, expected) -> None:
    client = SimpleNamespace(
        execute_skill=lambda *args, **kwargs: SimpleNamespace(output=output, errors=[])
    )
    assert _schematic_exists(client, "vda_test", "vda_inv") is expected


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
