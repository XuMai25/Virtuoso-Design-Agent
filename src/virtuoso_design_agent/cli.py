"""Dependency-light command line interface for humans and higher-level agents."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .adapters import DeterministicDemoAdapter, SubprocessBridgeAdapter
from .adapters.subprocess_bridge import BridgeWorkerError
from .binding_discovery_compiler import (
    ParameterBindingDiscoveryIntent,
    compile_parameter_binding_discovery_task,
)
from .binding_execution_audit import audit_onboarding_binding_execution
from .binding_promotion import compile_onboarding_with_discovered_bindings
from .bridge_lifecycle import BridgeLifecycleError, run_bridge_lifecycle
from .catalog import UnsupportedCapability, catalog_as_dicts
from .cascode_seed import (
    CascodeSeedPolicy,
    build_task_from_cascode_seed,
    derive_cascode_seed,
)
from .characterization_seed import (
    DeviceCharacterizationGridTemplate,
    derive_device_characterization_task,
)
from .executor import (
    TaskExecutor,
    load_execution_checkpoint,
    save_run_record,
)
from .execution_scope import compile_execution_scope
from .models import (
    DEFAULT_PDK_PROFILE,
    ExecutionPlan,
    Operation,
    RunStatus,
    TaskSpec,
)
from .onboarding import build_onboarding_draft
from .onboarding_promotion import compile_onboarding_post_refinement
from .onboarding_resolution import (
    ExistingSchematicOnboardingResolution,
    resolve_onboarding_draft,
)
from .op_small_signal import (
    OperatingPointSmallSignalPolicy,
    analyze_operating_point_small_signal_run,
)
from .op_relinearization import (
    OperatingPointRelinearizationPolicy,
    build_task_from_relinearization,
    relinearize_operating_point,
    validate_relinearization_run,
)
from .parameter_binding import reclassify_parameter_binding_run
from .planner import build_plan
from .preview_compile import build_preview_task_from_candidates
from .preview_oa_handoff import build_oa_task_from_preview_shortlist
from .preview_selection import (
    ProspectivePreviewPolicy,
    PreviewSelectionPolicy,
    audit_frozen_preview_shortlist,
    freeze_preview_shortlist,
    validate_preview_selection,
)
from .resource_audit import audit_local_resources, load_retention_pins
from .safety import SafetyViolation
from .small_signal import SmallSignalNetworkRequest, analyze_small_signal_network
from .small_signal_validation import (
    SmallSignalCircuitValidationPolicy,
    validate_small_signal_runs,
)
from .theory import DifferentialPairTheoryRequest, size_differential_pair
from .theory_calibration import calibrate_differential_pair_theory
from .theory_derivation import (
    DifferentialPairTheoryDerivationPolicy,
    derive_differential_pair_theory_request,
)
from .theory_seed import (
    DifferentialPairTheorySeedPolicy,
    build_theory_seeded_task,
)
from .theory_seed_validation import (
    TheorySeedValidationPolicy,
    validate_theory_seed_run,
)
from .topology_delta import compile_topology_delta


def _load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _print_plan(plan: ExecutionPlan, *, as_json: bool = False) -> None:
    if as_json:
        print(plan.model_dump_json(indent=2))
        return
    print(f"Task: {plan.task_id}")
    print(f"Operation: {plan.operation.value}")
    print(f"Circuit: {plan.circuit.value}")
    for step in plan.steps:
        print(
            f"  {step.id:>18}  [{step.side_effect.value:<14}] "
            f"{step.capability} - {step.description}"
        )
    print(f"Plan token: {plan.confirmation_token}")


def _adapter(name: str, bridge_python: str | None = None):
    if name == "demo":
        return DeterministicDemoAdapter()
    return SubprocessBridgeAdapter(bridge_python=bridge_python)


def _run_path(root: Path, task_id: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / task_id / f"run-{stamp}.json"


def _cmd_catalog(args: argparse.Namespace) -> int:
    data = catalog_as_dicts()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for item in data:
        state = "executable" if item["executable"] else "planned"
        print(f"{item['circuit']}: {item['stage']} [{state}]")
        if item["operations"]:
            print(f"  operations: {', '.join(item['operations'])}")
        if item["explicit_instance_parameters"]:
            raw_tuning = (
                " + bounded tuning"
                if Operation.DESIGN_TUNE.value in item["operations"]
                else ""
            )
            print(
                "  explicit instance parameters: parameters.apply"
                f"{raw_tuning} + OA readback"
            )
            if (
                Operation.PARAMETERS_BINDING_DISCOVER.value
                in item["operations"]
            ):
                print(
                    "  parameter binding discovery: reversible OA probe + "
                    "three si netlists"
                )
        print(f"  evidence gate: {item['evidence_gate']}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    task = _load_task(args.task)
    _print_plan(build_plan(task), as_json=args.json)
    return 0


def _cmd_theory(args: argparse.Namespace) -> int:
    request = DifferentialPairTheoryRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    result = size_differential_pair(request)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


def _cmd_theory_calibrate(args: argparse.Namespace) -> int:
    result = calibrate_differential_pair_theory(
        args.training_run,
        args.validation_run,
        calibration_id=args.id,
    )
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_theory_seed_task(args: argparse.Namespace) -> int:
    policy = DifferentialPairTheorySeedPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    task = build_theory_seeded_task(
        policy,
        args.theory_result,
        args.task_template,
    )
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_theory_seed_validate(args: argparse.Namespace) -> int:
    policy = TheorySeedValidationPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = validate_theory_seed_run(policy, args.task, args.run_record)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_cascode_seed(args: argparse.Namespace) -> int:
    policy = CascodeSeedPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = derive_cascode_seed(policy, args.source_run)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_candidate_task_from_cascode_seed(args: argparse.Namespace) -> int:
    task = build_task_from_cascode_seed(args.result, args.task_template)
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_preview_task_from_candidates(args: argparse.Namespace) -> int:
    task = build_preview_task_from_candidates(
        args.policy,
        args.candidate_source,
        args.task_template,
    )
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_oa_task_from_preview_shortlist(args: argparse.Namespace) -> int:
    task = build_oa_task_from_preview_shortlist(
        args.selection,
        args.candidate_task,
        task_id=args.id,
    )
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_preview_select(args: argparse.Namespace) -> int:
    policy = PreviewSelectionPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = validate_preview_selection(
        policy,
        args.preview_task,
        args.preview_run,
        args.reference_run,
    )
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_preview_shortlist(args: argparse.Namespace) -> int:
    policy = ProspectivePreviewPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = freeze_preview_shortlist(
        policy,
        args.preview_task,
        args.preview_run,
        args.reference_task,
    )
    payload = result.model_dump_json(indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_preview_shortlist_audit(args: argparse.Namespace) -> int:
    policy = ProspectivePreviewPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = audit_frozen_preview_shortlist(
        policy,
        args.shortlist,
        args.preview_task,
        args.preview_run,
        args.reference_task,
        args.reference_run,
    )
    payload = result.model_dump_json(indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_theory_request_from_validation(args: argparse.Namespace) -> int:
    policy = DifferentialPairTheoryDerivationPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    request = derive_differential_pair_theory_request(
        policy,
        args.validation,
        [args.characterization_run, *args.additional_characterization_run],
    )
    payload = request.model_dump_json(indent=2, exclude_none=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_op_relinearize(args: argparse.Namespace) -> int:
    policy = OperatingPointRelinearizationPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = relinearize_operating_point(policy, args.source_run)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_candidate_task_from_relinearization(args: argparse.Namespace) -> int:
    task = build_task_from_relinearization(args.result, args.task_template)
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_op_relinearization_validate(args: argparse.Namespace) -> int:
    result = validate_relinearization_run(
        args.result,
        args.task,
        args.run_record,
        args.policy,
    )
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_small_signal(args: argparse.Namespace) -> int:
    request = SmallSignalNetworkRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    result = analyze_small_signal_network(request)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


def _cmd_small_signal_from_run(args: argparse.Namespace) -> int:
    policy = OperatingPointSmallSignalPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = analyze_operating_point_small_signal_run(policy, args.circuit_run)
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _inspection_details(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("topology readback JSON must be an object")
    actions = payload.get("actions")
    if isinstance(actions, list):
        for action in reversed(actions):
            if (
                isinstance(action, dict)
                and action.get("status") == "succeeded"
                and str(action.get("action", "")).startswith("schematic.inspect")
                and isinstance(action.get("details"), dict)
            ):
                topology = action["details"].get("topology")
                if isinstance(topology, dict):
                    return topology
                raise ValueError(
                    "successful schematic.inspect action has no canonical topology; "
                    "compile the contract from an existing_schematic inspection so "
                    "the compiler and topology writer use the same structure summary"
                )
        raise ValueError(
            "run record has no successful schematic.inspect action with details"
        )
    return payload


def _cmd_topology_compile(args: argparse.Namespace) -> int:
    readback = _inspection_details(
        json.loads(args.readback.read_text(encoding="utf-8"))
    )
    raw_operations = json.loads(args.operations.read_text(encoding="utf-8"))
    operations = (
        raw_operations.get("operations")
        if isinstance(raw_operations, dict)
        else raw_operations
    )
    if not isinstance(operations, list):
        raise ValueError("topology operations JSON must be a list or an operations list")
    migrations = (
        raw_operations.get("master_parameter_migrations", [])
        if isinstance(raw_operations, dict)
        else []
    )
    if not isinstance(migrations, list):
        raise ValueError("master_parameter_migrations must be a list")
    contract = compile_topology_delta(
        args.id,
        readback,
        operations,
        master_parameter_migrations=migrations,
    )
    payload = contract.model_dump_json(indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_onboarding_draft(args: argparse.Namespace) -> int:
    child_inspections = [
        (top_instance, Path(task_path), Path(run_path))
        for top_instance, task_path, run_path in args.child_inspection
    ]
    draft = build_onboarding_draft(
        args.inspect_task,
        args.inspect_run,
        draft_id=args.id,
        child_inspections=child_inspections,
    )
    payload = draft.model_dump_json(indent=2, exclude_none=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_binding_discovery_task(args: argparse.Namespace) -> int:
    child_inspections = [
        (top_instance, Path(task_path), Path(run_path))
        for top_instance, task_path, run_path in args.child_inspection
    ]
    intent = ParameterBindingDiscoveryIntent.model_validate_json(
        args.intent.read_text(encoding="utf-8")
    )
    task, compilation = compile_parameter_binding_discovery_task(
        args.inspect_task,
        args.inspect_run,
        intent,
        child_inspections=child_inspections,
    )
    outputs = (
        (args.output, task.model_dump_json(indent=2, exclude_none=True)),
        (
            args.record_output,
            compilation.model_dump_json(indent=2, exclude_none=True),
        ),
    )
    for path, payload in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((payload + "\n").encode("utf-8"))
    print(outputs[0][1])
    print(outputs[1][1])
    return 0


def _cmd_binding_reclassify(args: argparse.Namespace) -> int:
    result = reclassify_parameter_binding_run(args.run_record)
    payload = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_onboarding_resolve(args: argparse.Namespace) -> int:
    resolution = ExistingSchematicOnboardingResolution.model_validate_json(
        args.resolution.read_text(encoding="utf-8")
    )
    task = resolve_onboarding_draft(args.draft, resolution)
    payload = task.model_dump_json(indent=2, exclude_none=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_onboarding_resolve_bindings(args: argparse.Namespace) -> int:
    binding_sources = [
        (Path(task_path), Path(run_path))
        for task_path, run_path in args.binding_source
    ]
    task, compilation = compile_onboarding_with_discovered_bindings(
        args.draft,
        args.resolution,
        binding_sources,
    )
    outputs = (
        (args.output, task.model_dump_json(indent=2, exclude_none=True)),
        (
            args.record_output,
            compilation.model_dump_json(indent=2, exclude_none=True),
        ),
    )
    for path, payload in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((payload + "\n").encode("utf-8"))
    print(outputs[0][1])
    print(outputs[1][1])
    return 0


def _cmd_execution_scope(args: argparse.Namespace) -> int:
    task, compilation = compile_execution_scope(args.task)
    outputs = (
        (args.output, task.model_dump_json(indent=2, exclude_none=True)),
        (
            args.record_output,
            compilation.model_dump_json(indent=2, exclude_none=True),
        ),
    )
    for path, payload in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((payload + "\n").encode("utf-8"))
    print(outputs[0][1])
    print(outputs[1][1])
    return 0


def _cmd_onboarding_binding_audit(args: argparse.Namespace) -> int:
    audit = audit_onboarding_binding_execution(
        args.promotion,
        args.execution_scope,
        args.task,
        args.run_record,
    )
    payload = audit.model_dump_json(indent=2, exclude_none=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((payload + "\n").encode("utf-8"))
    print(payload)
    return 0


def _cmd_onboarding_promote(args: argparse.Namespace) -> int:
    child_inspections = [
        (top_instance, Path(task_path), Path(run_path))
        for top_instance, task_path, run_path in args.child_inspection
    ]
    draft, task, compilation = compile_onboarding_post_refinement(
        args.source_task,
        args.source_run,
        args.winner_inspect_task,
        args.winner_inspect_run,
        args.intent,
        child_inspections=child_inspections,
    )
    outputs = (
        (args.draft_output, draft.model_dump_json(indent=2, exclude_none=True)),
        (args.task_output, task.model_dump_json(indent=2, exclude_none=True)),
        (
            args.record_output,
            compilation.model_dump_json(indent=2, exclude_none=True),
        ),
    )
    for path, payload in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((payload + "\n").encode("utf-8"))
    print(compilation.model_dump_json(indent=2, exclude_none=True))
    return 0


def _cmd_small_signal_validate(args: argparse.Namespace) -> int:
    policy = SmallSignalCircuitValidationPolicy.model_validate_json(
        args.policy.read_text(encoding="utf-8")
    )
    result = validate_small_signal_runs(
        policy,
        [args.characterization_run, *args.additional_characterization_run],
        args.circuit_run,
    )
    payload = result.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if result.status is RunStatus.SUCCEEDED else 1


def _cmd_characterization_task_from_run(args: argparse.Namespace) -> int:
    template = DeviceCharacterizationGridTemplate.model_validate_json(
        args.template.read_text(encoding="utf-8")
    )
    task = derive_device_characterization_task(
        template,
        args.circuit_run,
        task_id=args.id,
        instance_name=args.instance,
        polarity=args.polarity,
        source_action=args.source_action,
        operating_condition_name=args.operating_condition,
    )
    payload = task.model_dump_json(
        indent=2,
        exclude_none=True,
        exclude_unset=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    task = _load_task(args.task)
    plan = build_plan(task)
    if not args.execute:
        _print_plan(plan, as_json=False)
        print("No action executed. Re-run with --execute and the plan token.")
        return 0
    if not args.token:
        raise SafetyViolation("--execute requires --token")

    output = args.output or _run_path(args.artifact_root, task.id)
    tuning = task.operation in {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}
    if args.resume is not None and args.checkpoint is not None:
        if args.resume.resolve() != args.checkpoint.resolve():
            raise ValueError("--resume and --checkpoint must name the same file")
    checkpoint_path = args.resume or args.checkpoint
    if tuning and checkpoint_path is None:
        checkpoint_path = output.with_name(f"{output.stem}.checkpoint.json")
    resume_checkpoint = (
        load_execution_checkpoint(args.resume) if args.resume is not None else None
    )

    adapter = _adapter(args.adapter, args.bridge_python)
    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=args.token,
        checkpoint_path=checkpoint_path,
        resume_checkpoint=resume_checkpoint,
    )
    save_run_record(record, output)
    print(f"Status: {record.status.value}")
    print(f"Adapter: {record.adapter}")
    if record.selected_parameters:
        print(
            "Selected parameters: "
            + json.dumps(record.selected_parameters, ensure_ascii=False, sort_keys=True)
        )
    if record.selected_instance_parameters:
        print(
            "Selected instance parameters: "
            + json.dumps(
                record.selected_instance_parameters,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    if record.selected_metrics:
        print(
            "Selected metrics: "
            + json.dumps(record.selected_metrics, ensure_ascii=False, sort_keys=True)
        )
    if record.search_audit is not None:
        print(f"Selection scope: {record.search_audit.selection_scope.value}")
        print(f"Selection boundary: {record.search_audit.statement}")
    for note in record.notes:
        print(f"Note: {note}")
    if checkpoint_path is not None:
        print(f"Checkpoint: {checkpoint_path.resolve()}")
    print(f"Run record: {output.resolve()}")
    return 1 if record.status is RunStatus.FAILED else 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = _adapter(args.adapter, args.bridge_python).probe(args.pdk_profile)
    print(f"Adapter: {args.adapter}")
    print(f"Evidence: {result.evidence_source.value}")
    print(json.dumps(result.data, ensure_ascii=False, indent=2))
    return 0


def _cmd_bridge(args: argparse.Namespace) -> int:
    return run_bridge_lifecycle(
        args.bridge_action,
        bridge_python=args.bridge_python,
        profile=args.profile,
        env_file=args.env_file,
        verbose=args.verbose,
    )


def _cmd_resources(args: argparse.Namespace) -> int:
    pins = load_retention_pins(args.pin_manifest)
    local = audit_local_resources(
        args.artifact_root,
        older_than_days=args.older_than_days,
        pins=pins,
    )
    remote = None
    if args.remote:
        result = SubprocessBridgeAdapter(
            bridge_python=args.bridge_python
        ).audit_resources(
            args.pdk_profile,
            older_than_days=args.older_than_days,
        )
        remote = result.data
        for entry in remote.get("directories", []):
            reason = pins.get(str(entry.get("path") or ""))
            entry["pinned"] = reason is not None
            entry["pin_reason"] = reason
            entry["review_candidate"] = bool(entry["review_candidate"] and not reason)
            entry["delete_authorized"] = False
        remote["review_candidate_count"] = sum(
            item["review_candidate"] for item in remote.get("directories", [])
        )
    payload = {
        "mode": "read_only_dry_run",
        "deletion_performed": False,
        "pins": [
            {"path": path, "reason": reason}
            for path, reason in sorted(pins.items())
        ],
        "local": local,
        "remote": remote,
        "evidence_sources": {
            "local_inventory": "software_inference",
            "remote_inventory": "bridge_readback" if remote is not None else None,
            "retention_pins": "user_input" if args.pin_manifest else None,
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    if args.json:
        print(serialized)
    else:
        print("Mode: read-only dry-run (no deletion)")
        print(
            "Local evidence: "
            f"{local['entry_count']} entries, {local['total_size_bytes']} bytes, "
            f"{local['review_candidate_count']} review candidates"
        )
        print(
            "Local transient VDA resources: "
            f"{local['transient_entry_count']} entries, "
            f"{local['transient_total_size_bytes']} bytes "
            f"({local['transient_cancel_marker_count']} cancel markers)"
        )
        if remote is not None:
            print(
                "Remote evidence: "
                f"{remote['directory_count']} directories, "
                f"{remote['total_size_bytes']} bytes, "
                f"{remote['review_candidate_count']} review candidates"
            )
            print(
                "Remote EDA processes: "
                + json.dumps(remote["process_counts"], sort_keys=True)
            )
            print(
                "Remote Maestro sessions: "
                f"{len(remote['maestro_sessions'])} total, "
                f"{remote['vda_managed_maestro_session_count']} VDA-managed"
            )
            if remote.get("maestro_session_inventory_error"):
                print(
                    "Maestro inventory warning: "
                    f"{remote['maestro_session_inventory_error']}"
                )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vda",
        description="Bounded Virtuoso design and verification agent",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog", help="show circuit capability gates")
    catalog.add_argument("--json", action="store_true")
    catalog.set_defaults(handler=_cmd_catalog)

    plan = subparsers.add_parser("plan", help="compile a task without executing it")
    plan.add_argument("task", type=Path)
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(handler=_cmd_plan)

    theory = subparsers.add_parser(
        "theory",
        help="derive and rank one topology over a declared gm/Id domain locally",
    )
    theory.add_argument("request", type=Path)
    theory.add_argument("--output", type=Path)
    theory.set_defaults(handler=_cmd_theory)

    theory_calibrate = subparsers.add_parser(
        "theory-calibrate",
        help=(
            "fit and cross-check the Gate-6 one-pole model from bound real-EDA "
            "run records"
        ),
    )
    theory_calibrate.add_argument("training_run", type=Path)
    theory_calibrate.add_argument("--validation-run", type=Path, required=True)
    theory_calibrate.add_argument("--id", default="gate6-one-pole-calibration")
    theory_calibrate.add_argument("--output", type=Path)
    theory_calibrate.set_defaults(handler=_cmd_theory_calibrate)

    theory_seed_task = subparsers.add_parser(
        "theory-seed-task",
        help=(
            "compile a hash-bound theory result into atomic finite EDA candidates"
        ),
    )
    theory_seed_task.add_argument("policy", type=Path)
    theory_seed_task.add_argument("theory_result", type=Path)
    theory_seed_task.add_argument("task_template", type=Path)
    theory_seed_task.add_argument("--output", type=Path, required=True)
    theory_seed_task.set_defaults(handler=_cmd_theory_seed_task)

    theory_seed_validate = subparsers.add_parser(
        "theory-seed-validate",
        help=(
            "audit a hash-bound theory shortlist against its exhausted real EDA run"
        ),
    )
    theory_seed_validate.add_argument("policy", type=Path)
    theory_seed_validate.add_argument("task", type=Path)
    theory_seed_validate.add_argument("run_record", type=Path)
    theory_seed_validate.add_argument("--output", type=Path)
    theory_seed_validate.set_defaults(handler=_cmd_theory_seed_validate)

    cascode_seed = subparsers.add_parser(
        "cascode-seed",
        help=(
            "derive a bounded cascode width/bias neighborhood from one hash-bound "
            "real operating point"
        ),
    )
    cascode_seed.add_argument("policy", type=Path)
    cascode_seed.add_argument("source_run", type=Path)
    cascode_seed.add_argument("--output", type=Path)
    cascode_seed.set_defaults(handler=_cmd_cascode_seed)

    cascode_seed_task = subparsers.add_parser(
        "candidate-task-from-cascode-seed",
        help=(
            "compile a hash-bound cascode seed into an atomic DC or AC tuning task"
        ),
    )
    cascode_seed_task.add_argument("result", type=Path)
    cascode_seed_task.add_argument("task_template", type=Path)
    cascode_seed_task.add_argument("--output", type=Path, required=True)
    cascode_seed_task.set_defaults(handler=_cmd_candidate_task_from_cascode_seed)

    preview_candidate_task = subparsers.add_parser(
        "preview-task-from-candidates",
        help=(
            "compile an explicit atomic/theory candidate shortlist into typed "
            "standalone Spectre preview variants"
        ),
    )
    preview_candidate_task.add_argument("policy", type=Path)
    preview_candidate_task.add_argument("candidate_source", type=Path)
    preview_candidate_task.add_argument("task_template", type=Path)
    preview_candidate_task.add_argument("--output", type=Path, required=True)
    preview_candidate_task.set_defaults(handler=_cmd_preview_task_from_candidates)

    preview_oa_task = subparsers.add_parser(
        "oa-task-from-preview-shortlist",
        help=(
            "compile a passed hash-bound preview shortlist into a normal OA "
            "candidate task"
        ),
    )
    preview_oa_task.add_argument("selection", type=Path)
    preview_oa_task.add_argument("candidate_task", type=Path)
    preview_oa_task.add_argument("--id", required=True)
    preview_oa_task.add_argument("--output", type=Path, required=True)
    preview_oa_task.set_defaults(handler=_cmd_oa_task_from_preview_shortlist)

    preview_select = subparsers.add_parser(
        "preview-select",
        help=(
            "audit a hash-bound Spectre preview, shortlist candidates, and "
            "compare an exhausted real OA/si reference run"
        ),
    )
    preview_select.add_argument("policy", type=Path)
    preview_select.add_argument("preview_task", type=Path)
    preview_select.add_argument("preview_run", type=Path)
    preview_select.add_argument("reference_run", type=Path)
    preview_select.add_argument("--output", type=Path)
    preview_select.set_defaults(handler=_cmd_preview_select)

    preview_shortlist = subparsers.add_parser(
        "preview-shortlist",
        help=(
            "freeze a prospective preview shortlist without reading an OA "
            "reference run"
        ),
    )
    preview_shortlist.add_argument("policy", type=Path)
    preview_shortlist.add_argument("preview_task", type=Path)
    preview_shortlist.add_argument("preview_run", type=Path)
    preview_shortlist.add_argument("reference_task", type=Path)
    preview_shortlist.add_argument("--output", type=Path, required=True)
    preview_shortlist.set_defaults(handler=_cmd_preview_shortlist)

    preview_shortlist_audit = subparsers.add_parser(
        "preview-shortlist-audit",
        help=(
            "audit a frozen prospective shortlist against a later exhausted "
            "OA/si reference run"
        ),
    )
    preview_shortlist_audit.add_argument("policy", type=Path)
    preview_shortlist_audit.add_argument("shortlist", type=Path)
    preview_shortlist_audit.add_argument("preview_task", type=Path)
    preview_shortlist_audit.add_argument("preview_run", type=Path)
    preview_shortlist_audit.add_argument("reference_task", type=Path)
    preview_shortlist_audit.add_argument("reference_run", type=Path)
    preview_shortlist_audit.add_argument("--output", type=Path, required=True)
    preview_shortlist_audit.set_defaults(handler=_cmd_preview_shortlist_audit)

    theory_request = subparsers.add_parser(
        "theory-request-from-validation",
        help=(
            "derive a PDK-bound differential-pair theory request from a passed "
            "held-out validation and its exact characterization runs"
        ),
    )
    theory_request.add_argument("policy", type=Path)
    theory_request.add_argument("validation", type=Path)
    theory_request.add_argument("characterization_run", type=Path)
    theory_request.add_argument(
        "--additional-characterization-run",
        action="append",
        type=Path,
        default=[],
    )
    theory_request.add_argument("--output", type=Path, required=True)
    theory_request.set_defaults(handler=_cmd_theory_request_from_validation)

    op_relinearize = subparsers.add_parser(
        "op-relinearize",
        help=(
            "fit a local operating-point response model from a hash-bound real "
            "EDA run and validate explicit held-out candidates"
        ),
    )
    op_relinearize.add_argument("policy", type=Path)
    op_relinearize.add_argument("source_run", type=Path)
    op_relinearize.add_argument("--output", type=Path)
    op_relinearize.set_defaults(handler=_cmd_op_relinearize)

    candidate_task = subparsers.add_parser(
        "candidate-task-from-relinearization",
        help=(
            "compile a passed hash-bound local model into an atomic EDA "
            "candidate-set task"
        ),
    )
    candidate_task.add_argument("result", type=Path)
    candidate_task.add_argument("task_template", type=Path)
    candidate_task.add_argument("--output", type=Path, required=True)
    candidate_task.set_defaults(handler=_cmd_candidate_task_from_relinearization)

    op_relinearization_validate = subparsers.add_parser(
        "op-relinearization-validate",
        help=(
            "audit a compiled local response model against its exhausted real "
            "EDA candidate run"
        ),
    )
    op_relinearization_validate.add_argument("result", type=Path)
    op_relinearization_validate.add_argument("task", type=Path)
    op_relinearization_validate.add_argument("run_record", type=Path)
    op_relinearization_validate.add_argument(
        "--policy",
        type=Path,
        help=(
            "exact original policy required when a legacy result predates "
            "serialized relative-error floors"
        ),
    )
    op_relinearization_validate.add_argument("--output", type=Path)
    op_relinearization_validate.set_defaults(
        handler=_cmd_op_relinearization_validate
    )

    small_signal = subparsers.add_parser(
        "small-signal",
        help="solve a characterization-bound MOS/R/C network without a topology formula",
    )
    small_signal.add_argument("request", type=Path)
    small_signal.add_argument("--output", type=Path)
    small_signal.set_defaults(handler=_cmd_small_signal)

    small_signal_from_run = subparsers.add_parser(
        "small-signal-from-run",
        help=(
            "linearize one structured si graph from the same action's exact "
            "Spectre operating-point derivatives"
        ),
    )
    small_signal_from_run.add_argument("policy", type=Path)
    small_signal_from_run.add_argument("circuit_run", type=Path)
    small_signal_from_run.add_argument("--output", type=Path)
    small_signal_from_run.set_defaults(handler=_cmd_small_signal_from_run)

    small_signal_validate = subparsers.add_parser(
        "small-signal-validate",
        help=(
            "bind a real OA/si/DC/AC run to an independent MOS table and "
            "audit the held-out circuit error"
        ),
    )
    small_signal_validate.add_argument("policy", type=Path)
    small_signal_validate.add_argument("characterization_run", type=Path)
    small_signal_validate.add_argument("circuit_run", type=Path)
    small_signal_validate.add_argument(
        "--additional-characterization-run",
        action="append",
        type=Path,
        default=[],
        help=(
            "add an independently evidenced MOS width/model plane; repeat once "
            "for each additional si device signature"
        ),
    )
    small_signal_validate.add_argument("--output", type=Path)
    small_signal_validate.set_defaults(handler=_cmd_small_signal_validate)

    characterization_task = subparsers.add_parser(
        "characterization-task-from-run",
        help=(
            "derive a bounded standalone MOS characterization task from one "
            "structured real-si instance"
        ),
    )
    characterization_task.add_argument("template", type=Path)
    characterization_task.add_argument("circuit_run", type=Path)
    characterization_task.add_argument("--id", required=True)
    characterization_task.add_argument("--instance", required=True)
    characterization_task.add_argument(
        "--polarity",
        choices=("nmos", "pmos"),
        required=True,
    )
    characterization_task.add_argument(
        "--source-action",
        default="simulation.candidate.1",
    )
    characterization_task.add_argument("--operating-condition")
    characterization_task.add_argument("--output", type=Path, required=True)
    characterization_task.set_defaults(handler=_cmd_characterization_task_from_run)

    topology_compile = subparsers.add_parser(
        "topology-compile",
        help=(
            "compile explicit graph operations against one successful schematic "
            "inspection into an exact reversible contract"
        ),
    )
    topology_compile.add_argument("readback", type=Path)
    topology_compile.add_argument("operations", type=Path)
    topology_compile.add_argument("--id", required=True)
    topology_compile.add_argument("--output", type=Path, required=True)
    topology_compile.set_defaults(handler=_cmd_topology_compile)

    onboarding_draft = subparsers.add_parser(
        "onboarding-draft",
        help=(
            "compile exact read-only existing-schematic inspection evidence into "
            "a non-executable design-context and simulation draft"
        ),
    )
    onboarding_draft.add_argument("inspect_task", type=Path)
    onboarding_draft.add_argument("inspect_run", type=Path)
    onboarding_draft.add_argument("--id", required=True)
    onboarding_draft.add_argument(
        "--child-inspection",
        action="append",
        nargs=3,
        default=[],
        metavar=("TOP_INSTANCE", "INSPECT_TASK", "INSPECT_RUN"),
        help=(
            "bind one exact child schematic readback to a top instance; repeat for "
            "each unique one-level child"
        ),
    )
    onboarding_draft.add_argument("--output", type=Path, required=True)
    onboarding_draft.set_defaults(handler=_cmd_onboarding_draft)

    binding_discovery_task = subparsers.add_parser(
        "binding-discovery-task",
        help=(
            "compile fresh read-only inspection evidence and one explicit CDF "
            "probe intent into a non-executable binding-discovery TaskSpec"
        ),
    )
    binding_discovery_task.add_argument("inspect_task", type=Path)
    binding_discovery_task.add_argument("inspect_run", type=Path)
    binding_discovery_task.add_argument("intent", type=Path)
    binding_discovery_task.add_argument(
        "--child-inspection",
        action="append",
        nargs=3,
        default=[],
        metavar=("TOP_INSTANCE", "INSPECT_TASK", "INSPECT_RUN"),
        help=(
            "bind one exact child readback for a scoped probe; repeat for each "
            "one-level child named by the intent"
        ),
    )
    binding_discovery_task.add_argument("--output", type=Path, required=True)
    binding_discovery_task.add_argument(
        "--record-output",
        type=Path,
        required=True,
    )
    binding_discovery_task.set_defaults(handler=_cmd_binding_discovery_task)

    binding_reclassify = subparsers.add_parser(
        "binding-reclassify",
        help=(
            "re-evaluate preserved OA/si binding evidence locally without another "
            "remote probe"
        ),
    )
    binding_reclassify.add_argument("run_record", type=Path)
    binding_reclassify.add_argument("--output", type=Path)
    binding_reclassify.set_defaults(handler=_cmd_binding_reclassify)

    onboarding_resolve = subparsers.add_parser(
        "onboarding-resolve",
        help=(
            "compile a hash-bound onboarding draft plus confirmed intent into a "
            "normal safe existing-schematic TaskSpec"
        ),
    )
    onboarding_resolve.add_argument("draft", type=Path)
    onboarding_resolve.add_argument("resolution", type=Path)
    onboarding_resolve.add_argument("--output", type=Path, required=True)
    onboarding_resolve.set_defaults(handler=_cmd_onboarding_resolve)

    onboarding_resolve_bindings = subparsers.add_parser(
        "onboarding-resolve-bindings",
        help=(
            "compile locally revalidated OA-to-si discovery runs into a normal "
            "disabled onboarding TaskSpec"
        ),
    )
    onboarding_resolve_bindings.add_argument("draft", type=Path)
    onboarding_resolve_bindings.add_argument("resolution", type=Path)
    onboarding_resolve_bindings.add_argument(
        "--binding-source",
        action="append",
        nargs=2,
        required=True,
        metavar=("DISCOVERY_TASK", "DISCOVERY_RUN"),
        help=(
            "add one exact binding-discovery task/run pair; repeat for each "
            "authorized CDF field"
        ),
    )
    onboarding_resolve_bindings.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    onboarding_resolve_bindings.add_argument(
        "--record-output",
        type=Path,
        required=True,
    )
    onboarding_resolve_bindings.set_defaults(
        handler=_cmd_onboarding_resolve_bindings
    )

    execution_scope = subparsers.add_parser(
        "execution-scope",
        help=(
            "derive the minimum executable safety flags and a new confirmation "
            "token from one disabled TaskSpec without executing it"
        ),
    )
    execution_scope.add_argument("task", type=Path)
    execution_scope.add_argument("--output", type=Path, required=True)
    execution_scope.add_argument(
        "--record-output",
        type=Path,
        required=True,
    )
    execution_scope.set_defaults(handler=_cmd_execution_scope)

    onboarding_binding_audit = subparsers.add_parser(
        "onboarding-binding-audit",
        help=(
            "audit one promoted-binding shared-netlist Bridge run against its "
            "promotion record, execution scope, exact task, and manifests"
        ),
    )
    onboarding_binding_audit.add_argument("promotion", type=Path)
    onboarding_binding_audit.add_argument("execution_scope", type=Path)
    onboarding_binding_audit.add_argument("task", type=Path)
    onboarding_binding_audit.add_argument("run_record", type=Path)
    onboarding_binding_audit.add_argument("--output", type=Path, required=True)
    onboarding_binding_audit.set_defaults(handler=_cmd_onboarding_binding_audit)

    onboarding_promote = subparsers.add_parser(
        "onboarding-promote",
        help=(
            "bind a completed topology-refinement winner and fresh CDF readback "
            "into a normal fixed-topology tuning task"
        ),
    )
    onboarding_promote.add_argument("source_task", type=Path)
    onboarding_promote.add_argument("source_run", type=Path)
    onboarding_promote.add_argument("winner_inspect_task", type=Path)
    onboarding_promote.add_argument("winner_inspect_run", type=Path)
    onboarding_promote.add_argument("intent", type=Path)
    onboarding_promote.add_argument(
        "--child-inspection",
        action="append",
        nargs=3,
        default=[],
        metavar=("TOP_INSTANCE", "INSPECT_TASK", "INSPECT_RUN"),
        help=(
            "bind one fresh child readback for a retained one-level hierarchy; "
            "repeat for each unique child"
        ),
    )
    onboarding_promote.add_argument("--draft-output", type=Path, required=True)
    onboarding_promote.add_argument("--task-output", type=Path, required=True)
    onboarding_promote.add_argument("--record-output", type=Path, required=True)
    onboarding_promote.set_defaults(handler=_cmd_onboarding_promote)

    run = subparsers.add_parser("run", help="plan or execute a task")
    run.add_argument("task", type=Path)
    run.add_argument("--adapter", choices=("demo", "bridge"), default="demo")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--token")
    run.add_argument("--bridge-python")
    run.add_argument("--artifact-root", type=Path, default=Path("artifacts/runs"))
    run.add_argument("--output", type=Path)
    run.add_argument(
        "--checkpoint",
        type=Path,
        help="checkpoint tuning progress after each confirmed candidate boundary",
    )
    run.add_argument(
        "--resume",
        type=Path,
        help="resume an incomplete tuning checkpoint after probing and OA readback",
    )
    run.set_defaults(handler=_cmd_run)

    doctor = subparsers.add_parser("doctor", help="probe an adapter without writing OA")
    doctor.add_argument("--adapter", choices=("demo", "bridge"), default="bridge")
    doctor.add_argument("--bridge-python")
    doctor.add_argument("--pdk-profile", default=DEFAULT_PDK_PROFILE)
    doctor.set_defaults(handler=_cmd_doctor)

    bridge = subparsers.add_parser(
        "bridge",
        help="start, inspect, or stop Bridge without a separate console window",
    )
    bridge_actions = bridge.add_subparsers(dest="bridge_action", required=True)
    for action, help_text in (
        ("start", "start the Bridge tunnel in a hidden child process"),
        ("status", "show Bridge tunnel, daemon, and simulator status"),
        ("stop", "stop the Bridge tunnel"),
    ):
        bridge_action = bridge_actions.add_parser(action, help=help_text)
        bridge_action.add_argument("--bridge-python")
        bridge_action.add_argument(
            "-p",
            "--profile",
            help="Bridge connection profile (not a VDA PDK profile)",
        )
        bridge_action.add_argument(
            "--env",
            dest="env_file",
            type=Path,
            help="explicit Bridge .env path; file contents are never printed by VDA",
        )
        bridge_action.add_argument(
            "--verbose",
            action="store_true",
            help="also echo Bridge command diagnostics in the current console",
        )
        bridge_action.set_defaults(handler=_cmd_bridge)

    resources = subparsers.add_parser(
        "resources",
        help="inventory retained evidence and transient VDA resources without deletion",
    )
    resources.add_argument("--artifact-root", type=Path, default=Path("artifacts/runs"))
    resources.add_argument("--older-than-days", type=float, default=7.0)
    resources.add_argument("--pin-manifest", type=Path)
    resources.add_argument("--remote", action="store_true")
    resources.add_argument("--bridge-python")
    resources.add_argument("--pdk-profile", default=DEFAULT_PDK_PROFILE)
    resources.add_argument("--output", type=Path)
    resources.add_argument("--json", action="store_true")
    resources.set_defaults(handler=_cmd_resources)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.handler(args))
    except (
        ValidationError,
        ValueError,
        SafetyViolation,
        UnsupportedCapability,
        BridgeWorkerError,
        BridgeLifecycleError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
