from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".obsidian",
        ".pytest_cache",
        ".trash",
        ".cache",
        "__pycache__",
        "node_modules",
        "tmp",
    }
)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class AppConfig:
    vault_path: Path
    data_dir: Path
    project_root: Path
    port: int = 4280
    ignored_directories: frozenset[str] = field(
        default_factory=lambda: DEFAULT_IGNORED_DIRECTORIES
    )

    @classmethod
    def build(
        cls,
        vault_path: str | Path,
        *,
        data_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        port: int | None = None,
    ) -> "AppConfig":
        vault = Path(vault_path).expanduser().resolve()
        if not vault.is_dir():
            raise ValueError(f"Vault 路径不可用：{vault}")

        root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path(__file__).resolve().parents[3]
        )
        if data_dir:
            app_data = Path(data_dir).expanduser().resolve()
        elif configured := os.getenv("RESEARCH_WORKBENCH_DATA_DIR"):
            app_data = Path(configured).expanduser().resolve()
        elif local_app_data := os.getenv("LOCALAPPDATA"):
            app_data = (Path(local_app_data) / "ResearchWorkbench").resolve()
        else:
            app_data = (Path.home() / ".research-workbench").resolve()

        reports_dir = (root / "reports").resolve()
        for writable in (app_data, reports_dir):
            if _is_relative_to(writable, vault):
                raise ValueError(
                    f"应用写入目录不得位于 Vault 内：{writable}（Vault：{vault}）"
                )

        configured_port = port or int(os.getenv("RESEARCH_WORKBENCH_PORT", "4280"))
        return cls(
            vault_path=vault,
            data_dir=app_data,
            project_root=root,
            port=configured_port,
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "research-workbench.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    @property
    def frontend_dist(self) -> Path:
        return self.project_root / "frontend" / "dist"

    def prepare_writable_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
