"""Bounded execution loop shared by partial tasks and full L5A closure."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from .adapters.base import AdapterInterrupted, AdapterResult, DesignAdapter
from .calculator_expressions import calculator_expressions_equal
from .catalog import task_requests_oa_parameter_write
from .characterization import normalize_mos_characterization
from .design_context import audit_design_context
from .generic_simulation import GenericTestbenchOverrides
from .metrics import evaluate_constraints
from .models import (
    ActionRecord,
    AnalysisStageExecution,
    AnalysisStageEvaluation,
    AnalysisStageSpec,
    CandidateEvaluation,
    CircuitKind,
    EvidenceSource,
    ExecutionCheckpoint,
    ExecutionPlan,
    InstanceParameterUpdate,
    ObjectiveGoal,
    Operation,
    OperatingConditionEvaluation,
    Relation,
    RunRecord,
    RunStatus,
    SearchAudit,
    SelectionScope,
    SchematicTransformAction,
    TaskSpec,
)
from .parameter_binding import (
    classify_parameter_binding_probe,
    validate_parameter_binding_eda_stage,
)
from .safety import authorize_execution
from .spectre_values import spectre_scalar, spectre_values_equal
from .topology_delta import (
    audit_derived_topology_delta,
    snapshot_from_inspection,
    topology_fingerprint,
    validate_topology_execution_readback,
)

T = TypeVar("T")

_OA_SEMANTIC_PARAMETERS = {
    CircuitKind.INVERTER: ("nmos_width_um", "pmos_width_um", "length_um"),
    CircuitKind.COMMON_SOURCE: (
        "device_width_um",
        "length_um",
        "load_resistance_ohm",
    ),
    CircuitKind.DIFFERENTIAL_PAIR: (
        "input_width_um",
        "length_um",
    ),
}

_COMMON_SOURCE_OPTIONAL_OA_PARAMETERS = (
    "source_resistance_ohm",
    "cascode_width_um",
    "cascode_length_um",
)
_DIFFERENTIAL_PAIR_OPTIONAL_OA_PARAMETERS = (
    "tail_width_um",
    "tail_length_um",
    "source_resistance_ohm",
    "load_resistance_ohm",
    "pmos_load_width_um",
    "pmos_load_length_um",
)


@dataclass(frozen=True)
class _CandidateInput:
    parameters: dict[str, float]
    instance_parameters: dict[str, dict[str, str]]
    testbench_overrides: GenericTestbenchOverrides | None = None
    testbench_override_evidence_source: EvidenceSource | None = None
    atomic_candidate_id: str | None = None
    atomic_candidate_predicted_metrics: dict[str, float] = field(default_factory=dict)
    atomic_candidate_evidence_source: EvidenceSource | None = None
    theory_seed_candidate_id: str | None = None
    theory_seed_source_candidate_id: str | None = None
    theory_seed_predicted_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _AppliedCandidateState:
    oa_parameters: dict[str, float]
    oa_instance_parameters: dict[str, dict[str, str]]


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

    def _bind_design_context(
        self,
        task: TaskSpec,
        inspection: AdapterResult,
        *,
        action_name: str = "design.context.bind",
    ) -> AdapterResult | None:
        if task.design_context is None:
            return None
        return self._action(
            action_name,
            lambda: AdapterResult(
                data=audit_design_context(
                    inspection.data,
                    task.design_context,
                ).model_dump(mode="json"),
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            ),
        )

    @staticmethod
    def _candidates(task: TaskSpec) -> list[dict[str, float]]:
        if task.candidate_set is not None:
            return [
                dict(task.parameters) | dict(candidate.parameters)
                for candidate in task.candidate_set.candidates[
                    : task.limits.max_iterations
                ]
            ]
        if task.theory_seed is not None:
            return [
                dict(task.parameters) | dict(candidate.parameters)
                for candidate in task.theory_seed.candidates[
                    : task.limits.max_iterations
                ]
            ]
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
    def _instance_parameter_candidates(
        task: TaskSpec,
    ) -> list[dict[str, dict[str, str]]]:
        fixed = {
            update.instance: dict(update.parameters)
            for update in task.instance_parameter_updates
        }
        dimensions = sorted(
            task.instance_parameter_space,
            key=lambda sweep: (sweep.instance, sweep.parameter),
        )
        if not dimensions:
            return [fixed]
        candidates: list[dict[str, dict[str, str]]] = []
        for values in itertools.product(*(sweep.values for sweep in dimensions)):
            candidate = {
                instance: dict(parameters)
                for instance, parameters in fixed.items()
            }
            for sweep, value in zip(dimensions, values, strict=True):
                candidate.setdefault(sweep.instance, {})[sweep.parameter] = value
            candidates.append(candidate)
            if len(candidates) >= task.limits.max_iterations:
                break
        return candidates

    @staticmethod
    def _merge_instance_parameters(
        fixed: dict[str, dict[str, str]],
        varying: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        merged = {instance: dict(values) for instance, values in fixed.items()}
        for instance, values in varying.items():
            merged.setdefault(instance, {}).update(values)
        return merged

    @classmethod
    def _candidate_inputs(cls, task: TaskSpec) -> list[_CandidateInput]:
        if task.candidate_set is not None:
            fixed_instance_parameters = cls._instance_parameter_candidates(task)[0]
            return [
                _CandidateInput(
                    parameters=dict(task.parameters) | dict(candidate.parameters),
                    instance_parameters=cls._merge_instance_parameters(
                        fixed_instance_parameters,
                        candidate.instance_parameters(),
                    ),
                    testbench_overrides=candidate.testbench_overrides,
                    testbench_override_evidence_source=(
                        task.candidate_set.source.evidence_source
                        if candidate.testbench_overrides is not None
                        else None
                    ),
                    atomic_candidate_id=candidate.id,
                    atomic_candidate_predicted_metrics=dict(
                        candidate.predicted_metrics
                    ),
                    atomic_candidate_evidence_source=(
                        task.candidate_set.source.evidence_source
                    ),
                )
                for candidate in task.candidate_set.candidates[
                    : task.limits.max_iterations
                ]
            ]
        candidates: list[_CandidateInput] = []
        for semantic_index, parameters in enumerate(cls._candidates(task)):
            seed = (
                task.theory_seed.candidates[semantic_index]
                if task.theory_seed is not None
                else None
            )
            for instance_parameters in cls._instance_parameter_candidates(task):
                candidates.append(
                    _CandidateInput(
                        parameters=dict(parameters),
                        instance_parameters={
                            instance: dict(values)
                            for instance, values in instance_parameters.items()
                        },
                        theory_seed_candidate_id=(seed.id if seed is not None else None),
                        theory_seed_source_candidate_id=(
                            seed.source_candidate_id if seed is not None else None
                        ),
                        theory_seed_predicted_metrics=(
                            dict(seed.predicted_metrics) if seed is not None else {}
                        ),
                    )
                )
                if len(candidates) >= task.limits.max_iterations:
                    return candidates
        return candidates

    @staticmethod
    def _evaluate_candidate(
        task: TaskSpec,
        index: int,
        parameters: dict[str, float],
        simulation: AdapterResult,
        instance_parameters: dict[str, dict[str, str]] | None = None,
        testbench_overrides: GenericTestbenchOverrides | None = None,
        testbench_override_evidence_source: EvidenceSource | None = None,
        oa_parameters: dict[str, float] | None = None,
        atomic_candidate_id: str | None = None,
        atomic_candidate_predicted_metrics: dict[str, float] | None = None,
        atomic_candidate_evidence_source: EvidenceSource | None = None,
        theory_seed_candidate_id: str | None = None,
        theory_seed_source_candidate_id: str | None = None,
        theory_seed_predicted_metrics: dict[str, float] | None = None,
        topology_variant_id: str | None = None,
        topology_sha256: str | None = None,
    ) -> CandidateEvaluation:
        raw_conditions = simulation.data.get("operating_condition_results")
        if raw_conditions is not None:
            return TaskExecutor._evaluate_operating_condition_candidate(
                task,
                index,
                parameters,
                simulation,
                raw_conditions,
                instance_parameters=instance_parameters,
                testbench_overrides=testbench_overrides,
                testbench_override_evidence_source=(
                    testbench_override_evidence_source
                ),
                oa_parameters=oa_parameters,
                atomic_candidate_id=atomic_candidate_id,
                atomic_candidate_predicted_metrics=(
                    atomic_candidate_predicted_metrics
                ),
                atomic_candidate_evidence_source=(
                    atomic_candidate_evidence_source
                ),
                theory_seed_candidate_id=theory_seed_candidate_id,
                theory_seed_source_candidate_id=theory_seed_source_candidate_id,
                theory_seed_predicted_metrics=theory_seed_predicted_metrics,
                topology_variant_id=topology_variant_id,
                topology_sha256=topology_sha256,
            )
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
            topology_variant_id=topology_variant_id,
            topology_sha256=topology_sha256,
            parameters=evaluated_parameters,
            instance_parameters=instance_parameters or {},
            testbench_overrides=testbench_overrides,
            testbench_override_evidence_source=(
                testbench_override_evidence_source
            ),
            oa_parameters=oa_parameters or {},
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
            atomic_candidate_id=atomic_candidate_id,
            atomic_candidate_predicted_metrics=(
                atomic_candidate_predicted_metrics or {}
            ),
            atomic_candidate_evidence_source=atomic_candidate_evidence_source,
            theory_seed_candidate_id=theory_seed_candidate_id,
            theory_seed_source_candidate_id=theory_seed_source_candidate_id,
            theory_seed_predicted_metrics=theory_seed_predicted_metrics or {},
            theory_seed_evidence_source=(
                EvidenceSource.SOFTWARE_INFERENCE
                if theory_seed_candidate_id is not None
                else None
            ),
        )

    @staticmethod
    def _constraints_for_analysis_stage(
        task: TaskSpec, stage: AnalysisStageSpec
    ) -> list[Any]:
        metrics = set(stage.constraint_metrics)
        return [
            constraint
            for constraint in task.constraints
            if constraint.metric in metrics
        ]

    @staticmethod
    def _worst_constraint_evaluation(
        constraint: Any,
        rows: list[Any],
    ) -> Any:
        if constraint.relation is Relation.LESS_OR_EQUAL:
            return max(
                rows,
                key=lambda item: (
                    item.normalized_violation,
                    -math.inf if item.actual is None else item.actual,
                ),
            )
        if constraint.relation is Relation.GREATER_OR_EQUAL:
            return max(
                rows,
                key=lambda item: (
                    item.normalized_violation,
                    math.inf if item.actual is None else -item.actual,
                ),
            )
        return max(
            rows,
            key=lambda item: (
                item.normalized_violation,
                math.inf
                if item.actual is None
                else abs(item.actual - constraint.value),
            ),
        )

    @classmethod
    def _evaluate_analysis_stage(
        cls,
        task: TaskSpec,
        stage: AnalysisStageSpec,
        simulation: AdapterResult,
    ) -> AnalysisStageEvaluation:
        raw_conditions = simulation.data.get("operating_condition_results")
        if raw_conditions is not None:
            return cls._evaluate_operating_condition_analysis_stage(
                task,
                stage,
                simulation,
                raw_conditions,
            )
        metrics = {
            str(name): float(value)
            for name, value in simulation.data.get("metrics", {}).items()
        }
        constraints = evaluate_constraints(
            metrics,
            cls._constraints_for_analysis_stage(task, stage),
        )
        analysis_complete = bool(simulation.data.get("analysis_complete", True))
        analysis_issues = [
            str(value) for value in simulation.data.get("analysis_issues", [])
        ]
        analysis_warnings = [
            str(value) for value in simulation.data.get("analysis_warnings", [])
        ]
        if not analysis_complete and not analysis_issues:
            analysis_issues = ["analysis did not produce its required core metrics"]
        missing_constraint_metrics = [
            item.metric for item in constraints if item.actual is None
        ]
        if missing_constraint_metrics:
            analysis_complete = False
            analysis_issues.append(
                "analysis omitted stage constraint metrics: "
                + ", ".join(missing_constraint_metrics)
            )
        raw_sources = simulation.data.get("metric_sources", {})
        metric_sources = {
            name: EvidenceSource(raw_sources.get(name, simulation.evidence_source))
            for name in metrics
        }
        return AnalysisStageEvaluation(
            stage_id=stage.id,
            analysis=stage.analysis,
            metrics=metrics,
            constraints=constraints,
            passed=analysis_complete and all(item.passed for item in constraints),
            evidence_source=simulation.evidence_source,
            metric_sources=metric_sources,
            analysis_complete=analysis_complete,
            analysis_issues=analysis_issues,
            analysis_warnings=analysis_warnings,
        )

    @staticmethod
    def _default_operating_condition_vdd(task: TaskSpec) -> float:
        if "vdd_v" in task.parameters:
            return float(task.parameters["vdd_v"])
        settings = task.generic_simulation
        if settings is None or settings.operating_condition_supply_source is None:
            raise RuntimeError(
                "operating-condition evaluation has no declared supply source"
            )
        source = next(
            (
                item
                for item in settings.sources
                if item.name == settings.operating_condition_supply_source
            ),
            None,
        )
        if source is None:
            raise RuntimeError(
                "operating-condition supply source disappeared from the testbench"
            )
        return float(source.dc_value)

    @classmethod
    def _evaluate_operating_condition_analysis_stage(
        cls,
        task: TaskSpec,
        stage: AnalysisStageSpec,
        simulation: AdapterResult,
        raw_conditions: Any,
    ) -> AnalysisStageEvaluation:
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise RuntimeError("analysis stage returned no operating conditions")
        expected = [
            condition.model_dump(mode="json")
            for condition in task.operating_conditions
        ]
        if len(raw_conditions) != len(expected):
            raise RuntimeError(
                "analysis stage did not return every declared operating condition"
            )
        stage_constraints = cls._constraints_for_analysis_stage(task, stage)
        evaluations: list[OperatingConditionEvaluation] = []
        for position, (raw, expected_condition) in enumerate(
            zip(raw_conditions, expected, strict=True), start=1
        ):
            if not isinstance(raw, dict) or not isinstance(raw.get("result"), dict):
                raise RuntimeError(
                    f"analysis-stage operating condition {position} is not structured"
                )
            if raw.get("condition") != expected_condition:
                raise RuntimeError(
                    "analysis-stage operating-condition identity/order mismatch"
                )
            result = raw["result"]
            metrics = {
                str(name): float(value)
                for name, value in result.get("metrics", {}).items()
            }
            constraints = evaluate_constraints(metrics, stage_constraints)
            analysis_complete = bool(result.get("analysis_complete", True))
            issues = [str(value) for value in result.get("analysis_issues", [])]
            missing = [item.metric for item in constraints if item.actual is None]
            if missing:
                analysis_complete = False
                issues.append(
                    "analysis omitted stage constraint metrics: "
                    + ", ".join(missing)
                )
            if not analysis_complete and not issues:
                issues = ["analysis did not produce its required core metrics"]
            warnings = [
                str(value) for value in result.get("analysis_warnings", [])
            ]
            raw_sources = result.get("metric_sources", {})
            metric_sources = {
                name: EvidenceSource(
                    raw_sources.get(name, simulation.evidence_source)
                )
                for name in metrics
            }
            effective_vdd = (
                float(expected_condition["vdd_v"])
                if expected_condition["vdd_v"] is not None
                else cls._default_operating_condition_vdd(task)
            )
            returned_parameters = {
                str(name): float(value)
                for name, value in result.get("parameters", {}).items()
            }
            if not math.isclose(
                returned_parameters.get("vdd_v", float("nan")),
                effective_vdd,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise RuntimeError(
                    f"operating condition {expected_condition['name']} did not "
                    "confirm its effective supply voltage"
                )
            evaluations.append(
                OperatingConditionEvaluation(
                    name=str(expected_condition["name"]),
                    process_corner=str(expected_condition["process_corner"]),
                    temperature_c=float(expected_condition["temperature_c"]),
                    vdd_v=effective_vdd,
                    parameters=returned_parameters,
                    metrics=metrics,
                    constraints=constraints,
                    feasible=(
                        analysis_complete and all(item.passed for item in constraints)
                    ),
                    total_violation=(
                        sum(item.normalized_violation for item in constraints)
                        + (1_000_000.0 if not analysis_complete else 0.0)
                    ),
                    evidence_source=simulation.evidence_source,
                    metric_sources=metric_sources,
                    analysis_complete=analysis_complete,
                    analysis_issues=issues,
                    analysis_warnings=warnings,
                )
            )

        aggregate_constraints = []
        aggregate_metrics: dict[str, float] = {}
        for constraint_index, constraint in enumerate(stage_constraints):
            rows = [item.constraints[constraint_index] for item in evaluations]
            worst = cls._worst_constraint_evaluation(constraint, rows)
            aggregate_constraints.append(worst)
            if worst.actual is not None:
                aggregate_metrics[constraint.metric] = float(worst.actual)
        return AnalysisStageEvaluation(
            stage_id=stage.id,
            analysis=stage.analysis,
            metrics=aggregate_metrics,
            constraints=aggregate_constraints,
            passed=all(item.feasible for item in evaluations),
            evidence_source=simulation.evidence_source,
            metric_sources={
                name: EvidenceSource.SOFTWARE_INFERENCE
                for name in aggregate_metrics
            },
            analysis_complete=all(item.analysis_complete for item in evaluations),
            analysis_issues=[
                f"{item.name}: {issue}"
                for item in evaluations
                for issue in item.analysis_issues
            ],
            analysis_warnings=[
                f"{item.name}: {warning}"
                for item in evaluations
                for warning in item.analysis_warnings
            ],
            operating_conditions=evaluations,
        )

    @classmethod
    def _merge_analysis_stage_candidate(
        cls,
        task: TaskSpec,
        index: int,
        candidate: _CandidateInput,
        stages: list[AnalysisStageEvaluation],
        *,
        oa_parameters: dict[str, float] | None = None,
        topology_variant_id: str | None = None,
        topology_sha256: str | None = None,
        terminated_after_stage: str | None = None,
    ) -> CandidateEvaluation:
        if any(stage.operating_conditions for stage in stages):
            if not stages or not all(stage.operating_conditions for stage in stages):
                raise RuntimeError(
                    "analysis-stage PVT evidence is incomplete across stages"
                )
            return cls._merge_operating_condition_analysis_stage_candidate(
                task,
                index,
                candidate,
                stages,
                oa_parameters=oa_parameters,
                topology_variant_id=topology_variant_id,
                topology_sha256=topology_sha256,
                terminated_after_stage=terminated_after_stage,
            )
        metrics: dict[str, float] = {}
        metric_sources: dict[str, EvidenceSource] = {}
        for stage in stages:
            for name, value in stage.metrics.items():
                if name in metrics and not math.isclose(
                    metrics[name],
                    value,
                    rel_tol=1e-5,
                    abs_tol=1e-12,
                ):
                    raise RuntimeError(
                        "analysis stages returned inconsistent repeated metric "
                        f"{name}: {metrics[name]:.12g} versus {value:.12g}"
                    )
                metrics.setdefault(name, value)
                metric_sources.setdefault(name, stage.metric_sources[name])

        constraints = evaluate_constraints(metrics, task.constraints)
        if terminated_after_stage is not None:
            constraints = [
                evaluation.model_copy(
                    update={
                        "reason": (
                            "not evaluated after earlier analysis-stage gate rejection"
                            if evaluation.actual is None
                            else evaluation.reason
                        )
                    }
                )
                for evaluation in constraints
            ]
        completed_all_stages = len(stages) == len(task.analysis_stages)
        decision_complete = completed_all_stages or terminated_after_stage is not None
        stage_analyses_complete = all(stage.analysis_complete for stage in stages)
        objective_value = (
            metrics.get(task.objective.metric) if task.objective is not None else None
        )
        objective_missing = (
            completed_all_stages
            and task.objective is not None
            and objective_value is None
        )
        analysis_complete = (
            decision_complete and stage_analyses_complete and not objective_missing
        )
        analysis_issues = [
            f"{stage.stage_id}: {issue}"
            for stage in stages
            for issue in stage.analysis_issues
        ]
        if not decision_complete:
            analysis_issues.append("candidate did not complete every required analysis stage")
        if objective_missing:
            analysis_issues.append(
                f"candidate omitted objective metric {task.objective.metric!r}"
            )
        analysis_warnings = [
            f"{stage.stage_id}: {warning}"
            for stage in stages
            for warning in stage.analysis_warnings
        ]
        return CandidateEvaluation(
            index=index,
            topology_variant_id=topology_variant_id,
            topology_sha256=topology_sha256,
            parameters=dict(candidate.parameters),
            instance_parameters={
                instance: dict(parameters)
                for instance, parameters in candidate.instance_parameters.items()
            },
            testbench_overrides=candidate.testbench_overrides,
            testbench_override_evidence_source=(
                candidate.testbench_override_evidence_source
            ),
            oa_parameters=oa_parameters or {},
            metrics=metrics,
            constraints=constraints,
            feasible=(
                completed_all_stages
                and analysis_complete
                and all(item.passed for item in constraints)
                and not objective_missing
            ),
            total_violation=(
                sum(item.normalized_violation for item in constraints)
                + (1_000_000.0 if objective_missing else 0.0)
                + (1_000_000.0 if not analysis_complete else 0.0)
            ),
            objective_value=objective_value,
            evidence_source=(
                stages[-1].evidence_source
                if stages
                else EvidenceSource.SYSTEM_EVENT
            ),
            metric_sources=metric_sources,
            analysis_complete=analysis_complete,
            analysis_issues=analysis_issues,
            analysis_warnings=analysis_warnings,
            atomic_candidate_id=candidate.atomic_candidate_id,
            atomic_candidate_predicted_metrics=(
                candidate.atomic_candidate_predicted_metrics
            ),
            atomic_candidate_evidence_source=(
                candidate.atomic_candidate_evidence_source
            ),
            theory_seed_candidate_id=candidate.theory_seed_candidate_id,
            theory_seed_source_candidate_id=(
                candidate.theory_seed_source_candidate_id
            ),
            theory_seed_predicted_metrics=(
                candidate.theory_seed_predicted_metrics
            ),
            theory_seed_evidence_source=(
                EvidenceSource.SOFTWARE_INFERENCE
                if candidate.theory_seed_candidate_id is not None
                else None
            ),
            analysis_stages=stages,
            terminated_after_stage=terminated_after_stage,
        )

    @classmethod
    def _merge_operating_condition_analysis_stage_candidate(
        cls,
        task: TaskSpec,
        index: int,
        candidate: _CandidateInput,
        stages: list[AnalysisStageEvaluation],
        *,
        oa_parameters: dict[str, float] | None,
        topology_variant_id: str | None,
        topology_sha256: str | None,
        terminated_after_stage: str | None,
    ) -> CandidateEvaluation:
        expected = [
            condition.model_dump(mode="json")
            for condition in task.operating_conditions
        ]
        for stage in stages:
            identities = [
                {
                    "name": item.name,
                    "process_corner": item.process_corner,
                    "temperature_c": item.temperature_c,
                    "vdd_v": (
                        next(
                            condition.vdd_v
                            for condition in task.operating_conditions
                            if condition.name == item.name
                        )
                    ),
                }
                for item in stage.operating_conditions
            ]
            if identities != expected:
                raise RuntimeError(
                    "analysis stages returned inconsistent operating-condition identities"
                )

        completed_all_stages = len(stages) == len(task.analysis_stages)
        decision_complete = completed_all_stages or terminated_after_stage is not None
        condition_evaluations: list[OperatingConditionEvaluation] = []
        for condition_index, condition in enumerate(task.operating_conditions):
            rows = [stage.operating_conditions[condition_index] for stage in stages]
            metrics: dict[str, float] = {}
            metric_sources: dict[str, EvidenceSource] = {}
            for row in rows:
                for name, value in row.metrics.items():
                    if name in metrics and not math.isclose(
                        metrics[name], value, rel_tol=1e-5, abs_tol=1e-12
                    ):
                        raise RuntimeError(
                            "PVT analysis stages returned inconsistent repeated metric "
                            f"{condition.name}.{name}"
                        )
                    metrics.setdefault(name, value)
                    metric_sources.setdefault(name, row.metric_sources[name])
            constraints = evaluate_constraints(metrics, task.constraints)
            if terminated_after_stage is not None:
                constraints = [
                    item.model_copy(
                        update={
                            "reason": (
                                "not evaluated after earlier analysis-stage gate rejection"
                                if item.actual is None
                                else item.reason
                            )
                        }
                    )
                    for item in constraints
                ]
            objective_value = (
                metrics.get(task.objective.metric)
                if task.objective is not None
                else None
            )
            objective_missing = (
                completed_all_stages
                and task.objective is not None
                and objective_value is None
            )
            analysis_complete = (
                decision_complete
                and all(row.analysis_complete for row in rows)
                and not objective_missing
            )
            condition_evaluations.append(
                OperatingConditionEvaluation(
                    name=condition.name,
                    process_corner=condition.process_corner,
                    temperature_c=condition.temperature_c,
                    vdd_v=rows[0].vdd_v,
                    parameters=dict(rows[0].parameters),
                    metrics=metrics,
                    constraints=constraints,
                    feasible=(
                        completed_all_stages
                        and analysis_complete
                        and all(item.passed for item in constraints)
                        and not objective_missing
                    ),
                    total_violation=(
                        sum(item.normalized_violation for item in constraints)
                        + (1_000_000.0 if objective_missing else 0.0)
                        + (1_000_000.0 if not analysis_complete else 0.0)
                    ),
                    objective_value=objective_value,
                    evidence_source=rows[-1].evidence_source,
                    metric_sources=metric_sources,
                    analysis_complete=analysis_complete,
                    analysis_issues=[
                        f"{stage.stage_id}: {issue}"
                        for stage, row in zip(stages, rows, strict=True)
                        for issue in row.analysis_issues
                    ],
                    analysis_warnings=[
                        f"{stage.stage_id}: {warning}"
                        for stage, row in zip(stages, rows, strict=True)
                        for warning in row.analysis_warnings
                    ],
                )
            )

        aggregate_metrics: dict[str, float] = {}
        aggregate_constraints = []
        for constraint_index, constraint in enumerate(task.constraints):
            worst = cls._worst_constraint_evaluation(
                constraint,
                [
                    item.constraints[constraint_index]
                    for item in condition_evaluations
                ],
            )
            aggregate_constraints.append(worst)
            if worst.actual is not None:
                aggregate_metrics[constraint.metric] = float(worst.actual)
        objective_value: float | None = None
        if task.objective is not None:
            values = [item.objective_value for item in condition_evaluations]
            if all(value is not None for value in values):
                numeric_values = [float(value) for value in values if value is not None]
                objective_value = (
                    max(numeric_values)
                    if task.objective.goal is ObjectiveGoal.MINIMIZE
                    else min(numeric_values)
                )
                aggregate_metrics[task.objective.metric] = objective_value
        objective_missing = task.objective is not None and objective_value is None
        analysis_complete = all(
            item.analysis_complete for item in condition_evaluations
        )
        return CandidateEvaluation(
            index=index,
            topology_variant_id=topology_variant_id,
            topology_sha256=topology_sha256,
            parameters=dict(candidate.parameters),
            instance_parameters={
                instance: dict(parameters)
                for instance, parameters in candidate.instance_parameters.items()
            },
            testbench_overrides=candidate.testbench_overrides,
            testbench_override_evidence_source=(
                candidate.testbench_override_evidence_source
            ),
            oa_parameters=oa_parameters or {},
            metrics=aggregate_metrics,
            constraints=aggregate_constraints,
            feasible=(
                completed_all_stages
                and analysis_complete
                and all(item.feasible for item in condition_evaluations)
                and not objective_missing
            ),
            total_violation=sum(
                item.total_violation for item in condition_evaluations
            ),
            objective_value=objective_value,
            evidence_source=stages[-1].evidence_source,
            metric_sources={
                name: EvidenceSource.SOFTWARE_INFERENCE
                for name in aggregate_metrics
            },
            analysis_complete=analysis_complete,
            analysis_issues=[
                f"{item.name}: {issue}"
                for item in condition_evaluations
                for issue in item.analysis_issues
            ],
            analysis_warnings=[
                f"{item.name}: {warning}"
                for item in condition_evaluations
                for warning in item.analysis_warnings
            ],
            operating_conditions=condition_evaluations,
            atomic_candidate_id=candidate.atomic_candidate_id,
            atomic_candidate_predicted_metrics=(
                candidate.atomic_candidate_predicted_metrics
            ),
            atomic_candidate_evidence_source=(
                candidate.atomic_candidate_evidence_source
            ),
            theory_seed_candidate_id=candidate.theory_seed_candidate_id,
            theory_seed_source_candidate_id=(
                candidate.theory_seed_source_candidate_id
            ),
            theory_seed_predicted_metrics=candidate.theory_seed_predicted_metrics,
            theory_seed_evidence_source=(
                EvidenceSource.SOFTWARE_INFERENCE
                if candidate.theory_seed_candidate_id is not None
                else None
            ),
            analysis_stages=stages,
            terminated_after_stage=terminated_after_stage,
        )

    @staticmethod
    def _evaluate_operating_condition_candidate(
        task: TaskSpec,
        index: int,
        parameters: dict[str, float],
        simulation: AdapterResult,
        raw_conditions: Any,
        *,
        instance_parameters: dict[str, dict[str, str]] | None = None,
        testbench_overrides: GenericTestbenchOverrides | None = None,
        testbench_override_evidence_source: EvidenceSource | None = None,
        oa_parameters: dict[str, float] | None = None,
        atomic_candidate_id: str | None = None,
        atomic_candidate_predicted_metrics: dict[str, float] | None = None,
        atomic_candidate_evidence_source: EvidenceSource | None = None,
        theory_seed_candidate_id: str | None = None,
        theory_seed_source_candidate_id: str | None = None,
        theory_seed_predicted_metrics: dict[str, float] | None = None,
        topology_variant_id: str | None = None,
        topology_sha256: str | None = None,
    ) -> CandidateEvaluation:
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise RuntimeError("operating-condition simulation returned no cases")
        expected = [
            condition.model_dump(mode="json")
            for condition in task.operating_conditions
        ]
        if len(raw_conditions) != len(expected):
            raise RuntimeError(
                "operating-condition simulation did not return every declared case"
            )

        evaluations: list[OperatingConditionEvaluation] = []
        for position, (raw, expected_condition) in enumerate(
            zip(raw_conditions, expected, strict=True), start=1
        ):
            if not isinstance(raw, dict) or not isinstance(raw.get("result"), dict):
                raise RuntimeError(
                    f"operating-condition result {position} is not structured"
                )
            condition = raw.get("condition")
            if condition != expected_condition:
                raise RuntimeError(
                    "operating-condition result identity/order does not match the task"
                )
            result = raw["result"]
            metrics = {
                str(name): float(value)
                for name, value in result.get("metrics", {}).items()
            }
            constraints = evaluate_constraints(metrics, task.constraints)
            objective_value = (
                metrics.get(task.objective.metric)
                if task.objective is not None
                else None
            )
            objective_missing = (
                task.objective is not None and objective_value is None
            )
            analysis_complete = bool(result.get("analysis_complete", True))
            analysis_issues = [
                str(value) for value in result.get("analysis_issues", [])
            ]
            analysis_warnings = [
                str(value) for value in result.get("analysis_warnings", [])
            ]
            if not analysis_complete and not analysis_issues:
                analysis_issues = [
                    "analysis did not produce its required core metrics"
                ]
            raw_sources = result.get("metric_sources", {})
            metric_sources = {
                name: EvidenceSource(
                    raw_sources.get(name, simulation.evidence_source)
                )
                for name in metrics
            }
            evaluated_parameters = {
                str(name): float(value)
                for name, value in result.get("parameters", parameters).items()
            }
            effective_vdd = (
                expected_condition["vdd_v"]
                if expected_condition["vdd_v"] is not None
                else TaskExecutor._default_operating_condition_vdd(task)
            )
            for name, value in parameters.items():
                expected_value = effective_vdd if name == "vdd_v" else value
                if not math.isclose(
                    float(evaluated_parameters.get(name, float("nan"))),
                    float(expected_value),
                    rel_tol=1e-9,
                    abs_tol=1e-12,
                ):
                    raise RuntimeError(
                        f"operating condition {expected_condition['name']} did not "
                        f"confirm candidate parameter {name}"
                    )
            if not math.isclose(
                float(evaluated_parameters.get("vdd_v", float("nan"))),
                float(effective_vdd),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise RuntimeError(
                    f"operating condition {expected_condition['name']} did not "
                    "confirm its effective vdd_v"
                )
            evaluations.append(
                OperatingConditionEvaluation(
                    name=str(expected_condition["name"]),
                    process_corner=str(expected_condition["process_corner"]),
                    temperature_c=float(expected_condition["temperature_c"]),
                    vdd_v=float(effective_vdd),
                    parameters=evaluated_parameters,
                    metrics=metrics,
                    constraints=constraints,
                    feasible=(
                        all(item.passed for item in constraints)
                        and not objective_missing
                        and analysis_complete
                    ),
                    total_violation=(
                        sum(item.normalized_violation for item in constraints)
                        + (1_000_000.0 if objective_missing else 0.0)
                        + (1_000_000.0 if not analysis_complete else 0.0)
                    ),
                    objective_value=objective_value,
                    evidence_source=simulation.evidence_source,
                    metric_sources=metric_sources,
                    analysis_complete=analysis_complete,
                    analysis_issues=analysis_issues,
                    analysis_warnings=analysis_warnings,
                )
            )

        aggregate_metrics: dict[str, float] = {}
        aggregate_constraints = []
        for constraint_index, constraint in enumerate(task.constraints):
            rows = [item.constraints[constraint_index] for item in evaluations]
            if constraint.relation is Relation.LESS_OR_EQUAL:
                worst = max(
                    rows,
                    key=lambda item: (
                        item.normalized_violation,
                        -math.inf if item.actual is None else item.actual,
                    ),
                )
            elif constraint.relation is Relation.GREATER_OR_EQUAL:
                worst = max(
                    rows,
                    key=lambda item: (
                        item.normalized_violation,
                        math.inf if item.actual is None else -item.actual,
                    ),
                )
            else:
                worst = max(
                    rows,
                    key=lambda item: (
                        item.normalized_violation,
                        math.inf
                        if item.actual is None
                        else abs(item.actual - constraint.value),
                    ),
                )
            aggregate_constraints.append(worst)
            if worst.actual is not None:
                aggregate_metrics[constraint.metric] = float(worst.actual)

        objective_value: float | None = None
        if task.objective is not None:
            objective_values = [
                item.objective_value
                for item in evaluations
                if item.objective_value is not None
            ]
            if len(objective_values) == len(evaluations):
                objective_value = (
                    max(objective_values)
                    if task.objective.goal is ObjectiveGoal.MINIMIZE
                    else min(objective_values)
                )
                aggregate_metrics[task.objective.metric] = float(objective_value)

        analysis_complete = all(item.analysis_complete for item in evaluations)
        analysis_issues = [
            f"{item.name}: {issue}"
            for item in evaluations
            for issue in item.analysis_issues
        ]
        analysis_warnings = [
            f"{item.name}: {warning}"
            for item in evaluations
            for warning in item.analysis_warnings
        ]
        evaluated_parameters = {
            str(name): float(value)
            for name, value in simulation.data.get("parameters", parameters).items()
        }
        objective_missing = task.objective is not None and objective_value is None
        feasible = (
            all(item.feasible for item in evaluations)
            and not objective_missing
            and analysis_complete
        )
        return CandidateEvaluation(
            index=index,
            topology_variant_id=topology_variant_id,
            topology_sha256=topology_sha256,
            parameters=evaluated_parameters,
            instance_parameters=instance_parameters or {},
            testbench_overrides=testbench_overrides,
            testbench_override_evidence_source=(
                testbench_override_evidence_source
            ),
            oa_parameters=oa_parameters or {},
            metrics=aggregate_metrics,
            constraints=aggregate_constraints,
            feasible=feasible,
            total_violation=sum(item.total_violation for item in evaluations),
            objective_value=objective_value,
            evidence_source=simulation.evidence_source,
            metric_sources={
                name: EvidenceSource.SOFTWARE_INFERENCE
                for name in aggregate_metrics
            },
            analysis_complete=analysis_complete,
            analysis_issues=analysis_issues,
            analysis_warnings=analysis_warnings,
            operating_conditions=evaluations,
            atomic_candidate_id=atomic_candidate_id,
            atomic_candidate_predicted_metrics=(
                atomic_candidate_predicted_metrics or {}
            ),
            atomic_candidate_evidence_source=atomic_candidate_evidence_source,
            theory_seed_candidate_id=theory_seed_candidate_id,
            theory_seed_source_candidate_id=theory_seed_source_candidate_id,
            theory_seed_predicted_metrics=theory_seed_predicted_metrics or {},
            theory_seed_evidence_source=(
                EvidenceSource.SOFTWARE_INFERENCE
                if theory_seed_candidate_id is not None
                else None
            ),
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
        if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
            # Generic existing schematics intentionally expose only exact raw
            # instance fields.  They do not invent topology-specific semantic
            # aliases, and the Bridge summary therefore has no semantic map.
            return {}
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
        elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            has_resistive_load = "load_resistance_ohm" in raw
            has_active_load = all(
                name in raw
                for name in ("pmos_load_width_um", "pmos_load_length_um")
            )
            if has_resistive_load == has_active_load:
                raise RuntimeError(
                    "differential-pair inspection must return exactly one load "
                    "parameterization"
                )
            names.extend(
                name
                for name in _DIFFERENTIAL_PAIR_OPTIONAL_OA_PARAMETERS
                if name in raw
            )
        return {name: float(raw[name]) for name in names}

    @staticmethod
    def _assert_expected_target_topology(
        result: AdapterResult,
        task: TaskSpec,
        *,
        before_candidate_write: bool = True,
    ) -> None:
        expected = task.expected_target_topology_variant
        if expected is None:
            return
        actual = result.data.get("topology_variant")
        if not isinstance(actual, str) or not actual:
            raise RuntimeError(
                "target topology precondition could not be verified: "
                f"expected {expected!r}, but schematic inspection omitted "
                "topology_variant"
            )
        if actual != expected:
            consequence = (
                "no candidate OA write was attempted"
                if before_candidate_write
                else "final topology readback does not match the task contract"
            )
            raise RuntimeError(
                "target topology precondition mismatch: "
                f"expected {expected!r}, read back {actual!r}; "
                f"{consequence}"
            )

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
    def _assert_differential_pair_tail_device_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        tail_width_um: float,
        tail_length_um: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        core_names = {"MN0", "MN1", "RD0", "RD1"}
        tail_names = core_names | {"MNTAIL"}
        if frozenset(before_instances) not in {
            frozenset(core_names),
            frozenset(tail_names),
        }:
            raise RuntimeError(
                "tail-device transform requires the exact VDA differential-pair core "
                "or real-tail instance set"
            )
        if set(after_instances) != tail_names:
            raise RuntimeError(
                "tail-device transform did not produce exactly "
                "MN0/MN1/RD0/RD1/MNTAIL"
            )

        expected_core_terminals = {
            "MN0": {"D": "OUTP", "G": "INP", "S": "TAIL", "B": "VSS"},
            "MN1": {"D": "OUTN", "G": "INN", "S": "TAIL", "B": "VSS"},
            "RD0": {"PLUS": "VDD", "MINUS": "OUTP"},
            "RD1": {"PLUS": "VDD", "MINUS": "OUTN"},
        }
        for name, terminals in expected_core_terminals.items():
            if before_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"tail-device transform found unexpected {name} terminals"
                )
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"tail-device transform changed {name} terminals"
                )
        if after_instances["MNTAIL"].get("terminals") != {
            "D": "TAIL",
            "G": "BIAS",
            "S": "VSS",
            "B": "VSS",
        }:
            raise RuntimeError("MNTAIL is not connected to TAIL/BIAS/VSS/VSS")
        if (
            after_instances["MNTAIL"].get("library"),
            after_instances["MNTAIL"].get("cell"),
        ) != (
            after_instances["MN0"].get("library"),
            after_instances["MN0"].get("cell"),
        ):
            raise RuntimeError("MNTAIL does not use the input NMOS master")

        before_pins = set(before_data.get("pins", []))
        after_pins = set(after_data.get("pins", []))
        before_nets = set(before_data.get("nets", []))
        after_nets = set(after_data.get("nets", []))
        if after_pins != before_pins | {"BIAS"}:
            raise RuntimeError("tail-device transform changed pins beyond adding BIAS")
        if after_nets != before_nets | {"BIAS"}:
            raise RuntimeError("tail-device transform changed nets beyond adding BIAS")

        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "tail-device transform is missing full instance parameter readback"
            )
        for name in sorted(core_names):
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    f"tail-device transform unexpectedly changed {name} parameters"
                )

        immutable_fields = (
            "library",
            "cell",
            "xy",
            "orient",
            "bBox",
            "numInst",
            "view",
        )
        for name in sorted(core_names):
            for field in immutable_fields:
                if before_instances[name].get(field) != after_instances[name].get(field):
                    raise RuntimeError(
                        f"tail-device transform unexpectedly changed {name}.{field}"
                    )

        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict):
            raise RuntimeError("tail-device transform is missing semantic OA readback")
        for name, expected in (
            ("tail_width_um", tail_width_um),
            ("tail_length_um", tail_length_um),
        ):
            if name not in semantic:
                raise RuntimeError(f"tail-device transform is missing {name}")
            actual = float(semantic[name])
            tolerance = max(abs(float(expected)) * 1e-6, 1e-9)
            if abs(actual - float(expected)) > tolerance:
                raise RuntimeError(f"MNTAIL {name} does not match the request")
        if after_data.get("topology_variant") != (
            "resistive_load_nmos_differential_pair_with_tail_device"
        ):
            raise RuntimeError("tail-device transform returned the wrong topology variant")

    @classmethod
    def _assert_differential_pair_source_degeneration_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        source_resistance_ohm: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        preserved_names = {"MN0", "MN1", "RD0", "RD1", "MNTAIL"}
        degenerated_names = preserved_names | {"RS0", "RS1"}
        before_names = set(before_instances)
        if frozenset(before_names) not in {
            frozenset(preserved_names),
            frozenset(degenerated_names),
        }:
            raise RuntimeError(
                "differential source-degeneration transform requires the exact "
                "real-tail differential-pair instance set"
            )
        if set(after_instances) != degenerated_names:
            raise RuntimeError(
                "differential source-degeneration transform did not produce exactly "
                "MN0/MN1/RD0/RD1/MNTAIL/RS0/RS1"
            )

        expected_static_terminals = {
            "RD0": {"PLUS": "VDD", "MINUS": "OUTP"},
            "RD1": {"PLUS": "VDD", "MINUS": "OUTN"},
            "MNTAIL": {"D": "TAIL", "G": "BIAS", "S": "VSS", "B": "VSS"},
        }
        for name, terminals in expected_static_terminals.items():
            if before_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"differential source-degeneration transform found unexpected "
                    f"{name} terminals"
                )
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"differential source-degeneration transform changed {name}"
                )
        before_sources = (
            {"MN0": "NSP", "MN1": "NSN"}
            if before_names == degenerated_names
            else {"MN0": "TAIL", "MN1": "TAIL"}
        )
        for name, drain, gate in (
            ("MN0", "OUTP", "INP"),
            ("MN1", "OUTN", "INN"),
        ):
            if before_instances[name].get("terminals") != {
                "D": drain,
                "G": gate,
                "S": before_sources[name],
                "B": "VSS",
            }:
                raise RuntimeError(
                    f"differential source-degeneration transform found unexpected "
                    f"{name} topology"
                )
            if after_instances[name].get("terminals") != {
                "D": drain,
                "G": gate,
                "S": "NSP" if name == "MN0" else "NSN",
                "B": "VSS",
            }:
                raise RuntimeError(
                    f"differential source-degeneration transform did not reconnect "
                    f"{name}.S"
                )
        expected_resistors = {
            "RS0": {"PLUS": "NSP", "MINUS": "TAIL"},
            "RS1": {"PLUS": "NSN", "MINUS": "TAIL"},
        }
        for name, terminals in expected_resistors.items():
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(f"{name} has unexpected differential degeneration wiring")
            if (
                after_instances[name].get("library"),
                after_instances[name].get("cell"),
            ) != ("analogLib", "res"):
                raise RuntimeError(f"{name} is not an analogLib resistor")

        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError(
                "differential source-degeneration transform changed top-level pins"
            )
        before_nets = set(before_data.get("nets", []))
        after_nets = set(after_data.get("nets", []))
        if after_nets != before_nets | {"NSP", "NSN"}:
            raise RuntimeError(
                "differential source-degeneration transform changed nets beyond "
                "adding NSP/NSN"
            )

        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "differential source-degeneration transform is missing complete "
                "instance parameter readback"
            )
        for name in sorted(preserved_names):
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    "differential source-degeneration transform unexpectedly changed "
                    f"{name} parameters"
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
        for name in sorted(preserved_names):
            for field in immutable_fields:
                if before_instances[name].get(field) != after_instances[name].get(field):
                    raise RuntimeError(
                        "differential source-degeneration transform unexpectedly "
                        f"changed {name}.{field}"
                    )
        if before_names == degenerated_names:
            for name in ("RS0", "RS1"):
                for field in immutable_fields[:-1]:
                    if before_instances[name].get(field) != after_instances[name].get(field):
                        raise RuntimeError(
                            "repeated differential source-degeneration transform "
                            f"changed {name}.{field}"
                        )

        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict) or "source_resistance_ohm" not in semantic:
            raise RuntimeError(
                "differential source resistance is missing from semantic OA readback"
            )
        actual = float(semantic["source_resistance_ohm"])
        tolerance = max(abs(float(source_resistance_ohm)) * 1e-6, 1e-9)
        if abs(actual - float(source_resistance_ohm)) > tolerance:
            raise RuntimeError(
                "RS0/RS1 resistance does not match the requested source degeneration"
            )
        if after_data.get("topology_variant") != (
            "resistive_load_nmos_differential_pair_with_tail_device_and_source_degeneration"
        ):
            raise RuntimeError(
                "differential source-degeneration transform returned the wrong variant"
            )

    @classmethod
    def _assert_differential_pair_source_degeneration_removal_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        restored_names = {"MN0", "MN1", "RD0", "RD1", "MNTAIL"}
        degenerated_names = restored_names | {"RS0", "RS1"}
        before_names = set(before_instances)
        if frozenset(before_names) not in {
            frozenset(restored_names),
            frozenset(degenerated_names),
        }:
            raise RuntimeError(
                "differential source-degeneration removal requires the exact "
                "real-tail or degenerated real-tail instance set"
            )
        if set(after_instances) != restored_names:
            raise RuntimeError(
                "differential source-degeneration removal did not restore exactly "
                "MN0/MN1/RD0/RD1/MNTAIL"
            )

        expected_static_terminals = {
            "RD0": {"PLUS": "VDD", "MINUS": "OUTP"},
            "RD1": {"PLUS": "VDD", "MINUS": "OUTN"},
            "MNTAIL": {"D": "TAIL", "G": "BIAS", "S": "VSS", "B": "VSS"},
        }
        for name, terminals in expected_static_terminals.items():
            if before_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"differential source-degeneration removal found unexpected "
                    f"{name} terminals"
                )
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(
                    f"differential source-degeneration removal changed {name}"
                )
        for name, drain, gate, degenerated_source in (
            ("MN0", "OUTP", "INP", "NSP"),
            ("MN1", "OUTN", "INN", "NSN"),
        ):
            before_source = (
                degenerated_source
                if before_names == degenerated_names
                else "TAIL"
            )
            if before_instances[name].get("terminals") != {
                "D": drain,
                "G": gate,
                "S": before_source,
                "B": "VSS",
            }:
                raise RuntimeError(
                    f"differential source-degeneration removal found unexpected "
                    f"{name} topology"
                )
            if after_instances[name].get("terminals") != {
                "D": drain,
                "G": gate,
                "S": "TAIL",
                "B": "VSS",
            }:
                raise RuntimeError(
                    f"differential source-degeneration removal did not restore "
                    f"{name}.S to TAIL"
                )
        if before_names == degenerated_names:
            if before_instances["RS0"].get("terminals") != {
                "PLUS": "NSP",
                "MINUS": "TAIL",
            } or before_instances["RS1"].get("terminals") != {
                "PLUS": "NSN",
                "MINUS": "TAIL",
            }:
                raise RuntimeError(
                    "differential source-degeneration removal found unexpected "
                    "RS0/RS1 wiring"
                )

        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError(
                "differential source-degeneration removal changed top-level pins"
            )
        expected_nets = set(before_data.get("nets", [])) - (
            {"NSP", "NSN"} if before_names == degenerated_names else set()
        )
        if set(after_data.get("nets", [])) != expected_nets:
            raise RuntimeError(
                "differential source-degeneration removal changed nets beyond "
                "removing NSP/NSN"
            )

        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "differential source-degeneration removal is missing complete "
                "instance parameter readback"
            )
        for name in sorted(restored_names):
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    "differential source-degeneration removal unexpectedly changed "
                    f"{name} parameters"
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
        for name in sorted(restored_names):
            for field in immutable_fields:
                if before_instances[name].get(field) != after_instances[name].get(field):
                    raise RuntimeError(
                        "differential source-degeneration removal unexpectedly "
                        f"changed {name}.{field}"
                    )
        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict):
            raise RuntimeError(
                "differential source-degeneration removal is missing semantic readback"
            )
        if "source_resistance_ohm" in semantic:
            raise RuntimeError(
                "differential source-degeneration removal left source resistance"
            )
        if after_data.get("topology_variant") != (
            "resistive_load_nmos_differential_pair_with_tail_device"
        ):
            raise RuntimeError(
                "differential source-degeneration removal returned the wrong variant"
            )

    @classmethod
    def _assert_differential_pair_current_mirror_load_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        pmos_load_width_um: float,
        pmos_load_length_um: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        preserved = {"MN0", "MN1", "MNTAIL"}
        resistive = preserved | {"RD0", "RD1"}
        active = preserved | {"MP0", "MP1"}
        if set(before_instances) not in (resistive, active):
            raise RuntimeError(
                "current-mirror-load transform requires the exact undegenerated "
                "real-tail differential-pair instance set"
            )
        if set(after_instances) != active:
            raise RuntimeError(
                "current-mirror-load transform did not produce exactly "
                "MN0/MN1/MNTAIL/MP0/MP1"
            )
        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError("current-mirror-load transform changed pins")
        if before_data.get("nets") != after_data.get("nets"):
            raise RuntimeError("current-mirror-load transform changed nets")
        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "current-mirror-load transform is missing instance parameter readback"
            )
        for name in sorted(preserved):
            if before_instances[name] != after_instances[name]:
                raise RuntimeError(
                    f"current-mirror-load transform changed preserved {name}"
                )
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    f"current-mirror-load transform changed {name} parameters"
                )
        expected_terminals = {
            "MP0": {"D": "OUTP", "G": "OUTP", "S": "VDD", "B": "VDD"},
            "MP1": {"D": "OUTN", "G": "OUTP", "S": "VDD", "B": "VDD"},
        }
        for name, terminals in expected_terminals.items():
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(f"{name} has unexpected current-mirror wiring")
        if set(before_instances) == active:
            immutable_fields = (
                "library",
                "cell",
                "xy",
                "orient",
                "bBox",
                "numInst",
                "view",
            )
            for name in ("MP0", "MP1"):
                for field in immutable_fields:
                    if before_instances[name].get(field) != after_instances[name].get(
                        field
                    ):
                        raise RuntimeError(
                            f"repeated current-mirror-load transform changed "
                            f"{name}.{field}"
                        )
        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict) or "load_resistance_ohm" in semantic:
            raise RuntimeError(
                "current-mirror-load transform returned invalid load semantics"
            )
        for name, expected in (
            ("pmos_load_width_um", pmos_load_width_um),
            ("pmos_load_length_um", pmos_load_length_um),
        ):
            if name not in semantic:
                raise RuntimeError(f"current-mirror-load readback is missing {name}")
            tolerance = max(abs(expected) * 1e-6, 1e-9)
            if abs(float(semantic[name]) - expected) > tolerance:
                raise RuntimeError(f"current-mirror-load {name} does not match")
        if after_data.get("topology_variant") != (
            "pmos_current_mirror_load_nmos_differential_pair_with_tail_device"
        ):
            raise RuntimeError("current-mirror-load transform returned wrong variant")

    @classmethod
    def _assert_differential_pair_resistive_load_restore_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        load_resistance_ohm: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        preserved = {"MN0", "MN1", "MNTAIL"}
        active = preserved | {"MP0", "MP1"}
        resistive = preserved | {"RD0", "RD1"}
        if set(before_instances) not in (active, resistive):
            raise RuntimeError(
                "resistive-load restore requires the exact active- or "
                "resistive-load real-tail instance set"
            )
        if set(after_instances) != resistive:
            raise RuntimeError(
                "resistive-load restore did not produce exactly "
                "MN0/MN1/MNTAIL/RD0/RD1"
            )
        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError("resistive-load restore changed pins")
        if before_data.get("nets") != after_data.get("nets"):
            raise RuntimeError("resistive-load restore changed nets")
        for name in sorted(preserved):
            if before_instances[name] != after_instances[name]:
                raise RuntimeError(f"resistive-load restore changed {name}")
        for name, terminals in {
            "RD0": {"PLUS": "VDD", "MINUS": "OUTP"},
            "RD1": {"PLUS": "VDD", "MINUS": "OUTN"},
        }.items():
            if after_instances[name].get("terminals") != terminals:
                raise RuntimeError(f"{name} has unexpected restored wiring")
        semantic = after_data.get("semantic_parameters")
        if (
            not isinstance(semantic, dict)
            or "load_resistance_ohm" not in semantic
            or "pmos_load_width_um" in semantic
            or "pmos_load_length_um" in semantic
        ):
            raise RuntimeError("resistive-load restore returned invalid semantics")
        tolerance = max(abs(load_resistance_ohm) * 1e-6, 1e-9)
        if (
            abs(float(semantic["load_resistance_ohm"]) - load_resistance_ohm)
            > tolerance
        ):
            raise RuntimeError("restored RD0/RD1 resistance does not match")
        if after_data.get("topology_variant") != (
            "resistive_load_nmos_differential_pair_with_tail_device"
        ):
            raise RuntimeError("resistive-load restore returned wrong variant")

    @classmethod
    def _assert_source_degeneration_removal_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        base_names = {"MN0", "RD0"}
        degenerated_names = base_names | {"RS0"}
        before_names = set(before_instances)
        if frozenset(before_names) not in {
            frozenset(base_names),
            frozenset(degenerated_names),
        }:
            raise RuntimeError(
                "source-degeneration removal requires the exact VDA common-source "
                f"instance set; got {sorted(before_names)}"
            )
        if set(after_instances) != base_names:
            raise RuntimeError(
                "source-degeneration removal did not restore exactly MN0/RD0"
            )

        expected_before_mn_terms = {
            "D": "OUT",
            "G": "IN",
            "S": "NSRC" if before_names == degenerated_names else "VSS",
            "B": "VSS",
        }
        expected_after_mn_terms = {
            "D": "OUT",
            "G": "IN",
            "S": "VSS",
            "B": "VSS",
        }
        if before_instances["MN0"].get("terminals") != expected_before_mn_terms:
            raise RuntimeError(
                "source-degeneration removal found an unexpected MN0 topology"
            )
        if before_instances["RD0"].get("terminals") != {
            "PLUS": "VDD",
            "MINUS": "OUT",
        }:
            raise RuntimeError(
                "source-degeneration removal found an unexpected RD0 topology"
            )
        if (
            before_names == degenerated_names
            and before_instances["RS0"].get("terminals")
            != {"PLUS": "NSRC", "MINUS": "VSS"}
        ):
            raise RuntimeError(
                "source-degeneration removal found an unexpected RS0 topology"
            )
        if after_instances["MN0"].get("terminals") != expected_after_mn_terms:
            raise RuntimeError(
                "source-degeneration removal did not restore MN0.S to VSS"
            )
        if after_instances["RD0"].get("terminals") != {
            "PLUS": "VDD",
            "MINUS": "OUT",
        }:
            raise RuntimeError("source-degeneration removal changed RD0 topology")

        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError(
                "source-degeneration removal unexpectedly changed pins"
            )
        before_nets = set(before_data.get("nets", []))
        after_nets = set(after_data.get("nets", []))
        expected_after_nets = before_nets - (
            {"NSRC"} if before_names == degenerated_names else set()
        )
        if after_nets != expected_after_nets:
            raise RuntimeError(
                "source-degeneration removal changed nets beyond removing NSRC"
            )

        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "source-degeneration removal is missing full instance parameter "
                "readback"
            )
        if set(before_parameters) != before_names or set(after_parameters) != base_names:
            raise RuntimeError(
                "source-degeneration removal returned inconsistent instance "
                "parameter readback"
            )
        for name in sorted(base_names):
            if before_parameters[name] != after_parameters[name]:
                raise RuntimeError(
                    "source-degeneration removal unexpectedly changed "
                    f"{name} parameters"
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
                        "source-degeneration removal unexpectedly changed "
                        f"{name}.{field}"
                    )

        semantic = after_data.get("semantic_parameters")
        if not isinstance(semantic, dict):
            raise RuntimeError(
                "source-degeneration removal is missing semantic OA readback"
            )
        if "source_resistance_ohm" in semantic:
            raise RuntimeError(
                "source-degeneration removal left source_resistance_ohm in OA readback"
            )

    @classmethod
    def _assert_inverter_testbench_delta(
        cls,
        before: AdapterResult,
        after: AdapterResult,
        vdd_v: float,
        load_ff: float,
    ) -> None:
        before_data = before.data
        after_data = after.data
        before_instances = cls._instances_by_name(before_data)
        after_instances = cls._instances_by_name(after_data)
        core_names = {"MN0", "MP0"}
        testbench_names = core_names | {"VDD0", "VIN0", "CL0", "GND0"}
        before_names = frozenset(before_instances)
        if before_names not in {frozenset(core_names), frozenset(testbench_names)}:
            raise RuntimeError(
                "inverter-testbench transform requires the exact inverter core or "
                "testbench instance set"
            )
        if set(after_instances) != testbench_names:
            raise RuntimeError(
                "inverter-testbench transform did not produce exactly "
                "MN0/MP0/VDD0/VIN0/CL0/GND0"
            )
        expected_terminals = {
            "MN0": {"D": "OUT", "G": "IN", "S": "gnd!", "B": "gnd!"},
            "MP0": {"D": "OUT", "G": "IN", "S": "VDD", "B": "VDD"},
            "VDD0": {"PLUS": "VDD", "MINUS": "gnd!"},
            "VIN0": {"PLUS": "IN", "MINUS": "gnd!"},
            "CL0": {"PLUS": "OUT", "MINUS": "gnd!"},
            "GND0": {"gnd!": "gnd!"},
        }
        if any(
            after_instances[name].get("terminals") != terminals
            for name, terminals in expected_terminals.items()
        ):
            raise RuntimeError(
                "inverter-testbench transform produced unexpected terminal nets"
            )
        expected_masters = {
            "VDD0": ("analogLib", "vdc"),
            "VIN0": ("analogLib", "vpulse"),
            "CL0": ("analogLib", "cap"),
            "GND0": ("analogLib", "gnd"),
        }
        for name, expected in expected_masters.items():
            actual = (
                after_instances[name].get("library"),
                after_instances[name].get("cell"),
            )
            if actual != expected:
                raise RuntimeError(
                    f"inverter-testbench transform used unexpected {name} master"
                )
        if before_data.get("pins") != after_data.get("pins"):
            raise RuntimeError("inverter-testbench transform unexpectedly changed pins")
        before_nets = set(before_data.get("nets", []))
        after_nets = set(after_data.get("nets", []))
        expected_nets = before_nets | (
            {"gnd!"} if before_names == frozenset(core_names) else set()
        )
        if after_nets != expected_nets:
            raise RuntimeError(
                "inverter-testbench transform changed nets beyond adding gnd!"
            )
        before_parameters = before_data.get("instance_parameters")
        after_parameters = after_data.get("instance_parameters")
        if not isinstance(before_parameters, dict) or not isinstance(
            after_parameters, dict
        ):
            raise RuntimeError(
                "inverter-testbench transform is missing full parameter readback"
            )
        for name in sorted(core_names):
            if before_parameters.get(name) != after_parameters.get(name):
                raise RuntimeError(
                    f"inverter-testbench transform changed {name} parameters"
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
        for name in sorted(core_names):
            for field in immutable_fields:
                if before_instances[name].get(field) != after_instances[name].get(field):
                    raise RuntimeError(
                        f"inverter-testbench transform changed {name}.{field}"
                    )
        expected_parameters = {
            "VDD0": {"vdc": f"{vdd_v:.12g}", "srcType": "dc"},
            "VIN0": {
                "v1": "0",
                "v2": f"{vdd_v:.12g}",
                "per": "100p",
                "td": "0",
                "tr": "5p",
                "tf": "5p",
                "pw": "50p",
                "srcType": "pulse",
            },
            "CL0": {"c": f"{load_ff:.12g}f"},
        }
        for instance, parameters in expected_parameters.items():
            actual_parameters = after_parameters.get(instance)
            if not isinstance(actual_parameters, dict):
                raise RuntimeError(
                    f"inverter-testbench transform omitted {instance} parameters"
                )
            for name, expected in parameters.items():
                actual = actual_parameters.get(name)
                if actual is None or not spectre_values_equal(actual, expected):
                    raise RuntimeError(
                        f"inverter-testbench transform did not confirm "
                        f"{instance}.{name}"
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
    def _checkpoint_applied_candidate_matches(
        declared: dict[str, float],
        actual: dict[str, float],
        applied_oa: dict[str, float],
    ) -> bool:
        if not declared.keys() <= actual.keys():
            return False
        if not actual.keys() <= declared.keys() | applied_oa.keys():
            return False
        expected = dict(declared)
        expected.update(applied_oa)
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

    @staticmethod
    def _candidate_task(
        task: TaskSpec,
        instance_parameters: dict[str, dict[str, str]],
        testbench_overrides: GenericTestbenchOverrides | None = None,
    ) -> TaskSpec:
        updates = [
            InstanceParameterUpdate(instance=instance, parameters=parameters)
            for instance, parameters in sorted(instance_parameters.items())
        ]
        generic_simulation = task.generic_simulation
        if testbench_overrides is not None:
            if generic_simulation is None:
                raise ValueError(
                    "candidate testbench overrides require generic_simulation"
                )
            generic_simulation = testbench_overrides.apply_to(generic_simulation)
        return task.model_copy(
            update={
                "instance_parameter_updates": updates,
                "generic_simulation": generic_simulation,
                # Candidate enumeration belongs to VDA.  The Bridge receives only
                # the exact point that it must apply/read back for this action.
                "instance_parameter_space": [],
                "candidate_set": None,
                "theory_seed": None,
            }
        )

    @staticmethod
    def _instance_parameter_targets(
        task: TaskSpec,
    ) -> dict[str, set[str]]:
        targets: dict[str, set[str]] = {}
        for update in task.instance_parameter_updates:
            targets.setdefault(update.instance, set()).update(update.parameters)
        for sweep in task.instance_parameter_space:
            targets.setdefault(sweep.instance, set()).add(sweep.parameter)
        if task.candidate_set is not None:
            for update in task.candidate_set.candidates[0].instance_parameter_updates:
                targets.setdefault(update.instance, set()).update(update.parameters)
        return targets

    @classmethod
    def _targeted_instance_parameters(
        cls,
        result: AdapterResult,
        task: TaskSpec,
    ) -> dict[str, dict[str, str]]:
        targets = cls._instance_parameter_targets(task)
        if not targets:
            return {}
        raw = result.data.get("instance_parameters")
        if not isinstance(raw, dict):
            raise RuntimeError(
                "schematic inspection did not return full instance parameters"
            )
        selected: dict[str, dict[str, str]] = {}
        for instance, names in targets.items():
            parameters = raw.get(instance)
            if not isinstance(parameters, dict):
                raise RuntimeError(
                    f"schematic inspection is missing instance {instance}"
                )
            missing = sorted(name for name in names if name not in parameters)
            if missing:
                raise RuntimeError(
                    "instance parameter search requires names from unfiltered OA "
                    f"readback; {instance} is missing {', '.join(missing)}"
                )
            selected[instance] = {
                name: str(parameters[name]) for name in sorted(names)
            }
        return selected

    @classmethod
    def _applied_instance_parameter_state(
        cls,
        result: AdapterResult,
        task: TaskSpec,
    ) -> dict[str, dict[str, str]]:
        requested = cls._requested_instance_parameters(task)
        if not requested:
            return {}
        echoed = cls._confirmed_instance_parameters(
            result.data, "requested_instance_parameters"
        )
        cls._assert_explicit_parameter_confirmation(requested, echoed)
        applied = cls._confirmed_instance_parameters(
            result.data, "applied_instance_parameters"
        )
        confirmed = cls._confirmed_instance_parameters(
            result.data, "confirmed_instance_parameters"
        )
        cls._assert_explicit_parameter_confirmation(applied, confirmed)
        return confirmed

    @staticmethod
    def _same_instance_parameters(
        expected: dict[str, dict[str, str]],
        actual: dict[str, dict[str, str]],
    ) -> bool:
        return expected == actual

    @staticmethod
    def _is_ade_output_evaluation_error(value: Any) -> bool:
        normalized = " ".join(str(value or "").strip().lower().split())
        return normalized in {"eval err", "evaluation error", "error"}

    @staticmethod
    def _assert_ade_sweep_evidence(task: TaskSpec, data: dict[str, Any]) -> None:
        assert task.ade_run is not None
        sweep = task.ade_run.sweep_verification
        if sweep is None:
            return
        expected_variables = {
            variable.evidence_key(): variable.expected_value
            for variable in sweep.variables
        }
        point_sweep_names = {
            variable.name for variable in sweep.variables if variable.sweep
        }
        effective_variable_names = set(sweep.effective_variable_names())
        expected_methods = {
            variable.evidence_key(): (
                "bridge_public_get_var"
                if variable.scope.value == "global"
                else (
                    "cadence_maeGetVar_string_typeValue_via_bridge_skill_channel"
                    if variable.scope.value == "test"
                    else (
                        "cadence_axlGetCorner_axlGetVarValue_"
                        "via_bridge_skill_channel"
                    )
                )
            )
            for variable in sweep.variables
        }
        expected_selections = dict(sweep.expected_global_variable_selections)
        setup_readbacks: list[dict[str, Any]] = []
        for field in (
            "sweep_setup_readback_before",
            "sweep_setup_readback_after",
        ):
            readback = data.get(field)
            if not isinstance(readback, dict):
                raise RuntimeError(f"ADE sweep evidence lacked {field}")
            if (
                readback.get("tests") != list(sweep.expected_tests)
                or readback.get("corners")
                != (
                    None
                    if sweep.expected_corners is None
                    else list(sweep.expected_corners)
                )
                or readback.get("variables") != expected_variables
                or readback.get("variable_readback_methods") != expected_methods
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(readback.get("fingerprint_sha256") or ""),
                )
            ):
                raise RuntimeError(
                    "ADE sweep setup readback did not match the declared exact "
                    "tests, corners, and variable scopes"
                )
            if expected_selections:
                selection_state = readback.get(
                    "global_variable_selection_state"
                )
                enabled_names = (
                    selection_state.get("enabled")
                    if isinstance(selection_state, dict)
                    else None
                )
                disabled_names = (
                    selection_state.get("disabled")
                    if isinstance(selection_state, dict)
                    else None
                )
                valid_selection_lists = (
                    isinstance(enabled_names, list)
                    and isinstance(disabled_names, list)
                    and all(
                        isinstance(name, str) and name
                        for name in [*enabled_names, *disabled_names]
                    )
                    and len(enabled_names) == len(set(enabled_names))
                    and len(disabled_names) == len(set(disabled_names))
                    and not set(enabled_names) & set(disabled_names)
                )
                selection_values_match = valid_selection_lists and all(
                    (name in enabled_names) is expected_enabled
                    and ((name in enabled_names) + (name in disabled_names) == 1)
                    for name, expected_enabled in expected_selections.items()
                )
                if (
                    readback.get("global_variable_selections")
                    != expected_selections
                    or not selection_values_match
                    or readback.get(
                        "global_variable_selection_readback_method"
                    )
                    != (
                        "cadence_maeGetSetup_enabled_variables_"
                        "via_bridge_skill_channel"
                    )
                ):
                    raise RuntimeError(
                        "ADE sweep setup readback did not match the declared "
                        "global-variable selections"
                    )
            elif any(
                field in readback
                for field in (
                    "global_variable_selections",
                    "global_variable_selection_state",
                    "global_variable_selection_readback_method",
                )
            ):
                raise RuntimeError(
                    "ADE sweep setup readback exposed undeclared global-variable "
                    "selection evidence"
                )
            setup_readbacks.append(readback)
        if setup_readbacks[0] != setup_readbacks[1]:
            raise RuntimeError("ADE sweep setup changed during background execution")
        exact_point_mode = (
            data.get("exact_point_input_result_binding_verified") is True
        )
        database_mode = data.get("native_sweep_database_binding_verified") is True
        expected_mode_name = (
            "maestro_exact_history_rdb_with_shared_symbolic_runtime_input"
            if database_mode
            else "exact_point_artifacts"
        )
        if (
            data.get("sweep_setup_readback_evidence_source") != "bridge_readback"
            or data.get("expected_sweep_evidence_source") != "user_input"
            or data.get("sweep_point_consistency_verified") is not True
            or data.get("effective_simulation_values_verified") is not True
            or exact_point_mode == database_mode
            or data.get("sweep_point_evidence_mode") != expected_mode_name
            or data.get("sweep_consistency_evidence_sources")
            != {
                "expected_sweep": "user_input",
                "maestro_setup_and_oa": "bridge_readback",
                "spectre_input_and_results": "eda_result",
                "comparison": "software_inference",
            }
        ):
            raise RuntimeError(
                "ADE sweep evidence did not preserve its setup/input/result sources"
            )
        if sweep.corner_mode():
            if (
                not database_mode
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(data.get("corner_detail_csv_sha256") or ""),
                )
                or int(data.get("corner_detail_csv_size_bytes") or 0) <= 0
                or data.get("corner_detail_csv_evidence_sources")
                != {"raw": "eda_result", "parser": "software_inference"}
            ):
                raise RuntimeError(
                    "ADE corner sweep lacked hashed raw-Detail CSV evidence"
                )
        elif any(
            data.get(field) is not None
            for field in (
                "corner_detail_csv_sha256",
                "corner_detail_csv_size_bytes",
                "corner_detail_csv_evidence_sources",
            )
        ):
            raise RuntimeError("ordinary ADE sweep mixed corner-CSV evidence")

        raw_points = data.get("sweep_point_consistency")
        if not isinstance(raw_points, list) or len(raw_points) != len(sweep.points):
            raise RuntimeError("ADE sweep evidence did not cover every declared point")
        manifest = data.get("artifact_manifest")
        if not isinstance(manifest, list):
            raise RuntimeError("ADE sweep evidence lacked the artifact manifest")
        manifest_by_path = {
            str(item.get("path")): item
            for item in manifest
            if isinstance(item, dict) and item.get("path")
        }
        history = str(data.get("history") or "")
        raw_input_consistency = data.get("simulator_input_consistency")
        if not isinstance(raw_input_consistency, list) or not raw_input_consistency:
            raise RuntimeError("ADE sweep evidence lacked OA/input comparisons")
        binding_counts = {
            test: sum(
                1 for binding in sweep.input_bindings if binding.test == test
            )
            for test in sweep.expected_tests
        }
        expected_points = {point.point: point for point in sweep.points}
        expected_error_cells: dict[tuple[int, str], dict[str, Any]] = {}
        for expectation in sweep.expected_output_evaluation_errors:
            for point in sweep.points:
                if all(
                    point.values[name] == value
                    for name, value in expectation.point_values.items()
                ):
                    expected_error_cells[(point.point, expectation.output)] = {
                        "point": point.point,
                        "test": expectation.test,
                        "output": expectation.output,
                        "point_values": dict(expectation.point_values),
                    }
        raw_error_rows = data.get("output_evaluation_errors")
        expected_sources = {
            "expected": "user_input" if expected_error_cells else None,
            "actual": "eda_result",
            "comparison": "software_inference",
        }
        if (
            data.get("expected_output_evaluation_errors_verified") is not True
            or data.get("output_evaluation_error_count")
            != len(expected_error_cells)
            or data.get("output_evaluation_error_evidence_sources")
            != expected_sources
            or not isinstance(raw_error_rows, list)
            or len(raw_error_rows) != len(expected_error_cells)
        ):
            raise RuntimeError(
                "ADE sweep output evaluation-error evidence was incomplete"
            )
        error_rows: dict[tuple[int, str], dict[str, Any]] = {}
        for row in raw_error_rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "ADE sweep output evaluation-error evidence was malformed"
                )
            try:
                identity = (int(row.get("point")), str(row.get("output") or ""))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "ADE sweep output evaluation-error point was invalid"
                ) from exc
            expected_row = expected_error_cells.get(identity)
            if (
                expected_row is None
                or identity in error_rows
                or row.get("test") != expected_row["test"]
                or row.get("point_values") != expected_row["point_values"]
                or row.get("expectation_evidence_source") != "user_input"
                or row.get("raw_evidence_source") != "eda_result"
                or not TaskExecutor._is_ade_output_evaluation_error(
                    row.get("raw_value")
                )
            ):
                raise RuntimeError(
                    "ADE sweep output evaluation-error evidence did not match the "
                    "declared point/output cells"
                )
            error_rows[identity] = row
        if set(error_rows) != set(expected_error_cells):
            raise RuntimeError(
                "ADE sweep output evaluation-error evidence omitted a declared cell"
            )
        actual_error_cells: dict[tuple[int, str], str] = {}
        for point_evidence in raw_points:
            if not isinstance(point_evidence, dict):
                continue
            try:
                point_number = int(point_evidence.get("point"))
            except (TypeError, ValueError):
                continue
            scalar_outputs = point_evidence.get("scalar_outputs")
            if not isinstance(scalar_outputs, dict):
                continue
            for output, value in scalar_outputs.items():
                if TaskExecutor._is_ade_output_evaluation_error(value):
                    actual_error_cells[(point_number, str(output))] = str(value)
        if set(actual_error_cells) != set(expected_error_cells) or any(
            actual_error_cells[identity] != str(error_rows[identity]["raw_value"])
            for identity in expected_error_cells
        ):
            raise RuntimeError(
                "ADE sweep trusted scalar outputs did not exactly preserve the "
                "declared output evaluation errors"
            )
        input_consistency_by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
        for item in raw_input_consistency:
            if not isinstance(item, dict):
                raise RuntimeError("ADE sweep OA/input comparison is not an object")
            if exact_point_mode:
                try:
                    item_point = int(item.get("point"))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "ADE sweep OA/input comparison has an invalid point"
                    ) from exc
            else:
                item_point = 0
            item_test = str(item.get("test") or "")
            item_path = str(item.get("input_path") or "")
            key = (item_point, item_test, item_path)
            if (
                item_test not in binding_counts
                or not item_path
                or key in input_consistency_by_key
                or (
                    exact_point_mode
                    and item.get("effective_sweep_bindings_verified") is not True
                )
                or (
                    database_mode
                    and item.get("symbolic_sweep_bindings_verified") is not True
                )
                or item.get("verified_sweep_binding_pairs")
                != binding_counts[item_test]
            ):
                raise RuntimeError(
                    "ADE sweep OA/input comparison did not uniquely verify every "
                    "declared binding"
                )
            if database_mode:
                retained_values = item.get("retained_sweep_values")
                if not isinstance(retained_values, dict) or set(
                    retained_values
                ) != effective_variable_names:
                    raise RuntimeError(
                        "ADE sweep symbolic input retained values did not match one "
                        "declared point"
                    )
                if sweep.corner_mode():
                    try:
                        retained_maestro_point = int(
                            item.get("retained_maestro_point")
                        )
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "ADE corner sweep symbolic input has an invalid retained "
                            "Maestro point"
                        ) from exc
                    retained_group = [
                        point
                        for point in sweep.points
                        if point.maestro_point == retained_maestro_point
                    ]
                    if not retained_group:
                        raise RuntimeError(
                            "ADE corner sweep symbolic input retained an undeclared "
                            "Maestro point"
                        )
                    retained_matches = all(
                        (
                            all(
                                spectre_values_equal(
                                    retained_values[name], point.values[name]
                                )
                                for point in retained_group
                            )
                            if name in point_sweep_names
                            else any(
                                spectre_values_equal(
                                    retained_values[name], point.values[name]
                                )
                                for point in retained_group
                            )
                        )
                        for name in effective_variable_names
                    )
                else:
                    try:
                        retained_point = int(item.get("retained_point"))
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "ADE sweep symbolic input has an invalid retained point"
                        ) from exc
                    expected_retained = expected_points.get(retained_point)
                    retained_matches = expected_retained is not None and all(
                        spectre_values_equal(
                            retained_values[name], expected_value
                        )
                        for name, expected_value in (
                            expected_retained.values.items()
                            if expected_retained is not None
                            else []
                        )
                    )
                if not retained_matches:
                    raise RuntimeError(
                        "ADE sweep symbolic input retained values did not match one "
                        "declared point"
                    )
            input_consistency_by_key[key] = item
        database_artifacts_by_path: dict[str, dict[str, Any]] = {}
        if database_mode:
            raw_database_artifacts = data.get("sweep_result_database_artifacts")
            if (
                not isinstance(raw_database_artifacts, list)
                or len(raw_database_artifacts) != 1
            ):
                raise RuntimeError(
                    "ADE sweep database mode requires one exact-history RDB "
                    "artifact"
                )
            for artifact in raw_database_artifacts:
                if not isinstance(artifact, dict):
                    raise RuntimeError("ADE sweep database artifact is malformed")
                artifact_path = str(artifact.get("path") or "")
                manifest_item = manifest_by_path.get(artifact_path)
                if (
                    artifact_path != f"{history}/{history}.rdb"
                    or not isinstance(manifest_item, dict)
                    or manifest_item.get("category") != "eda_result"
                    or manifest_item.get("binding")
                    != "exact_history_companion"
                    or manifest_item.get("sha256") != artifact.get("sha256")
                    or manifest_item.get("size_bytes")
                    != artifact.get("size_bytes")
                    or int(artifact.get("size_bytes") or 0) <= 0
                ):
                    raise RuntimeError(
                        "ADE sweep database artifact did not match the exact-history "
                        "manifest"
                    )
                database_artifacts_by_path[artifact_path] = artifact

            history_log = data.get("sweep_history_log_evidence")
            if not isinstance(history_log, dict):
                raise RuntimeError("ADE sweep database mode lacked history log evidence")
            history_log_path = str(history_log.get("path") or "")
            manifest_log = manifest_by_path.get(history_log_path)
            if (
                history_log_path != f"{history}/{history}.log"
                or not isinstance(manifest_log, dict)
                or manifest_log.get("category") != "run_log"
                or manifest_log.get("binding") != "exact_history_companion"
                or manifest_log.get("sha256") != history_log.get("sha256")
                or manifest_log.get("size_bytes") != history_log.get("size_bytes")
                or int(history_log.get("size_bytes") or 0) <= 0
                or history_log.get("points_completed") != sweep.maestro_point_count()
                or history_log.get("simulation_errors")
                != len(expected_error_cells)
                or history_log.get(
                    "simulation_errors_accounted_by_output_evaluation_errors"
                )
                != len(expected_error_cells)
                or history_log.get("unaccounted_simulation_errors") != 0
                or history_log.get("history_completed") is not True
            ):
                raise RuntimeError(
                    "ADE sweep history log did not prove the declared completed "
                    "point count without unaccounted simulation errors"
                )
        elif (
            data.get("sweep_history_log_evidence") is not None
            or data.get("sweep_result_database_artifacts") not in (None, [])
        ):
            raise RuntimeError(
                "ADE exact-point sweep evidence unexpectedly mixed database mode "
                "artifacts"
            )
        seen_points: set[int] = set()
        seen_input_consistency: set[tuple[int, str, str]] = set()
        for point_evidence in raw_points:
            if not isinstance(point_evidence, dict):
                raise RuntimeError("ADE sweep point evidence is not an object")
            try:
                point_number = int(point_evidence.get("point"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("ADE sweep point evidence has an invalid index") from exc
            expected_point = expected_points.get(point_number)
            if expected_point is None or point_number in seen_points:
                raise RuntimeError("ADE sweep point evidence repeated an unknown point")
            seen_points.add(point_number)
            tests = point_evidence.get("tests")
            scalar_outputs = point_evidence.get("scalar_outputs")
            result_parameters = point_evidence.get("result_parameters")
            if (
                point_evidence.get("expected_parameters") != expected_point.values
                or point_evidence.get("maestro_point")
                != expected_point.maestro_point
                or point_evidence.get("corner") != expected_point.corner
                or not isinstance(result_parameters, dict)
                or not isinstance(scalar_outputs, dict)
                or not scalar_outputs
                or any(not str(value).strip() for value in scalar_outputs.values())
                or not isinstance(tests, list)
                or [item.get("test") for item in tests if isinstance(item, dict)]
                != list(sweep.expected_tests)
            ):
                raise RuntimeError(
                    f"ADE sweep point {point_number} lacked declared parameters, "
                    "tests, or non-empty scalar outputs"
                )
            for name, expected_value in expected_point.values.items():
                result_value = result_parameters.get(name)
                if result_value is None or not spectre_values_equal(
                    result_value, expected_value
                ):
                    raise RuntimeError(
                        f"ADE sweep point {point_number} result parameter {name} "
                        "did not match the declared effective value"
                    )
            payload = {
                "point": point_number,
                "expected_parameters": point_evidence["expected_parameters"],
                "result_parameters": point_evidence["result_parameters"],
                "scalar_outputs": scalar_outputs,
                "tests": tests,
            }
            if sweep.corner_mode():
                payload.update(
                    maestro_point=point_evidence.get("maestro_point"),
                    corner=point_evidence.get("corner"),
                )
            expected_hash = hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if point_evidence.get("point_binding_sha256") != expected_hash:
                raise RuntimeError(
                    f"ADE sweep point {point_number} binding fingerprint mismatch"
                )
            for test_evidence in tests:
                if not isinstance(test_evidence, dict):
                    raise RuntimeError(
                        f"ADE sweep point {point_number} test evidence is malformed"
                    )
                test_name = str(test_evidence.get("test") or "")
                inputs = test_evidence.get("inputs")
                results = test_evidence.get("result_artifacts")
                if database_mode and test_evidence.get("evidence_mode") != (
                    "maestro_exact_history_rdb_with_shared_symbolic_runtime_input"
                ):
                    raise RuntimeError(
                        f"ADE sweep point {point_number} did not declare its "
                        "database evidence mode"
                    )
                if exact_point_mode and test_evidence.get("evidence_mode") not in (
                    None,
                    "exact_point_artifacts",
                ):
                    raise RuntimeError(
                        f"ADE sweep point {point_number} mixed point and database "
                        "evidence modes"
                    )
                if not isinstance(inputs, list) or not inputs:
                    raise RuntimeError(
                        f"ADE sweep point {point_number} lacked input evidence"
                    )
                if not isinstance(results, list) or not results:
                    raise RuntimeError(
                        f"ADE sweep point {point_number} lacked result evidence"
                    )
                if database_mode and (
                    len(inputs) != 1
                    or len(results) != len(database_artifacts_by_path)
                    or {
                        str(item.get("path") or "")
                        for item in results
                        if isinstance(item, dict)
                    }
                    != set(database_artifacts_by_path)
                ):
                    raise RuntimeError(
                        f"ADE sweep point {point_number} did not reference exactly "
                        "one shared runtime input and the declared history RDB"
                    )
                for input_item in inputs:
                    if not isinstance(input_item, dict):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} input is malformed"
                        )
                    input_path = str(input_item.get("path") or "")
                    manifest_item = manifest_by_path.get(input_path)
                    comparison_key = (
                        point_number if exact_point_mode else 0,
                        test_name,
                        input_path,
                    )
                    comparison = input_consistency_by_key.get(
                        comparison_key
                    )
                    test_token = re.sub(r"[^A-Za-z0-9_.-]", "_", test_name)
                    included_netlist_path = str(
                        input_item.get("included_netlist_path") or ""
                    )
                    included_netlist_sha256 = str(
                        input_item.get("included_netlist_sha256") or ""
                    )
                    included_manifest = manifest_by_path.get(
                        included_netlist_path
                    )
                    expected_bundle_sha256 = hashlib.sha256(
                        json.dumps(
                            {
                                "input.scs": str(input_item.get("sha256") or ""),
                                "netlist": included_netlist_sha256,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if exact_point_mode:
                        binding_valid = (
                            isinstance(manifest_item, dict)
                            and manifest_item.get("binding")
                            == "exact_history_path"
                            and input_path.startswith(
                                f"{history}/{point_number}/"
                            )
                        )
                    else:
                        binding_valid = (
                            isinstance(manifest_item, dict)
                            and manifest_item.get("binding")
                            == "unique_runtime_session"
                            and input_path.startswith(
                                f"{history}/runtime/{test_token}/"
                            )
                            and input_path.endswith("/input.scs")
                            and included_netlist_path
                            == input_path.removesuffix("input.scs") + "netlist"
                            and isinstance(included_manifest, dict)
                            and included_manifest.get("category")
                            == "simulator_input"
                            and included_manifest.get("binding")
                            == "unique_runtime_session"
                            and included_manifest.get("sha256")
                            == included_netlist_sha256
                            and int(included_manifest.get("size_bytes") or 0) > 0
                            and input_item.get("input_bundle_sha256")
                            == expected_bundle_sha256
                        )
                    if (
                        not isinstance(manifest_item, dict)
                        or manifest_item.get("category") != "simulator_input"
                        or not binding_valid
                        or manifest_item.get("sha256") != input_item.get("sha256")
                        or int(manifest_item.get("size_bytes") or 0) <= 0
                        or not re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(input_item.get("comparison_sha256") or ""),
                        )
                        or not isinstance(comparison, dict)
                        or comparison.get("input_sha256")
                        != input_item.get("sha256")
                        or comparison.get("comparison_sha256")
                        != input_item.get("comparison_sha256")
                        or (
                            database_mode
                            and (
                                comparison.get("included_netlist_path")
                                != included_netlist_path
                                or comparison.get("included_netlist_sha256")
                                != included_netlist_sha256
                                or comparison.get("input_bundle_sha256")
                                != expected_bundle_sha256
                            )
                        )
                    ):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} input did not match "
                            "the declared artifact mode and OA/input comparison"
                        )
                    seen_input_consistency.add(comparison_key)
                for result_item in results:
                    if not isinstance(result_item, dict):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} result is malformed"
                        )
                    result_path = str(result_item.get("path") or "")
                    manifest_item = manifest_by_path.get(result_path)
                    if exact_point_mode:
                        result_binding_valid = (
                            isinstance(manifest_item, dict)
                            and manifest_item.get("binding")
                            == "exact_history_path"
                            and result_path.startswith(
                                f"{history}/{point_number}/"
                            )
                        )
                    else:
                        expected_database_artifact = (
                            database_artifacts_by_path.get(result_path)
                        )
                        result_binding_valid = (
                            isinstance(manifest_item, dict)
                            and manifest_item.get("binding")
                            == "exact_history_companion"
                            and expected_database_artifact == result_item
                        )
                    if (
                        not isinstance(manifest_item, dict)
                        or manifest_item.get("category") != "eda_result"
                        or not result_binding_valid
                        or manifest_item.get("sha256") != result_item.get("sha256")
                        or manifest_item.get("size_bytes")
                        != result_item.get("size_bytes")
                        or int(result_item.get("size_bytes") or 0) <= 0
                    ):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} result did not match "
                            "the declared exact-history artifact mode"
                        )
        if seen_points != set(expected_points):
            raise RuntimeError("ADE sweep evidence omitted a declared point")
        if seen_input_consistency != set(input_consistency_by_key):
            raise RuntimeError(
                "ADE sweep OA/input comparisons did not map one-to-one to point inputs"
            )

    @classmethod
    def _evaluate_ade_result_mapping(
        cls, task: TaskSpec, data: dict[str, Any]
    ) -> tuple[list[CandidateEvaluation], dict[str, Any]]:
        assert task.ade_run is not None
        mapping = task.ade_run.result_mapping
        sweep = task.ade_run.sweep_verification
        if mapping is None or sweep is None:
            raise RuntimeError("ADE result mapping contract disappeared at execution")

        setup_readbacks: list[dict[str, Any]] = []
        for field in (
            "result_mapping_setup_readback_before",
            "result_mapping_setup_readback_after",
        ):
            readback = data.get(field)
            if not isinstance(readback, dict):
                raise RuntimeError(f"ADE result mapping lacked {field}")
            outputs = readback.get("outputs")
            fingerprint = str(readback.get("fingerprint_sha256") or "")
            if not isinstance(outputs, list) or len(outputs) != len(mapping.metrics):
                raise RuntimeError("ADE result mapping output setup was incomplete")
            for binding, row in zip(mapping.metrics, outputs, strict=True):
                state = row.get("state") if isinstance(row, dict) else None
                if (
                    not isinstance(row, dict)
                    or row.get("test") != binding.test
                    or row.get("output") != binding.output
                    or row.get("metric") != binding.metric
                    or not isinstance(state, dict)
                    or binding.output != state.get("name")
                    or "point" not in {state.get("type"), state.get("eval_type")}
                    or state.get("signal_name") is not None
                    or not calculator_expressions_equal(
                        binding.expected_expression, state.get("expression")
                    )
                ):
                    raise RuntimeError(
                        "ADE result mapping output expression did not match the "
                        "declared saved setup"
                    )
            expected_fingerprint = hashlib.sha256(
                json.dumps(
                    outputs,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if fingerprint != expected_fingerprint:
                raise RuntimeError("ADE result mapping setup fingerprint mismatch")
            setup_readbacks.append(readback)
        if (
            setup_readbacks[0] != setup_readbacks[1]
            or data.get("result_mapping_setup_unchanged") is not True
            or data.get("result_mapping_setup_readback_evidence_source")
            != "bridge_readback"
            or data.get("expected_result_mapping_evidence_source") != "user_input"
        ):
            raise RuntimeError(
                "ADE result mapping setup changed or lost its evidence provenance"
            )

        results = data.get("structured_results")
        if not isinstance(results, dict):
            raise RuntimeError("ADE result mapping lacked structured RDB results")
        if results.get("history") != data.get("history"):
            raise RuntimeError("ADE result mapping history did not match the exact run")
        if results.get("tests") != list(sweep.expected_tests):
            raise RuntimeError("ADE result mapping tests did not match the strict sweep")
        raw_points = results.get(
            "corner_points" if sweep.corner_mode() else "points"
        )
        if not isinstance(raw_points, list) or len(raw_points) != len(sweep.points):
            raise RuntimeError("ADE result mapping did not cover every strict sweep point")
        trusted_points = data.get("sweep_point_consistency")
        if not isinstance(trusted_points, list):
            raise RuntimeError("ADE result mapping lacked verified point evidence")
        trusted_by_point = {
            int(item["point"]): item
            for item in trusted_points
            if isinstance(item, dict) and isinstance(item.get("point"), int)
        }
        if len(trusted_by_point) != len(sweep.points):
            raise RuntimeError("ADE result mapping point evidence was incomplete")

        expected_points = {point.point: point for point in sweep.points}
        seen_points: set[int] = set()
        evaluations: list[CandidateEvaluation] = []
        mapped_points: list[dict[str, Any]] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, dict):
                raise RuntimeError("ADE result mapping encountered a malformed point")
            try:
                point_number = int(raw_point.get("point"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("ADE result mapping encountered an invalid point") from exc
            expected_point = expected_points.get(point_number)
            if expected_point is None or point_number in seen_points:
                raise RuntimeError("ADE result mapping repeated an unknown point")
            seen_points.add(point_number)
            if (
                raw_point.get("maestro_point") != expected_point.maestro_point
                or raw_point.get("corner") != expected_point.corner
            ):
                raise RuntimeError(
                    f"ADE result mapping point {point_number} corner selector changed"
                )

            raw_parameters = raw_point.get("parameters")
            raw_outputs = raw_point.get("outputs")
            if not isinstance(raw_parameters, dict) or not isinstance(raw_outputs, dict):
                raise RuntimeError(
                    f"ADE result mapping point {point_number} lacked parameter/output tables"
                )
            trusted_point = trusted_by_point.get(point_number)
            trusted_outputs = (
                trusted_point.get("scalar_outputs")
                if isinstance(trusted_point, dict)
                else None
            )
            if not isinstance(trusted_outputs, dict):
                raise RuntimeError(
                    f"ADE result mapping point {point_number} lacked trusted scalars"
                )

            parameters: dict[str, float] = {}
            parameter_rows: list[dict[str, Any]] = []
            for binding in mapping.parameters:
                raw_value = raw_parameters.get(binding.source)
                expected_value = expected_point.values.get(binding.source)
                if raw_value is None or expected_value is None or not spectre_values_equal(
                    raw_value, expected_value
                ):
                    raise RuntimeError(
                        f"ADE result mapping point {point_number} parameter "
                        f"{binding.source} did not match the strict sweep"
                    )
                scalar = spectre_scalar(raw_value)
                normalized = None if scalar is None else scalar * binding.scale
                if normalized is None or not math.isfinite(normalized):
                    raise RuntimeError(
                        f"ADE result mapping point {point_number} parameter "
                        f"{binding.source} was not a finite Spectre scalar"
                    )
                parameters[binding.parameter] = normalized
                parameter_rows.append(
                    {
                        "source": binding.source,
                        "parameter": binding.parameter,
                        "raw_value": str(raw_value),
                        "scale": binding.scale,
                        "unit": binding.unit,
                        "normalized_value": normalized,
                    }
                )

            metrics: dict[str, float] = {}
            metric_rows: list[dict[str, Any]] = []
            for binding in mapping.metrics:
                output = raw_outputs.get(binding.output)
                if not isinstance(output, dict):
                    raise RuntimeError(
                        f"ADE result mapping point {point_number} lacked scalar output "
                        f"{binding.output!r}"
                    )
                raw_value = output.get("value")
                trusted_value = trusted_outputs.get(binding.output)
                if trusted_value is None or not spectre_values_equal(
                    raw_value, trusted_value
                ):
                    raise RuntimeError(
                        f"ADE result mapping point {point_number} output "
                        f"{binding.output!r} did not match verified point evidence"
                    )
                scalar = spectre_scalar(raw_value)
                normalized = None if scalar is None else scalar * binding.scale
                if normalized is None or not math.isfinite(normalized):
                    raise RuntimeError(
                        f"ADE result mapping point {point_number} output "
                        f"{binding.output!r} was empty or non-finite"
                    )
                metrics[binding.metric] = normalized
                metric_rows.append(
                    {
                        "test": binding.test,
                        "output": binding.output,
                        "metric": binding.metric,
                        "raw_value": str(raw_value),
                        "scale": binding.scale,
                        "unit": binding.unit,
                        "normalized_value": normalized,
                        "raw_evidence_source": "eda_result",
                        "normalization_evidence_source": "software_inference",
                    }
                )

            simulation = AdapterResult(
                data={
                    "parameters": parameters,
                    "metrics": metrics,
                    "metric_sources": {
                        name: EvidenceSource.SOFTWARE_INFERENCE.value for name in metrics
                    },
                    "analysis_complete": True,
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
            evaluation = cls._evaluate_candidate(
                task, point_number, parameters, simulation
            )
            evaluations.append(evaluation)
            mapped_points.append(
                {
                    "point": point_number,
                    "maestro_point": expected_point.maestro_point,
                    "corner": expected_point.corner,
                    "parameters": parameter_rows,
                    "metrics": metric_rows,
                    "constraints": [
                        item.model_dump(mode="json") for item in evaluation.constraints
                    ],
                    "feasible": evaluation.feasible,
                    "total_violation": evaluation.total_violation,
                    "objective_value": evaluation.objective_value,
                }
            )

        if seen_points != set(expected_points):
            raise RuntimeError("ADE result mapping omitted a strict sweep point")
        evaluations.sort(key=lambda item: item.index)
        mapped_points.sort(key=lambda item: int(item["point"]))
        return evaluations, {
            "history": data.get("history"),
            "tests": list(sweep.expected_tests),
            "result_mapping_setup_fingerprint_sha256": setup_readbacks[0][
                "fingerprint_sha256"
            ],
            "result_mapping_setup_evidence_source": "bridge_readback",
            "raw_result_evidence_source": "eda_result",
            "mapping_and_constraint_evidence_source": "software_inference",
            "parameter_bindings": [
                item.model_dump(mode="json") for item in mapping.parameters
            ],
            "metric_bindings": [
                item.model_dump(mode="json") for item in mapping.metrics
            ],
            "points": mapped_points,
        }

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

        declared = cls._candidate_inputs(task)
        if checkpoint.next_candidate_index > len(declared) + 1:
            raise ValueError("checkpoint next candidate is outside the task search space")
        expected_indexes = list(range(1, checkpoint.next_candidate_index))
        if [candidate.index for candidate in checkpoint.candidates] != expected_indexes:
            raise ValueError("checkpoint candidates are not a completed search prefix")
        cls._validate_checkpoint_analysis_stage_state(checkpoint, task)
        for candidate in checkpoint.candidates:
            declared_candidate = declared[candidate.index - 1]
            declared_parameters = declared_candidate.parameters
            parameters_match = (
                cls._checkpoint_applied_candidate_matches(
                    declared_parameters,
                    candidate.parameters,
                    candidate.oa_parameters,
                )
                if candidate.oa_parameters
                else cls._checkpoint_candidate_matches(
                    declared_parameters,
                    candidate.parameters,
                    checkpoint.initial_parameters,
                )
            )
            if not parameters_match:
                raise ValueError(
                    f"checkpoint candidate {candidate.index} parameters do not match task"
                )
            if (
                candidate.instance_parameters
                != declared_candidate.instance_parameters
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} instance parameters "
                    "do not match task"
                )
            if (
                candidate.testbench_overrides
                != declared_candidate.testbench_overrides
                or candidate.testbench_override_evidence_source
                != declared_candidate.testbench_override_evidence_source
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} testbench overrides "
                    "do not match task"
                )
            if (
                candidate.atomic_candidate_id
                != declared_candidate.atomic_candidate_id
                or candidate.atomic_candidate_predicted_metrics
                != declared_candidate.atomic_candidate_predicted_metrics
                or candidate.atomic_candidate_evidence_source
                != declared_candidate.atomic_candidate_evidence_source
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} atomic provenance "
                    "does not match task"
                )

    @staticmethod
    def _validate_checkpoint_analysis_stage_state(
        checkpoint: ExecutionCheckpoint,
        task: TaskSpec,
    ) -> None:
        if not task.analysis_stages:
            if (
                checkpoint.active_candidate_index is not None
                or checkpoint.next_analysis_stage_index != 1
                or checkpoint.active_candidate_stages
            ):
                raise ValueError(
                    "checkpoint contains analysis-stage state for a non-staged task"
                )
            return
        if checkpoint.active_candidate_index is None:
            if (
                checkpoint.next_analysis_stage_index != 1
                or checkpoint.active_candidate_stages
            ):
                raise ValueError(
                    "checkpoint active analysis-stage state lacks a candidate identity"
                )
            return
        if (
            task.analysis_stage_execution
            is AnalysisStageExecution.SHARED_NETLIST
            and (
                checkpoint.next_analysis_stage_index != 1
                or checkpoint.active_candidate_stages
            )
        ):
            raise ValueError(
                "shared-netlist checkpoint cannot contain partial stage results"
            )
        if checkpoint.active_candidate_index != checkpoint.next_candidate_index:
            raise ValueError(
                "checkpoint active candidate must equal next_candidate_index"
            )
        if checkpoint.next_analysis_stage_index > len(task.analysis_stages) + 1:
            raise ValueError("checkpoint next analysis stage is outside the task")
        completed_count = checkpoint.next_analysis_stage_index - 1
        if len(checkpoint.active_candidate_stages) != completed_count:
            raise ValueError(
                "checkpoint active analysis-stage count is inconsistent"
            )
        expected = task.analysis_stages[:completed_count]
        if [item.stage_id for item in checkpoint.active_candidate_stages] != [
            item.id for item in expected
        ] or [item.analysis for item in checkpoint.active_candidate_stages] != [
            item.analysis for item in expected
        ]:
            raise ValueError(
                "checkpoint active analysis stages do not match the task prefix"
            )

    @classmethod
    def _validate_resume_oa_state(
        cls,
        checkpoint: ExecutionCheckpoint,
        task: TaskSpec,
        actual: dict[str, float],
        actual_instance_parameters: dict[str, dict[str, str]],
    ) -> None:
        allowed: list[
            tuple[dict[str, float], dict[str, dict[str, str]]]
        ] = [
            (
                checkpoint.initial_parameters,
                checkpoint.initial_instance_parameters,
            ),
            (
                checkpoint.expected_oa_parameters,
                checkpoint.expected_oa_instance_parameters,
            ),
        ]
        if checkpoint.pending_oa_parameters is not None:
            allowed.append(
                (
                    checkpoint.pending_oa_parameters,
                    checkpoint.pending_oa_instance_parameters or {},
                )
            )
        for candidate in checkpoint.candidates:
            allowed.append(
                (
                    candidate.oa_parameters or checkpoint.initial_parameters,
                    candidate.instance_parameters,
                )
            )
        for candidate in cls._candidate_inputs(task):
            allowed.append(
                (
                    {
                        name: float(
                            candidate.parameters.get(
                                name, checkpoint.initial_parameters[name]
                            )
                        )
                        for name in checkpoint.initial_parameters
                    },
                    candidate.instance_parameters,
                )
            )
        if not any(
            cls._same_parameters(expected, actual)
            and cls._same_instance_parameters(
                expected_instance_parameters,
                actual_instance_parameters,
            )
            for expected, expected_instance_parameters in allowed
        ):
            raise RuntimeError(
                "current OA semantic/instance parameters do not match the checkpoint "
                "baseline, last confirmed write, or pending write; refusing "
                "automatic resume"
            )

    @staticmethod
    def _candidate_space_size(task: TaskSpec) -> int:
        if task.candidate_set is not None:
            return len(task.candidate_set.candidates)
        if task.theory_seed is not None:
            return len(task.theory_seed.candidates)
        semantic_size = (
            math.prod(len(values) for values in task.parameter_space.values())
            if task.parameter_space
            else 1
        )
        instance_size = (
            math.prod(len(sweep.values) for sweep in task.instance_parameter_space)
            if task.instance_parameter_space
            else 1
        )
        return semantic_size * instance_size

    @classmethod
    def _analysis_stage_task(
        cls,
        task: TaskSpec,
        stage: AnalysisStageSpec,
        instance_parameters: dict[str, dict[str, str]],
        testbench_overrides: GenericTestbenchOverrides | None = None,
    ) -> TaskSpec:
        candidate_task = cls._candidate_task(
            task,
            instance_parameters,
            testbench_overrides,
        )
        return candidate_task.model_copy(
            update={
                "analysis": stage.analysis,
                "analysis_stages": [],
                "ac_sweep": (
                    task.ac_sweep if stage.analysis.value == "ac" else None
                ),
                "linearity_sweep": (
                    task.linearity_sweep
                    if stage.analysis.value == "transient"
                    else None
                ),
                "noise_sweep": (
                    task.noise_sweep if stage.analysis.value == "noise" else None
                ),
                "constraints": cls._constraints_for_analysis_stage(task, stage),
                "objective": None,
            }
        )

    def _run_staged_candidates(
        self,
        task: TaskSpec,
        *,
        stage_parameters: bool,
        evaluations: list[CandidateEvaluation],
        start_index: int,
        index_offset: int,
        topology_variant_id: str | None,
        topology_sha256: str | None,
        progress: Callable[
            [
                str,
                int,
                _CandidateInput,
                list[CandidateEvaluation],
                _AppliedCandidateState | None,
            ],
            None,
        ]
        | None,
        stage_progress: Callable[
            [
                int,
                _CandidateInput,
                list[AnalysisStageEvaluation],
                int,
            ],
            None,
        ]
        | None,
        resume_candidate_index: int | None,
        resume_stages: list[AnalysisStageEvaluation] | None,
    ) -> list[CandidateEvaluation]:
        shared_netlist = (
            task.analysis_stage_execution is AnalysisStageExecution.SHARED_NETLIST
        )
        if shared_netlist and (resume_stages or []):
            raise ValueError(
                "shared-netlist staged execution resumes only at candidate boundaries"
            )
        for local_index, candidate in enumerate(self._candidate_inputs(task), start=1):
            index = index_offset + local_index
            if index < start_index:
                continue
            completed_stages = (
                list(resume_stages or [])
                if resume_candidate_index == index
                else []
            )
            expected_prefix = task.analysis_stages[: len(completed_stages)]
            if [item.stage_id for item in completed_stages] != [
                item.id for item in expected_prefix
            ] or [item.analysis for item in completed_stages] != [
                item.analysis for item in expected_prefix
            ]:
                raise ValueError(
                    "checkpoint active analysis stages do not match the task prefix"
                )
            resumed_termination = next(
                (
                    spec.id
                    for position, (spec, evaluation) in enumerate(
                        zip(expected_prefix, completed_stages, strict=True),
                        start=1,
                    )
                    if spec.stop_on_failure
                    and not evaluation.passed
                    and evaluation.analysis_complete
                    and position < len(task.analysis_stages)
                ),
                None,
            )
            resumed_incomplete = any(
                not evaluation.analysis_complete for evaluation in completed_stages
            )
            if progress is not None:
                progress("started", index, candidate, evaluations, None)
            stage_completed = not stage_parameters
            applied_state: _AppliedCandidateState | None = None
            candidate_task = self._candidate_task(
                task,
                candidate.instance_parameters,
                candidate.testbench_overrides,
            )
            try:
                if stage_parameters:
                    staged = self._action(
                        f"parameters.stage.{index}",
                        lambda candidate=candidate, candidate_task=candidate_task: self.adapter.apply_parameters(
                            candidate_task, candidate.parameters
                        ),
                    )
                    applied_state = _AppliedCandidateState(
                        oa_parameters=self._applied_semantic_parameters(
                            staged, candidate_task
                        ),
                        oa_instance_parameters=(
                            self._applied_instance_parameter_state(
                                staged, candidate_task
                            )
                        ),
                    )
                    stage_completed = True
                    if progress is not None:
                        progress(
                            "staged", index, candidate, evaluations, applied_state
                        )

                terminated_after_stage: str | None = resumed_termination
                pending_stages = (
                    []
                    if resumed_termination is not None or resumed_incomplete
                    else task.analysis_stages[len(completed_stages) :]
                )
                if shared_netlist and pending_stages:
                    batch_method = getattr(
                        self.adapter, "simulate_analysis_stages", None
                    )
                    if not callable(batch_method):
                        raise RuntimeError(
                            "adapter does not implement shared-netlist staged simulation"
                        )
                    batch_result = self._action(
                        f"simulation.candidate.{index}.stages.shared-netlist",
                        lambda candidate=candidate, candidate_task=candidate_task: (
                            batch_method(candidate_task, candidate.parameters)
                        ),
                    )
                    (
                        completed_stages,
                        terminated_after_stage,
                    ) = self._evaluate_shared_analysis_stage_batch(
                        candidate_task,
                        batch_result,
                    )
                else:
                    for stage_index, stage in enumerate(
                        pending_stages, start=len(completed_stages) + 1
                    ):
                        analysis_task = self._analysis_stage_task(
                            task,
                            stage,
                            candidate.instance_parameters,
                            candidate.testbench_overrides,
                        )
                        result = self._action(
                            (
                                f"simulation.candidate.{index}.stage.{stage_index}."
                                f"{stage.id}"
                            ),
                            lambda candidate=candidate, analysis_task=analysis_task: self.adapter.simulate(
                                analysis_task, candidate.parameters
                            ),
                        )
                        stage_evaluation = self._evaluate_analysis_stage(
                            analysis_task, stage, result
                        )
                        completed_stages.append(stage_evaluation)
                        if stage_progress is not None:
                            stage_progress(
                                index,
                                candidate,
                                completed_stages,
                                stage_index + 1,
                            )
                        if not stage_evaluation.analysis_complete:
                            break
                        if (
                            stage.stop_on_failure
                            and not stage_evaluation.passed
                            and stage_index < len(task.analysis_stages)
                        ):
                            terminated_after_stage = stage.id
                            break
            except AdapterInterrupted:
                if progress is not None:
                    progress("interrupted", index, candidate, evaluations, None)
                raise
            except Exception as exc:
                if stage_parameters and not stage_completed:
                    raise
                failed = self._merge_analysis_stage_candidate(
                    candidate_task,
                    index,
                    candidate,
                    completed_stages,
                    oa_parameters=(
                        applied_state.oa_parameters
                        if applied_state is not None
                        else {}
                    ),
                    topology_variant_id=topology_variant_id,
                    topology_sha256=topology_sha256,
                ).model_copy(
                    update={
                        "feasible": False,
                        "analysis_complete": False,
                        "analysis_issues": [
                            *(
                                f"{item.stage_id}: {issue}"
                                for item in completed_stages
                                for issue in item.analysis_issues
                            ),
                            f"{type(exc).__name__}: {exc}",
                        ],
                        "evidence_source": EvidenceSource.SYSTEM_EVENT,
                    }
                )
                evaluations.append(failed)
                if progress is not None:
                    progress("completed", index, candidate, evaluations, None)
                continue

            evaluations.append(
                self._merge_analysis_stage_candidate(
                    candidate_task,
                    index,
                    candidate,
                    completed_stages,
                    oa_parameters=(
                        applied_state.oa_parameters
                        if applied_state is not None
                        else {}
                    ),
                    topology_variant_id=topology_variant_id,
                    topology_sha256=topology_sha256,
                    terminated_after_stage=terminated_after_stage,
                )
            )
            if progress is not None:
                progress("completed", index, candidate, evaluations, None)
        return evaluations

    @classmethod
    def _evaluate_shared_analysis_stage_batch(
        cls,
        task: TaskSpec,
        batch: AdapterResult,
    ) -> tuple[list[AnalysisStageEvaluation], str | None]:
        raw_results = batch.data.get("stage_results")
        if not isinstance(raw_results, list) or not raw_results:
            raise RuntimeError(
                "shared-netlist staged simulation returned no stage results"
            )
        if len(raw_results) > len(task.analysis_stages):
            raise RuntimeError(
                "shared-netlist staged simulation returned too many stages"
            )

        evaluations: list[AnalysisStageEvaluation] = []
        netlists: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_results):
            if not isinstance(raw, dict) or not isinstance(raw.get("result"), dict):
                raise RuntimeError(
                    "shared-netlist staged simulation returned an invalid stage result"
                )
            expected = task.analysis_stages[index]
            if (
                raw.get("stage_id") != expected.id
                or raw.get("analysis") != expected.analysis.value
            ):
                raise RuntimeError(
                    "shared-netlist stage identity/order does not match the task"
                )
            nested = raw["result"]
            evidence = nested.get("evidence")
            netlist = evidence.get("netlist") if isinstance(evidence, dict) else None
            if not isinstance(netlist, dict):
                raise RuntimeError(
                    f"shared-netlist stage {expected.id!r} lacks netlist evidence"
                )
            netlists.append(netlist)
            evaluations.append(
                cls._evaluate_analysis_stage(
                    task,
                    expected,
                    AdapterResult(
                        data=nested,
                        evidence_source=batch.evidence_source,
                    ),
                )
            )

        if any(netlist != netlists[0] for netlist in netlists[1:]):
            raise RuntimeError(
                "shared-netlist stages did not bind to identical netlist evidence"
            )
        reuse = batch.data.get("shared_netlist")
        if not isinstance(reuse, dict):
            raise RuntimeError("shared-netlist reuse evidence is missing")
        if reuse.get("netlist_generation_count") != 1:
            raise RuntimeError("shared-netlist batch did not prove one si generation")
        if (
            reuse.get("remote_path") != netlists[0].get("remote_path")
            or reuse.get("sha256") != netlists[0].get("sha256")
        ):
            raise RuntimeError(
                "shared-netlist reuse summary does not match stage evidence"
            )

        expected_termination: str | None = None
        for position, (stage, evaluation) in enumerate(
            zip(task.analysis_stages, evaluations, strict=False),
            start=1,
        ):
            if not evaluation.analysis_complete:
                break
            if (
                stage.stop_on_failure
                and not evaluation.passed
                and position < len(task.analysis_stages)
            ):
                expected_termination = stage.id
                break
        prefix_is_complete = len(evaluations) == len(task.analysis_stages)
        prefix_ended_incomplete = not evaluations[-1].analysis_complete
        if (
            not prefix_is_complete
            and expected_termination is None
            and not prefix_ended_incomplete
        ):
            raise RuntimeError(
                "shared-netlist batch omitted a required later analysis stage"
            )
        reported_termination = batch.data.get("terminated_after_stage")
        if reported_termination != expected_termination:
            raise RuntimeError(
                "shared-netlist early-termination result disagrees with independent "
                "executor constraint evaluation"
            )
        return evaluations, expected_termination

    def _run_candidates(
        self,
        task: TaskSpec,
        *,
        stage_parameters: bool = False,
        evaluations: list[CandidateEvaluation] | None = None,
        start_index: int = 1,
        index_offset: int = 0,
        topology_variant_id: str | None = None,
        topology_sha256: str | None = None,
        progress: Callable[
            [
                str,
                int,
                _CandidateInput,
                list[CandidateEvaluation],
                _AppliedCandidateState | None,
            ],
            None,
        ]
        | None = None,
        stage_progress: Callable[
            [
                int,
                _CandidateInput,
                list[AnalysisStageEvaluation],
                int,
            ],
            None,
        ]
        | None = None,
        resume_candidate_index: int | None = None,
        resume_stages: list[AnalysisStageEvaluation] | None = None,
    ) -> list[CandidateEvaluation]:
        evaluations = evaluations if evaluations is not None else []
        if task.analysis_stages:
            return self._run_staged_candidates(
                task,
                stage_parameters=stage_parameters,
                evaluations=evaluations,
                start_index=start_index,
                index_offset=index_offset,
                topology_variant_id=topology_variant_id,
                topology_sha256=topology_sha256,
                progress=progress,
                stage_progress=stage_progress,
                resume_candidate_index=resume_candidate_index,
                resume_stages=resume_stages,
            )
        for local_index, candidate in enumerate(
            self._candidate_inputs(task), start=1
        ):
            index = index_offset + local_index
            if index < start_index:
                continue
            if progress is not None:
                progress("started", index, candidate, evaluations, None)
            stage_completed = not stage_parameters
            applied_state: _AppliedCandidateState | None = None
            candidate_task = self._candidate_task(
                task,
                candidate.instance_parameters,
                candidate.testbench_overrides,
            )
            try:
                if stage_parameters:
                    staged = self._action(
                        f"parameters.stage.{index}",
                        lambda candidate=candidate, candidate_task=candidate_task: self.adapter.apply_parameters(
                            candidate_task, candidate.parameters
                        ),
                    )
                    applied_state = _AppliedCandidateState(
                        oa_parameters=self._applied_semantic_parameters(
                            staged, candidate_task
                        ),
                        oa_instance_parameters=(
                            self._applied_instance_parameter_state(
                                staged, candidate_task
                            )
                        ),
                    )
                    stage_completed = True
                    if progress is not None:
                        progress(
                            "staged",
                            index,
                            candidate,
                            evaluations,
                            applied_state,
                        )
                result = self._action(
                    f"simulation.candidate.{index}",
                    lambda candidate=candidate, candidate_task=candidate_task: self.adapter.simulate(
                        candidate_task, candidate.parameters
                    ),
                )
            except AdapterInterrupted:
                if progress is not None:
                    progress("interrupted", index, candidate, evaluations, None)
                raise
            except Exception:
                if stage_parameters and not stage_completed:
                    raise
                constraints = evaluate_constraints({}, task.constraints)
                evaluations.append(
                    CandidateEvaluation(
                        index=index,
                        topology_variant_id=topology_variant_id,
                        topology_sha256=topology_sha256,
                        parameters=candidate.parameters,
                        instance_parameters=candidate.instance_parameters,
                        testbench_overrides=candidate.testbench_overrides,
                        testbench_override_evidence_source=(
                            candidate.testbench_override_evidence_source
                        ),
                        oa_parameters=(
                            applied_state.oa_parameters
                            if applied_state is not None
                            else {}
                        ),
                        metrics={},
                        constraints=constraints,
                        feasible=False,
                        total_violation=sum(
                            item.normalized_violation for item in constraints
                        ),
                        evidence_source=EvidenceSource.SYSTEM_EVENT,
                        metric_sources={},
                        atomic_candidate_id=candidate.atomic_candidate_id,
                        atomic_candidate_predicted_metrics=(
                            candidate.atomic_candidate_predicted_metrics
                        ),
                        atomic_candidate_evidence_source=(
                            candidate.atomic_candidate_evidence_source
                        ),
                        theory_seed_candidate_id=candidate.theory_seed_candidate_id,
                        theory_seed_source_candidate_id=(
                            candidate.theory_seed_source_candidate_id
                        ),
                        theory_seed_predicted_metrics=(
                            candidate.theory_seed_predicted_metrics
                        ),
                        theory_seed_evidence_source=(
                            EvidenceSource.SOFTWARE_INFERENCE
                            if candidate.theory_seed_candidate_id is not None
                            else None
                        ),
                    )
                )
                if progress is not None:
                    progress("completed", index, candidate, evaluations, None)
                continue
            evaluations.append(
                self._evaluate_candidate(
                    candidate_task,
                    index,
                    candidate.parameters,
                    result,
                    instance_parameters=candidate.instance_parameters,
                    testbench_overrides=candidate.testbench_overrides,
                    testbench_override_evidence_source=(
                        candidate.testbench_override_evidence_source
                    ),
                    oa_parameters=(
                        applied_state.oa_parameters
                        if applied_state is not None
                        else {}
                    ),
                    atomic_candidate_id=candidate.atomic_candidate_id,
                    atomic_candidate_predicted_metrics=(
                        candidate.atomic_candidate_predicted_metrics
                    ),
                    atomic_candidate_evidence_source=(
                        candidate.atomic_candidate_evidence_source
                    ),
                    theory_seed_candidate_id=candidate.theory_seed_candidate_id,
                    theory_seed_source_candidate_id=(
                        candidate.theory_seed_source_candidate_id
                    ),
                    theory_seed_predicted_metrics=(
                        candidate.theory_seed_predicted_metrics
                    ),
                    topology_variant_id=topology_variant_id,
                    topology_sha256=topology_sha256,
                )
            )
            if progress is not None:
                progress("completed", index, candidate, evaluations, None)
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

    @classmethod
    def _search_audit(
        cls,
        task: TaskSpec,
        candidates: list[CandidateEvaluation],
        *,
        has_recommendation: bool,
        declared_candidate_count: int | None = None,
        topology_variant_count: int = 1,
        recommendation_withheld_after_winner_verification: bool = False,
    ) -> SearchAudit:
        declared = (
            cls._candidate_space_size(task)
            if declared_candidate_count is None
            else declared_candidate_count
        )
        attempted = len(candidates)
        completed = sum(
            candidate.analysis_complete
            and candidate.evidence_source is not EvidenceSource.SYSTEM_EVENT
            for candidate in candidates
        )
        domain_exhausted = attempted == declared and completed == declared
        if recommendation_withheld_after_winner_verification:
            scope = SelectionScope.NO_RECOMMENDATION_FROM_EVALUATED_POINTS
            statement = (
                "The nominal candidate domain was evaluated and produced a "
                "provisional winner, but that point failed or did not complete "
                "the separately declared winner-only verification Gate. No robust "
                "recommendation is made, and unverified nominal runners-up were not "
                "silently promoted."
            )
        elif has_recommendation and domain_exhausted:
            scope = SelectionScope.BEST_IN_DECLARED_DISCRETE_DOMAIN
            statement = (
                "The recommendation is the highest-ranked feasible candidate "
                "under the declared constraints and optional objective in the "
                "fully evaluated discrete domain. It is not a continuous-space "
                "or global optimum."
            )
        elif has_recommendation:
            scope = SelectionScope.BEST_EVALUATED
            statement = (
                "The recommendation is only the highest-ranked feasible point "
                "among completed evaluated candidates; the declared discrete "
                "domain was not exhausted."
            )
        elif domain_exhausted:
            scope = SelectionScope.NO_FEASIBLE_IN_DECLARED_DISCRETE_DOMAIN
            statement = (
                "No candidate in the fully evaluated declared discrete domain met "
                "the specification. This does not prove continuous-space "
                "infeasibility."
            )
        else:
            scope = SelectionScope.NO_RECOMMENDATION_FROM_EVALUATED_POINTS
            statement = (
                "No recommendation was produced from the completed evaluated "
                "points, and the declared discrete domain was not exhausted."
            )
        return SearchAudit(
            declared_candidate_count=declared,
            attempted_candidate_count=attempted,
            completed_candidate_count=completed,
            topology_variant_count=topology_variant_count,
            domain_exhausted=domain_exhausted,
            selection_scope=scope,
            statement=statement,
            candidate_set_source=(
                task.candidate_set.source if task.candidate_set is not None else None
            ),
            theory_seed_source=(
                task.theory_seed.source if task.theory_seed is not None else None
            ),
        )

    @staticmethod
    def _merge_instance_parameter_updates(
        *collections: list[InstanceParameterUpdate],
    ) -> list[InstanceParameterUpdate]:
        merged: dict[str, dict[str, str]] = {}
        for collection in collections:
            for update in collection:
                merged.setdefault(update.instance, {}).update(update.parameters)
        return [
            InstanceParameterUpdate(instance=instance, parameters=parameters)
            for instance, parameters in sorted(merged.items())
        ]

    @classmethod
    def _winner_verification_task(
        cls,
        task: TaskSpec,
        variant_task: TaskSpec,
        selected: CandidateEvaluation,
    ) -> TaskSpec:
        verification = task.winner_verification
        if verification is None:
            raise ValueError("winner verification settings are missing")
        candidate_task = cls._candidate_task(
            variant_task,
            selected.instance_parameters,
            selected.testbench_overrides,
        )
        return candidate_task.model_copy(
            update={
                "analysis": None,
                "analysis_stages": list(verification.analysis_stages),
                "analysis_stage_execution": AnalysisStageExecution.SHARED_NETLIST,
                "ac_sweep": verification.ac_sweep,
                "linearity_sweep": verification.linearity_sweep,
                "noise_sweep": verification.noise_sweep,
                "constraints": list(verification.constraints),
                "objective": None,
                "operating_conditions": list(
                    verification.operating_conditions
                ),
                "parameter_space": {},
                "instance_parameter_space": [],
                "candidate_set": None,
                "theory_seed": None,
                "topology_refinement": None,
                "winner_verification": None,
            }
        )

    def _run_winner_verification(
        self,
        task: TaskSpec,
        variant_task: TaskSpec,
        selected: CandidateEvaluation,
    ) -> CandidateEvaluation:
        verification_task = self._winner_verification_task(
            task,
            variant_task,
            selected,
        )
        batch = self._action(
            f"simulation.winner.{selected.index}.stages.shared-netlist",
            lambda: self.adapter.simulate_analysis_stages(
                verification_task,
                selected.parameters,
            ),
        )
        stages, terminated_after_stage = self._evaluate_shared_analysis_stage_batch(
            verification_task,
            batch,
        )
        candidate = _CandidateInput(
            parameters=dict(selected.parameters),
            instance_parameters={
                instance: dict(parameters)
                for instance, parameters in selected.instance_parameters.items()
            },
            testbench_overrides=selected.testbench_overrides,
            testbench_override_evidence_source=(
                selected.testbench_override_evidence_source
            ),
            atomic_candidate_id=selected.atomic_candidate_id,
            atomic_candidate_predicted_metrics=dict(
                selected.atomic_candidate_predicted_metrics
            ),
            atomic_candidate_evidence_source=(
                selected.atomic_candidate_evidence_source
            ),
            theory_seed_candidate_id=selected.theory_seed_candidate_id,
            theory_seed_source_candidate_id=(
                selected.theory_seed_source_candidate_id
            ),
            theory_seed_predicted_metrics=dict(
                selected.theory_seed_predicted_metrics
            ),
        )
        return self._merge_analysis_stage_candidate(
            verification_task,
            selected.index,
            candidate,
            stages,
            oa_parameters=selected.oa_parameters,
            topology_variant_id=selected.topology_variant_id,
            topology_sha256=selected.topology_sha256,
            terminated_after_stage=terminated_after_stage,
        )

    @classmethod
    def _topology_refinement_variant_tasks(
        cls, task: TaskSpec
    ) -> tuple[TaskSpec, list[tuple[Any, TaskSpec]]]:
        refinement = task.topology_refinement
        if refinement is None:
            raise ValueError("topology refinement settings are missing")
        baseline = task.model_copy(update={"topology_refinement": None})
        alternatives = []
        for alternative in refinement.resolved_alternatives():
            alternative_task = baseline.model_copy(
                update={
                    "design_context": alternative.design_context,
                    "generic_simulation": alternative.generic_simulation,
                    "instance_parameter_updates": cls._merge_instance_parameter_updates(
                        list(task.instance_parameter_updates),
                        list(alternative.instance_parameter_updates),
                    ),
                }
            )
            alternatives.append((alternative, alternative_task))
        return baseline, alternatives

    @classmethod
    def _topology_refinement_declared_candidates(
        cls, task: TaskSpec
    ) -> list[tuple[str, str, _CandidateInput]]:
        refinement = task.topology_refinement
        if refinement is None:
            raise ValueError("topology refinement settings are missing")
        baseline, alternatives = cls._topology_refinement_variant_tasks(task)
        baseline_sha256 = alternatives[
            0
        ][0].topology_delta.contract.expected_before_sha256
        declared = [
            (
                refinement.baseline_id,
                baseline_sha256,
                candidate,
            )
            for candidate in cls._candidate_inputs(baseline)
        ]
        for alternative, alternative_task in alternatives:
            declared.extend(
                (
                    alternative.id,
                    alternative.topology_delta.contract.expected_after_sha256,
                    candidate,
                )
                for candidate in cls._candidate_inputs(alternative_task)
            )
        return declared

    @classmethod
    def _validate_topology_refinement_checkpoint(
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

        refinement = task.topology_refinement
        if refinement is None:
            raise ValueError("topology refinement settings are missing")
        alternatives = refinement.resolved_alternatives()
        baseline_sha256 = alternatives[
            0
        ].topology_delta.contract.expected_before_sha256
        variants = {refinement.baseline_id: baseline_sha256}
        variants.update(
            {
                alternative.id: alternative.topology_delta.contract.expected_after_sha256
                for alternative in alternatives
            }
        )
        if checkpoint.initial_topology_sha256 != baseline_sha256:
            raise ValueError("checkpoint topology baseline does not match the task")
        if (
            checkpoint.expected_topology_variant_id not in variants
            or variants[checkpoint.expected_topology_variant_id]
            != checkpoint.expected_topology_sha256
        ):
            raise ValueError("checkpoint expected topology state is inconsistent")
        if (
            checkpoint.pending_topology_sha256 is not None
            and checkpoint.pending_topology_sha256 not in set(variants.values())
        ):
            raise ValueError("checkpoint pending topology is outside the task")

        declared = cls._topology_refinement_declared_candidates(task)
        if checkpoint.next_candidate_index > len(declared) + 1:
            raise ValueError("checkpoint next candidate is outside the task search space")
        expected_indexes = list(range(1, checkpoint.next_candidate_index))
        if [candidate.index for candidate in checkpoint.candidates] != expected_indexes:
            raise ValueError("checkpoint candidates are not a completed search prefix")
        cls._validate_checkpoint_analysis_stage_state(checkpoint, task)
        for candidate in checkpoint.candidates:
            variant_id, topology_sha256, declared_candidate = declared[
                candidate.index - 1
            ]
            if (
                candidate.topology_variant_id != variant_id
                or candidate.topology_sha256 != topology_sha256
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} topology identity "
                    "does not match task"
                )
            if candidate.parameters != declared_candidate.parameters:
                raise ValueError(
                    f"checkpoint candidate {candidate.index} parameters do not "
                    "match task"
                )
            if candidate.instance_parameters != declared_candidate.instance_parameters:
                raise ValueError(
                    f"checkpoint candidate {candidate.index} instance parameters "
                    "do not match task"
                )
            if (
                candidate.atomic_candidate_id
                != declared_candidate.atomic_candidate_id
                or candidate.atomic_candidate_predicted_metrics
                != declared_candidate.atomic_candidate_predicted_metrics
                or candidate.atomic_candidate_evidence_source
                != declared_candidate.atomic_candidate_evidence_source
            ):
                raise ValueError(
                    f"checkpoint candidate {candidate.index} provenance does not "
                    "match task"
                )

    @classmethod
    def _validate_topology_refinement_resume_parameters(
        cls,
        checkpoint: ExecutionCheckpoint,
        variant_task: TaskSpec,
        actual: dict[str, dict[str, str]],
    ) -> None:
        targets = cls._instance_parameter_targets(variant_task)

        def selected(
            state: dict[str, dict[str, str]] | None,
        ) -> dict[str, dict[str, str]] | None:
            if state is None:
                return None
            result: dict[str, dict[str, str]] = {}
            for instance, names in targets.items():
                parameters = state.get(instance)
                if parameters is None or not names <= parameters.keys():
                    return None
                result[instance] = {
                    name: parameters[name] for name in sorted(names)
                }
            return result

        allowed: list[dict[str, dict[str, str]]] = []
        for state in (
            checkpoint.initial_instance_parameters,
            checkpoint.expected_oa_instance_parameters,
            checkpoint.pending_oa_instance_parameters,
        ):
            filtered = selected(state)
            if filtered is not None:
                allowed.append(filtered)
        allowed.extend(
            candidate.instance_parameters
            for candidate in cls._candidate_inputs(variant_task)
        )
        if actual not in allowed:
            raise RuntimeError(
                "current OA instance parameters do not match the checkpoint "
                "baseline, a declared candidate, or a confirmed/pending write; "
                "refusing automatic topology-loop resume"
            )

    def _execute_topology_refinement(
        self,
        task: TaskSpec,
        plan: ExecutionPlan,
        *,
        checkpoint_path: Path | None,
        resume_checkpoint: ExecutionCheckpoint | None,
    ) -> RunRecord:
        refinement = task.topology_refinement
        if refinement is None:
            raise ValueError("topology refinement settings are missing")
        baseline_task, alternative_variants = self._topology_refinement_variant_tasks(
            task
        )
        alternatives = [alternative for alternative, _ in alternative_variants]
        baseline_sha256 = alternatives[
            0
        ].topology_delta.contract.expected_before_sha256
        alternative_by_id = {
            alternative.id: (alternative, alternative_task)
            for alternative, alternative_task in alternative_variants
        }
        alternative_by_sha256 = {
            alternative.topology_delta.contract.expected_after_sha256: (
                alternative,
                alternative_task,
            )
            for alternative, alternative_task in alternative_variants
        }
        parameter_candidate_count = self._candidate_space_size(task)
        declared_candidate_count = (1 + len(alternatives)) * parameter_candidate_count

        if resume_checkpoint is not None:
            if checkpoint_path is None:
                raise ValueError("resuming requires a checkpoint output path")
            self._validate_topology_refinement_checkpoint(
                resume_checkpoint,
                task,
                plan,
                self.adapter.name,
            )

        self.actions = (
            list(resume_checkpoint.actions) if resume_checkpoint is not None else []
        )
        started = (
            resume_checkpoint.started_at
            if resume_checkpoint is not None
            else datetime.now(UTC)
        )
        notes = (
            list(resume_checkpoint.notes) if resume_checkpoint is not None else []
        )
        candidates = (
            list(resume_checkpoint.candidates)
            if resume_checkpoint is not None
            else []
        )
        initial_parameters = (
            dict(resume_checkpoint.initial_parameters)
            if resume_checkpoint is not None
            else {}
        )
        initial_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.initial_instance_parameters.items()
            }
            if resume_checkpoint is not None
            else {}
        )
        expected_oa_parameters = (
            dict(resume_checkpoint.expected_oa_parameters)
            if resume_checkpoint is not None
            else {}
        )
        expected_oa_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.expected_oa_instance_parameters.items()
            }
            if resume_checkpoint is not None
            else {}
        )
        pending_oa_parameters = (
            dict(resume_checkpoint.pending_oa_parameters)
            if resume_checkpoint is not None
            and resume_checkpoint.pending_oa_parameters is not None
            else None
        )
        pending_oa_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.pending_oa_instance_parameters.items()
            }
            if resume_checkpoint is not None
            and resume_checkpoint.pending_oa_instance_parameters is not None
            else None
        )
        expected_topology_sha256 = (
            resume_checkpoint.expected_topology_sha256
            if resume_checkpoint is not None
            else baseline_sha256
        )
        pending_topology_sha256 = (
            resume_checkpoint.pending_topology_sha256
            if resume_checkpoint is not None
            else None
        )
        expected_topology_variant_id = (
            resume_checkpoint.expected_topology_variant_id
            if resume_checkpoint is not None
            else refinement.baseline_id
        )
        next_candidate_index = (
            resume_checkpoint.next_candidate_index
            if resume_checkpoint is not None
            else 1
        )
        active_candidate_index = (
            resume_checkpoint.active_candidate_index
            if resume_checkpoint is not None
            else None
        )
        next_analysis_stage_index = (
            resume_checkpoint.next_analysis_stage_index
            if resume_checkpoint is not None
            else 1
        )
        active_candidate_stages = (
            list(resume_checkpoint.active_candidate_stages)
            if resume_checkpoint is not None
            else []
        )
        selected_parameters: dict[str, float] | None = None
        selected_instance_parameters: dict[str, dict[str, str]] | None = None
        selected_testbench_overrides: GenericTestbenchOverrides | None = None
        selected_testbench_override_evidence_source: EvidenceSource | None = None
        selected_metrics: dict[str, float] | None = None
        selected_topology_variant_id: str | None = None
        selected_topology_sha256: str | None = None
        winner_verification: CandidateEvaluation | None = None
        winner_verification_rejected = False
        status = RunStatus.SUCCEEDED

        alternative_fixed_parameters = {
            alternative.id: {
                update.instance: dict(update.parameters)
                for update in alternative.instance_parameter_updates
            }
            for alternative in alternatives
        }

        def merged_state(
            left: dict[str, dict[str, str]],
            right: dict[str, dict[str, str]],
        ) -> dict[str, dict[str, str]]:
            result = {
                instance: dict(parameters)
                for instance, parameters in left.items()
            }
            for instance, parameters in right.items():
                result.setdefault(instance, {}).update(parameters)
            return result

        def checkpoint_state(*, complete: bool = False) -> ExecutionCheckpoint:
            return ExecutionCheckpoint(
                    task_id=task.id,
                    plan_token=plan.confirmation_token,
                    adapter=self.adapter.name,
                    started_at=started,
                    initial_parameters=initial_parameters,
                    expected_oa_parameters=expected_oa_parameters,
                    pending_oa_parameters=pending_oa_parameters,
                    initial_instance_parameters=initial_instance_parameters,
                    expected_oa_instance_parameters=(
                        expected_oa_instance_parameters
                    ),
                    pending_oa_instance_parameters=(
                        pending_oa_instance_parameters
                    ),
                    initial_topology_sha256=baseline_sha256,
                    expected_topology_sha256=expected_topology_sha256,
                    pending_topology_sha256=pending_topology_sha256,
                    expected_topology_variant_id=(
                        expected_topology_variant_id
                    ),
                    next_candidate_index=next_candidate_index,
                    active_candidate_index=active_candidate_index,
                    next_analysis_stage_index=next_analysis_stage_index,
                    active_candidate_stages=active_candidate_stages,
                    actions=self.actions,
                    candidates=candidates,
                    notes=notes,
                    complete=complete,
                )

        def persist_checkpoint(*, complete: bool = False) -> None:
            if checkpoint_path is None or not initial_instance_parameters:
                return
            save_execution_checkpoint(
                checkpoint_state(complete=complete),
                checkpoint_path,
            )

        def inspection_sha256(result: AdapterResult) -> str:
            return topology_fingerprint(snapshot_from_inspection(result.data))

        def assert_topology(
            result: AdapterResult,
            expected: str,
            label: str,
        ) -> None:
            actual = inspection_sha256(result)
            if actual != expected:
                raise RuntimeError(
                    f"{label} topology fingerprint mismatch: expected={expected}, "
                    f"actual={actual}"
                )

        def apply_state(
            action: str,
            variant_task: TaskSpec,
            instance_parameters: dict[str, dict[str, str]],
        ) -> _AppliedCandidateState:
            nonlocal expected_oa_parameters
            nonlocal expected_oa_instance_parameters
            nonlocal pending_oa_parameters
            nonlocal pending_oa_instance_parameters
            pending_oa_parameters = {}
            pending_oa_instance_parameters = {
                instance: dict(parameters)
                for instance, parameters in instance_parameters.items()
            }
            persist_checkpoint()
            candidate_task = self._candidate_task(
                variant_task,
                instance_parameters,
            )
            result = self._action(
                action,
                lambda: self.adapter.apply_parameters(candidate_task, {}),
            )
            expected_oa_parameters = self._applied_semantic_parameters(
                result,
                candidate_task,
            )
            expected_oa_instance_parameters = (
                self._applied_instance_parameter_state(result, candidate_task)
            )
            pending_oa_parameters = None
            pending_oa_instance_parameters = None
            persist_checkpoint()
            return _AppliedCandidateState(
                oa_parameters=expected_oa_parameters,
                oa_instance_parameters=expected_oa_instance_parameters,
            )

        def transform_task(
            alternative: Any,
            alternative_task: TaskSpec,
            direction: str,
        ) -> TaskSpec:
            context = (
                baseline_task.design_context
                if direction == "forward"
                else alternative_task.design_context
            )
            return task.model_copy(
                update={
                    "operation": Operation.SCHEMATIC_TRANSFORM,
                    "analysis": None,
                    "analysis_stages": [],
                    "ac_sweep": None,
                    "linearity_sweep": None,
                    "noise_sweep": None,
                    "generic_simulation": None,
                    "topology_refinement": None,
                    "winner_verification": None,
                    "topology_delta": alternative.topology_delta.model_copy(
                        update={"direction": direction}
                    ),
                    "design_context": context,
                    "parameters": {},
                    "instance_parameter_updates": [],
                    "instance_parameter_space": [],
                    "candidate_set": None,
                    "theory_seed": None,
                    "constraints": [],
                    "objective": None,
                    "create_if_missing": False,
                }
            )

        def apply_topology(
            alternative: Any,
            alternative_task: TaskSpec,
            direction: str,
            *,
            recovery: bool = False,
        ) -> None:
            nonlocal expected_oa_instance_parameters
            nonlocal expected_topology_sha256
            nonlocal pending_topology_sha256
            nonlocal expected_topology_variant_id
            if direction == "forward":
                input_task = baseline_task
                output_task = alternative_task
                input_sha256 = baseline_sha256
                output_sha256 = (
                    alternative.topology_delta.contract.expected_after_sha256
                )
                output_variant_id = alternative.id
            else:
                input_task = alternative_task
                output_task = baseline_task
                input_sha256 = (
                    alternative.topology_delta.contract.expected_after_sha256
                )
                output_sha256 = baseline_sha256
                output_variant_id = refinement.baseline_id
            suffix = ".recovery" if recovery else ""
            variant_suffix = (
                "" if len(alternative_variants) == 1 else f".{alternative.id}"
            )
            before = self._action(
                f"schematic.inspect.topology-{direction}{variant_suffix}.before{suffix}",
                lambda: self.adapter.inspect_schematic(input_task),
            )
            assert_topology(before, input_sha256, f"{direction} input")
            self._bind_design_context(
                input_task,
                before,
                action_name=(
                    f"design.context.bind.{direction}{variant_suffix}.before{suffix}"
                ),
            )
            pending_topology_sha256 = output_sha256
            persist_checkpoint()
            directed_task = transform_task(
                alternative,
                alternative_task,
                direction,
            )
            transformed = self._action(
                f"schematic.transform.topology-delta.{direction}{variant_suffix}{suffix}",
                lambda: self.adapter.transform_schematic(directed_task),
            )
            after = self._action(
                f"schematic.inspect.topology-{direction}{variant_suffix}.after{suffix}",
                lambda: self.adapter.inspect_schematic(output_task),
            )
            assert_topology(after, output_sha256, f"{direction} output")
            audit = validate_topology_execution_readback(
                before.data,
                after.data,
                directed_task.topology_delta,
            )
            self._action(
                f"schematic.transform.topology-delta.{direction}{variant_suffix}.audit{suffix}",
                lambda: AdapterResult(
                    data={
                        "transform_readback": transformed.data,
                        "contract_audit": audit.model_dump(mode="json"),
                    },
                    evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                ),
            )
            self._bind_design_context(
                output_task,
                after,
                action_name=(
                    f"design.context.bind.{direction}{variant_suffix}.after{suffix}"
                ),
            )
            expected_topology_sha256 = output_sha256
            expected_topology_variant_id = output_variant_id
            pending_topology_sha256 = None
            expected_oa_instance_parameters = self._targeted_instance_parameters(
                after,
                output_task,
            )
            persist_checkpoint()

        def progress_for_variant(
            _variant_task: TaskSpec,
        ) -> Callable[
            [
                str,
                int,
                _CandidateInput,
                list[CandidateEvaluation],
                _AppliedCandidateState | None,
            ],
            None,
        ]:
            def progress(
                event: str,
                index: int,
                candidate: _CandidateInput,
                _evaluations: list[CandidateEvaluation],
                readback: _AppliedCandidateState | None,
            ) -> None:
                nonlocal expected_oa_parameters
                nonlocal expected_oa_instance_parameters
                nonlocal next_candidate_index
                nonlocal pending_oa_parameters
                nonlocal pending_oa_instance_parameters
                nonlocal active_candidate_index
                nonlocal next_analysis_stage_index
                nonlocal active_candidate_stages
                if event == "started":
                    if task.analysis_stages:
                        if active_candidate_index not in {None, index}:
                            raise RuntimeError(
                                "checkpoint active topology candidate changed before completion"
                            )
                        active_candidate_index = index
                    pending_oa_parameters = {}
                    pending_oa_instance_parameters = {
                        instance: dict(parameters)
                        for instance, parameters in candidate.instance_parameters.items()
                    }
                elif event == "staged":
                    if readback is None:
                        raise RuntimeError(
                            "candidate stage did not return OA readback"
                        )
                    expected_oa_parameters = readback.oa_parameters
                    expected_oa_instance_parameters = (
                        readback.oa_instance_parameters
                    )
                    pending_oa_parameters = None
                    pending_oa_instance_parameters = None
                elif event == "completed":
                    next_candidate_index = index + 1
                    active_candidate_index = None
                    next_analysis_stage_index = 1
                    active_candidate_stages = []
                    pending_oa_parameters = None
                    pending_oa_instance_parameters = None
                persist_checkpoint()

            return progress

        def analysis_stage_progress_for_variant(
            index: int,
            _candidate: _CandidateInput,
            stages: list[AnalysisStageEvaluation],
            next_stage_index: int,
        ) -> None:
            nonlocal active_candidate_index
            nonlocal active_candidate_stages
            nonlocal next_analysis_stage_index
            active_candidate_index = index
            active_candidate_stages = list(stages)
            next_analysis_stage_index = next_stage_index
            persist_checkpoint()

        def verify_final(
            variant_task: TaskSpec,
            expected_sha256: str,
        ) -> None:
            if expected_oa_instance_parameters:
                result = self._action(
                    "schematic.inspect.final",
                    lambda: self.adapter.verify_parameters(
                        variant_task,
                        expected_oa_instance_parameters,
                    ),
                )
                confirmed = self._confirmed_instance_parameters(
                    result.data,
                    "confirmed_instance_parameters",
                )
                if confirmed != expected_oa_instance_parameters:
                    raise RuntimeError(
                        "final OA readback does not match the confirmed instance "
                        "parameter write"
                    )
            else:
                result = self._action(
                    "schematic.inspect.final",
                    lambda: self.adapter.inspect_schematic(variant_task),
                )
            assert_topology(result, expected_sha256, "final")
            self._bind_design_context(
                variant_task,
                result,
                action_name="design.context.bind.final",
            )

        def recover_initial_baseline() -> None:
            nonlocal expected_oa_parameters
            nonlocal expected_oa_instance_parameters
            nonlocal expected_topology_sha256
            nonlocal pending_topology_sha256
            nonlocal expected_topology_variant_id
            current = self._action(
                "schematic.inspect.recovery",
                lambda: self.adapter.inspect_schematic(baseline_task),
            )
            current_sha256 = inspection_sha256(current)
            if current_sha256 in alternative_by_sha256:
                recovery_alternative, recovery_task = alternative_by_sha256[
                    current_sha256
                ]
                action_variant = (
                    ""
                    if len(alternative_variants) == 1
                    else f".{recovery_alternative.id}"
                )
                self._bind_design_context(
                    recovery_task,
                    current,
                    action_name=(
                        "design.context.bind.recovery.alternative"
                        + action_variant
                    ),
                )
                actual = self._targeted_instance_parameters(
                    current,
                    recovery_task,
                )
                self._validate_topology_refinement_resume_parameters(
                    checkpoint_state(),
                    recovery_task,
                    actual,
                )
                apply_state(
                    "parameters.restore.recovery.alternative" + action_variant,
                    recovery_task,
                    merged_state(
                        initial_instance_parameters,
                        alternative_fixed_parameters[recovery_alternative.id],
                    ),
                )
                apply_topology(
                    recovery_alternative,
                    recovery_task,
                    "inverse",
                    recovery=True,
                )
            elif current_sha256 == baseline_sha256:
                self._bind_design_context(
                    baseline_task,
                    current,
                    action_name="design.context.bind.recovery.baseline",
                )
                actual = self._targeted_instance_parameters(
                    current,
                    baseline_task,
                )
                self._validate_topology_refinement_resume_parameters(
                    checkpoint_state(),
                    baseline_task,
                    actual,
                )
                if actual != initial_instance_parameters:
                    apply_state(
                        "parameters.restore.recovery.baseline",
                        baseline_task,
                        initial_instance_parameters,
                    )
            else:
                raise RuntimeError(
                    "current topology is neither the declared baseline nor complete "
                    "alternative; refusing automatic recovery from an unknown or "
                    "partial topology fingerprint"
                )
            expected_oa_parameters = {}
            expected_oa_instance_parameters = {
                instance: dict(parameters)
                for instance, parameters in initial_instance_parameters.items()
            }
            expected_topology_sha256 = baseline_sha256
            expected_topology_variant_id = refinement.baseline_id
            pending_topology_sha256 = None
            verify_final(baseline_task, baseline_sha256)
            persist_checkpoint()

        try:
            self._action(
                "bridge.probe",
                lambda: self.adapter.probe(task.pdk_profile),
            )
            current = self._action(
                "schematic.inspect.resume"
                if resume_checkpoint is not None
                else "schematic.inspect.before",
                lambda: self.adapter.inspect_schematic(baseline_task),
            )
            current_sha256 = inspection_sha256(current)

            if resume_checkpoint is None:
                assert_topology(current, baseline_sha256, "initial baseline")
                self._bind_design_context(baseline_task, current)
                initial_parameters = self._semantic_parameters(
                    current,
                    baseline_task,
                )
                initial_instance_parameters = self._targeted_instance_parameters(
                    current,
                    baseline_task,
                )
                expected_oa_parameters = dict(initial_parameters)
                expected_oa_instance_parameters = {
                    instance: dict(parameters)
                    for instance, parameters in initial_instance_parameters.items()
                }
                expected_topology_sha256 = baseline_sha256
                expected_topology_variant_id = refinement.baseline_id
                persist_checkpoint()
            else:
                if current_sha256 in alternative_by_sha256:
                    resume_alternative, resume_task = alternative_by_sha256[
                        current_sha256
                    ]
                    action_variant = (
                        ""
                        if len(alternative_variants) == 1
                        else f".{resume_alternative.id}"
                    )
                    self._bind_design_context(
                        resume_task,
                        current,
                        action_name=(
                            "design.context.bind.resume.alternative"
                            + action_variant
                        ),
                    )
                    actual = self._targeted_instance_parameters(
                        current,
                        resume_task,
                    )
                    self._validate_topology_refinement_resume_parameters(
                        resume_checkpoint,
                        resume_task,
                        actual,
                    )
                    apply_state(
                        "parameters.restore.resume.alternative" + action_variant,
                        resume_task,
                        merged_state(
                            initial_instance_parameters,
                            alternative_fixed_parameters[resume_alternative.id],
                        ),
                    )
                    apply_topology(
                        resume_alternative,
                        resume_task,
                        "inverse",
                        recovery=True,
                    )
                elif current_sha256 == baseline_sha256:
                    self._bind_design_context(
                        baseline_task,
                        current,
                        action_name="design.context.bind.resume.baseline",
                    )
                    actual = self._targeted_instance_parameters(
                        current,
                        baseline_task,
                    )
                    self._validate_topology_refinement_resume_parameters(
                        resume_checkpoint,
                        baseline_task,
                        actual,
                    )
                    if actual != initial_instance_parameters:
                        apply_state(
                            "parameters.restore.resume.baseline",
                            baseline_task,
                            initial_instance_parameters,
                        )
                else:
                    raise RuntimeError(
                        "resume topology is neither the declared baseline nor complete "
                        "alternative; refusing automatic write"
                    )
                expected_oa_parameters = {}
                expected_oa_instance_parameters = {
                    instance: dict(parameters)
                    for instance, parameters in initial_instance_parameters.items()
                }
                expected_topology_sha256 = baseline_sha256
                expected_topology_variant_id = refinement.baseline_id
                pending_topology_sha256 = None
                notes.append(
                    "resumed the flattened topology-parameter search at index "
                    f"{next_candidate_index} after exact readback and baseline "
                    "normalization"
                )
                persist_checkpoint()

            if next_candidate_index <= parameter_candidate_count:
                candidates = self._run_candidates(
                    baseline_task,
                    stage_parameters=True,
                    evaluations=candidates,
                    start_index=next_candidate_index,
                    topology_variant_id=refinement.baseline_id,
                    topology_sha256=baseline_sha256,
                    progress=progress_for_variant(baseline_task),
                    stage_progress=analysis_stage_progress_for_variant,
                    resume_candidate_index=active_candidate_index,
                    resume_stages=active_candidate_stages,
                )

            current_alternative: Any | None = None
            current_alternative_task: TaskSpec | None = None
            for alternative_position, (
                alternative,
                alternative_task,
            ) in enumerate(alternative_variants, start=1):
                block_start = alternative_position * parameter_candidate_count + 1
                block_end = (alternative_position + 1) * parameter_candidate_count
                if next_candidate_index > block_end:
                    continue
                action_variant = (
                    ""
                    if len(alternative_variants) == 1
                    else f".{alternative.id}"
                )
                apply_state(
                    "parameters.restore.before-topology" + action_variant,
                    baseline_task,
                    initial_instance_parameters,
                )
                apply_topology(alternative, alternative_task, "forward")
                current_alternative = alternative
                current_alternative_task = alternative_task
                candidates = self._run_candidates(
                    alternative_task,
                    stage_parameters=True,
                    evaluations=candidates,
                    start_index=max(next_candidate_index, block_start),
                    index_offset=alternative_position * parameter_candidate_count,
                    topology_variant_id=alternative.id,
                    topology_sha256=(
                        alternative.topology_delta.contract.expected_after_sha256
                    ),
                    progress=progress_for_variant(alternative_task),
                    stage_progress=analysis_stage_progress_for_variant,
                    resume_candidate_index=active_candidate_index,
                    resume_stages=active_candidate_stages,
                )
                if alternative_position < len(alternative_variants):
                    apply_state(
                        "parameters.restore.alternative-before-inverse"
                        + action_variant,
                        alternative_task,
                        merged_state(
                            initial_instance_parameters,
                            alternative_fixed_parameters[alternative.id],
                        ),
                    )
                    apply_topology(alternative, alternative_task, "inverse")
                    current_alternative = None
                    current_alternative_task = None

            status = self._note_candidate_failures(candidates, status, notes)
            complete_domain = (
                len(candidates) == declared_candidate_count
                and all(
                    candidate.analysis_complete
                    and candidate.evidence_source is not EvidenceSource.SYSTEM_EVENT
                    for candidate in candidates
                )
            )
            feasible = (
                [candidate for candidate in candidates if candidate.feasible]
                if complete_domain
                else []
            )
            if not complete_domain:
                status = RunStatus.PARTIAL
                notes.append(
                    "topology-parameter selection was withheld because all variants "
                    "did not complete the full declared domain"
                )
            elif not feasible:
                status = RunStatus.PARTIAL
                notes.append(
                    "no candidate in any declared topology met every constraint"
                )

            if feasible:
                selected = min(feasible, key=lambda item: self._rank(task, item))
                selected_parameters = dict(selected.parameters)
                selected_instance_parameters = {
                    instance: dict(parameters)
                    for instance, parameters in selected.instance_parameters.items()
                }
                selected_testbench_overrides = selected.testbench_overrides
                selected_testbench_override_evidence_source = (
                    selected.testbench_override_evidence_source
                )
                selected_metrics = dict(selected.metrics)
                selected_topology_variant_id = selected.topology_variant_id
                selected_topology_sha256 = selected.topology_sha256
                if selected.topology_variant_id == refinement.baseline_id:
                    selected_variant_task = baseline_task
                    if (
                        current_alternative is not None
                        and current_alternative_task is not None
                    ):
                        action_variant = (
                            ""
                            if len(alternative_variants) == 1
                            else f".{current_alternative.id}"
                        )
                        apply_state(
                            "parameters.restore.alternative-before-inverse"
                            + action_variant,
                            current_alternative_task,
                            merged_state(
                                initial_instance_parameters,
                                alternative_fixed_parameters[
                                    current_alternative.id
                                ],
                            ),
                        )
                        apply_topology(
                            current_alternative,
                            current_alternative_task,
                            "inverse",
                        )
                        current_alternative = None
                        current_alternative_task = None
                    apply_state(
                        "parameters.apply.best.baseline",
                        baseline_task,
                        selected.instance_parameters,
                    )
                    verify_final(baseline_task, baseline_sha256)
                else:
                    selected_alternative, selected_variant_task = alternative_by_id[
                        str(selected.topology_variant_id)
                    ]
                    if (
                        current_alternative is None
                        or current_alternative.id != selected_alternative.id
                    ):
                        if (
                            current_alternative is not None
                            and current_alternative_task is not None
                        ):
                            current_variant = (
                                ""
                                if len(alternative_variants) == 1
                                else f".{current_alternative.id}"
                            )
                            apply_state(
                                "parameters.restore.alternative-before-inverse"
                                + current_variant,
                                current_alternative_task,
                                merged_state(
                                    initial_instance_parameters,
                                    alternative_fixed_parameters[
                                        current_alternative.id
                                    ],
                                ),
                            )
                            apply_topology(
                                current_alternative,
                                current_alternative_task,
                                "inverse",
                            )
                        apply_state(
                            "parameters.restore.before-selected-topology",
                            baseline_task,
                            initial_instance_parameters,
                        )
                        apply_topology(
                            selected_alternative,
                            selected_variant_task,
                            "forward",
                        )
                        current_alternative = selected_alternative
                        current_alternative_task = selected_variant_task
                    selected_variant = (
                        ""
                        if len(alternative_variants) == 1
                        else f".{selected_alternative.id}"
                    )
                    apply_state(
                        "parameters.apply.best.alternative" + selected_variant,
                        selected_variant_task,
                        selected.instance_parameters,
                    )
                    verify_final(
                        selected_variant_task,
                        selected_alternative.topology_delta.contract.expected_after_sha256,
                    )
                if task.winner_verification is not None:
                    winner_verification = self._run_winner_verification(
                        task,
                        selected_variant_task,
                        selected,
                    )
                    if winner_verification.feasible:
                        notes.append(
                            "the nominal topology/parameter winner passed every "
                            "declared winner-only analysis and PVT condition"
                        )
                    else:
                        winner_verification_rejected = True
                        status = RunStatus.PARTIAL
                        recover_initial_baseline()
                        selected_parameters = None
                        selected_instance_parameters = None
                        selected_testbench_overrides = None
                        selected_testbench_override_evidence_source = None
                        selected_metrics = None
                        selected_topology_variant_id = None
                        selected_topology_sha256 = None
                        notes.append(
                            "the nominal topology/parameter winner failed or did not "
                            "complete winner-only verification; the exact initial "
                            "baseline was restored and no unverified runner-up was "
                            "silently promoted"
                        )
                notes.append(
                    "nominal selection was limited to the fully evaluated "
                    f"{1 + len(alternatives)}-topology discrete domain; no continuous "
                    "or global optimum "
                    "is claimed"
                )
            else:
                if (
                    current_alternative is not None
                    and current_alternative_task is not None
                ):
                    action_variant = (
                        ""
                        if len(alternative_variants) == 1
                        else f".{current_alternative.id}"
                    )
                    apply_state(
                        "parameters.restore.alternative-before-inverse"
                        + action_variant,
                        current_alternative_task,
                        merged_state(
                            initial_instance_parameters,
                            alternative_fixed_parameters[current_alternative.id],
                        ),
                    )
                    apply_topology(
                        current_alternative,
                        current_alternative_task,
                        "inverse",
                    )
                apply_state(
                    "parameters.restore.initial-baseline",
                    baseline_task,
                    initial_instance_parameters,
                )
                verify_final(baseline_task, baseline_sha256)
                notes.append(
                    "no topology or parameter candidate was committed; the exact "
                    "initial baseline topology and target parameters were restored"
                )
            persist_checkpoint(complete=True)
        except BaseException as exc:
            status = RunStatus.FAILED
            selected_parameters = None
            selected_instance_parameters = None
            selected_testbench_overrides = None
            selected_testbench_override_evidence_source = None
            selected_metrics = None
            selected_topology_variant_id = None
            selected_topology_sha256 = None
            notes.append(f"{type(exc).__name__}: {exc}")
            persist_checkpoint()
            try:
                if initial_instance_parameters:
                    recover_initial_baseline()
                    notes.append(
                        "the interrupted topology-parameter loop restored and "
                        "independently verified the exact initial baseline"
                    )
                    persist_checkpoint()
            except Exception as recovery_exc:
                notes.append(
                    "automatic topology-loop recovery failed; OA state remains "
                    "unverified and requires a fresh readback before resume: "
                    f"{type(recovery_exc).__name__}: {recovery_exc}"
                )
                persist_checkpoint()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

        finished = datetime.now(UTC)
        search_audit = self._search_audit(
            task,
            candidates,
            has_recommendation=selected_topology_variant_id is not None,
            declared_candidate_count=declared_candidate_count,
            topology_variant_count=1 + len(alternatives),
            recommendation_withheld_after_winner_verification=(
                winner_verification_rejected
            ),
        )
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
            selected_instance_parameters=selected_instance_parameters,
            selected_testbench_overrides=selected_testbench_overrides,
            selected_testbench_override_evidence_source=(
                selected_testbench_override_evidence_source
            ),
            selected_metrics=selected_metrics,
            selected_topology_variant_id=selected_topology_variant_id,
            selected_topology_sha256=selected_topology_sha256,
            winner_verification=winner_verification,
            search_audit=search_audit,
            notes=notes,
        )

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
        if task.topology_refinement is not None:
            return self._execute_topology_refinement(
                task,
                plan,
                checkpoint_path=checkpoint_path,
                resume_checkpoint=resume_checkpoint,
            )
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
        selected_instance_parameters: dict[str, dict[str, str]] | None = None
        selected_testbench_overrides: GenericTestbenchOverrides | None = None
        selected_testbench_override_evidence_source: EvidenceSource | None = None
        selected_metrics: dict[str, float] | None = None
        winner_verification: CandidateEvaluation | None = None
        winner_verification_rejected = False
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
        initial_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.initial_instance_parameters.items()
            }
            if resume_checkpoint is not None
            else {}
        )
        expected_oa_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.expected_oa_instance_parameters.items()
            }
            if resume_checkpoint is not None
            else {}
        )
        pending_oa_instance_parameters = (
            {
                instance: dict(parameters)
                for instance, parameters in resume_checkpoint.pending_oa_instance_parameters.items()
            }
            if resume_checkpoint is not None
            and resume_checkpoint.pending_oa_instance_parameters is not None
            else None
        )
        next_candidate_index = (
            resume_checkpoint.next_candidate_index
            if resume_checkpoint is not None
            else 1
        )

        active_candidate_index = (
            resume_checkpoint.active_candidate_index
            if resume_checkpoint is not None
            else None
        )
        next_analysis_stage_index = (
            resume_checkpoint.next_analysis_stage_index
            if resume_checkpoint is not None
            else 1
        )
        active_candidate_stages = (
            list(resume_checkpoint.active_candidate_stages)
            if resume_checkpoint is not None
            else []
        )

        def persist_checkpoint(*, complete: bool = False) -> None:
            if (
                checkpoint_path is None
                or not tuning
                or (not initial_parameters and not initial_instance_parameters)
            ):
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
                    initial_instance_parameters=initial_instance_parameters,
                    expected_oa_instance_parameters=(
                        expected_oa_instance_parameters
                    ),
                    pending_oa_instance_parameters=(
                        pending_oa_instance_parameters
                    ),
                    next_candidate_index=next_candidate_index,
                    active_candidate_index=active_candidate_index,
                    next_analysis_stage_index=next_analysis_stage_index,
                    active_candidate_stages=active_candidate_stages,
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
            candidate: _CandidateInput,
            _evaluations: list[CandidateEvaluation],
            readback: _AppliedCandidateState | None,
        ) -> None:
            nonlocal expected_oa_parameters
            nonlocal expected_oa_instance_parameters
            nonlocal next_candidate_index
            nonlocal pending_oa_parameters
            nonlocal pending_oa_instance_parameters
            nonlocal active_candidate_index
            nonlocal next_analysis_stage_index
            nonlocal active_candidate_stages
            if event == "started":
                if task.analysis_stages:
                    if active_candidate_index not in {None, index}:
                        raise RuntimeError(
                            "checkpoint active candidate changed before completion"
                        )
                    active_candidate_index = index
                pending_oa_parameters = (
                    semantic_candidate(candidate.parameters)
                    if candidate_oa_write
                    else None
                )
                pending_oa_instance_parameters = (
                    candidate.instance_parameters if candidate_oa_write else None
                )
            elif event == "staged":
                if readback is None:
                    raise RuntimeError("candidate stage did not return OA readback")
                expected_oa_parameters = readback.oa_parameters
                expected_oa_instance_parameters = (
                    readback.oa_instance_parameters
                )
                pending_oa_parameters = None
                pending_oa_instance_parameters = None
            elif event == "completed":
                next_candidate_index = index + 1
                active_candidate_index = None
                next_analysis_stage_index = 1
                active_candidate_stages = []
                pending_oa_parameters = None
                pending_oa_instance_parameters = None
            persist_checkpoint()

        def analysis_stage_progress(
            index: int,
            _candidate: _CandidateInput,
            stages: list[AnalysisStageEvaluation],
            next_stage_index: int,
        ) -> None:
            nonlocal active_candidate_index
            nonlocal active_candidate_stages
            nonlocal next_analysis_stage_index
            active_candidate_index = index
            active_candidate_stages = list(stages)
            next_analysis_stage_index = next_stage_index
            persist_checkpoint()

        def apply_with_checkpoint(
            action: str,
            parameters: dict[str, float],
            instance_parameters: dict[str, dict[str, str]],
        ) -> _AppliedCandidateState:
            nonlocal expected_oa_parameters
            nonlocal expected_oa_instance_parameters
            nonlocal pending_oa_parameters
            nonlocal pending_oa_instance_parameters
            pending_oa_parameters = semantic_candidate(parameters)
            pending_oa_instance_parameters = instance_parameters
            persist_checkpoint()
            candidate_task = self._candidate_task(task, instance_parameters)
            result = self._action(
                action,
                lambda: self.adapter.apply_parameters(candidate_task, parameters),
            )
            expected_oa_parameters = self._applied_semantic_parameters(
                result, candidate_task
            )
            expected_oa_instance_parameters = (
                self._applied_instance_parameter_state(result, candidate_task)
            )
            pending_oa_parameters = None
            pending_oa_instance_parameters = None
            persist_checkpoint()
            return _AppliedCandidateState(
                oa_parameters=expected_oa_parameters,
                oa_instance_parameters=expected_oa_instance_parameters,
            )

        try:
            if task.circuit is CircuitKind.NETLIST_PREVIEW:
                self._action(
                    "bridge.spectre.probe",
                    lambda: self.adapter.probe_simulator(task.pdk_profile),
                )
            else:
                self._action(
                    "bridge.probe", lambda: self.adapter.probe(task.pdk_profile)
                )
            operation = task.operation

            if operation is Operation.DEVICE_CHARACTERIZE:
                raw_characterization = self._action(
                    "device.characterize",
                    lambda: self.adapter.characterize_devices(task),
                )
                normalized_characterization = self._action(
                    "device.characterize.validate",
                    lambda: AdapterResult(
                        data=normalize_mos_characterization(
                            task,
                            raw_characterization.data,
                        ),
                        evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                    ),
                )
                holdout_audit = normalized_characterization.data["holdout_audit"]
                if not holdout_audit["passed"]:
                    status = RunStatus.PARTIAL
                    failed = [
                        item["id"]
                        for item in holdout_audit["points"]
                        if not item["passed"]
                    ]
                    notes.append(
                        "raw PDK characterization completed, but the declared "
                        "interpolation holdout gate failed: " + ", ".join(failed)
                    )
                notes.append(
                    "device characterization ran in standalone Spectre scratch; "
                    "no OA library, cell, or view was opened or modified"
                )
                notes.append(
                    "raw operating points and artifact hashes are eda_result; "
                    "width normalization and interpolation audit are "
                    "software_inference"
                )
            elif operation is Operation.SCHEMATIC_CREATE:
                self._action(
                    "schematic.create", lambda: self.adapter.create_schematic(task)
                )
                self._action(
                    "schematic.inspect", lambda: self.adapter.inspect_schematic(task)
                )
            elif operation is Operation.SCHEMATIC_INSPECT:
                inspected = self._action(
                    "schematic.inspect", lambda: self.adapter.inspect_schematic(task)
                )
                self._bind_design_context(task, inspected)
            elif operation is Operation.SCHEMATIC_SYMBOL_GENERATE:
                self._action(
                    "schematic.inspect.source",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._action(
                    "schematic.symbol.generate",
                    lambda: self.adapter.generate_schematic_symbol(task),
                )
                self._action(
                    "schematic.symbol.inspect",
                    lambda: self.adapter.inspect_schematic_symbol(task),
                )
            elif operation is Operation.SCHEMATIC_TRANSFORM:
                before = self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._bind_design_context(task, before)
                resolved_transform = task.resolved_schematic_transform_action()
                transform_action = "schematic.transform.inverter-testbench"
                if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
                    assert task.topology_delta is not None
                    transform_action = (
                        "schematic.transform.topology-delta."
                        f"{task.topology_delta.direction}"
                    )
                elif task.circuit is CircuitKind.COMMON_SOURCE:
                    transform_action = (
                        "schematic.transform.source-degeneration.remove"
                        if resolved_transform
                        is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                        else "schematic.transform.source-degeneration"
                    )
                elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                    if resolved_transform is SchematicTransformAction.ADD_TAIL_DEVICE:
                        transform_action = (
                            "schematic.transform.differential-pair-tail-device"
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                    ):
                        transform_action = (
                            "schematic.transform.differential-pair-source-"
                            "degeneration.remove"
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
                    ):
                        transform_action = (
                            "schematic.transform.differential-pair-current-"
                            "mirror-load"
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.RESTORE_RESISTIVE_LOAD
                    ):
                        transform_action = (
                            "schematic.transform.differential-pair-current-"
                            "mirror-load.remove"
                        )
                    else:
                        transform_action = (
                            "schematic.transform.differential-pair-source-"
                            "degeneration"
                        )
                transformed = self._action(
                    transform_action,
                    lambda: self.adapter.transform_schematic(task),
                )
                if (
                    resolved_transform
                    in {
                        SchematicTransformAction.REMOVE_SOURCE_DEGENERATION,
                        SchematicTransformAction.RESTORE_RESISTIVE_LOAD,
                    }
                    and task.schematic_transform is not None
                    and task.schematic_transform.expected_restored_placement_sha256
                    is not None
                ):
                    expected_placement = (
                        task.schematic_transform.expected_restored_placement_sha256
                    )
                    placement_after = transformed.data.get("placement_after")
                    actual_placement = (
                        placement_after.get("sha256")
                        if isinstance(placement_after, dict)
                        else None
                    )
                    if (
                        transformed.data.get("restored_placement_match") is not True
                        or actual_placement != expected_placement
                    ):
                        raise RuntimeError(
                            "restoring transform did not confirm the "
                            "declared restored placement fingerprint"
                        )
                after = self._action(
                    "schematic.inspect.after",
                    lambda: self.adapter.inspect_schematic(task),
                )
                if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
                    assert task.topology_delta is not None
                    partial_before = before.data.get("partial_prefix_state")
                    if partial_before is not None:
                        partial_resume = transformed.data.get(
                            "partial_prefix_resume"
                        )
                        if (
                            not isinstance(partial_resume, dict)
                            or partial_resume.get("status") != "resumed"
                        ):
                            raise RuntimeError(
                                "partial-prefix topology readback was verified, "
                                "but transform did not confirm an exact resume"
                            )
                        audit_data = {
                            **dict(transformed.data.get("contract_audit", {})),
                            "before_partial_prefix_state": partial_before,
                            "transform_partial_prefix_resume": partial_resume,
                        }
                    else:
                        audit = validate_topology_execution_readback(
                            before.data,
                            after.data,
                            task.topology_delta,
                        )
                        audit_data = audit.model_dump(mode="json")
                    self._action(
                        "schematic.transform.topology-delta.predeclared-audit",
                        lambda: AdapterResult(
                            data=audit_data,
                            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                        ),
                    )
                elif task.circuit is CircuitKind.INVERTER:
                    self._assert_inverter_testbench_delta(
                        before,
                        after,
                        float(task.parameters["vdd_v"]),
                        float(task.parameters["load_ff"]),
                    )
                elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                    if resolved_transform is SchematicTransformAction.ADD_TAIL_DEVICE:
                        self._assert_differential_pair_tail_device_delta(
                            before,
                            after,
                            float(task.parameters["tail_width_um"]),
                            float(task.parameters["tail_length_um"]),
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                    ):
                        self._assert_differential_pair_source_degeneration_removal_delta(
                            before, after
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
                    ):
                        self._assert_differential_pair_current_mirror_load_delta(
                            before,
                            after,
                            float(task.parameters["pmos_load_width_um"]),
                            float(task.parameters["pmos_load_length_um"]),
                        )
                    elif (
                        resolved_transform
                        is SchematicTransformAction.RESTORE_RESISTIVE_LOAD
                    ):
                        self._assert_differential_pair_resistive_load_restore_delta(
                            before,
                            after,
                            float(task.parameters["load_resistance_ohm"]),
                        )
                    else:
                        self._assert_differential_pair_source_degeneration_delta(
                            before,
                            after,
                            float(task.parameters["source_resistance_ohm"]),
                        )
                elif (
                    resolved_transform
                    is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                ):
                    self._assert_source_degeneration_removal_delta(before, after)
                else:
                    self._assert_source_degeneration_delta(
                        before,
                        after,
                        float(task.parameters["source_resistance_ohm"]),
                    )
                if task.circuit is not CircuitKind.EXISTING_SCHEMATIC:
                    self._action(
                        "schematic.transform.topology-delta.audit",
                        lambda: AdapterResult(
                            data=audit_derived_topology_delta(
                                f"{task.id}:{transform_action}",
                                before.data,
                                after.data,
                            ),
                            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                        ),
                    )
                selected_parameters = dict(task.parameters)
            elif operation is Operation.PARAMETERS_APPLY:
                before = self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._bind_design_context(task, before)
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
            elif operation is Operation.PARAMETERS_BINDING_DISCOVER:
                discovery = task.parameter_binding_discovery
                if discovery is None:
                    raise RuntimeError(
                        "parameter binding discovery contract disappeared after plan"
                    )
                expected = {
                    str(name): str(value)
                    for name, value in sorted(
                        discovery.expected_instance_parameters.items()
                    )
                }

                def exact_inspection_table(
                    result: AdapterResult,
                    *,
                    label: str,
                ) -> dict[str, str]:
                    raw = (result.data.get("instance_parameters") or {}).get(
                        discovery.instance
                    )
                    if not isinstance(raw, dict):
                        raise RuntimeError(
                            f"{label} is missing the complete target CDF table for "
                            f"{discovery.instance}"
                        )
                    table = {
                        str(name): str(value)
                        for name, value in sorted(raw.items())
                    }
                    if table != expected:
                        raise RuntimeError(
                            f"{label} target CDF table does not match the binding "
                            "discovery CAS baseline"
                        )
                    return table

                before = self._action(
                    "schematic.inspect.before",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._bind_design_context(task, before)
                exact_inspection_table(before, label="pre-probe OA readback")
                raw_discovery = self._action(
                    "parameters.binding.discover",
                    lambda: self.adapter.discover_parameter_binding(task),
                )
                data = raw_discovery.data
                if data.get("contract") != discovery.model_dump(mode="json"):
                    raise RuntimeError(
                        "binding discovery adapter did not echo the exact probe contract"
                    )
                if data.get("target") != task.target.model_dump(mode="json"):
                    raise RuntimeError(
                        "binding discovery adapter target does not match the task"
                    )
                if data.get("spectre_simulation_performed") is not False:
                    raise RuntimeError(
                        "binding discovery must stop after si netlisting and cannot "
                        "run Spectre"
                    )
                if data.get("oa_write_performed") is not True:
                    raise RuntimeError(
                        "binding discovery did not report its temporary OA write"
                    )
                real_eda_discovery = (
                    raw_discovery.evidence_source is EvidenceSource.EDA_RESULT
                )
                if real_eda_discovery and data.get("remote_compute_performed") is not True:
                    raise RuntimeError(
                        "real binding discovery did not report its three remote si "
                        "netlisting stages"
                    )
                restoration = data.get("restoration")
                if (
                    not isinstance(restoration, dict)
                    or restoration.get("oa_exact") is not True
                    or restoration.get("canonical_netlist_signature_exact") is not True
                    or restoration.get("verified") is not True
                ):
                    raise RuntimeError(
                        "binding discovery did not prove exact OA and canonical si "
                        "restoration"
                    )
                for stage_name in ("baseline", "probe", "restored"):
                    stage = data.get(stage_name)
                    if not isinstance(stage, dict):
                        raise RuntimeError(
                            f"binding discovery is missing the {stage_name} evidence"
                        )
                    if not isinstance(stage.get("oa_instance_parameters"), dict):
                        raise RuntimeError(
                            f"binding discovery {stage_name} lacks OA CDF evidence"
                        )
                    if not isinstance(
                        stage.get("netlist_instance_parameters"), dict
                    ):
                        raise RuntimeError(
                            f"binding discovery {stage_name} lacks si parameter evidence"
                        )
                    if not isinstance(stage.get("parameter_inventory"), dict):
                        raise RuntimeError(
                            f"binding discovery {stage_name} lacks the complete si "
                            "parameter inventory"
                        )
                    signature = stage.get("canonical_netlist_signature_sha256")
                    if not isinstance(signature, str) or len(signature) != 64:
                        raise RuntimeError(
                            f"binding discovery {stage_name} lacks a canonical si "
                            "signature"
                        )
                    if real_eda_discovery:
                        if stage.get("oa_source") != "bridge_readback":
                            raise RuntimeError(
                                f"binding discovery {stage_name} OA state is not "
                                "Bridge readback"
                            )
                        validate_parameter_binding_eda_stage(stage_name, stage)
                if data["baseline"]["oa_instance_parameters"] != expected:
                    raise RuntimeError(
                        "binding discovery baseline CDF table changed before netlisting"
                    )
                if data["restored"]["oa_instance_parameters"] != expected:
                    raise RuntimeError(
                        "binding discovery returned a non-baseline restored CDF table"
                    )
                probe_value = data["probe"]["oa_instance_parameters"].get(
                    discovery.oa_parameter
                )
                if probe_value is None or not spectre_values_equal(
                    probe_value,
                    discovery.probe_value,
                ):
                    raise RuntimeError(
                        "binding discovery probe evidence does not contain the "
                        "declared OA value"
                    )
                if (
                    data["baseline"].get("canonical_netlist_signature_sha256")
                    != data["restored"].get(
                        "canonical_netlist_signature_sha256"
                    )
                ):
                    raise RuntimeError(
                        "binding discovery canonical si signature did not restore"
                    )
                classification = data.get("classification")
                allowed_statuses = {
                    "direct_literal_binding",
                    "direct_literal_binding_with_derived_callbacks",
                    "single_netlist_parameter_nonliteral",
                    "callback_coupled",
                    "ambiguous_netlist_change",
                    "netlist_structure_changed",
                    "inert",
                }
                if (
                    not isinstance(classification, dict)
                    or classification.get("status") not in allowed_statuses
                    or classification.get("source") != "software_inference"
                    or classification.get("same_name_assumption_used", False)
                    is not False
                ):
                    raise RuntimeError(
                        "binding discovery returned an invalid differential "
                        "classification"
                    )
                independent_classification = classify_parameter_binding_probe(
                    discovery,
                    baseline_oa_parameters={
                        str(name): str(value)
                        for name, value in data["baseline"][
                            "oa_instance_parameters"
                        ].items()
                    },
                    probe_oa_parameters={
                        str(name): str(value)
                        for name, value in data["probe"][
                            "oa_instance_parameters"
                        ].items()
                    },
                    baseline_netlist_inventory=data["baseline"][
                        "parameter_inventory"
                    ],
                    probe_netlist_inventory=data["probe"]["parameter_inventory"],
                )
                if classification != independent_classification:
                    raise RuntimeError(
                        "binding discovery adapter classification does not match "
                        "the parent executor's independent evidence evaluation"
                    )
                promoted = classification.get("promoted_binding")
                promotable_statuses = {
                    "direct_literal_binding",
                    "direct_literal_binding_with_derived_callbacks",
                }
                if classification["status"] in promotable_statuses:
                    if (
                        not isinstance(promoted, dict)
                        or promoted.get("instance") != discovery.instance
                        or promoted.get("oa_parameter")
                        != discovery.oa_parameter
                        or not isinstance(promoted.get("netlist_parameter"), str)
                        or not promoted["netlist_parameter"]
                        or classification.get("literal_identity_verified") is not True
                    ):
                        raise RuntimeError(
                            "direct binding classification lacks one exact promoted "
                            "OA-to-si mapping"
                        )
                    if (
                        classification["status"]
                        == "direct_literal_binding_with_derived_callbacks"
                    ):
                        if (
                            classification.get("callback_effects_verified") is not True
                            or not classification.get("dependent_netlist_changes")
                            or len(
                                classification.get(
                                    "derived_callback_evidence", []
                                )
                            )
                            != len(classification["dependent_netlist_changes"])
                        ):
                            raise RuntimeError(
                                "derived-callback binding lacks complete same-instance "
                                "OA-to-si side-effect evidence"
                            )
                        notes.append(
                            "one direct literal OA-CDF to si binding was discovered "
                            "with fully mirrored same-instance derived callback "
                            "effects; the probe value was not retained in OA"
                        )
                    else:
                        notes.append(
                            "one direct literal OA-CDF to si binding was discovered; "
                            "the probe value was not retained in OA"
                        )
                else:
                    if promoted is not None:
                        raise RuntimeError(
                            "non-direct binding classification cannot promote a "
                            "netlist parameter"
                        )
                    status = RunStatus.PARTIAL
                    notes.append(
                        "the reversible probe completed, but no direct literal "
                        f"binding was promoted ({classification['status']})"
                    )
                self._action(
                    "parameters.binding.evaluate",
                    lambda: AdapterResult(
                        data={
                            "classification": classification,
                            "restoration": restoration,
                            "interrupted_probe_recovery": data.get(
                                "interrupted_probe_recovery"
                            ),
                            "probe_was_not_retained": True,
                        },
                        evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                    ),
                )
                after = self._action(
                    "schematic.inspect.after",
                    lambda: self.adapter.inspect_schematic(task),
                )
                self._bind_design_context(task, after)
                exact_inspection_table(after, label="post-probe OA readback")
            elif operation is Operation.ADE_PREPARE:
                prepared = self._action(
                    "ade.prepare", lambda: self.adapter.prepare_ade(task)
                )
                assert task.ade_prepare is not None
                expected_design = task.ade_prepare.design or task.target.model_copy(
                    update={"view": task.ade_prepare.design_view}
                )
                if (
                    prepared.data.get("persistent_view_confirmed") is not True
                    or prepared.data.get("existing_maestro_overwritten") is not False
                    or prepared.data.get("schematic_oa_write_performed") is not False
                    or prepared.data.get("maestro_oa_write_performed") is not True
                    or prepared.data.get("design_target_confirmed") is not True
                    or prepared.data.get("design_readback")
                    != expected_design.model_dump(mode="json")
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
                    or ran.data.get("runtime_directory_persisted") is not False
                    or ran.data.get("runtime_directory_restored") is not True
                    or ran.data.get("runtime_artifacts_restricted_to_data_xum")
                    is not True
                    or not str(ran.data.get("runtime_scratch_root") or "").startswith(
                        "/data/xum/"
                    )
                ):
                    raise RuntimeError(
                        "ADE run did not prove an exact background history without "
                        "OA or Maestro setup writes"
                    )
                callback_recovered = (
                    ran.data.get(
                        "callback_timeout_history_recovery_performed"
                    )
                    is True
                )
                callback_evidence = ran.data.get(
                    "callback_timeout_history_recovery_evidence"
                )
                if callback_recovered:
                    completed_logs = (
                        callback_evidence.get("completed_history_logs")
                        if isinstance(callback_evidence, dict)
                        else None
                    )
                    histories_before = (
                        callback_evidence.get("histories_before")
                        if isinstance(callback_evidence, dict)
                        else None
                    )
                    history = str(ran.data.get("history") or "")
                    histories_before_valid = (
                        isinstance(histories_before, list)
                        and all(
                            isinstance(item, str)
                            and re.fullmatch(r"[A-Za-z0-9_.-]+", item)
                            for item in histories_before
                        )
                        and histories_before
                        == sorted(set(histories_before))
                        and history not in histories_before
                    )
                    completed_log_paths = (
                        [str(item.get("path") or "") for item in completed_logs]
                        if isinstance(completed_logs, list)
                        and all(isinstance(item, dict) for item in completed_logs)
                        else []
                    )
                    if (
                        task.ade_run is None
                        or task.ade_run.resume_history is not None
                        or not task.ade_run.sweep_verification
                        or not task.ade_run.sweep_verification.corner_mode()
                        or ran.data.get("simulation_performed_by_this_invocation")
                        is not True
                        or ran.data.get("history_recovery_performed") is not False
                        or ran.data.get("run_status")
                        != "recovered_after_bridge_timeout"
                        or not isinstance(callback_evidence, dict)
                        or not histories_before_valid
                        or callback_evidence.get("method")
                        != (
                            "single_new_completed_history_log_after_bridge_timeout"
                        )
                        or not str(callback_evidence.get("bridge_timeout") or "")
                        or callback_evidence.get("new_histories") != [history]
                        or callback_evidence.get("evidence_sources")
                        != {
                            "history_log": "eda_result",
                            "selection": "software_inference",
                        }
                        or not isinstance(completed_logs, list)
                        or not completed_logs
                        or len(completed_log_paths)
                        != len(set(completed_log_paths))
                        or any(
                            not isinstance(item, dict)
                            or not str(item.get("path") or "").endswith(
                                f"/{history}.log"
                            )
                            or int(item.get("size_bytes") or 0) <= 0
                            or not re.fullmatch(
                                r"[0-9a-f]{64}",
                                str(item.get("sha256") or ""),
                            )
                            for item in completed_logs
                        )
                    ):
                        raise RuntimeError(
                            "ADE run callback-timeout recovery evidence was "
                            "incomplete or ambiguous"
                        )
                elif callback_evidence is not None:
                    raise RuntimeError(
                        "ADE run exposed callback-timeout recovery evidence without "
                        "declaring recovery"
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
                artifacts_complete = bool(
                    ran.data.get("artifact_manifest_complete", False)
                )
                if artifacts_complete:
                    manifest = ran.data.get("artifact_manifest")
                    counts = ran.data.get("artifact_counts")
                    history = str(ran.data.get("history") or "")
                    fingerprint = str(
                        ran.data.get("simulation_fingerprint_sha256") or ""
                    )
                    locations = ran.data.get("artifact_locations_checked")
                    manifest_directory = str(
                        ran.data.get("remote_manifest_directory") or ""
                    )
                    if (
                        ran.data.get("artifacts_captured") is not True
                        or ran.data.get("artifact_history")
                        != history
                        or ran.data.get("artifact_history_path_binding_verified")
                        is not True
                        or ran.data.get("artifact_runtime_input_binding_verified")
                        is not True
                        or ran.data.get("artifact_run_binding_verified") is not True
                        or not isinstance(manifest, list)
                        or not manifest
                        or not isinstance(counts, dict)
                        or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                        or not isinstance(locations, list)
                        or not locations
                        or not manifest_directory.startswith("/data/xum/")
                    ):
                        raise RuntimeError(
                            "ADE run artifact evidence was internally inconsistent"
                        )
                    required_categories = {
                        "simulator_input",
                        "eda_result",
                        "run_log",
                    }
                    actual_counts = {category: 0 for category in required_categories}
                    nonempty_input_names: set[str] = set()
                    nonempty_result = False
                    nonempty_log = False
                    for item in manifest:
                        if not isinstance(item, dict):
                            raise RuntimeError(
                                "ADE run artifact manifest contained a non-object row"
                            )
                        path = str(item.get("path") or "")
                        category = str(item.get("category") or "")
                        digest = str(item.get("sha256") or "")
                        size = item.get("size_bytes")
                        remote_paths = item.get("remote_paths")
                        if (
                            not path.startswith(f"{history}/")
                            or category not in required_categories | {"other"}
                            or not isinstance(size, int)
                            or size < 0
                            or not re.fullmatch(r"[0-9a-f]{64}", digest)
                            or not isinstance(remote_paths, list)
                            or not remote_paths
                            or any(
                                not isinstance(remote_path, str)
                                or not remote_path.startswith("/data/xum/")
                                for remote_path in remote_paths
                            )
                        ):
                            raise RuntimeError(
                                "ADE run artifact manifest row was internally "
                                "inconsistent"
                            )
                        if category in required_categories:
                            if item.get("evidence_source") != "eda_result":
                                raise RuntimeError(
                                    "ADE run artifact evidence source was not "
                                    "eda_result"
                                )
                            actual_counts[category] += 1
                        if category == "simulator_input" and size > 0:
                            nonempty_input_names.add(Path(path).name)
                        elif category == "eda_result" and size > 0:
                            nonempty_result = True
                        elif category == "run_log" and size > 0:
                            nonempty_log = True
                    for category in required_categories:
                        if int(counts.get(category, -1)) != actual_counts[category]:
                            raise RuntimeError(
                                "ADE run artifact category count did not match "
                                f"manifest rows for {category}"
                            )
                    if (
                        not {"netlist", "input.scs"}.issubset(
                            nonempty_input_names
                        )
                        or not nonempty_result
                        or not nonempty_log
                    ):
                        raise RuntimeError(
                            "ADE run artifact manifest lacked non-empty core input/"
                            "result/log evidence"
                        )
                    fingerprint_payload = sorted(
                        (
                            {"path": item["path"], "sha256": item["sha256"]}
                            for item in manifest
                            if item["category"] in required_categories
                        ),
                        key=lambda item: item["path"],
                    )
                    calculated_fingerprint = hashlib.sha256(
                        json.dumps(
                            fingerprint_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ).encode("utf-8")
                    ).hexdigest()
                    if calculated_fingerprint != fingerprint:
                        raise RuntimeError(
                            "ADE run simulation fingerprint did not match artifact "
                            "manifest"
                        )
                    for location in locations:
                        if not isinstance(location, dict) or not str(
                            location.get("remote_manifest_path") or ""
                        ).startswith(f"{manifest_directory}/"):
                            raise RuntimeError(
                                "ADE run artifact location metadata was internally "
                                "inconsistent"
                            )
                        binding = str(location.get("binding") or "exact_history")
                        if binding == "exact_history":
                            valid_binding = str(
                                location.get("history_root") or ""
                            ).endswith(f"/{history}")
                        elif binding == "unique_runtime_session":
                            valid_binding = (
                                location.get("history_root") is None
                                and location.get("source_location") == "runtime"
                                and bool(location.get("runtime_test"))
                                and str(location.get("tree_root") or "").startswith(
                                    f"{ran.data['runtime_scratch_root']}/"
                                )
                            )
                        else:
                            valid_binding = False
                        if not valid_binding:
                            raise RuntimeError(
                                "ADE run artifact location metadata was internally "
                                "inconsistent"
                            )
                if task.ade_run is not None and (
                    task.ade_run.require_artifact_manifest
                    and not artifacts_complete
                ):
                    raise RuntimeError(
                        "ADE run required an exact-history simulator input/result/"
                        "log manifest but the adapter did not provide one"
                    )
                if (
                    task.ade_run is not None
                    and task.ade_run.require_simulator_input_consistency
                ):
                    consistency = ran.data.get("simulator_input_consistency")
                    sources = ran.data.get(
                        "simulator_input_consistency_evidence_sources"
                    )
                    if (
                        ran.data.get("simulator_input_consistency_verified") is not True
                        or not isinstance(consistency, list)
                        or not consistency
                        or sources
                        != {
                            "maestro_design_and_oa": "bridge_readback",
                            "spectre_input": "eda_result",
                            "comparison": "software_inference",
                        }
                        or any(
                            not isinstance(item, dict)
                            or item.get("design_identity_verified") is not True
                            or item.get("instance_set_verified") is not True
                            or item.get("node_connectivity_verified") is not True
                            or item.get("raw_parameter_mapping_verified") is not True
                            or not isinstance(item.get("verified_parameter_pairs"), int)
                            or item["verified_parameter_pairs"] <= 0
                            or not re.fullmatch(
                                r"[0-9a-f]{64}",
                                str(item.get("input_sha256") or ""),
                            )
                            or not re.fullmatch(
                                r"[0-9a-f]{64}",
                                str(item.get("comparison_sha256") or ""),
                            )
                            for item in consistency
                        )
                    ):
                        raise RuntimeError(
                            "ADE run did not prove the requested OA-to-input.scs "
                            "consistency"
                        )
                if (
                    task.ade_run is not None
                    and task.ade_run.sweep_verification is not None
                ):
                    self._assert_ade_sweep_evidence(task, ran.data)
                    if ran.data.get("native_sweep_database_binding_verified") is True:
                        notes.append(
                            "verified every declared native Maestro sweep point "
                            "against saved variable scope readback, one symbolic "
                            "runtime Spectre input per test, OA parameter references, "
                            "the exact-history RDB/completion log, and structured "
                            "point parameters and outputs"
                        )
                    else:
                        notes.append(
                            "verified every declared native Maestro sweep point "
                            "against saved variable scope readback, exact-history "
                            "input.scs, OA parameter references, non-empty results, "
                            "and structured point parameters"
                        )
                if task.ade_run is not None and task.ade_run.result_mapping is not None:
                    mapped_evaluations: list[CandidateEvaluation] = []

                    def evaluate_ade_results() -> AdapterResult:
                        nonlocal mapped_evaluations
                        mapped_evaluations, details = self._evaluate_ade_result_mapping(
                            task, ran.data
                        )
                        return AdapterResult(
                            data=details,
                            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
                        )

                    self._action("ade.results.evaluate", evaluate_ade_results)
                    candidates.extend(mapped_evaluations)
                    feasible = [item for item in mapped_evaluations if item.feasible]
                    if feasible:
                        selected = min(feasible, key=lambda item: self._rank(task, item))
                        selected_parameters = selected.parameters
                        selected_metrics = selected.metrics
                        notes.append(
                            "mapped exact-history Maestro scalar outputs into VDA "
                            "metrics and constraints for every declared sweep point; "
                            "raw values remain EDA evidence and normalization/selection "
                            "are software inference"
                        )
                    else:
                        status = RunStatus.PARTIAL
                        notes.append(
                            "all declared Maestro sweep points were evaluated, but none "
                            "satisfied every VDA constraint; no point was selected"
                        )
                if ran.data.get("history_recovery_performed") is True:
                    notes.append(
                        "recovered the explicitly named Maestro history without "
                        "rerunning simulation; no GUI focus, setup save, or OA write "
                        "was performed"
                    )
                elif callback_recovered:
                    notes.append(
                        "the Bridge completion wait timed out after simulation, but "
                        "VDA identified exactly one newly named completed Maestro "
                        "history and validated it without rerunning or writing OA"
                    )
                else:
                    notes.append(
                        "executed the saved Maestro setup in a background session; no "
                        "GUI focus, setup save, or OA write was performed"
                    )
                if task.ade_run is not None and task.ade_run.result_mapping is None:
                    notes.append(
                        "ADE output/spec values, runtime input hashes, and exact-history "
                        "result/log hashes are EDA evidence but are not mapped to VDA "
                        "constraints because this task declared no result_mapping"
                    )
                notes.append(
                    "history naming and overwrite behavior came from the saved "
                    "Maestro setup; VDA did not change it or prove history uniqueness"
                )
                if not structured or not artifacts_complete:
                    status = RunStatus.PARTIAL
                    notes.append(
                        "Maestro history completed without every requested structured "
                        "result/artifact evidence gate; completion alone was not "
                        "treated as design success"
                    )
            elif operation is Operation.ADE_VARIABLES_APPLY:
                patched = self._action(
                    "ade.variables.apply",
                    lambda: self.adapter.apply_ade_variables(task),
                )
                if task.ade_variables is None:
                    raise RuntimeError("ADE variable settings disappeared at execution")
                expected_tests = list(task.ade_variables.expected_tests)
                requested = {
                    update.evidence_key(): {
                        "name": update.name,
                        "scope": update.scope.value,
                        "scope_name": update.scope_name,
                        "expected_value": update.expected_value,
                        "value": update.value,
                    }
                    for update in task.ade_variables.updates
                }
                expected_before = {
                    update.evidence_key(): update.expected_value
                    for update in task.ade_variables.updates
                }
                expected_after = {
                    update.evidence_key(): update.value
                    for update in task.ade_variables.updates
                }
                requested_selections = {
                    update.name: {
                        "expected_enabled": update.expected_enabled,
                        "enabled": update.enabled,
                    }
                    for update in task.ade_variables.global_selection_updates
                }
                expected_selection_before = {
                    update.name: update.expected_enabled
                    for update in task.ade_variables.global_selection_updates
                }
                expected_selection_after = {
                    update.name: update.enabled
                    for update in task.ade_variables.global_selection_updates
                }
                selection_names = set(expected_selection_after)
                expected_scopes = list(
                    dict.fromkeys(
                        update.scope.value for update in task.ade_variables.updates
                    )
                )
                expected_corners = (
                    None
                    if task.ade_variables.expected_corners is None
                    else list(task.ade_variables.expected_corners)
                )
                expected_readback_methods = {
                    scope: (
                        "bridge_public_get_var"
                        if scope == "global"
                        else (
                            "cadence_maeGetVar_string_typeValue_"
                            "via_bridge_skill_channel"
                            if scope == "test"
                            else (
                                "cadence_axlGetCorner_axlGetVarValue_"
                                "via_bridge_skill_channel"
                            )
                        )
                    )
                    for scope in expected_scopes
                }
                expected_write_methods = {
                    scope: (
                        "bridge_public_set_var_global"
                        if scope == "global"
                        else (
                            "bridge_public_set_var_list_typeValue"
                            if scope == "test"
                            else "cadence_axlPutVar_via_bridge_skill_channel"
                        )
                    )
                    for scope in expected_scopes
                }
                selection_states = [
                    patched.data.get("global_variable_selection_state_before"),
                    patched.data.get("global_variable_selection_state_immediate"),
                    patched.data.get("global_variable_selection_state_persisted"),
                ]
                selection_state_valid = not selection_names and selection_states == [
                    None,
                    None,
                    None,
                ]
                if selection_names:
                    selection_state_valid = all(
                        isinstance(state, dict)
                        and isinstance(state.get("enabled"), list)
                        and isinstance(state.get("disabled"), list)
                        and all(
                            isinstance(name, str) and name
                            for name in [
                                *state["enabled"],
                                *state["disabled"],
                            ]
                        )
                        and len(state["enabled"])
                        == len(set(state["enabled"]))
                        and len(state["disabled"])
                        == len(set(state["disabled"]))
                        and not (
                            set(state["enabled"]) & set(state["disabled"])
                        )
                        for state in selection_states
                    )
                    if selection_state_valid:
                        before_state, immediate_state, persisted_state = selection_states
                        assert isinstance(before_state, dict)
                        assert isinstance(immediate_state, dict)
                        assert isinstance(persisted_state, dict)

                        def undeclared(state: dict[str, Any]) -> dict[str, bool]:
                            enabled = set(state["enabled"])
                            disabled = set(state["disabled"])
                            return {
                                name: name in enabled
                                for name in enabled | disabled
                                if name not in selection_names
                            }

                        def declared(state: dict[str, Any]) -> dict[str, bool] | None:
                            enabled = set(state["enabled"])
                            disabled = set(state["disabled"])
                            if any(
                                (name in enabled) + (name in disabled) != 1
                                for name in selection_names
                            ):
                                return None
                            return {
                                name: name in enabled for name in selection_names
                            }

                        selection_state_valid = (
                            immediate_state == persisted_state
                            and undeclared(before_state) == undeclared(immediate_state)
                            and declared(before_state) == expected_selection_before
                            and declared(immediate_state) == expected_selection_after
                            and declared(persisted_state) == expected_selection_after
                        )
                if (
                    patched.evidence_source is not EvidenceSource.BRIDGE_READBACK
                    or patched.data.get("requested_evidence_source") != "user_input"
                    or patched.data.get("confirmed_evidence_source")
                    != "bridge_readback"
                    or patched.data.get("existing_maestro_replaced") is not False
                    or patched.data.get("schematic_oa_write_performed") is not False
                    or patched.data.get("maestro_setup_write_performed") is not True
                    or patched.data.get("automated_simulation_performed") is not False
                    or patched.data.get("variable_scope")
                    != (
                        "none"
                        if not expected_scopes
                        else (
                            "global"
                            if expected_scopes == ["global"]
                            else "declared_scopes"
                        )
                    )
                    or patched.data.get("variable_scopes") != expected_scopes
                    or patched.data.get("expected_tests") != expected_tests
                    or patched.data.get("tests_readback_before") != expected_tests
                    or patched.data.get("tests_readback_after") != expected_tests
                    or patched.data.get("expected_corners") != expected_corners
                    or patched.data.get("corners_readback_before")
                    != expected_corners
                    or patched.data.get("corners_readback_after")
                    != expected_corners
                    or patched.data.get("requested_variable_updates") != requested
                    or patched.data.get("requested_global_selection_updates")
                    != requested_selections
                    or patched.data.get("before_variables") != expected_before
                    or patched.data.get("immediate_variables") != expected_after
                    or patched.data.get("persisted_variables") != expected_after
                    or patched.data.get("global_variable_selection_before")
                    != expected_selection_before
                    or patched.data.get("global_variable_selection_immediate")
                    != expected_selection_after
                    or patched.data.get("global_variable_selection_persisted")
                    != expected_selection_after
                    or not selection_state_valid
                    or patched.data.get(
                        "global_variable_selection_readback_method"
                    )
                    != (
                        "cadence_maeGetSetup_enabled_variables_"
                        "via_bridge_skill_channel"
                        if selection_names
                        else None
                    )
                    or patched.data.get("global_variable_selection_write_method")
                    != (
                        "cadence_maeSetSetup_variables_via_bridge_skill_channel"
                        if selection_names
                        else None
                    )
                    or patched.data.get(
                        "global_variable_selection_preserved_undeclared"
                    )
                    is not bool(selection_names)
                    or patched.data.get("declared_scoped_values_verified") is not True
                    or patched.data.get("declared_global_selections_verified")
                    is not True
                    or patched.data.get("variable_readback_methods")
                    != expected_readback_methods
                    or patched.data.get("variable_write_methods")
                    != expected_write_methods
                    or (
                        patched.data.get("test_or_corner_overrides_checked")
                        is not False
                    )
                    or (
                        patched.data.get("unlisted_scope_overrides_checked")
                        is not False
                    )
                    or (
                        patched.data.get("effective_simulation_value_verified")
                        is not False
                    )
                ):
                    raise RuntimeError(
                        "ADE variable patch did not prove an exact declared-scope "
                        "compare-and-swap with persistent readback"
                    )
                notes.append(
                    "patched only the declared Maestro variable scopes/selections "
                    "after exact old-value preconditions plus selection-state "
                    "preconditions and an independent "
                    "persisted readback"
                )
                notes.append(
                    "no simulation, schematic write, test, analysis, output, or "
                    "corner-membership modification was performed"
                )
                notes.append(
                    "comma-separated values can request a native sweep at their "
                    "declared scope, but unlisted scope overrides and effective "
                    "simulator values were not verified by this operation"
                )
            elif operation is Operation.ADE_CORNERS_APPLY:
                patched = self._action(
                    "ade.corners.apply",
                    lambda: self.adapter.apply_ade_corners(task),
                )
                if task.ade_corners is None:
                    raise RuntimeError("ADE corner settings disappeared at execution")
                expected_tests = list(task.ade_corners.expected_tests)
                expected_before = list(task.ade_corners.expected_corners)
                additions = [addition.name for addition in task.ade_corners.additions]
                expected_after = [*expected_before, *additions]
                before_fingerprint = patched.data.get(
                    "before_target_fingerprint_sha256"
                )
                after_fingerprint = patched.data.get(
                    "after_target_fingerprint_sha256"
                )
                valid_fingerprints = all(
                    isinstance(value, str)
                    and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                    for value in (before_fingerprint, after_fingerprint)
                ) and before_fingerprint != after_fingerprint
                immediate = patched.data.get("immediate_corner_states")
                expected_immediate = []
                for index, name in enumerate(additions, start=1):
                    membership = [*expected_before, *additions[:index]]
                    expected_immediate.append(
                        {
                            "name": name,
                            "all_corners": membership,
                            "enabled_corners": membership,
                        }
                    )
                if (
                    patched.evidence_source is not EvidenceSource.BRIDGE_READBACK
                    or patched.data.get("target")
                    != task.target.model_dump(mode="json")
                    or patched.data.get("requested_evidence_source") != "user_input"
                    or patched.data.get("confirmed_evidence_source")
                    != "bridge_readback"
                    or patched.data.get("expected_tests") != expected_tests
                    or patched.data.get("tests_readback_before") != expected_tests
                    or patched.data.get("tests_readback_after") != expected_tests
                    or patched.data.get("expected_corners_before")
                    != expected_before
                    or patched.data.get("requested_corner_additions") != additions
                    or patched.data.get("all_corners_readback_before")
                    != expected_before
                    or patched.data.get("enabled_corners_readback_before")
                    != expected_before
                    or patched.data.get("all_corners_readback_after")
                    != expected_after
                    or patched.data.get("enabled_corners_readback_after")
                    != expected_after
                    or immediate != expected_immediate
                    or not valid_fingerprints
                    or patched.data.get("corner_write_method")
                    != "bridge_public_set_corner"
                    or patched.data.get("corner_readback_method")
                    != (
                        "cadence_maeGetSetup_all_and_enabled_via_bridge_skill_channel"
                    )
                    or patched.data.get("existing_corners_modified") is not False
                    or patched.data.get("existing_maestro_replaced") is not False
                    or patched.data.get("model_files_modified") is not False
                    or patched.data.get("variables_modified") is not False
                    or patched.data.get("analyses_or_outputs_modified") is not False
                    or patched.data.get("schematic_oa_write_performed") is not False
                    or patched.data.get("maestro_setup_write_performed") is not True
                    or patched.data.get("automated_simulation_performed") is not False
                ):
                    raise RuntimeError(
                        "ADE corner patch did not prove exact add-only membership "
                        "preconditions and persistent readback"
                    )
                notes.append(
                    "added only the declared enabled Maestro corners after exact "
                    "all/enabled membership preconditions and an independent reopen"
                )
                notes.append(
                    "this operation did not attach process models, set scoped "
                    "variables, run simulation, or modify the schematic"
                )
            elif operation is Operation.ADE_SETUP_APPLY:
                patched = self._action(
                    "ade.setup.apply",
                    lambda: self.adapter.apply_ade_setup(task),
                )
                if task.ade_setup is None:
                    raise RuntimeError("ADE setup settings disappeared at execution")
                expected_tests = list(task.ade_setup.expected_tests)
                requested_analyses = [
                    update.model_dump(mode="json")
                    for update in task.ade_setup.analyses
                ]
                requested_outputs = [
                    output.model_dump(mode="json")
                    for output in task.ade_setup.outputs
                ]
                expected_before_analyses = [
                    {
                        "test": update.test,
                        "analysis": update.analysis,
                        "state": (
                            None
                            if update.expected is None
                            else update.expected.model_dump(mode="json")
                        ),
                    }
                    for update in task.ade_setup.analyses
                ]
                expected_before_outputs = [
                    {"test": output.test, "name": output.name, "state": None}
                    for output in task.ade_setup.outputs
                ]
                before_fingerprint = patched.data.get(
                    "before_target_fingerprint_sha256"
                )
                after_fingerprint = patched.data.get(
                    "after_target_fingerprint_sha256"
                )
                valid_fingerprints = all(
                    isinstance(value, str)
                    and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                    for value in (before_fingerprint, after_fingerprint)
                ) and before_fingerprint != after_fingerprint
                if (
                    patched.evidence_source is not EvidenceSource.BRIDGE_READBACK
                    or patched.data.get("target")
                    != task.target.model_dump(mode="json")
                    or patched.data.get("requested_evidence_source") != "user_input"
                    or patched.data.get("confirmed_evidence_source")
                    != "bridge_readback"
                    or patched.data.get("existing_maestro_replaced") is not False
                    or patched.data.get("existing_outputs_replaced") is not False
                    or patched.data.get("schematic_oa_write_performed") is not False
                    or patched.data.get("maestro_setup_write_performed") is not True
                    or patched.data.get("automated_simulation_performed") is not False
                    or patched.data.get("unlisted_setup_state_checked") is not False
                    or patched.data.get("full_setup_fingerprint_verified") is not False
                    or patched.data.get("analysis_write_method")
                    != "bridge_public_set_analysis"
                    or patched.data.get("analysis_readback_method")
                    != "cadence_maeGetAnalysis_via_bridge_skill_channel"
                    or patched.data.get("output_write_method")
                    != "bridge_public_add_output_and_set_spec"
                    or patched.data.get("output_readback_method")
                    != (
                        "cadence_maeGetTestOutputs_and_axlGetSpecData_"
                        "via_bridge_skill_channel"
                    )
                    or patched.data.get("expected_tests") != expected_tests
                    or patched.data.get("tests_readback_before") != expected_tests
                    or patched.data.get("tests_readback_after") != expected_tests
                    or patched.data.get("requested_analysis_updates")
                    != requested_analyses
                    or patched.data.get("requested_output_additions")
                    != requested_outputs
                    or patched.data.get("before_analyses")
                    != expected_before_analyses
                    or patched.data.get("before_outputs")
                    != expected_before_outputs
                    or not valid_fingerprints
                ):
                    raise RuntimeError(
                        "ADE setup patch did not prove the declared target, exact "
                        "preconditions, write boundary, and persistent fingerprints"
                    )

                immediate_analyses = patched.data.get("immediate_analyses")
                persisted_analyses = patched.data.get("persisted_analyses")
                immediate_outputs = patched.data.get("immediate_outputs")
                persisted_outputs = patched.data.get("persisted_outputs")
                if (
                    not isinstance(immediate_analyses, list)
                    or not isinstance(persisted_analyses, list)
                    or not isinstance(immediate_outputs, list)
                    or not isinstance(persisted_outputs, list)
                    or persisted_analyses != immediate_analyses
                    or persisted_outputs != immediate_outputs
                    or len(immediate_analyses) != len(requested_analyses)
                    or len(immediate_outputs) != len(requested_outputs)
                ):
                    raise RuntimeError(
                        "ADE setup patch did not prove identical immediate and "
                        "independently reopened targeted state"
                    )
                for request, entry in zip(
                    requested_analyses, immediate_analyses, strict=True
                ):
                    if (
                        not isinstance(entry, dict)
                        or entry.get("test") != request["test"]
                        or entry.get("analysis") != request["analysis"]
                        or not isinstance(entry.get("state"), dict)
                    ):
                        raise RuntimeError(
                            "ADE setup analysis readback identity is incomplete"
                        )
                    state = entry["state"]
                    options = state.get("options")
                    if (
                        state.get("enabled") != request["enabled"]
                        or not isinstance(options, dict)
                    ):
                        raise RuntimeError(
                            "ADE setup analysis readback does not match enable/options"
                        )
                    expected = request.get("expected")
                    if isinstance(expected, dict):
                        desired = dict(expected.get("options") or {})
                        desired.update(request.get("options") or {})
                        options_match = options == desired
                    else:
                        options_match = all(
                            options.get(name) == value
                            for name, value in (request.get("options") or {}).items()
                        )
                    if not options_match:
                        raise RuntimeError(
                            "ADE setup analysis option readback does not match request"
                        )
                for request, entry in zip(
                    requested_outputs, immediate_outputs, strict=True
                ):
                    if (
                        not isinstance(entry, dict)
                        or entry.get("test") != request["test"]
                        or entry.get("name") != request["name"]
                        or not isinstance(entry.get("state"), dict)
                    ):
                        raise RuntimeError(
                            "ADE setup output readback identity is incomplete"
                        )
                    state = entry["state"]
                    if (
                        state.get("name") != request["name"]
                        or request["output_type"]
                        not in {state.get("type"), state.get("eval_type")}
                        or state.get("signal_name") != request.get("signal_name")
                        or not calculator_expressions_equal(
                            state.get("expression"), request.get("expression")
                        )
                        or state.get("spec") != request.get("spec")
                    ):
                        raise RuntimeError(
                            "ADE setup output/spec readback does not match request"
                        )
                notes.append(
                    "patched only the declared Maestro analyses and absent named "
                    "outputs after exact preconditions, then verified one saved setup "
                    "through an independent reopen"
                )
                notes.append(
                    "existing outputs, variables, corners, tests, schematic, and "
                    "unlisted setup state were not replaced or modified"
                )
                notes.append(
                    "no simulation was run; configured analyses and outputs remain "
                    "Bridge readback until an ADE run supplies EDA results"
                )
            elif operation is Operation.SIMULATION_RUN:
                if task.circuit is not CircuitKind.NETLIST_PREVIEW:
                    before = self._action(
                        "schematic.inspect.before",
                        lambda: self.adapter.inspect_schematic(task),
                    )
                    self._bind_design_context(task, before)
                    self._assert_expected_target_topology(before, task)
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
                        failed_conditions = [
                            condition.name
                            for condition in selected.operating_conditions
                            if not condition.feasible
                        ]
                        if failed_conditions:
                            notes.append(
                                "simulation completed, but not every declared "
                                "operating condition met the full specification: "
                                + ", ".join(failed_conditions)
                            )
                        elif selected.analysis_issues:
                            notes.append(
                                "simulation completed but required analysis metrics were "
                                "incomplete: " + "; ".join(selected.analysis_issues)
                            )
                        else:
                            notes.append(
                                "simulation completed but one or more constraints failed"
                            )
                if task.circuit is CircuitKind.NETLIST_PREVIEW:
                    notes.append(
                        "standalone Spectre preview used the declared structured "
                        "netlist directly; no OA, si, Maestro, or schematic state was "
                        "accessed"
                    )
                    notes.append(
                        "raw simulator values are eda_result and A/B comparisons are "
                        "software_inference; the selected topology still requires "
                        "OA-to-si-to-Spectre or ADE validation before design closure"
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
                self._bind_design_context(task, before)
                self._assert_expected_target_topology(before, task)
                current_parameters = self._semantic_parameters(before, task)
                current_instance_parameters = self._targeted_instance_parameters(
                    before, task
                )
                if resume_checkpoint is None:
                    initial_parameters = current_parameters
                    expected_oa_parameters = current_parameters
                    initial_instance_parameters = current_instance_parameters
                    expected_oa_instance_parameters = current_instance_parameters
                    persist_checkpoint()
                else:
                    self._validate_resume_oa_state(
                        resume_checkpoint,
                        task,
                        current_parameters,
                        current_instance_parameters,
                    )
                    expected_oa_parameters = current_parameters
                    expected_oa_instance_parameters = current_instance_parameters
                    pending_oa_parameters = None
                    pending_oa_instance_parameters = None
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
                        stage_progress=analysis_stage_progress,
                        resume_candidate_index=active_candidate_index,
                        resume_stages=active_candidate_stages,
                    )
                    status = self._note_candidate_failures(candidates, status, notes)
                    status = self._note_budget_exhaustion(task, status, notes)
                    feasible = [
                        candidate for candidate in candidates if candidate.feasible
                    ]
                    if not feasible:
                        status = RunStatus.PARTIAL
                        if task.operating_conditions:
                            notes.append(
                                "no candidate met the full specification across every "
                                "declared operating condition"
                            )
                        if candidate_oa_write:
                            apply_with_checkpoint(
                                "parameters.restore",
                                initial_parameters,
                                initial_instance_parameters,
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
                        selected_instance_parameters = (
                            selected.instance_parameters or None
                        )
                        selected_testbench_overrides = selected.testbench_overrides
                        selected_testbench_override_evidence_source = (
                            selected.testbench_override_evidence_source
                        )
                        selected_metrics = selected.metrics
                        if task.operating_conditions:
                            notes.append(
                                "selected candidate satisfied the full specification in "
                                "all declared operating conditions; aggregate metrics "
                                "and objective use conservative worst-case software "
                                "inference"
                            )
                        if candidate_oa_write:
                            apply_with_checkpoint(
                                "parameters.apply.best",
                                selected.parameters,
                                selected.instance_parameters,
                            )
                        else:
                            expected_oa_parameters = current_parameters
                            notes.append(
                                "selected the best testbench condition without changing "
                                "OA parameters"
                            )
                        if task.winner_verification is not None:
                            if not candidate_oa_write:
                                raise RuntimeError(
                                    "winner verification cannot run without a staged OA "
                                    "candidate"
                                )
                            try:
                                winner_verification = self._run_winner_verification(
                                    task,
                                    task,
                                    selected,
                                )
                            except BaseException:
                                selected_parameters = None
                                selected_instance_parameters = None
                                selected_testbench_overrides = None
                                selected_testbench_override_evidence_source = None
                                selected_metrics = None
                                raise
                            if winner_verification.feasible:
                                notes.append(
                                    "the nominal provisional winner passed every "
                                    "declared winner-only analysis and PVT condition; "
                                    "only that candidate incurred the expensive Gate"
                                )
                            else:
                                winner_verification_rejected = True
                                status = RunStatus.PARTIAL
                                apply_with_checkpoint(
                                    "parameters.restore.winner-verification",
                                    initial_parameters,
                                    initial_instance_parameters,
                                )
                                selected_parameters = None
                                selected_instance_parameters = None
                                selected_testbench_overrides = None
                                selected_testbench_override_evidence_source = None
                                selected_metrics = None
                                notes.append(
                                    "the nominal provisional winner failed or did not "
                                    "complete winner-only verification; VDA restored the "
                                    "exact initial OA parameters and withheld a robust "
                                    "recommendation. Other nominal candidates were not "
                                    "silently promoted without the same Gate"
                                )
                        parameters_finalized = True
                    if expected_oa_instance_parameters:
                        after = self._action(
                            "schematic.inspect.after",
                            lambda: self.adapter.verify_parameters(
                                task, expected_oa_instance_parameters
                            ),
                        )
                        final_instance_parameters = (
                            self._confirmed_instance_parameters(
                                after.data, "confirmed_instance_parameters"
                            )
                        )
                    else:
                        after = self._action(
                            "schematic.inspect.after",
                            lambda: self.adapter.inspect_schematic(task),
                        )
                        final_instance_parameters = {}
                    self._assert_expected_target_topology(
                        after,
                        task,
                        before_candidate_write=False,
                    )
                    final_parameters = self._semantic_parameters(after, task)
                    if not self._same_parameters(
                        expected_oa_parameters, final_parameters
                    ):
                        raise RuntimeError(
                            "final OA readback does not match the confirmed parameter write"
                        )
                    if not self._same_instance_parameters(
                        expected_oa_instance_parameters,
                        final_instance_parameters,
                    ):
                        raise RuntimeError(
                            "final OA readback does not match the confirmed instance "
                            "parameter write"
                        )
                    expected_oa_parameters = final_parameters
                    persist_checkpoint(complete=True)
                except BaseException:
                    persist_checkpoint()
                    if not parameters_finalized and candidate_oa_write:
                        try:
                            apply_with_checkpoint(
                                "parameters.restore.interrupted",
                                initial_parameters,
                                initial_instance_parameters,
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
        search_audit = (
            self._search_audit(
                task,
                candidates,
                has_recommendation=selected_parameters is not None,
                recommendation_withheld_after_winner_verification=(
                    winner_verification_rejected
                ),
            )
            if task.operation in {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}
            else None
        )
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
            selected_instance_parameters=selected_instance_parameters,
            selected_testbench_overrides=selected_testbench_overrides,
            selected_testbench_override_evidence_source=(
                selected_testbench_override_evidence_source
            ),
            selected_metrics=selected_metrics,
            winner_verification=winner_verification,
            search_audit=search_audit,
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
