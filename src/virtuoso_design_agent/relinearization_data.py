"""Explain local response-data gaps without fitting, changing a split, or executing."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .models import RunRecord
from .op_relinearization import (
    RelinearizationDataRequest, _candidate_map, _constant_inputs, _features,
    _fit_slopes, _parameter_value, _same_parameter_coordinates, _validate_source_candidate,
)


def inspect_relinearization_data(request: RelinearizationDataRequest, run_path: Path) -> dict:
    raw = run_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != request.expected_source_run_sha256:
        raise ValueError("source run SHA-256 does not match data-check request")
    run = RunRecord.model_validate_json(raw)
    if run.task_id != request.expected_source_task_id:
        raise ValueError("source run task id does not match data-check request")
    by_index = _candidate_map(run)
    anchor = by_index.get(request.anchor_candidate_index)
    if anchor is None:
        raise ValueError("data-check anchor is missing from the source run")
    _validate_source_candidate(anchor, request)
    issues, ignored, valid = [], [], {}

    def issue(kind: str, detail: str, **facts):
        issues.append({"kind": kind, "detail": detail, **facts})

    declared = set(request.training_candidate_indices) | set(request.holdout_candidate_indices)
    for candidate in run.candidates:
        try:
            _validate_source_candidate(candidate, request)
            _constant_inputs([anchor, candidate], request.parameters)
        except ValueError as error:
            row = {"candidate_index": candidate.index, "reason": str(error)}
            ignored.append(row)
            if candidate.index in declared:
                issue("declared_point_unusable", str(error), candidate_index=candidate.index)
        else:
            valid[candidate.index] = candidate
    for index in sorted(declared - set(by_index)):
        issue("declared_point_missing", "declared index is absent from the bound run", candidate_index=index)
    if run.adapter != "virtuoso-bridge-subprocess":
        issue("non_eda_run", "software-only runs cannot calibrate a physical response model")
    if run.status.value != "succeeded":
        issue("execution_reconciliation_required", "run must be reconciled before any fit or next execution")

    def unique(points):
        result = []
        for point in points:
            if not any(_same_parameter_coordinates(point, previous, request.parameters) for previous in result):
                result.append(point)
        return result

    training = unique([valid[i] for i in request.training_candidate_indices if i in valid])
    heldout = []
    for index in request.holdout_candidate_indices:
        if index not in valid:
            continue
        match = next((c for c in training if _same_parameter_coordinates(valid[index], c, request.parameters)), None)
        if match is not None:
            issue("holdout_repeats_training", "different indices do not reserve an unseen parameter tuple",
                  candidate_index=index, training_candidate_index=match.index)
        else:
            heldout.append(valid[index])
    heldout = unique(heldout)
    dimension = len(request.parameters)
    if len(training) < dimension + 1:
        issue("insufficient_training_count", "need anchor plus independent training directions",
              available_unique=len(training), required_count_lower_bound=dimension + 1)
    independence = False
    if training:
        try:
            # Only the input geometry's solvability is checked. No measured metric
            # response is fitted and no zero sensitivity is inferred or retained.
            _fit_slopes([_features(c, anchor, request.parameters) for c in training], [0.0] * len(training))
        except ValueError:
            issue("training_directions_not_independent", "declared training perturbations do not span all variables")
        else:
            independence = True
    if not heldout:
        issue("no_unseen_holdout", "reserve at least one complete, same-context, unseen held-out point")
    axes, fixed_axes, slices = [], [], []
    for parameter in request.parameters:
        base = _parameter_value(anchor, parameter)
        changed = lambda c: not math.isclose(_parameter_value(c, parameter), base, rel_tol=1e-12, abs_tol=0.0)
        train_coverage = any(changed(c) for c in training)
        holdout_coverage = any(changed(c) for c in heldout)
        unit = (parameter.instance_parameter.unit if parameter.instance_parameter else
                parameter.testbench_parameter.unit if parameter.testbench_parameter else None)
        axes.append({"name": parameter.name, "anchor": base, "unit": unit,
                     "training_varies": train_coverage, "unseen_holdout_varies": holdout_coverage})
        if not train_coverage:
            issue("training_axis_missing", "no training perturbation for this variable", parameter=parameter.name)
        if not holdout_coverage:
            issue("holdout_axis_missing", "no unseen holdout perturbation for this variable", parameter=parameter.name)
        if not any(changed(c) for c in valid.values()):
            fixed_axes.append({"name": parameter.name, "value": base, "unit": unit,
                               "statement": "may explicitly keep fixed; remaining variables still need independent validation"})
        others = [p for p in request.parameters if p.name != parameter.name]
        slice_points = [c for c in valid.values() if _same_parameter_coordinates(c, anchor, others)]
        values = sorted({_parameter_value(c, parameter) for c in slice_points})
        slices.append({"varying_parameter": parameter.name, "unit": unit,
                       "other_parameters_fixed_at_anchor": True,
                       "candidate_indices": [c.index for c in slice_points], "distinct_values": values,
                       "distinct_point_count": len(values), "one_axis_point_count_lower_bound": 3,
                       "count_lower_bound_met": len(values) >= 3,
                       "fit_or_holdout_validated": False})
    if run_path.read_bytes() != raw:
        raise ValueError("source run changed during data inspection")
    return {"schema_version": 1, "kind": "relinearization_data_check", "source": "software_inference",
            "read_only": True, "fit_performed": False, "task_produced": False, "execution_authorized": False,
            "run": {"path": str(run_path.resolve()), "sha256": digest}, "task_id": run.task_id,
            "status": "data_ready_for_fitting" if not issues else "model_data_insufficient",
            "strict_fit_inputs_ready": not issues, "declared_variable_count": dimension,
            "point_count_lower_bound": dimension + 2,
            "training_candidate_indices": request.training_candidate_indices,
            "holdout_candidate_indices": request.holdout_candidate_indices,
            "unique_usable_training_count": len(training), "unseen_usable_holdout_count": len(heldout),
            "joint_training_independence": independence, "axes": axes, "fixed_axis_options": fixed_axes,
            "historical_one_axis_slices": slices, "issues": issues, "ignored_points": ignored,
            "next_requirements": ["Keep the declared split and error limits; do not recycle training tuples as holdouts.",
                                  "Use OP/physical reasoning to select missing directions; no automatic sampling or execution.",
                                  "A count or rank check is not a fitted-error, working-region or performance guarantee."]}


def render_relinearization_data(report: dict) -> str:
    lines = ["## 局部模型数据检查", "", f"状态：{report['status']}；未拟合、未生成任务、未执行。",
             f"独立变量 {report['declared_variable_count']} 个；点数下界 {report['point_count_lower_bound']}，"
             "点数够也不保证满秩、留出误差或物理适用性。", "",
             "| 变量 | 锚点 | 单位 | 训练有扰动 | 未见点留出有扰动 |",
             "| --- | ---: | --- | --- | --- |"]
    for axis in report["axes"]:
        lines.append(f"| {axis['name']} | {axis['anchor']} | {axis['unit'] or '原任务语义单位'} | "
                     f"{axis['training_varies']} | {axis['unseen_holdout_varies']} |")
    lines.extend(["", *[f"- {row['kind']}: {row['detail']}；{row.get('parameter', '')}" for row in report["issues"]]])
    for row in report["historical_one_axis_slices"]:
        lines.append(f"- 仅 {row['varying_parameter']} 变化、其余固定在锚点的旧记录：{row['candidate_indices']}；"
                     f"不同值 {len(row['distinct_values'])} 个。尚未替你改分组或验证单变量模型。")
    return "\n".join(lines) + "\n"
