from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from virtuoso_design_agent.adapters.bridge_worker import _inverter_deck, _schematic_exists
from virtuoso_design_agent.adapters.subprocess_bridge import (
    BridgeWorkerError,
    SubprocessBridgeAdapter,
)
from virtuoso_design_agent.profiles import load_pdk_profile


def test_pdk_profile_contains_verified_nics4304_paths() -> None:
    profile = load_pdk_profile("nics4304_tsmc28")
    assert profile.tech_library == "tsmcN28"
    assert profile.model_include.startswith("/data/technique/")


def test_inverter_deck_uses_profile_and_semantic_parameters() -> None:
    profile = load_pdk_profile("nics4304_tsmc28").model_dump()
    deck = _inverter_deck(
        profile,
        {
            "nmos_width_um": 0.5,
            "pmos_width_um": 1.0,
            "length_um": 0.03,
            "load_ff": 2.0,
            "vdd_v": 0.9,
        },
    )
    assert "nch_lvt_mac" in deck
    assert "pch_lvt_mac" in deck
    assert "save VIN VOUT VDD" in deck
    assert "w=wn" in deck and "w=wp" in deck


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
