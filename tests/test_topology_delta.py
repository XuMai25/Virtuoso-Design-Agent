from __future__ import annotations

import copy

import pytest

from virtuoso_design_agent.topology_delta import (
    AddInstanceOperation,
    AddNetOperation,
    AddPinOperation,
    ReconnectTerminalOperation,
    RemoveInstanceOperation,
    RemoveNetOperation,
    RemovePinOperation,
    ReplaceMasterOperation,
    TopologyDeltaContract,
    TopologyDeltaExecutionSpec,
    TopologyDeltaError,
    TopologyInstance,
    TopologyMaster,
    TopologyNet,
    TopologyPin,
    apply_topology_delta,
    apply_topology_delta_execution,
    apply_topology_operations,
    compile_topology_delta,
    derive_topology_delta,
    snapshot_from_inspection,
    topology_fingerprint,
    validate_topology_readback,
    validate_topology_execution_readback,
)


def _common_source_before() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "library": "tsmcN28",
                "cell": "nch_lvt_mac",
                "view": "symbol",
                "parameters": {"Wfg": "1u", "l": "30n"},
                "terminals": {
                    "D": "OUT",
                    "G": "IN",
                    "S": "VSS",
                    "B": "VSS",
                },
                "xy": [0.0, 0.0],
                "orient": "R0",
            },
            {
                "name": "RD0",
                "library": "analogLib",
                "cell": "res",
                "view": "symbol",
                "parameters": {"r": "20K"},
                "terminals": {"PLUS": "VDD", "MINUS": "OUT"},
                "xy": [0.0, 1.3],
                "orient": "R0",
            },
        ],
        "nets": ["VSS", "OUT", "IN", "VDD"],
        "pins": ["VSS", "OUT", "IN", "VDD"],
        "instance_parameters": {
            "MN0": {"Wfg": "1u", "l": "30n"},
            "RD0": {"r": "20K"},
        },
    }


def _common_source_after() -> dict:
    after = copy.deepcopy(_common_source_before())
    next(item for item in after["instances"] if item["name"] == "MN0")[
        "terminals"
    ]["S"] = "NSRC"
    after["instances"].append(
        {
            "name": "RS0",
            "library": "analogLib",
            "cell": "res",
            "view": "symbol",
            "parameters": {"r": "1K"},
            "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
            "xy": [0.0, -1.3],
            "orient": "R0",
        }
    )
    after["nets"].append("NSRC")
    after["instance_parameters"]["RS0"] = {"r": "1K"}
    return after


def test_source_degeneration_derives_minimal_reversible_contract() -> None:
    before = _common_source_before()
    after = _common_source_after()

    contract = derive_topology_delta("source-degeneration", before, after)

    assert [item.operation for item in contract.operations] == [
        "add_net",
        "reconnect_terminal",
        "add_instance",
    ]
    assert [item.operation for item in contract.inverse_operations] == [
        "remove_instance",
        "reconnect_terminal",
        "remove_net",
    ]
    assert apply_topology_delta(before, contract) == snapshot_from_inspection(after)
    audit = validate_topology_readback(before, after, contract)
    assert audit.forward_readback_match is True
    assert audit.inverse_restored_before is True
    assert audit.evidence_source == "software_inference"

    serialized = contract.model_dump_json()
    assert TopologyDeltaContract.model_validate_json(serialized) == contract


def test_topology_fingerprint_excludes_instance_parameters() -> None:
    before = _common_source_before()
    retargeted = copy.deepcopy(before)
    retargeted["instances"][0]["parameters"]["Wfg"] = "2u"
    retargeted["instance_parameters"]["MN0"]["Wfg"] = "2u"

    assert topology_fingerprint(snapshot_from_inspection(before)) == topology_fingerprint(
        snapshot_from_inspection(retargeted)
    )
    contract = derive_topology_delta("parameter-only-noop", before, retargeted)
    assert contract.operations == []
    assert contract.inverse_operations == []


