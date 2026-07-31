from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.generic_simulation import ParameterBindingDiscoverySpec
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.parameter_binding import (
    canonical_parameter_table_sha256,
    classify_parameter_binding_probe,
    reclassify_parameter_binding_run,
    validate_parameter_binding_eda_stage,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.topology_delta import (
    snapshot_from_inspection,
    topology_fingerprint,
)


EXPECTED_CDF = {
    "Wfg": "1u",
    "fingers": "1",
    "l": "30n",
    "m": "1",
    "model": "nch_lvt_mac",
}


def _inspection() -> dict:
    return {
        "instances": [
            {
                "name": "MN0",
                "library": "tsmcN28",
                "cell": "nch_lvt_mac",
                "view": "symbol",
                "parameters": dict(EXPECTED_CDF),
                "terminals": {
                    "D": "OUT",
                    "G": "IN",
                    "S": "VSS",
                    "B": "VSS",
                },
            }
        ],
        "nets": ["IN", "OUT", "VSS"],
        "pins": ["IN", "OUT", "VSS"],
        "parameters": {},
        "semantic_parameters": {},
        "instance_parameters": {"MN0": dict(EXPECTED_CDF)},
    }


def _task_payload() -> dict:
    topology_sha256 = topology_fingerprint(
        snapshot_from_inspection(_inspection())
    )
    return {
        "id": "binding-discovery-demo",
        "operation": "parameters.binding.discover",
        "circuit": "existing_schematic",
        "target": {
            "library": "vda_test",
            "cell": "vda_binding_discovery_001",
            "view": "schematic",
        },
        "design_context": {
            "id": "binding-discovery-context",
            "expected_topology_sha256": topology_sha256,
            "roles": [
                {"role": "device.input", "instances": ["MN0"]},
                {"role": "signal.input", "nets": ["IN"]},
                {"role": "signal.output", "nets": ["OUT"]},
            ],
            "instance_parameter_permissions": [
                {
                    "instance": "MN0",
                    "parameters": ["Wfg"],
                    "modes": ["fixed"],
                }
            ],
        },
        "parameter_binding_discovery": {
            "instance": "MN0",
            "oa_parameter": "Wfg",
            "probe_value": "2u",
            "expected_instance_parameters": dict(EXPECTED_CDF),
        },
        "safety": {
            "allow_remote_compute": True,
            "allow_remote_write": True,
            "allowed_library": "vda_test",
        },
    }


def _task() -> TaskSpec:
    return TaskSpec.model_validate(_task_payload())


def _demo_schematic() -> dict:
    inspection = _inspection()
    return {
        "instances": deepcopy(inspection["instances"]),
        "nets": list(inspection["nets"]),
        "pins": list(inspection["pins"]),
        "parameters": {},
        "semantic_parameters": {},
        "instance_parameters": {"MN0": dict(EXPECTED_CDF)},
    }


def test_binding_discovery_contract_and_plan_are_bounded() -> None:
    task = _task()
    plan = build_plan(task)

    assert plan.requires_remote_write is True
    assert plan.requires_remote_compute is True
    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "schematic.inspect",
        "design.context.bind",
        "parameters.binding.netlist-baseline",
        "parameters.binding.probe",
        "parameters.binding.netlist-probe",
        "parameters.binding.restore",
        "parameters.binding.netlist-restored",
        "parameters.binding.evaluate",
        "schematic.inspect",
        "evidence.persist",
    ]
    assert task.analysis is None
    assert task.generic_simulation is None
    assert task.instance_parameter_updates == []


