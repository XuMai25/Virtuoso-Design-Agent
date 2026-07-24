from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from virtuoso_design_agent.characterization import (
    MosCharacterizationArtifact,
    interpolate_mos_characterization_point,
)
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import RunStatus
from virtuoso_design_agent.small_signal_validation import (
    SmallSignalCircuitValidationPolicy,
    validate_common_source_small_signal_runs,
)


_NOW = "2026-07-24T00:00:00Z"
_TARGET = {"library": "vda_test", "cell": "vda_cs_gate7b", "view": "schematic"}


def _artifact() -> dict:
    points = []
    for vgs in (0.3, 0.5):
        for vds in (0.4, 0.6):
            for vsb in (0.0, 0.16):
                points.append(
                    {
                        "id": f"n-{vgs}-{vds}-{vsb}",
                        "model": "test_nmos",
                        "polarity": "nmos",
                        "length_um": 0.03,
                        "vgs_magnitude_v": vgs,
                        "vds_magnitude_v": vds,
                        "vsb_magnitude_v": vsb,
                        "vdsat_magnitude_v": 0.1,
                        "drain_current_density_a_per_um": 40e-6,
                        "gm_over_id_per_v": 10.0,
                        "gds_over_id_per_v": 0.5,
                        "gmb_over_id_per_v": 0.0,
                        "cgs_f_per_um": 0.0,
                        "cgd_f_per_um": 0.0,
                        "cgb_f_per_um": 0.0,
                        "cdb_f_per_um": 1e-12,
                        "csb_f_per_um": 0.0,
                    }
                )
    return {
        "schema_version": 1,
        "id": "test-mos-table",
        "source": "pdk_characterization",
        "source_artifact_sha256": "a" * 64,
        "pdk_profile": "test_pdk",
        "process_corner": "test_tt",
        "temperature_c": 27.0,
        "characterized_width_um": 1.0,
        "model_parameters_by_polarity": {
            "nmos": {"ad": "3.75e-14", "dfm_flag": "0"}
        },
        "raw_data_evidence_source": "eda_result",
        "normalized_point_evidence_source": "software_inference",
        "points": points,
    }


def _drain_bulk_charge_matrix(capacitance_f: float) -> dict[str, float]:
    matrix = {
        f"c{row}{column}": 0.0
        for row in ("g", "d", "s", "b")
        for column in ("g", "d", "s", "b")
    }
    matrix["cdd"] = capacitance_f
    matrix["cdb"] = -capacitance_f
    matrix["cbd"] = -capacitance_f
    matrix["cbb"] = capacitance_f
    return matrix


def _characterization_run() -> dict:
    return {
        "schema_version": 1,
        "task_id": "test-characterization",
        "plan_token": "char-token",
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": _NOW,
        "finished_at": _NOW,
        "actions": [
            {
                "action": "device.characterize",
                "status": "succeeded",
                "started_at": _NOW,
                "finished_at": _NOW,
                "evidence_source": "eda_result",
                "details": {
                    "task_id": "test-characterization",
                    "pdk_profile": "test_pdk",
                    "process_corner": "test_tt",
                    "temperature_c": 27.0,
                    "width_um": 1.0,
                    "model_parameters_by_polarity": {
                        "nmos": {"ad": "3.75e-14", "dfm_flag": "0"}
                    },
                    "raw_point_evidence_source": "eda_result",
                    "points": [{"id": "raw-point"}],
                    "tool_version": "test-spectre-1",
                    "evidence": {
                        "source": "eda_result",
                        "remote_run_root": "/data/xum/test-characterization",
                        "remote_simulation_dir": (
                            "/data/xum/test-characterization/simulator"
                        ),
                        "artifact_manifest_complete": True,
                        "manifest_sha256": "a" * 64,
                        "oa_access_performed": False,
                        "oa_write_performed": False,
                    },
                },
            },
            {
                "action": "device.characterize.validate",
                "status": "succeeded",
                "started_at": _NOW,
                "finished_at": _NOW,
                "evidence_source": "software_inference",
                "details": {
                    "artifact": _artifact(),
                    "raw_data_evidence_source": "eda_result",
                    "normalization_evidence_source": "software_inference",
                },
            }
        ],
    }


