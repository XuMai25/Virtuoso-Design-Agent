from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from virtuoso_design_agent.adapters.bridge_worker import (
    _apply_explicit_instance_parameters,
    _assert_parameter_consistency,
    _common_source_metrics_from_result,
    _common_source_testbench_deck,
    _complete_si_env,
    _generate_oa_netlist,
    _inverter_testbench_deck,
    _instance_parameters_from_schematic,
    _parse_common_source_netlist,
    _parse_inverter_netlist,
    _read_nonempty_text,
    _requested_instance_parameters,
    _schematic_exists,
    _signal,
    _validate_si_log,
    _verify_instance_parameter_values,
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


def test_common_source_operating_point_requires_consistent_eda_scalars() -> None:
    metrics, evidence = _common_source_metrics_from_result(
        {
            "dc_IN": 0.45,
            "dc_OUT": 0.5,
            "dc_VDD": 0.9,
            "dc_VSS": 0.0,
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