def test_binding_discovery_rejects_unbound_or_noncausal_probe_contracts() -> None:
    same_value = _task_payload()
    same_value["parameter_binding_discovery"]["probe_value"] = "1000n"
    with pytest.raises(ValidationError, match="probe must differ"):
        TaskSpec.model_validate(same_value)

    missing_full_field = _task_payload()
    missing_full_field["parameter_binding_discovery"][
        "expected_instance_parameters"
    ].pop("Wfg")
    with pytest.raises(ValidationError, match="missing from the expected"):
        TaskSpec.model_validate(missing_full_field)

    unpermitted = _task_payload()
    unpermitted["design_context"]["instance_parameter_permissions"] = []
    with pytest.raises(ValidationError, match="does not permit requested"):
        TaskSpec.model_validate(unpermitted)

    no_topology_cas = _task_payload()
    no_topology_cas["design_context"].pop("expected_topology_sha256")
    with pytest.raises(ValidationError, match="exact expected topology"):
        TaskSpec.model_validate(no_topology_cas)

    unscoped_child = _task_payload()
    unscoped_child["parameter_binding_discovery"]["hierarchy_bindings"] = [
        {
            "instance": "XAMP",
            "library": "vda_test",
            "cell": "vda_child",
            "subcircuit": "vda_child",
            "terminal_order": ["IN", "OUT", "VSS"],
        }
    ]
    with pytest.raises(ValidationError, match="must exactly match"):
        TaskSpec.model_validate(unscoped_child)


def test_parser_can_inventory_si_parameters_without_a_simulation_contract() -> None:
    schematic = {
        "instances": [
            {
                "name": "MN0",
                "lib": "tsmcN28",
                "cell": "nch_lvt_mac",
                "params": {"model": "nch_lvt_mac", **EXPECTED_CDF},
                "terms": {
                    "D": "OUT",
                    "G": "IN",
                    "S": "VSS",
                    "B": "VSS",
                },
            }
        ],
        "nets": {name: {} for name in ("IN", "OUT", "VSS")},
        "pins": {name: {} for name in ("IN", "OUT", "VSS")},
    }
    parsed = bridge_worker._parse_existing_schematic_netlist(  # noqa: SLF001
        "MN0 (OUT IN VSS VSS) nch_lvt_mac w=1u l=30n nf=1 multi=1\n",
        schematic,
        None,
        include_parameter_inventory=True,
    )

    assert parsed["topology_consistency"] == "matched"
    assert parsed["parameter_inventory"]["MN0"]["parameters"] == {
        "l": "30n",
        "multi": "1",
        "nf": "1",
        "w": "1u",
    }
    assert len(parsed["canonical_signature_sha256"]) == 64


@pytest.mark.parametrize(
    ("probe_oa", "probe_netlist", "expected_status"),
    [
        (
            {**EXPECTED_CDF, "Wfg": "2u"},
            {"w": "2u", "l": "30n"},
            "direct_literal_binding",
        ),
        (
            {**EXPECTED_CDF, "Wfg": "2u"},
            {"w": "4u", "l": "30n"},
            "single_netlist_parameter_nonliteral",
        ),
        (
            {**EXPECTED_CDF, "Wfg": "2u", "fingers": "2"},
            {"w": "2u", "l": "30n"},
            "callback_coupled",
        ),
        (
            {**EXPECTED_CDF, "Wfg": "2u"},
            {"w": "2u", "l": "60n"},
            "ambiguous_netlist_change",
        ),
        (
            {**EXPECTED_CDF, "Wfg": "2u"},
            {"w": "1u", "l": "30n"},
            "inert",
        ),
    ],
)
def test_binding_classification_never_promotes_ambiguous_or_derived_changes(
    probe_oa: dict[str, str],
    probe_netlist: dict[str, str],
    expected_status: str,
) -> None:
    spec = ParameterBindingDiscoverySpec.model_validate(
        _task_payload()["parameter_binding_discovery"]
    )
    classified = classify_parameter_binding_probe(
        spec,
        baseline_oa_parameters=dict(EXPECTED_CDF),
        probe_oa_parameters=probe_oa,
        baseline_netlist_inventory={
            "MN0": {
                "model": "nch_lvt_mac",
                "nodes": ["OUT", "IN", "VSS", "VSS"],
                "parameters": {"w": "1u", "l": "30n"},
            }
        },
        probe_netlist_inventory={
            "MN0": {
                "model": "nch_lvt_mac",
                "nodes": ["OUT", "IN", "VSS", "VSS"],
                "parameters": probe_netlist,
            }
        },
    )

    assert classified["status"] == expected_status
    assert classified["same_name_assumption_used"] is False
    if expected_status == "direct_literal_binding":
        assert classified["promoted_binding"] == {
            "instance": "MN0",
            "oa_parameter": "Wfg",
            "netlist_parameter": "w",
        }
    else:
        assert classified["promoted_binding"] is None


