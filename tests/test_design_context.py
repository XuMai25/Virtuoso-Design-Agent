from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.design_context import (
    DesignContext,
    DesignContextError,
    audit_design_context,
    validate_topology_delta_scope,
)
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.topology_delta import (
    TopologyDeltaExecutionSpec,
    derive_topology_delta,
)


def _single_ended_readback() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "library": "tsmcN28",
                "cell": "nch_mac",
                "view": "symbol",
                "terminals": {
                    "D": "OUT",
                    "G": "IN",
                    "S": "VSS",
                    "B": "VSS",
                },
            },
            {
                "name": "RD0",
                "library": "analogLib",
                "cell": "res",
                "view": "symbol",
                "terminals": {"PLUS": "VDD", "MINUS": "OUT"},
            },
        ],
        "nets": ["IN", "OUT", "VDD", "VSS"],
        "pins": ["IN", "OUT", "VDD", "VSS"],
        "instance_parameters": {
            "MN0": {"Wfg": "1u", "l": "30n", "fingers": "1"},
            "RD0": {"r": "20k"},
        },
    }


def _single_ended_context(**updates) -> DesignContext:
    payload = {
        "id": "user-common-source",
        "roles": [
            {"role": "signal.input", "nets": ["IN"]},
            {"role": "signal.output", "nets": ["OUT"]},
            {"role": "supply.positive", "pins": ["VDD"]},
            {
                "role": "device.input",
                "instances": ["MN0"],
                "evidence_source": "software_inference",
            },
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
                "parameters": ["Wfg", "l"],
                "modes": ["fixed", "search"],
            }
        ],
        "semantic_parameter_permissions": [
            {"parameter": "bias_v", "modes": ["fixed"]},
            {"parameter": "device_width_um", "modes": ["search"]},
        ],
        "frozen_instances": ["RD0"],
        "frozen_nets": ["VDD"],
        "frozen_pins": ["VDD"],
        "topology_edits": {
            "allowed_operations": [
                "add_instance",
                "add_net",
                "reconnect_terminal",
            ],
            "mutable_instances": ["MN0", "RS0"],
            "mutable_nets": ["NSRC"],
        },
        "required_analyses": ["dc", "ac"],
        "optional_analyses": ["noise"],
        "metrics": ["dc_supply_power_uw", "gain_db", "bandwidth_hz"],
    }
    payload.update(updates)
    return DesignContext.model_validate(payload)


def _differential_readback() -> dict:
    return {
        "instances": [
            {
                "name": "MINP",
                "library": "tsmcN28",
                "cell": "nch_mac",
                "terminals": {
                    "D": "OUTP",
                    "G": "INP",
                    "S": "TAIL",
                    "B": "VSS",
                },
            },
            {
                "name": "MINN",
                "library": "tsmcN28",
                "cell": "nch_mac",
                "terminals": {
                    "D": "OUTN",
                    "G": "INN",
                    "S": "TAIL",
                    "B": "VSS",
                },
            },
        ],
        "nets": ["INP", "INN", "OUTP", "OUTN", "TAIL", "VSS"],
        "pins": ["INP", "INN", "OUTP", "OUTN", "VSS"],
        "instance_parameters": {
            "MINP": {"Wfg": "2u", "l": "30n"},
            "MINN": {"Wfg": "2u", "l": "30n"},
        },
    }


def test_context_audit_binds_two_different_user_topologies() -> None:
    single = audit_design_context(_single_ended_readback(), _single_ended_context())
    differential_context = DesignContext.model_validate(
        {
            "id": "user-differential-stage",
            "roles": [
                {"role": "signal.inputs", "nets": ["INP", "INN"]},
                {"role": "signal.outputs", "nets": ["OUTP", "OUTN"]},
                {"role": "device.input_pair", "instances": ["MINP", "MINN"]},
                {
                    "role": "device.common_source",
                    "terminals": [
                        {"instance": "MINP", "terminal": "S", "net": "TAIL"},
                        {"instance": "MINN", "terminal": "S", "net": "TAIL"},
                    ],
                },
            ],
            "instance_parameter_permissions": [
                {"instance": "MINP", "parameters": ["Wfg", "l"]},
                {"instance": "MINN", "parameters": ["Wfg", "l"]},
            ],
        }
    )
    differential = audit_design_context(
        _differential_readback(), differential_context
    )

    assert single.resolved_roles["device.input"] == ["MN0"]
    assert differential.resolved_roles["device.input_pair"] == ["MINN", "MINP"]
    assert single.topology_sha256 != differential.topology_sha256
    assert single.evidence_source == "software_inference"
    assert differential.evidence_source == "software_inference"


