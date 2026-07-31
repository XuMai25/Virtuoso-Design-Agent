from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters.base import AdapterInterrupted, AdapterResult
from virtuoso_design_agent.design_context import (
    DesignContext,
    DesignContextError,
    audit_design_context,
)
from virtuoso_design_agent.executor import TaskExecutor, load_execution_checkpoint
from virtuoso_design_agent.instance_path import (
    is_scoped_instance_path,
    split_instance_path,
)
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan


CHILD_TOPOLOGY_SHA256 = "1" * 64
CHILD_PLACEMENT_SHA256 = "2" * 64


def _hierarchy_context() -> dict:
    return {
        "id": "one-level-child-parameters",
        "roles": [
            {"role": "signal.input", "nets": ["IN"]},
            {"role": "signal.output", "nets": ["OUT"]},
            {"role": "block.amplifier", "instances": ["XAMP"]},
        ],
        "hierarchy_parameter_scopes": [
            {
                "top_instance": "XAMP",
                "library": "vda_test",
                "cell": "vda_child",
                "view": "schematic",
                "expected_child_topology_sha256": CHILD_TOPOLOGY_SHA256,
                "expected_child_placement_sha256": CHILD_PLACEMENT_SHA256,
            }
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
        "required_analyses": ["ac"],
        "metrics": ["low_frequency_gain_v_per_v"],
    }


def _hierarchy_readback() -> dict:
    return {
        "instances": [
            {
                "name": "XAMP",
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
        ],
        "nets": ["IN", "OUT", "VDD", "VSS"],
        "pins": ["IN", "OUT", "VDD", "VSS"],
        "instance_parameters": {
            "XAMP/MN0": {"Wfg": "1u", "l": "30n"},
            "XAMP/RD0": {"r": "20K"},
        },
        "hierarchy_parameter_scopes": {
            "XAMP": {
                "top_instance": "XAMP",
                "child_target": {
                    "library": "vda_test",
                    "cell": "vda_child",
                    "view": "schematic",
                },
                "child_topology_sha256": CHILD_TOPOLOGY_SHA256,
                "child_placement_sha256": CHILD_PLACEMENT_SHA256,
                "state_source": "bridge_readback",
                "binding_source": "software_inference",
            }
        },
    }


def _hierarchy_tune_payload() -> dict:
    return {
        "id": "one-level-child-parameter-tune",
        "operation": "design.tune",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_top",
            "view": "schematic",
        },
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e3,
            "stop_hz": 1e10,
            "points_per_decade": 20,
        },
        "design_context": _hierarchy_context(),
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
            "netlist_parameter_bindings": [
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
            ],
            "hierarchy_bindings": [
                {
                    "instance": "XAMP",
                    "library": "vda_test",
                    "cell": "vda_child",
                    "view": "schematic",
                    "subcircuit": "vda_child",
                    "terminal_order": ["IN", "OUT", "VDD", "VSS"],
                }
            ],
        },
        "candidate_set": {
            "source": {"id": "declared-child-shortlist"},
            "candidates": [
                {
                    "id": "compact",
                    "instance_parameter_updates": [
                        {"instance": "XAMP/MN0", "parameters": {"Wfg": "1u"}},
                        {"instance": "XAMP/RD0", "parameters": {"r": "5K"}},
                    ],
                },
                {
                    "id": "gain",
                    "instance_parameter_updates": [
                        {"instance": "XAMP/MN0", "parameters": {"Wfg": "1.1u"}},
                        {"instance": "XAMP/RD0", "parameters": {"r": "18.5K"}},
                    ],
                },
            ],
        },
        "constraints": [
            {
                "metric": "low_frequency_gain_v_per_v",
                "relation": ">=",
                "value": 1.0,
            }
        ],
        "objective": {
            "metric": "low_frequency_gain_v_per_v",
            "goal": "maximize",
        },
        "limits": {"max_iterations": 2},
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
    }


def test_instance_paths_preserve_oa_names_and_limit_hierarchy_depth() -> None:
    assert split_instance_path("I0<3>") == (None, "I0<3>")
    assert split_instance_path("XAMP/MN0") == ("XAMP", "MN0")
    assert is_scoped_instance_path("XAMP/MN0") is True
    assert is_scoped_instance_path("I0<3>") is False

    for invalid in ("", "/MN0", "XAMP/", "X1/X2/MN0", "X-AMP/MN0"):
        with pytest.raises(ValueError, match="one-level"):
            split_instance_path(invalid)


