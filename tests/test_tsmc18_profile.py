from __future__ import annotations

import pytest

from virtuoso_design_agent.adapters.bridge_worker import (
    _assert_inverter,
    _parse_inverter_netlist,
)
from virtuoso_design_agent.models import DEFAULT_PDK_PROFILE, TaskSpec
from virtuoso_design_agent.netlist_preview import (
    NetlistPreviewSpec,
    render_spectre_preview_deck,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.profiles import load_pdk_profile


def _tsmc18_inverter_readback() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "lib": "tsmc18",
                "cell": "nmos2v_mac",
                "params": {"w": "2u", "l": "180n"},
                "terms": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
            },
            {
                "name": "MP0",
                "lib": "tsmc18",
                "cell": "pmos2v_mac",
                "params": {"w": "4u", "l": "180n"},
                "terms": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
            },
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VDD", "VSS")},
    }


def _tsmc18_preview() -> NetlistPreviewSpec:
    return NetlistPreviewSpec.model_validate(
        {
            "id": "tsmc18-inverter",
            "input_source": "VIN",
            "supply_sources": ["VDD_SRC"],
            "voltage_sources": [
                {"name": "VDD_SRC", "positive": "VDD", "negative": "0", "dc_v": 1.8},
                {"name": "VIN", "positive": "IN", "negative": "0", "dc_v": 0.9},
            ],
            "variants": [
                {
                    "id": "inverter",
                    "output_positive": "OUT",
                    "mosfets": [
                        {
                            "name": "MN0",
                            "polarity": "nmos",
                            "drain": "OUT",
                            "gate": "IN",
                            "source": "0",
                            "bulk": "0",
                            "width_um": 2.0,
                            "length_um": 0.18,
                        },
                        {
                            "name": "MP0",
                            "polarity": "pmos",
                            "drain": "OUT",
                            "gate": "IN",
                            "source": "VDD",
                            "bulk": "VDD",
                            "width_um": 4.0,
                            "length_um": 0.18,
                        },
                    ],
                }
            ],
        }
    )


def test_tsmc18_profile_is_explicit_and_does_not_change_the_default_pdk() -> None:
    profile = load_pdk_profile("nics4304_tsmc18")

    assert DEFAULT_PDK_PROFILE == "nics4304_tsmc28"
    assert profile.tech_library == "tsmc18"
    assert (profile.nmos_cell, profile.pmos_cell) == (
        "nmos2v_mac",
        "pmos2v_mac",
    )
    assert (profile.nmos_model, profile.pmos_model) == ("nch_mac", "pch_mac")
    assert profile.model_section == "tt"
    assert set(profile.process_corners) == {"tt", "ff", "ss", "sf", "fs"}
    assert profile.default_vdd_v == pytest.approx(1.8)
    assert profile.default_length_um == pytest.approx(0.18)
    assert profile.cds_lib_path == "/data/xum/Analog_VLSI/icfb/cds.lib"


def test_tsmc18_profile_can_compile_a_normal_vda_plan() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "tsmc18-inverter-inspect",
            "operation": "schematic.inspect",
            "circuit": "inverter",
            "pdk_profile": "nics4304_tsmc18",
            "target": {
                "library": "Analog_VLSI",
                "cell": "vda_tsmc18_inv",
            },
        }
    )

    plan = build_plan(task)

    assert plan.task_id == task.id
    assert plan.confirmation_token
    assert plan.requires_remote_write is False
    assert plan.requires_remote_compute is False


def test_tsmc18_oa_master_and_si_model_names_are_checked_separately() -> None:
    profile = load_pdk_profile("nics4304_tsmc18").model_dump(mode="json")

    assert _assert_inverter(_tsmc18_inverter_readback(), profile) == "inverter_core"
    parsed = _parse_inverter_netlist(
        """
MN0 (OUT IN VSS VSS) nch_mac l=180n w=2u nf=1 multi=1
MP0 (OUT IN VDD VDD) pch_mac l=180n w=4u nf=1 multi=1
""",
        profile,
    )

    assert parsed["semantic_parameters"] == {
        "nmos_width_um": pytest.approx(2.0),
        "pmos_width_um": pytest.approx(4.0),
        "length_um": pytest.approx(0.18),
    }
    with pytest.raises(RuntimeError, match="si netlist model mismatch"):
        _parse_inverter_netlist(
            """
MN0 (OUT IN VSS VSS) nmos2v_mac l=180n w=2u nf=1 multi=1
MP0 (OUT IN VDD VDD) pmos2v_mac l=180n w=4u nf=1 multi=1
""",
            profile,
        )


def test_tsmc18_preview_uses_spectre_models_not_oa_cell_names() -> None:
    profile = load_pdk_profile("nics4304_tsmc18").model_dump(mode="json")

    deck = render_spectre_preview_deck(
        _tsmc18_preview(),
        "inverter",
        profile,
        analysis="dc",
    )

    assert (
        'include "/data/xum/Analog_VLSI/PDK_TSMC18_1809/models/spectre/'
        'cor_std_mos.scs" section=tt'
    ) in deck
    assert "MN0 (OUT IN 0 0) nch_mac w=2u l=0.18u" in deck
    assert "MP0 (OUT IN VDD VDD) pch_mac w=4u l=0.18u" in deck
    assert "nmos2v_mac" not in deck
    assert "pmos2v_mac" not in deck
