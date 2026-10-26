from __future__ import annotations

from pathlib import Path

import pytest

from virtuoso_design_agent.adapters.bridge_worker import (
    _assert_inverter,
    _common_source_instance_parameter_updates,
    _differential_pair_instance_parameter_updates,
    _inverter_testbench_deck,
    _mos_width_update,
    _parse_common_source_netlist,
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
    assert profile.mos_width_write_argument == "w"
    assert profile.model_section == "tt"
    assert set(profile.process_corners) == {"tt", "ff", "ss", "sf", "fs"}
    assert [item.section for item in profile.process_corners["tt"]] == [
        "tt",
        "stat_noise",
    ]
    assert profile.default_vdd_v == pytest.approx(1.8)
    assert profile.default_length_um == pytest.approx(0.18)
    assert profile.cds_lib_path == "/data/xum/Analog_VLSI/icfb/cds.lib"


def test_tsmc18_semantic_width_writes_use_total_width_cdf_argument() -> None:
    tsmc18 = load_pdk_profile("nics4304_tsmc18").model_dump(mode="json")
    tsmc28 = load_pdk_profile("nics4304_tsmc28").model_dump(mode="json")

    assert _mos_width_update(2.0, tsmc18) == {"w": "2u"}
    assert _mos_width_update(2.0, tsmc28) == {"wf": "2u"}
    assert _common_source_instance_parameter_updates(
        {"device_width_um": 2.0, "cascode_width_um": 3.0}, tsmc18
    ) == {"MN0": {"w": "2u"}, "MNCAS": {"w": "3u"}}
    assert _differential_pair_instance_parameter_updates(
        {
            "input_width_um": 2.0,
            "pmos_load_width_um": 4.0,
            "tail_width_um": 6.0,
        },
        tsmc18,
    ) == {
        "MN0": {"w": "2u"},
        "MN1": {"w": "2u"},
        "MP0": {"w": "4u"},
        "MP1": {"w": "4u"},
        "MNTAIL": {"w": "6u"},
    }


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

    main_model = (
        "/data/xum/Analog_VLSI/PDK_TSMC18_1809/models/spectre/"
        "cr018gpii_v1d0.scs"
    )
    assert f'include "{main_model}" section=tt' in deck
    assert f'include "{main_model}" section=stat_noise' in deck
    assert deck.index("section=tt") < deck.index("section=stat_noise")
    assert "MN0 (OUT IN 0 0) nch_mac w=2u l=0.18u" in deck
    assert "MP0 (OUT IN VDD VDD) pch_mac w=4u l=0.18u" in deck
    assert "nmos2v_mac" not in deck
    assert "pmos2v_mac" not in deck


def test_tsmc18_inverter_oa_testbench_uses_complete_default_model_bundle() -> None:
    profile = load_pdk_profile("nics4304_tsmc18").model_dump(mode="json")
    deck = _inverter_testbench_deck(
        profile,
        {"vdd_v": 1.8, "load_ff": 10.0},
        "/data/xum/virtuoso_bridge_smoke/tsmc18/netlist",
    )

    assert "section=tt" in deck
    assert "section=stat_noise" in deck
    assert deck.index("section=tt") < deck.index("section=stat_noise")
    assert 'include "/data/xum/virtuoso_bridge_smoke/tsmc18/netlist"' in deck


def test_tsmc18_common_source_si_preserves_theory_seeded_geometry() -> None:
    profile = load_pdk_profile("nics4304_tsmc18").model_dump(mode="json")
    parsed = _parse_common_source_netlist(
        "MN0 (OUT IN VSS VSS) nch_mac l=360n w=8u nf=1 multi=1\n"
        "RD0 (VDD OUT) resistor r=21.2k\n",
        profile,
    )

    assert parsed["semantic_parameters"] == {
        "device_width_um": pytest.approx(8.0),
        "length_um": pytest.approx(0.36),
        "load_resistance_ohm": pytest.approx(21200.0),
    }


@pytest.mark.parametrize("stage", ["create", "dc", "ac", "refine", "final-ac"])
def test_tsmc18_common_source_spec_tasks_keep_scopes_and_fixed_goals(stage) -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "tasks" / (
        f"tsmc18-common-source-spec-{stage}.bridge.json"
    )
    task = TaskSpec.model_validate_json(path.read_text(encoding="utf-8"))
    plan = build_plan(task)

    assert task.pdk_profile == "nics4304_tsmc18"
    assert task.target.library == task.safety.allowed_library == "Analog_VLSI"
    assert task.target.cell == "vda_tsmc18_cs_spec_001"
    assert task.safety.replace_existing is False
    assert plan.requires_remote_write is (stage in {"create", "refine"})
    assert plan.requires_remote_compute is (stage not in {"create", "refine"})
    if stage == "refine":
        assert task.parameters == {"load_resistance_ohm": 23500.0}
        return
    assert task.parameters["device_width_um"] == 8.0
    assert task.parameters["length_um"] == 0.36
    assert task.parameters["load_resistance_ohm"] == (
        23500.0 if stage == "final-ac" else 21200.0
    )
    if stage != "create":
        assert task.parameters["bias_v"] == 0.58
        assert task.operating_conditions[0].vdd_v == 1.8
        assert task.operating_conditions[0].temperature_c == 27.0
        assert any(
            item.metric == "dc_supply_power_uw" and item.value == 100.0
            for item in task.constraints
        )
    if stage in {"ac", "final-ac"}:
        assert task.parameters["load_ff"] == 50.0
        assert any(
            item.metric == "low_frequency_gain_db" and item.value == 20.0
            for item in task.constraints
        )
        assert any(
            item.metric == "bandwidth_3db_hz" and item.value == 100_000_000.0
            for item in task.constraints
        )
        if stage == "final-ac":
            original = TaskSpec.model_validate_json(
                path.with_name("tsmc18-common-source-spec-ac.bridge.json").read_text(
                    encoding="utf-8"
                )
            )
            assert task.constraints == original.constraints
            assert task.ac_sweep == original.ac_sweep
            assert task.operating_conditions == original.operating_conditions
