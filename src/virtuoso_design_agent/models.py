"""Stable domain contracts shared by the planner, executor, and adapters."""

from __future__ import annotations

import math
import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from .design_context import DesignContext, validate_topology_delta_scope
from .generic_simulation import GenericOaSimulationSpec
from .netlist_preview import NetlistPreviewSpec
from .topology_delta import TopologyDeltaExecutionSpec

# The default is the currently verified foundry-CMOS environment. Packaging,
# TSV, and hybrid-bonding profiles must always be selected explicitly.
DEFAULT_PDK_PROFILE = "nics4304_tsmc28"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(str, Enum):
    DEVICE_CHARACTERIZE = "device.characterize"
    SCHEMATIC_CREATE = "schematic.create"
    SCHEMATIC_INSPECT = "schematic.inspect"
    SCHEMATIC_SYMBOL_GENERATE = "schematic.symbol.generate"
    SCHEMATIC_TRANSFORM = "schematic.transform"
    PARAMETERS_APPLY = "parameters.apply"
    ADE_PREPARE = "ade.prepare"
    ADE_CAPTURE = "ade.capture"
    ADE_RUN = "ade.run"
    ADE_VARIABLES_APPLY = "ade.variables.apply"
    ADE_CORNERS_APPLY = "ade.corners.apply"
    ADE_SETUP_APPLY = "ade.setup.apply"
    SIMULATION_RUN = "simulation.run"
    DESIGN_TUNE = "design.tune"
    DESIGN_CLOSE_LOOP = "design.close_loop"


class CircuitKind(str, Enum):
    MOS_DEVICE = "mos_device"
    NETLIST_PREVIEW = "netlist_preview"
    EXISTING_SCHEMATIC = "existing_schematic"
    INVERTER = "inverter"
    COMMON_SOURCE = "common_source"
    SOURCE_DEGENERATED_COMMON_SOURCE = "source_degenerated_common_source"
    DIFFERENTIAL_PAIR = "differential_pair"


class SchematicTransformAction(str, Enum):
    ADD_SOURCE_DEGENERATION = "add_source_degeneration"
    REMOVE_SOURCE_DEGENERATION = "remove_source_degeneration"
    ADD_TAIL_DEVICE = "add_tail_device"
    REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR = (
        "replace_resistive_load_with_current_mirror"
    )
    RESTORE_RESISTIVE_LOAD = "restore_resistive_load"


class SchematicTransformSpec(StrictModel):
    action: SchematicTransformAction
    expected_restored_placement_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class AnalysisKind(str, Enum):
    TRANSIENT = "transient"
    DC = "dc"
    AC = "ac"
    NOISE = "noise"
    PSRR = "psrr"
    QUALITY = "quality"


class AnalysisStageExecution(str, Enum):
    ISOLATED = "isolated"
    SHARED_NETLIST = "shared_netlist"


class AdeBackend(str, Enum):
    MAESTRO = "maestro"


class AdeVariableScope(str, Enum):
    GLOBAL = "global"
    TEST = "test"
    CORNER = "corner"


class AdeOutputType(str, Enum):
    NET = "net"
    POINT = "point"


class AdeSpecRelation(str, Enum):
    LESS_THAN = "lt"
    GREATER_THAN = "gt"


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


