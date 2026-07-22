from __future__ import annotations

import hashlib
import json

import pytest

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.adapters.subprocess_bridge import BridgeWorkerError
from virtuoso_design_agent.executor import (
    TaskExecutor,
    load_execution_checkpoint,
)
from virtuoso_design_agent.models import (
    AnalysisKind,
    EvidenceSource,
    RunStatus,
    TaskSpec,
)
from virtuoso_design_agent.planner import build_plan


def _close_loop(delay_limit: float = 45.0) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "loop",
            "operation": "design.close_loop",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"length_um": 0.03, "load_ff": 2.0, "vdd_v": 0.9},
            "parameter_space": {
                "nmos_width_um": [0.4, 0.5, 0.6],
                "pmos_width_um": [0.8, 1.0, 1.2],
            },
            "constraints": [
                {"metric": "delay_ps", "relation": "<=", "value": delay_limit},
                {"metric": "rise_fall_skew_ps", "relation": "<=", "value": 8},
            ],
            "objective": {"metric": "delay_ps", "goal": "minimize"},
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
            },
            "limits": {"max_iterations": 9, "timeout_seconds": 600},
        }
    )


def _explicit_parameter_apply() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "explicit-parameter-apply",
            "operation": "parameters.apply",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "instance_parameter_updates": [
                {
                    "instance": "MN0",
                    "parameters": {"fingers": "2", "m": "1"},
                },
                {"instance": "RD0", "parameters": {"r": "22k"}},
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def _ade_setup_task() -> TaskSpec:
    return TaskSpec.model_validate(
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
                        "expression": "bandwidth(mag(VF(\"/OUT\")) 3 \"low\")",
                        "spec": {"relation": "gt", "value": "1G"},
                    }
                ],
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def _ade_setup_evidence(task: TaskSpec) -> dict:
    assert task.ade_setup is not None
    requested_analyses = [
        update.model_dump(mode="json") for update in task.ade_setup.analyses
    ]
    requested_outputs = [
        output.model_dump(mode="json") for output in task.ade_setup.outputs
    ]
    analysis_state = {
        "enabled": True,
        "options": {"anaName": "ac", "start": "1", "stop": "10G"},
    }
    output_state = {
        "name": "BW",
        "type": "point",
        "signal_name": None,
        "expression": requested_outputs[0]["expression"],
        "eval_type": "point",
        "plot": None,
        "save": None,
        "spec": {"relation": "gt", "value": "1G"},
    }
    immediate_analyses = [
        {"test": "AC", "analysis": "ac", "state": analysis_state}
    ]
    immediate_outputs = [{"test": "AC", "name": "BW", "state": output_state}]
    return {
        "target": task.target.model_dump(mode="json"),
        "expected_tests": ["AC"],
        "tests_readback_before": ["AC"],
        "tests_readback_after": ["AC"],
        "requested_analysis_updates": requested_analyses,
        "requested_output_additions": requested_outputs,
        "requested_evidence_source": "user_input",
        "before_analyses": [{"test": "AC", "analysis": "ac", "state": None}],
        "immediate_analyses": immediate_analyses,
        "persisted_analyses": immediate_analyses,
        "before_outputs": [{"test": "AC", "name": "BW", "state": None}],
        "immediate_outputs": immediate_outputs,
        "persisted_outputs": immediate_outputs,
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": "a" * 64,
        "after_target_fingerprint_sha256": "b" * 64,
        "analysis_write_method": "bridge_public_set_analysis",
        "analysis_readback_method": (
            "cadence_maeGetAnalysis_via_bridge_skill_channel"
        ),
        "output_write_method": "bridge_public_add_output_and_set_spec",
        "output_readback_method": (
            "cadence_maeGetTestOutputs_and_axlGetSpecData_"
            "via_bridge_skill_channel"
        ),
        "existing_outputs_replaced": False,
        "existing_maestro_replaced": False,
        "unlisted_setup_state_checked": False,
        "full_setup_fingerprint_verified": False,
        "schematic_oa_write_performed": False,
        "maestro_setup_write_performed": True,
        "automated_simulation_performed": False,
    }


def test_explicit_instance_parameter_apply_is_independently_read_back() -> None:
    task = _explicit_parameter_apply()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters is None
    applied = next(
        action for action in record.actions if action.action == "parameters.apply"
    )
    assert applied.details["requested_evidence_source"] == "user_input"
    assert applied.details["confirmed_evidence_source"] == "software_inference"
    assert applied.details["confirmed_instance_parameters"] == {
        "MN0": {"fingers": "2", "m": "1"},
        "RD0": {"r": "22k"},
    }
    readback = adapter.inspect_schematic(task).data["instance_parameters"]
    assert readback["MN0"]["fingers"] == "2"
    assert readback["RD0"]["r"] == "22k"


