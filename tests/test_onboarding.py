from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.binding_promotion import (
    OnboardingBindingPromotionCompilation,
    compile_onboarding_with_discovered_bindings,
)
from virtuoso_design_agent.binding_discovery_compiler import (
    ParameterBindingDiscoveryCompilation,
    ParameterBindingDiscoveryIntent,
    compile_parameter_binding_discovery_task,
)
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import (
    ActionRecord,
    EvidenceSource,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from virtuoso_design_agent.onboarding import build_onboarding_draft
from virtuoso_design_agent.onboarding_promotion import (
    compile_onboarding_post_refinement,
)
from virtuoso_design_agent.onboarding_resolution import resolve_onboarding_draft
from virtuoso_design_agent.parameter_binding import canonical_parameter_table_sha256
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.topology_delta import (
    apply_topology_delta_execution,
    compile_topology_delta,
    snapshot_from_inspection,
    topology_fingerprint,
)


def _flat_topology() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "library": "tsmcN28",
                "cell": "nch_lvt_mac",
                "view": "symbol",
                "terminals": {"D": "OUT", "G": "IN", "S": "VSS", "B": "VSS"},
            },
            {
                "name": "RD0",
                "library": "analogLib",
                "cell": "res",
                "view": "symbol",
                "terminals": {"PLUS": "VDD", "MINUS": "OUT"},
            },
        ],
        "nets": [
            {"name": "IN"},
            {"name": "OUT"},
            {"name": "VDD"},
            {"name": "VSS"},
        ],
        "pins": [
            {"name": "IN", "net": "IN", "direction": "input", "numBits": 1},
            {"name": "OUT", "net": "OUT", "direction": "output", "numBits": 1},
            {
                "name": "VDD",
                "net": "VDD",
                "direction": "inputOutput",
                "numBits": 1,
            },
            {
                "name": "VSS",
                "net": "VSS",
                "direction": "inputOutput",
                "numBits": 1,
            },
        ],
    }


def _hierarchical_topology(*, aliases: int = 1) -> dict:
    instances = []
    for index in range(aliases):
        instances.append(
            {
                "name": "XAMP" if index == 0 else f"XAMP{index}",
                "library": "vda_test",
                "cell": "vda_child",
                "view": "symbol",
                "terminals": {
                    "IN": "IN",
                    "OUT": "OUT",
                    "VDD": "VDD",
                    "VSS": "VSS",
                },
            }
        )
    return {
        "instances": instances,
        "nets": [{"name": name} for name in ("IN", "OUT", "VDD", "VSS")],
        "pins": [
            {"name": "IN", "net": "IN", "direction": "input"},
            {"name": "OUT", "net": "OUT", "direction": "output"},
            {"name": "VDD", "net": "VDD", "direction": "inputOutput"},
            {"name": "VSS", "net": "VSS", "direction": "inputOutput"},
        ],
    }


def _write_inspection(
    root: Path,
    *,
    stem: str,
    library: str,
    cell: str,
    topology: dict,
    instance_parameters: dict[str, dict[str, str]],
    action_source: EvidenceSource = EvidenceSource.BRIDGE_READBACK,
    adapter: str = "virtuoso-bridge-subprocess",
    allow_remote_compute: bool = False,
    allow_remote_write: bool = False,
) -> tuple[Path, Path]:
    task = TaskSpec.model_validate(
        {
            "schema_version": 1,
            "id": f"{stem}-inspect",
            "operation": "schematic.inspect",
            "circuit": "existing_schematic",
            "target": {"library": library, "cell": cell, "view": "schematic"},
            "pdk_profile": "nics4304_tsmc28",
            "safety": {
                "allow_remote_compute": allow_remote_compute,
                "allow_remote_write": allow_remote_write,
                "allowed_library": library,
                "required_cell_prefix": "vda_",
                "replace_existing": False,
            },
        }
    )
    task_path = root / f"{stem}-task.json"
    run_path = root / f"{stem}-run.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    now = datetime.now(UTC)
    record = RunRecord(
        task_id=task.id,
        plan_token=build_plan(task).confirmation_token,
        adapter=adapter,
        status=RunStatus.SUCCEEDED,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        actions=[
            ActionRecord(
                action="bridge.probe",
                status="succeeded",
                started_at=now,
                finished_at=now,
                evidence_source=EvidenceSource.BRIDGE_READBACK,
                details={"connected": True},
            ),
            ActionRecord(
                action="schematic.inspect",
                status="succeeded",
                started_at=now,
                finished_at=now + timedelta(seconds=1),
                evidence_source=action_source,
                details={
                    "topology": topology,
                    "instance_parameters": instance_parameters,
                    "placement": {"sha256": "a" * 64},
                },
            ),
        ],
    )
    run_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return task_path, run_path


def _inventory(draft, instance_path: str):
    return next(
        item for item in draft.parameter_inventory if item.instance_path == instance_path
    )


def _binding_resolution_payload(draft_path: Path) -> dict:
    return {
        "schema_version": 1,
        "draft_sha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "task_id": "binding-promoted-ac",
        "context_id": "binding-promoted-ac-context",
        "operation": "simulation.run",
        "roles": [
            {"role": "signal.input", "nets": ["IN"]},
            {"role": "signal.output", "nets": ["OUT"]},
            {"role": "supply.positive", "nets": ["VDD"]},
            {"role": "supply.return", "nets": ["VSS"]},
            {"role": "device.gain", "instances": ["MN0"]},
        ],
        "instance_parameter_permissions": [
            {
                "instance": "MN0",
                "parameters": ["Wfg"],
                "modes": ["fixed"],
            }
        ],
        "required_analyses": ["ac"],
        "metrics": [
            "low_frequency_gain_v_per_v",
            "bandwidth_3db_hz",
            "gain_bandwidth_product_hz",
        ],
        "generic_simulation": {
            "sources": [
                {
                    "name": "VDD_SRC",
                    "kind": "voltage",
                    "positive_node": "VDD",
                    "dc_value": 0.9,
                },
                {
                    "name": "VSS_SRC",
                    "kind": "voltage",
                    "positive_node": "VSS",
                    "dc_value": 0.0,
                },
                {
                    "name": "VIN_SRC",
                    "kind": "voltage",
                    "positive_node": "IN",
                    "dc_value": 0.35,
                    "ac_magnitude": 1.0,
                },
            ],
            "loads": [
                {
                    "name": "CL0",
                    "kind": "capacitor",
                    "positive_node": "OUT",
                    "value": 2e-15,
                }
            ],
            "transfer": {
                "input": {"positive_node": "IN"},
                "output": {"positive_node": "OUT"},
            },
            "netlist_parameter_bindings": [],
        },
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "points_per_decade": 20,
        },
        "limits": {"max_iterations": 1, "timeout_seconds": 600},
        "evidence_source": "user_input",
    }