class SelectionScope(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    BEST_EVALUATED = "best_evaluated"
    BEST_IN_DECLARED_DISCRETE_DOMAIN = "best_in_declared_discrete_domain"
    NO_FEASIBLE_IN_DECLARED_DISCRETE_DOMAIN = (
        "no_feasible_in_declared_discrete_domain"
    )
    NO_RECOMMENDATION_FROM_EVALUATED_POINTS = (
        "no_recommendation_from_evaluated_points"
    )


class DesignTarget(StrictModel):
    library: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    cell: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    view: str = Field(default="schematic", pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")


class SchematicSymbolPinSpec(StrictModel):
    """One source-schematic terminal required in the generated symbol."""

    name: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$")
    direction: Literal["input", "output", "inputOutput"]
    num_bits: int = Field(default=1, ge=1, le=4096)


class SchematicSymbolGenerationSpec(StrictModel):
    """Non-overwrite sibling-symbol generation bound to an exact source graph."""

    schema_version: Literal[1] = 1
    expected_schematic_topology_sha256: StrictStr = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    expected_pins: list[SchematicSymbolPinSpec] = Field(
        min_length=1,
        max_length=128,
    )
    pin_sort: Literal["alphanumeric", "geometric"] = "geometric"
    output_view: Literal["symbol"] = "symbol"

    @model_validator(mode="after")
    def validate_expected_pins(self) -> "SchematicSymbolGenerationSpec":
        names = [pin.name for pin in self.expected_pins]
        if len(names) != len(set(names)):
            raise ValueError("symbol-generation expected pins must be unique")
        return self


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


class InstanceParameterSweep(StrictModel):
    """One finite raw CDF/OA string dimension for bounded tuning."""

    instance: StrictStr = Field(min_length=1)
    parameter: StrictStr = Field(min_length=1)
    values: list[StrictStr] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: list[StrictStr]) -> list[StrictStr]:
        if any(not item for item in value):
            raise ValueError("instance parameter sweep values cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("instance parameter sweep values contain duplicates")
        return value


class CandidateSetSource(StrictModel):
    """Provenance shared by one ordered set of complete candidate tuples."""

    generator: StrictStr = Field(
        default="user_declared",
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    id: StrictStr = Field(
        default="user-declared",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    bindings: dict[StrictStr, str] = Field(default_factory=dict, max_length=16)
    evidence_source: Literal[
        EvidenceSource.USER_INPUT,
        EvidenceSource.SOFTWARE_INFERENCE,
    ] = EvidenceSource.USER_INPUT

    @field_validator("bindings")
    @classmethod
    def validate_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            if not name or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(
                    "candidate-set bindings require nonempty names and SHA-256 values"
                )
        return value

    @model_validator(mode="after")
    def inferred_sources_are_hash_bound(self) -> "CandidateSetSource":
        if (
            self.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
            and not self.bindings
        ):
            raise ValueError(
                "software-inference candidate sets require at least one hash binding"
            )
        return self


class AtomicCandidate(StrictModel):
    """One complete semantic/raw parameter tuple; it is never product-expanded."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    parameters: dict[StrictStr, float] = Field(default_factory=dict)
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list
    )
    predicted_metrics: dict[StrictStr, float] = Field(default_factory=dict)

    @field_validator("parameters", "predicted_metrics")
    @classmethod
    def validate_finite_numbers(
        cls, value: dict[str, float], info: Any
    ) -> dict[str, float]:
        for name, number in value.items():
            if not name or not math.isfinite(number):
                raise ValueError(f"atomic candidate {info.field_name} must be finite")
            if info.field_name == "parameters" and number <= 0.0:
                raise ValueError("atomic candidate parameters must be positive")
        return value

    @model_validator(mode="after")
    def validate_complete_tuple(self) -> "AtomicCandidate":
        if not self.parameters and not self.instance_parameter_updates:
            raise ValueError(
                "atomic candidate requires semantic or instance parameters"
            )
        instances = [update.instance for update in self.instance_parameter_updates]
        if len(instances) != len(set(instances)):
            raise ValueError(
                "atomic candidate instance_parameter_updates cannot repeat an instance"
            )
        return self

    def instance_parameters(self) -> dict[str, dict[str, str]]:
        return {
            update.instance: dict(update.parameters)
            for update in self.instance_parameter_updates
        }

    def field_identity(self) -> tuple[frozenset[str], frozenset[tuple[str, str]]]:
        return (
            frozenset(self.parameters),
            frozenset(
                (update.instance, parameter)
                for update in self.instance_parameter_updates
                for parameter in update.parameters
            ),
        )

    def value_identity(self) -> tuple[Any, ...]:
        raw_values = tuple(
            sorted(
                (update.instance, parameter, value)
                for update in self.instance_parameter_updates
                for parameter, value in update.parameters.items()
            )
        )
        return (tuple(sorted(self.parameters.items())), raw_values)


class AtomicCandidateSet(StrictModel):
    """An ordered, provenance-bound finite domain of complete parameter tuples."""

    source: CandidateSetSource = Field(default_factory=CandidateSetSource)
    candidates: list[AtomicCandidate] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_atomic_domain(self) -> "AtomicCandidateSet":
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("atomic candidate ids must be unique")
        fields = [candidate.field_identity() for candidate in self.candidates]
        if any(identity != fields[0] for identity in fields[1:]):
            raise ValueError(
                "atomic candidates must declare the same semantic and raw fields"
            )
        values = [candidate.value_identity() for candidate in self.candidates]
        if len(values) != len(set(values)):
            raise ValueError("atomic candidate parameter tuples must be unique")
        return self


class TheorySeedCandidate(StrictModel):
    """One atomic semantic-parameter tuple proposed by the local theory path."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    source_candidate_id: StrictStr = Field(min_length=1, max_length=256)
    parameters: dict[StrictStr, float]
    predicted_metrics: dict[StrictStr, float] = Field(default_factory=dict)
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("theory-seed candidate parameters cannot be empty")
        for name, number in value.items():
            if not name or not math.isfinite(number) or number <= 0.0:
                raise ValueError(
                    f"theory-seed candidate parameter {name!r} must be finite and positive"
                )
        return value

    @field_validator("predicted_metrics")
    @classmethod
    def validate_predicted_metrics(
        cls, value: dict[str, float]
    ) -> dict[str, float]:
        for name, number in value.items():
            if not name or not math.isfinite(number):
                raise ValueError(
                    f"theory-seed predicted metric {name!r} must be finite"
                )
        return value


class TheorySeedSource(StrictModel):
    """Hash-bound provenance for an atomic theory-seeded candidate set."""

    generator: Literal["vda.theory"] = "vda.theory"
    policy_id: StrictStr = Field(min_length=1, max_length=96)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    theory_request_id: StrictStr = Field(min_length=1, max_length=96)
    theory_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    theory_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_data_source: StrictStr = Field(min_length=1, max_length=64)
    device_data_artifact_id: StrictStr | None = Field(default=None, min_length=1)
    device_data_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    optimality_classification: StrictStr = Field(min_length=1, max_length=128)
    declared_theory_combinations: int = Field(ge=1)
    evaluated_theory_combinations: int = Field(ge=1)
    theory_domain_exhausted: Literal[True] = True
    selection_strategy: Literal["ranked_then_log_maximin"]
    width_quantization: Literal["decimal_places", "nearest_grid_half_up"] = (
        "decimal_places"
    )
    width_grid_um: float | None = Field(default=None, gt=0.0)
    continuous_optimum_claim: Literal[False] = False
    global_optimum_claim: Literal[False] = False
    evidence_source: Literal[EvidenceSource.SOFTWARE_INFERENCE] = (
        EvidenceSource.SOFTWARE_INFERENCE
    )

    @model_validator(mode="after")
    def validate_width_quantization(self) -> "TheorySeedSource":
        if self.evaluated_theory_combinations != self.declared_theory_combinations:
            raise ValueError(
                "exhausted theory-seed provenance requires evaluated and declared "
                "combination counts to match"
            )
        if self.width_quantization == "nearest_grid_half_up":
            if self.width_grid_um is None:
                raise ValueError(
                    "nearest-grid theory-seed provenance requires width_grid_um"
                )
        elif self.width_grid_um is not None:
            raise ValueError(
                "decimal-place theory-seed provenance cannot declare width_grid_um"
            )
        return self


class TheorySeedCandidateSet(StrictModel):
    """A provenance-bound list whose tuples must not be expanded as a product."""

    source: TheorySeedSource
    candidates: list[TheorySeedCandidate] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_atomic_domain(self) -> "TheorySeedCandidateSet":
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("theory-seed candidate ids must be unique")
        source_ids = [candidate.source_candidate_id for candidate in self.candidates]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("theory-seed source candidate ids must be unique")
        parameter_names = [set(candidate.parameters) for candidate in self.candidates]
        if any(names != parameter_names[0] for names in parameter_names[1:]):
            raise ValueError(
                "theory-seed candidates must declare the same parameter names"
            )
        canonical = [
            tuple(sorted(candidate.parameters.items())) for candidate in self.candidates
        ]
        if len(canonical) != len(set(canonical)):
            raise ValueError("theory-seed candidate parameter tuples must be unique")
        return self


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


class AnalysisStageSpec(StrictModel):
    """One ordered analysis gate inside a candidate evaluation."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    analysis: AnalysisKind
    constraint_metrics: list[StrictStr] = Field(
        default_factory=list,
        max_length=64,
    )
    stop_on_failure: bool = True

    @field_validator("constraint_metrics")
    @classmethod
    def validate_constraint_metrics(
        cls, value: list[StrictStr]
    ) -> list[StrictStr]:
        if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) is None for item in value):
            raise ValueError("analysis-stage constraint metrics must be identifiers")
        if len(value) != len(set(value)):
            raise ValueError("analysis-stage constraint metrics contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_supported_analysis(self) -> "AnalysisStageSpec":
        if self.analysis not in {
            AnalysisKind.DC,
            AnalysisKind.AC,
            AnalysisKind.TRANSIENT,
            AnalysisKind.NOISE,
        }:
            raise ValueError(
                "analysis stages support only dc, ac, transient, or noise"
            )
        return self


class OperatingCondition(StrictModel):
    """One explicit process/voltage/temperature verification condition."""

    name: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    process_corner: StrictStr = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    temperature_c: float = Field(ge=-273.15, le=300.0)
    vdd_v: float | None = Field(default=None, gt=0.0, le=10.0)


class ExecutionLimits(StrictModel):
    max_iterations: int = Field(default=12, ge=1, le=64)
    timeout_seconds: int = Field(default=600, ge=10, le=7200)


class ExistingSchematicTopologyAlternativeSpec(StrictModel):
    """One baseline-anchored, exact, reversible local topology alternative."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    topology_delta: TopologyDeltaExecutionSpec
    design_context: DesignContext
    generic_simulation: GenericOaSimulationSpec
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_alternative(self) -> "ExistingSchematicTopologyAlternativeSpec":
        if self.topology_delta.direction != "forward":
            raise ValueError("topology alternative requires a forward topology_delta")
        contract = self.topology_delta.contract
        if not contract.operations or not contract.inverse_operations:
            raise ValueError(
                "topology alternative requires nonempty forward and inverse operations"
            )
        if contract.expected_before_sha256 == contract.expected_after_sha256:
            raise ValueError("topology alternative requires a real topology change")
        instances = [update.instance for update in self.instance_parameter_updates]
        if len(instances) != len(set(instances)):
            raise ValueError(
                "topology alternative instance_parameter_updates cannot repeat an instance"
            )
        return self


class ExistingSchematicTopologyRefinementSpec(StrictModel):
    """One baseline plus bounded independent reversible topology alternatives."""

    baseline_id: StrictStr = Field(
        default="baseline",
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    alternatives: list[ExistingSchematicTopologyAlternativeSpec] = Field(
        default_factory=list,
        max_length=7,
    )
    alternative_id: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    topology_delta: TopologyDeltaExecutionSpec | None = None
    alternative_design_context: DesignContext | None = None
    alternative_generic_simulation: GenericOaSimulationSpec | None = None
    alternative_instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_refinement(self) -> "ExistingSchematicTopologyRefinementSpec":
        legacy_values = (
            self.alternative_id,
            self.topology_delta,
            self.alternative_design_context,
            self.alternative_generic_simulation,
        )
        if self.alternatives and any(value is not None for value in legacy_values):
            raise ValueError(
                "topology refinement must use either alternatives or the legacy "
                "single-alternative fields, not both"
            )
        if self.alternatives and self.alternative_instance_parameter_updates:
            raise ValueError(
                "topology refinement alternatives own their instance parameter updates"
            )
        if not self.alternatives and not all(
            value is not None for value in legacy_values
        ):
            raise ValueError(
                "topology refinement requires at least one complete alternative"
            )
        alternatives = self.resolved_alternatives()
        ids = [alternative.id for alternative in alternatives]
        if self.baseline_id in ids or len(ids) != len(set(ids)):
            raise ValueError("topology refinement variant ids must be unique")
        baseline_hashes = {
            alternative.topology_delta.contract.expected_before_sha256
            for alternative in alternatives
        }
        if len(baseline_hashes) != 1:
            raise ValueError(
                "all topology alternatives must be anchored to one baseline fingerprint"
            )
        output_hashes = [
            alternative.topology_delta.contract.expected_after_sha256
            for alternative in alternatives
        ]
        if len(output_hashes) != len(set(output_hashes)):
            raise ValueError(
                "topology alternatives must have distinct output fingerprints"
            )
        return self

    def resolved_alternatives(
        self,
    ) -> tuple[ExistingSchematicTopologyAlternativeSpec, ...]:
        if self.alternatives:
            return tuple(self.alternatives)
        assert self.alternative_id is not None
        assert self.topology_delta is not None
        assert self.alternative_design_context is not None
        assert self.alternative_generic_simulation is not None
        return (
            ExistingSchematicTopologyAlternativeSpec(
                id=self.alternative_id,
                topology_delta=self.topology_delta,
                design_context=self.alternative_design_context,
                generic_simulation=self.alternative_generic_simulation,
                instance_parameter_updates=(
                    self.alternative_instance_parameter_updates
                ),
            ),
        )


class AcSweep(StrictModel):
    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    evaluation_stop_hz: float | None = Field(default=None, gt=0)
    points_per_decade: int = Field(default=20, ge=1, le=1000)
    reference_points: int = Field(default=5, ge=2, le=20)
    max_reference_variation_db: float = Field(default=0.5, gt=0, le=3.0)

    @model_validator(mode="after")
    def stop_must_exceed_start(self) -> "AcSweep":
        if self.stop_hz <= self.start_hz:
            raise ValueError("AC sweep stop_hz must be greater than start_hz")
        if self.evaluation_stop_hz is not None and not (
            self.start_hz < self.evaluation_stop_hz <= self.stop_hz
        ):
            raise ValueError(
                "AC sweep evaluation_stop_hz must be greater than start_hz "
                "and no greater than stop_hz"
            )
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


class ExistingSchematicWinnerVerificationSpec(StrictModel):
    """Optional expensive checks run only after nominal candidate selection."""

    analysis_stages: list[AnalysisStageSpec] = Field(min_length=1, max_length=4)
    constraints: list[MetricConstraint] = Field(min_length=1, max_length=128)
    operating_conditions: list[OperatingCondition] = Field(
        default_factory=list,
        max_length=5,
    )
    ac_sweep: AcSweep | None = None
    linearity_sweep: LinearitySweep | None = None
    noise_sweep: NoiseSweep | None = None

    @model_validator(mode="after")
    def validate_verification(self) -> "ExistingSchematicWinnerVerificationSpec":
        stage_ids = [stage.id for stage in self.analysis_stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("winner-verification analysis stage ids must be unique")
        stage_analyses = [stage.analysis for stage in self.analysis_stages]
        if len(stage_analyses) != len(set(stage_analyses)):
            raise ValueError(
                "winner-verification analysis stages cannot repeat an analysis"
            )
        declared_metrics = {constraint.metric for constraint in self.constraints}
        assigned_metrics = [
            metric
            for stage in self.analysis_stages
            for metric in stage.constraint_metrics
        ]
        duplicate_assignments = sorted(
            {
                metric
                for metric in assigned_metrics
                if assigned_metrics.count(metric) > 1
            }
        )
        if duplicate_assignments:
            raise ValueError(
                "winner-verification constraints are assigned more than once: "
                + ", ".join(duplicate_assignments)
            )
        if set(assigned_metrics) != declared_metrics:
            missing = sorted(declared_metrics - set(assigned_metrics))
            unknown = sorted(set(assigned_metrics) - declared_metrics)
            details = []
            if missing:
                details.append("unassigned=" + ", ".join(missing))
            if unknown:
                details.append("undeclared=" + ", ".join(unknown))
            raise ValueError(
                "winner-verification stage constraint mapping is incomplete: "
                + "; ".join(details)
            )
        for stage in self.analysis_stages[:-1]:
            if not stage.constraint_metrics:
                raise ValueError(
                    "every non-final winner-verification stage requires a constraint gate"
                )
        requested = set(stage_analyses)
        required_settings = {
            AnalysisKind.AC: ("ac_sweep", self.ac_sweep),
            AnalysisKind.TRANSIENT: ("linearity_sweep", self.linearity_sweep),
            AnalysisKind.NOISE: ("noise_sweep", self.noise_sweep),
        }
        for analysis, (name, value) in required_settings.items():
            if (analysis in requested) != (value is not None):
                if analysis in requested:
                    raise ValueError(
                        f"winner-verification {analysis.value} analysis requires {name}"
                    )
                raise ValueError(
                    f"winner-verification {name} requires a matching "
                    f"{analysis.value} analysis stage"
                )
        names = [condition.name for condition in self.operating_conditions]
        if len(names) != len(set(names)):
            raise ValueError(
                "winner-verification operating conditions require unique names"
            )
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
    design: DesignTarget | None = None
    simulator: str = Field(default="spectre", pattern=r"^spectre$")


class AdeSweepVariableExpectation(StrictModel):
    """One exact saved Maestro variable scope used by a strict sweep."""

    name: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    expected_value: StrictStr = Field(min_length=1, max_length=1024)
    scope: AdeVariableScope = AdeVariableScope.GLOBAL
    scope_name: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    sweep: bool = True

    @field_validator("expected_value")
    @classmethod
    def validate_expected_value(cls, value: str) -> str:
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "Maestro sweep values cannot contain quotes, backslashes, or "
                "control characters"
            )
        return value

    @field_validator("scope_name")
    @classmethod
    def validate_scope_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "Maestro sweep scope names cannot contain quotes, backslashes, "
                "or control characters"
            )
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "AdeSweepVariableExpectation":
        if self.scope is AdeVariableScope.GLOBAL and self.scope_name is not None:
            raise ValueError("global Maestro sweep variables cannot declare scope_name")
        if self.scope is not AdeVariableScope.GLOBAL and self.scope_name is None:
            raise ValueError("test/corner Maestro sweep variables require scope_name")
        values = [item.strip() for item in self.expected_value.split(",")]
        if any(not item for item in values):
            raise ValueError("Maestro sweep expected_value contains an empty value")
        if self.sweep:
            if len(values) < 2:
                raise ValueError(
                    "Maestro sweep expected_value must declare at least two "
                    "comma-separated values"
                )
            if self.scope is AdeVariableScope.CORNER:
                raise ValueError("corner-scoped variables cannot be point sweeps")
        elif len(values) != 1:
            raise ValueError(
                "fixed Maestro sweep variables must declare exactly one value"
            )
        if len(values) != len(set(values)):
            raise ValueError("Maestro sweep expected_value contains duplicates")
        return self

    def evidence_key(self) -> str:
        if self.scope is AdeVariableScope.GLOBAL:
            return self.name
        return f"{self.scope.value}:{self.scope_name}:{self.name}"

    def declared_values(self) -> list[str]:
        return [item.strip() for item in self.expected_value.split(",")]


class AdeSweepPointExpectation(StrictModel):
    """One VDA result case, optionally selecting a Maestro point/corner cell."""

    point: int = Field(ge=1, le=256)
    maestro_point: int | None = Field(default=None, ge=1, le=256)
    corner: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    values: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=32)

    @field_validator("corner")
    @classmethod
    def validate_corner(cls, value: str | None) -> str | None:
        if value is not None and any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError("invalid Maestro sweep point corner")
        return value

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, str]) -> dict[str, str]:
        for name, point_value in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
                raise ValueError(f"invalid Maestro sweep point variable: {name!r}")
            if not point_value or any(
                character in ('"', "\\")
                or ord(character) < 32
                or ord(character) == 127
                for character in point_value
            ):
                raise ValueError(
                    f"invalid Maestro sweep point value for {name!r}"
                )
        return value


class AdeSweepInputBinding(StrictModel):
    """Bind an effective ADE variable to one known OA instance parameter."""

    test: StrictStr = Field(min_length=1, max_length=128)
    variable: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    instance: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    oa_parameter: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )

    def identity(self) -> tuple[str, str, str, str]:
        return self.test, self.variable, self.instance, self.oa_parameter


class AdeSweepOutputEvaluationErrorExpectation(StrictModel):
    """Pin one known non-metric calculator failure to exact sweep points."""

    test: StrictStr = Field(min_length=1, max_length=128)
    output: StrictStr = Field(min_length=1, max_length=128)
    point_values: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=32)

    @field_validator("test", "output")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "ADE expected output evaluation-error labels cannot contain "
                "quotes, backslashes, or control characters"
            )
        return value

    @field_validator("point_values")
    @classmethod
    def validate_point_values(cls, value: dict[str, str]) -> dict[str, str]:
        for name, point_value in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
                raise ValueError(
                    f"invalid ADE expected evaluation-error variable: {name!r}"
                )
            if not point_value or any(
                character in ('"', "\\")
                or ord(character) < 32
                or ord(character) == 127
                for character in point_value
            ):
                raise ValueError(
                    "ADE expected output evaluation-error point values cannot "
                    "contain quotes, backslashes, or control characters"
                )
        return value


