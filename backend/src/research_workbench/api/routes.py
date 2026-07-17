from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from research_workbench.indexing.service import IndexService


Limit = Annotated[int, Query(ge=1, le=500)]


def create_api_router(service: IndexService) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "product": "Research Workbench", "mode": "local"}

    @router.get("/summary")
    def summary() -> dict:
        current = service.summary()
        if current is None:
            return {"indexed": False, "vault_path": str(service.config.vault_path)}
        return {"indexed": True, **current}

    @router.post("/scan")
    def scan() -> dict:
        try:
            return service.scan()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/dashboard")
    def dashboard() -> dict:
        service.ensure_indexed()
        return service.dashboard()

    @router.get("/notes")
    def notes(
        type: str | None = None,
        status: str | None = None,
        domain: str | None = None,
        reading_stage: str | None = None,
        collection: str | None = None,
        q: str | None = None,
        limit: Limit = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict]:
        service.ensure_indexed()
        return service.notes(
            note_type=type,
            status=status,
            domain=domain,
            reading_stage=reading_stage,
            collection=collection,
            query=q,
            limit=limit,
            offset=offset,
        )

    @router.get("/notes/{note_id}")
    def note(note_id: str) -> dict:
        service.ensure_indexed()
        result = service.note(note_id)
        if result is None:
            raise HTTPException(status_code=404, detail="笔记不存在")
        return result

    @router.get("/projects")
    def projects(
        status: str | None = None,
        domain: str | None = None,
        q: str | None = None,
        limit: Limit = 200,
    ) -> list[dict]:
        service.ensure_indexed()
        return service.projects(status=status, domain=domain, query=q, limit=limit)

    @router.get("/literature")
    def literature(
        status: str | None = None,
        domain: str | None = None,
        reading_stage: str | None = None,
        collection: str | None = None,
        q: str | None = None,
        limit: Limit = 200,
    ) -> list[dict]:
        service.ensure_indexed()
        return service.literature(
            status=status,
            domain=domain,
            reading_stage=reading_stage,
            collection=collection,
            query=q,
            limit=limit,
        )

    @router.get("/knowledge")
    def knowledge(
        domain: str | None = None,
        q: str | None = None,
        limit: Limit = 200,
    ) -> dict:
        service.ensure_indexed()
        return service.knowledge(domain=domain, query=q, limit=limit)

    @router.get("/experiments")
    def experiments(
        status: str | None = None,
        domain: str | None = None,
        q: str | None = None,
        limit: Limit = 200,
    ) -> list[dict]:
        service.ensure_indexed()
        return service.experiments(status=status, domain=domain, query=q, limit=limit)

    @router.get("/issues")
    def issues(
        severity: str | None = None,
        code: str | None = None,
        limit: Limit = 300,
    ) -> list[dict]:
        service.ensure_indexed()
        return service.issues(severity=severity, code=code, limit=limit)

    @router.get("/facets")
    def facets() -> dict:
        service.ensure_indexed()
        return service.facets()

    @router.get("/inbox/suggestions")
    def inbox_suggestions() -> list[dict]:
        service.ensure_indexed()
        return service.inbox_suggestions()

    @router.get("/reviews/weekly")
    def weekly_review() -> dict:
        service.ensure_indexed()
        return service.weekly_review()

    @router.get("/reviews/daily")
    def daily_review() -> dict:
        service.ensure_indexed()
        return service.daily_review()

    @router.post("/reports")
    def write_report() -> dict[str, str]:
        output = service.write_report()
        return {"path": str(output), "scope": "Research Workbench reports/"}

    @router.get("/settings")
    def settings() -> dict:
        return service.settings()

    return router