def _circuit_run() -> dict:
    frequencies = [10.0 ** (3.0 + index / 20.0) for index in range(121)]
    output_conductance = 20e-6 + 1.0 / 10_000.0
    load_capacitance = 1e-12 + 1e-15
    gain = 400e-6 / output_conductance
    bandwidth = output_conductance / (2.0 * math.pi * load_capacitance)
    metrics = {
        "low_frequency_gain_db": 20.0 * math.log10(gain),
        "low_frequency_phase_deg": 180.0,
        "bandwidth_3db_hz": bandwidth,
        "phase_at_bandwidth_deg": 135.0,
        "gain_bandwidth_product_hz": gain * bandwidth,
    }
    metric_sources = {name: "eda_result" for name in metrics}
    semantic = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 10_000.0,
    }
    geometry = {
        "finger_width_um": 1.0,
        "fingers": 1.0,
        "multiplicity": 1.0,
        "total_width_um": 1.0,
    }
    result = {
        "parameters": {**semantic, "bias_v": 0.4, "vdd_v": 0.9, "load_ff": 1.0},
        "metrics": metrics,
        "metric_sources": metric_sources,
        "analysis_complete": True,
        "analysis_issues": [],
        "analysis_warnings": [],
        "scalar_count": 100,
        "tool_version": "test-spectre-1",
        "warnings": [],
        "evidence": {
            "side_effects": {
                "oa_access_performed": True,
                "oa_write_performed": False,
                "remote_compute_performed": True,
            },
            "schematic_readback": {
                "source": "bridge_readback",
                "target": copy.deepcopy(_TARGET),
                "semantic_parameters": dict(semantic),
                "device_geometry": dict(geometry),
                "topology_variant": "common_source",
            },
            "netlist": {
                "source": "eda_result",
                "generator": "Cadence si -batch",
                "remote_path": "/data/xum/vda_gate7b/netlist",
                "sha256": "b" * 64,
                "semantic_parameters": dict(semantic),
                "device_geometry": dict(geometry),
                "topology_variant": "common_source",
                "parameter_consistency": "matched",
                "instances": {
                    "MN0": {
                        "nodes": ["OUT", "IN", "VSS", "VSS"],
                        "model": "test_nmos",
                        "netlist_width_um": 1.0,
                        "finger_width_um": 1.0,
                        "fingers": 1.0,
                        "multiplicity": 1.0,
                        "total_width_um": 1.0,
                        "length_um": 0.03,
                        "model_parameters": {
                            "ad": "3.75e-14",
                            "dfm_flag": "0",
                        },
                    },
                    "RD0": {
                        "nodes": ["VDD", "OUT"],
                        "model": "resistor",
                        "resistance_ohm": 10_000.0,
                    },
                },
            },
            "testbench": {
                "remote_path": "/data/xum/vda_gate7b/input_from_oa.scs",
                "sha256": "c" * 64,
                "values": {
                    "analysis": "ac",
                    "bias_v": 0.4,
                    "vdd_v": 0.9,
                    "load_ff": 1.0,
                    "ac_sweep": {
                        "start_hz": frequencies[0],
                        "stop_hz": frequencies[-1],
                        "points_per_decade": 20,
                        "reference_points": 5,
                        "max_reference_variation_db": 0.5,
                    },
                },
                "model_configuration": {
                    "source": "software_inference",
                    "profile": "test_pdk",
                    "process_corner": "test_tt",
                    "temperature_c": 27.0,
                },
            },
            "operating_point": {
                "source": "eda_result",
                "node_values_v": {"IN": 0.4, "OUT": 0.5, "VDD": 0.9, "VSS": 0.0},
                "device_values": {
                    "ids_a": 40e-6,
                    "vgs_v": 0.4,
                    "vds_v": 0.5,
                    "vdsat_v": 0.1,
                    "gm_s": 400e-6,
                    "gds_s": 20e-6,
                },
                "node_device_consistency": "matched",
                "kcl_consistency": "matched",
            },
            "ac_response": {
                "source": "eda_result",
                "extraction_source": "software_inference",
                "analysis_complete": True,
                "sample_count": len(frequencies),
                "frequency_hz": frequencies,
                "reference": {
                    "points": 5,
                    "variation_limit_db": 0.5,
                    "status": "flat",
                },
                "raw_files": {
                    "selection": "shallowest analysis-specific PSF file",
                    "ac": {
                        "relative_path": "ac.ac",
                        "size_bytes": 100,
                        "sha256": "d" * 64,
                    },
                },
            },
        },
    }
    return {
        "schema_version": 1,
        "task_id": "test-common-source",
        "plan_token": "circuit-token",
        "adapter": "virtuoso-bridge-subprocess",
        "status": "succeeded",
        "started_at": _NOW,
        "finished_at": _NOW,
        "actions": [
            {
                "action": "simulation.candidate.1",
                "status": "succeeded",
                "started_at": _NOW,
                "finished_at": _NOW,
                "evidence_source": "eda_result",
                "details": result,
            }
        ],
    }


def _source_degenerated_circuit_run() -> dict:
    circuit = _circuit_run()
    details = circuit["actions"][0]["details"]
    evidence = details["evidence"]
    source_resistance_ohm = 2_000.0
    source_voltage_v = 0.08
    drain_voltage_v = 0.5

    details["parameters"]["bias_v"] = 0.48
    details["parameters"]["source_resistance_ohm"] = source_resistance_ohm
    for section in (evidence["schematic_readback"], evidence["netlist"]):
        section["semantic_parameters"][
            "source_resistance_ohm"
        ] = source_resistance_ohm
        section["topology_variant"] = "source_degenerated_common_source"
    evidence["netlist"]["instances"]["MN0"]["nodes"] = [
        "OUT",
        "IN",
        "NSRC",
        "VSS",
    ]
    evidence["netlist"]["instances"]["RS0"] = {
        "nodes": ["NSRC", "VSS"],
        "model": "resistor",
        "resistance_ohm": source_resistance_ohm,
    }
    evidence["testbench"]["values"]["bias_v"] = 0.48
    operating_point = evidence["operating_point"]
    operating_point["node_values_v"] = {
        "IN": 0.48,
        "OUT": drain_voltage_v,
        "VDD": 0.9,
        "VSS": 0.0,
        "NSRC": source_voltage_v,
    }
    operating_point["device_values"]["vgs_v"] = 0.4
    operating_point["device_values"]["vds_v"] = (
        drain_voltage_v - source_voltage_v
    )
    operating_point["source_degeneration_consistency"] = "matched"

    gm_s = operating_point["device_values"]["gm_s"]
    gds_s = operating_point["device_values"]["gds_s"]
    load_resistance_ohm = details["parameters"]["load_resistance_ohm"]
    gain = gm_s * load_resistance_ohm / (
        1.0
        + gm_s * source_resistance_ohm
        + gds_s * (load_resistance_ohm + source_resistance_ohm)
    )
    effective_gds_s = gds_s / (
        1.0 + (gm_s + gds_s) * source_resistance_ohm
    )
    output_conductance_s = 1.0 / load_resistance_ohm + effective_gds_s
    load_capacitance_f = 1e-12 + 1e-15
    bandwidth_hz = output_conductance_s / (
        2.0 * math.pi * load_capacitance_f
    )
    details["metrics"].update(
        {
            "low_frequency_gain_db": 20.0 * math.log10(gain),
            "low_frequency_phase_deg": 180.0,
            "bandwidth_3db_hz": bandwidth_hz,
            "phase_at_bandwidth_deg": 135.0,
            "gain_bandwidth_product_hz": gain * bandwidth_hz,
        }
    )
    return circuit


