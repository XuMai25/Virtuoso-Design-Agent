"""Topology-independent device-characterization contracts."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, model_validator

from .models import EvidenceSource, StrictModel


class DeviceDataSource(str, Enum):
    """Declared origin of device values supplied to a theory calculation."""

    PDK_CHARACTERIZATION = "pdk_characterization"
    EDA_OPERATING_POINT = "eda_operating_point"
    SYNTHETIC_EXAMPLE = "synthetic_example"


class MosPolarity(str, Enum):
    NMOS = "nmos"
    PMOS = "pmos"


class _FiniteStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MosSmallSignalPoint(_FiniteStrictModel):
    """One width-normalized MOS operating point, independent of circuit role."""

    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    model: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z_][A-Za-z0-9_$.-]*$",
    )
    polarity: MosPolarity
    length_um: float = Field(gt=0.0)
    vgs_magnitude_v: float = Field(ge=0.0)
    vds_magnitude_v: float = Field(ge=0.0)
    vsb_magnitude_v: float = Field(ge=0.0)
    vdsat_magnitude_v: float = Field(gt=0.0)
    drain_current_density_a_per_um: float = Field(gt=0.0)
    gm_over_id_per_v: float = Field(gt=0.0)
    gds_over_id_per_v: float = Field(ge=0.0)
    gmb_over_id_per_v: float = Field(default=0.0, ge=0.0)
    cgs_f_per_um: float = Field(default=0.0, ge=0.0)
    cgd_f_per_um: float = Field(default=0.0, ge=0.0)
    cgb_f_per_um: float = Field(default=0.0, ge=0.0)
    cdb_f_per_um: float = Field(default=0.0, ge=0.0)
    csb_f_per_um: float = Field(default=0.0, ge=0.0)


class MosCharacterizationArtifact(_FiniteStrictModel):
    """Finite MOS data set plus the conditions and evidence that define it."""

    schema_version: Literal[1] = 1
    id: StrictStr = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    source: DeviceDataSource
    source_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pdk_profile: StrictStr = Field(min_length=1, max_length=96)
    process_corner: StrictStr = Field(min_length=1, max_length=64)
    temperature_c: float = Field(ge=-273.15, le=300.0)
    raw_data_evidence_source: EvidenceSource
    normalized_point_evidence_source: EvidenceSource
    points: list[MosSmallSignalPoint] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_source_and_points(self) -> "MosCharacterizationArtifact":
        if self.source is DeviceDataSource.SYNTHETIC_EXAMPLE:
            if self.source_artifact_sha256 is not None:
                raise ValueError("synthetic characterization cannot claim an artifact hash")
            if (
                self.raw_data_evidence_source is not EvidenceSource.USER_INPUT
                or self.normalized_point_evidence_source
                is not EvidenceSource.USER_INPUT
            ):
                raise ValueError("synthetic characterization data must be user_input")
        else:
            if self.source_artifact_sha256 is None:
                raise ValueError(
                    "PDK/EDA characterization requires source_artifact_sha256"
                )
            if self.raw_data_evidence_source is not EvidenceSource.EDA_RESULT:
                raise ValueError("PDK/EDA raw characterization must be eda_result")
            if (
                self.normalized_point_evidence_source
                is not EvidenceSource.SOFTWARE_INFERENCE
            ):
                raise ValueError(
                    "normalized PDK/EDA characterization points must be "
                    "software_inference"
                )
        point_ids = [point.id for point in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("characterization contains duplicate point ids")
        return self
