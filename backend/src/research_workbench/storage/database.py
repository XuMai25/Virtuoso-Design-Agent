from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from research_workbench.indexing.records import ScanBundle


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    scan_id INTEGER NOT NULL,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    note_type TEXT NOT NULL,
    status TEXT,
    domain TEXT,
    project TEXT,
    reading_stage TEXT,
    reading_value TEXT,
    collection_name TEXT,
    zotero_key TEXT,
    bibtex_key TEXT,
    modified_at TEXT NOT NULL,
    size INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    properties_json TEXT NOT NULL,
    headings_json TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    FOREIGN KEY(scan_id) REFERENCES scans(id)
);

CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(note_type);
CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
CREATE INDEX IF NOT EXISTS idx_notes_domain ON notes(domain);
CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes(modified_at DESC);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_text TEXT NOT NULL,
    alias TEXT,
    line INTEGER NOT NULL,
    embed INTEGER NOT NULL,
    status TEXT NOT NULL,
    resolved_note_id TEXT,
    FOREIGN KEY(scan_id) REFERENCES scans(id),
    FOREIGN KEY(source_id) REFERENCES notes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(resolved_note_id);
CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    note_id TEXT,
    path TEXT,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    field TEXT,
    FOREIGN KEY(scan_id) REFERENCES scans(id)
);

CREATE INDEX IF NOT EXISTS idx_issues_severity ON issues(severity);
CREATE INDEX IF NOT EXISTS idx_issues_code ON issues(code);