def _policy() -> dict:
    return {
        "schema_version": 1,
        "id": "test-gate7b",
        "expected_target": copy.deepcopy(_TARGET),
        "expected_pdk_profile": "test_pdk",
        "expected_process_corner": "test_tt",
        "expected_temperature_c": 27.0,
        "expected_vdd_v": 0.9,
        "expected_topology_variant": "common_source",
        "require_exact_characterization_width": True,
        "require_exact_model_parameters": True,
        "thresholds": {
            "maximum_device_dc_relative_error": 0.01,
            "maximum_gain_error_db": 0.05,
            "maximum_phase_error_deg": 2.0,
            "maximum_bandwidth_relative_error": 0.02,
            "maximum_gbw_relative_error": 0.02,
        },
    }


def _write_inputs(
    tmp_path: Path,
    circuit: dict | None = None,
    characterization: dict | None = None,
) -> tuple[Path, Path]:
    characterization_path = tmp_path / "characterization.json"
    circuit_path = tmp_path / "circuit.json"
    characterization_path.write_text(
        json.dumps(characterization or _characterization_run()), encoding="utf-8"
    )
    circuit_path.write_text(json.dumps(circuit or _circuit_run()), encoding="utf-8")
    return characterization_path, circuit_path


def _role_characterization_run(
    role: str,
    *,
    model: str,
    polarity: str,
    width_um: float,
    model_parameters: dict[str, str],
    hash_character: str,
    current_density_a_per_um: float,
    gm_over_id_per_v: float,
    gds_over_id_per_v: float,
) -> dict:
    run = _characterization_run()
    task_id = f"test-{role}-characterization"
    artifact = run["actions"][1]["details"]["artifact"]
    artifact["id"] = f"test-{role}-table"
    artifact["source_artifact_sha256"] = hash_character * 64
    artifact["characterized_width_um"] = width_um
    artifact["model_parameters_by_polarity"] = {
        polarity: copy.deepcopy(model_parameters)
    }
    for index, point in enumerate(artifact["points"]):
        point["id"] = f"{role}-{index}"
        point["model"] = model
        point["polarity"] = polarity
        point["vds_magnitude_v"] = 0.1 if index % 4 < 2 else 0.5
        point["drain_current_density_a_per_um"] = current_density_a_per_um
        point["gm_over_id_per_v"] = gm_over_id_per_v
        point["gds_over_id_per_v"] = gds_over_id_per_v
    raw = run["actions"][0]["details"]
    run["task_id"] = task_id
    raw["task_id"] = task_id
    raw["width_um"] = width_um
    raw["model_parameters_by_polarity"] = {
        polarity: copy.deepcopy(model_parameters)
    }
    raw["evidence"]["manifest_sha256"] = hash_character * 64
    raw["evidence"]["remote_run_root"] = f"/data/xum/{task_id}"
    raw["evidence"]["remote_simulation_dir"] = (
        f"/data/xum/{task_id}/simulator"
    )
    return run


def _differential_characterizations() -> list[dict]:
    return [
        _role_characterization_run(
            "input-nmos",
            model="test_nmos",
            polarity="nmos",
            width_um=1.5,
            model_parameters={"ad": "input"},
            hash_character="1",
            current_density_a_per_um=40e-6,
            gm_over_id_per_v=10.0,
            gds_over_id_per_v=0.5,
        ),
        _role_characterization_run(
            "load-pmos",
            model="test_pmos",
            polarity="pmos",
            width_um=2.4,
            model_parameters={"ad": "load"},
            hash_character="2",
            current_density_a_per_um=25e-6,
            gm_over_id_per_v=8.0,
            gds_over_id_per_v=0.4,
        ),
        _role_characterization_run(
            "tail-nmos",
            model="test_nmos",
            polarity="nmos",
            width_um=0.8,
            model_parameters={"ad": "tail"},
            hash_character="3",
            current_density_a_per_um=150e-6,
            gm_over_id_per_v=8.0,
            gds_over_id_per_v=0.6,
        ),
    ]