class AdeSweepVerificationSpec(StrictModel):
    """Exact expected point set and OA bindings for a native Maestro sweep."""

    expected_tests: list[StrictStr] = Field(min_length=1, max_length=32)
    expected_corners: list[StrictStr] | None = Field(
        default=None, min_length=1, max_length=64
    )
    expected_global_variable_selections: dict[StrictStr, bool] = Field(
        default_factory=dict, max_length=32
    )
    variables: list[AdeSweepVariableExpectation] = Field(
        min_length=1, max_length=32
    )
    points: list[AdeSweepPointExpectation] = Field(min_length=2, max_length=256)
    input_bindings: list[AdeSweepInputBinding] = Field(
        min_length=1, max_length=128
    )
    expected_output_evaluation_errors: list[
        AdeSweepOutputEvaluationErrorExpectation
    ] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_sweep_contract(self) -> "AdeSweepVerificationSpec":
        for name in self.expected_global_variable_selections:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
                raise ValueError(
                    f"invalid Maestro global variable selection name: {name!r}"
                )
        for label, names in (
            ("test", self.expected_tests),
            ("corner", self.expected_corners or []),
        ):
            for name in names:
                if (
                    not name
                    or len(name) > 128
                    or any(
                        character in ('"', "\\")
                        or ord(character) < 32
                        or ord(character) == 127
                        for character in name
                    )
                ):
                    raise ValueError(f"invalid Maestro sweep {label} name: {name!r}")
            if len(names) != len(set(names)):
                raise ValueError(
                    f"Maestro sweep expected_{label}s cannot contain duplicates"
                )

        variable_identities = [variable.evidence_key() for variable in self.variables]
        if len(variable_identities) != len(set(variable_identities)):
            raise ValueError("Maestro sweep variable scopes must be unique")
        sweep_variables = [variable for variable in self.variables if variable.sweep]
        fixed_variables = [variable for variable in self.variables if not variable.sweep]
        sweep_names = [variable.name for variable in sweep_variables]
        if len(sweep_names) != len(set(sweep_names)):
            raise ValueError("Maestro point-sweep variables must have unique names")
        fixed_names = {variable.name for variable in fixed_variables}
        if set(sweep_names) & fixed_names:
            raise ValueError(
                "a Maestro variable cannot be both a point sweep and fixed scope"
            )
        variable_names = list(dict.fromkeys([*sweep_names, *sorted(fixed_names)]))
        if self.expected_global_variable_selections and set(
            self.expected_global_variable_selections
        ) != set(variable_names):
            raise ValueError(
                "declared global-variable selections must cover every effective "
                "Maestro variable exactly"
            )
        for variable in self.variables:
            if (
                variable.scope is AdeVariableScope.TEST
                and variable.scope_name not in self.expected_tests
            ):
                raise ValueError(
                    f"test-scoped sweep variable {variable.name!r} must target one "
                    "of expected_tests"
                )
            if variable.scope is AdeVariableScope.CORNER:
                if self.expected_corners is None:
                    raise ValueError(
                        "corner-scoped sweep variables require expected_corners"
                    )
                if variable.scope_name not in self.expected_corners:
                    raise ValueError(
                        f"corner-scoped sweep variable {variable.name!r} must target "
                        "one of expected_corners"
                    )
            if (
                variable.scope is AdeVariableScope.TEST
                and self.expected_global_variable_selections.get(variable.name)
                is not False
            ):
                raise ValueError(
                    f"test-scoped sweep variable {variable.name!r} requires an "
                    "explicit disabled global-variable selection"
                )

        point_numbers = [point.point for point in self.points]
        if point_numbers != list(range(1, len(self.points) + 1)):
            raise ValueError(
                "Maestro sweep points must be ordered and contiguous from point 1"
            )
        expected_names = set(variable_names)
        corner_mode = any(
            point.corner is not None or point.maestro_point is not None
            for point in self.points
        )
        if corner_mode and any(
            point.corner is None or point.maestro_point is None for point in self.points
        ):
            raise ValueError(
                "cornered Maestro sweep cases require corner and maestro_point"
            )
        if not corner_mode and any(
            point.corner is not None or point.maestro_point is not None
            for point in self.points
        ):
            raise ValueError("ordinary Maestro sweep points cannot mix corner selectors")
        if corner_mode:
            if self.expected_corners is None:
                raise ValueError("cornered sweep cases require expected_corners")
            if len(self.expected_tests) != 1:
                raise ValueError(
                    "cornered sweep verification currently requires exactly one "
                    "Maestro test"
                )
            if not fixed_variables:
                raise ValueError("cornered sweep cases require fixed scoped variables")
            maestro_points = sorted(
                {int(point.maestro_point or 0) for point in self.points}
            )
            if maestro_points != list(range(1, len(maestro_points) + 1)):
                raise ValueError(
                    "cornered Maestro point selectors must be contiguous from 1"
                )
            for maestro_point in maestro_points:
                group = [
                    point
                    for point in self.points
                    if point.maestro_point == maestro_point
                ]
                if [point.corner for point in group] != list(self.expected_corners):
                    raise ValueError(
                        "each Maestro point must declare every expected corner in order"
                    )
                for name in sweep_names:
                    if len({point.values.get(name) for point in group}) != 1:
                        raise ValueError(
                            f"Maestro point sweep value {name!r} changed across corners"
                        )
        combinations: list[tuple[str, ...]] = []
        for point in self.points:
            if set(point.values) != expected_names:
                raise ValueError(
                    f"Maestro sweep point {point.point} must declare exactly "
                    f"{sorted(expected_names)}"
                )
            combinations.append(
                (
                    str(point.corner or ""),
                    *(point.values[name] for name in variable_names),
                )
            )
        if len(combinations) != len(set(combinations)):
            raise ValueError("Maestro sweep points contain duplicate value combinations")
        for variable in sweep_variables:
            actual_values = {point.values[variable.name] for point in self.points}
            if actual_values != set(variable.declared_values()):
                raise ValueError(
                    f"Maestro sweep points do not cover the declared values for "
                    f"{variable.name!r}"
                )

        if corner_mode:
            assert self.expected_corners is not None
            for point in self.points:
                assert point.corner is not None
                for name in fixed_names:
                    scoped = [
                        variable
                        for variable in fixed_variables
                        if variable.name == name
                    ]
                    corner_values = [
                        variable.expected_value
                        for variable in scoped
                        if variable.scope is AdeVariableScope.CORNER
                        and variable.scope_name == point.corner
                    ]
                    test_values = [
                        variable.expected_value
                        for variable in scoped
                        if variable.scope is AdeVariableScope.TEST
                        and variable.scope_name in self.expected_tests
                    ]
                    global_values = [
                        variable.expected_value
                        for variable in scoped
                        if variable.scope is AdeVariableScope.GLOBAL
                    ]
                    candidates = corner_values or test_values or global_values
                    if len(candidates) != 1:
                        raise ValueError(
                            f"fixed variable {name!r} does not resolve uniquely for "
                            f"corner {point.corner!r}"
                        )
                    if point.values[name] != candidates[0]:
                        raise ValueError(
                            f"fixed variable {name!r} at corner {point.corner!r} "
                            "does not match its declared scope"
                        )

        identities = [binding.identity() for binding in self.input_bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("Maestro sweep input bindings cannot contain duplicates")
        for binding in self.input_bindings:
            if binding.test not in self.expected_tests:
                raise ValueError(
                    f"Maestro sweep binding test {binding.test!r} must be one of "
                    "expected_tests"
                )
            if binding.variable not in expected_names:
                raise ValueError(
                    f"Maestro sweep binding variable {binding.variable!r} was not "
                    "declared"
                )
        bound_pairs = {
            (binding.test, binding.variable) for binding in self.input_bindings
        }
        required_pairs = {
            (test, variable)
            for test in self.expected_tests
            for variable in variable_names
        }
        if bound_pairs != required_pairs:
            missing = sorted(required_pairs - bound_pairs)
            raise ValueError(
                "each Maestro sweep test/variable pair needs an OA input binding; "
                f"missing={missing}"
            )

        error_expectations = self.expected_output_evaluation_errors
        if error_expectations and len(self.expected_tests) != 1:
            raise ValueError(
                "expected output evaluation errors require exactly one Maestro test"
            )
        expected_error_cells: set[tuple[str, str, int]] = set()
        for expectation in error_expectations:
            if expectation.test not in self.expected_tests:
                raise ValueError(
                    "expected output evaluation errors must target expected_tests"
                )
            if not set(expectation.point_values).issubset(expected_names):
                raise ValueError(
                    "expected output evaluation-error point values must use declared "
                    "sweep variables"
                )
            matching_points = [
                point
                for point in self.points
                if all(
                    point.values[name] == value
                    for name, value in expectation.point_values.items()
                )
            ]
            if not matching_points:
                raise ValueError(
                    "expected output evaluation error did not match any declared "
                    "sweep point"
                )
            for point in matching_points:
                identity = (expectation.test, expectation.output, point.point)
                if identity in expected_error_cells:
                    raise ValueError(
                        "expected output evaluation-error rules overlap at one point"
                    )
                expected_error_cells.add(identity)
        return self

    def effective_variable_names(self) -> list[str]:
        return list(dict.fromkeys(variable.name for variable in self.variables))

    def maestro_point_count(self) -> int:
        selectors = {
            point.maestro_point for point in self.points if point.maestro_point is not None
        }
        return len(selectors) if selectors else len(self.points)

    def corner_mode(self) -> bool:
        return any(point.corner is not None for point in self.points)


class AdeResultParameterBinding(StrictModel):
    """Normalize one native Maestro point parameter for a VDA candidate."""

    source: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    parameter: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    scale: float = Field(default=1.0, gt=0.0)
    unit: StrictStr = Field(min_length=1, max_length=32)

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str) -> str:
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "ADE result parameter unit cannot contain quotes, backslashes, or "
                "control characters"
            )
        return value

    @field_validator("scale")
    @classmethod
    def validate_scale(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("ADE result parameter scale must be finite")
        return value


class AdeResultMetricBinding(StrictModel):
    """Bind one exact Maestro scalar output to one normalized VDA metric."""

    test: StrictStr = Field(min_length=1, max_length=128)
    output: StrictStr = Field(min_length=1, max_length=128)
    expected_expression: StrictStr = Field(min_length=1, max_length=4096)
    metric: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    scale: float = Field(default=1.0, gt=0.0)
    unit: StrictStr = Field(min_length=1, max_length=32)

    @field_validator("test", "output", "unit")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "ADE result binding text cannot contain quotes, backslashes, or "
                "control characters"
            )
        return value

    @field_validator("expected_expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("ADE result expression cannot contain control characters")
        return value

    @field_validator("scale")
    @classmethod
    def validate_scale(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("ADE result metric scale must be finite")
        return value


class AdeResultMappingSpec(StrictModel):
    """Strict native-sweep point/output mapping into VDA evaluation records."""

    parameters: list[AdeResultParameterBinding] = Field(
        min_length=1, max_length=32
    )
    metrics: list[AdeResultMetricBinding] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_bindings(self) -> "AdeResultMappingSpec":
        parameter_sources = [binding.source for binding in self.parameters]
        parameter_names = [binding.parameter for binding in self.parameters]
        metric_outputs = [
            (binding.test, binding.output) for binding in self.metrics
        ]
        metric_names = [binding.metric for binding in self.metrics]
        for label, identities in (
            ("parameter sources", parameter_sources),
            ("parameter names", parameter_names),
            ("metric outputs", metric_outputs),
            ("metric names", metric_names),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"ADE result mapping {label} must be unique")
        return self


class AdeRunSpec(StrictModel):
    """Run one saved Maestro setup in a background session."""

    backend: AdeBackend = AdeBackend.MAESTRO
    require_structured_outputs: bool = True
    require_artifact_manifest: bool = True
    require_simulator_input_consistency: bool = False
    sweep_verification: AdeSweepVerificationSpec | None = None
    result_mapping: AdeResultMappingSpec | None = None
    resume_history: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    resume_runtime_scratch_root: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
    )

    @model_validator(mode="after")
    def validate_resume_pair(self) -> "AdeRunSpec":
        if (
            self.require_simulator_input_consistency
            and not self.require_artifact_manifest
        ):
            raise ValueError(
                "ADE simulator input consistency requires the artifact manifest"
            )
        if self.sweep_verification is not None and not (
            self.require_structured_outputs
            and self.require_artifact_manifest
            and self.require_simulator_input_consistency
        ):
            raise ValueError(
                "ADE sweep verification requires structured outputs, artifact "
                "manifest, and simulator input consistency"
            )
        if self.result_mapping is not None:
            if self.sweep_verification is None:
                raise ValueError(
                    "ADE result mapping requires strict native sweep verification"
                )
            expected_tests = self.sweep_verification.expected_tests
            if len(expected_tests) != 1:
                raise ValueError(
                    "ADE result mapping currently requires exactly one Maestro test"
                )
            expected_test = expected_tests[0]
            if any(
                binding.test != expected_test for binding in self.result_mapping.metrics
            ):
                raise ValueError(
                    "ADE result metric bindings must target the sole expected test"
                )
            mapped_outputs = {
                (binding.test, binding.output)
                for binding in self.result_mapping.metrics
            }
            expected_error_outputs = {
                (expectation.test, expectation.output)
                for expectation in (
                    self.sweep_verification.expected_output_evaluation_errors
                )
            }
            overlap = sorted(mapped_outputs & expected_error_outputs)
            if overlap:
                raise ValueError(
                    "ADE mapped metric outputs cannot be declared as expected "
                    f"evaluation errors: {overlap}"
                )
            expected_variables = {
                variable.name for variable in self.sweep_verification.variables
            }
            mapped_variables = {
                binding.source for binding in self.result_mapping.parameters
            }
            if mapped_variables != expected_variables:
                raise ValueError(
                    "ADE result parameter bindings must cover every declared sweep "
                    "variable exactly"
                )
        if (self.resume_history is None) != (
            self.resume_runtime_scratch_root is None
        ):
            raise ValueError(
                "ADE run resume requires both resume_history and "
                "resume_runtime_scratch_root"
            )
        path = self.resume_runtime_scratch_root
        if path is not None:
            if not path.startswith("/data/xum/") or "\\" in path:
                raise ValueError("ADE run resume scratch root must stay under /data/xum")
            if any(
                part in {"", ".", ".."} for part in path.split("/")[1:]
            ) or any(ord(character) < 32 for character in path):
                raise ValueError("ADE run resume scratch root must be normalized")
        return self


class AdeVariableUpdate(StrictModel):
    """Compare-and-swap one Maestro design variable at one exact scope."""

    name: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    expected_value: StrictStr | None = Field(max_length=1024)
    value: StrictStr = Field(min_length=1, max_length=1024)
    scope: AdeVariableScope = AdeVariableScope.GLOBAL
    scope_name: StrictStr | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("expected_value", "value")
    @classmethod
    def validate_skill_string(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value:
            raise ValueError("Maestro variable values cannot be empty strings")
        if any(
            character in ('"', "\\") or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "Maestro variable values cannot contain quotes, backslashes, or "
                "control characters"
            )
        return value

    @field_validator("scope_name")
    @classmethod
    def validate_scope_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(
            character in ('"', "\\") or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "Maestro variable scope names cannot contain quotes, backslashes, "
                "or control characters"
            )
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "AdeVariableUpdate":
        if self.scope is AdeVariableScope.GLOBAL and self.scope_name is not None:
            raise ValueError("global Maestro variables cannot declare scope_name")
        if self.scope is not AdeVariableScope.GLOBAL and self.scope_name is None:
            raise ValueError("test/corner Maestro variables require scope_name")
        return self

    def evidence_key(self) -> str:
        if self.scope is AdeVariableScope.GLOBAL:
            return self.name
        return f"{self.scope.value}:{self.scope_name}:{self.name}"


class AdeGlobalVariableSelectionUpdate(StrictModel):
    """CAS one global-variable enable selector without changing its value."""

    name: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$]*$",
    )
    expected_enabled: bool
    enabled: bool

    @model_validator(mode="after")
    def validate_transition(self) -> "AdeGlobalVariableSelectionUpdate":
        if self.expected_enabled == self.enabled:
            raise ValueError("global variable selection updates must change state")
        return self


