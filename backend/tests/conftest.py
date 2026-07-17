from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from research_workbench.config import AppConfig
from research_workbench.indexing.service import IndexService


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "fixtures" / "vault"
    destination = tmp_path / "只读知识库"
    shutil.copytree(source, destination)
    return destination


@pytest.fixture()
def config(tmp_path: Path, vault: Path) -> AppConfig:
    root = tmp_path / "Research Workbench"
    root.mkdir()
    return AppConfig.build(
        vault,
        data_dir=root / ".runtime",
        project_root=root,
    )


@pytest.fixture()
def service(config: AppConfig) -> IndexService:
    instance = IndexService(config)
    instance.scan()
    return instance