def _differential_circuit_run() -> dict:
    circuit = _circuit_run()
    circuit["task_id"] = "test-differential-pair"
    details = circuit["actions"][0]["details"]
    evidence = details["evidence"]
    target = {"library": "vda_test", "cell": "vda_diffpair", "view": "schematic"}
    semantic = {
        "input_width_um": 1.5,
        "length_um": 0.03,
        "pmos_load_width_um": 2.4,
        "pmos_load_length_um": 0.03,
        "tail_width_um": 0.8,
        "tail_length_um": 0.03,
    }
    input_geometry = {
        "finger_width_um": 1.5,
        "fingers": 1.0,
        "multiplicity": 1.0,
        "total_width_um": 1.5,
    }
    topology = "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
    details["parameters"] = {
        **semantic,
        "tail_bias_v": 0.4,
        "common_mode_v": 0.5,
        "vdd_v": 0.9,
        "load_ff": 1.0,
    }
    details["metrics"] = {
        "differential_low_frequency_gain_db": 10.0,
        "differential_low_frequency_phase_deg": 0.0,
        "differential_bandwidth_3db_hz": 1.0,
        "differential_phase_at_bandwidth_deg": -45.0,
        "differential_gain_bandwidth_product_hz": 1.0,
    }
    details["metric_sources"] = {
        name: "eda_result" for name in details["metrics"]
    }
    evidence["schematic_readback"] = {
        "source": "bridge_readback",
        "target": target,
        "semantic_parameters": copy.deepcopy(semantic),
        "device_geometry": copy.deepcopy(input_geometry),
        "topology_variant": topology,
    }
    instances = {
        "MN0": {
            "nodes": ["OUTP", "INP", "TAIL", "VSS"],
            "model": "test_nmos",
            "netlist_width_um": 1.5,
            "finger_width_um": 1.5,
            "fingers": 1.0,
            "multiplicity": 1.0,
            "total_width_um": 1.5,
            "length_um": 0.03,
            "model_parameters": {"ad": "input"},
        },
        "MN1": {
            "nodes": ["OUTN", "INN", "TAIL", "VSS"],
            "model": "test_nmos",
            "netlist_width_um": 1.5,
            "finger_width_um": 1.5,
            "fingers": 1.0,
            "multiplicity": 1.0,
            "total_width_um": 1.5,
            "length_um": 0.03,
            "model_parameters": {"ad": "input"},
        },
        "MP0": {
            "nodes": ["OUTP", "OUTP", "VDD", "VDD"],
            "model": "test_pmos",
            "netlist_width_um": 2.4,
            "finger_width_um": 2.4,
            "fingers": 1.0,
            "multiplicity": 1.0,
            "total_width_um": 2.4,
            "length_um": 0.03,
            "model_parameters": {"ad": "load"},
        },
        "MP1": {
            "nodes": ["OUTN", "OUTP", "VDD", "VDD"],
            "model": "test_pmos",
            "netlist_width_um": 2.4,
            "finger_width_um": 2.4,
            "fingers": 1.0,
            "multiplicity": 1.0,
            "total_width_um": 2.4,
            "length_um": 0.03,
            "model_parameters": {"ad": "load"},
        },
        "MNTAIL": {
            "nodes": ["TAIL", "BIAS", "VSS", "VSS"],
            "model": "test_nmos",
            "netlist_width_um": 0.8,
            "finger_width_um": 0.8,
            "fingers": 1.0,
            "multiplicity": 1.0,
            "total_width_um": 0.8,
            "length_um": 0.03,
            "model_parameters": {"ad": "tail"},
        },
    }
    evidence["netlist"] = {
        "source": "eda_result",
        "generator": "Cadence si -batch",
        "remote_path": "/data/xum/vda_diffpair/netlist",
        "sha256": "4" * 64,
        "semantic_parameters": copy.deepcopy(semantic),
        "device_geometry": copy.deepcopy(input_geometry),
        "topology_variant": topology,
        "parameter_consistency": "matched",
        "instances": instances,
    }
    evidence["testbench"]["remote_path"] = "/data/xum/vda_diffpair/input.scs"
    evidence["testbench"]["sha256"] = "5" * 64
    evidence["testbench"]["values"] = {
        "analysis": "ac",
        "tail_bias_v": 0.4,
        "common_mode_v": 0.5,
        "vdd_v": 0.9,
        "load_ff": 1.0,
    }
    node_values = {
        "INP": 0.5,
        "INN": 0.5,
        "OUTP": 0.5,
        "OUTN": 0.5,
        "TAIL": 0.1,
        "BIAS": 0.4,
        "VDD": 0.9,
        "VSS": 0.0,
    }
    evidence["operating_point"] = {
        "source": "eda_result",
        "node_values_v": node_values,
        "device_values": {
            "MN0": {"ids_a": 60e-6, "vgs_v": 0.4, "vds_v": 0.4, "vdsat_v": 0.1, "gm_s": 600e-6, "gds_s": 30e-6},
            "MN1": {"ids_a": 60e-6, "vgs_v": 0.4, "vds_v": 0.4, "vdsat_v": 0.1, "gm_s": 600e-6, "gds_s": 30e-6},
            "MP0": {"ids_a": -60e-6, "vgs_v": -0.4, "vds_v": -0.4, "vdsat_v": -0.1, "gm_s": 480e-6, "gds_s": 24e-6},
            "MP1": {"ids_a": -60e-6, "vgs_v": -0.4, "vds_v": -0.4, "vdsat_v": -0.1, "gm_s": 480e-6, "gds_s": 24e-6},
            "MNTAIL": {"ids_a": 120e-6, "vgs_v": 0.4, "vds_v": 0.1, "vdsat_v": 0.1, "gm_s": 960e-6, "gds_s": 72e-6},
        },
        "node_device_consistency": "matched",
        "kcl_consistency": "matched",
    }
    return circuit


