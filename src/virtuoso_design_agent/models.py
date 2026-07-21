"""Stable domain contracts shared by the planner, executor, and adapters."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(str, Enum):
    SCHEMATIC_CREATE = "schematic.create"
    SCHEMATIC_INSPECT = "schematic.inspect"
    SCHEMATIC_TRANSFORM = "schematic.transform"
    PARAMETERS_APPLY = "parameters.apply"
    ADE_PREPARE = "ade.prepare"
    ADE_CAPTURE = "ade.capture"
    SIMULATION_RUN = "simulation.run"
    DESIGN_TUNE = "design.tune"
    DESIGN_CLOSE_LOOP = "design.close_loop"


class CircuitKind(str, Enum):
    EXISTING_SCHEMATIC = "existing_schematic"
    INVERTER = "inverter"
    COMMON_SOURCE = "common_source"
    SOURCE_DEGENERATED_COMMON_SOURCE = "source_degenerated_common_source"
    DIFFERENTIAL_PAIR = "differential_pair"


class AnalysisKind(str, Enum):
    TRANSIENT = "transient"
    DC = "dc"
    AC = "ac"
    NOISE = "noise"
    QUALITY = "quality"


class AdeBackend(str, Enum):
    MAESTRO = "maestro"


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


class InstanceParameterUpdate(StrictModel):
    """Exact CDF/OA parameter strings requested for one existing instance."""

    instance: StrictStr = Field(min_length=1)
    parameters: dict[StrictStr, StrictStr]

    @field_validator("parameters")
    @classmethod
    def validate_parameter_strings(
        cls, value: dict[str, StrictStr]
    ) -> dict[str, StrictStr]:
        if not value:
            raise ValueError("instance parameter update cannot be empty")
        return value


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


class AcSweep(StrictModel):
    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    points_per_decade: int = Field(default=20, ge=1, le=1000)
    reference_points: int = Field(default=5, ge=2, le=20)
    max_reference_variation_db: float = Field(default=0.5, gt=0, le=3.0)

    @model_validator(mode="after")
    def stop_must_exceed_start(self) -> "AcSweep":
        if self.stop_hz <= self.start_hz:
            raise ValueError("AC sweep stop_hz must be greater than start_hz")
        return self


class LinearitySweep(StrictModel):
    frequency_hz: float = Field(gt=0)
    amplitudes_v: list[float] = Field(min_length=2, max_length=12)
    settling_cycles: int = Field(default=4, ge=1, le=100)
    measurement_cycles: int = Field(default=8, ge=2, le=100)
    points_per_cycle: int = Field(default=128, ge=32, le=1000)
    max_harmonic: int = Field(default=5, ge=3, le=10)
    compression_db: float = Field(default=1.0, gt=0, le=6.0)

    @model_validator(mode="after")
    def validate_linearity_sweep(self) -> "LinearitySweep":
        if not math.isfinite(self.frequency_hz) or not math.isfinite(
            self.compression_db
        ):
            raise ValueError("linearity frequency and compression must be finite")
        if any(
            not math.isfinite(float(value)) or value <= 0
            for value in self.amplitudes_v
        ):
            raise ValueError(
                "linearity amplitudes_v must contain finite positive values"
            )
        if any(
            self.amplitudes_v[index] <= self.amplitudes_v[index - 1]
            for index in range(1, len(self.amplitudes_v))
        ):
            raise ValueError("linearity amplitudes_v must be strictly increasing")
        if self.points_per_cycle < 8 * self.max_harmonic:
            raise ValueError(
                "linearity points_per_cycle must be at least 8 * max_harmonic"
            )
        total_points = (
            len(self.amplitudes_v)
            * (self.settling_cycles + self.measurement_cycles)
            * self.points_per_cycle
        )
        if total_points > 2_000_000:
            raise ValueError("linearity sweep exceeds the 2,000,000 point budget")
        return self


class NoiseSweep(StrictModel):
    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    points_per_decade: int = Field(default=20, ge=1, le=1000)

    @model_validator(mode="after")
    def stop_must_exceed_start(self) -> "NoiseSweep":
        if not math.isfinite(self.start_hz) or not math.isfinite(self.stop_hz):
            raise ValueError("noise sweep frequencies must be finite")
        if self.stop_hz <= self.start_hz:
            raise ValueError("noise sweep stop_hz must be greater than start_hz")
        return self


class AdeCaptureSpec(StrictModel):
    """Read a human-operated ADE setup without changing or rerunning it."""

    backend: AdeBackend = AdeBackend.MAESTRO
    history: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    require_results: bool = True
    require_saved_setup: bool = True
    require_structured_outputs: bool = False


class AdePrepareSpec(StrictModel):
    """Create a new persistent Maestro view without touching an existing one."""

    backend: AdeBackend = AdeBackend.MAESTRO
    test_name: str = Field(
        default="VDA",
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$.-]*$",
    )
    design_view: str = Field(
        default="schematic",
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    simulator: str = Field(default="spectre", pattern=r"^spectre$")


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
_SIMULATION_OPERATIONS = _TUNING_OPERATIONS | {Operation.SIMULATION_RUN}


class TaskSpec(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation: Operation
    circuit: CircuitKind
    target: DesignTarget
    pdk_profile: str = Field(default="nics4304_tsmc28", min_length=1)
    analysis: AnalysisKind | None = None
    ac_sweep: AcSweep | None = None
    linearity_sweep: LinearitySweep | None = None
    noise_sweep: NoiseSweep | None = None
    ade_capture: AdeCaptureSpec | None = None
    ade_prepare: AdePrepareSpec | None = None
    parameters: dict[str, float] = Field(default_factory=dict)
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list
    )
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
        analysis_settings = (
            self.analysis is not None
            or self.ac_sweep is not None
            or self.linearity_sweep is not None
            or self.noise_sweep is not None
        )
        if self.operation not in _SIMULATION_OPERATIONS:
            if analysis_settings:
                raise ValueError(
                    "analysis settings are supported only by simulation and tuning operations"
                )
        else:
            resolved_analysis = self.resolved_analysis()
            if self.circuit is CircuitKind.INVERTER:
                if resolved_analysis is not AnalysisKind.TRANSIENT:
                    raise ValueError("inverter currently supports only transient analysis")
                if any(
                    setting is not None
                    for setting in (
                        self.ac_sweep,
                        self.linearity_sweep,
                        self.noise_sweep,
                    )
                ):
                    raise ValueError(
                        "inverter does not accept common-source analysis settings"
                    )
            elif self.circuit is CircuitKind.COMMON_SOURCE:
                if resolved_analysis not in {
                    AnalysisKind.DC,
                    AnalysisKind.AC,
                    AnalysisKind.TRANSIENT,
                    AnalysisKind.NOISE,
                    AnalysisKind.QUALITY,
                }:
                    raise ValueError(
                        "common_source supports dc, ac, transient, noise, or quality "
                        "analysis"
                    )
            elif analysis_settings:
                raise ValueError(
                    "analysis settings are not implemented for this circuit"
                )
            if self.circuit is CircuitKind.COMMON_SOURCE:
                if resolved_analysis is AnalysisKind.QUALITY:
                    missing = [
                        name
                        for name, value in (
                            ("ac_sweep", self.ac_sweep),
                            ("linearity_sweep", self.linearity_sweep),
                            ("noise_sweep", self.noise_sweep),
                        )
                        if value is None
                    ]
                    if missing:
                        raise ValueError(
                            "common-source quality analysis requires "
                            + ", ".join(missing)
                        )
                else:
                    required_settings = {
                        AnalysisKind.AC: ("ac_sweep", self.ac_sweep),
                        AnalysisKind.TRANSIENT: (
                            "linearity_sweep",
                            self.linearity_sweep,
                        ),
                        AnalysisKind.NOISE: ("noise_sweep", self.noise_sweep),
                    }
                    required = required_settings.get(resolved_analysis)
                    settings = {
                        "ac_sweep": self.ac_sweep,
                        "linearity_sweep": self.linearity_sweep,
                        "noise_sweep": self.noise_sweep,
                    }
                    allowed_setting = required[0] if required is not None else None
                    unexpected = [
                        name
                        for name, value in settings.items()
                        if value is not None and name != allowed_setting
                    ]
                    if unexpected:
                        raise ValueError(
                            f"{', '.join(unexpected)} requires its matching analysis"
                        )
                    if required is not None and required[1] is None:
                        raise ValueError(
                            f"common-source {resolved_analysis.value} analysis requires "
                            f"{required[0]}"
                        )
        if self.operation is Operation.ADE_CAPTURE:
            if self.ade_capture is None:
                raise ValueError("ade.capture requires ade_capture settings")
            if self.target.view != "maestro":
                raise ValueError("ade.capture currently requires target.view='maestro'")
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.capture only reads the focused ADE state and does not accept "
                    "parameters, search, constraints, objective, or creation requests"
                )
            if self.safety.replace_existing:
                raise ValueError("ade.capture cannot replace an existing view")
        elif self.ade_capture is not None:
            raise ValueError("ade_capture settings require operation='ade.capture'")
        if self.operation is Operation.ADE_PREPARE:
            if self.ade_prepare is None:
                raise ValueError("ade.prepare requires ade_prepare settings")
            if self.target.view != "maestro":
                raise ValueError("ade.prepare currently requires target.view='maestro'")
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.prepare only creates a new persistent ADE setup and does not "
                    "accept parameters, search, constraints, objective, or schematic "
                    "creation requests"
                )
            if self.safety.replace_existing:
                raise ValueError("ade.prepare never replaces an existing Maestro view")
        elif self.ade_prepare is not None:
            raise ValueError("ade_prepare settings require operation='ade.prepare'")
        if self.instance_parameter_updates:
            if self.operation is not Operation.PARAMETERS_APPLY:
                raise ValueError(
                    "instance_parameter_updates are currently supported only by "
                    "parameters.apply"
                )
            instances = [update.instance for update in self.instance_parameter_updates]
            if len(instances) != len(set(instances)):
                raise ValueError(
                    "instance_parameter_updates cannot repeat an instance"
                )
        if (
            self.operation is Operation.PARAMETERS_APPLY
            and not self.parameters
            and not self.instance_parameter_updates
        ):
            raise ValueError(
                "parameters.apply requires parameters or instance_parameter_updates"
            )
        if self.operation is Operation.SCHEMATIC_TRANSFORM:
            if not self.parameters:
                raise ValueError("schematic.transform requires parameters")
            if self.instance_parameter_updates:
                raise ValueError(
                    "schematic.transform does not accept instance_parameter_updates"
                )
            if self.parameter_space:
                raise ValueError("schematic.transform does not accept parameter_space")
        if self.operation in _TUNING_OPERATIONS:
            if not self.parameter_space:
                raise ValueError(f"{self.operation.value} requires parameter_space")
            if not self.constraints:
                raise ValueError(f"{self.operation.value} requires constraints")
        if self.operation is Operation.SIMULATION_RUN and not self.parameters:
            raise ValueError("simulation.run requires parameters")
        return self

    def resolved_analysis(self) -> AnalysisKind:
        if self.analysis is not None:
            return self.analysis
        if self.circuit is CircuitKind.INVERTER:
            return AnalysisKind.TRANSIENT
        return AnalysisKind.DC

    def resolved_analyses(self) -> tuple[AnalysisKind, ...]:
        analysis = self.resolved_analysis()
        if analysis is AnalysisKind.QUALITY:
            return (
                AnalysisKind.AC,
                AnalysisKind.TRANSIENT,
                AnalysisKind.NOISE,
            )
        return (analysis,)


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
    metric_sources: dict[str, EvidenceSource] = Field(default_factory=dict)
    analysis_complete: bool = True
    analysis_issues: list[str] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)


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


class ExecutionCheckpoint(StrictModel):
    schema_version: int = 1
    task_id: str
    plan_token: str
    adapter: str
    started_at: datetime
    initial_parameters: dict[str, float]
    expected_oa_parameters: dict[str, float]
    pending_oa_parameters: dict[str, float] | None = None
    next_candidate_index: int = Field(ge=1)
    actions: list[ActionRecord] = Field(default_factory=list)
    candidates: list[CandidateEvaluation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    complete: bool = False


class PdkProfile(StrictModel):
    name: str
    tech_library: str
    nmos_cell: str
    pmos_cell: str
    model_include: str
    model_section: str
    cds_lib_path: str
    cadence_cshrc: str
    remote_run_root: str
    default_vdd_v: float = Field(gt=0)
    default_load_ff: float = Field(gt=0)
    default_length_um: float = Field(gt=0)
    default_common_source_width_um: float = Field(gt=0)
    default_common_source_bias_v: float = Field(gt=0)
    default_common_source_load_resistance_ohm: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_remote_write_paths(self) -> "PdkProfile":
        for name in ("cds_lib_path", "remote_run_root"):
            value = getattr(self, name)
            if not value.startswith("/data/xum/"):
                raise ValueError(f"{name} must stay under /data/xum")
        return self
