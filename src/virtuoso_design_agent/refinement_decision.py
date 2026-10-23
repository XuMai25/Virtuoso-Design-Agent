"""Conservative local refinement utility, not a statistical confidence model."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, field_validator

from .models import EvidenceSource, StrictModel
from .op_relinearization import OperatingPointRelinearizationResult


class RefinementDecisionPolicy(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    minimum_objective_improvement: float = Field(default=0.0, ge=0.0)
    minimum_relative_improvement_percent: float = Field(default=0.0, ge=0.0)
    observed_error_multiplier: float = Field(default=1.0, ge=0.0)
    minimum_predicted_slack: dict[StrictStr, float] = Field(default_factory=dict)
    maximum_estimated_action_seconds: float | None = Field(default=None, gt=0.0)
    evidence_source: Literal[EvidenceSource.USER_INPUT, EvidenceSource.SOFTWARE_INFERENCE] = EvidenceSource.SOFTWARE_INFERENCE

    @field_validator("minimum_predicted_slack")
    @classmethod
    def nonnegative_slack(cls, values: dict[str, float]) -> dict[str, float]:
        if any(not name or not math.isfinite(value) or value < 0 for name, value in values.items()):
            raise ValueError("predicted slack limits must be named, finite and nonnegative")
        return values


def assess_refinement_utility(
    result: OperatingPointRelinearizationResult,
    anchor_objective: float,
    policy: RefinementDecisionPolicy,
    *,
    historical_action_seconds: float,
    historical_candidate_count: int,
) -> dict:
    """Use observed residuals as a risk heuristic; never infer agent/token time."""
    metric = result.objective.metric
    modeled_constraints = {item.metric for item in result.constraints}
    unknown_slacks = set(policy.minimum_predicted_slack) - modeled_constraints
    if unknown_slacks:
        raise ValueError("utility slack limits require modeled constraints: " + ", ".join(sorted(unknown_slacks)))
    errors = [abs(c.predicted_value - c.actual_value) for c in result.holdout_comparisons if c.metric == metric]
    observed_error = max(errors) if errors else None
    threshold = max(policy.minimum_objective_improvement,
                    abs(anchor_objective) * policy.minimum_relative_improvement_percent / 100,
                    (observed_error or 0.0) * policy.observed_error_multiplier)
    direction = 1 if result.objective.goal.value == "maximize" else -1
    points = []
    for proposal in result.proposals:
        if proposal.previously_evaluated or not proposal.predicted_feasible:
            continue
        gain = direction * (proposal.predicted_objective_value - anchor_objective)
        if gain <= 0 or math.isclose(proposal.predicted_objective_value, anchor_objective, rel_tol=1e-12, abs_tol=0.0):
            continue
        slacks: dict[str, float] = {}
        for constraint in proposal.constraints:
            actual = constraint.actual
            if actual is None:
                continue
            if constraint.relation.value == ">=":
                slack = actual - constraint.expected + constraint.tolerance
            elif constraint.relation.value == "<=":
                slack = constraint.expected - actual + constraint.tolerance
            else:
                slack = constraint.tolerance - abs(actual - constraint.expected)
            slacks[constraint.metric] = min(slacks.get(constraint.metric, slack), slack)
        points.append({"parameters": dict(proposal.parameters), "predicted_objective_improvement": gain,
                       "predicted_slack": slacks, "gain_resolved_by_policy": gain > threshold,
                       "headroom_sufficient": all(slacks.get(name, -math.inf) >= limit
                                                  for name, limit in policy.minimum_predicted_slack.items())})
    count = len(result.candidate_set.candidates) if result.candidate_set else 0
    per_candidate = (historical_action_seconds / historical_candidate_count
                     if math.isfinite(historical_action_seconds) and historical_action_seconds > 0
                     and historical_candidate_count > 0 else None)
    estimate = per_candidate * count if per_candidate is not None else None
    resolved = [p for p in points if p["gain_resolved_by_policy"]]
    worthy = [p for p in resolved if p["headroom_sufficient"]]
    status = ("no_predicted_improvement" if not points else
              "model_error_evidence_missing" if observed_error is None else
              "predicted_gain_not_resolved" if not resolved else
              "predicted_headroom_insufficient" if not worthy else
              "action_cost_unknown" if policy.maximum_estimated_action_seconds is not None and estimate is None else
              "action_cost_budget_exceeded" if policy.maximum_estimated_action_seconds is not None
                  and estimate > policy.maximum_estimated_action_seconds else "worth_evaluating")
    return {"status": status, "task_draft_allowed": status == "worth_evaluating",
            "source": "software_inference", "policy": policy.model_dump(mode="json"),
            "objective_metric": metric, "anchor_objective": anchor_objective,
            "maximum_observed_holdout_absolute_error": observed_error,
            "required_objective_improvement": threshold,
            "predicted_improving_count": len(points), "worthwhile_candidate_count": len(worthy),
            "best_predicted_improvement": max((p["predicted_objective_improvement"] for p in points), default=None),
            "candidate_assessments": points,
            "cost": {"historical_action_seconds_per_candidate": per_candidate,
                     "compiled_candidate_count": count, "estimated_action_seconds": estimate,
                     "agent_thinking_seconds": None,
                     "scope": "historical mean including recorded overhead; not incremental simulator time or end-to-end cost"},
            "limitations": ["Observed holdout residual is a risk heuristic, not a confidence interval or error bound.",
                            "Passing is permission to prepare a draft, not remote execution or measured improvement.",
                            "Unmodeled constraints and current OA/PDK still require their own evidence."]}