def _differential_policy() -> dict:
    return {
        "schema_version": 1,
        "id": "test-differential-multi-plane",
        "expected_target": {
            "library": "vda_test",
            "cell": "vda_diffpair",
            "view": "schematic",
        },
        "expected_pdk_profile": "test_pdk",
        "expected_process_corner": "test_tt",
        "expected_temperature_c": 27.0,
        "expected_vdd_v": 0.9,
        "expected_topology_variant": (
            "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
        ),
        "require_exact_characterization_width": True,
        "require_exact_model_parameters": True,
        "thresholds": {
            "maximum_device_dc_relative_error": 1.0,
            "maximum_gain_error_db": 20.0,
            "maximum_phase_error_deg": 180.0,
            "maximum_bandwidth_relative_error": 1.0,
            "maximum_gbw_relative_error": 1.0,
        },
    }


def _add_source_instance_binding(characterization: dict) -> None:
    model_parameters = characterization["actions"][1]["details"]["artifact"][
        "model_parameters_by_polarity"
    ]["nmos"]
    signature = hashlib.sha256(
        json.dumps(
            model_parameters,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    binding = {
        "source_evidence_source": "eda_result",
        "derivation_evidence_source": "software_inference",
        "source_run_sha256": "e" * 64,
        "source_task_id": "source-characterization-seed",
        "source_action": "simulation.candidate.1",
        "source_instance": "MN0",
        "source_netlist_sha256": "f" * 64,
        "source_pdk_profile": "test_pdk",
        "source_process_corner": "test_tt",
        "source_temperature_c": 27.0,
        "source_topology_variant": "common_source",
        "source_model": "test_nmos",
        "source_width_um": 1.0,
        "source_length_um": 0.03,
        "source_model_parameter_count": len(model_parameters),
        "source_model_parameters_sha256": signature,
    }
    characterization["actions"][0]["details"][
        "source_instance_binding"
    ] = copy.deepcopy(binding)
    characterization["actions"][1]["details"]["artifact"][
        "source_instance_binding"
    ] = binding


def test_rectilinear_interpolation_records_corners_and_rejects_length_guessing() -> None:
    artifact = MosCharacterizationArtifact.model_validate(_artifact())
    result = interpolate_mos_characterization_point(
        artifact,
        point_id="bound-MN0",
        model="test_nmos",
        polarity="nmos",
        length_um=0.03,
        vgs_magnitude_v=0.4,
        vds_magnitude_v=0.5,
        vsb_magnitude_v=0.0,
    )

    assert len(result.corners) == 4
    assert sum(item.weight for item in result.corners) == pytest.approx(1.0)
    assert result.point.drain_current_density_a_per_um == pytest.approx(40e-6)
    assert result.evidence_source.value == "software_inference"

    with pytest.raises(RuntimeError, match="length interpolation and extrapolation"):
        interpolate_mos_characterization_point(
            artifact,
            point_id="bad-length",
            model="test_nmos",
            polarity="nmos",
            length_um=0.04,
            vgs_magnitude_v=0.4,
            vds_magnitude_v=0.5,
            vsb_magnitude_v=0.0,
        )


def test_same_source_validation_binds_graph_bias_raw_files_and_ac_metrics(
    tmp_path: Path,
) -> None:
    characterization_path, circuit_path = _write_inputs(tmp_path)
    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(_policy()),
        characterization_path,
        circuit_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.gate_passed is True
    assert result.graph_binding["topology_equation_hardcoded"] is False
    assert result.graph_binding["mos_instances"] == ["MN0"]
    assert result.graph_binding["model_parameter_signatures"]["MN0"][
        "parameter_count"
    ] == 2
    assert result.raw_artifact_bindings["ac_raw_sha256"] == "d" * 64
    assert result.device_dc_validation[0].passed is True
    assert result.ac_validation["low_frequency_gain_db"].passed is True
    assert result.ac_validation["phase_at_bandwidth_deg"].passed is True
    assert result.network_result.gain_bandwidth_product_hz is not None
    assert result.evidence_sources["oa_schematic"].value == "bridge_readback"
    assert result.evidence_sources["network_prediction"].value == (
        "software_inference"
    )


def test_same_source_validation_gates_direct_circuit_charge_derivatives(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    matrix = _drain_bulk_charge_matrix(1e-12)
    for point in characterization["actions"][1]["details"]["artifact"]["points"]:
        point["charge_derivative_matrix_f_per_um"] = copy.deepcopy(matrix)
    circuit = _circuit_run()
    device_values = circuit["actions"][0]["details"]["evidence"][
        "operating_point"
    ]["device_values"]
    device_values["charge_derivative_matrix_f"] = copy.deepcopy(matrix)
    device_values["cjd_f"] = 0.0
    device_values["cjs_f"] = 0.0
    characterization_path, circuit_path = _write_inputs(
        tmp_path, circuit, characterization
    )

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(_policy()),
        characterization_path,
        circuit_path,
    )

    comparisons = result.device_dc_validation[0].comparisons
    assert comparisons["cdd_f"].error_kind == "normalized_relative"
    assert comparisons["cdd_f"].passed is True
    assert result.gate_passed is True

    device_values["charge_derivative_matrix_f"]["cdd"] = 2e-12
    characterization_path, circuit_path = _write_inputs(
        tmp_path, circuit, characterization
    )
    failed = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(_policy()),
        characterization_path,
        circuit_path,
    )
    assert failed.device_dc_validation[0].comparisons["cdd_f"].passed is False
    assert failed.gate_passed is False


def test_differential_pair_binds_independent_non_one_to_one_width_planes(
    tmp_path: Path,
) -> None:
    characterization_paths = []
    for index, run in enumerate(_differential_characterizations()):
        path = tmp_path / f"characterization-{index}.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        characterization_paths.append(path)
    circuit_path = tmp_path / "differential.json"
    circuit_path.write_text(json.dumps(_differential_circuit_run()), encoding="utf-8")

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(_differential_policy()),
        characterization_paths,
        circuit_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.gate_passed is True
    assert result.graph_binding["characterization_artifact_by_instance"] == {
        "MN0": "test-input-nmos-table",
        "MN1": "test-input-nmos-table",
        "MP0": "test-load-pmos-table",
        "MP1": "test-load-pmos-table",
        "MNTAIL": "test-tail-nmos-table",
    }
    assert result.graph_binding["characterized_width_um_by_instance"] == {
        "MN0": 1.5,
        "MN1": 1.5,
        "MP0": 2.4,
        "MP1": 2.4,
        "MNTAIL": 0.8,
    }
    assert result.network_result.characterization_ids == [
        "test-input-nmos-table",
        "test-load-pmos-table",
        "test-tail-nmos-table",
    ]
    derived = {
        item.instance: item.characterization_id
        for item in result.network_result.derived_mos_values
    }
    assert derived == result.graph_binding["characterization_artifact_by_instance"]
    assert len(result.raw_artifact_bindings["characterizations"]) == 3

    policy_path = tmp_path / "differential-policy.json"
    output_path = tmp_path / "differential-validation.json"
    policy_path.write_text(json.dumps(_differential_policy()), encoding="utf-8")
    assert (
        main(
            [
                "small-signal-validate",
                str(policy_path),
                str(characterization_paths[0]),
                str(circuit_path),
                "--additional-characterization-run",
                str(characterization_paths[1]),
                "--additional-characterization-run",
                str(characterization_paths[2]),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["characterization_task_ids"] == [
        "test-input-nmos-characterization",
        "test-load-pmos-characterization",
        "test-tail-nmos-characterization",
    ]


def test_differential_pair_rejects_missing_ambiguous_and_mismatched_planes(
    tmp_path: Path,
) -> None:
    circuit_path = tmp_path / "differential.json"
    circuit_path.write_text(json.dumps(_differential_circuit_run()), encoding="utf-8")
    policy = SmallSignalCircuitValidationPolicy.model_validate(_differential_policy())

    runs = _differential_characterizations()
    input_path = tmp_path / "input.json"
    tail_path = tmp_path / "tail.json"
    input_path.write_text(json.dumps(runs[0]), encoding="utf-8")
    tail_path.write_text(json.dumps(runs[2]), encoding="utf-8")
    with pytest.raises(ValueError, match="no characterization artifact.*MP0"):
        validate_common_source_small_signal_runs(
            policy,
            [input_path, tail_path],
            circuit_path,
        )

    duplicate = copy.deepcopy(runs[0])
    duplicate["task_id"] = "test-input-duplicate-characterization"
    duplicate["actions"][0]["details"]["task_id"] = duplicate["task_id"]
    duplicate["actions"][1]["details"]["artifact"]["id"] = (
        "test-input-duplicate-table"
    )
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
    pmos_path = tmp_path / "pmos.json"
    pmos_path.write_text(json.dumps(runs[1]), encoding="utf-8")
    with pytest.raises(ValueError, match="MN0 matches multiple"):
        validate_common_source_small_signal_runs(
            policy,
            [input_path, duplicate_path, pmos_path, tail_path],
            circuit_path,
        )

    wrong_width = copy.deepcopy(runs[1])
    wrong_width["actions"][0]["details"]["width_um"] = 2.0
    wrong_width["actions"][1]["details"]["artifact"][
        "characterized_width_um"
    ] = 2.0
    wrong_width_path = tmp_path / "wrong-width.json"
    wrong_width_path.write_text(json.dumps(wrong_width), encoding="utf-8")
    with pytest.raises(ValueError, match="MP0 width 2.4um does not match"):
        validate_common_source_small_signal_runs(
            policy,
            [input_path, wrong_width_path, tail_path],
            circuit_path,
        )

    wrong_signature = copy.deepcopy(runs[1])
    wrong_signature["actions"][0]["details"]["model_parameters_by_polarity"] = {
        "pmos": {"ad": "other"}
    }
    wrong_signature["actions"][1]["details"]["artifact"][
        "model_parameters_by_polarity"
    ] = {"pmos": {"ad": "other"}}
    wrong_signature_path = tmp_path / "wrong-signature.json"
    wrong_signature_path.write_text(json.dumps(wrong_signature), encoding="utf-8")
    with pytest.raises(ValueError, match="MP0 model parameter signature"):
        validate_common_source_small_signal_runs(
            policy,
            [input_path, wrong_signature_path, tail_path],
            circuit_path,
        )


def test_same_binder_and_characterization_plane_migrate_to_source_degeneration(
    tmp_path: Path,
) -> None:
    circuit = _source_degenerated_circuit_run()
    policy = _policy()
    policy["id"] = "test-gate7c-source-degenerated"
    policy["expected_topology_variant"] = "source_degenerated_common_source"
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(policy),
        characterization_path,
        circuit_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.gate_passed is True
    assert result.graph_binding["topology_equation_hardcoded"] is False
    assert result.graph_binding["resistor_instances"] == ["RD0", "RS0"]
    assert result.graph_binding["source_degeneration_bindings"] == [
        {
            "mos_instance": "MN0",
            "source_node": "NSRC",
            "reference_node": "VSS",
            "resistor_instance": "RS0",
            "resistance_ohm": 2_000.0,
        }
    ]
    assert result.graph_binding["model_parameter_signatures"]["MN0"][
        "parameter_count"
    ] == 2


def test_validation_can_require_a_reusable_source_instance_binding(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    _add_source_instance_binding(characterization)
    policy = _policy()
    policy["require_characterization_source_instance_binding"] = True
    characterization_path, circuit_path = _write_inputs(
        tmp_path,
        characterization=characterization,
    )

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(policy),
        characterization_path,
        circuit_path,
    )

    assert result.gate_passed is True
    assert result.graph_binding["characterization_source_instance_binding"][
        "source_instance"
    ] == "MN0"
    assert result.graph_binding["characterization_source_matching_instances"] == [
        "MN0"
    ]
    assert (
        result.graph_binding["characterization_source_run_is_current_circuit_run"]
        is False
    )
    assert result.evidence_sources["characterization_source_instance"].value == (
        "eda_result"
    )


def test_source_instance_binding_reuses_identity_across_instance_names(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    _add_source_instance_binding(characterization)
    circuit = _circuit_run()
    instances = circuit["actions"][0]["details"]["evidence"]["netlist"][
        "instances"
    ]
    instances["MCORE"] = instances.pop("MN0")
    policy = _policy()
    policy["require_characterization_source_instance_binding"] = True
    characterization_path, circuit_path = _write_inputs(
        tmp_path,
        circuit=circuit,
        characterization=characterization,
    )

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(policy),
        characterization_path,
        circuit_path,
    )

    assert result.gate_passed is True
    assert result.graph_binding["mos_instances"] == ["MCORE"]
    assert result.graph_binding["characterization_source_matching_instances"] == [
        "MCORE"
    ]


def test_required_source_instance_binding_rejects_signature_drift(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    _add_source_instance_binding(characterization)
    characterization["actions"][0]["details"]["source_instance_binding"][
        "source_model_parameters_sha256"
    ] = "0" * 64
    characterization["actions"][1]["details"]["artifact"][
        "source_instance_binding"
    ]["source_model_parameters_sha256"] = "0" * 64
    policy = _policy()
    policy["require_characterization_source_instance_binding"] = True
    characterization_path, circuit_path = _write_inputs(
        tmp_path,
        characterization=characterization,
    )

    with pytest.raises(ValueError, match="source-instance signature"):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(policy),
            characterization_path,
            circuit_path,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_source_resistor_node", "must connect to VSS"),
        ("missing_dc_consistency", "source-degeneration consistency"),
        ("inconsistent_source_voltage", "VGS does not match DC node voltages"),
    ],
)
def test_source_degenerated_binding_rejects_broken_graph_or_dc_evidence(
    tmp_path: Path, mutation: str, message: str
) -> None:
    circuit = _source_degenerated_circuit_run()
    details = circuit["actions"][0]["details"]
    if mutation == "wrong_source_resistor_node":
        details["evidence"]["netlist"]["instances"]["RS0"]["nodes"] = [
            "NSRC",
            "VDD",
        ]
    elif mutation == "missing_dc_consistency":
        details["evidence"]["operating_point"].pop(
            "source_degeneration_consistency"
        )
    else:
        details["evidence"]["operating_point"]["node_values_v"]["NSRC"] = 0.1
    policy = _policy()
    policy["expected_topology_variant"] = "source_degenerated_common_source"
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    with pytest.raises(ValueError, match=message):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(policy),
            characterization_path,
            circuit_path,
        )


def test_validation_selects_one_explicit_operating_condition(tmp_path: Path) -> None:
    circuit = _circuit_run()
    selected = circuit["actions"][0]["details"]
    circuit["actions"][0]["details"] = {
        "analysis_complete": True,
        "analysis_issues": [],
        "operating_condition_results": [
            {
                "condition": {
                    "name": "test_tt_27c",
                    "process_corner": "test_tt",
                    "temperature_c": 27.0,
                    "vdd_v": None,
                },
                "result": selected,
            }
        ],
    }
    policy = _policy()
    policy["operating_condition_name"] = "test_tt_27c"
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(policy),
        characterization_path,
        circuit_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.circuit_task_id == "test-common-source"


def test_validation_is_partial_instead_of_moving_a_failed_gate(tmp_path: Path) -> None:
    circuit = _circuit_run()
    details = circuit["actions"][0]["details"]
    details["metrics"]["bandwidth_3db_hz"] *= 2.0
    details["metrics"]["gain_bandwidth_product_hz"] *= 2.0
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(_policy()),
        characterization_path,
        circuit_path,
    )

    assert result.status is RunStatus.PARTIAL
    assert result.gate_passed is False
    assert result.ac_validation["bandwidth_3db_hz"].passed is False
    assert result.ac_validation["bandwidth_3db_hz"].threshold == 0.02


def test_validation_rejects_bias_extrapolation(tmp_path: Path) -> None:
    circuit = _circuit_run()
    details = circuit["actions"][0]["details"]
    details["evidence"]["operating_point"]["node_values_v"]["IN"] = 0.2
    details["evidence"]["operating_point"]["device_values"]["vgs_v"] = 0.2
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    with pytest.raises(RuntimeError, match="not bracketed by training data"):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(_policy()),
            characterization_path,
            circuit_path,
        )