def test_binding_classification_checks_the_full_netlist_inventory() -> None:
    spec = ParameterBindingDiscoverySpec.model_validate(
        _task_payload()["parameter_binding_discovery"]
    )
    baseline = {
        "MN0": {
            "model": "nch_lvt_mac",
            "nodes": ["OUT", "IN", "VSS", "VSS"],
            "parameters": {"w": "1u", "l": "30n"},
        },
        "RD0": {
            "model": "resistor",
            "nodes": ["VDD", "OUT"],
            "parameters": {"r": "5k"},
        },
    }
    cross_instance = deepcopy(baseline)
    cross_instance["MN0"]["parameters"]["w"] = "2u"
    cross_instance["RD0"]["parameters"]["r"] = "6k"
    ambiguous = classify_parameter_binding_probe(
        spec,
        baseline_oa_parameters=dict(EXPECTED_CDF),
        probe_oa_parameters={**EXPECTED_CDF, "Wfg": "2u"},
        baseline_netlist_inventory=baseline,
        probe_netlist_inventory=cross_instance,
    )
    assert ambiguous["status"] == "ambiguous_netlist_change"
    assert ambiguous["promoted_binding"] is None

    structural = deepcopy(baseline)
    structural["MN0"]["model"] = "nch_rvt_mac"
    structural["MN0"]["parameters"]["w"] = "2u"
    changed_structure = classify_parameter_binding_probe(
        spec,
        baseline_oa_parameters=dict(EXPECTED_CDF),
        probe_oa_parameters={**EXPECTED_CDF, "Wfg": "2u"},
        baseline_netlist_inventory=baseline,
        probe_netlist_inventory=structural,
    )
    assert changed_structure["status"] == "netlist_structure_changed"
    assert changed_structure["promoted_binding"] is None


def test_binding_classification_promotes_one_literal_with_mirrored_callbacks() -> None:
    spec = ParameterBindingDiscoverySpec.model_validate(
        {
            **_task_payload()["parameter_binding_discovery"],
            "expected_instance_parameters": {
                **EXPECTED_CDF,
                "w": "1u",
                "ad": "5e-14",
                "as": "5e-14",
                "display_width": "1u/30n",
            },
        }
    )
    classified = classify_parameter_binding_probe(
        spec,
        baseline_oa_parameters=spec.expected_instance_parameters,
        probe_oa_parameters={
            **spec.expected_instance_parameters,
            "Wfg": "2u",
            "w": "2u",
            "ad": "1e-13",
            "as": "1e-13",
            "display_width": "2u/30n",
        },
        baseline_netlist_inventory={
            "MN0": {
                "model": "nch_lvt_mac",
                "nodes": ["OUT", "IN", "VSS", "VSS"],
                "parameters": {
                    "w": "1u",
                    "l": "30n",
                    "ad": "5e-14",
                    "as": "5e-14",
                },
            }
        },
        probe_netlist_inventory={
            "MN0": {
                "model": "nch_lvt_mac",
                "nodes": ["OUT", "IN", "VSS", "VSS"],
                "parameters": {
                    "w": "2u",
                    "l": "30n",
                    "ad": "1e-13",
                    "as": "1e-13",
                },
            }
        },
    )

    assert classified["status"] == (
        "direct_literal_binding_with_derived_callbacks"
    )
    assert classified["promoted_binding"] == {
        "instance": "MN0",
        "oa_parameter": "Wfg",
        "netlist_parameter": "w",
    }
    assert classified["callback_effects_verified"] is True
    assert [
        item["netlist_parameter"]
        for item in classified["derived_callback_evidence"]
    ] == ["ad", "as"]
    assert {
        item["parameter"] for item in classified["dependent_oa_changes"]
    } == {"ad", "as", "display_width", "w"}


