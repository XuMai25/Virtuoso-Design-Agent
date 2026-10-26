from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.netlist_preview import NetlistPreviewSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.preview_compile import build_preview_task_from_candidates


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _candidate_source() -> dict:
    return {
        "pdk_profile": "nics4304_tsmc28",
        "candidate_set": {
            "source": {
                "generator": "vda.cascode-seed",
                "id": "cascode-policy-1",
                "bindings": {"source_run_sha256": "a" * 64},
                "evidence_source": "software_inference",
            },
            "candidates": [
                {
                    "id": "seed-a",
                    "parameters": {
                        "source_width_um": 1.0,
                        "cascode_width_um": 0.75,
                        "cascode_length_um": 0.03,
                        "cascode_bias_v": 0.525,
                    },
                },
                {
                    "id": "seed-b",
                    "parameters": {
                        "source_width_um": 1.0,
                        "cascode_width_um": 1.0,
                        "cascode_length_um": 0.04,
                        "cascode_bias_v": 0.55,
                    },
                },
            ],
        },
    }


def _compile_policy() -> dict:
    return {
        "schema_version": 1,
        "id": "cascode-preview-compile",
        "expected_candidate_generator": "vda.cascode-seed",
        "expected_candidate_source_id": "cascode-policy-1",
        "expected_pdk_profile": "nics4304_tsmc28",
        "candidate_ids": ["seed-b", "seed-a"],
        "template_variant_id": "cascode_template",
        "variant_id_prefix": "cascode_candidate",
        "fixed_parameters": {"source_width_um": 1.0},
        "bindings": [
            {
                "parameter": "cascode_width_um",
                "element_kind": "mosfet",
                "element": "MNCAS",
                "field": "width_um",
            },
            {
                "parameter": "cascode_length_um",
                "element_kind": "mosfet",
                "element": "MNCAS",
                "field": "length_um",
            },
            {
                "parameter": "cascode_bias_v",
                "element_kind": "voltage_source",
                "element": "VCAS_SRC",
                "field": "dc_v",
            },
        ],
    }


def _task_template() -> dict:
    return {
        "schema_version": 1,
        "id": "compiled-cascode-preview",
        "operation": "simulation.run",
        "circuit": "netlist_preview",
        "pdk_profile": "nics4304_tsmc28",
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e4,
            "stop_hz": 1e12,
            "points_per_decade": 20,
        },
        "netlist_preview": {
            "schema_version": 1,
            "id": "common-source-cascode-compiled-preview",
            "input_source": "VIN_SRC",
            "supply_sources": ["VDD_SRC"],
            "voltage_sources": [
                {
                    "name": "VDD_SRC",
                    "positive": "VDD",
                    "negative": "0",
                    "dc_v": 0.9,
                },
                {
                    "name": "VIN_SRC",
                    "positive": "IN",
                    "negative": "0",
                    "dc_v": 0.35,
                    "ac_magnitude_v": 1.0,
                },
            ],
            "resistors": [
                {
                    "name": "RLOAD",
                    "positive": "VDD",
                    "negative": "OUT",
                    "resistance_ohm": 20000.0,
                }
            ],
            "capacitors": [
                {
                    "name": "CLOAD",
                    "positive": "OUT",
                    "negative": "0",
                    "capacitance_f": 2e-15,
                }
            ],
            "variants": [
                {
                    "id": "common_source",
                    "output_positive": "OUT",
                    "mosfets": [
                        {
                            "name": "MN0",
                            "polarity": "nmos",
                            "drain": "OUT",
                            "gate": "IN",
                            "source": "0",
                            "bulk": "0",
                            "width_um": 1.0,
                            "length_um": 0.03,
                        }
                    ],
                },
                {
                    "id": "cascode_template",
                    "output_positive": "OUT",
                    "mosfets": [
                        {
                            "name": "MN0",
                            "polarity": "nmos",
                            "drain": "NCAS",
                            "gate": "IN",
                            "source": "0",
                            "bulk": "0",
                            "width_um": 1.0,
                            "length_um": 0.03,
                        },
                        {
                            "name": "MNCAS",
                            "polarity": "nmos",
                            "drain": "OUT",
                            "gate": "VCAS",
                            "source": "NCAS",
                            "bulk": "0",
                            "width_um": 1.0,
                            "length_um": 0.03,
                        },
                    ],
                    "voltage_sources": [
                        {
                            "name": "VCAS_SRC",
                            "positive": "VCAS",
                            "negative": "0",
                            "dc_v": 0.5,
                        }
                    ],
                },
            ],
        },
        "constraints": [
            {
                "metric": "common_source__all_mos_saturation_region",
                "relation": ">=",
                "value": 1.0,
            },
            {
                "metric": "cascode_template__all_mos_saturation_region",
                "relation": ">=",
                "value": 1.0,
            },
        ],
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": False,
            "replace_existing": False,
        },
        "limits": {"max_iterations": 1, "timeout_seconds": 600},
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    policy = tmp_path / "policy.json"
    source = tmp_path / "source.json"
    template = tmp_path / "template.json"
    _write_json(policy, _compile_policy())
    _write_json(source, _candidate_source())
    _write_json(template, _task_template())
    return policy, source, template


