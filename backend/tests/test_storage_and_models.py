from __future__ import annotations

from pathlib import Path

from research_workbench.domain.models import ExperimentView, LiteratureView, ProjectView
from research_workbench.indexing.service import IndexService


def test_domain_models_accept_workflow_fields() -> None:
    common = {
        "id": "x",
        "path": "02_项目/测试.md",
        "title": "测试",
        "modified_at": "2026-07-18T00:00:00+00:00",
    }
    assert ProjectView(**common, next_step="继续验证").source_label == "Vault 记录"
    assert LiteratureView(**common, reading_stage="AI已预读").reading_stage == "AI已预读"
    assert ExperimentView(**common, evidence="日志").evidence == "日志"


def test_sqlite_rebuild_replaces_current_index(config, vault: Path) -> None:
    service = IndexService(config)
    first = service.scan()
    (vault / "05_概念" / "新增概念.md").write_text(
        "---\ntype: concept\n---\n# 新增概念\n", encoding="utf-8"
    )
    second = service.scan()
    assert second["scan_id"] > first["scan_id"]
    assert second["markdown_files"] == first["markdown_files"] + 1
    assert len(service.notes(query="新增概念")) == 1


def test_project_literature_and_experiment_views(service: IndexService) -> None:
    project = service.projects()[0]
    assert project["next_step"] == "跑通 fixture Vault 扫描。"
    assert project["external_path_status"] == "missing"
    literature = service.literature()[0]
    assert literature["needs_close_reading"] == "图 3 的功耗统计边界。"
    experiment = service.experiments()[0]
    assert experiment["judgment"] == "待验证。"
