from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from virtuoso_design_agent.cascode_seed import (
    CascodeSeedPolicy,
    build_task_from_cascode_seed,
    derive_cascode_seed,
)
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import EvidenceSource, TaskSpec
from virtuoso_design_agent.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]


def _source_run_payload() -> dict:
    parameters = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 20_000.0,
        "bias_v": 0.4,
        "vdd_v": 0.9,
    }
    metrics = {
        "drain_current_ua": 20.0,
        "vgs_v": 0.4,
        "vds_v": 0.3,
        "vdsat_v": 0.1,
        "saturation_margin_v": 0.2,
        "saturation_region": 1.0,
    }
    metric_sources = {
        "drain_current_ua": "eda_result",
        "vgs_v": "eda_result",
        "vds_v": "eda_result",
        "vdsat_v": "eda_result",
        "saturation_margin_v": "eda_result",
        "saturation_region": "software_inference",
    }
    details = {
        "parameters": parameters,
        "metrics": metrics,
        "metric_sources": metric_sources,
        "analysis_complete": True,
        "evidence": {
            "schematic_readback": {
                "source": "bridge_readback",
                "target": {
                    "library": "vb_pdk_smoke",
                    "cell": "vda_cs_cascode_gate_001",
                    "view": "schematic",
                },
                "semantic_parameters": {
                    "device_width_um": 1.0,
                    "length_um": 0.03,
                    "load_resistance_ohm": 20_000.0,
                },
                "device_geometry": {
                    "finger_width_um": 1.0,
                    "fingers": 1.0,
                    "multiplicity": 1.0,
                    "total_width_um": 1.0,
                },
                "topology_variant": "common_source",
            },
            "netlist": {
                "source": "eda_result",
                "generator": "Cadence si -batch",
                "parameter_consistency": "matched",
                "instances": {
                    "MN0": {
                        "nodes": ["OUT", "IN", "VSS", "VSS"],
                        "model": "nch_lvt_mac",
                        "finger_width_um": 1.0,
                        "fingers": 1.0,
                        "multiplicity": 1.0,
                        "total_width_um": 1.0,
                        "length_um": 0.03,
                    },
                    "RD0": {
                        "nodes": ["VDD", "OUT"],
                        "model": "resistor",
                        "resistance_ohm": 20_000.0,
                    },
                },
            },
            "operating_point": {
                "source": "eda_result",
                "device_values": {
                    "ids_a": 20e-6,
                    "vgs_v": 0.4,
                    "vds_v": 0.3,
                    "vdsat_v": 0.1,
                },
            },
        },
    }
    return {
        "schema_version": 1,
        "task_id": "common-source-cascode-seed-op",
        "plan_token": "0123456789abcdef",
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": "2026-07-26T00:00:00Z",
        "finished_at": "2026-07-26T00:01:00Z",
        "actions": [
            {
                "action": "bridge.probe",
                "status": "succeeded",
                "started_at": "2026-07-26T00:00:01Z",
                "finished_at": "2026-07-26T00:00:02Z",
                "evidence_source": "bridge_readback",
                "details": {
                    "connected": True,
                    "bridge_version": "0.7.0",
                    "profile": "nics4304_tsmc28",
                },
            },
            {
                "action": "simulation.candidate.1",
                "status": "succeeded",
                "started_at": "2026-07-26T00:00:10Z",
                "finished_at": "2026-07-26T00:00:50Z",
                "evidence_source": "eda_result",
                "details": details,
            }
        ],
        "candidates": [
            {
                "index": 1,
                "parameters": parameters,
                "metrics": metrics,
                "constraints": [],
                "feasible": True,
                "total_violation": 0.0,
                "evidence_source": "eda_result",
                "metric_sources": metric_sources,
                "analysis_complete": True,
            }
        ],
        "selected_parameters": parameters,
        "selected_metrics": metrics,
        "notes": [],
    }


