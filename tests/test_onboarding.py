from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import (
    ActionRecord,
    EvidenceSource,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from virtuoso_design_agent.onboarding import build_onboarding_draft
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.topology_delta import (
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