CREATE TABLE IF NOT EXISTS external_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    note_id TEXT NOT NULL,
    note_path TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(scan_id) REFERENCES scans(id)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SQLiteStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def rebuild(self, bundle: ScanBundle) -> dict[str, Any]:
        type_counts = Counter(note.note_type for note in bundle.notes)
        issue_counts = Counter(issue.severity for issue in bundle.issues)
        link_counts = Counter(link.status for link in bundle.links)
        summary: dict[str, Any] = {
            "scan_id": 0,
            "vault_path": bundle.vault_path,
            "started_at": bundle.started_at,
            "finished_at": bundle.finished_at,
            "markdown_files": len(bundle.notes),
            "projects": type_counts["project"],
            "literature": type_counts["literature"],
            "concepts": type_counts["concept"],
            "experiments": type_counts["experiment"],
            "inbox": type_counts["inbox"],
            "broken_links": link_counts["missing"],
            "ambiguous_links": link_counts["ambiguous"],
            "invalid_external_paths": len(
                {
                    item.value.casefold()
                    for item in bundle.external_paths
                    if item.status == "missing"
                }
            ),
            "error": issue_counts["error"],
            "warning": issue_counts["warning"],
            "info": issue_counts["info"],
        }
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO scans(vault_path, started_at, finished_at, summary_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    bundle.vault_path,
                    bundle.started_at,
                    bundle.finished_at,
                    "{}",
                ),
            )
            scan_id = int(cursor.lastrowid)
            summary["scan_id"] = scan_id
            connection.execute("DELETE FROM links")
            connection.execute("DELETE FROM issues")
            connection.execute("DELETE FROM external_paths")
            connection.execute("DELETE FROM notes")

            connection.executemany(
                """
                INSERT INTO notes(
                    id, scan_id, path, title, note_type, status, domain, project,
                    reading_stage, reading_value, collection_name, zotero_key,
                    bibtex_key, modified_at, size, word_count, properties_json,
                    headings_json, aliases_json, content, excerpt
                ) VALUES (
                    :id, :scan_id, :path, :title, :note_type, :status, :domain,
                    :project, :reading_stage, :reading_value, :collection,
                    :zotero_key, :bibtex_key, :modified_at, :size, :word_count,
                    :properties_json, :headings_json, :aliases_json, :content, :excerpt
                )
                """,
                [
                    {
                        **asdict(note),
                        "scan_id": scan_id,
                        "properties_json": json.dumps(
                            note.properties, ensure_ascii=False, default=str
                        ),
                        "headings_json": json.dumps(note.headings, ensure_ascii=False),
                        "aliases_json": json.dumps(note.aliases, ensure_ascii=False),
                    }
                    for note in bundle.notes
                ],
            )
            connection.executemany(
                """
                INSERT INTO links(
                    scan_id, source_id, source_path, target_text, alias, line,
                    embed, status, resolved_note_id
                ) VALUES (
                    :scan_id, :source_id, :source_path, :target_text, :alias,
                    :line, :embed, :status, :resolved_note_id
                )
                """,
                [
                    {**asdict(link), "scan_id": scan_id, "embed": int(link.embed)}
                    for link in bundle.links
                ],
            )
            connection.executemany(
                """
                INSERT INTO issues(scan_id, note_id, path, severity, code, message, field)
                VALUES (:scan_id, :note_id, :path, :severity, :code, :message, :field)
                """,
                [{**asdict(issue), "scan_id": scan_id} for issue in bundle.issues],
            )
            connection.executemany(
                """
                INSERT INTO external_paths(
                    scan_id, note_id, note_path, field, value, status
                ) VALUES (
                    :scan_id, :note_id, :note_path, :field, :value, :status
                )
                """,
                [
                    {**asdict(record), "scan_id": scan_id}
                    for record in bundle.external_paths
                ],
            )
            connection.execute(
                "UPDATE scans SET summary_json = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False), scan_id),
            )
            connection.execute(
                "INSERT INTO app_state(key, value) VALUES ('current_scan_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(scan_id),),
            )
            connection.commit()
        return summary

    def get_summary(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT summary_json FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return json.loads(row["summary_json"]) if row else None

    def list_notes(
        self,
        *,
        note_type: str | None = None,
        status: str | None = None,
        domain: str | None = None,
        reading_stage: str | None = None,
        collection: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("note_type", note_type),
            ("status", status),
            ("domain", domain),
            ("reading_stage", reading_stage),
            ("collection_name", collection),
        ):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if query:
            clauses.append("(title LIKE ? OR path LIKE ? OR content LIKE ?)")
            pattern = f"%{query}%"
            parameters.extend([pattern, pattern, pattern])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT * FROM notes"
            + where
            + " ORDER BY modified_at DESC, title COLLATE NOCASE LIMIT ? OFFSET ?"
        )
        parameters.extend([min(max(limit, 1), 500), max(offset, 0)])
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._note_row(row, include_content=False) for row in rows]

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if not row:
                return None
            outgoing = connection.execute(
                "SELECT target_text, alias, line, status, resolved_note_id "
                "FROM links WHERE source_id = ? ORDER BY line", (note_id,)
            ).fetchall()
            incoming = connection.execute(
                "SELECT source_id, source_path, target_text, line "
                "FROM links WHERE resolved_note_id = ? ORDER BY source_path", (note_id,)
            ).fetchall()
            issues = connection.execute(
                "SELECT severity, code, message, field FROM issues WHERE note_id = ? "
                "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END",
                (note_id,),
            ).fetchall()
            external = connection.execute(
                "SELECT field, value, status FROM external_paths WHERE note_id = ?",
                (note_id,),
            ).fetchall()
        result = self._note_row(row, include_content=True)
        result["outgoing_links"] = [dict(item) for item in outgoing]
        result["backlinks"] = [dict(item) for item in incoming]
        result["issues"] = [dict(item) for item in issues]
        result["external_paths"] = [dict(item) for item in external]
        return result

    def list_issues(
        self,
        *,
        severity: str | None = None,
        code: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if code:
            clauses.append("code = ?")
            params.append(code)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(limit, 1), 1000))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, note_id, path, severity, code, message, field FROM issues"
                + where
                + " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, path LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def facets(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        with self.connect() as connection:
            for key, column in (
                ("types", "note_type"),
                ("statuses", "status"),
                ("domains", "domain"),
                ("reading_stages", "reading_stage"),
                ("collections", "collection_name"),
            ):
                rows = connection.execute(
                    f"SELECT DISTINCT {column} AS value FROM notes "
                    f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY value"
                ).fetchall()
                result[key] = [row["value"] for row in rows]
        return result

    @staticmethod
    def _note_row(row: sqlite3.Row, *, include_content: bool) -> dict[str, Any]:
        result = dict(row)
        result["type"] = result.pop("note_type")
        result["collection"] = result.pop("collection_name")
        result["properties"] = json.loads(result.pop("properties_json"))
        result["headings"] = json.loads(result.pop("headings_json"))
        result["aliases"] = json.loads(result.pop("aliases_json"))
        result.pop("scan_id", None)
        if not include_content:
            result.pop("content", None)
        return result
