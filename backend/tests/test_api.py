from __future__ import annotations

from fastapi.testclient import TestClient

from research_workbench.api.app import create_app


def test_api_vertical_slice(config, service) -> None:
    client = TestClient(create_app(config, service=service))
    assert client.get("/api/health").json()["status"] == "ok"
    summary = client.get("/api/summary").json()
    assert summary["indexed"] is True
    assert summary["markdown_files"] == 9
    assert client.get("/api/dashboard").json()["next_actions"]
    assert client.get("/api/projects?q=量子").json()[0]["source_label"] == "Vault 记录"
    assert client.get("/api/literature?reading_stage=AI已预读").json()
    assert client.get("/api/knowledge").json()["diagnostics"]["unresolved_links"] >= 1
    assert client.get("/api/experiments").json()[0]["output_path_status"] == "missing"
    assert client.get("/api/inbox/suggestions").json()[0]["write_action"] == "无；仅生成建议"
    assert "不会写入 Vault" in client.get("/api/reviews/weekly").json()["markdown"]
    assert client.get("/api/reviews/daily").json()["source_window_days"] == 1


def test_note_detail_and_not_found(config, service) -> None:
    client = TestClient(create_app(config, service=service))
    note_id = client.get("/api/notes?q=相干性").json()[0]["id"]
    detail = client.get(f"/api/notes/{note_id}")
    assert detail.status_code == 200
    assert detail.json()["backlinks"]
    assert client.get("/api/notes/does-not-exist").status_code == 404