def test_context_audit_proves_exact_child_scope_and_parameter_fields() -> None:
    audit = audit_design_context(
        _hierarchy_readback(),
        DesignContext.model_validate(_hierarchy_context()),
    )

    assert audit.verified_hierarchy_parameter_scopes == ["XAMP"]
    assert audit.verified_instance_parameter_fields == [
        "XAMP/MN0.Wfg",
        "XAMP/RD0.r",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda readback: readback["hierarchy_parameter_scopes"].clear(),
            "lacks verified hierarchy scope",
        ),
        (
            lambda readback: readback["hierarchy_parameter_scopes"]["XAMP"].update(
                {"child_topology_sha256": "3" * 64}
            ),
            "child topology mismatch",
        ),
        (
            lambda readback: readback["hierarchy_parameter_scopes"]["XAMP"].update(
                {"state_source": "software_inference"}
            ),
            "lacks bridge_readback state",
        ),
    ],
)
def test_context_audit_rejects_missing_or_drifted_child_scope(
    mutate, message: str
) -> None:
    readback = _hierarchy_readback()
    mutate(readback)
    with pytest.raises(DesignContextError, match=message):
        audit_design_context(
            readback,
            DesignContext.model_validate(_hierarchy_context()),
        )


def test_context_rejects_child_aliases_and_unscoped_permissions() -> None:
    aliased = _hierarchy_context()
    second_scope = deepcopy(aliased["hierarchy_parameter_scopes"][0])
    second_scope["top_instance"] = "XAMP2"
    aliased["hierarchy_parameter_scopes"].append(second_scope)
    with pytest.raises(ValidationError, match="alias the same child"):
        DesignContext.model_validate(aliased)

    missing = _hierarchy_context()
    missing["hierarchy_parameter_scopes"] = []
    with pytest.raises(ValidationError, match="matching hierarchy_parameter_scopes"):
        DesignContext.model_validate(missing)


def test_task_accepts_exact_one_level_child_parameter_tuning_contract() -> None:
    task = TaskSpec.model_validate(_hierarchy_tune_payload())

    assert task.design_context is not None
    assert task.design_context.hierarchy_parameter_scopes[0].top_instance == "XAMP"
    assert task.candidate_set is not None
    assert task.candidate_set.candidates[1].instance_parameters() == {
        "XAMP/MN0": {"Wfg": "1.1u"},
        "XAMP/RD0": {"r": "18.5K"},
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["generic_simulation"].update(
                {"hierarchy_bindings": []}
            ),
            "matching hierarchy binding",
        ),
        (
            lambda payload: (
                payload["design_context"]["hierarchy_parameter_scopes"][0].update(
                    {"library": "other_library"}
                ),
                payload["generic_simulation"]["hierarchy_bindings"][0].update(
                    {"library": "other_library"}
                ),
            ),
            "target design library",
        ),
        (
            lambda payload: (
                payload["design_context"]["hierarchy_parameter_scopes"][0].update(
                    {"cell": "vda_top"}
                ),
                payload["generic_simulation"]["hierarchy_bindings"][0].update(
                    {"cell": "vda_top"}
                ),
            ),
            "recursively target the top cell",
        ),
    ],
)
def test_task_rejects_unbound_cross_library_or_recursive_child_scope(
    mutate, message: str
) -> None:
    payload = _hierarchy_tune_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(payload)


def test_task_rejects_nested_child_instance_paths() -> None:
    payload = _hierarchy_tune_payload()
    payload["candidate_set"]["candidates"][0]["instance_parameter_updates"][0][
        "instance"
    ] = "XAMP/XMID/MN0"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        TaskSpec.model_validate(payload)