def _write_promotable_binding_source(
    root: Path,
    inspect_task_path: Path,
    inspect_run_path: Path,
    *,
    mirrored_callbacks: bool = True,
) -> tuple[Path, Path]:
    safe_task, _compilation = compile_parameter_binding_discovery_task(
        inspect_task_path,
        inspect_run_path,
        ParameterBindingDiscoveryIntent(
            task_id="binding-promote-source",
            context_id="binding-promote-source-context",
            instance="MN0",
            oa_parameter="Wfg",
            probe_value="2u",
        ),
    )
    task_payload = safe_task.model_dump(mode="json", exclude_none=True)
    task_payload["safety"]["allow_remote_compute"] = True
    task_payload["safety"]["allow_remote_write"] = True
    task = TaskSpec.model_validate(task_payload)
    task_path = root / "binding-promote-source-task.json"
    task_path.write_text(
        task.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    assert task.parameter_binding_discovery is not None
    expected = dict(task.parameter_binding_discovery.expected_instance_parameters)
    topology_sha256 = task.design_context.expected_topology_sha256
    assert topology_sha256 is not None
    stage_payloads: dict[str, dict] = {}
    for stage_name, width in (
        ("baseline", "1u"),
        ("probe", "2u"),
        ("restored", "1u"),
    ):
        oa_parameters = dict(expected)
        oa_parameters["Wfg"] = width
        area = "5e-14"
        if stage_name == "probe" and mirrored_callbacks:
            area = "1e-13"
        oa_parameters["ad"] = area
        oa_parameters["as"] = area
        netlist_area = "1e-13" if stage_name == "probe" else "5e-14"
        inventory = {
            "MN0": {
                "model": "nch_lvt_mac",
                "nodes": ["OUT", "IN", "VSS", "VSS"],
                "parameters": {
                    "w": width,
                    "l": "30n",
                    "ad": netlist_area,
                    "as": netlist_area,
                },
            },
            "RD0": {
                "model": "resistor",
                "nodes": ["VDD", "OUT"],
                "parameters": {"r": "20K"},
            },
        }
        signature = hashlib.sha256(
            json.dumps(
                inventory,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        work_dir = root / f"{stage_name}-binding-work"
        work_dir.mkdir()
        (work_dir / "oa_netlist.scs").write_text(
            "MN0 (OUT IN VSS VSS) nch_lvt_mac "
            f"w={width} l=30n ad={netlist_area} as={netlist_area}\n"
            "RD0 (VDD OUT) resistor r=20K\n",
            encoding="utf-8",
        )
        (work_dir / "si_batch_stdout.log").write_text(
            "End netlisting\n",
            encoding="utf-8",
        )
        artifact_bundle = bridge_worker._persist_binding_netlist_artifacts(  # noqa: SLF001
            work_dir,
            root / "binding-evidence",
            stage_name,
        )
        netlist_sha256 = next(
            item["sha256"]
            for item in artifact_bundle["files"]
            if item["relative_path"] == f"{stage_name}/oa_netlist.scs"
        )
        remote_dir = (
            "/data/xum/virtuoso_bridge_smoke/"
            f"vda_binding_promotion_{stage_name}"
        )
        stage_payloads[stage_name] = {
            "oa_instance_parameters": oa_parameters,
            "oa_instance_parameters_sha256": canonical_parameter_table_sha256(
                oa_parameters
            ),
            "oa_topology_sha256": topology_sha256,
            "oa_source": "bridge_readback",
            "netlist_instance_parameters": inventory["MN0"]["parameters"],
            "instance": "MN0",
            "instance_model": "nch_lvt_mac",
            "instance_nodes": inventory["MN0"]["nodes"],
            "instance_parameters": inventory["MN0"]["parameters"],
            "parameter_inventory": inventory,
            "canonical_netlist_signature_sha256": signature,
            "raw_netlist": {
                "source": "eda_result",
                "remote_path": f"{remote_dir}/netlist",
                "remote_retained": False,
                "sha256": netlist_sha256,
            },
            "artifact_bundle": artifact_bundle,
            "remote_cleanup": {
                "source": "system_event",
                "remote_path": remote_dir,
                "removed": True,
            },
            "source": "eda_result",
        }

    now = datetime.now(UTC)
    record = RunRecord(
        task_id=task.id,
        plan_token=build_plan(task).confirmation_token,
        adapter="virtuoso-bridge-subprocess",
        status=RunStatus.PARTIAL,
        started_at=now,
        finished_at=now + timedelta(seconds=3),
        actions=[
            ActionRecord(
                action="parameters.binding.discover",
                status="succeeded",
                started_at=now,
                finished_at=now + timedelta(seconds=3),
                evidence_source=EvidenceSource.EDA_RESULT,
                details={
                    "contract": task.parameter_binding_discovery.model_dump(
                        mode="json"
                    ),
                    **stage_payloads,
                    "restoration": {
                        "oa_exact": True,
                        "canonical_netlist_signature_exact": True,
                        "verified": True,
                    },
                    "spectre_simulation_performed": False,
                    "classification": {
                        "status": "ambiguous_netlist_change",
                        "source": "software_inference",
                    },
                },
            )
        ],
    )
    run_path = root / "binding-promote-source-run.json"
    run_path.write_text(
        record.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    return task_path, run_path


def _write_binding_promotion_inputs(
    root: Path,
    *,
    mirrored_callbacks: bool = True,
) -> tuple[Path, Path, Path, Path]:
    inspect_task, inspect_run = _write_inspection(
        root,
        stem="binding-promote-inspect",
        library="vda_test",
        cell="vda_binding_promote",
        topology=_flat_topology(),
        instance_parameters={
            "MN0": {
                "Wfg": "1u",
                "l": "30n",
                "ad": "5e-14",
                "as": "5e-14",
            },
            "RD0": {"r": "20K"},
        },
    )
    draft = build_onboarding_draft(
        inspect_task,
        inspect_run,
        draft_id="binding-promote-draft",
    )
    draft_path = root / "binding-promote-draft.json"
    draft_path.write_text(
        draft.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    resolution_path = root / "binding-promote-resolution.json"
    resolution_path.write_text(
        json.dumps(
            _binding_resolution_payload(draft_path),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    discovery_task, discovery_run = _write_promotable_binding_source(
        root,
        inspect_task,
        inspect_run,
        mirrored_callbacks=mirrored_callbacks,
    )
    return draft_path, resolution_path, discovery_task, discovery_run


def test_binding_discovery_compiler_preserves_fresh_complete_cdf_and_is_safe(
    tmp_path: Path,
) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="binding-flat",
        library="vda_test",
        cell="vda_binding_flat",
        topology=_flat_topology(),
        instance_parameters={
            "MN0": {
                "Wfg": "1u",
                "l": "30n",
                "fingers": "1",
                "m": "1",
                "customFlag": "KEEP_ME",
            },
            "RD0": {"r": "20K"},
        },
    )
    intent = ParameterBindingDiscoveryIntent(
        task_id="binding-flat-discovery",
        context_id="binding-flat-context",
        instance="MN0",
        oa_parameter="Wfg",
        probe_value="1.25u",
    )

    task, compilation = compile_parameter_binding_discovery_task(
        task_path,
        run_path,
        intent,
    )
    repeated_task, repeated_compilation = compile_parameter_binding_discovery_task(
        task_path,
        run_path,
        intent,
    )

    assert task == repeated_task
    assert compilation == repeated_compilation
    assert task.operation.value == "parameters.binding.discover"
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert task.safety.replace_existing is False
    assert task.design_context is not None
    assert task.design_context.expected_topology_sha256 == topology_fingerprint(
        snapshot_from_inspection(_flat_topology())
    )
    assert task.design_context.frozen_instances == ["MN0", "RD0"]
    assert task.design_context.instance_parameter_permissions[0].model_dump() == {
        "instance": "MN0",
        "parameters": ["Wfg"],
        "modes": ["fixed"],
    }
    assert task.parameter_binding_discovery is not None
    assert task.parameter_binding_discovery.expected_instance_parameters == {
        "Wfg": "1u",
        "customFlag": "KEEP_ME",
        "fingers": "1",
        "l": "30n",
        "m": "1",
    }
    assert compilation.complete_cdf_parameter_count == 5
    assert compilation.original_value == "1u"
    assert compilation.execution_enabled is False
    assert compilation.source_run_sha256 != compilation.compiled_task_sha256
    assert build_plan(task).requires_remote_compute is True
    assert build_plan(task).requires_remote_write is True


def test_binding_discovery_compiler_rejects_a_field_absent_from_fresh_readback(
    tmp_path: Path,
) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="binding-missing",
        library="vda_test",
        cell="vda_binding_missing",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u"}, "RD0": {"r": "20K"}},
    )
    intent = ParameterBindingDiscoveryIntent(
        task_id="binding-missing-discovery",
        context_id="binding-missing-context",
        instance="MN0",
        oa_parameter="notInOa",
        probe_value="2u",
    )

    with pytest.raises(ValueError, match="absent from the complete fresh CDF"):
        compile_parameter_binding_discovery_task(task_path, run_path, intent)


def test_binding_discovery_compiler_supports_one_explicit_child_scope(
    tmp_path: Path,
) -> None:
    top_task, top_run = _write_inspection(
        tmp_path,
        stem="binding-top",
        library="vda_test",
        cell="vda_binding_top",
        topology=_hierarchical_topology(),
        instance_parameters={"XAMP": {}},
    )
    child_task, child_run = _write_inspection(
        tmp_path,
        stem="binding-child",
        library="vda_test",
        cell="vda_child",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u", "l": "30n"}, "RD0": {"r": "20K"}},
    )
    intent = ParameterBindingDiscoveryIntent.model_validate(
        {
            "task_id": "binding-child-discovery",
            "context_id": "binding-child-context",
            "instance": "XAMP/MN0",
            "oa_parameter": "Wfg",
            "probe_value": "1.25u",
            "hierarchy_bindings": [
                {
                    "instance": "XAMP",
                    "library": "vda_test",
                    "cell": "vda_child",
                    "subcircuit": "vda_child",
                    "terminal_order": ["IN", "OUT", "VDD", "VSS"],
                }
            ],
        }
    )

    task, compilation = compile_parameter_binding_discovery_task(
        top_task,
        top_run,
        intent,
        child_inspections=[("XAMP", child_task, child_run)],
    )

    assert task.parameter_binding_discovery is not None
    assert task.parameter_binding_discovery.instance == "XAMP/MN0"
    assert task.parameter_binding_discovery.expected_instance_parameters == {
        "Wfg": "1u",
        "l": "30n",
    }
    assert task.design_context is not None
    assert task.design_context.roles[0].instances == ["XAMP"]
    assert task.design_context.hierarchy_parameter_scopes[0].top_instance == "XAMP"
    assert compilation.parameter_target.cell == "vda_child"


def test_binding_discovery_task_cli_writes_task_and_hash_handoff(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="binding-cli",
        library="vda_test",
        cell="vda_binding_cli",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u", "l": "30n"}, "RD0": {"r": "20K"}},
    )
    intent_path = tmp_path / "binding-intent.json"
    intent_path.write_text(
        ParameterBindingDiscoveryIntent(
            task_id="binding-cli-discovery",
            context_id="binding-cli-context",
            instance="MN0",
            oa_parameter="Wfg",
            probe_value="1.25u",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    output = tmp_path / "binding-task.json"
    record_output = tmp_path / "binding-record.json"

    assert (
        main(
            [
                "binding-discovery-task",
                str(task_path),
                str(run_path),
                str(intent_path),
                "--output",
                str(output),
                "--record-output",
                str(record_output),
            ]
        )
        == 0
    )
    capsys.readouterr()

    compiled_task = TaskSpec.model_validate_json(output.read_text(encoding="utf-8"))
    record = ParameterBindingDiscoveryCompilation.model_validate_json(
        record_output.read_text(encoding="utf-8")
    )
    assert compiled_task.id == "binding-cli-discovery"
    assert compiled_task.safety.allow_remote_write is False
    assert record.instance == "MN0"
    assert record.evidence_sources["field_selection_and_probe"] == "user_input"


def test_onboarding_binding_promotion_compiles_and_rechecks_callbacks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    draft, resolution, discovery_task, discovery_run = (
        _write_binding_promotion_inputs(tmp_path)
    )

    task, compilation = compile_onboarding_with_discovered_bindings(
        draft,
        resolution,
        [(discovery_task, discovery_run)],
    )
    repeated_task, repeated_compilation = (
        compile_onboarding_with_discovered_bindings(
            draft,
            resolution,
            [(discovery_task, discovery_run)],
        )
    )

    assert task == repeated_task
    assert compilation == repeated_compilation
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert task.safety.replace_existing is False
    assert task.generic_simulation is not None
    assert len(task.generic_simulation.netlist_parameter_bindings) == 1
    binding = task.generic_simulation.netlist_parameter_bindings[0]
    assert binding.instance == "MN0"
    assert binding.oa_parameter == "Wfg"
    assert binding.netlist_parameter == "w"
    assert binding.derived_callbacks is not None
    assert [item.oa_parameter for item in binding.derived_callbacks] == [
        "ad",
        "as",
    ]
    assert binding.discovery_source is not None
    assert binding.discovery_source.classification == (
        "direct_literal_binding_with_derived_callbacks"
    )
    assert binding.discovery_source.discovery_task_sha256 == hashlib.sha256(
        discovery_task.read_bytes()
    ).hexdigest()
    assert binding.discovery_source.discovery_run_sha256 == hashlib.sha256(
        discovery_run.read_bytes()
    ).hexdigest()
    assert compilation.compiled_plan_token == build_plan(task).confirmation_token
    assert compilation.execution_enabled is False
    assert compilation.remote_execution_performed is False
    assert compilation.oa_write_performed is False
    assert compilation.promoted_bindings[0].discovery_run_status == "partial"
    assert compilation.compiled_task_sha256 == hashlib.sha256(
        (
            task.model_dump_json(indent=2, exclude_none=True) + "\n"
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="repeats an OA field"):
        compile_onboarding_with_discovered_bindings(
            draft,
            resolution,
            [
                (discovery_task, discovery_run),
                (discovery_task, discovery_run),
            ],
        )

    output = tmp_path / "binding-promoted-task.json"
    record_output = tmp_path / "binding-promotion-record.json"
    assert (
        main(
            [
                "onboarding-resolve-bindings",
                str(draft),
                str(resolution),
                "--binding-source",
                str(discovery_task),
                str(discovery_run),
                "--output",
                str(output),
                "--record-output",
                str(record_output),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert TaskSpec.model_validate_json(output.read_bytes()) == task
    assert hashlib.sha256(output.read_bytes()).hexdigest() == (
        compilation.compiled_task_sha256
    )
    assert OnboardingBindingPromotionCompilation.model_validate_json(
        record_output.read_bytes()
    ) == compilation


def test_onboarding_binding_promotion_rejects_unmirrored_callback_delta(
    tmp_path: Path,
) -> None:
    draft, resolution, discovery_task, discovery_run = (
        _write_binding_promotion_inputs(
            tmp_path,
            mirrored_callbacks=False,
        )
    )

    with pytest.raises(ValueError, match="did not prove a promotable"):
        compile_onboarding_with_discovered_bindings(
            draft,
            resolution,
            [(discovery_task, discovery_run)],
        )


def test_onboarding_binding_promotion_compiles_normal_tuning_without_manual_map(
    tmp_path: Path,
) -> None:
    draft, resolution, discovery_task, discovery_run = (
        _write_binding_promotion_inputs(tmp_path)
    )
    payload = json.loads(resolution.read_text(encoding="utf-8"))
    payload["task_id"] = "binding-promoted-tune"
    payload["operation"] = "design.tune"
    payload["instance_parameter_permissions"][0]["modes"] = ["search"]
    payload["instance_parameter_space"] = [
        {
            "instance": "MN0",
            "parameter": "Wfg",
            "values": ["1u", "1.5u"],
        }
    ]
    payload["objective"] = {
        "metric": "gain_bandwidth_product_hz",
        "goal": "maximize",
    }
    payload["constraints"] = [
        {
            "metric": "low_frequency_gain_v_per_v",
            "relation": ">=",
            "value": 1.0,
        }
    ]
    payload["limits"]["max_iterations"] = 2
    resolution.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    task, compilation = compile_onboarding_with_discovered_bindings(
        draft,
        resolution,
        [(discovery_task, discovery_run)],
    )

    assert task.operation.value == "design.tune"
    assert task.instance_parameter_space[0].parameter == "Wfg"
    assert task.generic_simulation is not None
    assert task.generic_simulation.netlist_parameter_bindings[0].oa_parameter == (
        "Wfg"
    )
    assert compilation.compiled_plan_token == build_plan(task).confirmation_token
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False


def test_onboarding_binding_promotion_rejects_manual_mapping_collision(
    tmp_path: Path,
) -> None:
    draft, resolution, discovery_task, discovery_run = (
        _write_binding_promotion_inputs(tmp_path)
    )
    payload = json.loads(resolution.read_text(encoding="utf-8"))
    payload["generic_simulation"]["netlist_parameter_bindings"] = [
        {
            "instance": "MN0",
            "oa_parameter": "Wfg",
            "netlist_parameter": "w",
        }
    ]
    resolution.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already mapped by onboarding intent"):
        compile_onboarding_with_discovered_bindings(
            draft,
            resolution,
            [(discovery_task, discovery_run)],
        )


def test_onboarding_draft_is_read_only_deterministic_and_preserves_all_cdf(
    tmp_path: Path,
) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="flat",
        library="vda_test",
        cell="vda_flat",
        topology=_flat_topology(),
        instance_parameters={
            "MN0": {"Wfg": "1u", "l": "30n", "customFlag": "KEEP_ME"},
            "RD0": {"r": "20K"},
        },
    )

    first = build_onboarding_draft(
        task_path,
        run_path,
        draft_id="flat-onboarding",
    )
    second = build_onboarding_draft(
        task_path,
        run_path,
        draft_id="flat-onboarding",
    )

    assert first == second
    assert first.status == "needs_user_intent"
    assert first.read_only is True
    assert first.source.evidence_source == "bridge_readback"
    assert first.source.target.cell == "vda_flat"
    assert first.source.task_sha256 != first.source.run_sha256
    assert first.topology_sha256 == topology_fingerprint(
        snapshot_from_inspection(_flat_topology())
    )
    assert first.design_context_draft.expected_topology_sha256 == (
        first.topology_sha256
    )
    assert first.design_context_draft.frozen_instances == ["MN0", "RD0"]
    assert first.design_context_draft.frozen_nets == ["IN", "OUT", "VDD", "VSS"]
    assert first.design_context_draft.instance_parameter_permissions == []
    assert first.generic_simulation_draft.executable is False
    assert first.generic_simulation_draft.contract_template["sources"] == []
    assert first.generic_simulation_draft.contract_template[
        "netlist_parameter_bindings"
    ] == []
    assert first.generic_simulation_draft.signal_candidates.model_dump() == {
        "input_nodes": ["IN"],
        "output_nodes": ["OUT"],
        "positive_supply_nodes": ["VDD"],
        "return_nodes": ["VSS"],
        "bidirectional_nodes": [],
    }
    mn0 = _inventory(first, "MN0")
    assert set(mn0.fields) == {"Wfg", "customFlag", "l"}
    assert mn0.fields["customFlag"].raw_value == "KEEP_ME"
    assert mn0.fields["customFlag"].permission_default == "not_authorized"
    assert mn0.fields["Wfg"].kind_hint == "geometry_width"
    assert all(item.requires_confirmation for item in first.role_candidates)
    assert any("parameter permissions" in item for item in first.unresolved_decisions)


def test_onboarding_draft_binds_explicit_child_inspection_without_guessing_si_order(
    tmp_path: Path,
) -> None:
    top_task, top_run = _write_inspection(
        tmp_path,
        stem="top",
        library="vda_test",
        cell="vda_top",
        topology=_hierarchical_topology(),
        instance_parameters={"XAMP": {}},
    )
    child_task, child_run = _write_inspection(
        tmp_path,
        stem="child",
        library="vda_test",
        cell="vda_child",
        topology=_flat_topology(),
        instance_parameters={
            "MN0": {"Wfg": "1u", "l": "30n"},
            "RD0": {"r": "20K"},
        },
    )

    draft = build_onboarding_draft(
        top_task,
        top_run,
        draft_id="hierarchy-onboarding",
        child_inspections=[("XAMP", child_task, child_run)],
    )

    assert [
        item.top_instance
        for item in draft.design_context_draft.hierarchy_parameter_scopes
    ] == ["XAMP"]
    scope = draft.design_context_draft.hierarchy_parameter_scopes[0]
    assert scope.library == "vda_test"
    assert scope.cell == "vda_child"
    assert scope.expected_child_topology_sha256 == topology_fingerprint(
        snapshot_from_inspection(_flat_topology())
    )
    assert scope.expected_child_placement_sha256 == "a" * 64
    assert _inventory(draft, "XAMP/MN0").fields["Wfg"].raw_value == "1u"
    candidate = next(
        item for item in draft.hierarchy_candidates if item.top_instance == "XAMP"
    )
    assert candidate.status == "child_inspection_bound"
    assert candidate.suggested_subcircuit == "vda_child"
    assert candidate.terminal_set == ["IN", "OUT", "VDD", "VSS"]
    assert candidate.requires_terminal_order_confirmation is True
    assert draft.generic_simulation_draft.contract_template[
        "hierarchy_bindings"
    ] == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda task, run: setattr(run, "plan_token", "wrong-token"),
            "does not match the inspection task plan",
        ),
        (
            lambda task, run: setattr(
                run.actions[1],
                "evidence_source",
                EvidenceSource.SOFTWARE_INFERENCE,
            ),
            "successful bridge_readback evidence",
        ),
        (
            lambda task, run: setattr(run, "adapter", "demo"),
            "real Bridge inspection",
        ),
    ],
)
def test_onboarding_rejects_unbound_or_non_bridge_inspection(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="invalid",
        library="vda_test",
        cell="vda_flat",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u"}, "RD0": {"r": "20K"}},
    )
    task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))
    run = RunRecord.model_validate_json(run_path.read_text(encoding="utf-8"))
    mutation(task, run)
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    run_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        build_onboarding_draft(task_path, run_path, draft_id="invalid-onboarding")


def test_onboarding_rejects_write_enabled_source_and_child_target_mismatch(
    tmp_path: Path,
) -> None:
    unsafe_task, unsafe_run = _write_inspection(
        tmp_path,
        stem="unsafe",
        library="vda_test",
        cell="vda_top",
        topology=_hierarchical_topology(),
        instance_parameters={"XAMP": {}},
        allow_remote_write=True,
    )
    with pytest.raises(ValueError, match="dedicated read-only inspection task"):
        build_onboarding_draft(
            unsafe_task,
            unsafe_run,
            draft_id="unsafe-onboarding",
        )

    top_task, top_run = _write_inspection(
        tmp_path,
        stem="safe-top",
        library="vda_test",
        cell="vda_top",
        topology=_hierarchical_topology(),
        instance_parameters={"XAMP": {}},
    )
    wrong_task, wrong_run = _write_inspection(
        tmp_path,
        stem="wrong-child",
        library="vda_test",
        cell="vda_other_child",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u"}, "RD0": {"r": "20K"}},
    )
    with pytest.raises(ValueError, match="does not match top instance master"):
        build_onboarding_draft(
            top_task,
            top_run,
            draft_id="wrong-child-onboarding",
            child_inspections=[("XAMP", wrong_task, wrong_run)],
        )


def test_onboarding_rejects_shared_child_scope_alias(
    tmp_path: Path,
) -> None:
    top_task, top_run = _write_inspection(
        tmp_path,
        stem="aliased-top",
        library="vda_test",
        cell="vda_top",
        topology=_hierarchical_topology(aliases=2),
        instance_parameters={"XAMP": {}, "XAMP1": {}},
    )
    child_task, child_run = _write_inspection(
        tmp_path,
        stem="aliased-child",
        library="vda_test",
        cell="vda_child",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u"}, "RD0": {"r": "20K"}},
    )

    with pytest.raises(ValueError, match="shared child"):
        build_onboarding_draft(
            top_task,
            top_run,
            draft_id="aliased-onboarding",
            child_inspections=[("XAMP", child_task, child_run)],
        )


def test_onboarding_cli_writes_reviewable_draft(tmp_path: Path, capsys) -> None:
    task_path, run_path = _write_inspection(
        tmp_path,
        stem="cli",
        library="vda_test",
        cell="vda_flat",
        topology=_flat_topology(),
        instance_parameters={"MN0": {"Wfg": "1u"}, "RD0": {"r": "20K"}},
    )
    output = tmp_path / "onboarding.json"

    assert (
        main(
            [
                "onboarding-draft",
                str(task_path),
                str(run_path),
                "--id",
                "cli-onboarding",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.exists()
    assert '"status": "needs_user_intent"' in output.read_text(encoding="utf-8")
    assert "cli-onboarding" in capsys.readouterr().out


def _write_onboarding_draft(
    root: Path,
    *,
    hierarchy: bool = False,
) -> Path:
    if hierarchy:
        top_task, top_run = _write_inspection(
            root,
            stem="resolution-top",
            library="vda_test",
            cell="vda_top",
            topology=_hierarchical_topology(),
            instance_parameters={"XAMP": {}},
        )
        child_task, child_run = _write_inspection(
            root,
            stem="resolution-child",
            library="vda_test",
            cell="vda_child",
            topology=_flat_topology(),
            instance_parameters={
                "MN0": {"Wfg": "1u", "l": "30n"},
                "RD0": {"r": "20K"},
            },
        )
        draft = build_onboarding_draft(
            top_task,
            top_run,
            draft_id="hierarchy-resolution-draft",
            child_inspections=[("XAMP", child_task, child_run)],
        )
    else:
        task_path, run_path = _write_inspection(
            root,
            stem="resolution-flat",
            library="vda_test",
            cell="vda_flat",
            topology=_flat_topology(),
            instance_parameters={
                "MN0": {"Wfg": "1u", "l": "30n"},
                "RD0": {"r": "20K"},
            },
        )
        draft = build_onboarding_draft(
            task_path,
            run_path,
            draft_id="flat-resolution-draft",
        )
    path = root / ("hierarchy-draft.json" if hierarchy else "flat-draft.json")
    path.write_text(
        draft.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    return path


def _draft_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flat_resolution(draft_path: Path) -> dict:
    return {
        "schema_version": 1,
        "draft_sha256": _draft_sha256(draft_path),
        "task_id": "resolved-flat-ac",
        "context_id": "resolved-flat-ac-context",
        "operation": "simulation.run",
        "roles": [
            {"role": "signal.input", "nets": ["IN"]},
            {"role": "signal.output", "nets": ["OUT"]},
            {"role": "supply.positive", "nets": ["VDD"]},
            {"role": "supply.return", "nets": ["VSS"]},
            {"role": "device.gain", "instances": ["MN0"]},
            {"role": "load.resistive", "instances": ["RD0"]},
            {
                "role": "device.input_gate",
                "terminals": [
                    {"instance": "MN0", "terminal": "G", "net": "IN"}
                ],
            },
        ],
        "instance_parameter_permissions": [
            {
                "instance": "MN0",
                "parameters": ["Wfg"],
                "modes": ["fixed"],
            }
        ],
        "required_analyses": ["ac"],
        "metrics": [
            "output_dc_v",
            "low_frequency_gain_v_per_v",
            "bandwidth_3db_hz",
        ],
        "generic_simulation": {
            "schema_version": 1,
            "sources": [
                {
                    "name": "VDD_SRC",
                    "kind": "voltage",
                    "positive_node": "VDD",
                    "negative_node": "0",
                    "dc_value": 0.9,
                },
                {
                    "name": "VSS_SRC",
                    "kind": "voltage",
                    "positive_node": "VSS",
                    "negative_node": "0",
                    "dc_value": 0.0,
                },
                {
                    "name": "VIN_SRC",
                    "kind": "voltage",
                    "positive_node": "IN",
                    "negative_node": "0",
                    "dc_value": 0.35,
                    "ac_magnitude": 1.0,
                },
            ],
            "loads": [
                {
                    "name": "CL0",
                    "kind": "capacitor",
                    "positive_node": "OUT",
                    "negative_node": "0",
                    "value": 2e-15,
                }
            ],
            "dc_voltage_metrics": [
                {
                    "metric": "output_dc_v",
                    "expression": {
                        "positive_node": "OUT",
                        "negative_node": "0",
                    },
                }
            ],
            "transfer": {
                "input": {"positive_node": "IN", "negative_node": "0"},
                "output": {"positive_node": "OUT", "negative_node": "0"},
            },
            "netlist_parameter_bindings": [
                {
                    "instance": "MN0",
                    "oa_parameter": "Wfg",
                    "netlist_parameter": "w",
                }
            ],
        },
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e12,
            "points_per_decade": 30,
        },
        "constraints": [
            {"metric": "output_dc_v", "relation": ">=", "value": 0.1},
            {
                "metric": "low_frequency_gain_v_per_v",
                "relation": ">=",
                "value": 1.0,
            },
            {
                "metric": "bandwidth_3db_hz",
                "relation": ">=",
                "value": 1e8,
            },
        ],
        "limits": {"max_iterations": 1, "timeout_seconds": 600},
        "evidence_source": "user_input",
    }


def _hierarchy_resolution(draft_path: Path) -> dict:
    resolution = _flat_resolution(draft_path)
    resolution.update(
        {
            "task_id": "resolved-hierarchy-tune",
            "context_id": "resolved-hierarchy-tune-context",
            "operation": "design.tune",
            "roles": [
                {"role": "signal.input", "nets": ["IN"]},
                {"role": "signal.output", "nets": ["OUT"]},
                {"role": "supply.positive", "nets": ["VDD"]},
                {"role": "supply.return", "nets": ["VSS"]},
                {"role": "block.amplifier", "instances": ["XAMP"]},
            ],
            "instance_parameter_permissions": [
                {
                    "instance": "XAMP/MN0",
                    "parameters": ["Wfg"],
                    "modes": ["search"],
                },
                {
                    "instance": "XAMP/RD0",
                    "parameters": ["r"],
                    "modes": ["search"],
                },
            ],
            "required_analyses": ["dc", "ac"],
            "metrics": [
                "output_dc_v",
                "low_frequency_gain_v_per_v",
                "bandwidth_3db_hz",
                "gain_bandwidth_product_hz",
            ],
            "analysis": None,
            "analysis_stage_execution": "shared_netlist",
            "analysis_stages": [
                {
                    "id": "bias",
                    "analysis": "dc",
                    "constraint_metrics": ["output_dc_v"],
                },
                {
                    "id": "gain-bandwidth",
                    "analysis": "ac",
                    "constraint_metrics": [
                        "low_frequency_gain_v_per_v",
                        "bandwidth_3db_hz",
                    ],
                },
            ],
            "candidate_set": {
                "source": {
                    "generator": "user_declared",
                    "id": "resolved-hierarchy-two-point",
                    "evidence_source": "user_input",
                },
                "candidates": [
                    {
                        "id": "compact",
                        "instance_parameter_updates": [
                            {
                                "instance": "XAMP/MN0",
                                "parameters": {"Wfg": "1u"},
                            },
                            {
                                "instance": "XAMP/RD0",
                                "parameters": {"r": "5K"},
                            },
                        ],
                    },
                    {
                        "id": "gain",
                        "instance_parameter_updates": [
                            {
                                "instance": "XAMP/MN0",
                                "parameters": {"Wfg": "1.1u"},
                            },
                            {
                                "instance": "XAMP/RD0",
                                "parameters": {"r": "18.5K"},
                            },
                        ],
                    },
                ],
            },
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "limits": {"max_iterations": 2, "timeout_seconds": 600},
        }
    )
    simulation = resolution["generic_simulation"]
    simulation["netlist_parameter_bindings"] = [
        {
            "instance": "XAMP/MN0",
            "oa_parameter": "Wfg",
            "netlist_parameter": "w",
        },
        {
            "instance": "XAMP/RD0",
            "oa_parameter": "r",
            "netlist_parameter": "r",
        },
    ]
    simulation["hierarchy_bindings"] = [
        {
            "instance": "XAMP",
            "library": "vda_test",
            "cell": "vda_child",
            "view": "schematic",
            "subcircuit": "vda_child",
            "terminal_order": ["IN", "OUT", "VDD", "VSS"],
        }
    ]
    return resolution


def _close_loop_resolution(draft_path: Path) -> dict:
    resolution = _flat_resolution(draft_path)
    contract = compile_topology_delta(
        "onboarding-source-degeneration",
        snapshot_from_inspection(_flat_topology()),
        [
            {"operation": "add_net", "net": {"name": "NSRC"}},
            {
                "operation": "reconnect_terminal",
                "instance": "MN0",
                "terminal": "S",
                "expected_net": "VSS",
                "net": "NSRC",
            },
            {
                "operation": "add_instance",
                "instance": {
                    "name": "RS0",
                    "master": {
                        "library": "analogLib",
                        "cell": "res",
                        "view": "symbol",
                    },
                    "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
                },
            },
        ],
    )
    baseline_simulation = resolution["generic_simulation"]
    baseline_simulation["dynamic_analysis"] = {
        "stimulus_source": "VIN_SRC",
        "power_source": "VDD_SRC",
    }
    resolution.update(
        {
            "task_id": "resolved-flat-refinement",
            "context_id": "resolved-flat-refinement-baseline",
            "operation": "design.close_loop",
            "instance_parameter_permissions": [
                {
                    "instance": "MN0",
                    "parameters": ["Wfg"],
                    "modes": ["search"],
                }
            ],
            "required_analyses": ["dc", "ac"],
            "optional_analyses": ["transient", "noise"],
            "metrics": [
                "output_dc_v",
                "low_frequency_gain_v_per_v",
                "bandwidth_3db_hz",
                "gain_bandwidth_product_hz",
                "max_thd_percent",
                "integrated_input_referred_noise_uv_rms",
            ],
            "analysis": None,
            "analysis_stage_execution": "shared_netlist",
            "analysis_stages": [
                {
                    "id": "bias",
                    "analysis": "dc",
                    "constraint_metrics": ["output_dc_v"],
                },
                {
                    "id": "gain-bandwidth",
                    "analysis": "ac",
                    "constraint_metrics": [
                        "low_frequency_gain_v_per_v",
                        "bandwidth_3db_hz",
                    ],
                },
            ],
            "candidate_set": {
                "source": {
                    "generator": "user_declared",
                    "id": "onboarding-two-point",
                    "evidence_source": "user_input",
                },
                "candidates": [
                    {
                        "id": "nominal",
                        "instance_parameter_updates": [
                            {"instance": "MN0", "parameters": {"Wfg": "1u"}}
                        ],
                        "testbench_overrides": {
                            "sources": {"VIN_SRC": {"dc_value": 0.35}},
                            "loads": {"CL0": 2e-15},
                        },
                    },
                    {
                        "id": "wider",
                        "instance_parameter_updates": [
                            {"instance": "MN0", "parameters": {"Wfg": "1.1u"}}
                        ],
                        "testbench_overrides": {
                            "sources": {"VIN_SRC": {"dc_value": 0.37}},
                            "loads": {"CL0": 2e-15},
                        },
                    },
                ],
            },
            "objective": {
                "metric": "gain_bandwidth_product_hz",
                "goal": "maximize",
            },
            "topology_edits": {
                "allowed_operations": [
                    "add_instance",
                    "remove_instance",
                    "add_net",
                    "remove_net",
                    "reconnect_terminal",
                ],
                "mutable_instances": ["MN0", "RS0"],
                "mutable_nets": ["NSRC"],
                "mutable_pins": [],
                "max_operations_per_delta": 3,
            },
            "topology_alternatives": [
                {
                    "id": "source-degenerated",
                    "context_id": "resolved-flat-refinement-degenerated",
                    "topology_delta": {
                        "direction": "forward",
                        "contract": contract.model_dump(mode="json"),
                    },
                    "roles": [
                        *resolution["roles"],
                        {"role": "device.degeneration", "instances": ["RS0"]},
                    ],
                    "added_instance_parameter_permissions": [
                        {
                            "instance": "RS0",
                            "parameters": ["r"],
                            "modes": ["fixed"],
                        }
                    ],
                    "added_netlist_parameter_bindings": [
                        {
                            "instance": "RS0",
                            "oa_parameter": "r",
                            "netlist_parameter": "r",
                        }
                    ],
                    "instance_parameter_updates": [
                        {"instance": "RS0", "parameters": {"r": "1K"}}
                    ],
                    "evidence_source": "user_input",
                }
            ],
            "winner_verification": {
                "analysis_stages": [
                    {
                        "id": "winner-linearity",
                        "analysis": "transient",
                        "constraint_metrics": ["max_thd_percent"],
                    },
                    {
                        "id": "winner-noise",
                        "analysis": "noise",
                        "constraint_metrics": [
                            "integrated_input_referred_noise_uv_rms"
                        ],
                    },
                ],
                "constraints": [
                    {
                        "metric": "max_thd_percent",
                        "relation": "<=",
                        "value": 5.0,
                    },
                    {
                        "metric": "integrated_input_referred_noise_uv_rms",
                        "relation": "<=",
                        "value": 2000.0,
                    },
                ],
                "linearity_sweep": {
                    "frequency_hz": 1e6,
                    "amplitudes_v": [0.005, 0.02],
                    "points_per_cycle": 64,
                },
                "noise_sweep": {
                    "start_hz": 1e3,
                    "stop_hz": 1e9,
                    "points_per_decade": 20,
                },
            },
            "limits": {"max_iterations": 4, "timeout_seconds": 600},
        }
    )
    return resolution


def _replace_alternative_with_loadless_simulation(resolution: dict) -> None:
    alternative = resolution["topology_alternatives"][0]
    simulation = deepcopy(resolution["generic_simulation"])
    simulation["loads"] = []
    simulation["netlist_parameter_bindings"].append(
        {
            "instance": "RS0",
            "oa_parameter": "r",
            "netlist_parameter": "r",
        }
    )
    alternative["generic_simulation"] = simulation
    alternative["added_netlist_parameter_bindings"] = []


def test_onboarding_resolution_compiles_flat_safe_taskspec(tmp_path: Path) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _flat_resolution(draft_path)

    task = resolve_onboarding_draft(draft_path, resolution)

    assert task.operation.value == "simulation.run"
    assert task.circuit.value == "existing_schematic"
    assert task.target is not None
    assert task.target.model_dump() == {
        "library": "vda_test",
        "cell": "vda_flat",
        "view": "schematic",
    }
    assert task.design_context is not None
    assert task.design_context.expected_topology_sha256 is not None
    assert task.design_context.frozen_instances == ["MN0", "RD0"]
    assert task.design_context.topology_edits.allowed_operations == []
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert task.safety.allowed_library == "vda_test"
    assert task.safety.replace_existing is False
    assert build_plan(task).confirmation_token


def test_onboarding_resolution_compiles_scoped_hierarchy_tuning(
    tmp_path: Path,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path, hierarchy=True)
    resolution = _hierarchy_resolution(draft_path)

    task = resolve_onboarding_draft(draft_path, resolution)

    assert task.operation.value == "design.tune"
    assert task.design_context is not None
    assert [
        scope.top_instance for scope in task.design_context.hierarchy_parameter_scopes
    ] == ["XAMP"]
    assert task.generic_simulation is not None
    assert task.generic_simulation.hierarchy_bindings[0].terminal_order == [
        "IN",
        "OUT",
        "VDD",
        "VSS",
    ]
    assert task.candidate_set is not None
    assert len(task.candidate_set.candidates) == 2
    assert task.limits.max_iterations == 2
    assert task.safety.allow_remote_write is False
    assert build_plan(task).confirmation_token


def test_onboarding_resolution_compiles_topology_and_winner_only_quality(
    tmp_path: Path,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _close_loop_resolution(draft_path)

    task = resolve_onboarding_draft(draft_path, resolution)

    assert task.operation.value == "design.close_loop"
    assert task.topology_refinement is not None
    alternatives = task.topology_refinement.resolved_alternatives()
    assert [item.id for item in alternatives] == ["source-degenerated"]
    alternative = alternatives[0]
    assert alternative.design_context.expected_topology_sha256 == (
        alternative.topology_delta.contract.expected_after_sha256
    )
    assert task.design_context is not None
    assert task.design_context.frozen_instances == ["RD0"]
    assert alternative.design_context.frozen_instances == ["RD0"]
    assert alternative.instance_parameter_updates[0].parameters == {"r": "1K"}
    assert task.winner_verification is not None
    assert [
        stage.analysis.value for stage in task.winner_verification.analysis_stages
    ] == ["transient", "noise"]
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert task.limits.max_iterations == 4
    assert build_plan(task).confirmation_token


def _write_post_refinement_fixture(
    root: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    draft_path = _write_onboarding_draft(root)
    resolution = _close_loop_resolution(draft_path)
    winner_verification = resolution["winner_verification"]
    resolution_without_winner = deepcopy(resolution)
    resolution_without_winner.pop("winner_verification")
    source_task = resolve_onboarding_draft(draft_path, resolution_without_winner)
    source_payload = source_task.model_dump(mode="json", exclude_none=True)
    source_payload["safety"].update(
        {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
            "required_cell_prefix": "vda_",
            "replace_existing": False,
        }
    )
    source_task = TaskSpec.model_validate(source_payload)
    source_task_path = root / "refinement-task.json"
    source_task_path.write_text(
        source_task.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    assert source_task.topology_refinement is not None
    alternative = source_task.topology_refinement.resolved_alternatives()[0]
    after = apply_topology_delta_execution(
        snapshot_from_inspection(_flat_topology()),
        alternative.topology_delta,
    )
    baseline_hash = source_task.design_context.expected_topology_sha256
    after_hash = topology_fingerprint(after)
    assert baseline_hash is not None
    candidate_inputs = source_task.candidate_set.candidates
    candidates = []
    for index in range(1, 5):
        topology_variant_id = "baseline" if index <= 2 else alternative.id
        topology_sha256 = baseline_hash if index <= 2 else after_hash
        source_candidate = candidate_inputs[(index - 1) % 2]
        instance_parameters = source_candidate.instance_parameters()
        if index > 2:
            instance_parameters["RS0"] = {"r": "1K"}
        metrics = {
            "output_dc_v": 0.45 + index * 0.01,
            "low_frequency_gain_v_per_v": 2.0 + index,
            "bandwidth_3db_hz": 1e9 + index * 1e8,
            "gain_bandwidth_product_hz": 3e9 + index * 1e9,
        }
        candidates.append(
            {
                "index": index,
                "topology_variant_id": topology_variant_id,
                "topology_sha256": topology_sha256,
                "parameters": {},
                "instance_parameters": instance_parameters,
                "testbench_overrides": (
                    source_candidate.testbench_overrides.model_dump(mode="json")
                    if source_candidate.testbench_overrides is not None
                    else None
                ),
                "testbench_override_evidence_source": (
                    "user_input"
                    if source_candidate.testbench_overrides is not None
                    else None
                ),
                "metrics": metrics,
                "constraints": [],
                "feasible": True,
                "total_violation": 0.0,
                "objective_value": metrics["gain_bandwidth_product_hz"],
                "evidence_source": "eda_result",
                "metric_sources": {
                    name: "eda_result" for name in metrics
                },
            }
        )
    selected = candidates[-1]
    now = datetime.now(UTC) - timedelta(minutes=1)
    source_run = RunRecord.model_validate(
        {
            "task_id": source_task.id,
            "plan_token": build_plan(source_task).confirmation_token,
            "adapter": "virtuoso-bridge-subprocess",
            "status": "succeeded",
            "started_at": now,
            "finished_at": now + timedelta(seconds=10),
            "actions": [
                {
                    "action": "schematic.inspect.final",
                    "status": "succeeded",
                    "started_at": now + timedelta(seconds=9),
                    "finished_at": now + timedelta(seconds=10),
                    "evidence_source": "bridge_readback",
                    "details": {"topology": after.model_dump(mode="json")},
                }
            ],
            "candidates": candidates,
            "selected_parameters": {},
            "selected_instance_parameters": selected["instance_parameters"],
            "selected_testbench_overrides": selected["testbench_overrides"],
            "selected_testbench_override_evidence_source": "user_input",
            "selected_metrics": selected["metrics"],
            "selected_topology_variant_id": alternative.id,
            "selected_topology_sha256": after_hash,
            "search_audit": {
                "declared_candidate_count": 4,
                "attempted_candidate_count": 4,
                "completed_candidate_count": 4,
                "topology_variant_count": 2,
                "domain_exhausted": True,
                "selection_scope": "best_in_declared_discrete_domain",
                "statement": "best feasible point in the declared discrete domain",
            },
        }
    )
    source_run_path = root / "refinement-run.json"
    source_run_path.write_text(
        source_run.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    inspect_task_path, inspect_run_path = _write_inspection(
        root,
        stem="winner",
        library="vda_test",
        cell="vda_flat",
        topology=after.model_dump(mode="json"),
        instance_parameters={
            "MN0": {"Wfg": "1.1u", "l": "30n"},
            "RD0": {"r": "20K"},
            "RS0": {"r": "1K", "isnoisy": "yes"},
        },
    )
    intent = {
        "schema_version": 1,
        "source_task_sha256": hashlib.sha256(
            source_task_path.read_bytes()
        ).hexdigest(),
        "variants": [
            {
                "topology_variant_id": "baseline",
                "draft_id": "post-refinement-baseline",
                "task_id": "post-refinement-baseline-tune",
                "context_id": "post-refinement-baseline-tune-context",
                "instance_parameter_permissions": [
                    {
                        "instance": "MN0",
                        "parameters": ["Wfg"],
                        "modes": ["search"],
                    }
                ],
                "netlist_parameter_bindings": [
                    {
                        "instance": "MN0",
                        "oa_parameter": "Wfg",
                        "netlist_parameter": "w",
                    }
                ],
                "candidate_set": {
                    "source": {
                        "generator": "user_declared",
                        "id": "post-readback-baseline-point",
                        "evidence_source": "user_input",
                    },
                    "candidates": [
                        {
                            "id": "baseline-width",
                            "instance_parameter_updates": [
                                {
                                    "instance": "MN0",
                                    "parameters": {"Wfg": "1.05u"},
                                }
                            ],
                        }
                    ],
                },
                "limits": {"max_iterations": 1, "timeout_seconds": 600},
                "evidence_source": "user_input",
            },
            {
                "topology_variant_id": alternative.id,
                "draft_id": "post-refinement-winner",
                "task_id": "post-refinement-fine-tune",
                "context_id": "post-refinement-fine-tune-context",
                "instance_parameter_permissions": [
                    {
                        "instance": "MN0",
                        "parameters": ["Wfg"],
                        "modes": ["search"],
                    },
                    {
                        "instance": "RS0",
                        "parameters": ["r"],
                        "modes": ["search"],
                    },
                ],
                "netlist_parameter_bindings": [
                    {
                        "instance": "MN0",
                        "oa_parameter": "Wfg",
                        "netlist_parameter": "w",
                    },
                    {
                        "instance": "RS0",
                        "oa_parameter": "r",
                        "netlist_parameter": "r",
                    },
                ],
                "candidate_set": {
                    "source": {
                        "generator": "user_declared",
                        "id": "post-readback-two-point",
                        "evidence_source": "user_input",
                    },
                    "candidates": [
                        {
                            "id": "rs-750",
                            "instance_parameter_updates": [
                                {
                                    "instance": "MN0",
                                    "parameters": {"Wfg": "1.05u"},
                                },
                                {
                                    "instance": "RS0",
                                    "parameters": {"r": "750"},
                                },
                            ],
                        },
                        {
                            "id": "rs-1k",
                            "instance_parameter_updates": [
                                {
                                    "instance": "MN0",
                                    "parameters": {"Wfg": "1.1u"},
                                },
                                {
                                    "instance": "RS0",
                                    "parameters": {"r": "1K"},
                                },
                            ],
                        },
                    ],
                },
                "winner_verification": winner_verification,
                "limits": {"max_iterations": 2, "timeout_seconds": 600},
                "evidence_source": "user_input",
            }
        ],
        "evidence_source": "user_input",
    }
    intent_path = root / "promotion-intent.json"
    intent_path.write_text(
        json.dumps(intent, indent=2) + "\n",
        encoding="utf-8",
    )
    return (
        source_task_path,
        source_run_path,
        inspect_task_path,
        inspect_run_path,
        intent_path,
    )


def test_onboarding_promotion_compiles_readback_bound_fine_tuning(
    tmp_path: Path,
) -> None:
    paths = _write_post_refinement_fixture(tmp_path)

    first = compile_onboarding_post_refinement(*paths)
    second = compile_onboarding_post_refinement(*paths)

    assert first == second
    draft, task, compilation = first
    assert _inventory(draft, "RS0").fields["r"].raw_value == "1K"
    assert task.operation.value == "design.tune"
    assert task.topology_refinement is None
    assert task.design_context is not None
    assert task.design_context.expected_topology_sha256 == (
        compilation.selected_topology_sha256
    )
    assert {
        (permission.instance, parameter, tuple(permission.modes))
        for permission in task.design_context.instance_parameter_permissions
        for parameter in permission.parameters
    } == {
        ("MN0", "Wfg", ("search",)),
        ("RS0", "r", ("search",)),
    }
    assert task.candidate_set is not None
    assert [item.id for item in task.candidate_set.candidates] == [
        "rs-750",
        "rs-1k",
    ]
    assert len(task.candidate_set.source.bindings) == 6
    assert task.winner_verification is not None
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert task.safety.replace_existing is False
    assert compilation.compiled_plan_token == build_plan(task).confirmation_token


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda paths: _mutate_json(
                paths[1],
                lambda value: value["search_audit"].__setitem__(
                    "completed_candidate_count", 3
                ),
            ),
            "exhausted topology-parameter audit",
        ),
        (
            lambda paths: _mutate_json(
                paths[3],
                lambda value: value["actions"][1]["details"][
                    "instance_parameters"
                ]["RS0"].pop("r"),
            ),
            "lacks selected parameter RS0.r",
        ),
        (
            lambda paths: _drift_winner_parameter(paths),
            "winner inspection selected parameter drifted: RS0.r",
        ),
        (
            lambda paths: _disable_source_authority(paths),
            "explicit compute/write authority",
        ),
        (
            lambda paths: _drift_winner_topology(paths),
            "winner inspection topology differs from selected topology",
        ),
        (
            lambda paths: _mutate_json(
                paths[4],
                lambda value: value.__setitem__(
                    "variants", [value["variants"][0]]
                ),
            ),
            "does not cover the selected topology winner",
        ),
    ],
)
def test_onboarding_promotion_rejects_untrusted_stage_transition(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    paths = _write_post_refinement_fixture(tmp_path)
    mutation(paths)

    with pytest.raises(ValueError, match=message):
        compile_onboarding_post_refinement(*paths)


def _mutate_json(path: Path, mutation) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _disable_source_authority(paths: tuple[Path, ...]) -> None:
    source_task_path, _source_run, _inspect_task, _inspect_run, intent_path = paths
    _mutate_json(
        source_task_path,
        lambda value: value["safety"].__setitem__("allow_remote_write", False),
    )
    _mutate_json(
        intent_path,
        lambda value: value.__setitem__(
            "source_task_sha256",
            hashlib.sha256(source_task_path.read_bytes()).hexdigest(),
        ),
    )


def _drift_winner_topology(paths: tuple[Path, ...]) -> None:
    _source_task, _source_run, _inspect_task, inspect_run_path, _intent = paths

    def mutate(payload: dict) -> None:
        topology = payload["actions"][1]["details"]["topology"]
        rs0 = next(
            item for item in topology["instances"] if item["name"] == "RS0"
        )
        rs0["master"]["cell"] = "res_drifted"

    _mutate_json(inspect_run_path, mutate)


def _drift_winner_parameter(paths: tuple[Path, ...]) -> None:
    _source_task, _source_run, _inspect_task, inspect_run_path, _intent = paths
    _mutate_json(
        inspect_run_path,
        lambda value: value["actions"][1]["details"][
            "instance_parameters"
        ]["RS0"].__setitem__("r", "2K"),
    )


def test_onboarding_promotion_cli_writes_reusable_artifacts(
    tmp_path: Path,
    capsys,
) -> None:
    paths = _write_post_refinement_fixture(tmp_path)
    draft_output = tmp_path / "post-draft.json"
    task_output = tmp_path / "post-task.json"
    record_output = tmp_path / "post-record.json"

    assert (
        main(
            [
                "onboarding-promote",
                *(str(path) for path in paths),
                "--draft-output",
                str(draft_output),
                "--task-output",
                str(task_output),
                "--record-output",
                str(record_output),
            ]
        )
        == 0
    )
    task = TaskSpec.model_validate_json(task_output.read_text(encoding="utf-8"))
    record = json.loads(record_output.read_text(encoding="utf-8"))
    assert task.operation.value == "design.tune"
    assert record["compiled_task_sha256"] == hashlib.sha256(
        task_output.read_bytes()
    ).hexdigest()
    assert record["post_readback_draft_sha256"] == hashlib.sha256(
        draft_output.read_bytes()
    ).hexdigest()
    first_outputs = (
        draft_output.read_bytes(),
        task_output.read_bytes(),
        record_output.read_bytes(),
    )
    assert (
        main(
            [
                "onboarding-promote",
                *(str(path) for path in paths),
                "--draft-output",
                str(draft_output),
                "--task-output",
                str(task_output),
                "--record-output",
                str(record_output),
            ]
        )
        == 0
    )
    assert first_outputs == (
        draft_output.read_bytes(),
        task_output.read_bytes(),
        record_output.read_bytes(),
    )
    assert "ready_for_plan" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["topology_alternatives"][0]["topology_delta"][
                "contract"
            ].__setitem__("expected_after_sha256", "0" * 64),
            "exact draft-bound round trip",
        ),
        (
            lambda value: value["topology_edits"].__setitem__(
                "mutable_instances", ["RS0"]
            ),
            "edit scope",
        ),
        (
            lambda value: value["topology_alternatives"][0]["roles"][-1].__setitem__(
                "instances", ["MISSING"]
            ),
            "outside the onboarding topology",
        ),
        (
            lambda value: value["topology_alternatives"][0][
                "instance_parameter_updates"
            ][0]["parameters"].__setitem__("r", "2K")
            or value["topology_alternatives"][0][
                "instance_parameter_updates"
            ][0]["parameters"].__setitem__("extra", "1"),
            "permission/update surface differs",
        ),
        (
            _replace_alternative_with_loadless_simulation,
            "unknown loads",
        ),
    ],
)
def test_onboarding_resolution_rejects_unsafe_topology_or_quality_intent(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _close_loop_resolution(draft_path)
    mutation(resolution)

    with pytest.raises(ValueError, match=message):
        resolve_onboarding_draft(draft_path, resolution)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.__setitem__("draft_sha256", "0" * 64),
            "draft SHA-256",
        ),
        (
            lambda value: value["generic_simulation"]["sources"][0].__setitem__(
                "positive_node", "MISSING"
            ),
            "unknown top-level nodes",
        ),
        (
            lambda value: (
                value["instance_parameter_permissions"][0].__setitem__(
                    "parameters", ["not_a_cdf_field"]
                ),
                value["generic_simulation"]["netlist_parameter_bindings"][0].__setitem__(
                    "oa_parameter", "not_a_cdf_field"
                ),
            ),
            "outside the onboarding parameter inventory",
        ),
        (
            lambda value: value["roles"][0].__setitem__(
                "evidence_source", "software_inference"
            ),
            "final role bindings require user_input",
        ),
        (
            lambda value: value["roles"][0].__setitem__("nets", ["MISSING"]),
            "outside the onboarding topology",
        ),
        (
            lambda value: value["roles"][-1]["terminals"][0].__setitem__(
                "net", "OUT"
            ),
            "terminal binding does not match",
        ),
        (
            lambda value: value.__setitem__("operation", "design.close_loop"),
            "requires a topology alternative",
        ),
        (
            lambda value: value.__setitem__(
                "safety", {"allow_remote_compute": True}
            ),
            "safety",
        ),
        (
            lambda value: value.__setitem__(
                "instance_parameter_updates",
                [{"instance": "MN0", "parameters": {"Wfg": "1.1u"}}],
            ),
            "cannot request parameter writes",
        ),
    ],
)
def test_onboarding_resolution_rejects_unbound_intent(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _flat_resolution(draft_path)
    mutation(resolution)

    with pytest.raises(ValueError, match=message):
        resolve_onboarding_draft(draft_path, resolution)


@pytest.mark.parametrize("mutation", ["terminal_order", "child_target"])
def test_onboarding_resolution_rejects_inconsistent_hierarchy_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path, hierarchy=True)
    resolution = _hierarchy_resolution(draft_path)
    binding = resolution["generic_simulation"]["hierarchy_bindings"][0]
    if mutation == "terminal_order":
        binding["terminal_order"] = ["IN", "OUT", "VDD", "BIAS"]
        message = "terminal order does not match"
    else:
        binding["cell"] = "vda_other_child"
        message = "does not match onboarding child"

    with pytest.raises(ValueError, match=message):
        resolve_onboarding_draft(draft_path, resolution)


def test_onboarding_resolution_requires_exact_permission_binding_surface(
    tmp_path: Path,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _flat_resolution(draft_path)
    resolution["generic_simulation"]["netlist_parameter_bindings"] = []

    with pytest.raises(ValueError, match="permission/binding surface differs"):
        resolve_onboarding_draft(draft_path, resolution)


def test_onboarding_resolve_cli_writes_normal_taskspec(
    tmp_path: Path,
    capsys,
) -> None:
    draft_path = _write_onboarding_draft(tmp_path)
    resolution = _flat_resolution(draft_path)
    resolution_path = tmp_path / "resolution.json"
    resolution_path.write_text(
        json.dumps(resolution, indent=2) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "task.json"

    assert (
        main(
            [
                "onboarding-resolve",
                str(draft_path),
                str(resolution_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    task = TaskSpec.model_validate_json(output.read_text(encoding="utf-8"))
    assert task.id == "resolved-flat-ac"
    assert task.safety.allow_remote_compute is False
    assert task.safety.allow_remote_write is False
    assert "resolved-flat-ac" in capsys.readouterr().out
