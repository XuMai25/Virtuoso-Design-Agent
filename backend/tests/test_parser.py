from __future__ import annotations

from pathlib import Path

from research_workbench.adapters.obsidian.parser import parse_markdown, section_text


def test_frontmatter_headings_and_wiki_alias() -> None:
    parsed = parse_markdown(
        Path("中文路径/笔记.md"),
        """---
type: literature
created: 2026-07-18
aliases: [测试别名]
---
# 标题

链接 [[05_概念/相干性#定义|相干机制]] 与 ![[图示]]。

## 需要我细看的地方
图 2 的坐标。
""",
    )
    assert parsed.title == "标题"
    assert parsed.properties["created"] == "2026-07-18"
    assert parsed.aliases == ["测试别名"]
    assert parsed.links[0].target == "05_概念/相干性#定义"
    assert parsed.links[0].alias == "相干机制"
    assert parsed.links[1].embed is True
    assert section_text(parsed.body, ("需要我细看的地方",)) == "图 2 的坐标。"


def test_invalid_frontmatter_is_reported() -> None:
    parsed = parse_markdown(Path("bad.md"), "---\nkey: [\n---\n# Bad")
    assert parsed.parse_errors


def test_obsidian_unquoted_colon_uses_lenient_readonly_fallback() -> None:
    parsed = parse_markdown(
        Path("paper.md"),
        "---\nvenue: IEEE Transactions: Express Briefs\n---\n# Paper",
    )
    assert parsed.properties["venue"] == "IEEE Transactions: Express Briefs"
    assert parsed.parse_errors == []
    assert parsed.parse_warnings
