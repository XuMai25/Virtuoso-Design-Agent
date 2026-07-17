from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class NoteKind(StrEnum):
    PROJECT = "project"
    LITERATURE = "literature"
    EXPERIMENT = "experiment"
    CONCEPT = "concept"
    METHOD = "method"
    AREA = "area"
    MEETING = "meeting"
    DAILY = "daily"
    WEEKLY = "weekly"
    RESOURCE = "resource"
    INDEX = "index"
    INBOX = "inbox"
    OTHER = "other"


class ObjectBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    path: str
    title: str
    status: str | None = None
    domain: str | None = None
    modified_at: str
    excerpt: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class ProjectView(ObjectBase):
    goal: str | None = None
    next_step: str | None = None
    completion_criteria: str | None = None
    external_path: str | None = None
    external_path_status: str | None = None
    source_label: str = "Vault 记录"

    @field_validator("source_label")
    @classmethod
    def source_must_be_explicit(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_label 不能为空")
        return value


class LiteratureView(ObjectBase):
    collection: str | None = None
    reading_stage: str | None = None
    reading_value: str | None = None
    zotero_key: str | None = None
    bibtex_key: str | None = None
    fulltext_coverage: str | None = None
    needs_close_reading: str | None = None


class ExperimentView(ObjectBase):
    project: str | None = None
    environment: str | None = None
    command: str | None = None
    output_path: str | None = None
    output_path_status: str | None = None
    evidence: str | None = None
    judgment: str | None = None
    next_step: str | None = None


class ScanSummary(BaseModel):
    scan_id: int
    vault_path: str
    started_at: str
    finished_at: str
    markdown_files: int
    projects: int
    literature: int
    concepts: int
    experiments: int
    inbox: int
    broken_links: int
    ambiguous_links: int
    invalid_external_paths: int
    error: int
    warning: int
    info: int