def _fake_worker_state(current: dict[str, str]) -> dict:
    return {
        "schematic": {},
        "summary": {},
        "instance_parameters": dict(current),
        "instance_parameters_sha256": canonical_parameter_table_sha256(current),
        "topology_sha256": "a" * 64,
        "design_context_sha256": "b" * 64,
    }


def _fake_netlist(current: dict[str, str], stage: str) -> dict:
    original = current["Wfg"]
    inventory = {
        "MN0": {
            "model": "nch_lvt_mac",
            "nodes": ["OUT", "IN", "VSS", "VSS"],
            "parameters": {"w": original, "l": "30n"},
        }
    }
    return {
        "instance": "MN0",
        "instance_model": "nch_lvt_mac",
        "instance_nodes": ["OUT", "IN", "VSS", "VSS"],
        "instance_parameters": {"w": original, "l": "30n"},
        "parameter_inventory": inventory,
        "canonical_netlist_signature_sha256": (
            "c" * 64 if original == "1u" else "d" * 64
        ),
        "raw_netlist": {"source": "eda_result", "sha256": "e" * 64},
        "artifact_bundle": {"source": "eda_result", "stage": stage},
        "remote_cleanup": {"removed": True, "source": "system_event"},
        "source": "eda_result",
    }


def test_worker_transaction_restores_probe_and_recovers_declared_interruption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task()
    payload = task.model_dump(mode="json", exclude_none=True)
    payload["binding_discovery_output_root"] = str(tmp_path / "evidence")
    current = {**EXPECTED_CDF, "Wfg": "2u"}
    writes: list[str] = []
    monkeypatch.setattr(bridge_worker, "_client", lambda: object())
    monkeypatch.setattr(
        bridge_worker,
        "_binding_discovery_state",
        lambda *_args: _fake_worker_state(current),
    )

    def write(_client, _payload, discovery, value):
        writes.append(value)
        current[discovery.oa_parameter] = value
        return {}

    monkeypatch.setattr(bridge_worker, "_binding_discovery_write", write)
    monkeypatch.setattr(
        bridge_worker,
        "_binding_discovery_netlist_stage",
        lambda _client, _payload, _discovery, _state, *, stage, **_kwargs: (
            _fake_netlist(current, stage)
        ),
    )

    result = bridge_worker.discover_existing_schematic_parameter_binding(payload)

    assert writes == ["1u", "2u", "1u"]
    assert current == EXPECTED_CDF
    assert result["interrupted_probe_recovery"]["performed"] is True
    assert result["restoration"]["verified"] is True
    assert result["classification"]["status"] == "direct_literal_binding"


def test_worker_transaction_restores_oa_when_probe_netlisting_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task()
    payload = task.model_dump(mode="json", exclude_none=True)
    payload["binding_discovery_output_root"] = str(tmp_path / "failed-evidence")
    current = dict(EXPECTED_CDF)
    monkeypatch.setattr(bridge_worker, "_client", lambda: object())
    monkeypatch.setattr(
        bridge_worker,
        "_binding_discovery_state",
        lambda *_args: _fake_worker_state(current),
    )

    def write(_client, _payload, discovery, value):
        current[discovery.oa_parameter] = value
        return {}

    def netlist(_client, _payload, _discovery, _state, *, stage, **_kwargs):
        if stage == "probe":
            raise RuntimeError("injected si failure")
        return _fake_netlist(current, stage)

    monkeypatch.setattr(bridge_worker, "_binding_discovery_write", write)
    monkeypatch.setattr(bridge_worker, "_binding_discovery_netlist_stage", netlist)

    with pytest.raises(RuntimeError, match="injected si failure"):
        bridge_worker.discover_existing_schematic_parameter_binding(payload)

    assert current == EXPECTED_CDF