def test_context_audit_rejects_role_terminal_drift() -> None:
    readback = _single_ended_readback()
    readback["instances"][0]["terminals"]["G"] = "OUT"
    with pytest.raises(DesignContextError, match="expected MN0.G=IN"):
        audit_design_context(readback, _single_ended_context())


def test_context_audit_requires_declared_parameter_fields_in_readback() -> None:
    readback = _single_ended_readback()
    del readback["instance_parameters"]["MN0"]["Wfg"]
    with pytest.raises(DesignContextError, match="lacks declared parameters"):
        audit_design_context(readback, _single_ended_context())


def test_context_rejects_parameter_request_outside_permission() -> None:
    payload = {
        "id": "manual-refinement",
        "operation": "parameters.apply",
        "circuit": "existing_schematic",
        "target": {"library": "vda_test", "cell": "vda_manual"},
        "instance_parameter_updates": [
            {"instance": "MN0", "parameters": {"m": "2"}}
        ],
        "design_context": _single_ended_context().model_dump(mode="json"),
    }
    with pytest.raises(ValidationError, match=r"MN0.m \(fixed\)"):
        TaskSpec.model_validate(payload)


def test_context_accepts_permitted_manual_parameter_request() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "manual-refinement",
            "operation": "parameters.apply",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_manual"},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"Wfg": "1.2u"}}
            ],
            "design_context": _single_ended_context().model_dump(mode="json"),
        }
    )
    assert task.design_context is not None
    assert task.design_context.id == "user-common-source"


def test_context_guards_predeclared_topology_delta_scope() -> None:
    before = _single_ended_readback()
    after = _single_ended_readback()
    after["nets"].append("NSRC")
    after["instances"][0]["terminals"]["S"] = "NSRC"
    after["instances"].append(
        {
            "name": "RS0",
            "library": "analogLib",
            "cell": "res",
            "view": "symbol",
            "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
        }
    )
    contract = derive_topology_delta("add-local-degeneration", before, after)
    execution = TopologyDeltaExecutionSpec(contract=contract)

    validate_topology_delta_scope(_single_ended_context(), execution)
    restricted = _single_ended_context(
        topology_edits={
            "allowed_operations": ["add_net", "reconnect_terminal"],
            "mutable_instances": ["MN0"],
            "mutable_nets": ["NSRC"],
        }
    )
    with pytest.raises(ValueError, match="add_instance|instance RS0"):
        validate_topology_delta_scope(restricted, execution)


def test_context_rejects_specialized_transform_without_explicit_delta() -> None:
    with pytest.raises(
        ValidationError,
        match="requires an explicit topology_delta",
    ):
        TaskSpec.model_validate(
            {
                "id": "implicit-source-degeneration",
                "operation": "schematic.transform",
                "circuit": "common_source",
                "target": {"library": "vda_test", "cell": "vda_manual"},
                "parameters": {"source_resistance_ohm": 1000.0},
                "schematic_transform": {"action": "add_source_degeneration"},
                "design_context": _single_ended_context().model_dump(mode="json"),
            }
        )


def test_context_requires_permission_for_master_migration_parameters() -> None:
    before = _single_ended_readback()
    after = deepcopy(before)
    after["instances"][0]["cell"] = "nch_lvt_mac"
    contract = derive_topology_delta(
        "change-device-flavor",
        before,
        after,
        master_parameter_migrations=[
            {
                "instance": "MN0",
                "expected_parameters": {"Wfg": "1u"},
                "parameters": {"Wfg": "1u"},
                "undeclared_parameter_policy": "record_only",
            }
        ],
    )
    execution = TopologyDeltaExecutionSpec(contract=contract)
    topology_policy = {
        "allowed_operations": ["replace_master"],
        "mutable_instances": ["MN0"],
    }

    validate_topology_delta_scope(
        _single_ended_context(topology_edits=topology_policy), execution
    )
    restricted = _single_ended_context(
        topology_edits=topology_policy,
        instance_parameter_permissions=[
            {"instance": "MN0", "parameters": ["l"], "modes": ["fixed"]}
        ],
    )
    with pytest.raises(ValueError, match=r"MN0.Wfg \(fixed\)"):
        validate_topology_delta_scope(restricted, execution)


