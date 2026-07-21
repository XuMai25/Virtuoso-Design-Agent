"""Bounded execution loop shared by partial tasks and full L5A closure."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
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
from .spectre_values import spectre_values_equal

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
    def _assert_ade_sweep_evidence(task: TaskSpec, data: dict[str, Any]) -> None:
        assert task.ade_run is not None
        sweep = task.ade_run.sweep_verification
        if sweep is None:
            return
        expected_variables = {
            variable.evidence_key(): variable.expected_value
            for variable in sweep.variables
        }
        expected_methods = {
            variable.evidence_key(): (
                "bridge_public_get_var"
                if variable.scope.value == "global"
                else "cadence_maeGetVar_via_bridge_skill_channel"
            )
            for variable in sweep.variables
        }
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
            setup_readbacks.append(readback)
        if setup_readbacks[0] != setup_readbacks[1]:
            raise RuntimeError("ADE sweep setup changed during background execution")
        if (
            data.get("sweep_setup_readback_evidence_source") != "bridge_readback"
            or data.get("expected_sweep_evidence_source") != "user_input"
            or data.get("sweep_point_consistency_verified") is not True
            or data.get("effective_simulation_values_verified") is not True
            or data.get("exact_point_input_result_binding_verified") is not True
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
        input_consistency_by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
        for item in raw_input_consistency:
            if not isinstance(item, dict):
                raise RuntimeError("ADE sweep OA/input comparison is not an object")
            try:
                item_point = int(item.get("point"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "ADE sweep OA/input comparison has an invalid point"
                ) from exc
            item_test = str(item.get("test") or "")
            item_path = str(item.get("input_path") or "")
            key = (item_point, item_test, item_path)
            if (
                item_test not in binding_counts
                or not item_path
                or key in input_consistency_by_key
                or item.get("effective_sweep_bindings_verified") is not True
                or item.get("verified_sweep_binding_pairs")
                != binding_counts[item_test]
            ):
                raise RuntimeError(
                    "ADE sweep OA/input comparison did not uniquely verify every "
                    "declared binding"
                )
            input_consistency_by_key[key] = item
        expected_points = {point.point: point for point in sweep.points}
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
                if not isinstance(inputs, list) or not inputs:
                    raise RuntimeError(
                        f"ADE sweep point {point_number} lacked input evidence"
                    )
                if not isinstance(results, list) or not results:
                    raise RuntimeError(
                        f"ADE sweep point {point_number} lacked result evidence"
                    )
                for input_item in inputs:
                    if not isinstance(input_item, dict):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} input is malformed"
                        )
                    input_path = str(input_item.get("path") or "")
                    manifest_item = manifest_by_path.get(input_path)
                    comparison = input_consistency_by_key.get(
                        (point_number, test_name, input_path)
                    )
                    if (
                        not isinstance(manifest_item, dict)
                        or manifest_item.get("category") != "simulator_input"
                        or manifest_item.get("binding") != "exact_history_path"
                        or not input_path.startswith(f"{history}/{point_number}/")
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
                    ):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} input did not match "
                            "the exact-history manifest and OA/input comparison"
                        )
                    seen_input_consistency.add(
                        (point_number, test_name, input_path)
                    )
                for result_item in results:
                    if not isinstance(result_item, dict):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} result is malformed"
                        )
                    result_path = str(result_item.get("path") or "")
                    manifest_item = manifest_by_path.get(result_path)
                    if (
                        not isinstance(manifest_item, dict)
                        or manifest_item.get("category") != "eda_result"
                        or manifest_item.get("binding") != "exact_history_path"
                        or not result_path.startswith(f"{history}/{point_number}/")
                        or manifest_item.get("sha256") != result_item.get("sha256")
                        or manifest_item.get("size_bytes")
                        != result_item.get("size_bytes")
                        or int(result_item.get("size_bytes") or 0) <= 0
                    ):
                        raise RuntimeError(
                            f"ADE sweep point {point_number} result did not match "
                            "the exact-history manifest"
                        )
        if seen_points != set(expected_points):
            raise RuntimeError("ADE sweep evidence omitted a declared point")
        if seen_input_consistency != set(input_consistency_by_key):
            raise RuntimeError(
                "ADE sweep OA/input comparisons did not map one-to-one to point inputs"
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
                transform_action = (
                    "schematic.transform.inverter-testbench"
                    if task.circuit is CircuitKind.INVERTER
                    else "schematic.transform.source-degeneration"
                )
                self._action(
                    transform_action,
                    lambda: self.adapter.transform_schematic(task),
                )
                after = self._action(
                    "schematic.inspect.after",
                    lambda: self.adapter.inspect_schematic(task),
                )
                if task.circuit is CircuitKind.INVERTER:
                    self._assert_inverter_testbench_delta(
                        before,
                        after,
                        float(task.parameters["vdd_v"]),
                        float(task.parameters["load_ff"]),
                    )
                else:
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
                    notes.append(
                        "verified every declared native Maestro sweep point against "
                        "saved variable scope readback, exact-history input.scs, OA "
                        "parameter references, non-empty results, and structured "
                        "point parameters"
                    )
                if ran.data.get("history_recovery_performed") is True:
                    notes.append(
                        "recovered the explicitly named Maestro history without "
                        "rerunning simulation; no GUI focus, setup save, or OA write "
                        "was performed"
                    )
                else:
                    notes.append(
                        "executed the saved Maestro setup in a background session; no "
                        "GUI focus, setup save, or OA write was performed"
                    )
                notes.append(
                    "ADE output/spec values and exact-history input/result/log hashes "
                    "are EDA evidence but are not yet mapped to VDA constraints by "
                    "ade.run"
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
                        else "cadence_maeGetVar_via_bridge_skill_channel"
                    )
                    for scope in expected_scopes
                }
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
                        "global"
                        if expected_scopes == ["global"]
                        else "declared_scopes"
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
                    or patched.data.get("before_variables") != expected_before
                    or patched.data.get("immediate_variables") != expected_after
                    or patched.data.get("persisted_variables") != expected_after
                    or patched.data.get("declared_scoped_values_verified") is not True
                    or patched.data.get("variable_readback_methods")
                    != expected_readback_methods
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
                    "patched only the declared Maestro variable scopes after exact "
                    "old-value preconditions and an independent persisted readback"
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
                        or state.get("expression") != request.get("expression")
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
