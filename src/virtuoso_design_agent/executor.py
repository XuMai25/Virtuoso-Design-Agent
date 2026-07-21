"""Bounded execution loop shared by partial tasks and full L5A closure."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from .adapters.base import AdapterInterrupted, AdapterResult, DesignAdapter
from .catalog import task_requests_oa_parameter_write
from .metrics import evaluate_constraints
from .models import (
    ActionRecord,
    CandidateEvaluation,
    CircuitKind,
    EvidenceSource,
    ExecutionCheckpoint,
    ExecutionPlan,
    ObjectiveGoal,
    Operation,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from .safety import authorize_execution

T = TypeVar("T")

_OA_SEMANTIC_PARAMETERS = {
    CircuitKind.INVERTER: ("nmos_width_um", "pmos_width_um", "length_um"),
    CircuitKind.COMMON_SOURCE: (
        "device_width_um",
        "length_um",
        "load_resistance_ohm",
    ),
}

_COMMON_SOURCE_OPTIONAL_OA_PARAMETERS = ("source_resistance_ohm",)


class TaskExecutor:
    def __init__(self, adapter: DesignAdapter) -> None:
        self.adapter = adapter
        self.actions: list[ActionRecord] = []

    def _action(
        self, name: str, operation: Callable[[], AdapterResult]
    ) -> AdapterResult:
        started = datetime.now(UTC)
        try:
            result = operation()
        except (Exception, KeyboardInterrupt) as exc:
            finished = datetime.now(UTC)
            self.actions.append(
                ActionRecord(
                    action=name,
                    status="failed",
                    started_at=started,
                    finished_at=finished,
                    evidence_source=EvidenceSource.SYSTEM_EVENT,
                    details={"error": f"{type(exc).__name__}: {exc}"},
                )
            )
            raise
        finished = datetime.now(UTC)
        self.actions.append(
            ActionRecord(
                action=name,
                status="succeeded",
                started_at=started,
                finished_at=finished,
                evidence_source=result.evidence_source,
                details=result.data,
            )
        )
        return result

    @staticmethod
    def _candidates(task: TaskSpec) -> list[dict[str, float]]:
        if not task.parameter_space:
            return [dict(task.parameters)]
        names = sorted(task.parameter_space)
        candidates: list[dict[str, float]] = []
        for values in itertools.product(*(task.parameter_space[name] for name in names)):
            parameters = dict(task.parameters)
            parameters.update(dict(zip(names, values, strict=True)))
            candidates.append(parameters)
            if len(candidates) >= task.limits.max_iterations:
                break
        return candidates

    @staticmethod
    def _evaluate_candidate(
        task: TaskSpec,
        index: int,
        parameters: dict[str, float],
        simulation: AdapterResult,
    ) -> CandidateEvaluation:
        metrics = {
            str(name): float(value)
            for name, value in simulation.data.get("metrics", {}).items()
        }
        constraints = evaluate_constraints(metrics, task.constraints)
        objective_value = (
            metrics.get(task.objective.metric) if task.objective is not None else None
        )
        objective_missing = task.objective is not None and objective_value is None
        analysis_complete = bool(simulation.data.get("analysis_complete", True))
        analysis_issues = [
            str(value) for value in simulation.data.get("analysis_issues", [])
        ]
        analysis_warnings = [
            str(value) for value in simulation.data.get("analysis_warnings", [])
        ]
        if not analysis_complete and not analysis_issues:
            analysis_issues = ["analysis did not produce its required core metrics"]
        raw_sources = simulation.data.get("metric_sources", {})
        metric_sources = {
            name: EvidenceSource(raw_sources.get(name, simulation.evidence_source))
            for name in metrics
        }
        evaluated_parameters = {
            str(name): float(value)
            for name, value in simulation.data.get("parameters", parameters).items()
        }
        return CandidateEvaluation(
            index=index,
            parameters=evaluated_parameters,
            metrics=metrics,
            constraints=constraints,
            feasible=(
                all(item.passed for item in constraints)
                and not objective_missing
                and analysis_complete
            ),
            total_violation=sum(item.normalized_violation for item in constraints)
            + (1_000_000.0 if objective_missing else 0.0)
            + (1_000_000.0 if not analysis_complete else 0.0),
            objective_value=objective_value,
            evidence_source=simulation.evidence_source,
            metric_sources=metric_sources,
            analysis_complete=analysis_complete,
            analysis_issues=analysis_issues,
            analysis_warnings=analysis_warnings,
        )

    @staticmethod
    def _rank(task: TaskSpec, candidate: CandidateEvaluation) -> tuple[float, ...]:
        feasibility = 0.0 if candidate.feasible else 1.0
        if task.objective is None or candidate.objective_value is None:
            objective = 0.0
        elif task.objective.goal is ObjectiveGoal.MINIMIZE:
            objective = candidate.objective_value
        else:
            objective = -candidate.objective_value
        return feasibility, candidate.total_violation, objective, float(candidate.index)

    @staticmethod
    def _semantic_parameters(
        result: AdapterResult, task: TaskSpec
    ) -> dict[str, float]:
        raw = result.data.get("semantic_parameters")
        required = _OA_SEMANTIC_PARAMETERS[task.circuit]
        if not isinstance(raw, dict) or any(name not in raw for name in required):
            raise RuntimeError(
                "schematic inspection did not return canonical semantic parameters"
            )
        names = list(required)
        if task.circuit is CircuitKind.COMMON_SOURCE:
            names.extend(
                name
                for name in _COMMON_SOURCE_OPTIONAL_OA_PARAMETERS
                if name in raw
            )
        return {name: float(raw[name]) for name in names}

    @staticmethod
    def _instances_by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = data.get("instances")
        if not isinstance(raw, list):
            raise RuntimeError("schematic inspection did not return structured instances")
        instances: dict[str, dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise RuntimeError("schematic inspection returned an invalid instance")
            name = str(item["name"])
            if name in instances:
                raise RuntimeError(f"schematic inspection repeated instance {name}")
            instances[name] = item
        return instances

    @classmethod
    def _assert_source_degeneration_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        source_resistance_ohm: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        base_names = {"MN0", "RD0"}
        degenerated_names = base_names | {"RS0"}
        before_names = set(before_instances)
        if before_names != base_names and before_names != degenerated_names:
            raise RuntimeError(
                "source-degeneration transform requires the exact VDA common-source "
                f"instance set; got {sorted(before_names)}"
            )
        if set(after_instances) != degenerated_names:
            raise RuntimeError(
                "source-degeneration transform did not produce exactly MN0/RD0/RS0"
            )

        before_mn_terms = before_instances["MN0"].get("terminals")
        expected_before_mn_terms = {
            "D": "OUT",
            "G": "IN",
            "S": "VSS" if before_names == base_names else "NSRC",
            "B": "VSS",
        }
        if before_mn_terms != expected_before_mn_terms:
            raise RuntimeError(
                "source-degeneration transform found an unexpected MN0 topology"
            )
        if before_instances["RD0"].get("terminals") != {
            "PLUS": "VDD",
            "MINUS": "OUT",
        }:
            raise RuntimeError(
                "source-degeneration transform found an unexpected RD0 topology"
            )
        if after_instances["MN0"].get("terminals") != {
            "D": "OUT",
            "G": "IN",
            "S": "NSRC",
            "B": "VSS",
        } or after_instances["RD0"].get("terminals") != {
            "PLUS": "VDD",
            "MINUS": "OUT",
        }:
            raise RuntimeError(
                "source-degeneration transform changed the common-source core topology"
            )
        if after_instances["RS0"].get("terminals") != {
            "PLUS": "NSRC",
            "MINUS": "VSS",
        }:
            raise RuntimeError("RS0 is not connected between NSRC and VSS")

        for field in ("pins",):
            if before_data.get(field) != after_data.get(field):
                raise RuntimeError(
                    f"source-degeneration transform unexpectedly changed {field}"
                )
        before_nets = set(before_data.get("nets", []))
        after_nets = set(after_data.get("nets", []))
        if after_nets != before_nets | {"NSRC"}:
            raise RuntimeError(
                "source-degeneration transform changed nets beyond adding NSRC"
            )

        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "source-degeneration transform is missing full instance parameter readback"
            )
        for name in sorted(base_names):
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    f"source-degeneration transform unexpectedly changed {name} parameters"
                )

        immutable_fields = (
            "library",
            "cell",
            "xy",
            "orient",
            "bBox",
            "numInst",
            "view",
            "parameters",
        )
        for name in sorted(base_names):
            for field in immutable_fields:
                if before_instances[name].get(field) != after_instances[name].get(field):
                    raise RuntimeError(
                        "source-degeneration transform unexpectedly changed "
                        f"{name}.{field}"
                    )

        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict) or "source_resistance_ohm" not in semantic:
            raise RuntimeError("RS0 resistance is missing from semantic OA readback")
        actual = float(semantic["source_resistance_ohm"])
        tolerance = max(abs(float(source_resistance_ohm)) * 1e-6, 1e-9)
        if abs(actual - float(source_resistance_ohm)) > tolerance:
            raise RuntimeError(
                "RS0 resistance does not match the requested source degeneration"
            )

    @classmethod
    def _applied_semantic_parameters(
        cls, result: AdapterResult, task: TaskSpec
    ) -> dict[str, float]:
        data = result.data
        if "semantic_parameters" in data:
            return cls._semantic_parameters(result, task)
        readback = data.get("readback")
        if not isinstance(readback, dict):
            raise RuntimeError("parameter write did not return structured OA readback")
        return cls._semantic_parameters(
            AdapterResult(data=readback, evidence_source=result.evidence_source),
            task,
        )

    @staticmethod
    def _same_parameters(
        expected: dict[str, float], actual: dict[str, float]
    ) -> bool:
        if expected.keys() != actual.keys():
            return False
        return all(
            abs(float(expected[name]) - float(actual[name]))
            <= max(abs(float(expected[name])) * 1e-6, 1e-9)
            for name in expected
        )

    @staticmethod
    def _checkpoint_candidate_matches(
        declared: dict[str, float],
        actual: dict[str, float],
        initial_oa: dict[str, float],
    ) -> bool:
        """Match declared task inputs plus only canonical OA-derived extras."""
        if not declared.keys() <= actual.keys():
            return False
        if not actual.keys() <= declared.keys() | initial_oa.keys():
            return False
        expected = dict(initial_oa)
        expected.update(declared)
        return all(
            abs(float(actual[name]) - float(expected[name]))
            <= max(abs(float(expected[name])) * 1e-6, 1e-9)
            for name in actual
        )

    @staticmethod
    def _requested_instance_parameters(
        task: TaskSpec,
    ) -> dict[str, dict[str, str]]:
        return {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }

    @staticmethod
    def _confirmed_instance_parameters(
        data: dict[str, Any], field: str
    ) -> dict[str, dict[str, str]]:
        raw = data.get(field)
        if not isinstance(raw, dict):
            raise RuntimeError(
                f"parameter write did not return structured {field}"
            )
        confirmed: dict[str, dict[str, str]] = {}
        for instance, parameters in raw.items():
            if not isinstance(instance, str) or not isinstance(parameters, dict):
                raise RuntimeError(f"invalid structured {field}")
            confirmed[instance] = {
                str(name): str(value) for name, value in parameters.items()
            }
        return confirmed

    @staticmethod
    def _assert_explicit_parameter_confirmation(
        requested: dict[str, dict[str, str]],
        confirmed: dict[str, dict[str, str]],
    ) -> None:
        for instance, parameters in requested.items():
            if instance not in confirmed:
                raise RuntimeError(
                    f"parameter confirmation is missing instance {instance}"
                )
            for name, value in parameters.items():
                if confirmed[instance].get(name) != value:
                    raise RuntimeError(
                        "parameter confirmation mismatch for "
                        f"{instance}.{name}: requested={value!r}, "
                        f"readback={confirmed[instance].get(name)!r}"
                    )

    @classmethod
    def _validate_checkpoint(
        cls,
        checkpoint: ExecutionCheckpoint,
        task: TaskSpec,
        plan: ExecutionPlan,
        adapter_name: str,
    ) -> None:
        if checkpoint.complete:
            raise ValueError("checkpoint is already complete")
        if checkpoint.task_id != task.id:
            raise ValueError("checkpoint task_id does not match the requested task")
        if checkpoint.plan_token != plan.confirmation_token:
            raise ValueError("checkpoint plan token does not match the current task plan")
        if checkpoint.adapter != adapter_name:
            raise ValueError("checkpoint adapter does not match the selected adapter")

        declared = cls._candidates(task)
        if checkpoint.next_candidate_index > len(declared) + 1:
            raise ValueError("checkpoint next candidate is outside the task search space")
        expected_indexes = list(range(1, checkpoint.next_candidate_index))
        if [candidate.index for candidate in checkpoint.candidates] != expected_indexes:
            raise ValueError("checkpoint candidates are not a completed search prefix")
        for candidate in checkpoint.candidates:
            if not cls._checkpoint_candidate_matches(
                declared[candidate.index - 1],
                candidate.parameters,
                checkpoint.initial_parameters,
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} parameters do not match task"
                )

    @classmethod
    def _validate_resume_oa_state(
        cls,
        checkpoint: ExecutionCheckpoint,
        task: TaskSpec,
        actual: dict[str, float],
    ) -> None:
        allowed = [
            checkpoint.initial_parameters,
            checkpoint.expected_oa_parameters,
        ]
        if checkpoint.pending_oa_parameters is not None:
            allowed.append(checkpoint.pending_oa_parameters)
        for parameters in cls._candidates(task):
            allowed.append(
                {
                    name: float(parameters.get(name, checkpoint.initial_parameters[name]))
                    for name in checkpoint.initial_parameters
                }
            )
        if not any(cls._same_parameters(expected, actual) for expected in allowed):
            raise RuntimeError(
                "current OA parameters do not match the checkpoint baseline, last "
                "confirmed write, or pending write; refusing automatic resume"
            )

    @staticmethod
    def _candidate_space_size(task: TaskSpec) -> int:
        if not task.parameter_space:
            return 1
        return math.prod(len(values) for values in task.parameter_space.values())

    def _run_candidates(
        self,
        task: TaskSpec,
        *,
        stage_parameters: bool = False,
        evaluations: list[CandidateEvaluation] | None = None,
        start_index: int = 1,
        progress: Callable[
            [str, int, dict[str, float], list[CandidateEvaluation], dict[str, float] | None],
            None,
        ]
        | None = None,
    ) -> list[CandidateEvaluation]:
        evaluations = evaluations if evaluations is not None else []
        for index, parameters in enumerate(self._candidates(task), start=1):
            if index < start_index:
                continue
            if progress is not None:
                progress("started", index, parameters, evaluations, None)
            try:
                if stage_parameters:
                    staged = self._action(
                        f"parameters.stage.{index}",
                        lambda parameters=parameters: self.adapter.apply_parameters(
                            task, parameters
                        ),
                    )
                    if progress is not None:
                        progress(
                            "staged",
                            index,
                            parameters,
                            evaluations,
                            self._applied_semantic_parameters(staged, task),
                        )
                result = self._action(
                    f"simulation.candidate.{index}",
                    lambda parameters=parameters: self.adapter.simulate(task, parameters),
                )
            except AdapterInterrupted:
                if progress is not None:
                    progress("interrupted", index, parameters, evaluations, None)
                raise
            except Exception:
                constraints = evaluate_constraints({}, task.constraints)
                evaluations.append(
                    CandidateEvaluation(
                        index=index,
                        parameters=parameters,
                        metrics={},
                        constraints=constraints,
                        feasible=False,
                        total_violation=sum(
                            item.normalized_violation for item in constraints
                        ),
                        evidence_source=EvidenceSource.SYSTEM_EVENT,
                        metric_sources={},
                    )
                )
                if progress is not None:
                    progress("completed", index, parameters, evaluations, None)
                continue
            evaluations.append(
                self._evaluate_candidate(task, index, parameters, result)
            )
            if progress is not None:
                progress("completed", index, parameters, evaluations, None)
        return evaluations

    def _note_candidate_failures(
        self,
        candidates: list[CandidateEvaluation],
        status: RunStatus,
        notes: list[str],
    ) -> RunStatus:
        failures = sum(
            candidate.evidence_source is EvidenceSource.SYSTEM_EVENT
            for candidate in candidates
        )
        if failures:
            notes.append(
                f"{failures} candidate evaluation(s) failed during staging or simulation; "
                "selection used completed evidence only"
            )
            status = RunStatus.PARTIAL
        incomplete = sum(
            not candidate.analysis_complete
            and candidate.evidence_source is not EvidenceSource.SYSTEM_EVENT
            for candidate in candidates
        )
        if incomplete:
            notes.append(
                f"{incomplete} candidate analysis result(s) lacked required core metrics; "
                "selection used analysis-complete evidence only"
            )
            status = RunStatus.PARTIAL
        return status

    def _note_budget_exhaustion(
        self, task: TaskSpec, status: RunStatus, notes: list[str]
    ) -> RunStatus:
        total = self._candidate_space_size(task)
        evaluated = min(total, task.limits.max_iterations)
        if evaluated < total:
            note = (
                f"search budget exhausted after {evaluated} of {total} declared candidates; "
                "selection is only best within the evaluated prefix"
            )
            if note not in notes:
                notes.append(note)
            return RunStatus.PARTIAL
        return status

    def execute(
        self,
        task: TaskSpec,
        plan: ExecutionPlan,
        *,
        token: str,
        checkpoint_path: Path | None = None,
        resume_checkpoint: ExecutionCheckpoint | None = None,
    ) -> RunRecord:
        authorize_execution(task, plan, token)
        tuning = task.operation in {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}
        candidate_oa_write = tuning and task_requests_oa_parameter_write(task)
        if resume_checkpoint is not None:
            if not tuning:
                raise ValueError("only tuning operations can resume from a checkpoint")
            if checkpoint_path is None:
                raise ValueError("resuming requires a checkpoint output path")
            self._validate_checkpoint(
                resume_checkpoint, task, plan, self.adapter.name
            )

        self.actions = (
            list(resume_checkpoint.actions) if resume_checkpoint is not None else []
        )
        started = (
            resume_checkpoint.started_at
            if resume_checkpoint is not None
            else datetime.now(UTC)
        )
        status = RunStatus.SUCCEEDED
        notes = list(resume_checkpoint.notes) if resume_checkpoint is not None else []
        candidates = (
            list(resume_checkpoint.candidates)
            if resume_checkpoint is not None
            else []
        )
        selected_parameters: dict[str, float] | None = None
        selected_metrics: dict[str, float] | None = None
        initial_parameters = (
            dict(resume_checkpoint.initial_parameters)
            if resume_checkpoint is not None
            else {}
        )
        expected_oa_parameters = (
            dict(resume_checkpoint.expected_oa_parameters)
            if resume_checkpoint is not None
            else {}
        )
        pending_oa_parameters = (
            dict(resume_checkpoint.pending_oa_parameters)
            if resume_checkpoint is not None
            and resume_checkpoint.pending_oa_parameters is not None
            else None
        )
        next_candidate_index = (
            resume_checkpoint.next_candidate_index
            if resume_checkpoint is not None
            else 1
        )

        def persist_checkpoint(*, complete: bool = False) -> None:
            if checkpoint_path is None or not tuning or not initial_parameters:
                return
            save_execution_checkpoint(
                ExecutionCheckpoint(
                    task_id=task.id,
                    plan_token=plan.confirmation_token,
                    adapter=self.adapter.name,
                    started_at=started,
                    initial_parameters=initial_parameters,
                    expected_oa_parameters=expected_oa_parameters,
                    pending_oa_parameters=pending_oa_parameters,
                    next_candidate_index=next_candidate_index,
                    actions=self.actions,
                    candidates=candidates,
                    notes=notes,
                    complete=complete,
                ),
                checkpoint_path,
            )

        def semantic_candidate(parameters: dict[str, float]) -> dict[str, float]:
            return {
                name: float(parameters.get(name, initial_parameters[name]))
                for name in initial_parameters
            }

        def candidate_progress(
            event: str,
            index: int,
            parameters: dict[str, float],
            _evaluations: list[CandidateEvaluation],
            readback: dict[str, float] | None,
        ) -> None:
            nonlocal expected_oa_parameters
            nonlocal next_candidate_index
            nonlocal pending_oa_parameters
            if event == "started":
                pending_oa_parameters = (
                    semantic_candidate(parameters) if candidate_oa_write else None
                )
            elif event == "staged":
                if readback is None:
                    raise RuntimeError("candidate stage did not return OA readback")
                expected_oa_parameters = readback
                pending_oa_parameters = None
            elif event == "completed":
                next_candidate_index = index + 1
                pending_oa_parameters = None
            persist_checkpoint()

        def apply_with_checkpoint(
            action: str, parameters: dict[str, float]
        ) -> dict[str, float]:
            nonlocal expected_oa_parameters
            nonlocal pending_oa_parameters
            pending_oa_parameters = semantic_candidate(parameters)
            persist_checkpoint()
            result = self._action(
                action,
                lambda: self.adapter.apply_parameters(task, parameters),
            )
            expected_oa_parameters = self._applied_semantic_parameters(result, task)
            pending_oa_parameters = None
            persist_checkpoint()
            return expected_oa_parameters

        try:
            self._action(
                "bridge.probe", lambda: self.adapter.probe(task.pdk_profile)
            )
            operation = task.operation

            if operation is Operation.SCHEMATIC_CREATE:
                self._action(
                    "schematic.create", lambda: self.adapter.create_schematic(task)
                )
                self._action(
                    "schematic.inspect", lambda: self.adapter.inspect_schematic(task)
                )
            elif operation is Operation.SCHEMATIC_INSPECT:
                self._action(
                    "schematic.inspect", lambda: self.adapter.inspect_schematic(task)
                )
            elif operation is Operation.SCHEMATIC_TRANSFORM:
                before = self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._action(
                    "schematic.transform.source-degeneration",
                    lambda: self.adapter.transform_schematic(task),
                )
                after = self._action(
                    "schematic.inspect.after",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._assert_source_degeneration_delta(
                    before,
                    after,
                    float(task.parameters["source_resistance_ohm"]),
                )
                selected_parameters = dict(task.parameters)
            elif operation is Operation.PARAMETERS_APPLY:
                self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                applied = self._action(
                    "parameters.apply",
                    lambda: self.adapter.apply_parameters(task, task.parameters),
                )
                if task.instance_parameter_updates:
                    requested = self._requested_instance_parameters(task)
                    echoed_request = self._confirmed_instance_parameters(
                        applied.data, "requested_instance_parameters"
                    )
                    self._assert_explicit_parameter_confirmation(
                        requested, echoed_request
                    )
                    expected = self._confirmed_instance_parameters(
                        applied.data, "applied_instance_parameters"
                    )
                    confirmed = self._confirmed_instance_parameters(
                        applied.data, "confirmed_instance_parameters"
                    )
                    self._assert_explicit_parameter_confirmation(
                        expected, confirmed
                    )

                    def inspect_confirmed_parameters() -> AdapterResult:
                        result = self.adapter.verify_parameters(task, expected)
                        final = self._confirmed_instance_parameters(
                            result.data, "confirmed_instance_parameters"
                        )
                        self._assert_explicit_parameter_confirmation(
                            expected, final
                        )
                        return result

                    self._action(
                        "schematic.inspect.after", inspect_confirmed_parameters
                    )
                    if task.parameters:
                        selected_parameters = dict(task.parameters)
                else:
                    self._action(
                        "schematic.inspect.after",
                        lambda: self.adapter.inspect_schematic(task),
                    )
                    selected_parameters = dict(task.parameters)
            elif operation is Operation.ADE_PREPARE:
                prepared = self._action(
                    "ade.prepare", lambda: self.adapter.prepare_ade(task)
                )
                if (
                    prepared.data.get("persistent_view_confirmed") is not True
                    or prepared.data.get("existing_maestro_overwritten") is not False
                    or prepared.data.get("schematic_oa_write_performed") is not False
                    or prepared.data.get("maestro_oa_write_performed") is not True
                ):
                    raise RuntimeError(
                        "ADE prepare did not prove a new persistent Maestro-only write "
                        "with existing manual and schematic state preserved"
                    )
                notes.append(
                    "prepared a new persistent Spectre-backed Maestro test for manual "
                    "editing; no analysis, sweep, simulation, or schematic write was "
                    "performed"
                )
            elif operation is Operation.ADE_CAPTURE:
                captured = self._action(
                    "ade.capture", lambda: self.adapter.capture_ade(task)
                )
                notes.append(
                    "captured an existing human-operated ADE setup/history; no "
                    "simulation or OA write was performed by VDA"
                )
                if not captured.data.get("structured_results_available", False):
                    notes.append(
                        "ADE result artifacts were retained, but no structured "
                        "output/spec table was available"
                    )
            elif operation is Operation.ADE_RUN:
                ran = self._action("ade.run", lambda: self.adapter.run_ade(task))
                if (
                    ran.evidence_source is not EvidenceSource.EDA_RESULT
                    or ran.data.get("setup_evidence_source") != "bridge_readback"
                    or ran.data.get("automated_simulation_performed") is not True
                    or ran.data.get("oa_write_performed") is not False
                    or ran.data.get("maestro_setup_write_performed") is not False
                    or ran.data.get("session_mode") != "background"
                    or not ran.data.get("history")
                ):
                    raise RuntimeError(
                        "ADE run did not prove an exact background history without "
                        "OA or Maestro setup writes"
                    )
                structured = bool(
                    ran.data.get("structured_results_available", False)
                )
                if structured and (
                    ran.data.get("structured_results_evidence_source")
                    != "eda_result"
                ):
                    raise RuntimeError(
                        "ADE run did not label structured output/spec values as "
                        "eda_result"
                    )
                if task.ade_run is not None and (
                    task.ade_run.require_structured_outputs and not structured
                ):
                    raise RuntimeError(
                        "ADE run required structured output/spec results but the "
                        "adapter did not provide them"
                    )
                notes.append(
                    "executed the saved Maestro setup in a background session; no "
                    "GUI focus, setup save, or OA write was performed"
                )
                notes.append(
                    "ADE output/spec values are real EDA results but are not mapped "
                    "to VDA constraints or netlist/PSF artifact hashes by ade.run"
                )
                notes.append(
                    "history naming and overwrite behavior came from the saved "
                    "Maestro setup; VDA did not change it or prove history uniqueness"
                )
                if not structured:
                    status = RunStatus.PARTIAL
                    notes.append(
                        "Maestro history completed without a structured point/output "
                        "table; completion alone was not treated as design success"
                    )
            elif operation is Operation.SIMULATION_RUN:
                self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                candidates = self._run_candidates(task)
                status = self._note_candidate_failures(candidates, status, notes)
                if candidates:
                    selected = min(candidates, key=lambda item: self._rank(task, item))
                    selected_parameters = selected.parameters
                    selected_metrics = selected.metrics
                    if selected.evidence_source is EvidenceSource.SYSTEM_EVENT:
                        status = RunStatus.FAILED
                        notes.append("simulation produced no completed EDA result")
                    elif not selected.feasible:
                        status = RunStatus.PARTIAL
                        if selected.analysis_issues:
                            notes.append(
                                "simulation completed but required analysis metrics were "
                                "incomplete: " + "; ".join(selected.analysis_issues)
                            )
                        else:
                            notes.append(
                                "simulation completed but one or more constraints failed"
                            )
            else:
                if operation is Operation.DESIGN_CLOSE_LOOP and task.create_if_missing:
                    self._action(
                        "schematic.ensure", lambda: self.adapter.create_schematic(task)
                    )
                before = self._action(
                    "schematic.inspect.resume"
                    if resume_checkpoint is not None
                    else "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                current_parameters = self._semantic_parameters(before, task)
                if resume_checkpoint is None:
                    initial_parameters = current_parameters
                    expected_oa_parameters = current_parameters
                    persist_checkpoint()
                else:
                    self._validate_resume_oa_state(
                        resume_checkpoint, task, current_parameters
                    )
                    expected_oa_parameters = current_parameters
                    pending_oa_parameters = None
                    notes.append(
                        "resumed candidate search at index "
                        f"{next_candidate_index} after independent OA readback"
                    )
                    persist_checkpoint()

                parameters_finalized = False
                try:
                    candidates = self._run_candidates(
                        task,
                        stage_parameters=candidate_oa_write,
                        evaluations=candidates,
                        start_index=next_candidate_index,
                        progress=candidate_progress,
                    )
                    status = self._note_candidate_failures(candidates, status, notes)
                    status = self._note_budget_exhaustion(task, status, notes)
                    feasible = [
                        candidate for candidate in candidates if candidate.feasible
                    ]
                    if not feasible:
                        status = RunStatus.PARTIAL
                        if candidate_oa_write:
                            apply_with_checkpoint(
                                "parameters.restore", initial_parameters
                            )
                            notes.append(
                                "no feasible candidate was committed; initial OA "
                                "parameters were restored"
                            )
                        else:
                            expected_oa_parameters = current_parameters
                            notes.append(
                                "no feasible testbench condition was selected; OA "
                                "parameters remained unchanged"
                            )
                        parameters_finalized = True
                    else:
                        selected = min(feasible, key=lambda item: self._rank(task, item))
                        selected_parameters = selected.parameters
                        selected_metrics = selected.metrics
                        if candidate_oa_write:
                            apply_with_checkpoint(
                                "parameters.apply.best", selected.parameters
                            )
                        else:
                            expected_oa_parameters = current_parameters
                            notes.append(
                                "selected the best testbench condition without changing "
                                "OA parameters"
                            )
                        parameters_finalized = True
                    after = self._action(
                        "schematic.inspect.after",
                        lambda: self.adapter.inspect_schematic(task),
                    )
                    final_parameters = self._semantic_parameters(after, task)
                    if not self._same_parameters(
                        expected_oa_parameters, final_parameters
                    ):
                        raise RuntimeError(
                            "final OA readback does not match the confirmed parameter write"
                        )
                    expected_oa_parameters = final_parameters
                    persist_checkpoint(complete=True)
                except BaseException:
                    persist_checkpoint()
                    if not parameters_finalized and candidate_oa_write:
                        try:
                            apply_with_checkpoint(
                                "parameters.restore.interrupted", initial_parameters
                            )
                        except Exception as restore_exc:
                            notes.append(
                                "automatic parameter restoration failed; OA state was "
                                "unverified at interruption and requires readback before "
                                f"resume: {type(restore_exc).__name__}: {restore_exc}"
                            )
                            persist_checkpoint()
                    raise
        except Exception as exc:
            status = RunStatus.FAILED
            notes.append(f"{type(exc).__name__}: {exc}")
            persist_checkpoint()

        finished = datetime.now(UTC)
        return RunRecord(
            task_id=task.id,
            plan_token=plan.confirmation_token,
            adapter=self.adapter.name,
            status=status,
            started_at=started,
            finished_at=finished,
            actions=self.actions,
            candidates=candidates,
            selected_parameters=selected_parameters,
            selected_metrics=selected_metrics,
            notes=notes,
        )


def save_run_record(record: RunRecord, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def save_execution_checkpoint(
    checkpoint: ExecutionCheckpoint, path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_execution_checkpoint(path: Path) -> ExecutionCheckpoint:
    return ExecutionCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
