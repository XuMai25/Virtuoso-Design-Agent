"""Stable domain contracts shared by the planner, executor, and adapters."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(str, Enum):
    SCHEMATIC_CREATE = "schematic.create"
    SCHEMATIC_INSPECT = "schematic.inspect"
    PARAMETERS_APPLY = "parameters.apply"
    SIMULATION_RUN = "simulation.run"
    DESIGN_TUNE = "design.tune"
    DESIGN_CLOSE_LOOP = "design.close_loop"


class CircuitKind(str, Enum):
    INVERTER = "inverter"
    COMMON_SOURCE = "common_source"
    SOURCE_DEGENERATED_COMMON_SOURCE = "source_degenerated_common_source"
    DIFFERENTIAL_PAIR = "differential_pair"


class Relation(str, Enum):
    LESS_OR_EQUAL = "<="
    GREATER_OR_EQUAL = ">="
    TARGET = "target"


class ObjectiveGoal(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SideEffect(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    REMOTE_COMPUTE = "remote_compute"
    REMOTE_WRITE = "remote_write"


class EvidenceSource(str, Enum):
    USER_INPUT = "user_input"
    SOFTWARE_INFERENCE = "software_inference"
    BRIDGE_READBACK = "bridge_readback"
    EDA_RESULT = "eda_result"
    SYSTEM_EVENT = "system_event"


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class DesignTarget(StrictModel):
    library: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    cell: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    view: str = Field(default="schematic", pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")


class MetricConstraint(StrictModel):
    metric: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    relation: Relation
    value: float
    tolerance: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def target_requires_tolerance(self) -> "MetricConstraint":
        if self.relation is Relation.TARGET and self.tolerance <= 0:
            raise ValueError("target constraints require tolerance > 0")
        return self


class Objective(StrictModel):
    metric: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    goal: ObjectiveGoal = ObjectiveGoal.MINIMIZE


class ExecutionLimits(StrictModel):
    max_iterations: int = Field(default=12, ge=1, le=64)
    timeout_seconds: int = Field(default=600, ge=10, le=7200)


class SafetyPolicy(StrictModel):
    allow_remote_compute: bool = False
    allow_remote_write: bool = False
    allowed_library: str | None = None
    required_cell_prefix: str = "vda_"
    replace_existing: bool = False

    @field_validator("allowed_library")
    @classmethod
    def validate_allowed_library(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("allowed_library cannot be empty")
        return value


_TUNING_OPERATIONS = {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}


class TaskSpec(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation: Operation
    circuit: CircuitKind
    target: DesignTarget
    pdk_profile: str = Field(default="nics4304_tsmc28", min_length=1)
    parameters: dict[str, float] = Field(default_factory=dict)
    parameter_space: dict[str, list[float]] = Field(default_factory=dict)
    constraints: list[MetricConstraint] = Field(default_factory=list)
    objective: Objective | None = None
    create_if_missing: bool = False
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, float]) -> dict[str, float]:
        for name, number in value.items():
            if not name or number <= 0:
                raise ValueError(f"parameter {name!r} must be positive")
        return value

    @field_validator("parameter_space")
    @classmethod
    def validate_parameter_space(
        cls, value: dict[str, list[float]]
    ) -> dict[str, list[float]]:
        for name, values in value.items():
            if not name or not values:
                raise ValueError(f"parameter space {name!r} cannot be empty")
            if any(number <= 0 for number in values):
                raise ValueError(f"parameter space {name!r} must contain positive values")
            if len(values) != len(set(values)):
                raise ValueError(f"parameter space {name!r} contains duplicates")
        return value

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> "TaskSpec":
        if self.operation is Operation.PARAMETERS_APPLY and not self.parameters:
            raise ValueError("parameters.apply requires parameters")
        if self.operation in _TUNING_OPERATIONS:
            if not self.parameter_space:
                raise ValueError(f"{self.operation.value} requires parameter_space")
            if not self.constraints:
                raise ValueError(f"{self.operation.value} requires constraints")
        if self.operation is Operation.SIMULATION_RUN and not self.parameters:
            raise ValueError("simulation.run requires parameters")
        return self


class PlanStep(StrictModel):
    id: str
    capability: str
    description: str
    side_effect: SideEffect


class ExecutionPlan(StrictModel):
    task_id: str
    operation: Operation
    circuit: CircuitKind
    steps: list[PlanStep]
    confirmation_token: str

    @property
    def requires_remote_write(self) -> bool:
        return any(step.side_effect is SideEffect.REMOTE_WRITE for step in self.steps)

    @property
    def requires_remote_compute(self) -> bool:
        return any(step.side_effect is SideEffect.REMOTE_COMPUTE for step in self.steps)


class ConstraintEvaluation(StrictModel):
    metric: str
    relation: Relation
    expected: float
    actual: float | None
    tolerance: float
    passed: bool
    normalized_violation: float
    reason: str | None = None


class CandidateEvaluation(StrictModel):
    index: int
    parameters: dict[str, float]
    metrics: dict[str, float]
    constraints: list[ConstraintEvaluation]
    feasible: bool
    total_violation: float
    objective_value: float | None = None
    evidence_source: EvidenceSource


class ActionRecord(StrictModel):
    action: str
    status: str
    started_at: datetime
    finished_at: datetime
    evidence_source: EvidenceSource
    details: dict[str, Any] = Field(default_factory=dict)


class RunRecord(StrictModel):
    schema_version: int = 1
    task_id: str
    plan_token: str
    adapter: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    actions: list[ActionRecord]
    candidates: list[CandidateEvaluation] = Field(default_factory=list)
    selected_parameters: dict[str, float] | None = None
    selected_metrics: dict[str, float] | None = None
    notes: list[str] = Field(default_factory=list)


class PdkProfile(StrictModel):
    name: str
    tech_library: str
    nmos_cell: str
    pmos_cell: str
    model_include: str
    model_section: str
    default_vdd_v: float = Field(gt=0)
    default_load_ff: float = Field(gt=0)
    default_length_um: float = Field(gt=0)
