from __future__ import annotations

from pathlib import Path

from research_workbench.adapters.obsidian.scanner import VaultScanner
from research_workbench.config import DEFAULT_IGNORED_DIRECTORIES


def test_vault_index_resolves_alias_and_reports_broken_and_ambiguous(vault: Path) -> None:
    bundle = VaultScanner(vault, DEFAULT_IGNORED_DIRECTORIES).scan()
    assert len(bundle.notes) == 9
    alias_link = next(link for link in bundle.links if link.target_text == "Coherence")
    assert alias_link.status == "resolved"
    assert any(issue.code == "wiki_link_missing" for issue in bundle.issues)
    assert any(issue.code == "wiki_link_ambiguous" for issue in bundle.issues)


def test_windows_chinese_external_path_is_checked(vault: Path) -> None:
    bundle = VaultScanner(vault, DEFAULT_IGNORED_DIRECTORIES).scan()
    values = {item.value: item.status for item in bundle.external_paths}
    assert "H:\\不存在\\中文工程" in values
    assert values["H:\\不存在\\中文工程"] == "missing"
