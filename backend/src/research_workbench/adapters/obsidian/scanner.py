from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote

from research_workbench.adapters.obsidian.parser import ParsedMarkdown, parse_markdown, section_text
from research_workbench.indexing.records import (
    ExternalPathRecord,
    ScanBundle,
    ScannedLink,
    ScannedNote,
    ScanIssue,
)


FOLDER_TYPES = {
    "00_收件箱": "inbox",
    "01_日记": "daily",
    "02_项目": "project",
    "03_领域": "area",
    "04_文献": "literature",
    "05_概念": "concept",
    "06_方法": "method",
    "07_实验": "experiment",
    "08_会议": "meeting",
    "09_工具": "resource",
    "90_模板": "template",
}
VALID_STATUS = {
    "进行中",
    "等待中",
    "阻塞",
    "完成",
    "归档",
    "未读",
    "阅读中",
    "已读完",
    "草稿",
    "待复查",
}
PATH_FIELDS = {
    "external_path",
    "output_path",
    "result_path",
    "results_path",
    "evidence_path",
    "attachment_path",
    "working_directory",
}
WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])([A-Za-z]:\\[^\r\n`|<>\"?*；，。]+)", re.UNICODE
)
INLINE_CODE_PATTERN = re.compile(r"(?<!`)`([^`\r\n]+)`(?!`)")
MARKDOWN_PATH_PATTERN = re.compile(r"\]\((?:file:///)?([A-Za-z]:[\\/][^)]+)\)")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        joined = ", ".join(str(item) for item in value if str(item).strip())
        return joined or None
    text = str(value).strip()
    return text or None


def _normalize_link_path(value: str) -> str:
    clean = unquote(value).replace("\\", "/").strip().strip("/")
    if clean.casefold().endswith(".md"):
        clean = clean[:-3]
    return str(PurePosixPath(clean)).replace("./", "", 1).casefold()


def _note_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.casefold().encode("utf-8")).hexdigest()[:20]


def _excerpt(body: str, limit: int = 260) -> str:
    text = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"!?(?:\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\])", r"\2\1", text
    )
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _infer_type(relative_path: str, parsed: ParsedMarkdown) -> str:
    first = PurePosixPath(relative_path).parts[0] if "/" in relative_path else ""
    if first == "90_模板":
        return "template"
    explicit = _as_text(parsed.properties.get("type"))
    if explicit:
        return explicit.casefold()
    return FOLDER_TYPES.get(first, "other")


def _collection(relative_path: str, note_type: str) -> str | None:
    parts = PurePosixPath(relative_path).parts
    if note_type != "literature" or len(parts) < 3:
        return None
    return parts[1]


def _property_or_section(
    parsed: ParsedMarkdown, keys: Iterable[str], headings: tuple[str, ...]
) -> str | None:
    for key in keys:
        value = _as_text(parsed.properties.get(key))
        if value:
            return value
    return section_text(parsed.body, headings)


