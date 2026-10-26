from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.topology_delta import (
    snapshot_from_inspection,
    topology_fingerprint,
)


def _task(*, sha256: str = "0" * 64) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "generate-user-block-symbol",
            "operation": "schematic.symbol.generate",
            "circuit": "existing_schematic",
            "target": {
                "library": "vda_test",
                "cell": "vda_child",
                "view": "schematic",
            },
            "symbol_generation": {
                "expected_schematic_topology_sha256": sha256,
                "expected_pins": [
                    {"name": "IN", "direction": "input"},
                    {"name": "OUT", "direction": "output"},
                    {"name": "VDD", "direction": "inputOutput"},
                    {"name": "VSS", "direction": "inputOutput"},
                ],
                "pin_sort": "geometric",
            },
            "safety": {
                "allow_remote_write": True,
                "allowed_library": "vda_test",
                "required_cell_prefix": "vda_",
                "replace_existing": False,
            },
        }
    )


def _source_summary() -> dict:
    topology = {
        "instances": [],
        "nets": [
            {"name": name, "attributes": {}}
            for name in ("IN", "OUT", "VDD", "VSS")
        ],
        "pins": [
            {
                "name": name,
                "net": name,
                "direction": direction,
                "attributes": {},
            }
            for name, direction in (
                ("IN", "input"),
                ("OUT", "output"),
                ("VDD", "inputOutput"),
                ("VSS", "inputOutput"),
            )
        ],
    }
    return {
        "topology": topology,
        "bridge_schematic": {
            "pins": {
                name: {"direction": direction, "numBits": 1}
                for name, direction in (
                    ("IN", "input"),
                    ("OUT", "output"),
                    ("VDD", "inputOutput"),
                    ("VSS", "inputOutput"),
                )
            }
        },
    }


def test_symbol_task_is_non_overwrite_remote_write_with_stable_payload() -> None:
    task = _task()
    plan = build_plan(task)

    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "schematic.inspect.source",
        "schematic.symbol.generate",
        "schematic.symbol.inspect",
        "evidence.persist",
    ]
    assert plan.requires_remote_write is True
    assert plan.requires_remote_compute is False
    payload = SubprocessBridgeAdapter._task_payload(task)
    assert payload["symbol_generation"]["pin_sort"] == "geometric"
    assert "analysis" not in payload
    assert payload["replace_existing"] is False


def test_symbol_task_rejects_wrong_surface_and_overwrite() -> None:
    raw = _task().model_dump(mode="json")
    raw["target"]["view"] = "symbol"
    with pytest.raises(ValidationError, match="target.view='schematic'"):
        TaskSpec.model_validate(raw)

    raw = _task().model_dump(mode="json")
    raw["safety"]["replace_existing"] = True
    with pytest.raises(ValidationError, match="never replaces"):
        TaskSpec.model_validate(raw)


def test_symbol_executor_performs_separate_source_write_and_readback_actions() -> None:
    class SymbolAdapter:
        name = "symbol-test"

        def probe(self, _profile):
            return AdapterResult({}, EvidenceSource.BRIDGE_READBACK)

        def inspect_schematic(self, _task):
            return AdapterResult(
                {"symbol_source_contract": {"topology_match": True}},
                EvidenceSource.BRIDGE_READBACK,
            )

        def generate_schematic_symbol(self, _task):
            return AdapterResult(
                {"created": True, "session_setting": {"restored": True}},
                EvidenceSource.BRIDGE_READBACK,
            )

        def inspect_schematic_symbol(self, _task):
            return AdapterResult(
                {"terminals_match": True, "non_empty_bbox": True},
                EvidenceSource.BRIDGE_READBACK,
            )

    task = _task()
    plan = build_plan(task)
    record = TaskExecutor(SymbolAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert [action.action for action in record.actions] == [
        "bridge.probe",
        "schematic.inspect.source",
        "schematic.symbol.generate",
        "schematic.symbol.inspect",
    ]


def test_symbol_readback_parser_requires_terminals_and_bbox() -> None:
    parsed = bridge_worker._parse_symbol_readback(
        "SYMBOL\n"
        "TERM|IN|input|1\n"
        "TERM|OUT|output|1\n"
        "BBOX|((-1 -1) (1 1))\n"
        "END\n"
    )

    assert parsed["terminal_order"] == ["IN", "OUT"]
    assert parsed["non_empty_bbox"] is True
    with pytest.raises(RuntimeError, match="empty bounding box"):
        bridge_worker._parse_symbol_readback(
            "SYMBOL\nTERM|IN|input|1\nBBOX|nil\nEND\n"
        )


def test_worker_symbol_generation_uses_documented_api_and_restores_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_summary()
    sha256 = topology_fingerprint(snapshot_from_inspection(source))
    task = _task(sha256=sha256)
    payload = SubprocessBridgeAdapter._task_payload(task)
    state = {"symbol": False, "skill": ""}

    class FakeClient:
        def execute_skill(self, skill, timeout):
            state["skill"] = skill
            if "schPinListToSymbol" in skill:
                state["symbol"] = True
            return SimpleNamespace(output='"t"', errors=[])

    monkeypatch.setattr(bridge_worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        bridge_worker,
        "_fresh_existing_schematic_summary",
        lambda *_args: source,
    )
    monkeypatch.setattr(
        bridge_worker,
        "_cellview_exists",
        lambda *_args: state["symbol"],
    )
    monkeypatch.setattr(
        bridge_worker,
        "_schematic_env_literal",
        lambda *_args: '"alphanumeric"',
    )

    result = bridge_worker.generate_existing_schematic_symbol(payload)

    assert result["created"] is True
    assert result["session_setting"]["restored"] is True
    assert "unwindProtect" in state["skill"]
    assert "schSchemToPinList" in state["skill"]
    assert "schPinListToSymbol" in state["skill"]


def test_symbol_source_contract_detects_topology_or_pin_drift() -> None:
    source = _source_summary()
    sha256 = topology_fingerprint(snapshot_from_inspection(source))
    settings = SubprocessBridgeAdapter._task_payload(_task(sha256=sha256))[
        "symbol_generation"
    ]

    assert bridge_worker._symbol_source_contract(source, settings)[
        "topology_match"
    ] is True
    drifted = {**source, "bridge_schematic": {"pins": {}}}
    with pytest.raises(RuntimeError, match="pin contract mismatch"):
        bridge_worker._symbol_source_contract(drifted, settings)