def test_candidate_compiler_expands_in_declared_order_and_binds_sources(
    tmp_path: Path,
) -> None:
    policy, source, template = _write_inputs(tmp_path)

    task = build_preview_task_from_candidates(policy, source, template)
    repeated = build_preview_task_from_candidates(policy, source, template)
    plan = build_plan(task)

    assert task.model_dump(mode="json") == repeated.model_dump(mode="json")
    assert task.target is None
    assert plan.requires_remote_compute is True
    assert plan.requires_remote_write is False
    assert task.netlist_preview is not None
    spec = task.netlist_preview
    assert [variant.id for variant in spec.variants] == [
        "common_source",
        "cascode_candidate_001",
        "cascode_candidate_002",
    ]
    assert spec.variant_source_ids == {
        "cascode_candidate_001": "seed-b",
        "cascode_candidate_002": "seed-a",
    }
    assert spec.source_bindings_evidence_source == "software_inference"
    assert spec.variant_source_ids_evidence_source == "software_inference"
    first = spec.variant("cascode_candidate_001")
    assert first.mosfets[1].width_um == pytest.approx(1.0)
    assert first.mosfets[1].length_um == pytest.approx(0.04)
    assert first.voltage_sources[0].dc_v == pytest.approx(0.55)
    assert [constraint.metric for constraint in task.constraints] == [
        "common_source__all_mos_saturation_region",
        "cascode_candidate_001__all_mos_saturation_region",
        "cascode_candidate_002__all_mos_saturation_region",
    ]
    assert spec.source_bindings == {
        "candidate_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "preview_compile_policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
        "preview_task_template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
    }