def _write_source_run(path: Path) -> str:
    path.write_text(json.dumps(_source_run_payload(), indent=2), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(source_sha256: str) -> CascodeSeedPolicy:
    return CascodeSeedPolicy(
        id="cascode-seed-test",
        expected_source_task_id="common-source-cascode-seed-op",
        expected_source_run_sha256=source_sha256,
        source_candidate_index=1,
        pdk_profile="nics4304_tsmc28",
        expected_device_model="nch_lvt_mac",
        lower_saturation_margin_v=0.05,
        width_ratios=[1.0, 2.0],
        bias_offsets_v=[0.0, 0.02],
        maximum_candidates=4,
    )


def _task_template(path: Path, *, analysis: str = "dc") -> None:
    task = {
        "schema_version": 1,
        "id": f"common-source-cascode-{analysis}-seeded",
        "operation": "design.tune",
        "circuit": "common_source",
        "target": {
            "library": "vb_pdk_smoke",
            "cell": "vda_cs_cascode_gate_001",
            "view": "schematic",
        },
        "pdk_profile": "nics4304_tsmc28",
        "analysis": analysis,
        "parameters": {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.4,
            "vdd_v": 0.9,
        },
        "constraints": [
            {"metric": "saturation_region", "relation": ">=", "value": 1.0},
            {
                "metric": "input_device_saturation_margin_v",
                "relation": ">=",
                "value": 0.0,
            },
            {
                "metric": "cascode_saturation_margin_v",
                "relation": ">=",
                "value": 0.0,
            },
        ],
        "objective": {
            "metric": "stack_lower_saturation_headroom_v",
            "goal": "maximize",
        },
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vb_pdk_smoke",
            "required_cell_prefix": "vda_",
            "replace_existing": False,
        },
        "limits": {"max_iterations": 4, "timeout_seconds": 600},
    }
    if analysis == "ac":
        task["ac_sweep"] = {
            "start_hz": 1e3,
            "stop_hz": 1e12,
            "points_per_decade": 30,
        }
        task["parameters"]["load_ff"] = 2.0
        task["constraints"].extend(
            [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 1.0,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e6,
                },
            ]
        )
        task["objective"] = {
            "metric": "gain_bandwidth_product_hz",
            "goal": "maximize",
        }
    path.write_text(json.dumps(task, indent=2), encoding="utf-8")


def test_cascode_seed_uses_real_op_equations_and_emits_atomic_tuples(tmp_path) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)

    result = derive_cascode_seed(_policy(source_sha256), source)

    assert result.source_measurement_evidence is EvidenceSource.EDA_RESULT
    assert result.seed_evidence is EvidenceSource.SOFTWARE_INFERENCE
    assert result.operating_point.estimated_threshold_v == pytest.approx(0.3)
    assert result.operating_point.target_internal_node_v == pytest.approx(0.15)
    assert result.declared_combinations == result.emitted_combinations == 4
    assert result.continuous_optimum_claim is False
    assert result.global_optimum_claim is False
    candidates = result.candidate_set.candidates
    assert candidates[0].parameters == {
        "cascode_width_um": pytest.approx(1.0),
        "cascode_length_um": pytest.approx(0.03),
        "cascode_bias_v": pytest.approx(0.55),
    }
    assert candidates[1].parameters["cascode_bias_v"] == pytest.approx(0.57)
    assert candidates[2].parameters["cascode_width_um"] == pytest.approx(2.0)
    assert candidates[2].parameters["cascode_bias_v"] == pytest.approx(0.52)
    assert result.candidate_set.source.bindings == {
        "policy_sha256": result.policy_sha256,
        "source_run_sha256": source_sha256,
    }


@pytest.mark.parametrize("analysis", ["dc", "ac"])
def test_cascode_seed_compiles_same_atomic_domain_into_dc_and_ac_tasks(
    tmp_path, analysis: str
) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)
    result = derive_cascode_seed(_policy(source_sha256), source)
    result_path = tmp_path / "seed-result.json"
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    template = tmp_path / f"{analysis}-template.json"
    _task_template(template, analysis=analysis)

    task = build_task_from_cascode_seed(result_path, template)
    plan = build_plan(task)

    assert task.candidate_set is not None
    assert task.candidate_set.candidates == result.candidate_set.candidates
    assert task.candidate_set.source.generator == "vda.cascode-seed"
    assert set(task.candidate_set.source.bindings) == {
        "policy_sha256",
        "source_run_sha256",
        "cascode_seed_result_sha256",
        "task_template_sha256",
    }
    assert plan.requires_remote_compute
    assert plan.requires_remote_write


def test_cascode_seed_rejects_hash_topology_and_template_drift(tmp_path) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)
    policy = _policy(source_sha256)
    payload = _source_run_payload()
    payload["actions"][1]["details"]["evidence"]["schematic_readback"][
        "topology_variant"
    ] = "source_degenerated_common_source"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="run SHA-256 mismatch"):
        derive_cascode_seed(policy, source)

    changed_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="source topology mismatch"):
        derive_cascode_seed(
            policy.model_copy(update={"expected_source_run_sha256": changed_sha256}),
            source,
        )

    source_sha256 = _write_source_run(source)
    result = derive_cascode_seed(_policy(source_sha256), source)
    result_path = tmp_path / "seed-result.json"
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    template = tmp_path / "dc-template.json"
    _task_template(template)
    raw = json.loads(template.read_text(encoding="utf-8"))
    raw["parameters"]["bias_v"] = 0.45
    template.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="fixed parameters changed"):
        build_task_from_cascode_seed(result_path, template)