def test_binding_artifact_manifest_hashes_the_persisted_evidence(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "oa_netlist.scs").write_text(
        "MN0 (OUT IN VSS VSS) nch_lvt_mac w=1u\n",
        encoding="utf-8",
    )
    (work_dir / "si_batch_stdout.log").write_text(
        "si completed\n",
        encoding="utf-8",
    )

    bundle = bridge_worker._persist_binding_netlist_artifacts(  # noqa: SLF001
        work_dir,
        tmp_path / "evidence",
        "baseline",
    )

    manifest = Path(bundle["manifest_path"])
    assert bundle["manifest_sha256"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    assert {item["relative_path"] for item in bundle["files"]} == {
        "baseline/oa_netlist.scs",
        "baseline/si_batch_stdout.log",
    }
    for item in bundle["files"]:
        artifact = Path(item["path"])
        assert item["size_bytes"] == artifact.stat().st_size
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    netlist_sha256 = next(
        item["sha256"]
        for item in bundle["files"]
        if item["relative_path"] == "baseline/oa_netlist.scs"
    )
    stage = {
        "source": "eda_result",
        "canonical_netlist_signature_sha256": "a" * 64,
        "raw_netlist": {
            "source": "eda_result",
            "remote_path": "/data/xum/runs/vda_binding_probe/netlist",
            "remote_retained": False,
            "sha256": netlist_sha256,
        },
        "remote_cleanup": {
            "source": "system_event",
            "remote_path": "/data/xum/runs/vda_binding_probe",
            "removed": True,
        },
        "artifact_bundle": bundle,
    }
    validate_parameter_binding_eda_stage("baseline", stage)

    (tmp_path / "evidence" / "baseline" / "oa_netlist.scs").write_text(
        "tampered\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="artifact bytes do not match"):
        validate_parameter_binding_eda_stage("baseline", stage)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/home/xum/vda_binding_probe",
        "/data/xum/not_a_vda_run",
        "/data/xum/../../home/xum/vda_binding_probe",
    ],
)
def test_binding_remote_cleanup_rejects_paths_outside_exact_vda_scratch(
    unsafe_path: str,
) -> None:
    class ClientThatMustNotRun:
        def run_shell_command(self, *_args, **_kwargs):
            raise AssertionError("unsafe cleanup reached the remote shell")

    with pytest.raises(RuntimeError, match="unexpected remote path"):
        bridge_worker._cleanup_binding_netlist_scratch(  # noqa: SLF001
            ClientThatMustNotRun(),
            unsafe_path,
        )


def test_binding_remote_cleanup_uses_ssh_exit_status_for_silent_commands() -> None:
    class Result:
        returncode = 0
        stderr = ""

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def run_command(self, command: str, *, timeout: int):
            self.calls.append((command, timeout))
            return Result()

    class Client:
        def __init__(self) -> None:
            self.ssh_runner = Runner()

        def run_shell_command(self, *_args, **_kwargs):
            raise AssertionError("cleanup must not use Virtuoso csh over SSH")

    client = Client()
    remote_path = "/data/xum/virtuoso_bridge_smoke/vda_binding_probe_123"

    cleanup = bridge_worker._cleanup_binding_netlist_scratch(  # noqa: SLF001
        client,
        remote_path,
    )

    assert client.ssh_runner.calls == [
        (f"rm -rf -- {remote_path}", 60),
        (f"test ! -e {remote_path}", 30),
    ]
    assert cleanup == {
        "remote_path": remote_path,
        "removed": True,
        "transport": "bridge_ssh_runner",
        "source": "system_event",
    }


