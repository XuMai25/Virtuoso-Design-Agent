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
from .catalog import UnsupportedCapability, catalog_as_dicts
from .executor import (
    TaskExecutor,
    load_execution_checkpoint,
    save_run_record,
)
from .models import ExecutionPlan, Operation, RunStatus, TaskSpec
from .planner import build_plan
from .safety import SafetyViolation


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
            print("  explicit instance parameters: parameters.apply + OA readback")
        print(f"  evidence gate: {item['evidence_gate']}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    task = _load_task(args.task)
    _print_plan(build_plan(task), as_json=args.json)
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
    if record.selected_metrics:
        print(
            "Selected metrics: "
            + json.dumps(record.selected_metrics, ensure_ascii=False, sort_keys=True)
        )
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
    doctor.add_argument("--pdk-profile", default="nics4304_tsmc28")
    doctor.set_defaults(handler=_cmd_doctor)
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
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