class _ScopedHierarchyAdapter:
    name = "scoped-hierarchy-fixture"

    def __init__(self) -> None:
        self.width = "1u"
        self.resistance = "20K"
        self.interrupt_after: tuple[str, str] | None = None
        self.simulated: list[tuple[str, str]] = []

    def probe(self, _profile: str) -> AdapterResult:
        return AdapterResult(
            data={"connected": True},
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def _inspection(self) -> dict:
        result = _hierarchy_readback()
        result["instance_parameters"]["XAMP/MN0"]["Wfg"] = self.width
        result["instance_parameters"]["XAMP/RD0"]["r"] = self.resistance
        return result

    def inspect_schematic(self, _task: TaskSpec) -> AdapterResult:
        return AdapterResult(
            data=self._inspection(),
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    @staticmethod
    def _requested(task: TaskSpec) -> dict[str, dict[str, str]]:
        return {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        assert parameters == {}
        requested = self._requested(task)
        before = {
            "XAMP/MN0": {"Wfg": self.width},
            "XAMP/RD0": {"r": self.resistance},
        }
        self.width = requested["XAMP/MN0"]["Wfg"]
        self.resistance = requested["XAMP/RD0"]["r"]
        if self.interrupt_after == (self.width, self.resistance):
            self.interrupt_after = None
            raise AdapterInterrupted("transport reset after scoped child OA write")
        return AdapterResult(
            data={
                "requested_instance_parameters": requested,
                "applied_instance_parameters": requested,
                "before_instance_parameters": before,
                "confirmed_instance_parameters": requested,
                "readback": self._inspection(),
            },
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def verify_parameters(
        self, _task: TaskSpec, expected: dict[str, dict[str, str]]
    ) -> AdapterResult:
        actual = {
            "XAMP/MN0": {"Wfg": self.width},
            "XAMP/RD0": {"r": self.resistance},
        }
        if expected != actual:
            raise RuntimeError("scoped child parameter readback mismatch")
        result = self._inspection()
        result["confirmed_instance_parameters"] = actual
        return AdapterResult(
            data=result,
            evidence_source=EvidenceSource.BRIDGE_READBACK,
        )

    def simulate(
        self, _task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        assert parameters == {}
        point = (self.width, self.resistance)
        self.simulated.append(point)
        score = {
            ("1u", "5K"): 2.0,
            ("1.1u", "18.5K"): 5.0,
        }[point]
        return AdapterResult(
            data={
                "parameters": {},
                "metrics": {"low_frequency_gain_v_per_v": score},
                "metric_sources": {
                    "low_frequency_gain_v_per_v": "eda_result"
                },
                "analysis_complete": True,
            },
            evidence_source=EvidenceSource.EDA_RESULT,
        )


def test_executor_selects_and_reconfirms_scoped_child_candidate(tmp_path) -> None:
    task = TaskSpec.model_validate(_hierarchy_tune_payload())
    adapter = _ScopedHierarchyAdapter()
    plan = build_plan(task)
    checkpoint_path = tmp_path / "hierarchy-child.json"

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert record.selected_instance_parameters == {
        "XAMP/MN0": {"Wfg": "1.1u"},
        "XAMP/RD0": {"r": "18.5K"},
    }
    assert (adapter.width, adapter.resistance) == ("1.1u", "18.5K")
    assert adapter.simulated == [("1u", "5K"), ("1.1u", "18.5K")]
    assert load_execution_checkpoint(checkpoint_path).complete is True


def test_scoped_child_infeasible_domain_restores_exact_initial_state() -> None:
    payload = _hierarchy_tune_payload()
    payload["constraints"][0]["value"] = 10.0
    task = TaskSpec.model_validate(payload)
    adapter = _ScopedHierarchyAdapter()
    plan = build_plan(task)

    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    assert record.selected_instance_parameters is None
    assert (adapter.width, adapter.resistance) == ("1u", "20K")


def test_scoped_child_interruption_recovers_and_resumes_checkpoint(tmp_path) -> None:
    task = TaskSpec.model_validate(_hierarchy_tune_payload())
    adapter = _ScopedHierarchyAdapter()
    adapter.interrupt_after = ("1.1u", "18.5K")
    plan = build_plan(task)
    checkpoint_path = tmp_path / "hierarchy-child-resume.json"

    first = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
    )
    checkpoint = load_execution_checkpoint(checkpoint_path)

    assert first.status is RunStatus.FAILED
    assert checkpoint.next_candidate_index == 2
    assert checkpoint.pending_oa_instance_parameters is None
    assert (adapter.width, adapter.resistance) == ("1u", "20K")

    resumed = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=checkpoint,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.selected_instance_parameters == {
        "XAMP/MN0": {"Wfg": "1.1u"},
        "XAMP/RD0": {"r": "18.5K"},
    }
    assert adapter.simulated == [("1u", "5K"), ("1.1u", "18.5K")]