def test_binding_remote_cleanup_propagates_ssh_failure() -> None:
    class Result:
        returncode = 1
        stderr = "permission denied"

    class Runner:
        def run_command(self, _command: str, *, timeout: int):
            assert timeout == 60
            return Result()

    class Client:
        ssh_runner = Runner()

    with pytest.raises(RuntimeError, match="permission denied"):
        bridge_worker._cleanup_binding_netlist_scratch(  # noqa: SLF001
            Client(),
            "/data/xum/virtuoso_bridge_smoke/vda_binding_probe_123",
        )


def test_si_init_scopes_foreground_log_away_from_remote_nfs_scratch() -> None:
    skill = bridge_worker._si_init_environment_skill(  # noqa: SLF001
        "/data/xum/virtuoso_bridge_smoke/vda_binding_probe_123",
        "vda_test",
        "vda_binding_probe_001",
        "schematic",
    )

    assert skill == (
        'let((simForeGndLogFile) simForeGndLogFile="/dev/null" '
        'simInitEnvWithArgs('
        '"/data/xum/virtuoso_bridge_smoke/vda_binding_probe_123" '
        '"vda_test" "vda_binding_probe_001" "schematic" "spectre" nil))'
    )


def test_binding_reclassification_reuses_persisted_eda_bytes(tmp_path: Path) -> None:
    task = _task()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    adapter._schematics[(task.target.library, task.target.cell)] = _demo_schematic()  # noqa: SLF001
    run = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    ).model_dump(mode="json")
    run["adapter"] = "virtuoso-bridge-subprocess"
    run["status"] = "partial"
    action = next(
        item
        for item in run["actions"]
        if item["action"] == "parameters.binding.discover"
    )
    action["evidence_source"] = "eda_result"
    details = action["details"]
    details["remote_compute_performed"] = True
    for stage_name in ("baseline", "probe", "restored"):
        stage = details[stage_name]
        work_dir = tmp_path / f"{stage_name}-work"
        work_dir.mkdir()
        netlist = (
            "MN0 (OUT IN VSS VSS) nch_lvt_mac "
            f"w={stage['netlist_instance_parameters']['w']}\n"
        )
        (work_dir / "oa_netlist.scs").write_text(netlist, encoding="utf-8")
        (work_dir / "si_batch_stdout.log").write_text(
            "End netlisting\n",
            encoding="utf-8",
        )
        bundle = bridge_worker._persist_binding_netlist_artifacts(  # noqa: SLF001
            work_dir,
            tmp_path / "evidence",
            stage_name,
        )
        netlist_sha256 = next(
            item["sha256"]
            for item in bundle["files"]
            if item["relative_path"] == f"{stage_name}/oa_netlist.scs"
        )
        remote_dir = f"/data/xum/virtuoso_bridge_smoke/vda_{stage_name}_probe"
        stage.update(
            oa_source="bridge_readback",
            source="eda_result",
            raw_netlist={
                "source": "eda_result",
                "remote_path": f"{remote_dir}/netlist",
                "remote_retained": False,
                "sha256": netlist_sha256,
            },
            artifact_bundle=bundle,
            remote_cleanup={
                "source": "system_event",
                "remote_path": remote_dir,
                "removed": True,
            },
        )
    source = tmp_path / "source-run.json"
    source.write_text(json.dumps(run), encoding="utf-8")

    result = reclassify_parameter_binding_run(source)

    assert result["classification"]["status"] == "direct_literal_binding"
    assert result["classification"]["promoted_binding"] == {
        "instance": "MN0",
        "oa_parameter": "Wfg",
        "netlist_parameter": "w",
    }
    assert result["local_artifacts_revalidated"] is True
    assert result["recorded_remote_cleanup_claims_validated"] is True
    assert result["remote_paths_rechecked"] is False
    assert result["remote_execution_performed"] is False
    assert result["oa_write_performed"] is False


