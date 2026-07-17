from __future__ import annotations

import hashlib
from pathlib import Path

from research_workbench.indexing.service import IndexService


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    result = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        result[relative] = (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def test_scan_and_suggestions_never_write_vault(config, vault: Path) -> None:
    before = _snapshot(vault)
    service = IndexService(config)
    service.scan()
    service.dashboard()
    service.inbox_suggestions()
    service.weekly_review()
    after = _snapshot(vault)
    assert after == before
    assert not config.database_path.is_relative_to(vault)
    assert not config.reports_dir.is_relative_to(vault)


def test_report_is_only_written_to_project_reports(config) -> None:
    service = IndexService(config)
    service.scan()
    output = service.write_report()
    assert output.parent == config.reports_dir
    assert "未修改、创建、移动或删除任何 Vault 文件" in output.read_text(encoding="utf-8")
