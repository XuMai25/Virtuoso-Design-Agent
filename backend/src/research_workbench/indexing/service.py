from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from research_workbench.adapters.obsidian.parser import section_text
from research_workbench.adapters.obsidian.scanner import VaultScanner
from research_workbench.config import AppConfig
from research_workbench.storage.database import SQLiteStore


ACTIVE_STATUSES = {"进行中", "等待中", "阻塞"}
PENDING_READING_STAGES = {"待筛选", "AI已预读", "粗读", "精读中", "待复查"}


def _first_value(note: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    properties = note.get("properties", {})
    for key in keys:
        value = properties.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _field_or_section(
    note: dict[str, Any], keys: tuple[str, ...], headings: tuple[str, ...]
) -> str | None:
    return _first_value(note, keys) or section_text(note.get("content", ""), headings)


class IndexService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.config.prepare_writable_directories()
        self.store = SQLiteStore(config.database_path)
        self.store.initialize()
        self._scan_lock = threading.Lock()

    def scan(self) -> dict[str, Any]:
        if not self._scan_lock.acquire(blocking=False):
            raise RuntimeError("扫描正在进行中")
        try:
            scanner = VaultScanner(
                self.config.vault_path, self.config.ignored_directories
            )
            return self.store.rebuild(scanner.scan())
        finally:
            self._scan_lock.release()

    def ensure_indexed(self) -> dict[str, Any]:
        return self.store.get_summary() or self.scan()

    def summary(self) -> dict[str, Any] | None:
        return self.store.get_summary()

    def notes(self, **filters: Any) -> list[dict[str, Any]]:
        return self.store.list_notes(**filters)

    def note(self, note_id: str) -> dict[str, Any] | None:
        return self.store.get_note(note_id)

    def issues(self, **filters: Any) -> list[dict[str, Any]]:
        return self.store.list_issues(**filters)

    def facets(self) -> dict[str, list[str]]:
        return self.store.facets()

    def projects(self, **filters: Any) -> list[dict[str, Any]]:
        notes = self.store.list_notes(note_type="project", **filters)
        return [self._project_view(note) for note in notes]

    def literature(self, **filters: Any) -> list[dict[str, Any]]:
        notes = self.store.list_notes(note_type="literature", **filters)
        return [self._literature_view(note) for note in notes]

    def experiments(self, **filters: Any) -> list[dict[str, Any]]:
        notes = self.store.list_notes(note_type="experiment", **filters)
        return [self._experiment_view(note) for note in notes]

    def knowledge(self, **filters: Any) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        for note_type in ("concept", "method", "area"):
            result.extend(self.store.list_notes(note_type=note_type, **filters))
        result.sort(key=lambda item: item["modified_at"], reverse=True)
        broken = self.store.list_issues(code="wiki_link_missing", limit=100)
        ambiguous = self.store.list_issues(code="wiki_link_ambiguous", limit=100)
        return {
            "items": result,
            "diagnostics": {
                "unresolved_links": len(broken),
                "ambiguous_links": len(ambiguous),
            },
        }

    def dashboard(self) -> dict[str, Any]:
        summary = self.store.get_summary()
        projects = self.projects(limit=100)
        active = [
            item
            for item in projects
            if item.get("status") in ACTIVE_STATUSES
            or (not item.get("status") and item.get("next_step"))
        ]
        waiting = [
            item for item in projects if item.get("status") in {"等待中", "阻塞"}
        ]
        recent = self.store.list_notes(limit=8)
        inbox = [
            item
            for item in self.store.list_notes(note_type="inbox", limit=50)
            if not item["path"].endswith("收件箱.md")
        ]
        literature = [
            self._literature_view(item)
            for item in self.store.list_notes(note_type="literature", limit=100)
            if item.get("reading_stage") in PENDING_READING_STAGES
            or item.get("status") in {"未读", "阅读中", "待复查"}
        ][:8]
        experiments = [
            self._experiment_view(item)
            for item in self.store.list_notes(note_type="experiment", limit=6)
        ]
        review_items = self._review_items(projects)
        return {
            "summary": summary,
            "active_projects": active,
            "waiting_or_blocked": waiting,
            "recent_notes": recent,
            "inbox": inbox[:8],
            "reading_queue": literature,
            "recent_experiments": experiments,
            "review_items": review_items,
            "next_actions": [
                {
                    "project_id": item["id"],
                    "project": item["title"],
                    "status": item.get("status"),
                    "next_step": item.get("next_step") or "尚未记录明确下一步",
                    "source": "Vault 记录" if item.get("next_step") else "软件检查",
                }
                for item in active[:6]
            ],
        }

    def inbox_suggestions(self) -> list[dict[str, Any]]:
        inbox = [
            item
            for item in self.store.list_notes(note_type="inbox", limit=200)
            if not item["path"].endswith("收件箱.md")
        ]
        related_pool = self.store.list_notes(limit=250)
        suggestions: list[dict[str, Any]] = []
        for note in inbox:
            haystack = f"{note['title']} {note.get('excerpt', '')}".casefold()
            if any(token in haystack for token in ("doi", "paper", "论文", "文献")):
                destination = "04_文献（先核对 Zotero collection）"
                reason = "内容看起来与论文或题录有关"
            elif any(token in haystack for token in ("实验", "仿真", "综合", "结果")):
                destination = "07_实验"
                reason = "内容包含实验或工程结果线索"
            elif any(token in haystack for token in ("项目", "交付", "下一步", "阻塞")):
                destination = "02_项目"
                reason = "内容包含项目推进或行动线索"
            elif any(token in haystack for token in ("概念", "是什么", "定义", "术语")):
                destination = "05_概念"
                reason = "内容可能是可复用概念"
            else:
                destination = "保留在收件箱，等待人工判断"
                reason = "现有证据不足以可靠分类"

            tokens = {
                token
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", haystack)
                if len(token) > 2
            }
            related: list[dict[str, str]] = []
            for candidate in related_pool:
                if candidate["id"] == note["id"] or candidate["type"] == "inbox":
                    continue
                candidate_text = f"{candidate['title']} {candidate.get('excerpt', '')}".casefold()
                score = sum(token.casefold() in candidate_text for token in tokens)
                if score:
                    related.append(
                        {
                            "id": candidate["id"],
                            "title": candidate["title"],
                            "type": candidate["type"],
                            "score": str(score),
                        }
                    )
            related.sort(key=lambda item: int(item["score"]), reverse=True)
            suggestions.append(
                {
                    "note": note,
                    "suggested_destination": destination,
                    "reason": reason,
                    "confidence": "低" if "等待人工" in destination else "中",
                    "related": related[:3],
                    "write_action": "无；仅生成建议",
                }
            )
        return suggestions

    def daily_review(self) -> dict[str, Any]:
        return self._review(days=1, label="日")

    def weekly_review(self) -> dict[str, Any]:
        return self._review(days=7, label="周")

    def _review(self, *, days: int, label: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        notes = self.store.list_notes(limit=500)
        recent = [
            note
            for note in notes
            if datetime.fromisoformat(note["modified_at"]) >= since
        ]
        projects = self.projects(limit=200)
        blocked = [item for item in projects if item.get("status") in {"阻塞", "等待中"}]
        stale = self._review_items(projects)
        by_type: dict[str, list[dict[str, Any]]] = {
            "project": [],
            "literature": [],
            "concept": [],
            "experiment": [],
        }
        for item in recent:
            if item["type"] in by_type:
                by_type[item["type"]].append(item)

        lines = [
            f"# {label}复盘草稿 · {now.astimezone().date().isoformat()}",
            "",
            "> 由科研工作台根据 Vault 最近修改时间和当前 frontmatter 生成；这是预览，不会写入 Vault。",
            "",
            "## 本周期推进",
        ]
        if recent:
            lines.extend(f"- {item['title']}（{item['type']}）" for item in recent[:12])
        else:
            lines.append("- 本周期未检测到 Markdown 修改。")
        lines.extend(["", "## 项目状态与下一步"])
        for item in projects:
            if item.get("status") in ACTIVE_STATUSES:
                lines.append(
                    f"- {item['title']}：{item.get('status') or '未记录状态'}；下一步："
                    f"{item.get('next_step') or '待补充'}"
                )
        lines.extend(["", "## 新增或更新的知识证据"])
        for key, label in (
            ("literature", "文献"),
            ("concept", "概念"),
            ("experiment", "实验"),
        ):
            titles = "、".join(item["title"] for item in by_type[key][:8])
            lines.append(f"- {label}：{titles or '本周期未检测到更新'}")
        lines.extend(["", "## 阻塞与等待"])
        lines.extend(
            [f"- {item['title']}：{item.get('status')}" for item in blocked]
            or ["- 当前项目 frontmatter 未记录阻塞或等待。"]
        )
        lines.extend(["", "## 长期未更新项目"])
        lines.extend(
            [f"- {item['title']}：{item['message']}" for item in stale]
            or ["- 暂无。"]
        )
        lines.extend(
            [
                "",
                "## 下周期重点（请人工确认）",
                "- 从进行中项目里选择 1–3 个最重要的明确下一步。",
                "",
                "## 生活与精力",
                "- 本周精力、身体与生活状态：",
            ]
        )
        return {
            "markdown": "\n".join(lines) + "\n",
            "generated_at": now.isoformat(),
            "source_window_days": days,
            "recent_count": len(recent),
            "write_action": "无；仅在应用中预览",
        }

    def write_report(self) -> Path:
        summary = self.ensure_indexed()
        issues = self.store.list_issues(limit=1000)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = self.config.reports_dir / f"vault-check-{timestamp}.md"
        lines = [
            "# Research Workbench Vault 只读检查报告",
            "",
            f"- Vault：`{summary['vault_path']}`",
            f"- 扫描完成：{summary['finished_at']}",
            f"- Markdown：{summary['markdown_files']}",
            f"- 问题：error {summary['error']} / warning {summary['warning']} / info {summary['info']}",
            f"- 失效双链：{summary['broken_links']}",
            f"- 同名歧义：{summary['ambiguous_links']}",
            f"- 无效外部路径：{summary['invalid_external_paths']}",
            "",
            "## 说明",
            "",
            "本报告只读取 Vault；未修改、创建、移动或删除任何 Vault 文件，也未写入 Zotero。",
            "",
            "## 问题明细",
            "",
        ]
        if not issues:
            lines.append("- 未发现问题。")
        else:
            for issue in issues:
                location = issue.get("path") or "全局"
                field = f" · {issue['field']}" if issue.get("field") else ""
                lines.append(
                    f"- **{issue['severity']}** `{issue['code']}` · `{location}`{field}：{issue['message']}"
                )
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output

    def settings(self) -> dict[str, Any]:
        return {
            "vault_path": str(self.config.vault_path),
            "vault_status": "可读取" if self.config.vault_path.is_dir() else "不可用",
            "permission_mode": "只读 Vault",
            "data_dir": str(self.config.data_dir),
            "database_path": str(self.config.database_path),
            "reports_dir": str(self.config.reports_dir),
            "ignored_directories": sorted(self.config.ignored_directories),
            "zotero_adapter": {
                "status": "未启用",
                "mode": "预留只读 adapter 边界；不直接访问 Zotero 数据库",
            },
            "runner_adapters": {
                "status": "未启用",
                "mode": "WSL / SSH / EDA 第一阶段不执行",
            },
            "codex_board": {
                "url": None,
                "relationship": "独立产品；仅预留普通网页链接",
            },
        }

    def _project_view(self, note: dict[str, Any]) -> dict[str, Any]:
        detail = self.store.get_note(note["id"]) or note
        external = _first_value(detail, ("external_path",))
        external_records = detail.get("external_paths", [])
        external_status = next(
            (
                record["status"]
                for record in external_records
                if record["value"] == external
            ),
            None,
        )
        return {
            **note,
            "goal": _field_or_section(detail, ("goal", "target", "目标"), ("目标", "项目目标")),
            "next_step": _field_or_section(
                detail,
                ("next_step", "next_action", "下一步"),
                ("下一步", "下一步行动"),
            ),
            "completion_criteria": _field_or_section(
                detail,
                ("completion_criteria", "done_when", "完成标准"),
                ("完成标准", "验收标准"),
            ),
            "external_path": external,
            "external_path_status": external_status,
            "source_label": "Vault 记录",
            "external_state_source": "本机只读路径检查" if external else None,
        }

    def _literature_view(self, note: dict[str, Any]) -> dict[str, Any]:
        detail = self.store.get_note(note["id"]) or note
        return {
            **note,
            "fulltext_coverage": _first_value(
                detail, ("fulltext_coverage", "coverage", "预读覆盖范围")
            ),
            "needs_close_reading": _field_or_section(
                detail,
                ("needs_close_reading",),
                ("需要我细看的地方", "需要细看的地方"),
            ),
            "source_label": "Obsidian 文献笔记；未查询 Zotero",
        }

    def _experiment_view(self, note: dict[str, Any]) -> dict[str, Any]:
        detail = self.store.get_note(note["id"]) or note
        output = _first_value(detail, ("output_path", "result_path", "results_path"))
        external_records = detail.get("external_paths", [])
        output_status = next(
            (record["status"] for record in external_records if record["value"] == output),
            None,
        )
        return {
            **note,
            "environment": _field_or_section(
                detail, ("environment",), ("执行环境", "环境")
            ),
            "command": _field_or_section(detail, ("command",), ("命令", "脚本入口")),
            "output_path": output,
            "output_path_status": output_status,
            "evidence": _field_or_section(detail, ("evidence",), ("证据", "关键证据")),
            "judgment": _field_or_section(detail, ("judgment",), ("判断", "结论")),
            "next_step": _field_or_section(
                detail, ("next_step", "下一步"), ("下一步",)
            ),
            "source_label": "Vault 记录",
        }

    @staticmethod
    def _review_items(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        threshold = datetime.now(UTC) - timedelta(days=30)
        result: list[dict[str, Any]] = []
        for item in projects:
            if item.get("status") in {"完成", "归档"}:
                continue
            modified = datetime.fromisoformat(item["modified_at"])
            if modified < threshold:
                result.append(
                    {
                        "id": item["id"],
                        "title": item["title"],
                        "modified_at": item["modified_at"],
                        "message": "超过 30 天未修改（基于文件修改时间的软件检查）",
                        "source": "软件检查",
                    }
                )
        return result