def test_candidate_compiler_accepts_theory_seed_source(tmp_path: Path) -> None:
    policy, source, template = _write_inputs(tmp_path)
    theory_source = {
        "pdk_profile": "nics4304_tsmc28",
        "theory_seed": {
            "source": {
                "generator": "vda.theory",
                "policy_id": "theory-policy-1",
                "policy_sha256": "1" * 64,
                "theory_request_id": "request-1",
                "theory_request_sha256": "2" * 64,
                "theory_result_sha256": "3" * 64,
                "device_data_source": "pdk_characterization",
                "optimality_classification": "finite_declared_domain",
                "declared_theory_combinations": 2,
                "evaluated_theory_combinations": 2,
                "theory_domain_exhausted": True,
                "selection_strategy": "ranked_then_log_maximin",
                "width_quantization": "decimal_places",
                "continuous_optimum_claim": False,
                "global_optimum_claim": False,
                "evidence_source": "software_inference",
            },
            "candidates": [
                {
                    "id": "theory-a",
                    "source_candidate_id": "theory-result-a",
                    "parameters": {
                        "source_width_um": 1.0,
                        "cascode_width_um": 0.8,
                        "cascode_length_um": 0.03,
                        "cascode_bias_v": 0.53,
                    },
                    "evidence_source": "software_inference",
                }
            ],
        },
    }
    _write_json(source, theory_source)
    compile_policy = _compile_policy()
    compile_policy.update(
        {
            "expected_candidate_generator": "vda.theory",
            "expected_candidate_source_id": "theory-policy-1",
            "candidate_ids": ["theory-a"],
        }
    )
    _write_json(policy, compile_policy)

    task = build_preview_task_from_candidates(policy, source, template)

    assert task.netlist_preview is not None
    assert task.netlist_preview.variant_source_ids == {
        "cascode_candidate_001": "theory-a"
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda policy, source, template: policy.update(
                {"expected_candidate_generator": "wrong.generator"}
            ),
            "candidate generator mismatch",
        ),
        (
            lambda policy, source, template: policy.update(
                {"expected_candidate_source_id": "wrong-source"}
            ),
            "candidate source id mismatch",
        ),
        (
            lambda policy, source, template: source.update(
                {"pdk_profile": "wrong_pdk"}
            ),
            "candidate source PDK profile",
        ),
        (
            lambda policy, source, template: policy["bindings"].pop(),
            "unaccounted",
        ),
        (
            lambda policy, source, template: policy["bindings"].append(
                dict(policy["bindings"][0])
            ),
            "cannot write one target field twice",
        ),
        (
            lambda policy, source, template: policy["fixed_parameters"].update(
                {"source_width_um": 1.1}
            ),
            "fixed candidate parameter",
        ),
        (
            lambda policy, source, template: policy["bindings"][0].update(
                {"element": "MISSING"}
            ),
            "does not contain exactly one",
        ),
        (
            lambda policy, source, template: template.update(
                {
                    "objective": {
                        "metric": "cascode_template__gain_bandwidth_product_hz",
                        "goal": "maximize",
                    }
                }
            ),
            "placeholder-specific objective",
        ),
    ],
)
def test_candidate_compiler_rejects_drift_and_ambiguity(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    policy_payload = _compile_policy()
    source_payload = _candidate_source()
    template_payload = _task_template()
    mutation(policy_payload, source_payload, template_payload)
    policy = tmp_path / "policy.json"
    source = tmp_path / "source.json"
    template = tmp_path / "template.json"
    _write_json(policy, policy_payload)
    _write_json(source, source_payload)
    _write_json(template, template_payload)

    with pytest.raises(ValueError, match=message):
        build_preview_task_from_candidates(policy, source, template)


def test_candidate_compiler_rejects_raw_cdf_updates(tmp_path: Path) -> None:
    policy_payload = _compile_policy()
    source_payload = _candidate_source()
    for index, candidate in enumerate(
        source_payload["candidate_set"]["candidates"], start=1
    ):
        candidate["instance_parameter_updates"] = [
            {"instance": "MNCAS", "parameters": {"w": f"{index}u"}}
        ]
    policy = tmp_path / "policy.json"
    source = tmp_path / "source.json"
    template = tmp_path / "template.json"
    _write_json(policy, policy_payload)
    _write_json(source, source_payload)
    _write_json(template, _task_template())

    with pytest.raises(ValueError, match="raw instance_parameter_updates"):
        build_preview_task_from_candidates(policy, source, template)


def test_preview_variant_source_ids_are_closed_over_declared_variants() -> None:
    payload = _task_template()["netlist_preview"]
    payload["variant_source_ids"] = {"unknown": "seed-a"}
    with pytest.raises(ValueError, match="unknown variants"):
        NetlistPreviewSpec.model_validate(payload)

    payload["variant_source_ids"] = {
        "common_source": "seed-a",
        "cascode_template": "seed-a",
    }
    with pytest.raises(ValueError, match="must be unique"):
        NetlistPreviewSpec.model_validate(payload)


def test_preview_candidate_cli_writes_a_runnable_task(
    tmp_path: Path,
    capsys,
) -> None:
    policy, source, template = _write_inputs(tmp_path)
    output = tmp_path / "compiled.json"

    assert (
        main(
            [
                "preview-task-from-candidates",
                str(policy),
                str(source),
                str(template),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "cascode_candidate_001" in capsys.readouterr().out
    task = TaskSpec.model_validate_json(output.read_text(encoding="utf-8"))
    assert build_plan(task).requires_remote_compute is True