def test_ade_prepare_creates_only_new_manual_setup_without_simulating() -> None:
    class PreparingAdapter(DeterministicDemoAdapter):
        def prepare_ade(self, task):
            return AdapterResult(
                data={
                    "target": task.target.model_dump(mode="json"),
                    "persistent_view_confirmed": True,
                    "confirmed_setup_evidence_source": "bridge_readback",
                    "existing_maestro_overwritten": False,
                    "schematic_oa_write_performed": False,
                        "maestro_oa_write_performed": True,
                        "design_target_confirmed": True,
                        "design_readback": {
                            "library": "vda_test",
                            "cell": "vda_manual_tb",
                            "view": "schematic",
                        },
                    "configured_analyses": [],
                    "configured_sweeps": [],
                    "configured_outputs": [],
                },
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

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
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(PreparingAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.prepare",
    ]
    prepared = record.actions[-1]
    assert prepared.details["existing_maestro_overwritten"] is False
    assert prepared.details["schematic_oa_write_performed"] is False
    assert prepared.details["maestro_oa_write_performed"] is True
    assert prepared.details["configured_analyses"] == []
    assert record.candidates == []
    assert any("manual editing" in note for note in record.notes)


def test_ade_prepare_rejects_incomplete_adapter_write_evidence() -> None:
    class UntrustedPreparingAdapter(DeterministicDemoAdapter):
        def prepare_ade(self, task):
            return AdapterResult(
                data={"existing_maestro_overwritten": False},
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    task = TaskSpec.model_validate(
        {
            "id": "prepare-manual-ade-untrusted",
            "operation": "ade.prepare",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_prepare": {},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(UntrustedPreparingAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("new persistent Maestro-only write" in note for note in record.notes)


def test_ade_capture_records_manual_handoff_without_simulating_or_writing_oa() -> None:
    class CapturingAdapter(DeterministicDemoAdapter):
        def capture_ade(self, task):
            return AdapterResult(
                data={
                    "target": task.target.model_dump(mode="json"),
                    "setup_evidence_source": "bridge_readback",
                    "structured_results_available": True,
                    "structured_results_evidence_source": "eda_result",
                    "automated_simulation_performed": False,
                    "oa_write_performed": False,
                },
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

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
            "ade_capture": {"backend": "maestro"},
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(CapturingAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.capture",
    ]
    capture = record.actions[-1]
    assert capture.evidence_source is EvidenceSource.BRIDGE_READBACK
    assert capture.details["structured_results_evidence_source"] == "eda_result"
    assert capture.details["automated_simulation_performed"] is False
    assert capture.details["oa_write_performed"] is False
    assert record.candidates == []
    assert any("human-operated ADE" in note for note in record.notes)


@pytest.mark.parametrize(
    ("structured", "expected_status"),
    [
        (True, RunStatus.SUCCEEDED),
        (False, RunStatus.PARTIAL),
    ],
)
def test_ade_run_records_background_history_without_oa_write(
    structured: bool, expected_status: RunStatus
) -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data={
                    "target": task.target.model_dump(mode="json"),
                    "session_mode": "background",
                    "setup_evidence_source": "bridge_readback",
                    "history": "Interactive.8",
                    "structured_results_available": structured,
                    "structured_results_evidence_source": "eda_result",
                    "automated_simulation_performed": True,
                    "oa_write_performed": False,
                        "maestro_setup_write_performed": False,
                        "runtime_directory_persisted": False,
                        "runtime_directory_restored": True,
                        "runtime_artifacts_restricted_to_data_xum": True,
                        "runtime_scratch_root": "/data/xum/vda_runs/run-1",
                    "artifact_history": "Interactive.8",
                    "artifact_history_path_binding_verified": True,
                    "artifact_runtime_input_binding_verified": True,
                    "artifact_run_binding_verified": True,
                    "simulator_input_consistency_verified": True,
                    "simulator_input_consistency_evidence_sources": {
                        "maestro_design_and_oa": "bridge_readback",
                        "spectre_input": "eda_result",
                        "comparison": "software_inference",
                    },
                    "simulator_input_consistency": [
                        {
                            "design_identity_verified": True,
                            "instance_set_verified": True,
                            "node_connectivity_verified": True,
                            "raw_parameter_mapping_verified": True,
                            "verified_parameter_pairs": 4,
                            "input_sha256": "2" * 64,
                            "comparison_sha256": "5" * 64,
                        }
                    ],
                    "artifact_manifest_complete": True,
                    "artifacts_captured": True,
                    "artifact_counts": {
                        "simulator_input": 2,
                        "eda_result": 1,
                        "run_log": 1,
                    },
                    "artifact_manifest": [
                        {
                            "path": "Interactive.8/1/AC/netlist/netlist",
                            "size_bytes": 10,
                            "sha256": "1" * 64,
                            "category": "simulator_input",
                            "evidence_source": "eda_result",
                            "remote_paths": [
                                "/data/xum/scratch/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8/1/AC/netlist/netlist"
                            ],
                        },
                        {
                            "path": "Interactive.8/1/AC/netlist/input.scs",
                            "size_bytes": 20,
                            "sha256": "2" * 64,
                            "category": "simulator_input",
                            "evidence_source": "eda_result",
                            "remote_paths": [
                                "/data/xum/scratch/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8/1/AC/netlist/input.scs"
                            ],
                        },
                        {
                            "path": "Interactive.8/1/AC/psf/ac.ac",
                            "size_bytes": 30,
                            "sha256": "3" * 64,
                            "category": "eda_result",
                            "evidence_source": "eda_result",
                            "remote_paths": [
                                "/data/xum/scratch/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8/1/AC/psf/ac.ac"
                            ],
                        },
                        {
                            "path": "Interactive.8/Interactive.8.log",
                            "size_bytes": 40,
                            "sha256": "4" * 64,
                            "category": "run_log",
                            "evidence_source": "eda_result",
                            "remote_paths": [
                                "/data/xum/project/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8.log"
                            ],
                        },
                    ],
                    "simulation_fingerprint_sha256": (
                        "58070e0bdbe50ba3c4f343f00c553a6b425271a5b2d678b6d113fff3695d9104"
                    ),
                    "remote_manifest_directory": "/data/xum/vda_runs/manifest",
                    "artifact_locations_checked": [
                        {
                            "binding": "exact_history",
                            "history_root": (
                                "/data/xum/project/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8"
                            ),
                            "remote_manifest_path": (
                                "/data/xum/vda_runs/manifest/0_project.tsv"
                            ),
                        },
                        {
                            "binding": "exact_history",
                            "history_root": (
                                "/data/xum/scratch/vda_test/cell/maestro/results/"
                                "maestro/Interactive.8"
                            ),
                            "remote_manifest_path": (
                                "/data/xum/vda_runs/manifest/1_scratch.tsv"
                            ),
                        },
                        {
                            "source_location": "runtime",
                            "binding": "unique_runtime_session",
                            "tree_root": (
                                "/data/xum/vda_runs/run-1/vda_test/cell/maestro/"
                                "results/maestro/.tmpADEDir_vda/0_AC/netlist"
                            ),
                            "history_root": None,
                            "runtime_test": "AC",
                            "remote_manifest_path": (
                                "/data/xum/vda_runs/manifest/2_runtime.tsv"
                            ),
                        },
                    ],
                },
                evidence_source=EvidenceSource.EDA_RESULT,
            )

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
            "ade_run": {
                "require_structured_outputs": structured,
                "require_simulator_input_consistency": True,
            },
            "safety": {"allow_remote_compute": True},
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is expected_status
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.run",
    ]
    run = record.actions[-1]
    assert run.evidence_source is EvidenceSource.EDA_RESULT
    assert run.details["history"] == "Interactive.8"
    assert run.details["oa_write_performed"] is False
    assert run.details["artifact_manifest_complete"] is True
    assert record.candidates == []
    assert any("background session" in note for note in record.notes)


def _native_sweep_run_evidence(task: TaskSpec) -> dict:
    history = "Interactive.12"
    runtime_root = "/data/xum/vda_runs/sweep-12"
    manifest: list[dict] = []
    input_consistency: list[dict] = []
    point_consistency: list[dict] = []
    for point, value, result_value in (
        (1, "1f", "0.45"),
        (2, "2f", "0.40"),
    ):
        input_path = f"{history}/{point}/VDA/netlist/input.scs"
        netlist_path = f"{history}/{point}/VDA/netlist/netlist"
        result_path = f"{history}/{point}/VDA/psf/tran.tran"
        input_hash = str(point) * 64
        comparison_hash = str(point + 2) * 64
        manifest.extend(
            [
                {
                    "path": netlist_path,
                    "size_bytes": 10,
                    "sha256": str(point + 4) * 64,
                    "category": "simulator_input",
                    "binding": "exact_history_path",
                    "evidence_source": "eda_result",
                    "remote_paths": [f"/data/xum/results/{netlist_path}"],
                },
                {
                    "path": input_path,
                    "size_bytes": 20,
                    "sha256": input_hash,
                    "category": "simulator_input",
                    "binding": "exact_history_path",
                    "evidence_source": "eda_result",
                    "remote_paths": [f"/data/xum/results/{input_path}"],
                },
                {
                    "path": result_path,
                    "size_bytes": 30,
                    "sha256": str(point + 6) * 64,
                    "category": "eda_result",
                    "binding": "exact_history_path",
                    "evidence_source": "eda_result",
                    "remote_paths": [f"/data/xum/results/{result_path}"],
                },
            ]
        )
        input_consistency.append(
            {
                "point": point,
                "test": "VDA",
                "input_path": input_path,
                "design_identity_verified": True,
                "instance_set_verified": True,
                "node_connectivity_verified": True,
                "raw_parameter_mapping_verified": True,
                "effective_sweep_bindings_verified": True,
                "verified_sweep_binding_pairs": 1,
                "verified_parameter_pairs": 1,
                "input_sha256": input_hash,
                "comparison_sha256": comparison_hash,
            }
        )
        point_payload = {
            "point": point,
            "expected_parameters": {"CL": value},
            "result_parameters": {"CL": value},
            "scalar_outputs": {"VoutAvg": result_value},
            "tests": [
                {
                    "test": "VDA",
                    "inputs": [
                        {
                            "path": input_path,
                            "sha256": input_hash,
                            "comparison_sha256": comparison_hash,
                        }
                    ],
                    "result_artifacts": [
                        {
                            "path": result_path,
                            "sha256": str(point + 6) * 64,
                            "size_bytes": 30,
                        }
                    ],
                }
            ],
        }
        point_consistency.append(
            {
                **point_payload,
                "point_binding_sha256": hashlib.sha256(
                    json.dumps(
                        point_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    manifest.append(
        {
            "path": f"{history}/{history}.log",
            "size_bytes": 40,
            "sha256": "9" * 64,
            "category": "run_log",
            "binding": "exact_history_companion",
            "evidence_source": "eda_result",
            "remote_paths": [f"/data/xum/results/{history}.log"],
        }
    )
    counts = {
        category: sum(1 for item in manifest if item["category"] == category)
        for category in ("simulator_input", "eda_result", "run_log")
    }
    fingerprint_payload = sorted(
        (
            {"path": item["path"], "sha256": item["sha256"]}
            for item in manifest
            if item["category"] in counts
        ),
        key=lambda item: item["path"],
    )
    setup_readback = {
        "tests": ["VDA"],
        "corners": None,
        "variables": {"CL": "1f,2f"},
        "variable_readback_methods": {"CL": "bridge_public_get_var"},
        "fingerprint_sha256": "a" * 64,
    }
    return {
        "target": task.target.model_dump(mode="json"),
        "session_mode": "background",
        "setup_evidence_source": "bridge_readback",
        "history": history,
        "structured_results_available": True,
        "structured_results_evidence_source": "eda_result",
        "automated_simulation_performed": True,
        "oa_write_performed": False,
        "maestro_setup_write_performed": False,
        "runtime_directory_persisted": False,
        "runtime_directory_restored": True,
        "runtime_artifacts_restricted_to_data_xum": True,
        "runtime_scratch_root": runtime_root,
        "artifact_history": history,
        "artifact_history_path_binding_verified": True,
        "artifact_runtime_input_binding_verified": True,
        "artifact_run_binding_verified": True,
        "artifact_manifest_complete": True,
        "artifacts_captured": True,
        "artifact_counts": counts,
        "artifact_manifest": manifest,
        "simulation_fingerprint_sha256": hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "remote_manifest_directory": "/data/xum/vda_runs/manifest-sweep-12",
        "artifact_locations_checked": [
            {
                "binding": "exact_history",
                "history_root": f"/data/xum/results/{history}",
                "remote_manifest_path": (
                    "/data/xum/vda_runs/manifest-sweep-12/0_history.tsv"
                ),
            },
            {
                "source_location": "runtime",
                "binding": "unique_runtime_session",
                "tree_root": f"{runtime_root}/VDA/netlist",
                "history_root": None,
                "runtime_test": "VDA",
                "remote_manifest_path": (
                    "/data/xum/vda_runs/manifest-sweep-12/1_runtime.tsv"
                ),
            },
        ],
        "simulator_input_consistency_verified": True,
        "simulator_input_consistency": input_consistency,
        "simulator_input_consistency_evidence_sources": {
            "maestro_design_and_oa": "bridge_readback",
            "spectre_input": "eda_result",
            "comparison": "software_inference",
        },
        "expected_output_evaluation_errors_verified": True,
        "output_evaluation_errors": [],
        "output_evaluation_error_count": 0,
        "output_evaluation_error_evidence_sources": {
            "expected": None,
            "actual": "eda_result",
            "comparison": "software_inference",
        },
        "sweep_setup_readback_before": setup_readback,
        "sweep_setup_readback_after": setup_readback,
        "sweep_setup_readback_evidence_source": "bridge_readback",
        "expected_sweep_evidence_source": "user_input",
        "sweep_point_consistency_verified": True,
        "sweep_point_consistency": point_consistency,
        "effective_simulation_values_verified": True,
        "exact_point_input_result_binding_verified": True,
        "native_sweep_database_binding_verified": False,
        "sweep_point_evidence_mode": "exact_point_artifacts",
        "sweep_history_log_evidence": None,
        "sweep_result_database_artifacts": [],
        "sweep_consistency_evidence_sources": {
            "expected_sweep": "user_input",
            "maestro_setup_and_oa": "bridge_readback",
            "spectre_input_and_results": "eda_result",
            "comparison": "software_inference",
        },
    }


def _native_sweep_run_task() -> TaskSpec:
    return TaskSpec.model_validate(
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
                "sweep_verification": {
                    "expected_tests": ["VDA"],
                    "variables": [
                        {"name": "CL", "expected_value": "1f,2f"}
                    ],
                    "points": [
                        {"point": 1, "values": {"CL": "1f"}},
                        {"point": 2, "values": {"CL": "2f"}},
                    ],
                    "input_bindings": [
                        {
                            "test": "VDA",
                            "variable": "CL",
                            "instance": "CL0",
                            "oa_parameter": "c",
                        }
                    ],
                },
            },
            "safety": {"allow_remote_compute": True},
        }
    )


def _native_corner_sweep_run_task() -> TaskSpec:
    corners = ["Nominal", "VDA_LOW_VDD", "VDA_NOMINAL_VDD"]
    points = []
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
    return TaskSpec.model_validate(
        {
            "id": "run-native-corner-grid",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_sweep_tb",
                "view": "maestro",
            },
            "ade_run": {
                "require_simulator_input_consistency": True,
                "sweep_verification": {
                    "expected_tests": ["VDA"],
                    "expected_corners": corners,
                    "expected_global_variable_selections": {
                        "CL": False,
                        "VDD": True,
                    },
                    "variables": [
                        {
                            "name": "CL",
                            "scope": "test",
                            "scope_name": "VDA",
                            "expected_value": "1f,4f",
                        },
                        {
                            "name": "VDD",
                            "expected_value": "0.9",
                            "sweep": False,
                        },
                        {
                            "name": "VDD",
                            "scope": "corner",
                            "scope_name": "VDA_LOW_VDD",
                            "expected_value": "0.8",
                            "sweep": False,
                        },
                        {
                            "name": "VDD",
                            "scope": "corner",
                            "scope_name": "VDA_NOMINAL_VDD",
                            "expected_value": "0.9",
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
                },
            },
            "safety": {"allow_remote_compute": True},
        }
    )


def _mapped_native_sweep_run_task(*, delay_limit_ps: float = 5.0) -> TaskSpec:
    data = _native_sweep_run_task().model_dump(mode="json")
    data["id"] = "evaluate-native-cl-sweep"
    data["ade_run"]["result_mapping"] = {
        "parameters": [
            {
                "source": "CL",
                "parameter": "load_ff",
                "scale": 1e15,
                "unit": "fF",
            }
        ],
        "metrics": [
            {
                "test": "VDA",
                "output": "Delay",
                "expected_expression": "average(VT(\"/OUT\"))",
                "metric": "delay_ps",
                "scale": 1e12,
                "unit": "ps",
            },
            {
                "test": "VDA",
                "output": "Energy",
                "expected_expression": "integ(IT(\"/VDD0/PLUS\"))",
                "metric": "supply_energy_per_cycle_fj",
                "scale": 1e15,
                "unit": "fJ",
            },
        ],
    }
    data["constraints"] = [
        {"metric": "delay_ps", "relation": "<=", "value": delay_limit_ps}
    ]
    data["objective"] = {
        "metric": "supply_energy_per_cycle_fj",
        "goal": "minimize",
    }
    return TaskSpec.model_validate(data)


def _mapped_native_sweep_run_evidence(task: TaskSpec) -> dict:
    data = _native_sweep_run_evidence(task)
    raw_points = [
        (1, "1f", "3p", "4f"),
        (2, "2f", "6p", "2f"),
    ]
    data["structured_results"] = {
        "history": data["history"],
        "tests": ["VDA"],
        "points": [
            {
                "point": point,
                "parameters": {"CL": load},
                "outputs": {
                    "Delay": {"value": delay},
                    "Energy": {"value": energy},
                },
            }
            for point, load, delay, energy in raw_points
        ],
    }
    trusted_by_point = {
        item["point"]: item for item in data["sweep_point_consistency"]
    }
    for point, _load, delay, energy in raw_points:
        trusted = trusted_by_point[point]
        trusted["scalar_outputs"] = {
            "VoutAvg": trusted["scalar_outputs"]["VoutAvg"],
            "Delay": delay,
            "Energy": energy,
        }
        payload = {
            "point": trusted["point"],
            "expected_parameters": trusted["expected_parameters"],
            "result_parameters": trusted["result_parameters"],
            "scalar_outputs": trusted["scalar_outputs"],
            "tests": trusted["tests"],
        }
        trusted["point_binding_sha256"] = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    assert task.ade_run is not None
    assert task.ade_run.result_mapping is not None
    setup_outputs = [
        {
            "test": binding.test,
            "output": binding.output,
            "metric": binding.metric,
            "state": {
                "name": binding.output,
                "type": None,
                "signal_name": None,
                "expression": binding.expected_expression,
                "eval_type": "point",
                "plot": True,
                "save": True,
                "spec": None,
            },
        }
        for binding in task.ade_run.result_mapping.metrics
    ]
    setup_fingerprint = hashlib.sha256(
        json.dumps(
            setup_outputs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    setup_readback = {
        "outputs": setup_outputs,
        "fingerprint_sha256": setup_fingerprint,
    }
    data.update(
        {
            "result_mapping_setup_readback_before": setup_readback,
            "result_mapping_setup_readback_after": setup_readback,
            "result_mapping_setup_readback_evidence_source": "bridge_readback",
            "expected_result_mapping_evidence_source": "user_input",
            "result_mapping_setup_unchanged": True,
        }
    )
    return data


def _native_sweep_database_run_evidence(task: TaskSpec) -> dict:
    data = _native_sweep_run_evidence(task)
    history = data["history"]
    input_path = f"{history}/runtime/VDA/input.scs"
    netlist_path = f"{history}/runtime/VDA/netlist"
    rdb_path = f"{history}/{history}.rdb"
    log_path = f"{history}/{history}.log"
    input_hash = "b" * 64
    netlist_hash = "a" * 64
    comparison_hash = "c" * 64
    rdb_hash = "d" * 64
    log_hash = "e" * 64
    manifest = [
        {
            "path": netlist_path,
            "size_bytes": 120,
            "sha256": netlist_hash,
            "category": "simulator_input",
            "binding": "unique_runtime_session",
            "evidence_source": "eda_result",
            "remote_paths": [
                f"{data['runtime_scratch_root']}/VDA/netlist/netlist"
            ],
        },
        {
            "path": input_path,
            "size_bytes": 180,
            "sha256": input_hash,
            "category": "simulator_input",
            "binding": "unique_runtime_session",
            "evidence_source": "eda_result",
            "remote_paths": [
                f"{data['runtime_scratch_root']}/VDA/netlist/input.scs"
            ],
        },
        {
            "path": rdb_path,
            "size_bytes": 4096,
            "sha256": rdb_hash,
            "category": "eda_result",
            "binding": "exact_history_companion",
            "evidence_source": "eda_result",
            "remote_paths": [f"/data/xum/results/{history}.rdb"],
        },
        {
            "path": log_path,
            "size_bytes": 320,
            "sha256": log_hash,
            "category": "run_log",
            "binding": "exact_history_companion",
            "evidence_source": "eda_result",
            "remote_paths": [f"/data/xum/results/{history}.log"],
        },
    ]
    input_consistency = [
        {
            "test": "VDA",
            "input_path": input_path,
            "design_identity_verified": True,
            "instance_set_verified": True,
            "node_connectivity_verified": True,
            "raw_parameter_mapping_verified": True,
            "symbolic_sweep_bindings_verified": True,
            "effective_sweep_bindings_verified": False,
            "verified_sweep_binding_pairs": 1,
            "verified_parameter_pairs": 1,
            "retained_point": 1,
            "retained_sweep_values": {"CL": "1f"},
            "input_sha256": input_hash,
            "included_netlist_path": netlist_path,
            "included_netlist_sha256": netlist_hash,
            "input_bundle_sha256": hashlib.sha256(
                json.dumps(
                    {"input.scs": input_hash, "netlist": netlist_hash},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "comparison_sha256": comparison_hash,
        }
    ]
    point_consistency = []
    for point in data["sweep_point_consistency"]:
        point_payload = {
            "point": point["point"],
            "expected_parameters": point["expected_parameters"],
            "result_parameters": point["result_parameters"],
            "scalar_outputs": point["scalar_outputs"],
            "tests": [
                {
                    "test": "VDA",
                    "evidence_mode": (
                        "maestro_exact_history_rdb_with_shared_symbolic_"
                        "runtime_input"
                    ),
                    "inputs": [
                        {
                            "path": input_path,
                            "sha256": input_hash,
                            "included_netlist_path": netlist_path,
                            "included_netlist_sha256": netlist_hash,
                            "input_bundle_sha256": input_consistency[0][
                                "input_bundle_sha256"
                            ],
                            "comparison_sha256": comparison_hash,
                        }
                    ],
                    "result_artifacts": [
                        {
                            "path": rdb_path,
                            "sha256": rdb_hash,
                            "size_bytes": 4096,
                        }
                    ],
                }
            ],
        }
        point_consistency.append(
            {
                **point_payload,
                "point_binding_sha256": hashlib.sha256(
                    json.dumps(
                        point_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    counts = {
        category: sum(1 for item in manifest if item["category"] == category)
        for category in ("simulator_input", "eda_result", "run_log")
    }
    fingerprint_payload = sorted(
        (
            {"path": item["path"], "sha256": item["sha256"]}
            for item in manifest
            if item["category"] in counts
        ),
        key=lambda item: item["path"],
    )
    data.update(
        {
            "artifact_manifest": manifest,
            "artifact_counts": counts,
            "simulation_fingerprint_sha256": hashlib.sha256(
                json.dumps(
                    fingerprint_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "simulator_input_consistency": input_consistency,
            "sweep_point_consistency": point_consistency,
            "exact_point_input_result_binding_verified": False,
            "native_sweep_database_binding_verified": True,
            "sweep_point_evidence_mode": (
                "maestro_exact_history_rdb_with_shared_symbolic_runtime_input"
            ),
            "sweep_history_log_evidence": {
                "path": log_path,
                "sha256": log_hash,
                "size_bytes": 320,
                "points_completed": 2,
                "simulation_errors": 0,
                "simulation_errors_accounted_by_output_evaluation_errors": 0,
                "unaccounted_simulation_errors": 0,
                "history_completed": True,
            },
            "sweep_result_database_artifacts": [
                {"path": rdb_path, "sha256": rdb_hash, "size_bytes": 4096}
            ],
        }
    )
    return data


def _native_corner_sweep_database_run_evidence(task: TaskSpec) -> dict:
    data = _native_sweep_database_run_evidence(_native_sweep_run_task())
    assert task.ade_run is not None
    sweep = task.ade_run.sweep_verification
    assert sweep is not None
    data["target"] = task.target.model_dump(mode="json")
    setup_readback = {
        "tests": ["VDA"],
        "corners": list(sweep.expected_corners or []),
        "variables": {
            variable.evidence_key(): variable.expected_value
            for variable in sweep.variables
        },
        "variable_readback_methods": {
            "test:VDA:CL": (
                "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel"
            ),
            "VDD": "bridge_public_get_var",
            "corner:VDA_LOW_VDD:VDD": (
                "cadence_axlGetCorner_axlGetVarValue_via_bridge_skill_channel"
            ),
            "corner:VDA_NOMINAL_VDD:VDD": (
                "cadence_axlGetCorner_axlGetVarValue_via_bridge_skill_channel"
            ),
        },
        "global_variable_selections": {"CL": False, "VDD": True},
        "global_variable_selection_state": {
            "enabled": ["VDD"],
            "disabled": ["CL"],
        },
        "global_variable_selection_readback_method": (
            "cadence_maeGetSetup_enabled_variables_via_bridge_skill_channel"
        ),
        "fingerprint_sha256": "f" * 64,
    }
    data["sweep_setup_readback_before"] = setup_readback
    data["sweep_setup_readback_after"] = setup_readback

    input_consistency = data["simulator_input_consistency"][0]
    input_consistency["verified_sweep_binding_pairs"] = 3
    input_consistency.pop("retained_point")
    input_consistency["retained_maestro_point"] = 1
    input_consistency["retained_sweep_values"] = {"CL": "1f", "VDD": "0.9"}

    shared_tests = data["sweep_point_consistency"][0]["tests"]
    point_consistency = []
    for expected in sweep.points:
        point_payload = {
            "point": expected.point,
            "maestro_point": expected.maestro_point,
            "corner": expected.corner,
            "expected_parameters": dict(expected.values),
            "result_parameters": dict(expected.values),
            "scalar_outputs": {"VoutAvg": str(0.4 + expected.point / 100)},
            "tests": json.loads(json.dumps(shared_tests)),
        }
        point_consistency.append(
            {
                **point_payload,
                "point_binding_sha256": hashlib.sha256(
                    json.dumps(
                        point_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    data["sweep_point_consistency"] = point_consistency
    data["corner_detail_csv_sha256"] = "8" * 64
    data["corner_detail_csv_size_bytes"] = 2048
    data["corner_detail_csv_evidence_sources"] = {
        "raw": "eda_result",
        "parser": "software_inference",
    }
    return data


def test_ade_run_accepts_complete_native_sweep_point_evidence() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data=_native_sweep_run_evidence(task),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert any("every declared native Maestro sweep point" in note for note in record.notes)


def test_ade_run_maps_verified_scalar_outputs_to_constraints_and_selection() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data=_mapped_native_sweep_run_evidence(task),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _mapped_native_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions][-2:] == [
        "ade.run",
        "ade.results.evaluate",
    ]
    evaluation = record.actions[-1]
    assert evaluation.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    assert evaluation.details["raw_result_evidence_source"] == "eda_result"
    assert (
        evaluation.details["mapping_and_constraint_evidence_source"]
        == "software_inference"
    )
    assert len(record.candidates) == 2
    assert record.candidates[0].parameters == {"load_ff": pytest.approx(1.0)}
    assert record.candidates[0].metrics == {
        "delay_ps": pytest.approx(3.0),
        "supply_energy_per_cycle_fj": pytest.approx(4.0),
    }
    assert record.candidates[0].feasible is True
    assert record.candidates[1].feasible is False
    assert set(record.candidates[0].metric_sources.values()) == {
        EvidenceSource.SOFTWARE_INFERENCE
    }
    assert record.selected_parameters == {"load_ff": pytest.approx(1.0)}
    assert record.selected_metrics == record.candidates[0].metrics
    assert any("mapped exact-history Maestro scalar outputs" in note for note in record.notes)


def test_ade_run_marks_all_mapped_constraint_failures_partial() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data=_mapped_native_sweep_run_evidence(task),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _mapped_native_sweep_run_task(delay_limit_ps=2.0)
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert all(not candidate.feasible for candidate in record.candidates)
    assert record.selected_parameters is None
    assert record.selected_metrics is None
    assert any("none satisfied every VDA constraint" in note for note in record.notes)


def test_ade_run_rejects_result_mapping_expression_drift() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _mapped_native_sweep_run_evidence(task)
            before = json.loads(
                json.dumps(data["result_mapping_setup_readback_before"])
            )
            before["outputs"][0]["state"]["expression"] = 'ymax(VT("/OUT"))'
            before["fingerprint_sha256"] = hashlib.sha256(
                json.dumps(
                    before["outputs"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            data["result_mapping_setup_readback_before"] = before
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _mapped_native_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert record.actions[-1].action == "ade.results.evaluate"
    assert record.actions[-1].status == "failed"
    assert any("output expression did not match" in note for note in record.notes)


def test_ade_run_rejects_empty_mapped_output_as_evidence_failure() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _mapped_native_sweep_run_evidence(task)
            data["structured_results"]["points"][0]["outputs"]["Delay"]["value"] = ""
            trusted = data["sweep_point_consistency"][0]
            trusted["scalar_outputs"]["Delay"] = ""
            payload = {
                "point": trusted["point"],
                "expected_parameters": trusted["expected_parameters"],
                "result_parameters": trusted["result_parameters"],
                "scalar_outputs": trusted["scalar_outputs"],
                "tests": trusted["tests"],
            }
            trusted["point_binding_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _mapped_native_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert record.actions[-1].action == "ade.run"
    assert record.actions[-1].status == "succeeded"
    assert record.candidates == []
    assert any("non-empty scalar outputs" in note for note in record.notes)


@pytest.mark.parametrize("raw_value", ["nan", "inf"])
def test_ade_run_rejects_nonfinite_mapped_output(raw_value: str) -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _mapped_native_sweep_run_evidence(task)
            data["structured_results"]["points"][0]["outputs"]["Delay"][
                "value"
            ] = raw_value
            trusted = data["sweep_point_consistency"][0]
            trusted["scalar_outputs"]["Delay"] = raw_value
            payload = {
                "point": trusted["point"],
                "expected_parameters": trusted["expected_parameters"],
                "result_parameters": trusted["result_parameters"],
                "scalar_outputs": trusted["scalar_outputs"],
                "tests": trusted["tests"],
            }
            trusted["point_binding_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _mapped_native_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert record.actions[-1].action == "ade.results.evaluate"
    assert record.actions[-1].status == "failed"
    assert record.candidates == []
    assert any("empty or non-finite" in note for note in record.notes)


def test_ade_run_accepts_native_sweep_history_database_evidence() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data=_native_sweep_database_run_evidence(task),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert any("exact-history RDB/completion log" in note for note in record.notes)


def test_ade_run_accepts_exact_corner_grid_and_global_selection_evidence() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data=_native_corner_sweep_database_run_evidence(task),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_corner_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    run = next(action for action in record.actions if action.action == "ade.run")
    assert len(run.details["sweep_point_consistency"]) == 6
    assert run.details["sweep_history_log_evidence"]["points_completed"] == 2
    assert run.details["sweep_setup_readback_before"][
        "global_variable_selections"
    ] == {"CL": False, "VDD": True}


def test_ade_run_rejects_global_selection_map_that_conflicts_with_full_state() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_corner_sweep_database_run_evidence(task)
            for field in (
                "sweep_setup_readback_before",
                "sweep_setup_readback_after",
            ):
                data[field]["global_variable_selection_state"] = {
                    "enabled": ["CL", "VDD"],
                    "disabled": [],
                }
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_corner_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any(
        "global-variable selections" in note for note in record.notes
    )


def test_ade_run_accepts_unambiguous_completed_history_after_callback_timeout() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_corner_sweep_database_run_evidence(task)
            history = data["history"]
            data.update(
                {
                    "run_status": "recovered_after_bridge_timeout",
                    "simulation_performed_by_this_invocation": True,
                    "history_recovery_performed": False,
                    "callback_timeout_history_recovery_performed": True,
                    "callback_timeout_history_recovery_evidence": {
                        "method": (
                            "single_new_completed_history_log_after_bridge_timeout"
                        ),
                        "bridge_timeout": "Simulation did not finish within 90s",
                        "histories_before": ["Interactive.11"],
                        "new_histories": [history],
                        "completed_history_logs": [
                            {
                                "path": f"/data/xum/results/{history}.log",
                                "size_bytes": 320,
                                "sha256": "e" * 64,
                            }
                        ],
                        "evidence_sources": {
                            "history_log": "eda_result",
                            "selection": "software_inference",
                        },
                    },
                }
            )
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_corner_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert any(
        "exactly one newly named completed Maestro history" in note
        for note in record.notes
    )


def test_ade_run_rejects_callback_recovery_when_history_already_existed() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_corner_sweep_database_run_evidence(task)
            history = data["history"]
            data.update(
                {
                    "run_status": "recovered_after_bridge_timeout",
                    "simulation_performed_by_this_invocation": True,
                    "history_recovery_performed": False,
                    "callback_timeout_history_recovery_performed": True,
                    "callback_timeout_history_recovery_evidence": {
                        "method": (
                            "single_new_completed_history_log_after_bridge_timeout"
                        ),
                        "bridge_timeout": "Simulation did not finish within 90s",
                        "histories_before": [history],
                        "new_histories": [history],
                        "completed_history_logs": [
                            {
                                "path": f"/data/xum/results/{history}.log",
                                "size_bytes": 320,
                                "sha256": "e" * 64,
                            }
                        ],
                        "evidence_sources": {
                            "history_log": "eda_result",
                            "selection": "software_inference",
                        },
                    },
                }
            )
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_corner_sweep_run_task()
    plan = build_plan(task)
    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any(
        "callback-timeout recovery evidence was incomplete or ambiguous" in note
        for note in record.notes
    )


def test_ade_run_accepts_only_accounted_legacy_output_evaluation_errors() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_sweep_database_run_evidence(task)
            point = data["sweep_point_consistency"][0]
            point["scalar_outputs"]["LegacyRise"] = "eval err"
            payload = {
                "point": point["point"],
                "expected_parameters": point["expected_parameters"],
                "result_parameters": point["result_parameters"],
                "scalar_outputs": point["scalar_outputs"],
                "tests": point["tests"],
            }
            point["point_binding_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            data.update(
                {
                    "output_evaluation_errors": [
                        {
                            "point": 1,
                            "test": "VDA",
                            "output": "LegacyRise",
                            "point_values": {"CL": "1f"},
                            "expectation_evidence_source": "user_input",
                            "raw_value": "eval err",
                            "raw_evidence_source": "eda_result",
                        }
                    ],
                    "output_evaluation_error_count": 1,
                    "output_evaluation_error_evidence_sources": {
                        "expected": "user_input",
                        "actual": "eda_result",
                        "comparison": "software_inference",
                    },
                }
            )
            data["sweep_history_log_evidence"].update(
                {
                    "simulation_errors": 1,
                    "simulation_errors_accounted_by_output_evaluation_errors": 1,
                }
            )
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task_data = _native_sweep_run_task().model_dump(mode="json")
    task_data["ade_run"]["sweep_verification"][
        "expected_output_evaluation_errors"
    ] = [
        {
            "test": "VDA",
            "output": "LegacyRise",
            "point_values": {"CL": "1f"},
        }
    ]
    task = TaskSpec.model_validate(task_data)
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED


def test_ade_run_rejects_native_sweep_database_log_errors() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_sweep_database_run_evidence(task)
            data["sweep_history_log_evidence"]["simulation_errors"] = 1
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("history log" in note for note in record.notes)


def test_ade_run_rejects_a_sweep_point_without_effective_input_binding() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_sweep_run_evidence(task)
            data["effective_simulation_values_verified"] = False
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("sweep evidence" in note for note in record.notes)


def test_ade_run_rechecks_sweep_result_parameter_values() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_sweep_run_evidence(task)
            point = data["sweep_point_consistency"][1]
            point["result_parameters"]["CL"] = "4f"
            payload = {
                key: point[key]
                for key in (
                    "point",
                    "expected_parameters",
                    "result_parameters",
                    "scalar_outputs",
                    "tests",
                )
            }
            point["point_binding_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("result parameter CL" in note for note in record.notes)


def test_ade_run_correlates_point_inputs_with_oa_comparison_evidence() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            data = _native_sweep_run_evidence(task)
            point = data["sweep_point_consistency"][0]
            point["tests"][0]["inputs"][0]["comparison_sha256"] = "f" * 64
            payload = {
                key: point[key]
                for key in (
                    "point",
                    "expected_parameters",
                    "result_parameters",
                    "scalar_outputs",
                    "tests",
                )
            }
            point["point_binding_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = _native_sweep_run_task()
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("OA/input comparison" in note for note in record.notes)


def test_ade_run_rejects_incomplete_adapter_evidence() -> None:
    class UntrustedRunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data={"history": "Interactive.8"},
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro-untrusted",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {},
            "safety": {"allow_remote_compute": True},
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(UntrustedRunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("exact background history" in note for note in record.notes)


def test_ade_run_optional_missing_artifacts_is_partial() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data={
                    "session_mode": "background",
                    "setup_evidence_source": "bridge_readback",
                    "history": "Interactive.8",
                    "structured_results_available": True,
                    "structured_results_evidence_source": "eda_result",
                    "automated_simulation_performed": True,
                    "oa_write_performed": False,
                        "maestro_setup_write_performed": False,
                        "runtime_directory_persisted": False,
                        "runtime_directory_restored": True,
                        "runtime_artifacts_restricted_to_data_xum": True,
                        "runtime_scratch_root": "/data/xum/vda_runs/run-2",
                    "artifact_manifest_complete": False,
                    "artifacts_captured": False,
                    "artifact_capture_error": "remote manifest unavailable",
                },
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro-without-required-artifacts",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {"require_artifact_manifest": False},
            "safety": {"allow_remote_compute": True},
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert any("evidence gate" in note for note in record.notes)


def test_ade_run_rejects_corrupt_exact_history_artifact_evidence() -> None:
    class RunningAdapter(DeterministicDemoAdapter):
        def run_ade(self, task):
            return AdapterResult(
                data={
                    "session_mode": "background",
                    "setup_evidence_source": "bridge_readback",
                    "history": "Interactive.8",
                    "structured_results_available": True,
                    "structured_results_evidence_source": "eda_result",
                    "automated_simulation_performed": True,
                    "oa_write_performed": False,
                        "maestro_setup_write_performed": False,
                        "runtime_directory_persisted": False,
                        "runtime_directory_restored": True,
                        "runtime_artifacts_restricted_to_data_xum": True,
                        "runtime_scratch_root": "/data/xum/vda_runs/run-3",
                    "artifact_history": "Interactive.7",
                    "artifact_history_path_binding_verified": True,
                    "artifact_runtime_input_binding_verified": True,
                    "artifact_run_binding_verified": True,
                    "artifact_manifest_complete": True,
                    "artifacts_captured": True,
                    "artifact_counts": {
                        "simulator_input": 1,
                        "eda_result": 1,
                        "run_log": 1,
                    },
                    "artifact_manifest": [
                        {
                            "category": "simulator_input",
                            "evidence_source": "eda_result",
                        }
                    ],
                    "simulation_fingerprint_sha256": "a" * 64,
                },
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = TaskSpec.model_validate(
        {
            "id": "run-saved-maestro-corrupt-artifacts",
            "operation": "ade.run",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_manual_tb",
                "view": "maestro",
            },
            "ade_run": {},
            "safety": {"allow_remote_compute": True},
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(RunningAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("internally inconsistent" in note for note in record.notes)


def test_ade_variable_patch_records_exact_persistent_compare_and_swap() -> None:
    class VariableAdapter(DeterministicDemoAdapter):
        def apply_ade_variables(self, task):
            return AdapterResult(
                data={
                    "variable_scope": "declared_scopes",
                    "variable_scopes": ["global", "test", "corner"],
                    "expected_tests": ["VDA"],
                    "tests_readback_before": ["VDA"],
                    "tests_readback_after": ["VDA"],
                    "expected_corners": ["nominal", "TT"],
                    "corners_readback_before": ["nominal", "TT"],
                    "corners_readback_after": ["nominal", "TT"],
                        "requested_variable_updates": {
                        "bias_v": {
                            "name": "bias_v",
                            "scope": "global",
                            "scope_name": None,
                            "expected_value": "0.35",
                            "value": "0.40",
                        },
                        "test:VDA:bias_v": {
                            "name": "bias_v",
                            "scope": "test",
                            "scope_name": "VDA",
                            "expected_value": None,
                            "value": "0.30,0.35,0.40",
                        },
                        "corner:TT:vdd": {
                            "name": "vdd",
                            "scope": "corner",
                            "scope_name": "TT",
                            "expected_value": "0.9",
                            "value": "0.95",
                            },
                        },
                        "requested_global_selection_updates": {
                            "CL": {
                                "expected_enabled": True,
                                "enabled": False,
                            }
                        },
                        "requested_evidence_source": "user_input",
                    "before_variables": {
                        "bias_v": "0.35",
                        "test:VDA:bias_v": None,
                        "corner:TT:vdd": "0.9",
                    },
                    "immediate_variables": {
                        "bias_v": "0.40",
                        "test:VDA:bias_v": "0.30,0.35,0.40",
                        "corner:TT:vdd": "0.95",
                    },
                        "persisted_variables": {
                        "bias_v": "0.40",
                        "test:VDA:bias_v": "0.30,0.35,0.40",
                            "corner:TT:vdd": "0.95",
                        },
                        "global_variable_selection_before": {"CL": True},
                        "global_variable_selection_immediate": {"CL": False},
                        "global_variable_selection_persisted": {"CL": False},
                        "global_variable_selection_state_before": {
                            "enabled": ["CL", "VDD"],
                            "disabled": [],
                        },
                        "global_variable_selection_state_immediate": {
                            "enabled": ["VDD"],
                            "disabled": ["CL"],
                        },
                        "global_variable_selection_state_persisted": {
                            "enabled": ["VDD"],
                            "disabled": ["CL"],
                        },
                        "global_variable_selection_readback_method": (
                            "cadence_maeGetSetup_enabled_variables_"
                            "via_bridge_skill_channel"
                        ),
                        "global_variable_selection_write_method": (
                            "cadence_maeSetSetup_variables_via_bridge_skill_channel"
                        ),
                        "global_variable_selection_preserved_undeclared": True,
                        "confirmed_evidence_source": "bridge_readback",
                        "declared_scoped_values_verified": True,
                        "declared_global_selections_verified": True,
                    "variable_readback_methods": {
                        "global": "bridge_public_get_var",
                        "test": (
                            "cadence_maeGetVar_string_typeValue_"
                            "via_bridge_skill_channel"
                        ),
                        "corner": (
                            "cadence_axlGetCorner_axlGetVarValue_"
                            "via_bridge_skill_channel"
                        ),
                    },
                    "variable_write_methods": {
                        "global": "bridge_public_set_var_global",
                        "test": "bridge_public_set_var_list_typeValue",
                        "corner": "cadence_axlPutVar_via_bridge_skill_channel",
                    },
                    "test_or_corner_overrides_checked": False,
                    "unlisted_scope_overrides_checked": False,
                    "effective_simulation_value_verified": False,
                    "existing_maestro_replaced": False,
                    "schematic_oa_write_performed": False,
                    "maestro_setup_write_performed": True,
                    "automated_simulation_performed": False,
                },
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

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
                "expected_corners": ["nominal", "TT"],
                "updates": [
                    {
                        "name": "bias_v",
                        "expected_value": "0.35",
                        "value": "0.40",
                    },
                    {
                        "name": "bias_v",
                        "scope": "test",
                        "scope_name": "VDA",
                        "expected_value": None,
                        "value": "0.30,0.35,0.40",
                    },
                    {
                        "name": "vdd",
                        "scope": "corner",
                        "scope_name": "TT",
                        "expected_value": "0.9",
                        "value": "0.95",
                    },
                ],
                "global_selection_updates": [
                    {
                        "name": "CL",
                        "expected_enabled": True,
                        "enabled": False,
                    }
                ],
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(VariableAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.variables.apply",
    ]
    assert record.actions[-1].evidence_source is EvidenceSource.BRIDGE_READBACK
    assert record.candidates == []
    assert any("old-value preconditions" in note for note in record.notes)
    assert any("no simulation" in note for note in record.notes)


@pytest.mark.parametrize(
    ("immediate_value", "persisted_value", "readback_methods"),
    [
        ("0.38", "0.40", {"global": "bridge_public_get_var"}),
        ("0.40", "0.38", {"global": "bridge_public_get_var"}),
        (
            "0.40",
            "0.40",
            {
                "global": (
                    "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel"
                )
            },
        ),
    ],
)
def test_ade_variable_patch_rejects_untrusted_readback(
    immediate_value: str,
    persisted_value: str,
    readback_methods: dict[str, str],
) -> None:
    class UntrustedVariableAdapter(DeterministicDemoAdapter):
        def apply_ade_variables(self, task):
            return AdapterResult(
                data={
                    "variable_scope": "global",
                    "variable_scopes": ["global"],
                    "expected_tests": ["VDA"],
                    "tests_readback_before": ["VDA"],
                    "tests_readback_after": ["VDA"],
                    "expected_corners": None,
                    "corners_readback_before": None,
                    "corners_readback_after": None,
                    "requested_variable_updates": {
                        "bias_v": {
                            "name": "bias_v",
                            "scope": "global",
                            "scope_name": None,
                            "expected_value": "0.35",
                            "value": "0.40",
                        }
                    },
                    "requested_evidence_source": "user_input",
                    "before_variables": {"bias_v": "0.35"},
                    "immediate_variables": {"bias_v": immediate_value},
                    "persisted_variables": {"bias_v": persisted_value},
                    "confirmed_evidence_source": "bridge_readback",
                    "declared_scoped_values_verified": True,
                    "variable_readback_methods": readback_methods,
                    "variable_write_methods": {
                        "global": "bridge_public_set_var_global"
                    },
                    "test_or_corner_overrides_checked": False,
                    "unlisted_scope_overrides_checked": False,
                    "effective_simulation_value_verified": False,
                    "existing_maestro_replaced": False,
                    "schematic_oa_write_performed": False,
                    "maestro_setup_write_performed": True,
                    "automated_simulation_performed": False,
                },
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    task = TaskSpec.model_validate(
        {
            "id": "patch-maestro-variables-untrusted",
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
                        "expected_value": "0.35",
                        "value": "0.40",
                    }
                ],
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    plan = build_plan(task)

    record = TaskExecutor(UntrustedVariableAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("compare-and-swap" in note for note in record.notes)


def _ade_corner_task() -> TaskSpec:
    return TaskSpec.model_validate(
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
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def _ade_corner_evidence(task: TaskSpec) -> dict:
    assert task.ade_corners is not None
    additions = [addition.name for addition in task.ade_corners.additions]
    return {
        "target": task.target.model_dump(mode="json"),
        "expected_tests": ["VDA"],
        "tests_readback_before": ["VDA"],
        "tests_readback_after": ["VDA"],
        "expected_corners_before": [],
        "requested_corner_additions": additions,
        "all_corners_readback_before": [],
        "enabled_corners_readback_before": [],
        "immediate_corner_states": [
            {
                "name": "VDA_LOW",
                "all_corners": ["VDA_LOW"],
                "enabled_corners": ["VDA_LOW"],
            },
            {
                "name": "VDA_NOMINAL",
                "all_corners": ["VDA_LOW", "VDA_NOMINAL"],
                "enabled_corners": ["VDA_LOW", "VDA_NOMINAL"],
            },
        ],
        "all_corners_readback_after": ["VDA_LOW", "VDA_NOMINAL"],
        "enabled_corners_readback_after": ["VDA_LOW", "VDA_NOMINAL"],
        "requested_evidence_source": "user_input",
        "confirmed_evidence_source": "bridge_readback",
        "before_target_fingerprint_sha256": "a" * 64,
        "after_target_fingerprint_sha256": "b" * 64,
        "corner_write_method": "bridge_public_set_corner",
        "corner_readback_method": (
            "cadence_maeGetSetup_all_and_enabled_via_bridge_skill_channel"
        ),
        "existing_corners_modified": False,
        "existing_maestro_replaced": False,
        "model_files_modified": False,
        "variables_modified": False,
        "analyses_or_outputs_modified": False,
        "schematic_oa_write_performed": False,
        "maestro_setup_write_performed": True,
        "automated_simulation_performed": False,
    }


def test_ade_corner_patch_records_add_only_persistent_membership() -> None:
    task = _ade_corner_task()

    class CornerAdapter(DeterministicDemoAdapter):
        def apply_ade_corners(self, task):
            return AdapterResult(
                data=_ade_corner_evidence(task),
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    plan = build_plan(task)
    record = TaskExecutor(CornerAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.corners.apply",
    ]
    assert record.candidates == []
    assert any("all/enabled membership" in note for note in record.notes)
    assert any("did not attach process models" in note for note in record.notes)


@pytest.mark.parametrize("corruption", ["disabled", "modified", "fingerprint"])
def test_ade_corner_patch_rejects_untrusted_membership(corruption: str) -> None:
    task = _ade_corner_task()

    class UntrustedCornerAdapter(DeterministicDemoAdapter):
        def apply_ade_corners(self, task):
            data = _ade_corner_evidence(task)
            if corruption == "disabled":
                data["enabled_corners_readback_after"] = ["VDA_NOMINAL"]
            elif corruption == "modified":
                data["existing_corners_modified"] = True
            else:
                data["after_target_fingerprint_sha256"] = "a" * 64
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    plan = build_plan(task)
    record = TaskExecutor(UntrustedCornerAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("corner patch did not prove" in note for note in record.notes)


def test_ade_setup_patch_records_atomic_persistent_readback() -> None:
    task = _ade_setup_task()

    class SetupAdapter(DeterministicDemoAdapter):
        def apply_ade_setup(self, task):
            return AdapterResult(
                data=_ade_setup_evidence(task),
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    plan = build_plan(task)
    record = TaskExecutor(SetupAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "ade.setup.apply",
    ]
    assert record.actions[-1].evidence_source is EvidenceSource.BRIDGE_READBACK
    assert record.candidates == []
    assert any("absent named outputs" in note for note in record.notes)
    assert any("no simulation was run" in note for note in record.notes)


def test_ade_setup_patch_accepts_cadence_expression_canonicalization() -> None:
    task_data = _ade_setup_task().model_dump(mode="json")
    task_data["ade_setup"]["outputs"][0]["expression"] = (
        'cross(clip(VT("/OUT") 80p 130p) 0.45 1 "falling" nil nil nil) '
        '- cross(clip(VT("/IN") 80p 130p) 0.45 1 "rising" nil nil nil)'
    )
    task = TaskSpec.model_validate(task_data)

    class SetupAdapter(DeterministicDemoAdapter):
        def apply_ade_setup(self, task):
            data = _ade_setup_evidence(task)
            canonical = (
                '(cross(clip(VT("/OUT") 8e-11 1.3e-10) 0.45 1 "falling" '
                'nil nil nil) - cross(clip(VT("/IN") 8e-11 1.3e-10) 0.45 1 '
                '"rising" nil nil nil))'
            )
            data["immediate_outputs"][0]["state"]["expression"] = canonical
            data["persisted_outputs"][0]["state"]["expression"] = canonical
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    plan = build_plan(task)
    record = TaskExecutor(SetupAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("corruption", ["persisted", "fingerprint", "scope"])
def test_ade_setup_patch_rejects_untrusted_confirmation(corruption: str) -> None:
    task = _ade_setup_task()

    class UntrustedSetupAdapter(DeterministicDemoAdapter):
        def apply_ade_setup(self, task):
            data = _ade_setup_evidence(task)
            if corruption == "persisted":
                data["persisted_analyses"] = [
                    {
                        "test": "AC",
                        "analysis": "ac",
                        "state": {
                            "enabled": True,
                            "options": {"start": "1", "stop": "1G"},
                        },
                    }
                ]
            elif corruption == "fingerprint":
                data["after_target_fingerprint_sha256"] = "a" * 64
            else:
                data["unlisted_setup_state_checked"] = True
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

    plan = build_plan(task)
    record = TaskExecutor(UntrustedSetupAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("ADE setup patch did not prove" in note for note in record.notes)


def test_explicit_parameter_apply_fails_on_untrusted_adapter_confirmation() -> None:
    class MismatchedConfirmationAdapter(DeterministicDemoAdapter):
        def apply_parameters(self, task, parameters):
            result = super().apply_parameters(task, parameters)
            data = dict(result.data)
            data["confirmed_instance_parameters"] = {
                "MN0": {"fingers": "3", "m": "1"},
                "RD0": {"r": "22k"},
            }
            return AdapterResult(data=data, evidence_source=result.evidence_source)

    task = _explicit_parameter_apply()
    adapter = MismatchedConfirmationAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("confirmation mismatch" in note for note in record.notes)


def test_semantic_and_explicit_instance_parameters_are_both_applied() -> None:
    task = _explicit_parameter_apply().model_copy(
        update={"parameters": {"device_width_um": 0.5}}
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task.model_copy(update={"parameters": {}}))
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters == {"device_width_um": 0.5}
    readback = adapter.inspect_schematic(task).data
    assert readback["semantic_parameters"]["device_width_um"] == pytest.approx(0.5)
    assert readback["instance_parameters"]["MN0"]["fingers"] == "2"


def test_existing_schematic_parameter_surface_preserves_empty_and_long_values() -> None:
    long_value = "x" * 256
    task = TaskSpec.model_validate(
        {
            "id": "existing-schematic-params",
            "operation": "parameters.apply",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_existing"},
            "instance_parameter_updates": [
                {
                    "instance": "I0<3>",
                    "parameters": {"empty": "", "long": long_value},
                }
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    confirmed = next(
        action.details["confirmed_instance_parameters"]
        for action in record.actions
        if action.action == "schematic.inspect.after"
    )
    assert confirmed == {"I0<3>": {"empty": "", "long": long_value}}


def test_explicit_parameters_preserve_bridge_wf_and_nf_shorthands() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "bridge-parameter-shorthands",
            "operation": "parameters.apply",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"wf": "0.6u", "nf": "2"}}
            ],
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    applied = next(
        action.details
        for action in record.actions
        if action.action == "parameters.apply"
    )
    assert applied["requested_instance_parameters"] == {
        "MN0": {"wf": "0.6u", "nf": "2"}
    }
    assert applied["applied_instance_parameters"] == {
        "MN0": {"Wfg": "0.6u", "fingers": "2"}
    }


def test_demo_close_loop_selects_and_applies_feasible_candidate() -> None:
    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 9
    assert record.selected_parameters is not None
    assert record.selected_parameters["nmos_width_um"] == 0.6
    assert record.selected_parameters["pmos_width_um"] == 1.2
    assert any(action.action == "parameters.apply.best" for action in record.actions)
    assert all(
        candidate.evidence_source.value == "software_inference"
        for candidate in record.candidates
    )
    assert all(
        candidate.metric_sources["gate_area_proxy_um2"].value
        == "software_inference"
        for candidate in record.candidates
    )


def test_simulation_record_uses_actual_schematic_parameters_when_omitted() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "simulate-current-oa",
            "operation": "simulation.run",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"load_ff": 2.0, "vdd_v": 0.9},
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.candidates[0].parameters["nmos_width_um"] == 0.5
    assert record.candidates[0].parameters["pmos_width_um"] == 1.0
    assert record.candidates[0].parameters["length_um"] == 0.03


def test_infeasible_search_does_not_write_best_attempt_to_oa() -> None:
    task = _close_loop(delay_limit=1.0)
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert record.selected_parameters is None
    assert record.selected_metrics is None
    assert not any(action.action == "parameters.apply.best" for action in record.actions)
    assert any(action.action == "parameters.restore" for action in record.actions)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }
    assert any("no feasible candidate was committed" in note for note in record.notes)


def test_missing_objective_metric_blocks_writeback() -> None:
    data = _close_loop().model_dump(mode="json")
    data["objective"] = {"metric": "missing_energy_fj", "goal": "minimize"}
    task = TaskSpec.model_validate(data)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert not any(action.action == "parameters.apply.best" for action in record.actions)


def test_partial_candidate_failure_downgrades_run_status() -> None:
    class FlakyAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            if parameters["nmos_width_um"] == 0.4:
                raise RuntimeError("injected candidate failure")
            return super().simulate(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    record = TaskExecutor(FlakyAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert record.status is RunStatus.PARTIAL
    assert any("candidate evaluation(s) failed" in note for note in record.notes)
    assert any(action.action == "parameters.apply.best" for action in record.actions)


def test_search_budget_exhaustion_is_explicit() -> None:
    data = _close_loop().model_dump(mode="json")
    data["limits"]["max_iterations"] = 2
    task = TaskSpec.model_validate(data)
    plan = build_plan(task)
    record = TaskExecutor(DeterministicDemoAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )
    assert len(record.candidates) == 2
    assert record.status is RunStatus.PARTIAL
    assert any("search budget exhausted" in note for note in record.notes)


def test_interrupted_search_restores_initial_oa_parameters() -> None:
    class InterruptingAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.simulations = 0

        def simulate(self, task, parameters):
            self.simulations += 1
            if self.simulations == 2:
                raise KeyboardInterrupt
            return super().simulate(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    adapter = InterruptingAdapter()
    executor = TaskExecutor(adapter)
    with pytest.raises(KeyboardInterrupt):
        executor.execute(task, plan, token=plan.confirmation_token)

    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }
    assert any(
        action.action == "parameters.restore.interrupted"
        for action in executor.actions
    )
    assert any(
        action.action == "simulation.candidate.2" and action.status == "failed"
        for action in executor.actions
    )


class _RecoveringBridgeAdapter(DeterministicDemoAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.simulations: list[tuple[float, float]] = []
        self.interrupted = False

    def simulate(self, task, parameters):
        point = (
            parameters["nmos_width_um"],
            parameters["pmos_width_um"],
        )
        self.simulations.append(point)
        if point == (0.4, 1.0) and not self.interrupted:
            self.interrupted = True
            raise BridgeWorkerError("injected SSH reset")
        return super().simulate(task, parameters)


def _incomplete_checkpoint(tmp_path):
    task = _close_loop()
    plan = build_plan(task)
    adapter = _RecoveringBridgeAdapter()
    checkpoint_path = tmp_path / "loop.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    return task, plan, adapter, checkpoint_path, first


def test_bridge_interruption_resumes_without_repeating_completed_prefix(
    tmp_path,
) -> None:
    task, plan, adapter, checkpoint_path, first = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.complete is False
    assert checkpoint.next_candidate_index == 2
    assert [candidate.index for candidate in checkpoint.candidates] == [1]
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.5,
        "pmos_width_um": 1.0,
        "length_um": 0.03,
    }

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )
    completed = load_execution_checkpoint(checkpoint_path)

    assert resumed.status is RunStatus.SUCCEEDED
    assert completed.complete is True
    assert completed.next_candidate_index == 10
    assert len(resumed.candidates) == 9
    assert adapter.simulations.count((0.4, 0.8)) == 1
    assert adapter.simulations.count((0.4, 1.0)) == 2
    assert any("resumed candidate search at index 2" in note for note in resumed.notes)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.6,
        "pmos_width_um": 1.2,
        "length_um": 0.03,
    }


def test_resume_rejects_changed_task_plan_before_adapter_actions(tmp_path) -> None:
    task, _, adapter, checkpoint_path, _ = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)
    changed = task.model_copy(
        update={
            "constraints": [
                task.constraints[0].model_copy(update={"value": 44.0}),
                *task.constraints[1:],
            ]
        }
    )
    changed_plan = build_plan(changed)
    executor = TaskExecutor(adapter)

    with pytest.raises(ValueError, match="plan token"):
        executor.execute(
            changed,
            changed_plan,
            token=changed_plan.confirmation_token,
            checkpoint_path=checkpoint_path,
            resume_checkpoint=checkpoint,
        )
    assert executor.actions == []


def test_checkpoint_candidate_allows_only_matching_oa_derived_extras() -> None:
    declared = {
        "vdd_v": 0.9,
        "bias_v": 0.35,
        "source_resistance_ohm": 500.0,
    }
    initial_oa = {
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 22_000.0,
        "source_resistance_ohm": 1_000.0,
    }
    actual = {
        **declared,
        "device_width_um": 1.0,
        "length_um": 0.03,
        "load_resistance_ohm": 22_000.0,
    }

    assert TaskExecutor._checkpoint_candidate_matches(
        declared, actual, initial_oa
    )
    assert not TaskExecutor._checkpoint_candidate_matches(
        declared, actual | {"untrusted_extra": 1.0}, initial_oa
    )
    assert not TaskExecutor._checkpoint_candidate_matches(
        declared, actual | {"device_width_um": 1.1}, initial_oa
    )


def test_resume_refuses_unrecognized_current_oa_parameters(tmp_path) -> None:
    task, plan, adapter, checkpoint_path, _ = _incomplete_checkpoint(tmp_path)
    checkpoint = load_execution_checkpoint(checkpoint_path)
    adapter.apply_parameters(
        task,
        {
            "nmos_width_um": 0.7,
            "pmos_width_um": 1.4,
            "length_um": 0.03,
        },
    )

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.FAILED
    assert any("refusing automatic resume" in note for note in resumed.notes)
    assert not any(
        action.action.startswith("parameters.stage.")
        for action in resumed.actions[len(checkpoint.actions) :]
    )
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "nmos_width_um": 0.7,
        "pmos_width_um": 1.4,
        "length_um": 0.03,
    }


def test_resume_after_final_write_interruption_does_not_repeat_simulations(
    tmp_path,
) -> None:
    class FinalizeInterruptingAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.simulations = 0
            self.interrupted = False

        def simulate(self, task, parameters):
            self.simulations += 1
            return super().simulate(task, parameters)

        def apply_parameters(self, task, parameters):
            final_best = (
                parameters.get("nmos_width_um") == 0.6
                and parameters.get("pmos_width_um") == 1.2
                and self.simulations == 9
            )
            if final_best and not self.interrupted:
                self.interrupted = True
                raise BridgeWorkerError("injected final write reset")
            return super().apply_parameters(task, parameters)

    task = _close_loop()
    plan = build_plan(task)
    adapter = FinalizeInterruptingAdapter()
    checkpoint_path = tmp_path / "finalize.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 10
    assert len(checkpoint.candidates) == 9
    assert adapter.simulations == 9

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.simulations == 9
    assert load_execution_checkpoint(checkpoint_path).complete is True
    assert any(
        action.action == "parameters.apply.best" and action.status == "succeeded"
        for action in resumed.actions
    )


def _common_source_loop() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "cs-loop",
            "operation": "design.close_loop",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "vdd_v": 0.9,
            },
            "parameter_space": {
                "device_width_um": [0.5, 1.0, 1.5],
                "bias_v": [0.45],
            },
            "constraints": [
                {
                    "metric": "drain_current_ua",
                    "relation": "target",
                    "value": 20.0,
                    "tolerance": 1.0,
                },
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
            ],
            "objective": {
                "metric": "output_swing_margin_v",
                "goal": "maximize",
            },
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 3, "timeout_seconds": 600},
        }
    )


def test_common_source_demo_closes_dc_operating_point_with_oa_readback() -> None:
    task = _common_source_loop()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.0)
    assert record.selected_parameters["bias_v"] == pytest.approx(0.45)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(20_000.0),
    }
    assert all(
        candidate.evidence_source.value == "software_inference"
        for candidate in record.candidates
    )


def _common_source_ac_run(*, stop_hz: float = 1e11) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": f"cs-ac-{int(stop_hz)}",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": stop_hz,
                "points_per_decade": 20,
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e9,
                },
                {
                    "metric": "gain_bandwidth_product_hz",
                    "relation": ">=",
                    "value": 5e9,
                },
            ],
            "safety": {"allow_remote_compute": True},
        }
    )


def test_common_source_ac_is_a_formal_simulation_run_capability() -> None:
    task = _common_source_ac_run()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 1
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.analysis_issues == []
    assert candidate.metrics["low_frequency_gain_v_per_v"] >= 2.0
    assert candidate.metrics["bandwidth_3db_hz"] >= 1e9
    assert candidate.metrics["gain_bandwidth_product_hz"] >= 5e9
    assert candidate.metrics["unity_gain_frequency_hz"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def test_common_source_linearity_is_a_formal_simulation_run_capability() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-linearity-run",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_linearity"},
            "analysis": "transient",
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05, 0.15],
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 1.0,
            },
            "constraints": [
                {
                    "metric": "max_thd_percent",
                    "relation": "<=",
                    "value": 10.0,
                },
                {
                    "metric": "max_average_supply_power_uw",
                    "relation": "<=",
                    "value": 100.0,
                },
            ],
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.metrics["small_signal_gain_v_per_v"] > 0
    assert candidate.metrics["input_1db_compression_v_peak"] > 0
    assert candidate.metrics["max_thd_percent"] <= 10.0
    assert candidate.metrics["dc_supply_power_uw"] > 0
    assert candidate.metrics["max_average_supply_power_uw"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def test_common_source_noise_is_a_formal_simulation_run_capability() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-noise-run",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_noise"},
            "analysis": "noise",
            "noise_sweep": {
                "start_hz": 1e3,
                "stop_hz": 1e9,
                "points_per_decade": 20,
            },
            "parameters": {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 1.0,
            },
            "constraints": [
                {
                    "metric": "integrated_input_referred_noise_uv_rms",
                    "relation": "<=",
                    "value": 1e6,
                }
            ],
            "safety": {"allow_remote_compute": True},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.metrics["integrated_output_noise_uv_rms"] > 0
    assert candidate.metrics["integrated_input_referred_noise_uv_rms"] > 0
    assert candidate.metrics["dc_supply_power_uw"] > 0
    assert all(
        source.value == "software_inference"
        for source in candidate.metric_sources.values()
    )


def _common_source_quality_task(**updates) -> TaskSpec:
    data = {
        "id": "cs-quality-run",
        "operation": "simulation.run",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_cs_quality"},
        "analysis": "quality",
        "ac_sweep": {
            "start_hz": 1e4,
            "stop_hz": 1e11,
            "points_per_decade": 20,
        },
        "linearity_sweep": {
            "frequency_hz": 100e6,
            "amplitudes_v": [0.005, 0.05, 0.15],
        },
        "noise_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "points_per_decade": 20,
        },
        "parameters": {
            "device_width_um": 1.0,
            "length_um": 0.03,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        "constraints": [
            {"metric": "saturation_margin_v", "relation": ">=", "value": 0.01},
            {
                "metric": "gain_bandwidth_product_hz",
                "relation": ">=",
                "value": 1e9,
            },
            {"metric": "input_1db_compression_v_peak", "relation": ">=", "value": 0.01},
            {"metric": "max_thd_percent", "relation": "<=", "value": 100.0},
            {
                "metric": "integrated_input_referred_noise_uv_rms",
                "relation": "<=",
                "value": 1e6,
            },
        ],
        "safety": {"allow_remote_compute": True},
    }
    data.update(updates)
    return TaskSpec.model_validate(data)


def test_common_source_quality_combines_all_required_metrics() -> None:
    task = _common_source_quality_task()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    candidate = record.candidates[0]
    assert candidate.analysis_complete is True
    assert candidate.analysis_issues == []
    for metric in (
        "gain_bandwidth_product_hz",
        "input_1db_compression_v_peak",
        "max_thd_percent",
        "integrated_input_referred_noise_uv_rms",
        "dc_supply_power_uw",
    ):
        assert metric in candidate.metrics
    action = next(
        item for item in record.actions if item.action == "simulation.candidate.1"
    )
    assert action.details["analysis_bundle"]["analyses"] == [
        "ac",
        "transient",
        "noise",
    ]


def test_common_source_quality_rejects_one_incomplete_member() -> None:
    class MissingNoiseAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            result = super().simulate(task, parameters)
            if task.resolved_analysis() is AnalysisKind.NOISE:
                data = dict(result.data)
                data["analysis_complete"] = False
                data["analysis_issues"] = ["noise waveform is empty"]
                return AdapterResult(data=data, evidence_source=result.evidence_source)
            return result

    task = _common_source_quality_task(constraints=[])
    adapter = MissingNoiseAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert record.candidates[0].analysis_complete is False
    assert record.candidates[0].feasible is False
    assert "noise: noise waveform is empty" in record.candidates[0].analysis_issues


def test_common_source_quality_testbench_tuning_is_bounded_and_does_not_write_oa() -> None:
    task = _common_source_quality_task(
        id="cs-quality-tune",
        operation="design.tune",
        parameters={"vdd_v": 0.9},
        parameter_space={"bias_v": [0.32, 0.35], "load_ff": [1.0, 4.0]},
        objective={
            "metric": "integrated_input_referred_noise_uv_rms",
            "goal": "minimize",
        },
        limits={"max_iterations": 2, "timeout_seconds": 600},
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    before = adapter.inspect_schematic(task).data["semantic_parameters"]
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.PARTIAL
    assert len(record.candidates) == 2
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert before == after
    assert not any(
        action.action.startswith("parameters.stage")
        or action.action in {"parameters.finalize", "parameters.restore"}
        for action in record.actions
    )
    assert any("budget exhausted" in note for note in record.notes)


def test_common_source_quality_design_tuning_writes_and_reads_back_best_oa() -> None:
    task = _common_source_quality_task(
        id="cs-quality-design-tune",
        operation="design.tune",
        parameters={
            "length_um": 0.03,
            "bias_v": 0.35,
            "vdd_v": 0.9,
            "load_ff": 1.0,
        },
        parameter_space={
            "device_width_um": [0.5, 1.0],
            "load_resistance_ohm": [20_000.0, 22_000.0],
            "source_resistance_ohm": [1_000.0, 2_000.0],
        },
        objective={"metric": "gain_bandwidth_product_hz", "goal": "maximize"},
        safety={
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
        limits={"max_iterations": 8, "timeout_seconds": 600},
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    adapter.transform_schematic(
        TaskSpec.model_validate(
            {
                "id": "cs-quality-design-tune-transform",
                "operation": "schematic.transform",
                "circuit": "common_source",
                "target": task.target.model_dump(),
                "parameters": {"source_resistance_ohm": 1_000.0},
                "safety": {
                    "allow_remote_write": True,
                    "allowed_library": "vda_test",
                },
            }
        )
    )
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.SUCCEEDED
    assert plan.requires_remote_write is True
    assert len(record.candidates) == 8
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert record.selected_parameters is not None
    for name in (
        "device_width_um",
        "load_resistance_ohm",
        "source_resistance_ohm",
    ):
        assert after[name] == pytest.approx(record.selected_parameters[name])
    assert sum(
        action.action.startswith("parameters.stage.") for action in record.actions
    ) == 8
    assert any(
        action.action == "parameters.apply.best" for action in record.actions
    )


def test_common_source_quality_joint_length_vdd_search_writes_only_oa_subset() -> None:
    task = _common_source_quality_task(
        id="cs-quality-length-vdd-tune",
        operation="design.tune",
        parameters={
            "device_width_um": 1.0,
            "load_resistance_ohm": 20_000.0,
            "bias_v": 0.35,
            "load_ff": 1.0,
        },
        parameter_space={
            "length_um": [0.03, 0.04],
            "vdd_v": [0.8, 0.9],
        },
        constraints=[
            {"metric": "saturation_region", "relation": ">=", "value": 1.0},
            {
                "metric": "gain_bandwidth_product_hz",
                "relation": ">=",
                "value": 1e9,
            },
        ],
        objective={"metric": "gain_bandwidth_product_hz", "goal": "maximize"},
        safety={
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
        limits={"max_iterations": 4, "timeout_seconds": 600},
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    readback = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.SUCCEEDED
    assert plan.requires_remote_write is True
    assert len(record.candidates) == 4
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert {
        (candidate.parameters["length_um"], candidate.parameters["vdd_v"])
        for candidate in record.candidates
    } == {(0.03, 0.8), (0.03, 0.9), (0.04, 0.8), (0.04, 0.9)}
    assert record.selected_parameters is not None
    assert readback["length_um"] == pytest.approx(
        record.selected_parameters["length_um"]
    )
    assert "vdd_v" not in readback
    staged = [
        action
        for action in record.actions
        if action.action.startswith("parameters.stage.")
    ]
    assert len(staged) == 4
    assert all("vdd_v" not in action.details["semantic_parameters"] for action in staged)


def test_pvt_bundle_requires_every_condition_and_uses_robust_objective() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-pvt-evaluate",
            "operation": "simulation.run",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_pvt"},
            "parameters": {"bias_v": 0.35},
            "operating_conditions": [
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
            ],
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {"metric": "dc_supply_power_uw", "relation": "<=", "value": 30.0},
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {"allow_remote_compute": True},
        }
    )

    class PvtEvidenceAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            common = {
                "device_width_um": 1.0,
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
            }
            rows = [
                (
                    task.operating_conditions[0],
                    common | {"vdd_v": 0.9},
                    {
                        "low_frequency_gain_v_per_v": 3.0,
                        "dc_supply_power_uw": 20.0,
                        "gain_bandwidth_product_hz": 8e9,
                    },
                ),
                (
                    task.operating_conditions[1],
                    common | {"vdd_v": 0.81},
                    {
                        "low_frequency_gain_v_per_v": 1.8,
                        "dc_supply_power_uw": 25.0,
                        "gain_bandwidth_product_hz": 5e9,
                    },
                ),
            ]
            return AdapterResult(
                data={
                    "parameters": common,
                    "operating_condition_results": [
                        {
                            "condition": condition.model_dump(mode="json"),
                            "result": {
                                "parameters": effective,
                                "metrics": metrics,
                                "metric_sources": {
                                    name: "eda_result" for name in metrics
                                },
                                "analysis_complete": True,
                                "analysis_issues": [],
                                "analysis_warnings": [],
                            },
                        }
                        for condition, effective, metrics in rows
                    ],
                    "analysis_complete": True,
                    "analysis_issues": [],
                    "analysis_warnings": [],
                },
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    adapter = PvtEvidenceAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    candidate = record.candidates[0]
    assert candidate.feasible is False
    assert candidate.analysis_complete is True
    assert candidate.metrics["low_frequency_gain_v_per_v"] == pytest.approx(1.8)
    assert candidate.metrics["dc_supply_power_uw"] == pytest.approx(25.0)
    assert candidate.objective_value == pytest.approx(5e9)
    assert candidate.metric_sources == {
        "low_frequency_gain_v_per_v": EvidenceSource.SOFTWARE_INFERENCE,
        "dc_supply_power_uw": EvidenceSource.SOFTWARE_INFERENCE,
        "gain_bandwidth_product_hz": EvidenceSource.SOFTWARE_INFERENCE,
    }
    assert [item.name for item in candidate.operating_conditions] == [
        "tt_25c_0p90v",
        "ss_125c_0p81v",
    ]
    assert candidate.operating_conditions[0].feasible is True
    assert candidate.operating_conditions[1].feasible is False
    assert any("ss_125c_0p81v" in note for note in record.notes)


def _pvt_tuning_task(
    *, widths: list[float] | None = None, max_iterations: int = 2
) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "cs-pvt-design-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_pvt_tune"},
            "analysis": "quality",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "linearity_sweep": {
                "frequency_hz": 100e6,
                "amplitudes_v": [0.005, 0.05],
            },
            "noise_sweep": {"start_hz": 1e3, "stop_hz": 1e10},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "load_ff": 1.0,
            },
            "parameter_space": {"device_width_um": widths or [1.0, 2.0]},
            "operating_conditions": [
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
            ],
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {"metric": "dc_supply_power_uw", "relation": "<=", "value": 30.0},
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {
                "max_iterations": max_iterations,
                "timeout_seconds": 600,
            },
        }
    )


class _SyntheticPvtTuningAdapter(DeterministicDemoAdapter):
    def __init__(
        self,
        *,
        all_infeasible: bool = False,
        interrupt_width: float | None = None,
    ) -> None:
        super().__init__()
        self.all_infeasible = all_infeasible
        self.interrupt_width = interrupt_width
        self.interrupted = False
        self.widths: list[float] = []

    def simulate(self, task, parameters):
        width = float(parameters["device_width_um"])
        self.widths.append(width)
        if self.interrupt_width == width and not self.interrupted:
            self.interrupted = True
            raise BridgeWorkerError("injected PVT transport reset")
        gbw_by_width = {
            1.0: (20e9, 4e9),
            2.0: (12e9, 8e9),
            3.0: (10e9, 9e9),
        }
        gbw = gbw_by_width[width]
        rows = []
        for index, condition in enumerate(task.operating_conditions):
            metrics = {
                "low_frequency_gain_v_per_v": (
                    1.0 if self.all_infeasible and index == 1 else 2.5
                ),
                "dc_supply_power_uw": 20.0 + 2.0 * width + index,
                "gain_bandwidth_product_hz": gbw[index],
            }
            effective = dict(parameters)
            effective["vdd_v"] = float(condition.vdd_v)
            rows.append(
                {
                    "condition": condition.model_dump(mode="json"),
                    "result": {
                        "parameters": effective,
                        "metrics": metrics,
                        "metric_sources": {
                            name: "eda_result" for name in metrics
                        },
                        "analysis_complete": True,
                        "analysis_issues": [],
                        "analysis_warnings": [],
                    },
                }
            )
        return AdapterResult(
            data={
                "parameters": dict(parameters),
                "operating_condition_results": rows,
                "analysis_complete": True,
                "analysis_issues": [],
                "analysis_warnings": [],
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


def test_pvt_design_tuning_selects_robust_candidate_and_writes_back(
    tmp_path,
) -> None:
    task = _pvt_tuning_task()
    adapter = _SyntheticPvtTuningAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    checkpoint_path = tmp_path / "pvt-tune.checkpoint.json"

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [candidate.objective_value for candidate in record.candidates] == [
        pytest.approx(4e9),
        pytest.approx(8e9),
    ]
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(2.0)
    assert adapter.inspect_schematic(task).data["semantic_parameters"][
        "device_width_um"
    ] == pytest.approx(2.0)
    assert all(
        len(candidate.operating_conditions) == 2
        for candidate in record.candidates
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)
    assert checkpoint.complete is True
    assert len(checkpoint.candidates[0].operating_conditions) == 2
    assert any("all declared operating conditions" in note for note in record.notes)


def test_pvt_candidate_rejects_per_condition_design_parameter_drift() -> None:
    task = _pvt_tuning_task()
    adapter = _SyntheticPvtTuningAdapter()
    parameters = dict(task.parameters) | {"device_width_um": 1.0}
    result = adapter.simulate(task, parameters)
    result.data["operating_condition_results"][1]["result"]["parameters"][
        "device_width_um"
    ] = 9.0

    with pytest.raises(RuntimeError, match="confirm candidate parameter"):
        TaskExecutor._evaluate_candidate(task, 1, parameters, result)


def test_all_infeasible_pvt_tuning_restores_initial_oa() -> None:
    task = _pvt_tuning_task()
    adapter = _SyntheticPvtTuningAdapter(all_infeasible=True)
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert all(not candidate.feasible for candidate in record.candidates)
    assert any(action.action == "parameters.restore" for action in record.actions)
    assert adapter.inspect_schematic(task).data["semantic_parameters"][
        "device_width_um"
    ] == pytest.approx(1.0)
    assert any("across every declared operating condition" in note for note in record.notes)


def test_pvt_tuning_budget_is_best_only_within_evaluated_prefix() -> None:
    task = _pvt_tuning_task(widths=[1.0, 2.0, 3.0], max_iterations=2)
    adapter = _SyntheticPvtTuningAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert len(record.candidates) == 2
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(2.0)
    assert any("evaluated prefix" in note for note in record.notes)


def test_pvt_tuning_checkpoint_resumes_completed_candidate_bundle(tmp_path) -> None:
    task = _pvt_tuning_task()
    adapter = _SyntheticPvtTuningAdapter(interrupt_width=2.0)
    adapter.create_schematic(task)
    plan = build_plan(task)
    checkpoint_path = tmp_path / "pvt-resume.checkpoint.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert len(checkpoint.candidates[0].operating_conditions) == 2

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.widths.count(1.0) == 1
    assert adapter.widths.count(2.0) == 2
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_common_source_ac_short_sweep_is_partial_not_a_fake_bandwidth() -> None:
    task = _common_source_ac_run(stop_hz=1e8).model_copy(
        update={"constraints": []}
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    candidate = record.candidates[0]
    assert candidate.analysis_complete is False
    assert "bandwidth_3db_hz" not in candidate.metrics
    assert any("sweep_stop" in issue for issue in candidate.analysis_issues)
    assert any("required analysis metrics" in note for note in record.notes)


def test_common_source_ac_metrics_participate_in_bounded_design_tuning() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_tune"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
            },
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "parameter_space": {"device_width_um": [0.5, 1.0, 1.5]},
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 1.5,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 2e9,
                },
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 3, "timeout_seconds": 600},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 3
    assert all(candidate.analysis_complete for candidate in record.candidates)
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.5)
    assert adapter.inspect_schematic(task).data["semantic_parameters"][
        "device_width_um"
    ] == pytest.approx(1.5)


def test_common_source_ac_can_tune_testbench_conditions_without_oa_write() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-testbench-tune",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_conditions"},
            "analysis": "ac",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e11,
                "points_per_decade": 20,
            },
            "parameters": {"vdd_v": 0.9},
            "parameter_space": {
                "bias_v": [0.3, 0.35],
                "load_ff": [1.0, 4.0],
            },
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 2.0,
                },
                {
                    "metric": "bandwidth_3db_hz",
                    "relation": ">=",
                    "value": 1e9,
                },
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "safety": {"allow_remote_compute": True},
            "limits": {"max_iterations": 4, "timeout_seconds": 600},
        }
    )
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    before = adapter.inspect_schematic(task).data["semantic_parameters"]
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data["semantic_parameters"]

    assert record.status is RunStatus.SUCCEEDED
    assert plan.requires_remote_write is False
    assert len(record.candidates) == 4
    assert record.selected_parameters is not None
    assert record.selected_parameters["load_ff"] == pytest.approx(1.0)
    assert before == after
    assert not any(
        action.action.startswith("parameters.stage") for action in record.actions
    )
    assert not any(
        action.action == "parameters.apply.best" for action in record.actions
    )
    assert any("without changing OA" in note for note in record.notes)


def test_ac_tuning_marks_mixed_complete_and_incomplete_candidates_partial() -> None:
    class IncompleteFirstCandidateAdapter(DeterministicDemoAdapter):
        def simulate(self, task, parameters):
            result = super().simulate(task, parameters)
            if float(parameters["device_width_um"]) != 0.5:
                return result
            data = dict(result.data)
            data["analysis_complete"] = False
            data["analysis_issues"] = [
                "bandwidth_3db_hz unresolved: injected_short_sweep"
            ]
            metrics = dict(data["metrics"])
            metrics.pop("bandwidth_3db_hz", None)
            metrics.pop("gain_bandwidth_product_hz", None)
            data["metrics"] = metrics
            return AdapterResult(data=data, evidence_source=result.evidence_source)

    task = TaskSpec.model_validate(
        {
            "id": "cs-ac-mixed-completeness",
            "operation": "design.close_loop",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs_ac_mixed"},
            "analysis": "ac",
            "ac_sweep": {"start_hz": 1e4, "stop_hz": 1e11},
            "parameters": {
                "length_um": 0.03,
                "load_resistance_ohm": 20_000.0,
                "bias_v": 0.35,
                "vdd_v": 0.9,
                "load_ff": 2.0,
            },
            "parameter_space": {"device_width_um": [0.5, 1.0]},
            "constraints": [
                {
                    "metric": "low_frequency_gain_v_per_v",
                    "relation": ">=",
                    "value": 1.5,
                }
            ],
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "create_if_missing": True,
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )
    adapter = IncompleteFirstCandidateAdapter()
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert record.candidates[0].analysis_complete is False
    assert record.candidates[1].analysis_complete is True
    assert record.selected_parameters is not None
    assert record.selected_parameters["device_width_um"] == pytest.approx(1.0)
    assert any("lacked required core metrics" in note for note in record.notes)


def test_common_source_infeasible_search_restores_oa_parameters() -> None:
    task = _common_source_loop().model_copy(
        update={
            "constraints": [
                _common_source_loop().constraints[0].model_copy(
                    update={"value": 1.0, "tolerance": 0.1}
                )
            ]
        }
    )
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.PARTIAL
    assert any(action.action == "parameters.restore" for action in record.actions)
    assert adapter.inspect_schematic(task).data["semantic_parameters"] == {
        "device_width_um": pytest.approx(1.0),
        "length_um": pytest.approx(0.03),
        "load_resistance_ohm": pytest.approx(20_000.0),
    }


def test_common_source_budget_exhaustion_is_explicit() -> None:
    base = _common_source_loop()
    task = base.model_copy(
        update={
            "parameter_space": {
                "device_width_um": [0.5, 1.0, 1.5],
                "bias_v": [0.35, 0.45],
            },
            "limits": base.limits.model_copy(update={"max_iterations": 2}),
        }
    )
    plan = build_plan(task)
    executor = TaskExecutor(DeterministicDemoAdapter())
    record = executor.execute(
        task, plan, token=plan.confirmation_token
    )

    assert len(record.candidates) == 2
    assert record.status is RunStatus.PARTIAL
    assert any("search budget exhausted" in note for note in record.notes)
    notes = list(record.notes)
    executor._note_budget_exhaustion(task, record.status, notes)
    assert sum(note.startswith("search budget exhausted") for note in notes) == 1


def test_common_source_checkpoint_resumes_completed_prefix(tmp_path) -> None:
    class InterruptingCommonSourceAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.widths: list[float] = []
            self.interrupted = False

        def simulate(self, task, parameters):
            width = float(parameters["device_width_um"])
            self.widths.append(width)
            if width == 1.0 and not self.interrupted:
                self.interrupted = True
                raise BridgeWorkerError("injected common-source transport reset")
            return super().simulate(task, parameters)

    base = _common_source_loop()
    task = base.model_copy(
        update={
            "parameter_space": {
                "device_width_um": [0.5, 1.0],
                "bias_v": [0.45],
            },
            "limits": base.limits.model_copy(update={"max_iterations": 2}),
        }
    )
    plan = build_plan(task)
    adapter = InterruptingCommonSourceAdapter()
    checkpoint_path = tmp_path / "common-source.checkpoint.json"
    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert [candidate.index for candidate in checkpoint.candidates] == [1]

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert adapter.widths.count(0.5) == 1
    assert adapter.widths.count(1.0) == 2
    assert load_execution_checkpoint(checkpoint_path).complete is True


def _source_degeneration_transform(resistance_ohm: float = 1_000.0) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": f"source-degeneration-{resistance_ohm:g}",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"source_resistance_ohm": resistance_ohm},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def _source_degeneration_removal() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "source-degeneration-removal",
            "operation": "schematic.transform",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "schematic_transform": {"action": "remove_source_degeneration"},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def _inverter_testbench_transform() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "inverter-testbench-transform",
            "operation": "schematic.transform",
            "circuit": "inverter",
            "target": {"library": "vda_test", "cell": "vda_inv"},
            "parameters": {"vdd_v": 0.9, "load_ff": 2.0},
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
        }
    )


def test_inverter_testbench_is_an_in_place_audited_delta() -> None:
    task = _inverter_testbench_transform()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    before = adapter.inspect_schematic(task).data
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters == {"vdd_v": 0.9, "load_ff": 2.0}
    assert before["pins"] == after["pins"]
    assert set(after["nets"]) == set(before["nets"]) | {"gnd!"}
    assert set(after["instance_parameters"]) == {
        "MN0",
        "MP0",
        "VDD0",
        "VIN0",
        "CL0",
        "GND0",
    }
    action = next(
        item
        for item in record.actions
        if item.action == "schematic.transform.inverter-testbench"
    )
    assert action.details["transformed"] is True


def test_executor_rejects_inverter_testbench_transform_that_changes_mos_size() -> None:
    class CorruptingTransformAdapter(DeterministicDemoAdapter):
        def transform_schematic(self, task):
            result = super().transform_schematic(task)
            self._schematics[self._key(task)]["instance_parameters"]["MN0"][
                "Wfg"
            ] = "9u"
            return result

    task = _inverter_testbench_transform()
    adapter = CorruptingTransformAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("changed MN0 parameters" in note for note in record.notes)


def test_source_degeneration_is_an_in_place_audited_delta() -> None:
    task = _source_degeneration_transform()
    adapter = DeterministicDemoAdapter()
    adapter.create_schematic(task)
    adapter.apply_parameters(_explicit_parameter_apply(), {})
    before = adapter.inspect_schematic(task).data
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )
    after = adapter.inspect_schematic(task).data

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_parameters == {"source_resistance_ohm": 1_000.0}
    assert before["pins"] == after["pins"]
    assert set(after["nets"]) == set(before["nets"]) | {"NSRC"}
    assert set(after["instance_parameters"]) == {"MN0", "RD0", "RS0"}
    assert after["instance_parameters"]["MN0"] == before["instance_parameters"]["MN0"]
    assert after["instance_parameters"]["RD0"] == before["instance_parameters"]["RD0"]
    assert after["instance_parameters"]["MN0"]["fingers"] == "2"
    assert after["instance_parameters"]["RD0"]["r"] == "22k"
    assert after["instance_parameters"]["RS0"]["r"] == "1000"
    assert after["topology_variant"] == "source_degenerated_common_source"
    assert next(
        item for item in after["instances"] if item["name"] == "MN0"
    )["terminals"]["S"] == "NSRC"


def test_source_degeneration_transform_is_idempotent_and_can_retarget_only_rs0() -> None:
    adapter = DeterministicDemoAdapter()
    first = _source_degeneration_transform()
    adapter.create_schematic(first)
    first_plan = build_plan(first)
    TaskExecutor(adapter).execute(
        first, first_plan, token=first_plan.confirmation_token
    )
    baseline = adapter.inspect_schematic(first).data

    repeated = _source_degeneration_transform()
    repeated_plan = build_plan(repeated)
    repeated_record = TaskExecutor(adapter).execute(
        repeated, repeated_plan, token=repeated_plan.confirmation_token
    )
    assert repeated_record.status is RunStatus.SUCCEEDED
    repeated_action = next(
        action
        for action in repeated_record.actions
        if action.action == "schematic.transform.source-degeneration"
    )
    assert repeated_action.details["already_transformed"] is True
    assert repeated_action.details["resistance_changed"] is False

    retarget = _source_degeneration_transform(2_000.0)
    retarget_plan = build_plan(retarget)
    retarget_record = TaskExecutor(adapter).execute(
        retarget, retarget_plan, token=retarget_plan.confirmation_token
    )
    after = adapter.inspect_schematic(retarget).data
    assert retarget_record.status is RunStatus.SUCCEEDED
    assert after["instance_parameters"]["MN0"] == baseline["instance_parameters"]["MN0"]
    assert after["instance_parameters"]["RD0"] == baseline["instance_parameters"]["RD0"]
    assert after["instance_parameters"]["RS0"]["r"] == "2000"


def test_source_degeneration_add_remove_restores_the_nominal_readback() -> None:
    adapter = DeterministicDemoAdapter()
    remove = _source_degeneration_removal()
    adapter.create_schematic(remove)
    nominal = adapter.inspect_schematic(remove).data

    add = _source_degeneration_transform()
    add_plan = build_plan(add)
    add_record = TaskExecutor(adapter).execute(
        add, add_plan, token=add_plan.confirmation_token
    )
    assert add_record.status is RunStatus.SUCCEEDED

    remove_plan = build_plan(remove)
    remove_record = TaskExecutor(adapter).execute(
        remove, remove_plan, token=remove_plan.confirmation_token
    )
    restored = adapter.inspect_schematic(remove).data

    assert remove_record.status is RunStatus.SUCCEEDED
    assert remove_record.selected_parameters == {}
    assert restored == nominal
    action = next(
        item
        for item in remove_record.actions
        if item.action == "schematic.transform.source-degeneration.remove"
    )
    assert action.details["transformed"] is True
    assert action.details["already_removed"] is False


def test_source_degeneration_removal_is_idempotent() -> None:
    adapter = DeterministicDemoAdapter()
    remove = _source_degeneration_removal()
    adapter.create_schematic(remove)
    plan = build_plan(remove)

    record = TaskExecutor(adapter).execute(
        remove, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    action = next(
        item
        for item in record.actions
        if item.action == "schematic.transform.source-degeneration.remove"
    )
    assert action.details["transformed"] is False
    assert action.details["already_removed"] is True


def test_executor_requires_declared_restoration_fingerprint_confirmation() -> None:
    adapter = DeterministicDemoAdapter()
    payload = _source_degeneration_removal().model_dump(mode="json")
    payload["schematic_transform"] = {
        "action": "remove_source_degeneration",
        "expected_restored_placement_sha256": "0" * 64,
    }
    remove = TaskSpec.model_validate(payload)
    adapter.create_schematic(remove)
    plan = build_plan(remove)

    record = TaskExecutor(adapter).execute(
        remove, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any(
        "did not confirm the declared restored placement fingerprint" in note
        for note in record.notes
    )


def test_executor_rejects_source_degeneration_removal_that_changes_rd0() -> None:
    class CorruptingRemovalAdapter(DeterministicDemoAdapter):
        def transform_schematic(self, task):
            result = super().transform_schematic(task)
            if task.schematic_transform is not None:
                self._schematics[self._key(task)]["instance_parameters"]["RD0"][
                    "r"
                ] = "99k"
            return result

    adapter = CorruptingRemovalAdapter()
    remove = _source_degeneration_removal()
    adapter.create_schematic(remove)
    add = _source_degeneration_transform()
    adapter.transform_schematic(add)
    plan = build_plan(remove)

    record = TaskExecutor(adapter).execute(
        remove, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("changed RD0 parameters" in note for note in record.notes)


def test_executor_rejects_transform_that_changes_an_existing_instance() -> None:
    class CorruptingTransformAdapter(DeterministicDemoAdapter):
        def transform_schematic(self, task):
            result = super().transform_schematic(task)
            self._schematics[self._key(task)]["instance_parameters"]["MN0"][
                "fingers"
            ] = "9"
            return result

    task = _source_degeneration_transform()
    adapter = CorruptingTransformAdapter()
    adapter.create_schematic(task)
    plan = build_plan(task)
    record = TaskExecutor(adapter).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.FAILED
    assert any("unexpectedly changed MN0 parameters" in note for note in record.notes)


def test_source_resistance_uses_the_existing_dc_tuning_path() -> None:
    adapter = DeterministicDemoAdapter()
    transform = _source_degeneration_transform()
    adapter.create_schematic(transform)
    adapter.apply_parameters(_explicit_parameter_apply(), {})
    transform_plan = build_plan(transform)
    TaskExecutor(adapter).execute(
        transform, transform_plan, token=transform_plan.confirmation_token
    )
    tune = TaskSpec.model_validate(
        {
            "id": "tune-source-resistance",
            "operation": "design.tune",
            "circuit": "common_source",
            "target": {"library": "vda_test", "cell": "vda_cs"},
            "parameters": {"bias_v": 0.45, "vdd_v": 0.9},
            "parameter_space": {"source_resistance_ohm": [500.0, 1_000.0]},
            "constraints": [
                {
                    "metric": "saturation_margin_v",
                    "relation": ">=",
                    "value": 0.05,
                },
                {
                    "metric": "source_current_mismatch_percent",
                    "relation": "<=",
                    "value": 1.0,
                },
            ],
            "objective": {"metric": "drain_current_ua", "goal": "maximize"},
            "safety": {
                "allow_remote_compute": True,
                "allow_remote_write": True,
                "allowed_library": "vda_test",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )
    tune_plan = build_plan(tune)
    record = TaskExecutor(adapter).execute(
        tune, tune_plan, token=tune_plan.confirmation_token
    )
    readback = adapter.inspect_schematic(tune).data

    assert record.status is RunStatus.SUCCEEDED
    assert len(record.candidates) == 2
    assert record.selected_parameters is not None
    assert record.selected_parameters["source_resistance_ohm"] == pytest.approx(500.0)
    assert readback["semantic_parameters"]["source_resistance_ohm"] == pytest.approx(
        500.0
    )
    assert readback["instance_parameters"]["MN0"]["fingers"] == "2"
    assert all(
        "source_voltage_v" in candidate.metrics for candidate in record.candidates
    )