class AdeVariablesApplySpec(StrictModel):
    """Patch declared Maestro variable scopes with exact old-value preconditions."""

    backend: AdeBackend = AdeBackend.MAESTRO
    expected_tests: list[StrictStr] = Field(min_length=1, max_length=32)
    expected_corners: list[StrictStr] | None = Field(
        default=None, min_length=1, max_length=64
    )
    updates: list[AdeVariableUpdate] = Field(default_factory=list, max_length=64)
    global_selection_updates: list[AdeGlobalVariableSelectionUpdate] = Field(
        default_factory=list, max_length=64
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "AdeVariablesApplySpec":
        if not self.updates and not self.global_selection_updates:
            raise ValueError(
                "Maestro variable patch requires value or global selection updates"
            )
        for test in self.expected_tests:
            if (
                not test
                or len(test) > 128
                or any(
                    character in ('"', "\\")
                    or ord(character) < 32
                    or ord(character) == 127
                    for character in test
                )
            ):
                raise ValueError(f"invalid Maestro test name: {test!r}")
        if len(self.expected_tests) != len(set(self.expected_tests)):
            raise ValueError("expected_tests cannot contain duplicates")
        if self.expected_corners is not None:
            for corner in self.expected_corners:
                if (
                    not corner
                    or len(corner) > 128
                    or any(
                        character in ('"', "\\")
                        or ord(character) < 32
                        or ord(character) == 127
                        for character in corner
                    )
                ):
                    raise ValueError(f"invalid Maestro corner name: {corner!r}")
            if len(self.expected_corners) != len(set(self.expected_corners)):
                raise ValueError("expected_corners cannot contain duplicates")
        for update in self.updates:
            if (
                update.scope is AdeVariableScope.TEST
                and update.scope_name not in self.expected_tests
            ):
                raise ValueError(
                    f"test-scoped variable {update.name!r} must target one of "
                    "expected_tests"
                )
            if update.scope is AdeVariableScope.CORNER:
                if self.expected_corners is None:
                    raise ValueError(
                        "corner-scoped variables require expected_corners"
                    )
                if update.scope_name not in self.expected_corners:
                    raise ValueError(
                        f"corner-scoped variable {update.name!r} must target one of "
                        "expected_corners"
                    )
        identities = [update.evidence_key() for update in self.updates]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Maestro variable updates cannot repeat the same scoped variable"
            )
        selection_names = [update.name for update in self.global_selection_updates]
        if len(selection_names) != len(set(selection_names)):
            raise ValueError(
                "Maestro global variable selection updates cannot repeat names"
            )
        return self


class AdeCornerAddition(StrictModel):
    """Add one new enabled Maestro corner without replacing existing state."""

    name: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if any(
            character in ('"', "\\")
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                "Maestro corner names cannot contain quotes, backslashes, or "
                "control characters"
            )
        return value


class AdeCornersApplySpec(StrictModel):
    """Add named corners after exact tests/corner-membership preconditions."""

    backend: AdeBackend = AdeBackend.MAESTRO
    expected_tests: list[StrictStr] = Field(min_length=1, max_length=32)
    expected_corners: list[StrictStr] = Field(default_factory=list, max_length=64)
    additions: list[AdeCornerAddition] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_corner_patch(self) -> "AdeCornersApplySpec":
        for label, names in (
            ("test", self.expected_tests),
            ("corner", self.expected_corners),
        ):
            for name in names:
                if (
                    not name
                    or len(name) > 128
                    or any(
                        character in ('"', "\\")
                        or ord(character) < 32
                        or ord(character) == 127
                        for character in name
                    )
                ):
                    raise ValueError(f"invalid Maestro {label} name: {name!r}")
            if len(names) != len(set(names)):
                raise ValueError(f"expected_{label}s cannot contain duplicates")
        addition_names = [addition.name for addition in self.additions]
        if len(addition_names) != len(set(addition_names)):
            raise ValueError("Maestro corner additions cannot repeat a name")
        overlap = sorted(set(addition_names) & set(self.expected_corners))
        if overlap:
            raise ValueError(
                "Maestro corner additions must be absent from expected_corners: "
                f"{overlap}"
            )
        return self


AdeAnalysisOptionValue = StrictStr | bool | None


class AdeAnalysisState(StrictModel):
    enabled: bool
    options: dict[StrictStr, AdeAnalysisOptionValue] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def validate_options(
        cls, value: dict[str, AdeAnalysisOptionValue]
    ) -> dict[str, AdeAnalysisOptionValue]:
        for name, option in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
                raise ValueError(f"invalid Maestro analysis option name: {name!r}")
            if isinstance(option, str):
                if not option or len(option) > 1024:
                    raise ValueError(
                        f"Maestro analysis option {name!r} must be 1..1024 characters"
                    )
                if any(ord(character) < 32 or ord(character) == 127 for character in option):
                    raise ValueError(
                        f"Maestro analysis option {name!r} contains control characters"
                    )
        return value


class AdeAnalysisUpdate(StrictModel):
    test: StrictStr = Field(min_length=1, max_length=128)
    analysis: StrictStr = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    expected: AdeAnalysisState | None
    enabled: bool
    options: dict[StrictStr, AdeAnalysisOptionValue] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def validate_option_updates(
        cls, value: dict[str, AdeAnalysisOptionValue]
    ) -> dict[str, AdeAnalysisOptionValue]:
        return AdeAnalysisState(enabled=True, options=value).options

    @model_validator(mode="after")
    def reject_noop(self) -> "AdeAnalysisUpdate":
        if self.expected is None:
            if not self.enabled:
                raise ValueError("a new Maestro analysis must be enabled")
        else:
            desired_options = dict(self.expected.options)
            desired_options.update(self.options)
            if (
                self.expected.enabled == self.enabled
                and desired_options == self.expected.options
            ):
                raise ValueError("Maestro analysis update must change enable or options")
        return self

    def label(self) -> str:
        return f"{self.test}/{self.analysis}"


class AdeOutputSpec(StrictModel):
    relation: AdeSpecRelation
    value: StrictStr = Field(min_length=1, max_length=1024)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Maestro output spec contains control characters")
        return value


class AdeOutputAddition(StrictModel):
    test: StrictStr = Field(min_length=1, max_length=128)
    name: StrictStr = Field(min_length=1, max_length=128)
    output_type: AdeOutputType
    signal_name: StrictStr | None = Field(default=None, min_length=1, max_length=1024)
    expression: StrictStr | None = Field(default=None, min_length=1, max_length=4096)
    spec: AdeOutputSpec | None = None

    @field_validator("name", "signal_name", "expression")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Maestro output fields cannot contain control characters")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "AdeOutputAddition":
        if self.output_type is AdeOutputType.NET:
            if self.signal_name is None or self.expression is not None:
                raise ValueError(
                    "net Maestro outputs require signal_name and forbid expression"
                )
        elif self.expression is None or self.signal_name is not None:
            raise ValueError(
                "point Maestro outputs require expression and forbid signal_name"
            )
        return self

    def label(self) -> str:
        return f"{self.test}/{self.name}"


class AdeSetupApplySpec(StrictModel):
    """Patch analyses and add non-conflicting named outputs in one save."""

    backend: AdeBackend = AdeBackend.MAESTRO
    expected_tests: list[StrictStr] = Field(min_length=1, max_length=32)
    analyses: list[AdeAnalysisUpdate] = Field(default_factory=list, max_length=32)
    outputs: list[AdeOutputAddition] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_setup_patch(self) -> "AdeSetupApplySpec":
        if not self.analyses and not self.outputs:
            raise ValueError("ade.setup.apply requires analyses or outputs")
        for test in self.expected_tests:
            if (
                not test
                or len(test) > 128
                or any(
                    character in ('"', "\\")
                    or ord(character) < 32
                    or ord(character) == 127
                    for character in test
                )
            ):
                raise ValueError(f"invalid Maestro test name: {test!r}")
        if len(self.expected_tests) != len(set(self.expected_tests)):
            raise ValueError("expected_tests cannot contain duplicates")
        for item in [*self.analyses, *self.outputs]:
            if item.test not in self.expected_tests:
                raise ValueError(
                    f"Maestro setup item {item.label()!r} must target expected_tests"
                )
        analysis_identities = [
            (update.test, update.analysis) for update in self.analyses
        ]
        if len(analysis_identities) != len(set(analysis_identities)):
            raise ValueError("Maestro setup analyses cannot repeat a test/analysis")
        output_identities = [(output.test, output.name) for output in self.outputs]
        if len(output_identities) != len(set(output_identities)):
            raise ValueError("Maestro setup outputs cannot repeat a test/name")
        return self


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


