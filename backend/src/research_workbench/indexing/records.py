from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScannedNote:
    id: str
    path: str
    title: str
    note_type: str
    status: str | None
    domain: str | None
    project: str | None
    reading_stage: str | None
    reading_value: str | None
    collection: str | None
    zotero_key: str | None
    bibtex_key: str | None
    modified_at: str
    size: int
    word_count: int
    properties: dict[str, Any]
    headings: list[str]
    aliases: list[str]
    content: str
    excerpt: str


@dataclass(slots=True)
class ScannedLink:
    source_id: str
    source_path: str
    target_text: str
    alias: str | None
    line: int
    embed: bool
    status: str
    resolved_note_id: str | None = None


@dataclass(slots=True)
class ScanIssue:
    severity: str
    code: str
    message: str
    path: str | None = None
    note_id: str | None = None
    field: str | None = None


@dataclass(slots=True)
class ExternalPathRecord:
    note_id: str
    note_path: str
    field: str
    value: str
    status: str


@dataclass(slots=True)
class ScanBundle:
    vault_path: str
    started_at: str
    finished_at: str
    notes: list[ScannedNote] = field(default_factory=list)
    links: list[ScannedLink] = field(default_factory=list)
    issues: list[ScanIssue] = field(default_factory=list)
    external_paths: list[ExternalPathRecord] = field(default_factory=list)
