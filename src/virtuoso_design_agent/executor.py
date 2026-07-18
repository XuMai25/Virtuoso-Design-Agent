"""Bounded execution loop shared by partial tasks and full L5A closure."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from .adapters.base import AdapterResult, DesignAdapter
from .metrics import evaluate_constraints
from .models import (
    ActionRecord,
    CandidateEvaluation,
    EvidenceSource,
    ExecutionPlan,
    ObjectiveGoal,
    Operation,
    RunRecord,
    RunStatus,
    TaskSpec,
)
from .safety import authorize_execution

T = TypeVar("T")


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
        except Exception as exc:
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
        return CandidateEvaluation(
            index=index,
            parameters=parameters,
            metrics=metrics,
            constraints=constraints,
            feasible=all(item.passed for item in constraints) and not objective_missing,
            total_violation=sum(item.normalized_violation for item in constraints)
            + (1_000_000.0 if objective_missing else 0.0),
            objective_value=objective_value,
            evidence_source=simulation.evidence_source,
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

    def _run_candidates(self, task: TaskSpec) -> list[CandidateEvaluation]:
        evaluations: list[CandidateEvaluation] = []
        for index, parameters in enumerate(self._candidates(task), start=1):
            try:
                result = self._action(
                    f"simulation.candidate.{index}",
                    lambda parameters=parameters: self.adapter.simulate(task, parameters),
                )
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
                    )
                )
                continue
            evaluations.append(
                self._evaluate_candidate(task, index, parameters, result)
            )
        return evaluations

    def _note_candidate_failures(
        self, status: RunStatus, notes: list[str]
    ) -> RunStatus:
        failures = sum(
            action.status == "failed" and action.action.startswith("simulation.candidate.")
            for action in self.actions
        )
        if failures:
            notes.append(
                f"{failures} candidate simulation(s) failed; selection used completed evidence only"
            )
            return RunStatus.PARTIAL
        return status

    def execute(
        self, task: TaskSpec, plan: ExecutionPlan, *, token: str
    ) -> RunRecord:
        authorize_execution(task, plan, token)
        started = datetime.now(UTC)
        status = RunStatus.SUCCEEDED
        notes: list[str] = []
        candidates: list[CandidateEvaluation] = []
        selected_parameters: dict[str, float] | None = None
        selected_metrics: dict[str, float] | None = None

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
            elif operation is Operation.PARAMETERS_APPLY:
                self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._action(
                    "parameters.apply",
                    lambda: self.adapter.apply_parameters(task, task.parameters),
                )
                self._action(
                    "schematic.inspect.after",
                    lambda: self.adapter.inspect_schematic(task),
                )
                selected_parameters = dict(task.parameters)
            elif operation is Operation.SIMULATION_RUN:
                candidates = self._run_candidates(task)
                status = self._note_candidate_failures(status, notes)
                if candidates:
                    selected = min(candidates, key=lambda item: self._rank(task, item))
                    selected_parameters = selected.parameters
                    selected_metrics = selected.metrics
                    if not selected.feasible:
                        status = RunStatus.PARTIAL
                        notes.append("simulation completed but one or more constraints failed")
            else:
                if operation is Operation.DESIGN_CLOSE_LOOP and task.create_if_missing:
                    self._action(
                        "schematic.ensure", lambda: self.adapter.create_schematic(task)
                    )
                else:
                    self._action(
                        "schematic.inspect.before",
                        lambda: self.adapter.inspect_schematic(task),
                    )

                candidates = self._run_candidates(task)
                status = self._note_candidate_failures(status, notes)
                feasible = [candidate for candidate in candidates if candidate.feasible]
                if not feasible:
                    status = RunStatus.PARTIAL
                    notes.append(
                        "no feasible candidate; no parameters were written back to OA"
                    )
                    if candidates:
                        best_attempt = min(
                            candidates, key=lambda item: self._rank(task, item)
                        )
                        selected_parameters = best_attempt.parameters
                        selected_metrics = best_attempt.metrics
                else:
                    selected = min(feasible, key=lambda item: self._rank(task, item))
                    selected_parameters = selected.parameters
                    selected_metrics = selected.metrics
                    self._action(
                        "parameters.apply.best",
                        lambda: self.adapter.apply_parameters(
                            task, selected.parameters
                        ),
                    )
                    self._action(
                        "schematic.inspect.after",
                        lambda: self.adapter.inspect_schematic(task),
                    )
        except Exception as exc:
            status = RunStatus.FAILED
            notes.append(f"{type(exc).__name__}: {exc}")

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