def test_executor_records_derived_binding_separately_and_leaves_oa_restored() -> None:
    task = _task()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    adapter._schematics[(task.target.library, task.target.cell)] = _demo_schematic()  # noqa: SLF001

    run = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert run.status is RunStatus.SUCCEEDED
    assert adapter._schematics[(task.target.library, task.target.cell)][  # noqa: SLF001
        "instance_parameters"
    ]["MN0"] == EXPECTED_CDF
    evaluation = next(
        action
        for action in run.actions
        if action.action == "parameters.binding.evaluate"
    )
    assert evaluation.evidence_source.value == "software_inference"
    assert evaluation.details["classification"]["promoted_binding"] == {
        "instance": "MN0",
        "oa_parameter": "Wfg",
        "netlist_parameter": "w",
    }
    assert run.selected_instance_parameters is None


def test_executor_rejects_a_worker_classification_not_derived_from_raw_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    adapter._schematics[(task.target.library, task.target.cell)] = _demo_schematic()  # noqa: SLF001
    original_discovery = adapter.discover_parameter_binding

    def fabricated_discovery(requested_task: TaskSpec):
        result = original_discovery(requested_task)
        result.data["classification"] = {
            **result.data["classification"],
            "status": "inert",
            "literal_identity_verified": False,
            "promoted_binding": None,
        }
        return result

    monkeypatch.setattr(
        adapter,
        "discover_parameter_binding",
        fabricated_discovery,
    )

    run = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert run.status is RunStatus.FAILED
    assert any("independent evidence evaluation" in note for note in run.notes)
    assert not any(
        action.action == "parameters.binding.evaluate"
        and action.status == "succeeded"
        for action in run.actions
    )


def test_executor_rejects_real_eda_claims_without_raw_artifacts_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    plan = build_plan(task)
    adapter = DeterministicDemoAdapter()
    adapter._schematics[(task.target.library, task.target.cell)] = _demo_schematic()  # noqa: SLF001
    original_discovery = adapter.discover_parameter_binding

    def incomplete_eda_discovery(requested_task: TaskSpec) -> AdapterResult:
        result = original_discovery(requested_task)
        result.data["remote_compute_performed"] = True
        for stage_name in ("baseline", "probe", "restored"):
            result.data[stage_name]["oa_source"] = "bridge_readback"
            result.data[stage_name]["source"] = "eda_result"
        return AdapterResult(
            data=result.data,
            evidence_source=EvidenceSource.EDA_RESULT,
        )

    monkeypatch.setattr(
        adapter,
        "discover_parameter_binding",
        incomplete_eda_discovery,
    )

    run = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert run.status is RunStatus.FAILED
    assert any("lacks immutable raw si evidence" in note for note in run.notes)
    assert not any(
        action.action == "parameters.binding.evaluate"
        and action.status == "succeeded"
        for action in run.actions
    )


def test_subprocess_adapter_routes_discovery_to_dedicated_worker_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task()
    adapter = SubprocessBridgeAdapter(artifact_root=tmp_path)
    observed: dict = {}

    def request(action: str, payload: dict, *, timeout: int) -> dict:
        observed.update(action=action, payload=payload, timeout=timeout)
        return {"ok": True}

    monkeypatch.setattr(adapter, "_request", request)
    result = adapter.discover_parameter_binding(task)

    assert result.evidence_source.value == "eda_result"
    assert observed["action"] == "discover_existing_schematic_parameter_binding"
    assert observed["payload"]["parameter_binding_discovery"][
        "oa_parameter"
    ] == "Wfg"
    assert Path(observed["payload"]["binding_discovery_output_root"]).parent == (
        tmp_path / task.id
    )
    assert observed["timeout"] == task.limits.timeout_seconds * 3 + 300