def test_allowlisted_operations_apply_and_restore_exact_structure() -> None:
    before = snapshot_from_inspection(
        {
            "instances": [
                {
                    "name": "M0",
                    "library": "oldlib",
                    "cell": "oldmos",
                    "view": "symbol",
                    "terminals": {"D": "OUT", "G": "IN", "S": "OLD"},
                    "xy": [1.0, 2.0],
                    "orient": "R0",
                },
                {
                    "name": "X0",
                    "library": "analogLib",
                    "cell": "res",
                    "view": "symbol",
                    "terminals": {"PLUS": "OLD", "MINUS": "VSS"},
                },
            ],
            "nets": ["IN", "OUT", "OLD", "VSS"],
            "pins": ["IN", "OLD", "OUT", "VSS"],
        }
    )
    m0 = next(item for item in before.instances if item.name == "M0")
    x0 = next(item for item in before.instances if item.name == "X0")
    old_pin = next(item for item in before.pins if item.name == "OLD")
    old_net = next(item for item in before.nets if item.name == "OLD")
    new_master = TopologyMaster(library="newlib", cell="newmos", view="symbol")
    x1 = TopologyInstance(
        name="X1",
        master=TopologyMaster(library="analogLib", cell="res", view="symbol"),
        terminals={"PLUS": "NEW", "MINUS": "VSS"},
    )
    operations = [
        AddNetOperation(net=TopologyNet(name="NEW")),
        RemovePinOperation(expected=old_pin),
        RemoveInstanceOperation(expected=x0),
        ReplaceMasterOperation(
            instance="M0",
            expected_master=m0.master,
            master=new_master,
        ),
        ReconnectTerminalOperation(
            instance="M0",
            terminal="S",
            expected_net="OLD",
            net="NEW",
        ),
        AddInstanceOperation(instance=x1),
        AddPinOperation(pin=TopologyPin(name="NEW", net="NEW")),
        RemoveNetOperation(expected=old_net),
    ]

    contract = compile_topology_delta("all-allowlisted-operations", before, operations)
    after = apply_topology_delta(before, contract)
    restored = apply_topology_operations(after, contract.inverse_operations)

    assert topology_fingerprint(restored) == contract.expected_before_sha256
    assert restored == before
    assert {item.name for item in after.instances} == {"M0", "X1"}
    assert next(item for item in after.instances if item.name == "M0").master == (
        new_master
    )


def test_contract_rejects_stale_before_fingerprint() -> None:
    before = _common_source_before()
    contract = derive_topology_delta(
        "source-degeneration",
        before,
        _common_source_after(),
    )
    stale = copy.deepcopy(before)
    stale["nets"].append("UNDECLARED")

    with pytest.raises(TopologyDeltaError, match="before fingerprint mismatch"):
        apply_topology_delta(stale, contract)


def test_predeclared_execution_supports_exact_forward_and_inverse_directions() -> None:
    before = _common_source_before()
    after = _common_source_after()
    contract = derive_topology_delta("directional-source-degeneration", before, after)

    forward = TopologyDeltaExecutionSpec(direction="forward", contract=contract)
    inverse = TopologyDeltaExecutionSpec(direction="inverse", contract=contract)

    assert apply_topology_delta_execution(before, forward) == snapshot_from_inspection(
        after
    )
    assert apply_topology_delta_execution(after, inverse) == snapshot_from_inspection(
        before
    )
    forward_audit = validate_topology_execution_readback(before, after, forward)
    inverse_audit = validate_topology_execution_readback(after, before, inverse)
    assert forward_audit.direction == "forward"
    assert inverse_audit.direction == "inverse"
    assert forward_audit.output_readback_match is True
    assert inverse_audit.roundtrip_restored_input is True


def test_inverse_execution_rejects_a_non_after_input_fingerprint() -> None:
    contract = derive_topology_delta(
        "directional-source-degeneration",
        _common_source_before(),
        _common_source_after(),
    )

    with pytest.raises(TopologyDeltaError, match="inverse input fingerprint mismatch"):
        apply_topology_delta_execution(
            _common_source_before(),
            TopologyDeltaExecutionSpec(direction="inverse", contract=contract),
        )


def test_operation_rejects_old_state_conflict() -> None:
    before = snapshot_from_inspection(_common_source_before())

    with pytest.raises(TopologyDeltaError, match="expected old net"):
        compile_topology_delta(
            "stale-terminal-cas",
            before,
            [
                ReconnectTerminalOperation(
                    instance="MN0",
                    terminal="S",
                    expected_net="NSRC",
                    net="VSS",
                )
            ],
        )


def test_complete_after_readback_rejects_missing_object() -> None:
    before = _common_source_before()
    after = _common_source_after()
    contract = derive_topology_delta("source-degeneration", before, after)
    incomplete = copy.deepcopy(after)
    incomplete["pins"].remove("OUT")

    with pytest.raises(TopologyDeltaError, match="complete expected structure"):
        validate_topology_readback(before, incomplete, contract)


def test_snapshot_rejects_dangling_terminal() -> None:
    readback = _common_source_before()
    readback["nets"].remove("VSS")

    with pytest.raises(TopologyDeltaError, match="references missing nets"):
        snapshot_from_inspection(readback)


def test_derivation_rejects_unallowlisted_placement_change() -> None:
    before = _common_source_before()
    moved = copy.deepcopy(before)
    moved["instances"][0]["xy"] = [9.0, 9.0]

    with pytest.raises(TopologyDeltaError, match="move/reshape"):
        derive_topology_delta("unsupported-move", before, moved)


def test_fingerprint_is_independent_of_readback_order() -> None:
    before = _common_source_before()
    reordered = copy.deepcopy(before)
    reordered["instances"].reverse()
    reordered["nets"].reverse()
    reordered["pins"].reverse()
    reordered["instances"][0]["terminals"] = dict(
        reversed(list(reordered["instances"][0]["terminals"].items()))
    )

    assert topology_fingerprint(snapshot_from_inspection(before)) == topology_fingerprint(
        snapshot_from_inspection(reordered)
    )
