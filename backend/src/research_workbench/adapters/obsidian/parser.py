from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


FRONTMATTER_PATTERN = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
WIKI_LINK_PATTERN = re.compile(r"(!)?\[\[([^\[\]]+?)\]\]")


@dataclass(frozen=True, slots=True)
class WikiLink:
    target: str
    alias: str | None
    embed: bool
    line: int


@dataclass(slots=True)
class ParsedMarkdown:
    path: Path
    title: str
    properties: dict[str, Any]
    body: str
    headings: list[str]
    links: list[WikiLink]
    aliases: list[str]
    parse_errors: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _parse_aliases(properties: dict[str, Any]) -> list[str]:
    value = properties.get("aliases", properties.get("alias", []))
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _lenient_frontmatter(raw: str) -> dict[str, Any] | None:
    """Parse Obsidian-style simple properties that are not strict YAML.

    Obsidian accepts unquoted colons and template placeholders in scalar values.
    The fallback intentionally supports only flat keys and indented list items;
    complex malformed structures remain errors instead of being guessed.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        list_match = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if list_match and current_key:
            if result.get(current_key) is None:
                result[current_key] = []
            if not isinstance(result[current_key], list):
                return None
            result[current_key].append(list_match.group(1).strip().strip("'\""))
            continue
        key_match = re.match(r"^([^\s:#][^:]*?):(?:\s*(.*))?$", line)
        if not key_match:
            return None
        current_key = key_match.group(1).strip()
        raw_value = (key_match.group(2) or "").strip()
        if not raw_value:
            result[current_key] = None
            continue
        try:
            scalar = yaml.safe_load(f"value: {raw_value}")["value"]
        except (yaml.YAMLError, TypeError):
            if raw_value.startswith(("[", "{")) and not (
                raw_value.startswith("{{") and raw_value.endswith("}}")
            ):
                return None
            scalar = raw_value.strip("'\"")
        result[current_key] = _json_safe(scalar)
    return result


def parse_markdown(path: Path, text: str) -> ParsedMarkdown:
    properties: dict[str, Any] = {}
    body = text
    errors: list[str] = []
    warnings: list[str] = []
    match = FRONTMATTER_PATTERN.match(text)
    if match:
        raw_frontmatter = match.group(1)
        body = text[match.end() :]
        try:
            loaded = yaml.safe_load(raw_frontmatter) or {}
            if not isinstance(loaded, dict):
                errors.append("frontmatter 必须是键值映射")
            else:
                properties = _json_safe(loaded)
        except yaml.YAMLError as exc:
            fallback = _lenient_frontmatter(raw_frontmatter)
            if fallback is None:
                errors.append(f"frontmatter YAML 无法解析：{exc}")
            else:
                properties = fallback
                warnings.append("frontmatter 使用了 Obsidian 兼容但非标准 YAML 的写法")

    headings = [item.group(2).strip() for item in HEADING_PATTERN.finditer(body)]
    title = headings[0] if headings else path.stem
    links: list[WikiLink] = []
    for item in WIKI_LINK_PATTERN.finditer(body):
        payload = item.group(2).strip()
        target, separator, alias = payload.partition("|")
        target = target.strip()
        if not target:
            continue
        links.append(
            WikiLink(
                target=target,
                alias=alias.strip() if separator and alias.strip() else None,
                embed=bool(item.group(1)),
                line=body.count("\n", 0, item.start()) + 1,
            )
        )

    return ParsedMarkdown(
        path=path,
        title=title,
        properties=properties,
        body=body,
        headings=headings,
        links=links,
        aliases=_parse_aliases(properties),
        parse_errors=errors,
        parse_warnings=warnings,
    )


def section_text(body: str, names: tuple[str, ...]) -> str | None:
    """Return the first non-empty paragraph or list item under a named heading."""
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(body))
    wanted = {name.casefold() for name in names}
    for index, match in enumerate(matches):
        if match.group(2).strip().casefold() not in wanted:
            continue
        level = len(match.group(1))
        end = len(body)
        for following in matches[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        block = body[match.end() : end].strip()
        for line in block.splitlines():
            clean = re.sub(r"^\s*(?:[-*+] |\d+[.)]\s+)", "", line).strip()
            if clean and not clean.startswith("<!--"):
                return clean
    return None