def _iter_property_paths(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            field = f"{prefix}.{key_text}" if prefix else key_text
            key_folded = key_text.casefold()
            if isinstance(nested, str) and (
                key_folded in PATH_FIELDS or key_folded.endswith("_path")
            ):
                yield field, nested
            elif isinstance(nested, (dict, list)):
                yield from _iter_property_paths(nested, field)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _iter_property_paths(nested, f"{prefix}[{index}]")


def _clean_detected_path(value: str) -> str:
    return value.strip().strip("'\"").rstrip(".,;:，。；：)]}）】")


def _is_command_text(value: str) -> bool:
    return bool(
        re.search(r"\.(?:exe|py|bat|cmd|ps1)\s+\S", value, re.IGNORECASE)
        or re.search(r"\s--?[A-Za-z]", value)
    )


class VaultScanner:
    """Read an Obsidian vault without creating or modifying any vault entry."""

    def __init__(self, vault_path: Path, ignored_directories: frozenset[str]):
        self.vault_path = vault_path.resolve()
        self.ignored_directories = ignored_directories

    def _markdown_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.vault_path.rglob("*.md"):
            relative = path.relative_to(self.vault_path)
            if any(part in self.ignored_directories for part in relative.parts[:-1]):
                continue
            if path.is_file():
                files.append(path)
        return sorted(files, key=lambda item: item.as_posix().casefold())

    def scan(self) -> ScanBundle:
        started_at = _now()
        parsed_by_id: dict[str, ParsedMarkdown] = {}
        notes: list[ScannedNote] = []
        issues: list[ScanIssue] = []
        external_paths: list[ExternalPathRecord] = []

        for path in self._markdown_files():
            relative = path.relative_to(self.vault_path).as_posix()
            identifier = _note_id(relative)
            try:
                # Scanner boundary is deliberately read-only.
                with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                    text = handle.read()
            except OSError as exc:
                issues.append(
                    ScanIssue(
                        severity="error",
                        code="file_read_error",
                        message=f"无法读取 Markdown：{exc}",
                        path=relative,
                        note_id=identifier,
                    )
                )
                continue

            parsed = parse_markdown(path, text)
            parsed_by_id[identifier] = parsed
            stat = path.stat()
            note_type = _infer_type(relative, parsed)
            properties = parsed.properties
            note = ScannedNote(
                id=identifier,
                path=relative,
                title=parsed.title,
                note_type=note_type,
                status=_as_text(properties.get("status")),
                domain=_as_text(properties.get("domain")),
                project=_as_text(properties.get("project")),
                reading_stage=_as_text(properties.get("reading_stage")),
                reading_value=_as_text(properties.get("reading_value")),
                collection=_collection(relative, note_type),
                zotero_key=_as_text(properties.get("zotero_key")),
                bibtex_key=_as_text(properties.get("bibtex_key")),
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                size=stat.st_size,
                word_count=len(re.findall(r"[\w\u4e00-\u9fff]+", parsed.body)),
                properties=properties,
                headings=parsed.headings,
                aliases=parsed.aliases,
                content=parsed.body,
                excerpt=_excerpt(parsed.body),
            )
            notes.append(note)

            for error in parsed.parse_errors:
                issues.append(
                    ScanIssue(
                        severity="error",
                        code="frontmatter_parse_error",
                        message=error,
                        path=relative,
                        note_id=identifier,
                    )
                )
            for warning in parsed.parse_warnings if note_type != "template" else []:
                issues.append(
                    ScanIssue(
                        severity="info",
                        code="frontmatter_nonstandard",
                        message=warning,
                        path=relative,
                        note_id=identifier,
                    )
                )
            issues.extend(self._property_issues(note, parsed))
            external_paths.extend(self._external_paths(note, parsed))

        links, link_issues = self._resolve_links(notes, parsed_by_id)
        issues.extend(link_issues)
        for record in external_paths:
            if record.status == "missing":
                issues.append(
                    ScanIssue(
                        severity="warning",
                        code="external_path_missing",
                        message=f"外部路径不存在：{record.value}",
                        path=record.note_path,
                        note_id=record.note_id,
                        field=record.field,
                    )
                )

        return ScanBundle(
            vault_path=str(self.vault_path),
            started_at=started_at,
            finished_at=_now(),
            notes=notes,
            links=links,
            issues=issues,
            external_paths=external_paths,
        )

    def _property_issues(
        self, note: ScannedNote, parsed: ParsedMarkdown
    ) -> list[ScanIssue]:
        result: list[ScanIssue] = []

        def add_missing(field: str, label: str, severity: str = "info") -> None:
            result.append(
                ScanIssue(
                    severity=severity,
                    code="property_missing",
                    message=f"{label}缺失",
                    path=note.path,
                    note_id=note.id,
                    field=field,
                )
            )

        if note.status and note.status not in VALID_STATUS:
            result.append(
                ScanIssue(
                    severity="warning",
                    code="property_value_unknown",
                    message=f"status 使用了未登记值：{note.status}",
                    path=note.path,
                    note_id=note.id,
                    field="status",
                )
            )

        if note.note_type == "project":
            if not _property_or_section(parsed, ("goal", "target", "目标"), ("目标", "项目目标")):
                add_missing("goal", "项目目标")
            if not _property_or_section(
                parsed, ("next_step", "next_action", "下一步"), ("下一步", "下一步行动")
            ):
                add_missing("next_step", "项目下一步", "warning")
            if not _property_or_section(
                parsed,
                ("completion_criteria", "done_when", "完成标准"),
                ("完成标准", "验收标准"),
            ):
                add_missing("completion_criteria", "项目完成标准")
        elif note.note_type == "literature":
            for field, label in (
                ("reading_stage", "阅读阶段"),
                ("reading_value", "阅读价值"),
                ("zotero_key", "Zotero key"),
                ("bibtex_key", "BibTeX key"),
            ):
                if not _as_text(parsed.properties.get(field)):
                    add_missing(field, label)
        elif note.note_type == "experiment":
            if not _property_or_section(parsed, ("project",), ("所属项目", "项目")):
                add_missing("project", "实验所属项目")
            if not _property_or_section(parsed, ("evidence",), ("证据", "关键证据")):
                add_missing("evidence", "实验证据")
            if not _property_or_section(parsed, ("judgment",), ("判断", "结论")):
                add_missing("judgment", "实验判断")
        return result

    def _external_paths(
        self, note: ScannedNote, parsed: ParsedMarkdown
    ) -> list[ExternalPathRecord]:
        found: dict[str, tuple[str, str]] = {}
        for field, raw in _iter_property_paths(parsed.properties):
            value = _clean_detected_path(os.path.expandvars(raw))
            if re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\\\"):
                found[value.casefold()] = (field, value)
        body_candidates = [match.group(1) for match in INLINE_CODE_PATTERN.finditer(parsed.body)]
        body_candidates.extend(
            match.group(1) for match in MARKDOWN_PATH_PATTERN.finditer(parsed.body)
        )
        for raw in body_candidates:
            value = _clean_detected_path(os.path.expandvars(raw.replace("/", "\\")))
            if (
                WINDOWS_PATH_PATTERN.fullmatch(value)
                and not _is_command_text(value)
                and "..." not in value
            ):
                found.setdefault(value.casefold(), ("正文路径", value))

        records: list[ExternalPathRecord] = []
        for field, value in found.values():
            try:
                status = "exists" if Path(value).exists() else "missing"
            except OSError:
                status = "unavailable"
            records.append(
                ExternalPathRecord(
                    note_id=note.id,
                    note_path=note.path,
                    field=field,
                    value=value,
                    status=status,
                )
            )
        return records

    def _resolve_links(
        self, notes: list[ScannedNote], parsed_by_id: dict[str, ParsedMarkdown]
    ) -> tuple[list[ScannedLink], list[ScanIssue]]:
        exact: dict[str, list[str]] = defaultdict(list)
        basename: dict[str, list[str]] = defaultdict(list)
        aliases: dict[str, list[str]] = defaultdict(list)
        for note in notes:
            without_suffix = note.path[:-3] if note.path.casefold().endswith(".md") else note.path
            exact[_normalize_link_path(without_suffix)].append(note.id)
            basename[PurePosixPath(without_suffix).name.casefold()].append(note.id)
            aliases[note.title.casefold()].append(note.id)
            for alias in note.aliases:
                aliases[alias.casefold()].append(note.id)

        resolved_links: list[ScannedLink] = []
        issues: list[ScanIssue] = []
        for note in notes:
            parsed = parsed_by_id[note.id]
            source_parent = PurePosixPath(note.path).parent
            for raw in parsed.links:
                target_without_anchor = raw.target.split("#", 1)[0].split("^", 1)[0].strip()
                candidates: set[str] = set()
                if not target_without_anchor:
                    candidates.add(note.id)
                else:
                    normalized = _normalize_link_path(target_without_anchor)
                    candidates.update(exact.get(normalized, []))
                    relative_target = _normalize_link_path(str(source_parent / target_without_anchor))
                    candidates.update(exact.get(relative_target, []))
                    if "/" not in normalized:
                        candidates.update(basename.get(normalized, []))
                        candidates.update(aliases.get(normalized, []))
                    else:
                        for indexed_path, ids in exact.items():
                            if indexed_path.endswith("/" + normalized):
                                candidates.update(ids)

                if len(candidates) == 1:
                    status = "resolved"
                    resolved_id = next(iter(candidates))
                elif len(candidates) > 1:
                    status = "ambiguous"
                    resolved_id = None
                else:
                    status = "missing"
                    resolved_id = None
                resolved_links.append(
                    ScannedLink(
                        source_id=note.id,
                        source_path=note.path,
                        target_text=raw.target,
                        alias=raw.alias,
                        line=raw.line,
                        embed=raw.embed,
                        status=status,
                        resolved_note_id=resolved_id,
                    )
                )
                if status != "resolved":
                    label = raw.alias or raw.target
                    issues.append(
                        ScanIssue(
                            severity="warning",
                            code="wiki_link_ambiguous" if status == "ambiguous" else "wiki_link_missing",
                            message=(
                                f"双链目标有多个候选：{label}"
                                if status == "ambiguous"
                                else f"双链目标不存在：{label}"
                            ),
                            path=note.path,
                            note_id=note.id,
                            field=f"line:{raw.line}",
                        )
                    )
        return resolved_links, issues