class MosCharacterizationHoldout(StrictModel):
    """One simulated point withheld from the rectilinear interpolation grid."""

    polarity: Literal["nmos", "pmos"]
    length_um: float = Field(gt=0.0)
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(gt=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_finite_values(self) -> "MosCharacterizationHoldout":
        for name in (
            "length_um",
            "vgs_magnitude_v",
            "vds_magnitude_v",
            "vsb_magnitude_v",
        ):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        return self


class DeviceCharacterizationSourceBinding(StrictModel):
    """Trace one generated MOS task back to a structured real-si instance."""

    source_evidence_source: Literal["eda_result"] = "eda_result"
    derivation_evidence_source: Literal["software_inference"] = "software_inference"
    source_run_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_task_id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    source_action: StrictStr = Field(min_length=1, max_length=128)
    source_instance: StrictStr = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$.-]*$",
    )
    source_netlist_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    source_process_corner: StrictStr = Field(min_length=1, max_length=64)
    source_temperature_c: float = Field(ge=-273.15, le=300.0)
    source_topology_variant: StrictStr = Field(min_length=1, max_length=96)
    source_model: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$.-]*$",
    )
    source_width_um: float = Field(gt=0.0)
    source_length_um: float = Field(gt=0.0)
    source_model_parameter_count: int = Field(ge=0, le=64)
    source_model_parameters_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_finite_values(self) -> "DeviceCharacterizationSourceBinding":
        for name in ("source_temperature_c", "source_width_um", "source_length_um"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        return self


class DeviceCharacterizationSpec(StrictModel):
    """Finite, topology-independent MOS operating-point characterization."""

    polarities: list[Literal["nmos", "pmos"]] = Field(
        min_length=1,
        max_length=2,
    )
    width_um: float = Field(gt=0.0)
    model_parameters_by_polarity: dict[
        Literal["nmos", "pmos"], dict[StrictStr, StrictStr]
    ] = Field(default_factory=dict)
    source_instance_binding: DeviceCharacterizationSourceBinding | None = None
    lengths_um: list[float] = Field(min_length=1, max_length=8)
    vgs_magnitudes_v: list[float] = Field(min_length=1, max_length=16)
    vds_magnitudes_v: list[float] = Field(min_length=1, max_length=16)
    vsb_magnitudes_v: list[float] = Field(min_length=1, max_length=8)
    holdout_points: list[MosCharacterizationHoldout] = Field(
        default_factory=list,
        max_length=64,
    )
    temperature_c: float = Field(default=27.0, ge=-273.15, le=300.0)
    maximum_holdout_normalized_error: float = Field(default=0.25, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_grid(self) -> "DeviceCharacterizationSpec":
        axes = {
            "lengths_um": self.lengths_um,
            "vgs_magnitudes_v": self.vgs_magnitudes_v,
            "vds_magnitudes_v": self.vds_magnitudes_v,
            "vsb_magnitudes_v": self.vsb_magnitudes_v,
        }
        if len(self.polarities) != len(set(self.polarities)):
            raise ValueError("polarities cannot contain duplicates")
        if not math.isfinite(self.width_um):
            raise ValueError("width_um must be finite")
        if not math.isfinite(self.temperature_c):
            raise ValueError("temperature_c must be finite")
        if not math.isfinite(self.maximum_holdout_normalized_error):
            raise ValueError("maximum_holdout_normalized_error must be finite")
        reserved_parameters = {"w", "l", "nf", "m", "multi"}
        parameter_name = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
        numeric_literal = re.compile(
            r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
            r"(?:[A-Za-z][A-Za-z0-9_]*)?$"
        )
        for polarity, parameters in self.model_parameters_by_polarity.items():
            if polarity not in self.polarities:
                raise ValueError(
                    "model parameter polarity must be present in polarities"
                )
            if len(parameters) > 64:
                raise ValueError(
                    f"{polarity} model parameter signature exceeds 64 entries"
                )
            for name, value in parameters.items():
                if parameter_name.fullmatch(name) is None:
                    raise ValueError(f"invalid MOS model parameter name: {name!r}")
                if name.lower() in reserved_parameters:
                    raise ValueError(
                        f"MOS model parameter {name!r} is controlled by the "
                        "characterization contract"
                    )
                if len(value) > 64 or numeric_literal.fullmatch(value) is None:
                    raise ValueError(
                        f"MOS model parameter {name!r} must be a numeric Spectre "
                        "literal"
                    )
        for name, values in axes.items():
            if any(not math.isfinite(float(value)) for value in values):
                raise ValueError(f"{name} must contain only finite values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} cannot contain duplicates")
        if any(value <= 0.0 for value in self.lengths_um):
            raise ValueError("lengths_um must contain positive values")
        if any(value < 0.0 for value in self.vgs_magnitudes_v):
            raise ValueError("vgs_magnitudes_v cannot contain negative values")
        if any(value <= 0.0 for value in self.vds_magnitudes_v):
            raise ValueError("vds_magnitudes_v must contain positive values")
        if any(value < 0.0 for value in self.vsb_magnitudes_v):
            raise ValueError("vsb_magnitudes_v cannot contain negative values")
        if self.training_point_count > 256:
            raise ValueError("MOS characterization grid exceeds 256 training points")

        training = {
            (polarity, length, vgs, vds, vsb)
            for polarity in self.polarities
            for length in self.lengths_um
            for vgs in self.vgs_magnitudes_v
            for vds in self.vds_magnitudes_v
            for vsb in self.vsb_magnitudes_v
        }
        holdouts: set[tuple[str, float, float, float, float]] = set()
        bounds = {
            "length_um": (min(self.lengths_um), max(self.lengths_um)),
            "vgs_magnitude_v": (
                min(self.vgs_magnitudes_v),
                max(self.vgs_magnitudes_v),
            ),
            "vds_magnitude_v": (
                min(self.vds_magnitudes_v),
                max(self.vds_magnitudes_v),
            ),
            "vsb_magnitude_v": (
                min(self.vsb_magnitudes_v),
                max(self.vsb_magnitudes_v),
            ),
        }
        for holdout in self.holdout_points:
            identity = (
                holdout.polarity,
                holdout.length_um,
                holdout.vgs_magnitude_v,
                holdout.vds_magnitude_v,
                holdout.vsb_magnitude_v,
            )
            if holdout.polarity not in self.polarities:
                raise ValueError("holdout polarity must be present in polarities")
            if identity in training:
                raise ValueError("holdout point must not duplicate the training grid")
            if identity in holdouts:
                raise ValueError("holdout_points cannot contain duplicates")
            holdouts.add(identity)
            for name, (lower, upper) in bounds.items():
                value = float(getattr(holdout, name))
                if value < lower or value > upper:
                    raise ValueError(
                        f"holdout {name}={value} is outside the training grid "
                        f"[{lower}, {upper}]"
                    )
        return self

    @property
    def training_point_count(self) -> int:
        return (
            len(self.polarities)
            * len(self.lengths_um)
            * len(self.vgs_magnitudes_v)
            * len(self.vds_magnitudes_v)
            * len(self.vsb_magnitudes_v)
        )

    @property
    def total_point_count(self) -> int:
        return self.training_point_count + len(self.holdout_points)


_TUNING_OPERATIONS = {Operation.DESIGN_TUNE, Operation.DESIGN_CLOSE_LOOP}
_SIMULATION_OPERATIONS = _TUNING_OPERATIONS | {Operation.SIMULATION_RUN}


class TaskSpec(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation: Operation
    circuit: CircuitKind
    target: DesignTarget | None = None
    expected_target_topology_variant: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=96,
    )
    pdk_profile: str = Field(default=DEFAULT_PDK_PROFILE, min_length=1)
    analysis: AnalysisKind | None = None
    analysis_stages: list[AnalysisStageSpec] = Field(
        default_factory=list,
        max_length=4,
    )
    analysis_stage_execution: AnalysisStageExecution = (
        AnalysisStageExecution.ISOLATED
    )
    ac_sweep: AcSweep | None = None
    linearity_sweep: LinearitySweep | None = None
    noise_sweep: NoiseSweep | None = None
    ade_capture: AdeCaptureSpec | None = None
    ade_prepare: AdePrepareSpec | None = None
    ade_run: AdeRunSpec | None = None
    ade_variables: AdeVariablesApplySpec | None = None
    ade_corners: AdeCornersApplySpec | None = None
    ade_setup: AdeSetupApplySpec | None = None
    symbol_generation: SchematicSymbolGenerationSpec | None = None
    schematic_transform: SchematicTransformSpec | None = None
    topology_delta: TopologyDeltaExecutionSpec | None = None
    topology_refinement: ExistingSchematicTopologyRefinementSpec | None = None
    winner_verification: ExistingSchematicWinnerVerificationSpec | None = None
    device_characterization: DeviceCharacterizationSpec | None = None
    netlist_preview: NetlistPreviewSpec | None = None
    design_context: DesignContext | None = None
    generic_simulation: GenericOaSimulationSpec | None = None
    operating_conditions: list[OperatingCondition] = Field(
        default_factory=list,
        max_length=5,
    )
    parameters: dict[str, float] = Field(default_factory=dict)
    instance_parameter_updates: list[InstanceParameterUpdate] = Field(
        default_factory=list
    )
    parameter_space: dict[str, list[float]] = Field(default_factory=dict)
    candidate_set: AtomicCandidateSet | None = None
    theory_seed: TheorySeedCandidateSet | None = None
    instance_parameter_space: list[InstanceParameterSweep] = Field(
        default_factory=list,
        max_length=12,
    )
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
        if self.analysis_stages and self.circuit is not CircuitKind.EXISTING_SCHEMATIC:
            raise ValueError(
                "analysis_stages currently require circuit='existing_schematic'"
            )
        if (
            not self.analysis_stages
            and self.analysis_stage_execution
            is not AnalysisStageExecution.ISOLATED
        ):
            raise ValueError(
                "analysis_stage_execution='shared_netlist' requires analysis_stages"
            )
        if self.operation is Operation.DEVICE_CHARACTERIZE:
            if self.circuit is not CircuitKind.MOS_DEVICE:
                raise ValueError("device.characterize requires circuit='mos_device'")
            if self.target is not None:
                raise ValueError("device.characterize does not accept an OA target")
            if self.device_characterization is None:
                raise ValueError(
                    "device.characterize requires device_characterization settings"
                )
            if (
                self.analysis is not None
                or self.analysis_stages
                or self.ac_sweep is not None
                or self.linearity_sweep is not None
                or self.noise_sweep is not None
                or self.ade_capture is not None
                or self.ade_prepare is not None
                or self.ade_run is not None
                or self.ade_variables is not None
                or self.ade_corners is not None
                or self.ade_setup is not None
                or self.schematic_transform is not None
                or self.topology_delta is not None
                or self.topology_refinement is not None
                or self.winner_verification is not None
                or self.design_context is not None
                or self.generic_simulation is not None
                or self.netlist_preview is not None
                or self.expected_target_topology_variant is not None
                or self.operating_conditions
                or self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.instance_parameter_space
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "device.characterize accepts only device_characterization, "
                    "PDK, limits, and safety settings"
                )
            if self.safety.allow_remote_write or self.safety.replace_existing:
                raise ValueError("device.characterize cannot request remote OA writes")
            return self
        if self.circuit is CircuitKind.NETLIST_PREVIEW:
            if self.operation is not Operation.SIMULATION_RUN:
                raise ValueError(
                    "netlist_preview supports only operation='simulation.run'"
                )
            if self.target is not None:
                raise ValueError("netlist_preview does not accept an OA target")
            if self.netlist_preview is None:
                raise ValueError("netlist_preview requires netlist_preview settings")
            if self.analysis not in {AnalysisKind.DC, AnalysisKind.AC}:
                raise ValueError("netlist_preview requires explicit dc or ac analysis")
            if self.analysis_stages:
                raise ValueError("netlist_preview does not support analysis_stages")
            if self.analysis is AnalysisKind.AC and self.ac_sweep is None:
                raise ValueError("netlist_preview AC analysis requires ac_sweep")
            if self.analysis is AnalysisKind.DC and self.ac_sweep is not None:
                raise ValueError("netlist_preview DC analysis does not accept ac_sweep")
            if (
                self.linearity_sweep is not None
                or self.noise_sweep is not None
                or self.ade_capture is not None
                or self.ade_prepare is not None
                or self.ade_run is not None
                or self.ade_variables is not None
                or self.ade_corners is not None
                or self.ade_setup is not None
                or self.schematic_transform is not None
                or self.topology_delta is not None
                or self.topology_refinement is not None
                or self.winner_verification is not None
                or self.design_context is not None
                or self.generic_simulation is not None
                or self.device_characterization is not None
                or self.expected_target_topology_variant is not None
                or self.operating_conditions
                or self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.instance_parameter_space
                or self.create_if_missing
            ):
                raise ValueError(
                    "netlist_preview accepts only its structured circuit, dc/ac "
                    "settings, constraints/objective, PDK, limits, and safety"
                )
            if self.safety.allow_remote_write or self.safety.replace_existing:
                raise ValueError("netlist_preview cannot request remote OA writes")
            return self
        if self.circuit is CircuitKind.MOS_DEVICE:
            raise ValueError("mos_device supports only operation='device.characterize'")
        if self.netlist_preview is not None:
            raise ValueError(
                "netlist_preview settings require circuit='netlist_preview'"
            )
        if self.target is None:
            raise ValueError(f"{self.operation.value} requires an OA target")
        if (
            self.expected_target_topology_variant is not None
            and self.operation not in _SIMULATION_OPERATIONS
        ):
            raise ValueError(
                "expected_target_topology_variant requires a simulation or "
                "tuning operation"
            )
        if self.device_characterization is not None:
            raise ValueError(
                "device_characterization settings require operation='device.characterize'"
            )
        if self.operation is Operation.SCHEMATIC_SYMBOL_GENERATE:
            if self.circuit is not CircuitKind.EXISTING_SCHEMATIC:
                raise ValueError(
                    "schematic.symbol.generate requires circuit='existing_schematic'"
                )
            if self.target.view != "schematic":
                raise ValueError(
                    "schematic.symbol.generate requires target.view='schematic'; "
                    "the output is the sibling symbol view"
                )
            if self.symbol_generation is None:
                raise ValueError(
                    "schematic.symbol.generate requires symbol_generation settings"
                )
            if (
                self.expected_target_topology_variant is not None
                or self.analysis is not None
                or self.analysis_stages
                or self.ac_sweep is not None
                or self.linearity_sweep is not None
                or self.noise_sweep is not None
                or self.ade_capture is not None
                or self.ade_prepare is not None
                or self.ade_run is not None
                or self.ade_variables is not None
                or self.ade_corners is not None
                or self.ade_setup is not None
                or self.schematic_transform is not None
                or self.topology_delta is not None
                or self.topology_refinement is not None
                or self.winner_verification is not None
                or self.device_characterization is not None
                or self.netlist_preview is not None
                or self.design_context is not None
                or self.generic_simulation is not None
                or self.operating_conditions
                or self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.instance_parameter_space
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "schematic.symbol.generate accepts only its exact source/pin "
                    "contract, target, PDK, limits, and safety settings"
                )
            if self.safety.allow_remote_compute:
                raise ValueError(
                    "schematic.symbol.generate is an OA write, not remote compute"
                )
            if self.safety.replace_existing:
                raise ValueError(
                    "schematic.symbol.generate never replaces an existing symbol view"
                )
            return self
        if self.symbol_generation is not None:
            raise ValueError(
                "symbol_generation settings require "
                "operation='schematic.symbol.generate'"
            )
        generic_existing_simulation = (
            self.circuit is CircuitKind.EXISTING_SCHEMATIC
            and self.operation
            in {
                Operation.SIMULATION_RUN,
                Operation.DESIGN_TUNE,
                Operation.DESIGN_CLOSE_LOOP,
            }
        )
        if generic_existing_simulation:
            if self.generic_simulation is None:
                raise ValueError(
                    "existing_schematic simulation/tuning requires "
                    "generic_simulation"
                )
            if self.design_context is None:
                raise ValueError(
                    "existing_schematic simulation/tuning requires "
                    "design_context"
                )
            if self.analysis_stages:
                if self.analysis is not None:
                    raise ValueError(
                        "existing_schematic staged analysis cannot also declare analysis"
                    )
                stage_ids = [stage.id for stage in self.analysis_stages]
                if len(stage_ids) != len(set(stage_ids)):
                    raise ValueError("analysis stage ids must be unique")
                stage_analyses = [stage.analysis for stage in self.analysis_stages]
                if len(stage_analyses) != len(set(stage_analyses)):
                    raise ValueError(
                        "existing_schematic analysis stages cannot repeat an analysis"
                    )
                declared_constraint_metrics = {
                    constraint.metric for constraint in self.constraints
                }
                assigned_constraint_metrics = [
                    metric
                    for stage in self.analysis_stages
                    for metric in stage.constraint_metrics
                ]
                duplicate_assignments = sorted(
                    {
                        metric
                        for metric in assigned_constraint_metrics
                        if assigned_constraint_metrics.count(metric) > 1
                    }
                )
                if duplicate_assignments:
                    raise ValueError(
                        "analysis-stage constraints are assigned more than once: "
                        + ", ".join(duplicate_assignments)
                    )
                unknown_assignments = sorted(
                    set(assigned_constraint_metrics) - declared_constraint_metrics
                )
                if unknown_assignments:
                    raise ValueError(
                        "analysis stages reference undeclared constraint metrics: "
                        + ", ".join(unknown_assignments)
                    )
                unassigned_constraints = sorted(
                    declared_constraint_metrics - set(assigned_constraint_metrics)
                )
                if unassigned_constraints:
                    raise ValueError(
                        "every existing-schematic constraint must belong to one "
                        "analysis stage: " + ", ".join(unassigned_constraints)
                    )
                for index, stage in enumerate(self.analysis_stages):
                    self.generic_simulation.validate_analysis(stage.analysis.value)
                    available = self.generic_simulation.metric_names_for_analysis(
                        stage.analysis.value
                    )
                    unavailable = sorted(set(stage.constraint_metrics) - available)
                    if unavailable:
                        raise ValueError(
                            f"analysis stage {stage.id!r} cannot produce constraint "
                            "metrics: " + ", ".join(unavailable)
                        )
                    if index < len(self.analysis_stages) - 1 and not stage.constraint_metrics:
                        raise ValueError(
                            "every non-final analysis stage requires a constraint gate"
                        )
                if (
                    self.objective is not None
                    and not any(
                        self.objective.metric
                        in self.generic_simulation.metric_names_for_analysis(
                            stage.analysis.value
                        )
                        for stage in self.analysis_stages
                    )
                ):
                    raise ValueError(
                        "no analysis stage can produce the declared objective metric"
                    )
            else:
                if self.analysis not in {
                    AnalysisKind.DC,
                    AnalysisKind.AC,
                    AnalysisKind.TRANSIENT,
                    AnalysisKind.NOISE,
                }:
                    raise ValueError(
                        "existing_schematic simulation/tuning requires explicit "
                        "dc, ac, transient, or noise analysis, or analysis_stages"
                    )
                self.generic_simulation.validate_analysis(self.analysis.value)
            if self.target.view != "schematic":
                raise ValueError(
                    "existing_schematic simulation/tuning requires "
                    "target.view='schematic'"
                )
            if self.parameters:
                raise ValueError(
                    "generic existing-schematic simulation takes testbench values from "
                    "generic_simulation, not semantic parameters"
                )
            if self.parameter_space or self.theory_seed is not None:
                raise ValueError(
                    "generic existing-schematic tuning searches raw instance "
                    "parameters, not circuit-specific semantic parameters"
                )
            if (
                self.candidate_set is not None
                and self.candidate_set.candidates[0].parameters
            ):
                raise ValueError(
                    "generic existing-schematic candidate_set entries may contain "
                    "only instance_parameter_updates"
                )
            if self.create_if_missing:
                raise ValueError(
                    "existing_schematic simulation/tuning requires an "
                    "existing schematic"
                )
            if self.safety.replace_existing:
                raise ValueError(
                    "existing_schematic simulation/tuning never replaces "
                    "the target"
                )
            permitted_fields = {
                (permission.instance, parameter)
                for permission in self.design_context.instance_parameter_permissions
                for parameter in permission.parameters
            }
            bound_fields = {
                (binding.instance, binding.oa_parameter)
                for binding in self.generic_simulation.netlist_parameter_bindings
            }
            unpermitted_bindings = sorted(bound_fields - permitted_fields)
            if unpermitted_bindings:
                raise ValueError(
                    "generic simulation netlist bindings are outside design_context "
                    "parameter permissions: "
                    + ", ".join(
                        f"{instance}.{parameter}"
                        for instance, parameter in unpermitted_bindings
                    )
                )
            if self.operation in {
                Operation.DESIGN_TUNE,
                Operation.DESIGN_CLOSE_LOOP,
            }:
                changed_fields = {
                    (update.instance, parameter)
                    for update in self.instance_parameter_updates
                    for parameter in update.parameters
                }
                changed_fields.update(
                    (sweep.instance, sweep.parameter)
                    for sweep in self.instance_parameter_space
                )
                if self.candidate_set is not None:
                    changed_fields.update(
                        (update.instance, parameter)
                        for update in self.candidate_set.candidates[
                            0
                        ].instance_parameter_updates
                        for parameter in update.parameters
                    )
                unbound_fields = sorted(changed_fields - bound_fields)
                if unbound_fields:
                    raise ValueError(
                        "generic existing-schematic tuning fields require explicit "
                        "OA-to-si netlist parameter bindings: "
                        + ", ".join(
                            f"{instance}.{parameter}"
                            for instance, parameter in unbound_fields
                        )
                    )
        elif self.generic_simulation is not None:
            raise ValueError(
                "generic_simulation requires existing_schematic simulation.run or "
                "a tuning operation"
            )
        if self.winner_verification is not None:
            if (
                self.circuit is not CircuitKind.EXISTING_SCHEMATIC
                or self.operation not in _TUNING_OPERATIONS
            ):
                raise ValueError(
                    "winner_verification requires existing_schematic design.tune "
                    "or design.close_loop"
                )
            has_candidate_write = bool(
                self.instance_parameter_updates
                or self.instance_parameter_space
                or (
                    self.candidate_set is not None
                    and self.candidate_set.candidates[0].instance_parameter_updates
                )
            )
            if not has_candidate_write:
                raise ValueError(
                    "winner_verification requires an OA parameter candidate; it is "
                    "not a testbench-only selection mode"
                )
            assert self.generic_simulation is not None
            verification = self.winner_verification
            for stage in verification.analysis_stages:
                self.generic_simulation.validate_analysis(stage.analysis.value)
                unavailable = sorted(
                    set(stage.constraint_metrics)
                    - self.generic_simulation.metric_names_for_analysis(
                        stage.analysis.value
                    )
                )
                if unavailable:
                    raise ValueError(
                        f"winner-verification stage {stage.id!r} cannot produce "
                        "constraint metrics: " + ", ".join(unavailable)
                    )
            if verification.operating_conditions:
                if (
                    self.generic_simulation.operating_condition_supply_source
                    is None
                ):
                    raise ValueError(
                        "winner-verification PVT requires generic_simulation."
                        "operating_condition_supply_source"
                    )
        if self.topology_refinement is not None:
            if (
                self.circuit is not CircuitKind.EXISTING_SCHEMATIC
                or self.operation is not Operation.DESIGN_CLOSE_LOOP
            ):
                raise ValueError(
                    "topology_refinement requires existing_schematic "
                    "design.close_loop"
                )
            if self.topology_delta is not None or self.schematic_transform is not None:
                raise ValueError(
                    "topology_refinement owns its topology_delta and cannot be "
                    "combined with a top-level transform"
                )
            if self.objective is None:
                raise ValueError(
                    "existing-schematic topology close-loop requires an objective"
                )
            refinement = self.topology_refinement
            alternatives = refinement.resolved_alternatives()
            baseline_sha256 = alternatives[
                0
            ].topology_delta.contract.expected_before_sha256
            if self.design_context is None or self.generic_simulation is None:
                raise ValueError(
                    "topology refinement requires baseline design_context and "
                    "generic_simulation"
                )
            if (
                self.design_context.expected_topology_sha256
                != baseline_sha256
            ):
                raise ValueError(
                    "baseline design_context topology fingerprint must equal the "
                    "input of every topology alternative"
                )
            fixed_instance = {
                (update.instance, parameter)
                for update in self.instance_parameter_updates
                for parameter in update.parameters
            }
            searched_instance = {
                (sweep.instance, sweep.parameter)
                for sweep in self.instance_parameter_space
            }
            if self.candidate_set is not None:
                searched_instance.update(
                    (update.instance, parameter)
                    for update in self.candidate_set.candidates[
                        0
                    ].instance_parameter_updates
                    for parameter in update.parameters
                )
            metric_names = {constraint.metric for constraint in self.constraints}
            metric_names.add(self.objective.metric)
            context_ids = {self.design_context.id}
            for alternative in alternatives:
                contract = alternative.topology_delta.contract
                alternative_context = alternative.design_context
                alternative_simulation = alternative.generic_simulation
                if (
                    alternative_context.expected_topology_sha256
                    != contract.expected_after_sha256
                ):
                    raise ValueError(
                        f"alternative {alternative.id!r} design_context topology "
                        "fingerprint must equal its topology_delta output"
                    )
                if alternative_context.id in context_ids:
                    raise ValueError(
                        "baseline and alternative design_context ids must be unique"
                    )
                context_ids.add(alternative_context.id)
                for requested_analysis in self.resolved_analyses():
                    alternative_simulation.validate_analysis(
                        requested_analysis.value
                    )
                for stage in self.analysis_stages:
                    unavailable = sorted(
                        set(stage.constraint_metrics)
                        - alternative_simulation.metric_names_for_analysis(
                            stage.analysis.value
                        )
                    )
                    if unavailable:
                        raise ValueError(
                            f"alternative {alternative.id!r} analysis stage "
                            f"{stage.id!r} cannot produce constraint metrics: "
                            + ", ".join(unavailable)
                        )
                if self.winner_verification is not None:
                    for stage in self.winner_verification.analysis_stages:
                        alternative_simulation.validate_analysis(
                            stage.analysis.value
                        )
                        unavailable = sorted(
                            set(stage.constraint_metrics)
                            - alternative_simulation.metric_names_for_analysis(
                                stage.analysis.value
                            )
                        )
                        if unavailable:
                            raise ValueError(
                                f"alternative {alternative.id!r} winner-verification "
                                f"stage {stage.id!r} cannot produce constraint metrics: "
                                + ", ".join(unavailable)
                            )
                    if (
                        self.winner_verification.operating_conditions
                        and alternative_simulation.operating_condition_supply_source
                        is None
                    ):
                        raise ValueError(
                            f"alternative {alternative.id!r} winner-verification PVT "
                            "requires an operating_condition_supply_source"
                        )
                if self.analysis_stages and not any(
                    self.objective.metric
                    in alternative_simulation.metric_names_for_analysis(
                        stage.analysis.value
                    )
                    for stage in self.analysis_stages
                ):
                    raise ValueError(
                        f"no alternative {alternative.id!r} analysis stage can "
                        "produce the declared objective metric"
                    )

                alternative_fixed = {
                    (update.instance, parameter)
                    for update in alternative.instance_parameter_updates
                    for parameter in update.parameters
                }
                overlap = sorted(
                    alternative_fixed & (fixed_instance | searched_instance)
                )
                if overlap:
                    raise ValueError(
                        f"alternative {alternative.id!r} only parameters overlap "
                        "baseline/common fields: "
                        + ", ".join(
                            f"{instance}.{parameter}"
                            for instance, parameter in overlap
                        )
                    )
                added_instances = {
                    operation.instance.name
                    for operation in contract.operations
                    if operation.operation == "add_instance"
                }
                invalid_alternative_instances = sorted(
                    {instance for instance, _ in alternative_fixed}
                    - added_instances
                )
                if invalid_alternative_instances:
                    raise ValueError(
                        f"alternative {alternative.id!r} only parameters require "
                        "instances added by the topology_delta: "
                        + ", ".join(invalid_alternative_instances)
                    )
                alternative_context.validate_parameter_scope(
                    fixed_semantic=set(),
                    searched_semantic=set(),
                    fixed_instance=fixed_instance | alternative_fixed,
                    searched_instance=searched_instance,
                )
                alternative_context.validate_analysis_scope(
                    [analysis.value for analysis in self.resolved_analyses()],
                    metric_names,
                )
                validate_topology_delta_scope(
                    self.design_context,
                    alternative.topology_delta,
                )
                validate_topology_delta_scope(
                    alternative_context,
                    alternative.topology_delta.model_copy(
                        update={"direction": "inverse"}
                    ),
                )
                alternative_permitted_fields = {
                    (permission.instance, parameter)
                    for permission in alternative_context.instance_parameter_permissions
                    for parameter in permission.parameters
                }
                alternative_bound_fields = {
                    (binding.instance, binding.oa_parameter)
                    for binding in alternative_simulation.netlist_parameter_bindings
                }
                unpermitted_alternative_bindings = sorted(
                    alternative_bound_fields - alternative_permitted_fields
                )
                if unpermitted_alternative_bindings:
                    raise ValueError(
                        f"alternative {alternative.id!r} generic simulation bindings "
                        "are outside its design_context permissions: "
                        + ", ".join(
                            f"{instance}.{parameter}"
                            for instance, parameter in unpermitted_alternative_bindings
                        )
                    )
                unbound_alternative_fields = sorted(
                    (fixed_instance | searched_instance | alternative_fixed)
                    - alternative_bound_fields
                )
                if unbound_alternative_fields:
                    raise ValueError(
                        f"alternative {alternative.id!r} topology parameter fields "
                        "require explicit OA-to-si netlist bindings: "
                        + ", ".join(
                            f"{instance}.{parameter}"
                            for instance, parameter in unbound_alternative_fields
                        )
                    )

            if self.candidate_set is not None:
                parameter_candidate_count = len(self.candidate_set.candidates)
            else:
                parameter_candidate_count = math.prod(
                    len(sweep.values) for sweep in self.instance_parameter_space
                )
            required_iterations = (1 + len(alternatives)) * parameter_candidate_count
            if self.limits.max_iterations < required_iterations:
                raise ValueError(
                    "topology close-loop max_iterations must cover the complete "
                    "topology-parameter domain: "
                    f"required={required_iterations}, configured="
                    f"{self.limits.max_iterations}"
                )
        elif (
            self.circuit is CircuitKind.EXISTING_SCHEMATIC
            and self.operation is Operation.DESIGN_CLOSE_LOOP
        ):
            raise ValueError(
                "existing_schematic design.close_loop requires topology_refinement"
            )
        if self.operating_conditions:
            if self.operation not in _SIMULATION_OPERATIONS:
                raise ValueError(
                    "operating_conditions require a simulation or tuning operation"
                )
            if self.circuit not in {
                CircuitKind.COMMON_SOURCE,
                CircuitKind.DIFFERENTIAL_PAIR,
            }:
                raise ValueError(
                    "operating_conditions currently support only common_source "
                    "and differential_pair"
                )
            names = [condition.name for condition in self.operating_conditions]
            if len(names) != len(set(names)):
                raise ValueError("operating_conditions require unique names")
            condition_vdds = [
                condition.vdd_v is not None
                for condition in self.operating_conditions
            ]
            if any(condition_vdds) and not all(condition_vdds):
                raise ValueError(
                    "operating_conditions must all provide vdd_v or all inherit "
                    "one task-level vdd_v"
                )
            if all(condition_vdds):
                if "vdd_v" in self.parameters:
                    raise ValueError(
                        "operating_conditions with per-condition supplies must not "
                        "also declare vdd_v in task parameters"
                    )
                if (
                    "vdd_v" in self.parameter_space
                    or (
                        self.candidate_set is not None
                        and "vdd_v"
                        in self.candidate_set.candidates[0].parameters
                    )
                    or (
                        self.theory_seed is not None
                        and "vdd_v" in self.theory_seed.candidates[0].parameters
                    )
                ):
                    raise ValueError(
                        "operating_conditions with per-condition supplies must not "
                        "also tune vdd_v"
                    )
            if not any(condition_vdds) and "vdd_v" not in self.parameters:
                raise ValueError(
                    "operating_conditions without per-condition supplies require "
                    "one explicit task-level vdd_v"
                )
        analysis_settings = (
            self.analysis is not None
            or bool(self.analysis_stages)
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
            if (
                self.ac_sweep is not None
                and self.ac_sweep.evaluation_stop_hz is not None
                and resolved_analysis is not AnalysisKind.PSRR
            ):
                raise ValueError(
                    "ac_sweep evaluation_stop_hz is currently supported only "
                    "for differential-pair PSRR analysis"
                )
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
            elif self.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                if resolved_analysis not in {
                    AnalysisKind.DC,
                    AnalysisKind.AC,
                    AnalysisKind.TRANSIENT,
                    AnalysisKind.NOISE,
                    AnalysisKind.PSRR,
                }:
                    raise ValueError(
                        "differential_pair supports dc, ac, transient, noise, or "
                        "psrr analysis"
                    )
                if (
                    resolved_analysis in {AnalysisKind.AC, AnalysisKind.PSRR}
                    and self.ac_sweep is None
                ):
                    raise ValueError(
                        f"differential-pair {resolved_analysis.value.upper()} "
                        "analysis requires ac_sweep"
                    )
                if (
                    resolved_analysis is AnalysisKind.TRANSIENT
                    and self.linearity_sweep is None
                ):
                    raise ValueError(
                        "differential-pair transient analysis requires linearity_sweep"
                    )
                if (
                    resolved_analysis is AnalysisKind.NOISE
                    and self.noise_sweep is None
                ):
                    raise ValueError(
                        "differential-pair noise analysis requires noise_sweep"
                    )
                if resolved_analysis is AnalysisKind.DC and (
                    self.ac_sweep is not None
                    or self.linearity_sweep is not None
                    or self.noise_sweep is not None
                ):
                    raise ValueError(
                        "differential-pair DC does not accept dynamic sweep settings"
                    )
                if (
                    resolved_analysis in {AnalysisKind.AC, AnalysisKind.PSRR}
                    and (
                        self.linearity_sweep is not None
                        or self.noise_sweep is not None
                    )
                ):
                    raise ValueError(
                        f"differential-pair {resolved_analysis.value.upper()} "
                        "accepts only ac_sweep"
                    )
                if (
                    resolved_analysis is AnalysisKind.TRANSIENT
                    and (
                        self.ac_sweep is not None or self.noise_sweep is not None
                    )
                ):
                    raise ValueError(
                        "differential-pair transient accepts only linearity_sweep"
                    )
                if resolved_analysis is AnalysisKind.NOISE and (
                    self.ac_sweep is not None or self.linearity_sweep is not None
                ):
                    raise ValueError(
                        "differential-pair noise accepts only noise_sweep"
                    )
                if self.operation in _TUNING_OPERATIONS:
                    declared_parameters = (
                        self.parameters.keys()
                        | self.parameter_space.keys()
                        | (
                            self.candidate_set.candidates[0].parameters.keys()
                            if self.candidate_set is not None
                            else set()
                        )
                        | (
                            self.theory_seed.candidates[0].parameters.keys()
                            if self.theory_seed is not None
                            else set()
                        )
                    )
                    tail_parameter = (
                        "tail_bias_v"
                        if "tail_bias_v" in declared_parameters
                        else "tail_current_ua"
                    )
                    missing = sorted(
                        {tail_parameter, "common_mode_v", "vdd_v"}
                        - declared_parameters
                    )
                    if missing:
                        raise ValueError(
                            "differential_pair tuning requires explicit testbench "
                            "parameters for resumable evidence: " + ", ".join(missing)
                        )
            elif self.circuit is CircuitKind.EXISTING_SCHEMATIC:
                if not generic_existing_simulation:
                    raise ValueError(
                        "analysis settings for existing_schematic require "
                        "simulation.run or a tuning operation"
                    )
                requested_analyses = set(self.resolved_analyses())
                required_settings = {
                    AnalysisKind.AC: ("ac_sweep", self.ac_sweep),
                    AnalysisKind.TRANSIENT: (
                        "linearity_sweep",
                        self.linearity_sweep,
                    ),
                    AnalysisKind.NOISE: ("noise_sweep", self.noise_sweep),
                }
                for requested, (name, value) in required_settings.items():
                    if (requested in requested_analyses) != (value is not None):
                        if requested in requested_analyses:
                            raise ValueError(
                                f"existing-schematic {requested.value} analysis "
                                f"requires {name}"
                            )
                        raise ValueError(
                            f"existing-schematic {name} requires a matching "
                            f"{requested.value} analysis stage"
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
                or self.candidate_set is not None
                or self.theory_seed is not None
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
                or self.candidate_set is not None
                or self.theory_seed is not None
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
        if self.operation is Operation.ADE_RUN:
            if self.ade_run is None:
                raise ValueError("ade.run requires ade_run settings")
            if self.target.view != "maestro":
                raise ValueError("ade.run currently requires target.view='maestro'")
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.run executes the saved Maestro setup and does not accept "
                    "parameters, search, or creation requests"
                )
            result_mapping = self.ade_run.result_mapping
            if result_mapping is None and (self.constraints or self.objective is not None):
                raise ValueError(
                    "ade.run constraints/objective require an explicit result_mapping"
                )
            if result_mapping is not None:
                if not self.constraints:
                    raise ValueError(
                        "ade.run result_mapping requires at least one VDA constraint"
                    )
                mapped_metrics = {binding.metric for binding in result_mapping.metrics}
                required_metrics = {item.metric for item in self.constraints}
                if self.objective is not None:
                    required_metrics.add(self.objective.metric)
                missing_metrics = sorted(required_metrics - mapped_metrics)
                if missing_metrics:
                    raise ValueError(
                        "ADE result mapping is missing constraint/objective metrics: "
                        f"{missing_metrics}"
                    )
            if self.safety.replace_existing:
                raise ValueError("ade.run never replaces an existing Maestro view")
        elif self.ade_run is not None:
            raise ValueError("ade_run settings require operation='ade.run'")
        if self.operation is Operation.ADE_VARIABLES_APPLY:
            if self.ade_variables is None:
                raise ValueError(
                    "ade.variables.apply requires ade_variables settings"
                )
            if self.target.view != "maestro":
                raise ValueError(
                    "ade.variables.apply currently requires target.view='maestro'"
                )
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.variables.apply only patches declared global Maestro "
                    "variables and does not accept parameters, search, constraints, "
                    "objective, or creation requests"
                )
            if self.safety.replace_existing:
                raise ValueError(
                    "ade.variables.apply never replaces an existing Maestro view"
                )
        elif self.ade_variables is not None:
            raise ValueError(
                "ade_variables settings require operation='ade.variables.apply'"
            )
        if self.operation is Operation.ADE_CORNERS_APPLY:
            if self.ade_corners is None:
                raise ValueError("ade.corners.apply requires ade_corners settings")
            if self.target.view != "maestro":
                raise ValueError(
                    "ade.corners.apply currently requires target.view='maestro'"
                )
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.corners.apply only adds declared Maestro corners and does "
                    "not accept parameters, search, constraints, objective, or "
                    "creation requests"
                )
            if self.safety.replace_existing:
                raise ValueError(
                    "ade.corners.apply never replaces an existing Maestro view"
                )
        elif self.ade_corners is not None:
            raise ValueError(
                "ade_corners settings require operation='ade.corners.apply'"
            )
        if self.operation is Operation.ADE_SETUP_APPLY:
            if self.ade_setup is None:
                raise ValueError("ade.setup.apply requires ade_setup settings")
            if self.target.view != "maestro":
                raise ValueError(
                    "ade.setup.apply currently requires target.view='maestro'"
                )
            if (
                self.parameters
                or self.instance_parameter_updates
                or self.parameter_space
                or self.candidate_set is not None
                or self.theory_seed is not None
                or self.constraints
                or self.objective is not None
                or self.create_if_missing
            ):
                raise ValueError(
                    "ade.setup.apply only patches declared Maestro analyses/outputs "
                    "and does not accept parameters, search, constraints, objective, "
                    "or creation requests"
                )
            if self.safety.replace_existing:
                raise ValueError(
                    "ade.setup.apply never replaces an existing Maestro view"
                )
        elif self.ade_setup is not None:
            raise ValueError("ade_setup settings require operation='ade.setup.apply'")
        if self.instance_parameter_updates:
            if self.operation not in ({Operation.PARAMETERS_APPLY} | _TUNING_OPERATIONS):
                raise ValueError(
                    "instance_parameter_updates are supported only by "
                    "parameters.apply or tuning operations"
                )
            instances = [update.instance for update in self.instance_parameter_updates]
            if len(instances) != len(set(instances)):
                raise ValueError(
                    "instance_parameter_updates cannot repeat an instance"
                )
        if self.instance_parameter_space:
            if self.operation not in _TUNING_OPERATIONS:
                raise ValueError(
                    "instance_parameter_space requires design.tune or "
                    "design.close_loop"
                )
            dimensions = [
                (sweep.instance, sweep.parameter)
                for sweep in self.instance_parameter_space
            ]
            if len(dimensions) != len(set(dimensions)):
                raise ValueError(
                    "instance_parameter_space cannot repeat an instance/parameter"
                )
            fixed = {
                (update.instance, parameter)
                for update in self.instance_parameter_updates
                for parameter in update.parameters
            }
            overlap = sorted(fixed & set(dimensions))
            if overlap:
                formatted = ", ".join(
                    f"{instance}.{parameter}" for instance, parameter in overlap
                )
                raise ValueError(
                    "fixed instance_parameter_updates overlap swept dimensions: "
                    + formatted
                )
        if self.candidate_set is not None:
            if self.operation not in _TUNING_OPERATIONS:
                raise ValueError(
                    "candidate_set requires design.tune or design.close_loop"
                )
            if (
                self.parameter_space
                or self.instance_parameter_space
                or self.theory_seed is not None
            ):
                raise ValueError(
                    "candidate_set is atomic and cannot be combined with parameter_space, "
                    "instance_parameter_space, or theory_seed"
                )
            candidate = self.candidate_set.candidates[0]
            semantic_overlap = sorted(set(self.parameters) & set(candidate.parameters))
            if semantic_overlap:
                raise ValueError(
                    "fixed parameters overlap candidate_set fields: "
                    + ", ".join(semantic_overlap)
                )
            fixed_instance_fields = {
                (update.instance, parameter)
                for update in self.instance_parameter_updates
                for parameter in update.parameters
            }
            candidate_instance_fields = candidate.field_identity()[1]
            instance_overlap = sorted(
                fixed_instance_fields & candidate_instance_fields
            )
            if instance_overlap:
                raise ValueError(
                    "fixed instance_parameter_updates overlap candidate_set fields: "
                    + ", ".join(
                        f"{instance}.{parameter}"
                        for instance, parameter in instance_overlap
                    )
                )
        if self.theory_seed is not None:
            if self.operation not in _TUNING_OPERATIONS:
                raise ValueError(
                    "theory_seed requires design.tune or design.close_loop"
                )
            if self.parameter_space or self.instance_parameter_space:
                raise ValueError(
                    "theory_seed is atomic and cannot be combined with parameter_space "
                    "or instance_parameter_space"
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
            if self.instance_parameter_updates:
                raise ValueError(
                    "schematic.transform does not accept instance_parameter_updates"
                )
            if self.parameter_space:
                raise ValueError("schematic.transform does not accept parameter_space")
            if self.candidate_set is not None:
                raise ValueError("schematic.transform does not accept candidate_set")
            if self.theory_seed is not None:
                raise ValueError("schematic.transform does not accept theory_seed")
            if self.circuit is CircuitKind.EXISTING_SCHEMATIC:
                if self.topology_delta is None:
                    raise ValueError(
                        "existing_schematic schematic.transform requires a "
                        "predeclared topology_delta"
                    )
                if self.schematic_transform is not None:
                    raise ValueError(
                        "generic topology_delta cannot be combined with the fixed "
                        "schematic_transform action"
                    )
                if self.parameters:
                    raise ValueError(
                        "generic topology_delta does not accept semantic parameters; "
                        "device parameters remain a separate parameters.apply contract"
                    )
                if self.target is not None and self.target.view != "schematic":
                    raise ValueError(
                        "generic topology_delta currently requires target view='schematic'"
                    )
                if self.safety.replace_existing:
                    raise ValueError(
                        "generic topology_delta never replaces the target cellview"
                    )
            elif self.topology_delta is not None:
                raise ValueError(
                    "topology_delta currently requires circuit='existing_schematic'"
                )
            elif self.circuit is CircuitKind.COMMON_SOURCE:
                action = self.resolved_schematic_transform_action()
                if (
                    action is SchematicTransformAction.ADD_SOURCE_DEGENERATION
                    and not self.parameters
                ):
                    raise ValueError(
                        "add_source_degeneration requires source_resistance_ohm"
                    )
                if (
                    action is SchematicTransformAction.ADD_SOURCE_DEGENERATION
                    and self.schematic_transform is not None
                    and self.schematic_transform.expected_restored_placement_sha256
                    is not None
                ):
                    raise ValueError(
                        "expected_restored_placement_sha256 is valid only for "
                        "remove_source_degeneration"
                    )
                if (
                    action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                    and self.parameters
                ):
                    raise ValueError(
                        "remove_source_degeneration does not accept parameters"
                    )
            elif self.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                if self.schematic_transform is None:
                    raise ValueError(
                        "differential-pair schematic.transform requires an explicit "
                        "schematic_transform action"
                    )
                action = self.schematic_transform.action
                supported_actions = {
                    SchematicTransformAction.ADD_TAIL_DEVICE,
                    SchematicTransformAction.ADD_SOURCE_DEGENERATION,
                    SchematicTransformAction.REMOVE_SOURCE_DEGENERATION,
                    SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR,
                    SchematicTransformAction.RESTORE_RESISTIVE_LOAD,
                }
                if action not in supported_actions:
                    raise ValueError(
                        "unsupported differential-pair schematic.transform action"
                    )
                if (
                    self.schematic_transform.expected_restored_placement_sha256
                    is not None
                    and action
                    not in {
                        SchematicTransformAction.REMOVE_SOURCE_DEGENERATION,
                        SchematicTransformAction.RESTORE_RESISTIVE_LOAD,
                    }
                ):
                    raise ValueError(
                        "expected_restored_placement_sha256 is valid only for a "
                        "restoring transform"
                    )
                if action is SchematicTransformAction.ADD_TAIL_DEVICE and not self.parameters:
                    raise ValueError(
                        "add_tail_device requires tail_width_um and tail_length_um"
                    )
                if (
                    action is SchematicTransformAction.ADD_SOURCE_DEGENERATION
                    and not self.parameters
                ):
                    raise ValueError(
                        "add_source_degeneration requires source_resistance_ohm"
                    )
                if (
                    action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
                    and self.parameters
                ):
                    raise ValueError(
                        "remove_source_degeneration does not accept parameters"
                    )
                if (
                    action
                    is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
                    and not self.parameters
                ):
                    raise ValueError(
                        "replace_resistive_load_with_current_mirror requires "
                        "pmos_load_width_um and pmos_load_length_um"
                    )
                if (
                    action is SchematicTransformAction.RESTORE_RESISTIVE_LOAD
                    and not self.parameters
                ):
                    raise ValueError(
                        "restore_resistive_load requires load_resistance_ohm"
                    )
            else:
                if self.schematic_transform is not None:
                    raise ValueError(
                        "schematic_transform settings currently support only "
                        "common_source or differential_pair"
                    )
                if not self.parameters:
                    raise ValueError("schematic.transform requires parameters")
        elif self.schematic_transform is not None:
            raise ValueError(
                "schematic_transform settings require operation='schematic.transform'"
            )
        elif self.topology_delta is not None:
            raise ValueError(
                "topology_delta settings require operation='schematic.transform'"
            )
        if self.operation in _TUNING_OPERATIONS:
            if (
                not self.parameter_space
                and self.candidate_set is None
                and self.theory_seed is None
                and not self.instance_parameter_space
            ):
                raise ValueError(
                    f"{self.operation.value} requires parameter_space, candidate_set, "
                    "theory_seed, or instance_parameter_space"
                )
            if not self.constraints:
                raise ValueError(f"{self.operation.value} requires constraints")
        if (
            self.operation is Operation.SIMULATION_RUN
            and not self.parameters
            and not generic_existing_simulation
        ):
            raise ValueError("simulation.run requires parameters")
        if self.design_context is not None:
            context_operations = {
                Operation.SCHEMATIC_INSPECT,
                Operation.SCHEMATIC_TRANSFORM,
                Operation.PARAMETERS_APPLY,
                Operation.SIMULATION_RUN,
                Operation.DESIGN_TUNE,
                Operation.DESIGN_CLOSE_LOOP,
            }
            if self.operation not in context_operations:
                raise ValueError(
                    "design_context supports inspect, transform, parameter, simulation, "
                    "or tuning operations"
                )
            if (
                self.operation is Operation.DESIGN_CLOSE_LOOP
                and self.create_if_missing
            ):
                raise ValueError(
                    "design_context close-loop requires an existing schematic; "
                    "create_if_missing must remain false"
                )
            if (
                self.operation is Operation.SCHEMATIC_TRANSFORM
                and self.topology_delta is None
            ):
                raise ValueError(
                    "design_context schematic.transform requires an explicit "
                    "topology_delta so every local edit is scope-checked"
                )

            fixed_semantic = set(self.parameters)
            searched_semantic = set(self.parameter_space)
            if self.candidate_set is not None:
                searched_semantic.update(
                    self.candidate_set.candidates[0].parameters
                )
            if self.theory_seed is not None:
                searched_semantic.update(self.theory_seed.candidates[0].parameters)
            fixed_instance = {
                (update.instance, parameter)
                for update in self.instance_parameter_updates
                for parameter in update.parameters
            }
            searched_instance = {
                (sweep.instance, sweep.parameter)
                for sweep in self.instance_parameter_space
            }
            if self.candidate_set is not None:
                searched_instance.update(
                    {
                        (update.instance, parameter)
                        for update in self.candidate_set.candidates[
                            0
                        ].instance_parameter_updates
                        for parameter in update.parameters
                    }
                )
            self.design_context.validate_parameter_scope(
                fixed_semantic=fixed_semantic,
                searched_semantic=searched_semantic,
                fixed_instance=fixed_instance,
                searched_instance=searched_instance,
            )
            if self.operation in _SIMULATION_OPERATIONS:
                metric_names = {constraint.metric for constraint in self.constraints}
                if self.objective is not None:
                    metric_names.add(self.objective.metric)
                self.design_context.validate_analysis_scope(
                    [analysis.value for analysis in self.resolved_analyses()],
                    metric_names,
                )
            if self.topology_delta is not None:
                validate_topology_delta_scope(
                    self.design_context,
                    self.topology_delta,
                )
        return self

    def resolved_analysis(self) -> AnalysisKind:
        if self.analysis is not None:
            return self.analysis
        if self.analysis_stages:
            return self.analysis_stages[-1].analysis
        if self.circuit is CircuitKind.INVERTER:
            return AnalysisKind.TRANSIENT
        return AnalysisKind.DC

    def resolved_analyses(self) -> tuple[AnalysisKind, ...]:
        if self.analysis_stages:
            return tuple(stage.analysis for stage in self.analysis_stages)
        analysis = self.resolved_analysis()
        if analysis is AnalysisKind.QUALITY:
            return (
                AnalysisKind.AC,
                AnalysisKind.TRANSIENT,
                AnalysisKind.NOISE,
            )
        return (analysis,)

    def resolved_schematic_transform_action(
        self,
    ) -> SchematicTransformAction | None:
        if (
            self.operation is not Operation.SCHEMATIC_TRANSFORM
        ):
            return None
        if self.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            return (
                self.schematic_transform.action
                if self.schematic_transform is not None
                else None
            )
        if self.circuit is not CircuitKind.COMMON_SOURCE:
            return None
        if self.schematic_transform is None:
            return SchematicTransformAction.ADD_SOURCE_DEGENERATION
        return self.schematic_transform.action


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


class AnalysisStageEvaluation(StrictModel):
    stage_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    analysis: AnalysisKind
    metrics: dict[str, float]
    constraints: list[ConstraintEvaluation]
    passed: bool
    evidence_source: EvidenceSource
    metric_sources: dict[str, EvidenceSource] = Field(default_factory=dict)
    analysis_complete: bool = True
    analysis_issues: list[str] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    operating_conditions: list["OperatingConditionEvaluation"] = Field(
        default_factory=list
    )


class CandidateEvaluation(StrictModel):
    index: int
    topology_variant_id: str | None = None
    topology_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    parameters: dict[str, float]
    instance_parameters: dict[str, dict[str, str]] = Field(default_factory=dict)
    oa_parameters: dict[str, float] = Field(default_factory=dict)
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
    operating_conditions: list["OperatingConditionEvaluation"] = Field(
        default_factory=list
    )
    atomic_candidate_id: str | None = None
    atomic_candidate_predicted_metrics: dict[str, float] = Field(
        default_factory=dict
    )
    atomic_candidate_evidence_source: EvidenceSource | None = None
    theory_seed_candidate_id: str | None = None
    theory_seed_source_candidate_id: str | None = None
    theory_seed_predicted_metrics: dict[str, float] = Field(default_factory=dict)
    theory_seed_evidence_source: EvidenceSource | None = None
    analysis_stages: list[AnalysisStageEvaluation] = Field(default_factory=list)
    terminated_after_stage: str | None = None


class OperatingConditionEvaluation(StrictModel):
    name: str
    process_corner: str
    temperature_c: float
    vdd_v: float
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


class SearchAudit(StrictModel):
    """Explicitly bounds what a finite candidate selection can claim."""

    declared_candidate_count: int = Field(ge=1)
    attempted_candidate_count: int = Field(ge=0)
    completed_candidate_count: int = Field(ge=0)
    topology_variant_count: int = Field(default=1, ge=1, le=8)
    domain_exhausted: bool
    selection_scope: SelectionScope
    continuous_optimum_claim: Literal[False] = False
    global_optimum_claim: Literal[False] = False
    statement: str = Field(min_length=1)
    candidate_set_source: CandidateSetSource | None = None
    theory_seed_source: TheorySeedSource | None = None


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
    selected_instance_parameters: dict[str, dict[str, str]] | None = None
    selected_metrics: dict[str, float] | None = None
    selected_topology_variant_id: str | None = None
    selected_topology_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    winner_verification: CandidateEvaluation | None = None
    search_audit: SearchAudit | None = None
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
    initial_instance_parameters: dict[str, dict[str, str]] = Field(
        default_factory=dict
    )
    expected_oa_instance_parameters: dict[str, dict[str, str]] = Field(
        default_factory=dict
    )
    pending_oa_instance_parameters: dict[str, dict[str, str]] | None = None
    initial_topology_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_topology_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pending_topology_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_topology_variant_id: str | None = None
    next_candidate_index: int = Field(ge=1)
    active_candidate_index: int | None = Field(default=None, ge=1)
    next_analysis_stage_index: int = Field(default=1, ge=1)
    active_candidate_stages: list[AnalysisStageEvaluation] = Field(
        default_factory=list
    )
    actions: list[ActionRecord] = Field(default_factory=list)
    candidates: list[CandidateEvaluation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    complete: bool = False


class SpectreModelInclude(StrictModel):
    path: str = Field(min_length=1)
    section: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )


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
    default_inverter_nmos_width_um: float = Field(gt=0)
    default_inverter_pmos_to_nmos_width_ratio: float = Field(gt=0)
    default_common_source_width_um: float = Field(gt=0)
    default_common_source_bias_v: float = Field(gt=0)
    default_common_source_load_resistance_ohm: float = Field(gt=0)
    process_corners: dict[str, list[SpectreModelInclude]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_remote_write_paths(self) -> "PdkProfile":
        for name in ("cds_lib_path", "remote_run_root"):
            value = getattr(self, name)
            if not value.startswith("/data/xum/"):
                raise ValueError(f"{name} must stay under /data/xum")
        for corner, includes in self.process_corners.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", corner):
                raise ValueError(f"invalid process corner name: {corner!r}")
            if not includes:
                raise ValueError(f"process corner {corner!r} has no model includes")
            identities = [(item.path, item.section) for item in includes]
            if len(identities) != len(set(identities)):
                raise ValueError(
                    f"process corner {corner!r} repeats a model include"
                )
        return self
