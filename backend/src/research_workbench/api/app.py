from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from research_workbench import __version__
from research_workbench.api.routes import create_api_router
from research_workbench.config import AppConfig
from research_workbench.indexing.service import IndexService


def create_app(
    config: AppConfig,
    *,
    service: IndexService | None = None,
) -> FastAPI:
    index_service = service or IndexService(config)
    app = FastAPI(
        title="Research Workbench",
        description="科研工作台本地只读 API",
        version=__version__,
    )
    app.state.config = config
    app.state.index_service = index_service
    app.include_router(create_api_router(index_service))

    dist = config.frontend_dist
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    index_file = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        requested = (dist / full_path).resolve()
        if dist.is_dir() and requested.is_relative_to(dist.resolve()) and requested.is_file():
            return FileResponse(requested)
        if index_file.is_file():
            return FileResponse(index_file)
        return {
            "product": "Research Workbench / 科研工作台",
            "message": "前端尚未构建，请在 frontend 目录运行 npm run build。",
            "api": "/docs",
        }

    return app