def test_validation_rejects_a_different_characterized_width_plane(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    characterization["actions"][1]["details"]["artifact"][
        "characterized_width_um"
    ] = 2.0
    characterization["actions"][0]["details"]["width_um"] = 2.0
    characterization_path = tmp_path / "characterization.json"
    circuit_path = tmp_path / "circuit.json"
    characterization_path.write_text(json.dumps(characterization), encoding="utf-8")
    circuit_path.write_text(json.dumps(_circuit_run()), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match the characterized width"):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(_policy()),
            characterization_path,
            circuit_path,
        )


def test_validation_rejects_raw_characterization_manifest_drift(
    tmp_path: Path,
) -> None:
    characterization = _characterization_run()
    characterization["actions"][0]["details"]["evidence"][
        "manifest_sha256"
    ] = "e" * 64
    characterization_path, circuit_path = _write_inputs(
        tmp_path,
        characterization=characterization,
    )

    with pytest.raises(ValueError, match="manifest does not match"):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(_policy()),
            characterization_path,
            circuit_path,
        )


def test_exploratory_policy_can_skip_exact_model_parameter_binding(
    tmp_path: Path,
) -> None:
    circuit = _circuit_run()
    circuit["actions"][0]["details"]["evidence"]["netlist"]["instances"][
        "MN0"
    ].pop("model_parameters")
    policy = _policy()
    policy["require_exact_model_parameters"] = False
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    result = validate_common_source_small_signal_runs(
        SmallSignalCircuitValidationPolicy.model_validate(policy),
        characterization_path,
        circuit_path,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.graph_binding["exact_model_parameters_required"] is False
    assert result.graph_binding["model_parameter_signatures"] == {}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_ac_raw", "AC raw-file evidence"),
        ("oa_si_mismatch", "OA/si semantic parameters"),
        ("wrong_target", "schematic target"),
        ("write_action", "OA write action"),
        ("model_parameter_mismatch", "model parameter signature"),
        ("unparsed_model_parameter", "signature is not fully parsed"),
        ("simulation_vdd_mismatch", "simulation VDD"),
    ],
)
def test_validation_rejects_broken_evidence_chains(
    tmp_path: Path, mutation: str, message: str
) -> None:
    circuit = _circuit_run()
    details = circuit["actions"][0]["details"]
    if mutation == "missing_ac_raw":
        details["evidence"]["ac_response"].pop("raw_files")
    elif mutation == "oa_si_mismatch":
        details["evidence"]["netlist"]["semantic_parameters"][
            "load_resistance_ohm"
        ] = 20_000.0
    elif mutation == "wrong_target":
        details["evidence"]["schematic_readback"]["target"]["cell"] = "other"
    elif mutation == "model_parameter_mismatch":
        details["evidence"]["netlist"]["instances"]["MN0"][
            "model_parameters"
        ]["ad"] = "4e-14"
    elif mutation == "unparsed_model_parameter":
        details["evidence"]["netlist"]["instances"]["MN0"][
            "unparsed_model_parameter_tokens"
        ] = ["opaque=(foo", "+", "bar)"]
    elif mutation == "simulation_vdd_mismatch":
        details["parameters"]["vdd_v"] = 0.8
    else:
        circuit["actions"].append(
            {
                "action": "parameters.apply",
                "status": "succeeded",
                "started_at": _NOW,
                "finished_at": _NOW,
                "evidence_source": "bridge_readback",
                "details": {},
            }
        )
    characterization_path, circuit_path = _write_inputs(tmp_path, circuit)

    with pytest.raises(ValueError, match=message):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(_policy()),
            characterization_path,
            circuit_path,
        )


def test_small_signal_validation_cli_persists_the_same_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    characterization_path, circuit_path = _write_inputs(tmp_path)
    policy_path = tmp_path / "policy.json"
    output_path = tmp_path / "validation.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

    assert (
        main(
            [
                "small-signal-validate",
                str(policy_path),
                str(characterization_path),
                str(circuit_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["gate_passed"] is True


def test_validation_refuses_demo_records(tmp_path: Path) -> None:
    characterization = _characterization_run()
    characterization["adapter"] = "demo"
    characterization_path = tmp_path / "characterization.json"
    circuit_path = tmp_path / "circuit.json"
    characterization_path.write_text(json.dumps(characterization), encoding="utf-8")
    circuit_path.write_text(json.dumps(_circuit_run()), encoding="utf-8")

    with pytest.raises(ValueError, match="requires bridge run records"):
        validate_common_source_small_signal_runs(
            SmallSignalCircuitValidationPolicy.model_validate(_policy()),
            characterization_path,
            circuit_path,
        )
