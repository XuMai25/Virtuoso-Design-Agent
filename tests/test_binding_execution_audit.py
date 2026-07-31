from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from virtuoso_design_agent.binding_execution_audit import (
    OnboardingBindingExecutionAudit,
    audit_onboarding_binding_execution,
)
from virtuoso_design_agent.binding_promotion import (
    OnboardingBindingPromotionCompilation,
    OnboardingPromotedBindingEvidence,
)
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.execution_scope import (
    canonical_task_bytes,
    compile_execution_scope,
)
from virtuoso_design_agent.metrics import evaluate_constraints
from virtuoso_design_agent.models import (
    ActionRecord,
    EvidenceSource,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from virtuoso_design_agent.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]


def _model_bytes(model) -> bytes:
    return (model.model_dump_json(indent=2, exclude_none=True) + "\n").encode(
        "utf-8"
    )


def _audit_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = TaskSpec.model_validate_json(
        (
            ROOT
            / "examples/tasks/existing-schematic-generic-common-source-ac.bridge.json"
        ).read_bytes()
    )
    payload = source.model_dump(mode="json", exclude_none=True)
    payload["id"] = "binding-execution-audit"
    payload.pop("analysis", None)
    payload["analysis_stages"] = [
        {
            "id": "dc-gate",
            "analysis": "dc",
            "constraint_metrics": ["output_dc_v"],
        },
        {
            "id": "ac-gate",
            "analysis": "ac",
            "constraint_metrics": [
                "low_frequency_gain_v_per_v",
                "bandwidth_3db_hz",
            ],
        },
    ]
    payload["analysis_stage_execution"] = "shared_netlist"
    payload["safety"]["allow_remote_compute"] = False
    payload["safety"]["allow_remote_write"] = False
    binding_payload = payload["generic_simulation"]["netlist_parameter_bindings"][0]
    binding_payload["derived_callbacks"] = [
        {"oa_parameter": "ad", "netlist_parameter": "ad"},
        {"oa_parameter": "as", "netlist_parameter": "as"},
    ]
    binding_payload["discovery_source"] = {
        "discovery_task_id": "binding-source",
        "discovery_plan_token": "0123456789abcdef",
        "discovery_task_sha256": "a" * 64,
        "discovery_run_sha256": "b" * 64,
        "classification": "direct_literal_binding_with_derived_callbacks",
        "topology_sha256": payload["design_context"][
            "expected_topology_sha256"
        ],
        "complete_cdf_sha256": "c" * 64,
    }
    safe_task = TaskSpec.model_validate(payload)
    safe_path = tmp_path / "safe.json"
    safe_path.write_bytes(canonical_task_bytes(safe_task))
    execution_task, scope = compile_execution_scope(safe_path)
    task_path = tmp_path / "execution.json"
    scope_path = tmp_path / "scope.json"
    task_path.write_bytes(canonical_task_bytes(execution_task))
    scope_path.write_bytes(_model_bytes(scope))

    assert safe_task.target is not None
    assert safe_task.design_context is not None
    promoted_binding = safe_task.generic_simulation.netlist_parameter_bindings[0]
    promoted_evidence = OnboardingPromotedBindingEvidence(
        discovery_task_id="binding-source",
        discovery_plan_token="0123456789abcdef",
        discovery_task_sha256="a" * 64,
        discovery_run_sha256="b" * 64,
        discovery_run_status="succeeded",
        target=safe_task.target,
        pdk_profile=safe_task.pdk_profile,
        topology_sha256=safe_task.design_context.expected_topology_sha256,
        complete_cdf_sha256="c" * 64,
        complete_cdf_parameter_count=10,
        baseline_netlist_signature_sha256="d" * 64,
        probe_netlist_signature_sha256="e" * 64,
        restored_netlist_signature_sha256="d" * 64,
        binding=promoted_binding,
        evidence_sources={
            "oa": "bridge_readback",
            "netlist": "eda_result",
            "intent": "user_input",
            "classification": "software_inference",
            "cleanup": "system_event",
        },
    )
    promotion = OnboardingBindingPromotionCompilation(
        onboarding_draft_sha256="f" * 64,
        onboarding_resolution_sha256="1" * 64,
        promoted_bindings=[promoted_evidence],
        compiled_task_id=safe_task.id,
        compiled_task_sha256=hashlib.sha256(canonical_task_bytes(safe_task)).hexdigest(),
        compiled_plan_token=build_plan(safe_task).confirmation_token,
        evidence_sources={
            "oa": "bridge_readback",
            "netlist": "eda_result",
            "intent": "user_input",
            "compilation": "software_inference",
        },
    )
    promotion_path = tmp_path / "promotion.json"
    promotion_path.write_bytes(_model_bytes(promotion))

    dc_metrics = {"output_dc_v": 0.4}
    ac_metrics = {
        "output_dc_v": 0.4,
        "low_frequency_gain_v_per_v": 4.0,
        "bandwidth_3db_hz": 2.0e9,
        "gain_bandwidth_product_hz": 8.0e9,
    }
    stage_metrics = [dc_metrics, ac_metrics]
    manifests = ["2" * 64, "3" * 64]
    stage_results = []
    for stage, metrics, manifest_sha256 in zip(
        execution_task.analysis_stages,
        stage_metrics,
        manifests,
        strict=True,
    ):
        constraints = [
            item
            for item in execution_task.constraints
            if item.metric in stage.constraint_metrics
        ]
        stage_results.append(
            {
                "stage_id": stage.id,
                "analysis": stage.analysis.value,
                "gate_evaluations": [
                    item.model_dump(mode="json")
                    for item in evaluate_constraints(metrics, constraints)
                ],
                "gate_evidence_source": "software_inference",
                "result": {
                    "metrics": metrics,
                    "analysis_complete": True,
                    "analysis_issues": [],
                    "analysis_warnings": [],
                    "evidence": {
                        "simulation": {
                            "source": "eda_result",
                            "artifact_manifest_complete": True,
                            "artifact_manifest": [
                                {
                                    "path": f"{stage.id}.raw",
                                    "size_bytes": 10,
                                    "sha256": "4" * 64,
                                }
                            ],
                            "artifact_manifest_sha256": manifest_sha256,
                        },
                        "side_effects": {
                            "oa_access_performed": True,
                            "oa_write_performed": False,
                            "remote_compute_performed": True,
                        },
                    },
                },
            }
        )

    now = datetime.now(UTC)
    netlist_sha256 = "5" * 64
    record = RunRecord(
        task_id=execution_task.id,
        plan_token=build_plan(execution_task).confirmation_token,
        adapter="virtuoso-bridge-subprocess",
        status=RunStatus.SUCCEEDED,
        started_at=now,
        finished_at=now + timedelta(seconds=2),
        selected_metrics=ac_metrics,
        actions=[
            ActionRecord(
                action="bridge.probe",
                status="succeeded",
                started_at=now,
                finished_at=now,
                evidence_source=EvidenceSource.BRIDGE_READBACK,
                details={},
            ),
            ActionRecord(
                action="simulation.candidate.1.stages.shared-netlist",
                status="succeeded",
                started_at=now,
                finished_at=now + timedelta(seconds=2),
                evidence_source=EvidenceSource.EDA_RESULT,
                details={
                    "shared_netlist": {
                        "source": "software_inference",
                        "remote_path": "/data/xum/test/netlist",
                        "sha256": netlist_sha256,
                        "netlist_generation_count": 1,
                        "schematic_readback_count": 1,
                        "executed_stages": [
                            stage.id for stage in execution_task.analysis_stages
                        ],
                        "reuse_contract": (
                            "one_worker_one_oa_readback_one_si_netlist"
                        ),
                    },
                    "evidence": {
                        "schematic_readback": {
                            "source": "bridge_readback",
                            "topology_sha256": (
                                execution_task.design_context.expected_topology_sha256
                            ),
                        },
                        "netlist": {
                            "source": "eda_result",
                            "remote_path": "/data/xum/test/netlist",
                            "sha256": netlist_sha256,
                            "topology_consistency": "matched",
                            "parameter_consistency": "matched",
                            "parameter_bindings": [
                                {
                                    "instance": promoted_binding.instance,
                                    "oa_parameter": promoted_binding.oa_parameter,
                                    "netlist_parameter": (
                                        promoted_binding.netlist_parameter
                                    ),
                                    "oa_value": "1u",
                                    "netlist_value": "1u",
                                }
                            ],
                            "derived_callback_consistency": "matched",
                            "derived_callback_bindings": [
                                {
                                    "instance": promoted_binding.instance,
                                    "primary_oa_parameter": (
                                        promoted_binding.oa_parameter
                                    ),
                                    "primary_netlist_parameter": (
                                        promoted_binding.netlist_parameter
                                    ),
                                    "oa_parameter": name,
                                    "netlist_parameter": name,
                                    "oa_value": "5e-14",
                                    "netlist_value": "5e-14",
                                }
                                for name in ("ad", "as")
                            ],
                        },
                        "stage_gate_source": "software_inference",
                    },
                    "stage_results": stage_results,
                    "terminated_after_stage": None,
                },
            ),
        ],
    )
    run_path = tmp_path / "run.json"
    run_path.write_bytes(_model_bytes(record))
    return promotion_path, scope_path, task_path, run_path


def test_binding_execution_audit_passes_exact_chain_and_rejects_callback_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    promotion, scope, task, run = _audit_inputs(tmp_path)

    audit = audit_onboarding_binding_execution(promotion, scope, task, run)

    assert audit.status == "passed"
    assert len(audit.primary_bindings) == 1
    assert [item.oa_parameter for item in audit.derived_callbacks] == ["ad", "as"]
    assert [item.analysis.value for item in audit.stages] == ["dc", "ac"]
    assert all(item.passed for item in audit.constraints)
    assert audit.oa_write_performed is False
    assert audit.remote_compute_performed is True

    output = tmp_path / "audit.json"
    assert (
        main(
            [
                "onboarding-binding-audit",
                str(promotion),
                str(scope),
                str(task),
                str(run),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert OnboardingBindingExecutionAudit.model_validate_json(
        output.read_bytes()
    ) == audit

    payload = json.loads(run.read_text(encoding="utf-8"))
    payload["actions"][-1]["details"]["evidence"]["netlist"][
        "derived_callback_bindings"
    ][0]["netlist_value"] = "6e-14"
    run.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="derived callback mismatch"):
        audit_onboarding_binding_execution(promotion, scope, task, run)