def test_planner_exposes_context_binding_before_remote_write() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "manual-refinement",
            "operation": "parameters.apply",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_manual"},
            "instance_parameter_updates": [
                {"instance": "MN0", "parameters": {"Wfg": "1.2u"}}
            ],
            "design_context": _single_ended_context().model_dump(mode="json"),
        }
    )
    plan = build_plan(task)
    capabilities = [step.capability for step in plan.steps]
    assert capabilities == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
        "parameters.apply",
        "schematic.inspect",
        "evidence.persist",
    ]
    assert plan.steps[2].side_effect.value == "read_only"


class _InspectAdapter:
    name = "context-inspect"

    def probe(self, _pdk_profile: str) -> AdapterResult:
        return AdapterResult(
            data={"ok": True}, evidence_source=EvidenceSource.BRIDGE_READBACK
        )

    def inspect_schematic(self, _task: TaskSpec) -> AdapterResult:
        return AdapterResult(
            data=_single_ended_readback(),
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )


def test_executor_records_context_binding_as_software_inference() -> None:
    task = TaskSpec.model_validate(
        {
            "id": "inspect-user-topology",
            "operation": "schematic.inspect",
            "circuit": "existing_schematic",
            "target": {"library": "vda_test", "cell": "vda_manual"},
            "design_context": _single_ended_context().model_dump(mode="json"),
        }
    )
    plan = build_plan(task)
    record = TaskExecutor(_InspectAdapter()).execute(
        task, plan, token=plan.confirmation_token
    )

    assert record.status is RunStatus.SUCCEEDED
    binding = next(action for action in record.actions if action.action == "design.context.bind")
    assert binding.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    assert binding.details["resolved_roles"]["signal.output"] == ["OUT"]


def test_close_loop_context_declares_analysis_and_metric_surface() -> None:
    context = DesignContext.model_validate(
        {
            "id": "inverter-refinement",
            "roles": [
                {"role": "signal.input", "nets": ["IN"]},
                {"role": "signal.output", "nets": ["OUT"]},
            ],
            "semantic_parameter_permissions": [
                {"parameter": "length_um", "modes": ["fixed"]},
                {"parameter": "load_ff", "modes": ["fixed"]},
                {"parameter": "vdd_v", "modes": ["fixed"]},
                {"parameter": "nmos_width_um", "modes": ["search"]},
                {"parameter": "pmos_width_um", "modes": ["search"]},
            ],
            "required_analyses": ["transient"],
            "metrics": ["delay_ps", "rise_fall_skew_ps"],
        }
    )
    base = {
        "id": "context-close-loop",
        "operation": "design.close_loop",
        "circuit": "inverter",
        "target": {"library": "vda_test", "cell": "vda_inv"},
        "parameters": {"length_um": 0.03, "load_ff": 2.0, "vdd_v": 0.9},
        "parameter_space": {
            "nmos_width_um": [0.5, 0.6],
            "pmos_width_um": [0.6, 0.72],
        },
        "constraints": [
            {"metric": "delay_ps", "relation": "<=", "value": 50.0}
        ],
        "objective": {"metric": "rise_fall_skew_ps", "goal": "minimize"},
        "design_context": context.model_dump(mode="json"),
    }
    task = TaskSpec.model_validate(base)
    capabilities = [step.capability for step in build_plan(task).steps]
    assert capabilities[:3] == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
    ]

    create_missing = dict(base) | {"create_if_missing": True}
    with pytest.raises(ValidationError, match="requires an existing schematic"):
        TaskSpec.model_validate(create_missing)

    drifted = dict(base)
    drifted["constraints"] = [
        {"metric": "overshoot_v", "relation": "<=", "value": 0.05}
    ]
    with pytest.raises(ValidationError, match="does not declare requested metrics"):
        TaskSpec.model_validate(drifted)