def test_cascode_seed_rejects_profile_and_device_count_drift(tmp_path) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)

    with pytest.raises(ValueError, match="PDK profile mismatch"):
        derive_cascode_seed(
            _policy(source_sha256).model_copy(update={"pdk_profile": "wrong_profile"}),
            source,
        )

    payload = _source_run_payload()
    geometry = payload["actions"][1]["details"]["evidence"][
        "schematic_readback"
    ]["device_geometry"]
    geometry["fingers"] = 2.0
    geometry["total_width_um"] = 2.0
    mn0 = payload["actions"][1]["details"]["evidence"]["netlist"]["instances"][
        "MN0"
    ]
    mn0["fingers"] = 2.0
    mn0["total_width_um"] = 2.0
    source.write_text(json.dumps(payload), encoding="utf-8")
    changed_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="unit source fingers and multiplicity"):
        derive_cascode_seed(_policy(changed_sha256), source)


def test_cascode_seed_rejects_quantized_duplicate_points(tmp_path) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)
    policy = _policy(source_sha256).model_copy(
        update={"bias_offsets_v": [0.0, 0.001], "bias_grid_v": 0.01}
    )

    with pytest.raises(ValueError, match="quantization collapsed"):
        derive_cascode_seed(policy, source)


def test_cascode_seed_cli_writes_result_and_executable_task(
    tmp_path, capsys
) -> None:
    source = tmp_path / "source-run.json"
    source_sha256 = _write_source_run(source)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        _policy(source_sha256).model_dump_json(indent=2), encoding="utf-8"
    )
    result_path = tmp_path / "seed-result.json"

    assert (
        main(
            [
                "cascode-seed",
                str(policy_path),
                str(source),
                "--output",
                str(result_path),
            ]
        )
        == 0
    )
    template = tmp_path / "dc-template.json"
    _task_template(template)
    task_path = tmp_path / "dc-task.json"
    assert (
        main(
            [
                "candidate-task-from-cascode-seed",
                str(result_path),
                str(template),
                "--output",
                str(task_path),
            ]
        )
        == 0
    )

    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["candidate_set"]["source"]["generator"] == "vda.cascode-seed"
    assert len(task["candidate_set"]["candidates"]) == 4
    assert "physics-seeded" in capsys.readouterr().out


def test_live_gate_examples_bind_generated_contracts_and_all_plan() -> None:
    bindings = [
        (
            "common-source-cascode-forward.bridge.json",
            "common-source-add-cascode.contract.json",
        ),
        (
            "common-source-master-recovery-fault.bridge.json",
            "common-source-master-recovery-fault.contract.json",
        ),
    ]
    task_dir = ROOT / "examples" / "tasks"
    contract_dir = ROOT / "examples" / "topology"
    for task_name, contract_name in bindings:
        task_payload = json.loads((task_dir / task_name).read_text(encoding="utf-8"))
        contract_payload = json.loads(
            (contract_dir / contract_name).read_text(encoding="utf-8")
        )
        assert task_payload["topology_delta"]["contract"] == contract_payload
        if task_name == "common-source-cascode-forward.bridge.json":
            assert task_payload["topology_delta"][
                "expected_output_placement_sha256"
            ] == "00af355e590acefe3857987692020320110f693cabff0f724c1f0b11a00774ee"

    task_names = [
        "common-source-cascode-create.bridge.json",
        "common-source-cascode-inspect.bridge.json",
        "common-source-cascode-seed-op.bridge.json",
        "common-source-cascode-roundtrip-baseline-ac.bridge.json",
        "common-source-cascode-geometry-apply.bridge.json",
        "common-source-cascode-forward.bridge.json",
        "common-source-cascode-inverse.bridge.json",
        "common-source-master-recovery-create.bridge.json",
        "common-source-master-recovery-inspect.bridge.json",
        "common-source-master-recovery-fault.bridge.json",
        "common-source-master-recovery-manual-inverse.bridge.json",
    ]
    for task_name in task_names:
        task = TaskSpec.model_validate_json(
            (task_dir / task_name).read_text(encoding="utf-8")
        )
        plan = build_plan(task)
        assert plan.task_id == task.id
